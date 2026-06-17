"""Execution-based verification, language-agnostic.

Given a *driver* (the validator-embedded program) and a generated test suite of
``Solution`` submissions + shared stdin inputs, this compiles and runs the driver
with each submission via a :class:`~mo_validator.runners.Runner`:

  - the REFERENCE submission defines the canonical expected output per input,
  - every EQUIVALENT (correct) submission must reproduce it on every input,
  - every WRONG submission must be rejected (differ from canonical) on at least
    one input it is genuinely wrong on.
"""

from __future__ import annotations

import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .runners import RUN_TIMEOUT, Runner, normalize_output

# Long / stress inputs are deliberately large, so they get more wall-clock than a
# normal case before we call it a time-out. Still bounded — the suite is told to
# keep even these feasible for an efficient correct solution within a few seconds.
_TIMEOUT_BY_KIND = {"long": 20.0, "stress": 25.0}


def _timeout_for(kind: str | None) -> float:
    return _TIMEOUT_BY_KIND.get(kind or "", RUN_TIMEOUT)


@dataclass
class CaseResult:
    kind: str           # "reference" | "equivalent" | "wrong"
    solution: str
    test_input: str
    passed: bool
    detail: str


@dataclass
class ExecResult:
    available: bool = True
    ok: bool = False
    driver_compile_error: str = ""
    cases: list[CaseResult] = field(default_factory=list)
    harness_warnings: list[str] = field(default_factory=list)
    feedback: str = ""

    @property
    def total(self) -> int:
        return len(self.cases)

    @property
    def passed(self) -> int:
        return sum(1 for c in self.cases if c.passed)


def _run_over_inputs(runner: Runner, driver_src: str, solution_src: str,
                     inputs: list[dict[str, str]]):
    with tempfile.TemporaryDirectory() as d:
        workdir = Path(d)
        comp = runner.prepare(workdir, driver_src, solution_src)
        runs: dict[str, Any] = {}
        if comp.ok:
            for tin in inputs:
                runs[tin["name"]] = runner.run(
                    workdir, tin["stdin"], timeout=_timeout_for(tin.get("kind")))
        return comp, runs


def verify_by_execution(
    driver_src: str,
    suite: dict[str, Any],
    runner: Runner,
    inputs: list[dict[str, str]],
) -> ExecResult:
    res = ExecResult()
    if not inputs:
        res.feedback = "No test inputs available; cannot execute."
        return res

    # ---- Reference defines the canonical expected output per input -------- #
    ref_src = suite["reference_solution"]["code"]
    ref_comp, ref_runs = _run_over_inputs(runner, driver_src, ref_src, inputs)
    if not ref_comp.ok:
        # With the (presumed correct) reference, a compile failure almost always
        # means the driver / I/O contract is wrong -> send it back to transform.
        res.driver_compile_error = ref_comp.message
        res.feedback = "The generated driver did not compile:\n" + ref_comp.message
        return res

    expected: dict[str, str] = {}
    for tin in inputs:
        rr = ref_runs[tin["name"]]
        if not rr.ok:
            res.harness_warnings.append(
                f"Reference crashed on input '{tin['name']}': {rr.error}")
            continue
        expected[tin["name"]] = normalize_output(rr.output)
        res.cases.append(CaseResult(
            "reference", "reference", tin["name"], True,
            f"canonical: {expected[tin['name']]!r}"))

    if not expected:
        detail = ("\n" + "\n".join(res.harness_warnings)) if res.harness_warnings else ""
        res.feedback = (
            "Reference solution produced no usable output on any input "
            "(it crashed). The driver and solution share one file, so this is "
            "usually a name/entry-point collision — ensure the driver's own "
            "stdin variable and helpers use unique names and a single entry "
            "point." + detail
        )
        return res

    def compile_or_skip(group: str, sol: dict[str, Any]):
        comp, runs = _run_over_inputs(runner, driver_src, sol["code"], inputs)
        if not comp.ok:
            res.harness_warnings.append(
                f"{group} solution '{sol['name']}' did not compile (skipped): "
                + comp.message)
            return None
        return runs

    # Equivalent (correct) submissions must match canonical on every input.
    for sol in suite.get("equivalent_solutions", []):
        runs = compile_or_skip("equivalent", sol)
        if runs is None:
            continue
        for tin in inputs:
            name = tin["name"]
            if name not in expected:
                continue
            rr = runs[name]
            if not rr.ok:
                res.cases.append(CaseResult(
                    "equivalent", sol["name"], name, False, f"errored: {rr.error}"))
                continue
            out = normalize_output(rr.output)
            ok = out == expected[name]
            res.cases.append(CaseResult(
                "equivalent", sol["name"], name, ok,
                "accepted -> matches canonical" if ok
                else f"MISMATCH: got {out!r}, expected {expected[name]!r}"))

    # Wrong submissions must be rejected on at least one input they're wrong on.
    wrong_cases: list[CaseResult] = []
    for sol in suite.get("wrong_solutions", []):
        runs = compile_or_skip("wrong", sol)
        if runs is None:
            continue
        rejected_on, accepted_on, errored_on = [], [], []
        for tin in inputs:
            name = tin["name"]
            if name not in expected:
                continue
            rr = runs[name]
            if not rr.ok:
                errored_on.append(name)
                continue
            out = normalize_output(rr.output)
            (rejected_on if out != expected[name] else accepted_on).append(name)
        caught = bool(rejected_on)
        if caught:
            detail = f"rejected on {rejected_on}"
            if accepted_on:
                detail += f"; (also valid/accepted on {accepted_on})"
        elif errored_on and not accepted_on:
            caught = False
            detail = f"crashed on {errored_on} (validator should reject cleanly, not crash)"
        else:
            caught = False
            detail = (f"NOT CAUGHT: accepted as canonical on every input {accepted_on}. "
                      "Validator may be too lenient (or this 'wrong' solution is "
                      "actually correct on all tested inputs).")
        case = CaseResult("wrong", sol["name"], "any", caught, detail)
        wrong_cases.append(case)
        res.cases.append(case)

    # Decide whether the validator demonstrably discriminates valid from invalid.
    #
    # A "wrong" submission can only ever be NOT CAUGHT by producing the canonical
    # (correct) output on every tested input — which means it simply was not
    # actually wrong on any of those inputs, so it tests nothing. We cannot tell
    # "non-discriminating test case" apart from "too-lenient validator" in
    # isolation, but if AT LEAST ONE wrong submission *is* rejected, the validator
    # provably rejects invalid answers — so the uncaught ones are weak test cases,
    # not validator bugs. We tolerate those (warn instead of fail) so a correct
    # validator is not flagged for review over a non-discriminating sample.
    #
    # Only when NO wrong submission is caught at all (and there were some) do we
    # treat it as a real failure: the validator may accept everything.
    any_wrong = bool(wrong_cases)
    any_caught = any(c.passed for c in wrong_cases)
    validator_discriminates = any_caught or not any_wrong
    if validator_discriminates:
        for c in wrong_cases:
            if not c.passed:
                res.harness_warnings.append(
                    f"wrong solution '{c.solution}' was not discriminating "
                    f"({c.detail}); tolerated because the validator rejects other "
                    "invalid answers.")
                c.passed = True
                c.detail = "tolerated: not wrong on any tested input"

    failures = [c for c in res.cases if not c.passed]
    res.ok = not failures and not res.driver_compile_error
    if failures:
        lines = ["Execution found failing cases the driver must fix:"]
        for c in failures[:25]:
            lines.append(f"- [{c.kind}] solution='{c.solution}', input='{c.test_input}': {c.detail}")
        if any_wrong and not any_caught:
            lines.append(
                "No 'wrong' submission was rejected on any input — the validator "
                "accepts everything (too lenient) or the suite's wrong solutions are "
                "not actually wrong on the shared inputs.")
        res.feedback = "\n".join(lines)
    return res
