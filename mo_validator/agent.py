"""Thin wrapper around an OpenAI-compatible chat endpoint for *structured* calls.

Every step in the pipeline is an "agent": it gets a system prompt + a user
prompt and must answer with a JSON object matching a fixed schema. We force that
by declaring a single function tool and setting ``tool_choice`` to it, then
parsing the validated ``arguments`` JSON back out of the returned tool call.

LLM access goes through an OpenRouter **proxy gateway** that speaks the OpenAI
Chat Completions API, e.g.::

    client = openai.OpenAI(
        base_url="https://open-router-gateway.replit.app/api/proxy",
        api_key="YOUR_GATEWAY_API_KEY",
    )
    client.chat.completions.create(model="anthropic/claude-3-haiku", messages=[...])

So model ids are OpenRouter-style (``<provider>/<model>``), the base URL points
at the gateway, and the API key is the gateway key — all configurable via env.
"""

from __future__ import annotations

import json
import os
from typing import Any

import openai

try:
    import jsonschema
except ModuleNotFoundError:  # optional; falls back to required-key checking
    jsonschema = None  # type: ignore

from . import envload  # noqa: F401  -- loads .env on import (side effect)

DEFAULT_MODEL = os.environ.get("MO_MODEL", "anthropic/claude-haiku-4.5")
DEFAULT_BASE_URL = os.environ.get(
    "MO_BASE_URL", "https://open-router-gateway.replit.app/api/proxy"
)


def _api_key() -> str | None:
    """The gateway API key, checked across the supported env var names."""
    return (
        os.environ.get("MO_API_KEY")
        or os.environ.get("OPENAI_API_KEY")
        or os.environ.get("ANTHROPIC_API_KEY")
        or os.environ.get("ANTHROPIC_AUTH_TOKEN")
    )


class AgentError(RuntimeError):
    """Raised when the model response cannot be turned into structured data."""


def _missing_required(schema: dict[str, Any], data: Any, path: str = "") -> list[str]:
    """Recursively find required fields the model omitted.

    Fallback used only when ``jsonschema`` is not installed. OpenAI-style function
    calling treats JSON-schema ``required`` as a hint, not a hard constraint (the
    old Anthropic tool path enforced it), so the model can return an object missing
    a declared field. We re-check it ourselves and feed gaps back as a retry.
    """
    missing: list[str] = []
    if not isinstance(schema, dict):
        return missing
    if schema.get("type") == "object" and isinstance(data, dict):
        props = schema.get("properties", {})
        for key in schema.get("required", []):
            where = f"{path}.{key}" if path else key
            if key not in data or data[key] is None:
                missing.append(where)
            elif key in props:
                missing += _missing_required(props[key], data[key], where)
        for key, sub in props.items():
            if key in data and key not in schema.get("required", []):
                where = f"{path}.{key}" if path else key
                missing += _missing_required(sub, data[key], where)
    elif schema.get("type") == "array" and isinstance(data, list):
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for i, item in enumerate(data):
                missing += _missing_required(item_schema, item, f"{path}[{i}]")
    return missing


def _schema_violations(schema: dict[str, Any], data: Any) -> list[str]:
    """Return human-readable reasons *data* does not satisfy *schema*.

    Uses ``jsonschema`` for a full check (types, nesting, enums, required) when
    available — the gateway's function calling enforces none of these — and falls
    back to required-field presence otherwise. Empty list means valid.
    """
    if jsonschema is None:
        return [f"missing required field '{m}'" for m in _missing_required(schema, data)]
    validator = jsonschema.Draft7Validator(schema)
    problems: list[str] = []
    for err in sorted(validator.iter_errors(data), key=lambda e: list(e.path)):
        loc = "/".join(str(p) for p in err.path) or "(root)"
        problems.append(f"at '{loc}': {err.message}")
    return problems


class Agent:
    """A configured OpenAI-compatible client used to make structured agent calls."""

    def __init__(self, model: str | None = None, max_tokens: int | None = None) -> None:
        max_tokens = max_tokens or int(os.environ.get("MO_MAX_TOKENS", "16000"))
        api_key = _api_key()
        if not api_key:
            raise AgentError(
                "No API key set. Put MO_API_KEY (the gateway key) in a .env file "
                "(it is loaded automatically) or export it before running — see "
                ".env.example."
            )
        self.client = openai.OpenAI(
            base_url=os.environ.get("MO_BASE_URL", DEFAULT_BASE_URL),
            api_key=api_key,
            max_retries=int(os.environ.get("MO_API_RETRIES", "5")),
            timeout=float(os.environ.get("MO_API_TIMEOUT", "120")),
        )
        self.model = model or DEFAULT_MODEL
        self.max_tokens = max_tokens

    def _create(self, messages, tools, tool_name, max_tokens):
        """Call the chat endpoint, retrying transient errors with backoff.

        The SDK already retries connection/timeout/5xx/429 internally; this outer
        loop adds a few more attempts so one network blip on the proxy doesn't
        abort a long multi-call pipeline. Non-transient errors raise immediately.
        """
        import time
        transient = (
            openai.APIConnectionError,
            openai.APITimeoutError,
            openai.RateLimitError,
            openai.InternalServerError,
        )
        attempts = int(os.environ.get("MO_TRANSIENT_RETRIES", "3"))
        for i in range(attempts + 1):
            try:
                return self.client.chat.completions.create(
                    model=self.model,
                    max_tokens=max_tokens,
                    messages=messages,
                    tools=tools,
                    tool_choice={"type": "function", "function": {"name": tool_name}},
                )
            except transient:
                if i >= attempts:
                    raise
                time.sleep(2 ** i)  # 1s, 2s, 4s, ...

    def structured(
        self,
        *,
        system: str,
        user: str,
        tool_name: str,
        tool_description: str,
        schema: dict[str, Any],
        max_tokens: int | None = None,
    ) -> dict[str, Any]:
        """Run one structured call and return the validated tool-call arguments.

        The result is validated against the full JSON schema (types, nesting,
        enums, required fields); on any violation we retry (up to
        ``MO_STRUCTURED_RETRIES``, default 2) naming the exact problems, so a
        single lax/mis-typed response doesn't crash the pipeline downstream.
        """
        tools = [
            {
                "type": "function",
                "function": {
                    "name": tool_name,
                    "description": tool_description,
                    "parameters": schema,
                },
            }
        ]
        retries = int(os.environ.get("MO_STRUCTURED_RETRIES", "2"))
        messages: list[dict[str, str]] = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        last_problems: list[str] = []
        cur_max = max_tokens or self.max_tokens
        cap = int(os.environ.get("MO_MAX_TOKENS_CAP", "32000"))
        for attempt in range(retries + 1):
            try:
                response = self._create(messages, tools, tool_name, cur_max)
            except openai.OpenAIError as exc:  # pragma: no cover - network path
                raise AgentError(f"LLM API call failed: {exc}") from exc

            choice = response.choices[0]
            truncated = choice.finish_reason == "length"
            call = next(
                (c for c in (choice.message.tool_calls or [])
                 if c.function.name == tool_name),
                None,
            )
            if call is None:
                # A length cutoff can stop the model before it emits the tool call
                # at all; grow the budget and retry rather than failing outright.
                if truncated and attempt < retries:
                    cur_max = min(cur_max * 2, cap)
                    continue
                raise AgentError(
                    f"Model did not return the expected '{tool_name}' tool call "
                    f"(finish_reason={choice.finish_reason!r})."
                )
            try:
                data = dict(json.loads(call.function.arguments))
            except (json.JSONDecodeError, TypeError, ValueError) as exc:
                # Invalid/truncated JSON used to abort the whole pipeline. Treat it
                # like any other recoverable problem: if the output was cut off,
                # grow the token budget; either way ask the model to re-emit
                # COMPLETE, valid JSON, and only fail if retries are exhausted.
                last_problems = [
                    ("the tool-call arguments were truncated (incomplete JSON): "
                     if truncated else "the tool-call arguments were not valid JSON: ")
                    + str(exc)
                ]
                if attempt < retries:
                    if truncated:
                        cur_max = min(cur_max * 2, cap)
                    messages.append({
                        "role": "user",
                        "content": (
                            f"Your previous '{tool_name}' call could not be parsed: "
                            + last_problems[0]
                            + ". Call it again and return a COMPLETE, valid JSON "
                            "object. Make sure every string is properly escaped and "
                            "closed; keep each code field correct but as concise as "
                            "possible so the whole object fits."
                        ),
                    })
                    continue
                raise AgentError(
                    f"Model returned invalid JSON for '{tool_name}' after "
                    f"{retries + 1} attempts: {exc}"
                ) from exc

            last_problems = _schema_violations(schema, data)
            if not last_problems:
                return data
            if attempt < retries:  # ask the model to correct the exact problems
                messages.append({
                    "role": "user",
                    "content": (
                        "Your previous response did not match the required schema:\n"
                        + "\n".join(f"- {p}" for p in last_problems)
                        + f"\nCall '{tool_name}' again with a COMPLETE, correctly "
                        "typed object that satisfies every field."
                    ),
                })

        raise AgentError(
            f"Model response for '{tool_name}' kept violating the schema after "
            f"{retries + 1} attempts: {'; '.join(last_problems)}."
        )
