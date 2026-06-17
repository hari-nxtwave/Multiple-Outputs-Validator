"""Orchestrates the agentic pipeline:

    classify -> [generate test suite] -> loop( transform -> verify )

Verification is EXECUTION-BASED whenever a JDK is available: the rewritten
`Main` is compiled and run against generated reference / alternative-valid /
wrong `Solution`s. If no JDK is found, it falls back to agentic (reasoning-only)
review. Failures feed back into the next transform iteration.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Callable

from . import prompts
from .agent import Agent
from .executor import ExecResult, verify_by_execution
from .runners import get_runner
from .parser import Question

ProgressFn = Callable[[str], None]


@dataclass
class Result:
    accepted: bool
    category: str
    classification: dict[str, Any]
    transformed_main: str | None = None
    transform: dict[str, Any] | None = None
    verify_mode: str = "none"          # "execution" | "agentic" | "none"
    test_suite: dict[str, Any] | None = None
    executions: list[ExecResult] = field(default_factory=list)
    verifications: list[dict[str, Any]] = field(default_factory=list)
    iterations: int = 0
    verified_ok: bool = False
    message: str = ""


def _noop(_: str) -> None:
    pass


def run_pipeline(
    question: Question,
    agent: Agent | None = None,
    max_iterations: int | None = None,
    progress: ProgressFn = _noop,
    allow_execution: bool = True,
) -> Result:
    agent = agent or Agent()
    if max_iterations is None:
        max_iterations = int(os.environ.get("MO_MAX_ITERATIONS", "3"))

    context = question.as_prompt_context()

    # ---- 1. Classify ------------------------------------------------------ #
    progress("Classifying question (multiple-outputs? which kind?)...")
    classification = agent.structured(
        system=prompts.CLASSIFIER_SYSTEM,
        user=context + "\n\nClassify this question.",
        tool_name="classify_question",
        tool_description="Report the multiple-outputs classification.",
        schema=prompts.CLASSIFIER_SCHEMA,
    )
    category = classification["category"]

    if not classification.get("is_multiple_outputs") or category == "SINGLE":
        progress(f"Decision: SINGLE -> {classification['short_summary']}")
        return Result(
            accepted=False,
            category=category,
            classification=classification,
            message="This is a single-output question, so the validator generator "
            "does not apply. " + classification["short_summary"],
        )

    progress(
        f"Decision: {category} ({classification['confidence']} confidence) -> "
        f"{classification['short_summary']}"
    )

    result = Result(accepted=True, category=category, classification=classification)

    # ---- 2. Generate the execution test suite (once) ---------------------- #
    java_runner = get_runner("java")
    jdk = java_runner if (allow_execution and java_runner.available()) else None
    if jdk:
        result.verify_mode = "execution"
        progress("JDK found; building execution test suite...")
        result.test_suite = agent.structured(
            system=prompts.TESTSUITE_SYSTEM,
            user=context + f"\n\nClassification: {category}.\n"
            "Author the test suite (reference / equivalent / wrong solutions + inputs).",
            tool_name="emit_test_suite",
            tool_description="Return Java Solution files and stdin test cases.",
            schema=prompts.TESTSUITE_SCHEMA,
        )
        n_eq = len(result.test_suite.get("equivalent_solutions", []))
        n_wr = len(result.test_suite.get("wrong_solutions", []))
        n_in = len(result.test_suite.get("test_inputs", []))
        progress(f"Test suite: {n_in} inputs, {n_eq} equivalent + {n_wr} wrong solutions.")
    else:
        result.verify_mode = "agentic"
        if allow_execution:
            progress("No JDK found — falling back to agentic (reasoning-only) review.")

    # ---- 3. Transform + verify loop --------------------------------------- #
    feedback = ""
    for iteration in range(1, max_iterations + 1):
        result.iterations = iteration
        progress(f"[iter {iteration}] Transforming `main` ({category})...")

        user = (
            context
            + f"\n\nClassification: {category}.\n"
            "Rewrite `Main` accordingly and return the complete file."
        )
        if feedback:
            user += (
                "\n\nThe previous attempt failed verification. Fix exactly these "
                "issues and return the corrected full file:\n" + feedback
            )

        transform = agent.structured(
            system=prompts.TRANSFORMER_SYSTEM,
            user=user,
            tool_name="emit_transformed_main",
            tool_description="Return the rewritten Main.java and rationale.",
            schema=prompts.TRANSFORMER_SCHEMA,
        )
        result.transform = transform
        result.transformed_main = transform["transformed_main"]

        if result.verify_mode == "execution":
            progress(f"[iter {iteration}] Compiling & running against test suite...")
            exec_res = verify_by_execution(
                result.transformed_main, result.test_suite, jdk,
                result.test_suite.get("test_inputs", []))
            result.executions.append(exec_res)
            for w in exec_res.harness_warnings:
                progress(f"[iter {iteration}] (harness) {w}")
            progress(
                f"[iter {iteration}] Execution: {exec_res.passed}/{exec_res.total} "
                f"cases pass -> {'OK' if exec_res.ok else 'FAILURES'}"
            )
            if exec_res.ok:
                result.verified_ok = True
                result.message = "Validator generated and verified by execution."
                return result
            feedback = exec_res.feedback
            continue

        # Agentic fallback
        progress(f"[iter {iteration}] Verifying (adversarial review)...")
        verification = agent.structured(
            system=prompts.VERIFIER_SYSTEM,
            user=context + f"\n\nClassification: {category}.\n\nCandidate rewritten Main:\n"
            "```java\n" + result.transformed_main + "\n```\n\nReview it adversarially.",
            tool_name="report_verification",
            tool_description="Report the adversarial verification verdict.",
            schema=prompts.VERIFIER_SCHEMA,
        )
        result.verifications.append(verification)
        n_cases = len(verification.get("test_scenarios", []))
        n_fail = sum(1 for s in verification.get("test_scenarios", []) if not s.get("passes"))
        progress(
            f"[iter {iteration}] Verdict: "
            f"{'CORRECT' if verification['is_correct'] else 'BUGS FOUND'} "
            f"({n_cases - n_fail}/{n_cases} traced cases pass)"
        )
        if verification["is_correct"] and not verification.get("bugs"):
            result.verified_ok = True
            result.message = "Validator generated and verified (agentic review)."
            return result
        feedback = verification["fix_suggestions"]
        if verification.get("bugs"):
            feedback += "\nBugs:\n- " + "\n- ".join(verification["bugs"])

    result.verified_ok = False
    mode = "execution" if result.verify_mode == "execution" else "review"
    result.message = (
        f"Generated a validator but {mode} still reports issues after "
        f"{max_iterations} iterations. Review the report before using it."
    )
    return result
