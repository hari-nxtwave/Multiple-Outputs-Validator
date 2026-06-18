#!/usr/bin/env python3
"""Evaluate the multiple-outputs CLASSIFIER against the labeled set.

This runs the EXACT classifier the website uses (``ML_CLASSIFIER_SYSTEM``,
description-only) over ``evals/classification.jsonl`` and reports per-item
correctness, overall accuracy, and a confusion matrix. The classifier is the
cheapest and highest-leverage stage to evaluate because the whole pipeline
branches on its verdict.

Usage:
    python -m evals.run_classification                # whole set
    python -m evals.run_classification --limit 6      # quick smoke test
    python -m evals.run_classification --jobs 8       # more concurrency
    python -m evals.run_classification --verbose      # show summaries on misses
    python -m evals.run_classification --difficulty hard
"""

from __future__ import annotations

import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mo_validator import prompts  # noqa: E402
from mo_validator.agent import Agent, AgentError  # noqa: E402

HERE = Path(__file__).resolve().parent
DATA = HERE / "classification.jsonl"
CATS = ["ANY_ORDER", "ANY_VALID", "SINGLE"]


def load(path: Path) -> list[dict]:
    items = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("//"):
            continue
        items.append(json.loads(line))
    return items


def classify(agent: Agent, description: str) -> dict:
    return agent.structured(
        system=prompts.ML_CLASSIFIER_SYSTEM,
        user="## Problem description\n" + description + "\n\nClassify this question.",
        tool_name="classify_question",
        tool_description="Report the multiple-outputs classification.",
        schema=prompts.CLASSIFIER_SCHEMA,
    )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data", default=str(DATA))
    ap.add_argument("--limit", type=int, help="only the first N items")
    ap.add_argument("--jobs", type=int, default=4, help="concurrent classifier calls")
    ap.add_argument("--difficulty", choices=["easy", "medium", "hard"],
                    help="only items at this difficulty")
    ap.add_argument("--verbose", action="store_true",
                    help="print the model's one-line summary on misses")
    args = ap.parse_args()

    items = load(Path(args.data))
    if args.difficulty:
        items = [it for it in items if it.get("difficulty") == args.difficulty]
    if args.limit:
        items = items[: args.limit]
    if not items:
        print("No eval items matched.")
        return 2

    try:
        agent = Agent()
    except AgentError as exc:
        print(f"ERROR: {exc}")
        return 2

    def run_one(it: dict):
        try:
            res = classify(agent, it["description"])
            return it, res.get("category"), bool(res.get("is_multiple_outputs")), res, None
        except Exception as exc:  # noqa: BLE001 - surface any failure per item
            return it, None, None, None, f"{type(exc).__name__}: {exc}"

    with ThreadPoolExecutor(max_workers=max(1, args.jobs)) as pool:
        results = list(pool.map(run_one, items))

    confusion = {a: {b: 0 for b in CATS} for a in CATS}
    n_pass = n_mo_pass = errors = 0
    print(f"{'id':34}{'expected':11}{'got':11}{'mo':5}result")
    print("-" * 78)
    for it, got, got_mo, res, err in results:
        exp = it["category"]
        exp_mo = bool(it.get("is_multiple_outputs"))
        if err:
            errors += 1
            print(f"{it['id']:34}{exp:11}{'ERROR':11}{'':5}{err[:28]}")
            continue
        ok = got == exp
        n_pass += ok
        n_mo_pass += (got_mo == exp_mo)
        if exp in confusion and got in confusion[exp]:
            confusion[exp][got] += 1
        mark = "PASS" if ok else "FAIL"
        mo_mark = "ok" if got_mo == exp_mo else "X"
        line = f"{it['id']:34}{exp:11}{(got or '?'):11}{mo_mark:5}{mark}"
        if not ok and args.verbose and res:
            line += "  | " + str(res.get("short_summary", ""))[:64]
        print(line)

    scored = len(results) - errors
    acc = (n_pass / scored * 100) if scored else 0.0
    mo_acc = (n_mo_pass / scored * 100) if scored else 0.0
    print("-" * 78)
    print(f"category accuracy:        {n_pass}/{scored} = {acc:.1f}%")
    print(f"is_multiple_outputs acc:  {n_mo_pass}/{scored} = {mo_acc:.1f}%")
    if errors:
        print(f"errors (not scored):      {errors}")

    print("\nconfusion matrix (rows = expected, cols = predicted):")
    print(" " * 12 + "".join(f"{c:>12}" for c in CATS))
    for a in CATS:
        print(f"{a:>12}" + "".join(f"{confusion[a][b]:>12}" for b in CATS))

    # Non-zero exit if anything was wrong, so CI can gate on it.
    return 0 if (errors == 0 and n_pass == scored) else 1


if __name__ == "__main__":
    raise SystemExit(main())
