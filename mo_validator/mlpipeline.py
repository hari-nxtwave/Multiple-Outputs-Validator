"""Multi-language pipeline used by the website.

Input: a problem DESCRIPTION (+ chosen languages). Output: for each language, a
validator-embedded driver that has been EXECUTION-VERIFIED against generated
reference / alternative-valid / wrong solutions over a shared, coverage-checked
set of stdin inputs (including corner/edge cases).

    classify(desc) -> spec(contract + inputs) -> coverage(expand inputs)
                   -> per language: testsuite -> loop( transform -> run )
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from . import prompts
from .agent import Agent
from .executor import ExecResult, verify_by_execution
from .runners import ALL_LANGUAGES, get_runner, lint_solution, sanitize_solution

ProgressFn = Callable[[str, str], None]   # (stage, message)

# The test suite bundles several full code blobs (reference + equivalents +
# wrongs) into one JSON object, so it is by far the largest model output. Give it
# extra headroom up front to avoid a truncated/invalid-JSON first attempt; the
# agent still auto-grows and retries if even this is not enough.
_SUITE_MAX_TOKENS = int(os.environ.get("MO_SUITE_MAX_TOKENS", "24000"))

# Hard upper bound on how many shared stdin test cases we keep / run. Capping it
# bounds the compile-and-run cost per language (every solution runs against every
# case) so verification stays fast.
_MAX_TEST_CASES = int(os.environ.get("MO_MAX_TEST_CASES", "50"))

# Where the shared test cases are written for inspection / re-running.
_TEST_CASES_FILE = os.environ.get("MO_TEST_CASES_FILE", "test_cases.json")


@dataclass
class LangResult:
    language: str
    available: bool = True
    driver_code: str | None = None
    strategy: str | None = None
    validation_notes: str = ""
    test_suite: dict[str, Any] | None = None
    solutions: list[dict[str, str]] = field(default_factory=list)  # all correct solutions
    executions: list[ExecResult] = field(default_factory=list)
    iterations: int = 0
    verified_ok: bool = False
    full_program: str | None = None   # runnable solution + driver (with main)
    code_review: dict[str, Any] | None = None   # final complete-code review
    translated: bool = False          # ported from the Java validator (not executed)
    message: str = ""


@dataclass
class MLResult:
    accepted: bool
    category: str
    classification: dict[str, Any]
    spec: dict[str, Any] | None = None
    inputs: list[dict[str, str]] = field(default_factory=list)
    coverage: dict[str, Any] | None = None
    languages: dict[str, LangResult] = field(default_factory=dict)
    validator_review: dict[str, Any] | None = None  # cross-language validator review
    test_cases_path: str | None = None   # where test_cases.json was written
    message: str = ""


def _noop(stage: str, message: str) -> None:
    pass


# The canonical base language. The validator is authored, execution-verified and
# auto-fixed here, then translated to the rest — so this is the one language whose
# driver is proven by actually compiling and running it.
_BASE_LANGUAGE = "java"


def _runtime_ready(lang: str) -> bool:
    runner = get_runner(lang)
    return bool(runner and runner.available())


def _pick_base(languages: list[str]) -> str | None:
    """Choose the language to execution-verify (then translate from).

    Always Java when the JDK is available — Java is the language whose validator is
    compiled, run and auto-fixed against the suite, and every other language is a
    translation of it. Falls back to the first usable selected language only if no
    Java runtime is present.
    """
    if _runtime_ready(_BASE_LANGUAGE):
        return _BASE_LANGUAGE
    return next((l for l in languages if _runtime_ready(l)), None)


def _normalize_suite(flat: dict[str, Any]) -> dict[str, Any]:
    """Adapt the flat test-suite tool output into the internal suite structure.

    The LLM returns code strings (``reference_code`` / ``equivalent_codes`` /
    ``wrong_codes``) because small models emit those reliably; the executor and
    the rest of the pipeline expect ``{reference_solution, equivalent_solutions,
    wrong_solutions}`` with ``{name, code, note}`` items, so we synthesize names.
    """
    return {
        "reasoning": flat.get("reasoning", ""),
        "reference_solution": {"code": flat.get("reference_code", ""), "note": "reference"},
        "equivalent_solutions": [
            {"name": f"equivalent_{i + 1}", "code": c, "note": ""}
            for i, c in enumerate(flat.get("equivalent_codes", []))
        ],
        "wrong_solutions": [
            {"name": f"wrong_{i + 1}", "code": c, "note": ""}
            for i, c in enumerate(flat.get("wrong_codes", []))
        ],
    }


def _suite_solutions(suite: dict[str, Any]):
    """Yield (group_label, solution_dict) for every solution in a test suite."""
    ref = suite.get("reference_solution")
    if ref:
        yield "reference", ref
    for sol in suite.get("equivalent_solutions", []):
        yield "equivalent", sol
    for sol in suite.get("wrong_solutions", []):
        yield "wrong", sol


def _lint_suite(language: str, suite: dict[str, Any]) -> str:
    """Return corrective feedback if any solution breaks the calling contract."""
    problems: list[str] = []
    for group, sol in _suite_solutions(suite):
        name = sol.get("name", group)
        code = sanitize_solution(language, sol.get("code", ""))
        for reason in lint_solution(language, code):
            problems.append(f"- {group} solution '{name}': {reason}")
    if not problems:
        return ""
    return (
        "Some solutions are not bare functions/classes — they must define ONLY "
        "the required function/class (no stdin reading, no main / entry point). "
        "Fix exactly these and return the complete suite:\n" + "\n".join(problems)
    )


def _attribute_failure(ex: ExecResult, suite_regens: int) -> tuple[str, str]:
    """Split an execution failure into (driver_feedback, suite_feedback).

    Only one is non-empty. A compile error or a failing reference/equivalent case
    is the driver's fault (it must accept every valid answer and compile). A
    failing WRONG case has two possible causes that we cannot tell apart from the
    outside: either the suite's "wrong" solution is not actually wrong on any
    shared input (a suite problem), or the driver's validator is too lenient and
    accepts something it should reject (a driver problem). We try the suite first
    (cheaper, the common case), and if regenerating it once does not fix the
    uncaught-wrong, we escalate to the driver and tell it to tighten the validator.
    """
    driver_faults = [c for c in ex.cases
                     if not c.passed and c.kind in ("reference", "equivalent")]
    wrong_faults = [c for c in ex.cases if not c.passed and c.kind == "wrong"]

    if ex.driver_compile_error or driver_faults:
        return ex.feedback, ""

    if wrong_faults:
        names = ", ".join(sorted({c.solution for c in wrong_faults}))
        if suite_regens == 0:
            suite_feedback = (
                "The following 'wrong' solutions were NOT rejected on ANY shared "
                f"input ({names}): the driver produced the canonical output for "
                "them, which means they are not actually wrong on the inputs "
                "available — they differ from the correct answer only in "
                "order/structure that the driver normalises away, or their mistake "
                "is never triggered by any given input. Replace each of these with "
                "a WRONG solution whose answer differs in CONTENT (elements / "
                "grouping / size / membership) from the correct answer on at least "
                "one of the specific shared inputs listed above. Mentally check "
                "each replacement against those exact inputs. Keep the reference "
                "and equivalent solutions correct and unchanged in spirit. Return "
                "the complete suite."
            )
            return "", suite_feedback
        # Suite was already regenerated and the wrong solutions are still slipping
        # through -> the validator itself is too lenient. Push back on the driver.
        driver_feedback = (
            ex.feedback
            + "\n\nThese WRONG submissions are genuinely incorrect yet the driver "
            "accepted them (it printed the canonical answer for them). Your `check` "
            "step is TOO LENIENT: it is not checking every constraint that makes an "
            "answer correct. JUDGE the user's answer against EVERY structural "
            "constraint and, for an optimisation problem, against the optimal SCALAR "
            "value (the `check` step must not search for a solution to validate one). "
            "Reject any user answer that violates ANY constraint; only print the "
            "input-derived canonical answer when the user's answer is fully valid."
        )
        return driver_feedback, ""

    # Fallback: treat as a driver issue so we still make progress.
    return ex.feedback, ""


def _write_test_cases(spec: dict[str, Any], category: str,
                      inputs: list[dict[str, str]]) -> str | None:
    """Write the shared stdin test cases to ``test_cases.json`` for inspection.

    Returns the absolute path written, or ``None`` if the write failed (never
    fatal — it is a convenience artifact, not part of verification).
    """
    payload = {
        "title": spec.get("title", ""),
        "category": category,
        "stdin_format": spec.get("stdin_format", ""),
        "output_format": spec.get("output_format", ""),
        "count": len(inputs),
        "test_cases": inputs,
    }
    try:
        path = Path(_TEST_CASES_FILE).resolve()
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return str(path)
    except OSError:
        return None


def _spec_blob(lang: str, description: str, category: str,
               spec: dict[str, Any]) -> str:
    """The shared contract + description block sent to the transformer for one language."""
    sig = spec["signatures"].get(lang, "")
    return (
        f"## Contract\nstdin_format: {spec['stdin_format']}\n"
        f"output_format: {spec['output_format']}\n"
        f"function_name: {spec['function_name']}\n"
        f"{lang} signature: {sig}\n\n## Problem description\n{description}\n"
        f"\nCategory: {category}."
    )


def _generate_driver(agent: Agent, lang: str, category: str, spec_blob: str,
                     feedback: str = "") -> dict[str, Any]:
    """Ask the transformer for the complete validator-embedded driver for one language."""
    user = spec_blob + "\n\nEmit the complete driver program."
    if feedback:
        user += ("\n\nThe previous driver failed verification / review. Fix exactly "
                 "these and return the complete program:\n" + feedback)
    return agent.structured(
        system=prompts.ml_transformer_system(lang, category),
        user=user,
        tool_name="emit_driver",
        tool_description=f"Return the {lang} validator-embedded driver.",
        schema=prompts.ml_transformer_schema(),
    )


def _apply_driver(lr: LangResult, transform: dict[str, Any]) -> None:
    lr.driver_code = transform["driver_code"]
    lr.strategy = transform["strategy"]
    lr.validation_notes = transform["validation_notes"]


def _surface_full_program(lr: LangResult, runner) -> None:
    """Refresh the complete runnable program (reference solution + driver)."""
    if lr.driver_code is not None and lr.test_suite and lr.test_suite.get("reference_solution"):
        lr.full_program = runner.combined_program(
            lr.driver_code, lr.test_suite["reference_solution"]["code"])


def _exec_summary(lr: LangResult) -> str:
    """A compact summary of the latest execution, for the review agents."""
    ex = lr.executions[-1] if lr.executions else None
    if ex is None:
        return "no execution result available"
    lines = [f"{ex.passed}/{ex.total} cases pass; verified_ok={ex.ok}"]
    if ex.driver_compile_error:
        lines.append("driver compile error: " + ex.driver_compile_error[:300])
    fails = [c for c in ex.cases if not c.passed][:8]
    for c in fails:
        lines.append(f"FAIL [{c.kind}] {c.solution} / {c.test_input}: {c.detail}")
    for w in ex.harness_warnings[:4]:
        lines.append(f"(harness) {w}")
    return "\n".join(lines)


def _regen_and_verify(lr: LangResult, lang: str, category: str, spec_blob: str,
                      inputs: list[dict[str, str]], agent: Agent, feedback: str,
                      progress: ProgressFn, tag: str) -> None:
    """Regenerate one language's driver from review feedback and re-run execution."""
    runner = get_runner(lang)
    progress(lang, f"[{tag}] Regenerating validator driver from review feedback...")
    _apply_driver(lr, _generate_driver(agent, lang, category, spec_blob, feedback))
    if runner is None:
        return
    ex = verify_by_execution(lr.driver_code, lr.test_suite, runner, inputs)
    lr.executions.append(ex)
    lr.verified_ok = ex.ok
    _surface_full_program(lr, runner)
    progress(lang, f"[{tag}] Re-verify: {ex.passed}/{ex.total} cases pass -> "
             + ("OK" if ex.ok else "FAILURES"))


def _suite_blocks(lang: str, suite: dict[str, Any]) -> str:
    """Render the test-case solutions for one language for the validator review.

    Cost-aware: the reference and correct-approach solutions are already proven by
    execution (and summarised separately), so they are listed by name/note only.
    The WRONG solutions — the ones the validator must REJECT — are shown in full,
    since that is what the review needs to reason about.
    """
    blocks: list[str] = []
    ref = suite.get("reference_solution")
    correct = [ref] if (ref and ref.get("code")) else []
    correct += list(suite.get("equivalent_solutions", []))
    if correct:
        names = ", ".join(s.get("name", "reference") or "reference" for s in correct)
        blocks.append(f"Correct submissions (execution-verified, accepted): {names}.")
    for s in suite.get("wrong_solutions", []):
        blocks.append(f"### WRONG submission · {s.get('name', '')} "
                      f"({s.get('note', '')})\n```{lang}\n{s.get('code', '')}\n```")
    return "\n\n".join(blocks) if blocks else "(no test-case solutions)"


def _validator_review_user(description: str, category: str, spec: dict[str, Any],
                           inputs: list[dict[str, str]],
                           reviewable: dict[str, LangResult]) -> str:
    shared = "\n".join(
        f"- [{t.get('kind', 'normal')}] {t['name']}: {t['stdin']!r}" for t in inputs)
    parts = [
        f"## Problem\n{description}\n\nCategory: {category}",
        f"## Shared contract\nstdin_format: {spec['stdin_format']}\n"
        f"output_format: {spec['output_format']}\nfunction_name: {spec['function_name']}",
        f"## Shared test cases (stdin inputs every validator must handle)\n{shared}",
    ]
    for lang, lr in reviewable.items():
        parts.append(
            f"## {lang} — validator function (strategy: {lr.strategy})\n"
            f"Execution summary:\n{_exec_summary(lr)}\n\n"
            f"```{lang}\n{lr.driver_code}\n```\n\n"
            f"## {lang} — test-case solutions the validator is checked against\n"
            + _suite_blocks(lang, lr.test_suite or {})
        )
    parts.append("Review each validator function TOGETHER WITH its test cases per your "
                 "instructions: per-language correctness against the shared inputs and "
                 "solutions, AND cross-language consistency.")
    return "\n\n".join(parts)


def _single_validator_review_user(description: str, category: str, spec: dict[str, Any],
                                  inputs: list[dict[str, str]], lr: LangResult) -> str:
    shared = "\n".join(
        f"- [{t.get('kind', 'normal')}] {t['name']}: {t['stdin']!r}" for t in inputs)
    return (
        f"## Problem\n{description}\n\nCategory: {category}\n\n"
        f"## Shared contract\nstdin_format: {spec['stdin_format']}\n"
        f"output_format: {spec['output_format']}\nfunction_name: {spec['function_name']}\n\n"
        f"## Test cases ({len(inputs)} shared stdin inputs)\n{shared}\n\n"
        f"## {lr.language} validator function (strategy: {lr.strategy})\n"
        f"Execution summary:\n{_exec_summary(lr)}\n\n```{lr.language}\n{lr.driver_code}\n```\n\n"
        f"## Test-case solutions\n{_suite_blocks(lr.language, lr.test_suite or {})}\n\n"
        "Review this validator function across the test cases per your instructions."
    )


_TRANSLATE_DRIVER_KEY = {
    "python": "python_driver",
    "javascript": "javascript_driver",
    "cpp": "cpp_driver",
    "java": "java_driver",
}


def _translate_user(spec: dict[str, Any], source_lang: str, source_driver: str,
                    targets: list[str]) -> str:
    sigs = spec.get("signatures", {})
    sig_lines = "\n".join(f"- {l}: {sigs.get(l, '')}" for l in targets)
    return (
        f"## Shared contract\nstdin_format: {spec['stdin_format']}\n"
        f"output_format: {spec['output_format']}\nfunction_name: {spec['function_name']}\n\n"
        f"## Per-language solution signatures (the user's solution matches these)\n{sig_lines}\n\n"
        f"## Verified & reviewed {source_lang.upper()} validator to port\n"
        f"```{source_lang}\n{source_driver}\n```\n\n"
        f"Port this validator to these TARGET languages only: {', '.join(targets)}. "
        "Return the complete driver for each target."
    )


def _code_review_user(description: str, category: str, lr: LangResult) -> str:
    return (
        f"## Problem\n{description}\n\nCategory: {category}\n\n"
        f"Execution summary:\n{_exec_summary(lr)}\n\n"
        f"## Complete program ({lr.language})\n"
        f"```{lr.language}\n{lr.full_program}\n```\n\n"
        "Review this complete program end to end per your instructions."
    )


def _process_language(
    lang: str,
    description: str,
    category: str,
    spec: dict[str, Any],
    inputs: list[dict[str, str]],
    agent: Agent,
    max_iterations: int,
    progress: ProgressFn,
) -> LangResult:
    """Author a test suite and a verified validator driver for one language.

    Self-contained so several languages can run concurrently in threads — each
    call only reads the shared (already-finalised) spec/inputs and owns its own
    LangResult.
    """
    runner = get_runner(lang)
    lr = LangResult(language=lang)
    if runner is None or not runner.available():
        lr.available = False
        lr.message = f"No {lang} runtime available."
        progress(lang, lr.message)
        return lr

    spec_blob = _spec_blob(lang, description, category, spec)

    progress(lang, "Authoring test suite (reference / equivalent / wrong)...")
    suite_user = (
        spec_blob + "\n\nShared stdin inputs (use these):\n"
        + "\n".join(f"- {t['name']}: {t['stdin']!r}" for t in inputs)
        + "\n\nWrite the reference / equivalent / wrong solutions."
    )
    suite = _normalize_suite(agent.structured(
        system=prompts.ml_testsuite_system(lang),
        user=suite_user,
        tool_name="emit_test_suite",
        tool_description=f"Return {lang} reference/equivalent/wrong solutions.",
        schema=prompts.ml_testsuite_schema(),
        max_tokens=_SUITE_MAX_TOKENS,
    ))
    # Guard: solutions must be bare functions/classes. If any reads stdin or
    # defines a main/entry point it would collide with the driver, so send it
    # back to be fixed before we waste an execution round on it. We re-lint
    # after every regeneration (including the last) so a non-compliant suite
    # never slips through unchecked.
    attempts = 0
    while True:
        lint_feedback = _lint_suite(lang, suite)
        if not lint_feedback or attempts >= max_iterations:
            break
        attempts += 1
        progress(lang, f"Test suite not contract-compliant; regenerating "
                       f"(attempt {attempts})...")
        suite = _normalize_suite(agent.structured(
            system=prompts.ml_testsuite_system(lang),
            user=suite_user + "\n\n" + lint_feedback,
            tool_name="emit_test_suite",
            tool_description=f"Return {lang} reference/equivalent/wrong solutions.",
            schema=prompts.ml_testsuite_schema(),
            max_tokens=_SUITE_MAX_TOKENS,
        ))
    if lint_feedback:
        progress(lang, "Warning: test suite still not fully contract-compliant; "
                       "executing anyway.")
    lr.test_suite = suite

    driver_feedback = ""   # send back to the transformer (driver is at fault)
    suite_feedback = ""    # send back to the test-suite author (suite is at fault)
    suite_regens = 0       # how many times we have regenerated the suite
    for iteration in range(1, max_iterations + 1):
        lr.iterations = iteration

        # (Re)generate the driver on the first pass, or whenever the previous
        # failure was driver-attributable. A suite-only failure keeps the same
        # driver — regenerating it could never fix a bad test example.
        if lr.driver_code is None or driver_feedback:
            progress(lang, f"[iter {iteration}] Generating validator driver...")
            _apply_driver(lr, _generate_driver(
                agent, lang, category, spec_blob, driver_feedback))

        # The driver is fine but the test suite was unconvincing (e.g. a "wrong"
        # solution that no shared input actually distinguishes). Regenerate the
        # suite so verification exercises the validator properly.
        if suite_feedback:
            progress(lang, f"[iter {iteration}] Regenerating test suite "
                           "(previous one was not discriminating)...")
            suite = _normalize_suite(agent.structured(
                system=prompts.ml_testsuite_system(lang),
                user=suite_user + "\n\n" + suite_feedback,
                tool_name="emit_test_suite",
                tool_description=f"Return {lang} reference/equivalent/wrong solutions.",
                schema=prompts.ml_testsuite_schema(),
                max_tokens=_SUITE_MAX_TOKENS,
            ))
            lr.test_suite = suite
            suite_regens += 1

        progress(lang, f"[iter {iteration}] Compiling & running test cases...")
        ex = verify_by_execution(lr.driver_code, suite, runner, inputs)
        lr.executions.append(ex)
        for w in ex.harness_warnings:
            progress(lang, f"[iter {iteration}] (harness) {w}")
        progress(lang, f"[iter {iteration}] {ex.passed}/{ex.total} cases pass -> "
                 + ("OK" if ex.ok else "FAILURES"))
        if ex.ok:
            lr.verified_ok = True
            lr.message = "Validator generated and verified by execution."
            break

        # Attribute the failure. A failing REFERENCE/EQUIVALENT case or a driver
        # compile error means the driver is wrong. A failing WRONG case with no
        # such driver fault means the suite under-tested the validator -> the
        # test suite (not the driver) needs to change.
        driver_feedback, suite_feedback = _attribute_failure(ex, suite_regens)
    if not lr.verified_ok:
        lr.message = (f"Validator generated; the agent's auto-fix loop could not get "
                      f"every case passing after {max_iterations} iterations. The "
                      "agent review and the failing cases are below.")

    # Surface every CORRECT solution (reference + the distinct alternative
    # approaches) as deliverables — these are the "all possible solutions".
    if suite.get("reference_solution"):
        lr.solutions = [{"label": "reference", "approach": "reference / optimal",
                         "code": suite["reference_solution"]["code"]}]
    lr.solutions += [{"label": s.get("name", f"approach_{i+1}"),
                      "approach": s.get("note", "alternative approach"),
                      "code": s["code"]}
                     for i, s in enumerate(suite.get("equivalent_solutions", []))]

    # Surface a complete, runnable program (reference solution + driver, with
    # the driver's main/entry point) regardless of verdict.
    _surface_full_program(lr, runner)
    return lr


def run_multilang(
    description: str,
    languages: list[str] | None = None,
    agent: Agent | None = None,
    max_iterations: int | None = None,
    progress: ProgressFn = _noop,
) -> MLResult:
    agent = agent or Agent()
    languages = languages or list(ALL_LANGUAGES)
    if max_iterations is None:
        max_iterations = int(os.environ.get("MO_MAX_ITERATIONS", "3"))

    # ---- 1. Classify ------------------------------------------------------ #
    progress("classify", "Classifying question (multiple-outputs? which kind?)...")
    classification = agent.structured(
        system=prompts.ML_CLASSIFIER_SYSTEM,
        user="## Problem description\n" + description + "\n\nClassify this question.",
        tool_name="classify_question",
        tool_description="Report the multiple-outputs classification.",
        schema=prompts.CLASSIFIER_SCHEMA,
    )
    category = classification["category"]
    if not classification.get("is_multiple_outputs") or category == "SINGLE":
        progress("classify", f"Rejected: {classification['short_summary']}")
        return MLResult(
            accepted=False, category=category, classification=classification,
            message="This is a single-output question; the validator generator does "
            "not apply. " + classification["short_summary"],
        )
    progress("classify", f"{category} ({classification['confidence']}): "
             + classification["short_summary"])

    # ---- 2. Spec + 10 test inputs (one call) ------------------------------ #
    # The spec author defines the shared contract and the 10 stdin test inputs in a
    # single call (no separate coverage-critic pass / coverage write-up).
    progress("spec", "Designing the shared I/O contract and 10 test inputs "
             "(corner/edge cases included)...")
    spec = agent.structured(
        system=prompts.SPEC_SYSTEM,
        user=f"## Problem description\n{description}\n\nCategory: {category}.\n"
        "Author the shared contract, per-language signatures, and exactly 10 test "
        "inputs covering all four kinds (normal/edge/long/stress).",
        tool_name="emit_spec",
        tool_description="Return the I/O contract, signatures, and 10 test inputs.",
        schema=prompts.SPEC_SCHEMA,
    )
    inputs = [{"name": t["name"], "stdin": t["stdin"],
               "kind": t.get("kind", "normal")} for t in spec["test_inputs"]]

    # Run exactly 10 test cases against the base language (trim if the spec emitted
    # more). The base language compiles+runs every solution against every case, so a
    # tight bound keeps the one executed run fast (the others are translated).
    verify_cases = int(os.environ.get("MO_VERIFY_CASES", "10"))
    if len(inputs) > verify_cases:
        inputs = inputs[:verify_cases]
    progress("spec", f"Contract ready; {len(inputs)} test inputs prepared.")

    result = MLResult(accepted=True, category=category, classification=classification,
                      spec=spec, inputs=inputs, coverage=None)

    # Persist the shared test cases to test_cases.json for inspection / re-running.
    result.test_cases_path = _write_test_cases(spec, category, inputs)
    if result.test_cases_path:
        progress("coverage", f"Wrote {len(inputs)} test cases to "
                 f"{result.test_cases_path}.")

    # ---- 3. Base language: test suite + driver + EXECUTION verify ---------- #
    # Optimised flow: do the expensive work (generate solutions, compile & run, and
    # review) in ONE language only, then translate the reviewed validator to the
    # others without re-running anything.
    #
    # The base is ALWAYS Java when a JDK is available — Java is the language whose
    # validator is actually compiled, run and auto-fixed against the test suite, and
    # every other language is a translation of it (so the executed-and-verified
    # artifact the user relies on is the Java one). Only if no JDK is present do we
    # fall back to the first usable selected language.
    base = _pick_base(languages)
    if base is None:
        result.message = ("No usable language runtime available to build/verify the "
                          "base validator.")
        return result

    progress(base, f"Base language: building & execution-verifying the {base} "
             "validator against the test cases...")
    try:
        base_lr = _process_language(base, description, category, spec, inputs, agent,
                                    max_iterations, progress)
    except Exception as exc:
        progress(base, f"Error: {type(exc).__name__}: {exc}")
        base_lr = LangResult(language=base)
        base_lr.message = f"{type(exc).__name__}: {exc}"
    result.languages[base] = base_lr

    if not base_lr.driver_code:
        result.message = (
            f"No {base} validator was produced — the base language failed before "
            "emitting one (API / network / budget error). Check the log and retry."
        )
        return result

    # ---- 4. Reconcile execution with an agentic review --------------------- #
    # Execution is the GROUND TRUTH: the driver is compiled and run against the
    # reference, every equivalent (correct) solution, and every wrong / sub-optimal
    # solution. With the suite now required to include a valid-but-sub-optimal wrong
    # answer, that run already exercises the optimality check too.
    #
    # So we only spend LLM calls on the agentic review when execution could NOT
    # verify the driver — that is exactly when the review's diagnosis is worth it,
    # and it can only help (it regenerates + re-verifies). When execution already
    # passed, we trust it: running the reviewer there only risks a noisy false
    # "needs review" (a check it imagines missing is one the wrong/sub-optimal cases
    # already proved present) or a regeneration that breaks a known-good driver.
    spec_blob_base = _spec_blob(base, description, category, spec)
    if base_lr.verified_ok:
        result.validator_review = {
            "overall_ok": True,
            "summary": (f"Execution-verified in {base} on {len(inputs)} test cases — "
                        "the reference, every correct/equivalent solution and every "
                        "wrong/sub-optimal solution are judged correctly."),
            "cross_language_consistency": (
                f"Validator authored & execution-verified in {base}, then translated "
                "to the other languages (which inherit its verified logic)."),
            "per_language": [{"language": base, "ok": True, "issues": [],
                              "fix_suggestions": ""}],
        }
        progress("validator-review", f"{base} validator execution-verified; "
                 "skipping the advisory review (no extra model call needed).")
    else:
        # Execution couldn't get everything green — bring in the reviewer to diagnose
        # and fix, looping a few rounds (review -> regenerate -> re-verify).
        review_rounds = int(os.environ.get("MO_REVIEW_ROUNDS", "2"))
        for rnd in range(1, review_rounds + 1):
            progress("validator-review", f"[round {rnd}] Execution did not fully "
                     f"verify; reviewing the {base} validator to diagnose & fix...")
            review = agent.structured(
                system=prompts.validator_function_review_system(base, category),
                user=_single_validator_review_user(description, category, spec, inputs, base_lr),
                tool_name="report_validator_review",
                tool_description=f"Report the {base} validator review.",
                schema=prompts.COMPLETE_CODE_REVIEW_SCHEMA,
            )
            base_lr.code_review = review
            # The driver is good only if EXECUTION now passes; the review verdict is
            # advisory on top of that ground truth.
            overall_ok = base_lr.verified_ok
            result.validator_review = {
                "overall_ok": overall_ok,
                "summary": ("Execution-verified after review-guided fixes."
                            if overall_ok else
                            (review.get("fix_suggestions")
                             or "Execution still reports failing cases; see below.")),
                "cross_language_consistency": (
                    f"Validator authored, execution-verified and reviewed in {base}, "
                    "then translated to the other languages."),
                "per_language": [{
                    "language": base, "ok": overall_ok,
                    "issues": review.get("issues", []),
                    "fix_suggestions": review.get("fix_suggestions", ""),
                }],
            }
            if overall_ok:
                progress("validator-review", f"The {base} validator now passes "
                         "execution after review-guided fixes.")
                break
            if rnd == review_rounds:
                progress("validator-review", "Issues remain after the final review "
                         "round; see the review notes.")
                break
            fb = review.get("fix_suggestions", "")
            if review.get("issues"):
                fb += "\nIssues:\n- " + "\n- ".join(review["issues"])
            _regen_and_verify(base_lr, base, category, spec_blob_base,
                              inputs, agent, fb, progress, f"review r{rnd}")

    # ---- 5. Translate the reviewed validator to the other languages -------- #
    # ONE LLM call ports the Java validator to the remaining languages. These are
    # NOT executed (by request) — they inherit the base's verified+reviewed logic.
    targets = [l for l in ALL_LANGUAGES if l != base and l in _TRANSLATE_DRIVER_KEY]
    if targets:
        progress("translate", f"Translating the reviewed {base} validator to "
                 f"{', '.join(targets)} (single LLM call, no execution)...")
        translation = agent.structured(
            system=prompts.TRANSLATE_SYSTEM,
            user=_translate_user(spec, base, base_lr.driver_code, targets),
            tool_name="emit_translations",
            tool_description="Return the ported validator driver for each language.",
            schema=prompts.TRANSLATE_SCHEMA,
            max_tokens=_SUITE_MAX_TOKENS,
        )
        for lang in targets:
            runner = get_runner(lang)
            lr = LangResult(language=lang)
            lr.available = bool(runner and runner.available())
            lr.driver_code = sanitize_solution(lang, translation.get(
                _TRANSLATE_DRIVER_KEY[lang], "") or "")
            lr.strategy = base_lr.strategy
            lr.translated = True
            lr.validation_notes = (f"Translated from the execution-verified {base} "
                                   "validator; not executed (logic inherited from the "
                                   "base).")
            lr.message = (f"Translated from the {base} validator (not executed).")
            result.languages[lang] = lr
            progress("translate", f"{lang}: validator translated.")

    base_ok = ("verified by execution" if base_lr.verified_ok
               else "generated (execution still reports failing cases)")
    result.message = (
        f"{base} validator {base_ok} on {len(inputs)} test cases; "
        f"{len(targets)} other language(s) translated from it (not executed)."
    )
    return result
