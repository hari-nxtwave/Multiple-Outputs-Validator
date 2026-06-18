#!/usr/bin/env python3
"""End-to-end eval: run the full multi-language pipeline over a labeled set.

For each item it calls ``run_multilang`` (the same entry point the website uses)
on the item's base language and checks:

  1. accept/reject matches the label (SINGLE questions must be REJECTED);
  2. for accepted questions, the predicted category matches the label;
  3. the base-language validator was EXECUTION-VERIFIED (compiled, run, and judged
     every reference / equivalent / wrong+sub-optimal submission correctly);
  4. the other three languages were produced as TRANSLATIONS (not re-executed).

This stage compiles and runs code, so it is the expensive eval — keep the set
small and run it less often than the classification eval.

Usage:
    python -m evals.run_pipeline                  # whole set (sequential)
    python -m evals.run_pipeline --limit 2        # quick subset
    python -m evals.run_pipeline --iters 3        # max iterations per item
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mo_validator.agent import Agent, AgentError  # noqa: E402
from mo_validator.mlpipeline import run_multilang  # noqa: E402
from mo_validator.runners import ALL_LANGUAGES, get_runner  # noqa: E402

HERE = Path(__file__).resolve().parent
DATA = HERE / "pipeline.jsonl"


def load(path: Path) -> list[dict]:
    items = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("//"):
            continue
        items.append(json.loads(line))
    return items


def check(item: dict, result, base: str) -> list[str]:
    """Return a list of failure reasons (empty list == the item passed)."""
    fails: list[str] = []
    exp_accepted = bool(item.get("accepted"))
    if bool(result.accepted) != exp_accepted:
        fails.append(f"accepted={result.accepted}, expected {exp_accepted}")
        return fails  # the rest only makes sense once accept/reject agrees

    if not exp_accepted:
        return fails  # SINGLE: correctly rejected, nothing more to verify

    if result.category != item.get("category"):
        fails.append(f"category={result.category}, expected {item.get('category')}")

    base_lr = (result.languages or {}).get(base)
    if base_lr is None:
        fails.append(f"no result for base language '{base}'")
    elif not base_lr.verified_ok:
        fails.append(f"base '{base}' not execution-verified ({base_lr.message[:60]})")

    translated = [l for l, lr in (result.languages or {}).items()
                  if l != base and lr.translated]
    if not translated:
        fails.append("no translated languages produced")
    return fails


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data", default=str(DATA))
    ap.add_argument("--limit", type=int)
    ap.add_argument("--iters", type=int, default=3, help="max iterations per item")
    args = ap.parse_args()

    items = load(Path(args.data))
    if args.limit:
        items = items[: args.limit]
    if not items:
        print("No eval items.")
        return 2

    try:
        agent = Agent()
    except AgentError as exc:
        print(f"ERROR: {exc}")
        return 2

    n_pass = 0
    for it in items:
        base = it.get("base", "java")
        runner = get_runner(base)
        if base not in ALL_LANGUAGES or runner is None or not runner.available():
            print(f"SKIP  {it['id']:30} (no '{base}' runtime available)")
            continue
        print(f"\n=== {it['id']}  (base={base}, expect "
              f"{'ACCEPT/' + str(it.get('category')) if it.get('accepted') else 'REJECT'}) ===")
        try:
            result = run_multilang(it["description"], languages=[base], agent=agent,
                                   max_iterations=args.iters)
        except Exception as exc:  # noqa: BLE001
            print(f"  FAIL  pipeline raised {type(exc).__name__}: {exc}")
            continue
        fails = check(it, result, base)
        if fails:
            print(f"  FAIL  {it['id']}")
            for f in fails:
                print(f"        - {f}")
        else:
            n_pass += 1
            print(f"  PASS  {result.message[:90]}")

    print("\n" + "=" * 60)
    print(f"pipeline eval: {n_pass}/{len(items)} passed")
    return 0 if n_pass == len(items) else 1


if __name__ == "__main__":
    raise SystemExit(main())
