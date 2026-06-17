"""Flask backend for the multiple-outputs validator website.

POST /api/process  {description, languages?, max_iterations?}
    Runs the multi-language pipeline and returns the full structured result
    (classification, shared coverage-checked inputs, and per-language
    validator + execution verdicts), plus the run log.

GET  /api/health   reports available language runtimes and whether the API key
    is configured.
"""

from __future__ import annotations

import sys
from dataclasses import asdict
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mo_validator.agent import Agent, AgentError  # noqa: E402
from mo_validator.mlpipeline import run_multilang  # noqa: E402
from mo_validator.runners import ALL_LANGUAGES, available_languages  # noqa: E402

STATIC = Path(__file__).resolve().parent / "static"
app = Flask(__name__, static_folder=None)


@app.get("/")
def index():
    return send_from_directory(STATIC, "index.html")


@app.get("/static/<path:name>")
def static_files(name: str):
    return send_from_directory(STATIC, name)


@app.get("/api/health")
def health():
    import os
    return jsonify({
        "all_languages": ALL_LANGUAGES,
        "available_languages": available_languages(),
        "api_key_configured": bool(os.environ.get("MO_API_KEY")
                                   or os.environ.get("OPENAI_API_KEY")
                                   or os.environ.get("ANTHROPIC_API_KEY")
                                   or os.environ.get("ANTHROPIC_AUTH_TOKEN")),
    })


@app.post("/api/process")
def process():
    data = request.get_json(force=True, silent=True) or {}
    description = (data.get("description") or "").strip()
    if not description:
        return jsonify({"error": "Please provide a problem description."}), 400

    languages = data.get("languages") or available_languages()
    languages = [l for l in languages if l in ALL_LANGUAGES]
    if not languages:
        return jsonify({"error": "No valid languages selected."}), 400

    max_iterations = int(data.get("max_iterations") or 3)

    log: list[dict[str, str]] = []

    def progress(stage: str, message: str) -> None:
        log.append({"stage": stage, "message": message})

    try:
        agent = Agent()
    except AgentError as exc:
        return jsonify({"error": str(exc)}), 400

    try:
        result = run_multilang(
            description, languages=languages, agent=agent,
            max_iterations=max_iterations, progress=progress,
        )
    except AgentError as exc:
        return jsonify({"error": str(exc), "log": log}), 502
    except Exception as exc:  # surface unexpected failures to the UI
        return jsonify({"error": f"{type(exc).__name__}: {exc}", "log": log}), 500

    payload = asdict(result)
    payload["log"] = log
    return jsonify(payload)


def main() -> None:
    import argparse
    p = argparse.ArgumentParser(description="Serve the multiple-outputs validator website.")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=5000)
    args = p.parse_args()
    print(f"Serving on http://{args.host}:{args.port}  (Ctrl-C to stop)")
    app.run(host=args.host, port=args.port, threaded=True)


if __name__ == "__main__":
    main()
