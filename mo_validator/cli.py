"""Command-line entry point: turn a question file into a validating Main.java."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

from .agent import Agent, AgentError
from .parser import parse_question
from .pipeline import Result, run_pipeline


def _progress(msg: str) -> None:
    print(f"  • {msg}", file=sys.stderr, flush=True)


def _write_testsuite(out_dir: Path, suite: dict) -> None:
    sdir = out_dir / "testsuite"
    sdir.mkdir(parents=True, exist_ok=True)
    (sdir / "reference.Solution.java").write_text(suite["reference_solution"]["code"], "utf-8")
    for s in suite.get("equivalent_solutions", []):
        safe = "".join(c if c.isalnum() else "_" for c in s["name"])
        (sdir / f"equivalent_{safe}.Solution.java").write_text(s["code"], "utf-8")
    for s in suite.get("wrong_solutions", []):
        safe = "".join(c if c.isalnum() else "_" for c in s["name"])
        (sdir / f"wrong_{safe}.Solution.java").write_text(s["code"], "utf-8")
    for t in suite.get("test_inputs", []):
        safe = "".join(c if c.isalnum() else "_" for c in t["name"])
        (sdir / f"input_{safe}.txt").write_text(t["stdin"], "utf-8")


def _write_report(out_dir: Path, result: Result) -> None:
    lines = ["# Multiple-outputs validator report\n"]
    cls = result.classification
    lines += [
        f"**Classification:** {result.category} ({cls.get('confidence', '?')} confidence)\n",
        f"> {cls.get('short_summary', '').strip()}\n",
        f"\n**Verification mode:** {result.verify_mode}\n",
        f"**Verified clean:** {'yes' if result.verified_ok else 'NO — review needed'}\n",
        "\n<details><summary>Classifier reasoning</summary>\n\n"
        + cls.get("reasoning", "").strip() + "\n\n</details>\n",
    ]

    if result.transform:
        lines += [
            f"\n## Transformation — strategy: `{result.transform['strategy']}`\n",
            result.transform.get("validation_notes", "").strip() + "\n",
        ]

    if result.verify_mode == "execution" and result.executions:
        lines.append(f"\n## Execution verification ({result.iterations} iteration(s))\n")
        for i, ex in enumerate(result.executions, 1):
            lines.append(f"\n### Iteration {i} — {ex.passed}/{ex.total} cases pass"
                         f"{' ✅' if ex.ok else ' ❌'}\n")
            if ex.driver_compile_error:
                lines.append("**Main.java compile error:**\n\n```\n"
                             + ex.driver_compile_error + "\n```\n")
            for c in ex.cases:
                if c.kind == "reference":
                    continue
                mark = "✅" if c.passed else "❌"
                lines.append(f"- {mark} [{c.kind}] `{c.solution}` on `{c.test_input}` — {c.detail}")
            for w in ex.harness_warnings:
                lines.append(f"- ⚠️ harness: {w}")
    elif result.verifications:
        lines.append(f"\n## Agentic verification ({result.iterations} iteration(s))\n")
        for i, v in enumerate(result.verifications, 1):
            lines.append(f"\n### Iteration {i} — "
                         f"{'CORRECT' if v['is_correct'] else 'BUGS FOUND'}\n")
            for s in v.get("test_scenarios", []):
                mark = "✅" if s.get("passes") else "❌"
                verdict = "accept" if s.get("should_be_accepted") else "reject"
                lines.append(f"- {mark} **{s['name']}** (should {verdict})")
            if v.get("bugs"):
                lines.append("\n**Bugs:**")
                lines += [f"- {b}" for b in v["bugs"]]

    (out_dir / "report.md").write_text("\n".join(lines), encoding="utf-8")

    payload = {
        "accepted": result.accepted,
        "category": result.category,
        "verify_mode": result.verify_mode,
        "verified_ok": result.verified_ok,
        "iterations": result.iterations,
        "message": result.message,
        "classification": result.classification,
        "transform_notes": (result.transform or {}).get("validation_notes"),
        "executions": [asdict(e) for e in result.executions],
        "verifications": result.verifications,
    }
    (out_dir / "result.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="mo-validator",
        description="Generate and execution-verify a validator inside the Java "
        "Main for a multiple-outputs coding question. Refuses single-output questions.",
    )
    parser.add_argument("question", type=Path, help="Path to the question file (.md/.txt)")
    parser.add_argument("-o", "--out", type=Path, default=None,
                        help="Output directory (default: ./output/<question-name>)")
    parser.add_argument("--max-iterations", type=int, default=None,
                        help="Max transform/verify loops (default: 3 or $MO_MAX_ITERATIONS)")
    parser.add_argument("--model", default=None, help="Override the Claude model id")
    parser.add_argument("--no-execution", action="store_true",
                        help="Skip compiling/running; use agentic review only")
    args = parser.parse_args(argv)

    if not args.question.exists():
        print(f"error: no such file: {args.question}", file=sys.stderr)
        return 2

    try:
        question = parse_question(args.question.read_text(encoding="utf-8"))
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    out_dir = args.out or (Path("output") / args.question.stem)
    out_dir.mkdir(parents=True, exist_ok=True)

    try:
        agent = Agent(model=args.model)
        result = run_pipeline(
            question, agent=agent, max_iterations=args.max_iterations,
            progress=_progress, allow_execution=not args.no_execution,
        )
    except AgentError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if not result.accepted:
        print("\nNOT A MULTIPLE-OUTPUTS QUESTION — nothing to do.", file=sys.stderr)
        print(result.message, file=sys.stderr)
        _write_report(out_dir, result)
        print(f"\nReport written to {out_dir/'report.md'}", file=sys.stderr)
        return 3

    (out_dir / "Main.java").write_text(result.transformed_main, encoding="utf-8")
    if result.test_suite:
        _write_testsuite(out_dir, result.test_suite)
    _write_report(out_dir, result)

    status = "verified clean" if result.verified_ok else "GENERATED BUT NEEDS REVIEW"
    print(f"\n{result.category}: {status} via {result.verify_mode} "
          f"({result.iterations} iteration(s)).", file=sys.stderr)
    print(f"  {result.message}", file=sys.stderr)
    print(f"\nWrote:\n  {out_dir/'Main.java'}\n  {out_dir/'report.md'}\n  {out_dir/'result.json'}",
          file=sys.stderr)
    if result.test_suite:
        print(f"  {out_dir/'testsuite'}/  (generated Solutions + inputs)", file=sys.stderr)
    return 0 if result.verified_ok else 4


if __name__ == "__main__":
    raise SystemExit(main())
