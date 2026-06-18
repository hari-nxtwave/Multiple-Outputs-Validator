# Evals

Labeled datasets + runners for measuring the validator generator's quality. There
are two eval sets, one per pipeline stage that can go wrong:

| Set | File | What it measures | Cost |
|-----|------|------------------|------|
| **Classification** | `classification.jsonl` | Does the classifier pick the right branch (`ANY_ORDER` / `ANY_VALID` / `SINGLE`)? | cheap — 1 model call/item |
| **Pipeline (end-to-end)** | `pipeline.jsonl` | Does the full run accept/reject correctly, classify correctly, produce an **execution-verified** base validator, and translate the rest? | expensive — full pipeline + compile/run per item |

The classifier is the highest-leverage thing to test: the entire pipeline branches
on its verdict, and a single call is fast and cheap, so run it often. The pipeline
eval actually compiles and runs code, so keep it small and run it less frequently.

## Setup

Same as the app — the gateway key must be available (the runners load `.env`
automatically via `mo_validator`):

```bash
export MO_API_KEY=...      # or OPENAI_API_KEY / ANTHROPIC_API_KEY
```

The pipeline eval also needs the language runtimes (`python`, `node`, `g++`,
`javac`/`java`) on PATH for the base languages it uses; items whose base runtime
is missing are skipped.

## Running

From the project root:

```bash
# Classification — whole set, with a confusion matrix
python -m evals.run_classification

# quick smoke test / only the hard (trap) cases / show summaries on misses
python -m evals.run_classification --limit 6
python -m evals.run_classification --difficulty hard
python -m evals.run_classification --verbose --jobs 8

# End-to-end pipeline eval (compiles & runs — slower)
python -m evals.run_pipeline
python -m evals.run_pipeline --limit 2 --iters 3
```

Both runners exit non-zero if any item fails, so they can gate CI.

## Dataset format

`classification.jsonl` — one JSON object per line:

```json
{"id": "...", "category": "ANY_ORDER|ANY_VALID|SINGLE", "is_multiple_outputs": true,
 "difficulty": "easy|medium|hard", "description": "...", "notes": "why this label"}
```

`pipeline.jsonl` — one JSON object per line:

```json
{"id": "...", "base": "java|python|javascript|cpp", "accepted": true,
 "category": "ANY_ORDER|ANY_VALID|SINGLE", "description": "..."}
```

`accepted: false` marks `SINGLE` questions that the tool must reject. `# ...` /
`// ...` style comment lines are ignored by the loaders.

## Coverage notes

The classification set deliberately includes **boundary traps** (difficulty
`hard`) that probe the exact distinctions the README cares about:

- `topological_sort` — looks like `ANY_ORDER` (same vertices) but different valid
  orderings are genuinely different ordered sequences → `ANY_VALID`.
- `all_paths_dag` (return the *full set* of paths, any order → `ANY_ORDER`) vs
  `one_path_s_t` (return *any one* path → `ANY_VALID`).
- `two_sum_unique` ("exactly one solution exists" → `SINGLE`) vs
  `two_sum_any_pair` ("return any one" → `ANY_VALID`).
- `lis_length` (the length only → `SINGLE`) vs `any_longest_increasing_subseq`
  (the subsequence itself → `ANY_VALID`).
- `sort_stable_note` — "order of equal elements doesn't matter" wording, but the
  printed output is identical for all correct submissions → `SINGLE`, not
  `ANY_ORDER`.

When you find a real-world question the tool misclassifies, add it here (with the
correct label and a one-line `notes` justification) so the regression is tracked.
