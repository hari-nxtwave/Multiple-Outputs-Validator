# Multiple-Outputs Validator Generator

An **agentic** tool that converts a *multiple-outputs* coding question into a
single deterministic question, so a normal "compare stdout against one expected
file" autograder can grade it.

It uses Claude (Opus 4.8) as an agent to read the problem, decide what kind of
multiple-outputs question it is, generate a validator-embedded driver, and then
**verify it by actually compiling and running test cases** before handing it back.

Two ways to use it:

- **Website** (`python serve.py`) — paste a problem description; the agent
  generates and execution-verifies a validator in **Python, JavaScript, C++ and
  Java**. *(This is the main interface.)*
- **CLI** (`python run.py <file>`) — transform a single existing Java `Main`
  judge file.

## What it does

Given a question (description + the Java `Main` judge program), the tool runs a
three-agent pipeline:

1. **Classifier** — decides whether the question is multiple-output, and which kind:
   - **`ANY_ORDER`** — the answer *elements* are fixed but order is unspecified
     (e.g. *Group Anagrams*). → resolved by **sorting/normalising** the output in `main`.
   - **`ANY_VALID`** — genuinely different answers are all correct
     (e.g. *Largest Divisible Subset*, *any Hamiltonian path*). → resolved by
     **writing a validator** in `main` that **judges** the user's answer.
   - **`SINGLE`** — the answer is unique. → **rejected**: the tool does nothing,
     because the autograder already works. *(This is the "only work on multiple
     outputs questions" requirement.)*
2. **Transformer** — rewrites `Main`:
   - `ANY_ORDER`: deterministically sorts every level of the output before printing.
   - `ANY_VALID`: two separated parts. **(1)** a `check(...)` that **judges** the
     user's returned value against **every** structural constraint by its properties
     (and, for an *optimisation* problem, that its size/cost equals the optimal
     **scalar**) — it does **not** search for a solution to validate one. **(2)** it
     prints exactly one **canonical answer computed from the input only** (a fixed
     construction like a snake path, a sorted/DP reconstruction, or a bounded
     deterministic search) — identical for every valid submission, so it matches the
     single stored expected output. On invalid it prints the user's raw answer (so it
     mismatches and fails). Different valid answers all collapse to the same canonical.
3. **Verifier (execution-based)** — for *every* multiple-outputs question, a
   fourth agent authors a Java **test suite** (a correct reference `Solution`,
   one or more *alternative-valid* `Solution`s, several *wrong* `Solution`s, and
   stdin cases). The tool then **compiles and runs** the rewritten `Main` against
   each:
   - the reference defines the **canonical expected output** per input,
   - every **alternative-valid** solution must reproduce it (validator accepts →
     prints canonical, or normalisation collapses to the same output),
   - every **wrong** solution must be **rejected** on at least one input it is
     genuinely wrong on (validator rejects → prints the wrong raw answer, which
     mismatches).

   If any check fails, the transformer is re-run with the concrete execution
   feedback (up to `--max-iterations`, default 3). If no JDK is found, it falls
   back to agentic (reasoning-only) review.

## Setup

```bash
pip install -r requirements.txt
export ANTHROPIC_API_KEY=sk-ant-...   # required
```

**Java (for execution verification).** Verification compiles and runs the
generated program, so a JDK must be available. It is auto-discovered from `PATH`,
then `$JAVA_HOME/bin`, then a portable JDK under `~/.local/jdk/jdk-*`. A no-root
way to install one:

```bash
mkdir -p ~/.local/jdk && cd ~/.local/jdk
curl -sL -o jdk.tar.gz \
  "https://api.adoptium.net/v3/binary/latest/21/ga/linux/x64/jdk/hotspot/normal/eclipse"
tar -xzf jdk.tar.gz && rm jdk.tar.gz   # creates ~/.local/jdk/jdk-21.x.x+x
```

If no JDK is found the tool still runs, falling back to agentic review (or pass
`--no-execution` to force that).

For the website you also need the language runtimes you want to verify against —
`python3`, `node`, `g++`, and the JDK above. Any that are missing are simply
skipped (the UI shows which are available).

## Website (multi-language)

```bash
export ANTHROPIC_API_KEY=sk-ant-...
python serve.py            # -> http://127.0.0.1:5000
```

Paste a problem description, tick the languages, and click **Generate & Verify**.
The agentic flow is **Java-first, then translate** — to keep token cost low, the
expensive work (solutions, compile-and-run, review) happens in **one** language:

1. **Classify** — multiple-outputs? `ANY_ORDER` / `ANY_VALID` / `SINGLE` (rejected).
2. **Spec + coverage** — one shared stdin/stdout contract + per-language function
   signatures + stdin test inputs tagged **normal** / **edge** / **long** /
   **stress**. Capped to ~10 cases (`MO_VERIFY_CASES`, default 10).
3. **Base validator (Java)** — author *all possible solutions* (a reference plus
   several genuinely-different **correct approaches**, plus **wrong** `Solution`s),
   generate the Java validator driver, then **compile and run** it against every
   submission on every test case:
   - every correct approach must be **accepted** (match the canonical),
   - wrong submissions must be **rejected** on inputs they're wrong on.
   Failures feed back into a re-generate loop (up to *max iterations*).
4. **Validator review (Java)** — an agent reviews the Java validator across the
   (≤10) test cases: **edge/corner coverage** and whether it correctly judges the
   **user's returned output** (accepts every valid answer, rejects every invalid
   one; no crashes). By default (`MO_REVIEW_ROUNDS=1`) it reviews and reports; set
   it to `2`+ to also regenerate + re-verify the validator from the fix suggestions.
5. **Translate** — one LLM call ports the verified, reviewed Java validator to the
   other selected languages (Python / JavaScript / C++). These are **not executed**
   — they inherit the base validator's verified logic.

The page shows the Java tab first (driver, validator review, all correct solutions,
the executed PASS/FAIL table) and the translated languages after it (driver only,
marked *translated · not executed*), plus the validator-review verdict. Tunable via
env vars `MO_VERIFY_CASES`, `MO_REVIEW_ROUNDS`, `MO_MAX_ITERATIONS`.

**Cost note.** LLM access goes through the gateway in `MO_BASE_URL`, which may
enforce its own monthly spend cap (separate from your provider credit). If you hit
`429 "Monthly spending budget reached"` while your provider still shows credit, the
gateway's cap is the limit — raise it (proxy admin) or point `MO_BASE_URL` /
`MO_API_KEY` directly at your provider. Keep review rounds at `1` to minimise spend.

## CLI (single Java file)

```bash
python run.py examples/group_anagrams.md
python run.py examples/largest_divisible_subset.md
python run.py examples/two_sum_single.md      # -> rejected (single output)
```

Outputs land in `output/<question-name>/`:

- `Main.java`    — the rewritten judge program (only for accepted questions)
- `report.md`    — classification, strategy, and the verification trace
- `result.json`  — the full structured result
- `testsuite/`   — the generated reference / equivalent / wrong `Solution`s and
  stdin inputs that were compiled and run (so you can re-run them yourself)

Options: `-o/--out <dir>`, `--max-iterations <n>`, `--model <id>`, `--no-execution`.

Exit codes: `0` accepted & verified clean · `3` rejected (single-output) ·
`4` generated but verification still flagged issues (review `report.md`) ·
`1`/`2` errors.

## Question file format

A `.md`/`.txt` file containing the problem description (which should make clear
whether outputs may be returned in any order / any valid answer) and the Java
`Main` inside a fenced ` ```java ` block that defines `public static void main`.
Any other ` ```java ` blocks (e.g. the `Solution` signature) are treated as
context. See `examples/`.

## How "validating the validator" works here

Verification is **execution-based** whenever a JDK is present: the generated
`Main` is actually compiled and run against the generated `Solution`s, and the
accept/reject behaviour is measured, not merely reasoned about. The compiled
cases and their verdicts are recorded in `report.md`, and the generated test
files are saved under `output/<name>/testsuite/`. Without a JDK, the tool falls
back to an adversarial **agentic review** that traces scenarios by reasoning.

## Project layout

```
serve.py                   # website launcher
run.py                     # CLI launcher (single Java file)
mo_validator/
  agent.py                 # Anthropic client + structured-output helper
  prompts.py               # all agent prompts + JSON schemas (CLI + multi-language)
  parser.py                # question-file parsing (CLI)
  javatools.py             # JDK discovery
  runners.py               # per-language compile/run (python, javascript, cpp, java)
  executor.py              # execution-based verification (compile + run + compare)
  pipeline.py              # CLI pipeline: classify -> testsuite -> transform -> verify
  mlpipeline.py            # website pipeline: classify -> spec/coverage -> per-language
                           #   (solutions+validator+execute) -> validator review -> code review
  cli.py                   # CLI argument handling + report writing
webapp/
  server.py                # Flask backend (/api/process, /api/health)
  static/                  # index.html, app.js, style.css
examples/                  # sample questions (two multi-output, one single)
```
