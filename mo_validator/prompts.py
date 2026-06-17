"""System prompts and JSON schemas for the three agents in the pipeline.

Agents:
  1. CLASSIFIER  - decide whether the question is a multiple-outputs question,
                   and if so which kind. Refuses single-answer questions.
  2. TRANSFORMER - rewrite the Java `main` so the question becomes single-answer:
                   either by normalising (sorting) the output, or by embedding a
                   validator function that checks the user's returned output.
  3. VERIFIER    - adversarially review the transformed `main`: enumerate valid
                   answers, alternative valid answers, invalid answers and edge
                   cases, trace them through the new program, and report bugs.
"""

from __future__ import annotations

# --------------------------------------------------------------------------- #
# Shared background that every agent sees.
# --------------------------------------------------------------------------- #

_BACKGROUND = """\
You are part of an automated system that prepares competitive-programming /
interview coding questions for an autograder. The autograder compiles a Java
`Main` class, feeds it stdin, captures stdout, and compares it (after trimming)
against a single stored "expected output" file. That comparison model only works
when a question has exactly ONE correct output for a given input.

A "multiple-outputs question" is one where a correct submission can legitimately
produce more than one output for the same input. There are two distinct kinds:

  (A) RETURN IN ANY ORDER ("any order"):
      The set / multiset of answer elements is uniquely determined, but their
      ORDER is unspecified. Example: Group Anagrams — the groups, and the words
      inside each group, are fixed, but they may be printed in any order. These
      are resolved by NORMALISING the output: deterministically sort every level
      of the structure in `main` before printing, so one stored expected output
      (also normalised) always matches a correct submission.

  (B) RETURN ANY VALID ANSWER ("any valid"):
      Genuinely different outputs are all correct. Example: Largest Divisible
      Subset — many different subsets of the maximum length can be valid, as long
      as every pair is divisible and all elements come from the input. Sorting
      cannot fix this, because the elements themselves differ between valid
      answers. These are resolved by writing a VALIDATOR inside `main`: read what
      the user's `Solution` returned, check it satisfies ALL of the problem's
      constraints (including optimality, e.g. "largest"), and then deterministically
      emit a canonical answer when valid (so it matches the stored expected output)
      or emit something that will NOT match when invalid.

  (C) SINGLE OUTPUT:
      The answer is unique (or the order is fully determined by the problem). The
      autograder already works. This system MUST NOT modify these — it only
      handles kinds (A) and (B).
"""

# --------------------------------------------------------------------------- #
# 1. Classifier
# --------------------------------------------------------------------------- #

CLASSIFIER_SYSTEM = _BACKGROUND + """

YOUR ROLE: classifier.
Read the problem description and the provided Java `main`. Decide:
  - Is this a multiple-outputs question at all?
  - If so, is it kind (A) ANY_ORDER or kind (B) ANY_VALID?

Guidance:
  - Lean on the wording of the description first ("in any order", "return any
    valid", "any one of", "if there are multiple answers ...") but also reason
    about the problem semantics — a problem can be multiple-output even if it is
    only implied.
  - ANY_ORDER vs ANY_VALID hinges on ONE question: across two correct
    submissions, can the actual answer ELEMENTS differ (not just their order)?
      * Only the order differs  -> ANY_ORDER.
      * The elements themselves can differ -> ANY_VALID.
  - If the answer is unique and order is fully determined -> SINGLE. Be honest:
    if it is a normal single-answer question, say SINGLE and the system will stop.
Report your reasoning, then the classification."""

CLASSIFIER_SCHEMA = {
    "type": "object",
    "properties": {
        "reasoning": {
            "type": "string",
            "description": "Step-by-step reasoning: cite description wording and "
            "the element-vs-order test.",
        },
        "category": {
            "type": "string",
            "enum": ["ANY_ORDER", "ANY_VALID", "SINGLE"],
            "description": "ANY_ORDER and ANY_VALID are multiple-output; SINGLE is not.",
        },
        "is_multiple_outputs": {"type": "boolean"},
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
        "short_summary": {
            "type": "string",
            "description": "One sentence the user will see explaining the decision.",
        },
    },
    "required": [
        "reasoning",
        "category",
        "is_multiple_outputs",
        "confidence",
        "short_summary",
    ],
}

# --------------------------------------------------------------------------- #
# 2. Transformer
# --------------------------------------------------------------------------- #

_REFERENCE_EXAMPLE_ANY_ORDER = """\
================ REFERENCE EXAMPLE 1: ANY_ORDER (Group Anagrams) ================
Strategy: normalise the output. Sort the contents of every group, then sort the
list of groups with a deterministic comparator, then print. A single stored
expected output (sorted the same way) then matches any correct submission.

```java
import java.util.*;

public class Main {
    public static void main(String[] args) {
        Scanner sc = new Scanner(System.in);
        int n = sc.nextInt();
        String[] strs = new String[n];
        for (int i = 0; i < n; i++) strs[i] = sc.next();
        Solution sol = new Solution();
        List<List<String>> lists = sol.groupAnagrams(strs);
        for (List<String> list : lists) Collections.sort(list);
        lists.sort((a, b) -> {
            if (a.size() != b.size()) return Integer.compare(a.size(), b.size());
            for (int i = 0; i < a.size(); i++) {
                int cmp = a.get(i).compareTo(b.get(i));
                if (cmp != 0) return cmp;
            }
            return 0;
        });
        for (List<String> group : lists) System.out.println(String.join(" ", group));
    }
}
```
"""

_REFERENCE_EXAMPLE_ANY_VALID = """\
================ REFERENCE EXAMPLE 2: ANY_VALID (Largest Divisible Subset) ======
Strategy: write a validator in `main`. Independently recompute the property that
defines a correct answer (here: the maximum chain length, via DP), then check the
user's returned list: (1) every element is from the input set, (2) every pair is
mutually divisible, (3) the size equals the true maximum length. If valid, print a
CANONICAL answer the system recomputed (so it matches the single stored expected
output); if invalid, print the user's output (which will fail to match).

```java
import java.util.*;

public class Main {
    public static void main(String[] args) {
        Scanner sc = new Scanner(System.in);
        int n = sc.nextInt();
        int[] arr = new int[n];
        for (int i = 0; i < n; i++) arr[i] = sc.nextInt();
        Set<Integer> set = new HashSet<>();
        Solution sol = new Solution();
        List<Integer> result = sol.findLargestChain(arr);
        for (int num : arr) set.add(num);
        // Independently compute the canonical maximum chain.
        List<Integer> list = new ArrayList<>();
        int[] length = new int[arr.length];
        int[] previndex = new int[arr.length];
        int maxLen = 0, index = -1;
        Arrays.fill(length, 1);
        Arrays.fill(previndex, -1);
        Arrays.sort(arr);
        for (int i = 0; i < arr.length; i++) {
            for (int j = i - 1; j >= 0; j--) {
                if (arr[i] % arr[j] == 0 && 1 + length[j] > length[i]) {
                    length[i] = 1 + length[j];
                    previndex[i] = j;
                }
            }
            if (length[i] > maxLen) { maxLen = length[i]; index = i; }
        }
        while (index != -1) { list.add(arr[index]); index = previndex[index]; }
        // Validate the user's returned answer.
        boolean flag = true;
        if (result.size() != list.size()) flag = false;
        for (int i = 0; i < result.size() && flag; i++) {
            for (int j = 0; j < result.size(); j++) {
                if (i != j) {
                    if ((!set.contains(result.get(i)) || !set.contains(result.get(j)))
                            || ((result.get(i) % result.get(j) != 0)
                                && (result.get(j) % result.get(i) != 0))) {
                        flag = false; break;
                    }
                }
            }
        }
        if (flag) for (int x : list) System.out.print(x + " ");
        else for (int x : result) System.out.print(x + " ");
    }
}
```
"""

# Both examples, for the legacy single-language (Java) transformer path.
_REFERENCE_EXAMPLES = _REFERENCE_EXAMPLE_ANY_ORDER + "\n" + _REFERENCE_EXAMPLE_ANY_VALID


def _reference_example(category: str) -> str:
    """Return only the example matching the category.

    The multi-language transformer prompt used to ship BOTH worked examples on
    every call (for all four languages). Only one category ever applies, so
    sending just the relevant one trims a large chunk of input tokens per call.
    """
    if category == "ANY_ORDER":
        return _REFERENCE_EXAMPLE_ANY_ORDER
    if category == "ANY_VALID":
        return _REFERENCE_EXAMPLE_ANY_VALID
    return _REFERENCE_EXAMPLES


TRANSFORMER_SYSTEM = (
    _BACKGROUND
    + "\n\nYOUR ROLE: transformer. You rewrite the Java `Main` so the question "
    "becomes single-answer for the autograder.\n\n"
    + _REFERENCE_EXAMPLES
    + """

RULES FOR YOUR REWRITE:
  - Preserve the exact stdin reading and the exact call into the user's
    `Solution` — never change the Solution's signature or the input format.
  - Do NOT include or assume any particular `Solution` implementation; the user
    supplies it. Your code lives only in `Main`.
  - ANY_ORDER -> normalise output deterministically (sort every level) before
    printing. Keep the printed token/line format identical to the original.
  - ANY_VALID -> embed a validator. Independently recompute whatever defines a
    correct answer (size/optimality + all structural constraints), validate the
    user's returned value against EVERY constraint the problem imposes, then:
        * on valid   -> print a CANONICAL answer your code computed, in the
          original output format, so it matches the single stored expected output;
        * on invalid -> print the user's raw answer (so it mismatches and fails).
    Think carefully about EVERY constraint and EVERY edge case (empty result,
    single element, duplicates, the optimal value being 0/empty, ties, elements
    not in the input, wrong size, etc.).
  - Keep the trailing-output convention (spaces / newlines) consistent with the
    original `main` so the stored expected output still matches.
  - Output must be a COMPLETE, COMPILABLE `Main.java` (with imports).

If you are given reviewer feedback from a previous attempt, fix exactly those
issues and return the corrected full file."""
)

TRANSFORMER_SCHEMA = {
    "type": "object",
    "properties": {
        "reasoning": {
            "type": "string",
            "description": "Enumerate every correctness constraint of the problem "
            "and how your rewrite enforces/normalises it.",
        },
        "strategy": {
            "type": "string",
            "enum": ["sort_normalize", "validator_function"],
            "description": "sort_normalize for ANY_ORDER, validator_function for ANY_VALID.",
        },
        "transformed_main": {
            "type": "string",
            "description": "The complete, compilable Main.java source.",
        },
        "validation_notes": {
            "type": "string",
            "description": "Human-readable summary of what is checked / normalised "
            "and the edge cases handled.",
        },
    },
    "required": ["reasoning", "strategy", "transformed_main", "validation_notes"],
}

# --------------------------------------------------------------------------- #
# 3. Verifier
# --------------------------------------------------------------------------- #

VERIFIER_SYSTEM = (
    _BACKGROUND
    + """

YOUR ROLE: adversarial verifier. You are given the problem and a candidate
rewritten `Main`. Your job is to TRY TO BREAK IT by reasoning, since the code is
not executed.

Do the following:
  1. Construct a set of test scenarios. You MUST include:
       - at least one fully correct answer,
       - for ANY_VALID: at least one ALTERNATIVE correct answer that differs in
         its elements (the validator must accept it too),
       - several incorrect answers (wrong size, missing constraint, element not
         from input, sub-optimal, etc.) that the program MUST reject,
       - edge cases (empty/one element/duplicates/all-equal/ties/optimal-empty).
  2. For each scenario, mentally trace the candidate `Main`: given that
     `Solution` returns the candidate output, what would `Main` print, and would
     it match the stored canonical expected output?
  3. Decide whether the rewrite is correct: it must ACCEPT every valid answer
     (print the canonical expected output) and REJECT every invalid answer (print
     something that won't match), with deterministic output, no crashes, and the
     original I/O format preserved.

Be skeptical. If a validator can be fooled by some valid-but-unusual answer, or
wrongly accepts an invalid one, or can crash (e.g. division by zero, empty list
indexing, NPE), that is a bug. Report concrete bugs and concrete fixes."""
)

VERIFIER_SCHEMA = {
    "type": "object",
    "properties": {
        "reasoning": {"type": "string"},
        "test_scenarios": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "stdin": {"type": "string"},
                    "candidate_solution_output": {"type": "string"},
                    "should_be_accepted": {"type": "boolean"},
                    "traced_program_behavior": {"type": "string"},
                    "passes": {
                        "type": "boolean",
                        "description": "True if the traced behavior matches the "
                        "expected accept/reject.",
                    },
                },
                "required": [
                    "name",
                    "stdin",
                    "candidate_solution_output",
                    "should_be_accepted",
                    "traced_program_behavior",
                    "passes",
                ],
            },
        },
        "is_correct": {"type": "boolean"},
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
        "bugs": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Concrete defects found; empty if none.",
        },
        "fix_suggestions": {
            "type": "string",
            "description": "Actionable guidance for the transformer to fix the bugs.",
        },
    },
    "required": [
        "reasoning",
        "test_scenarios",
        "is_correct",
        "confidence",
        "bugs",
        "fix_suggestions",
    ],
}

# --------------------------------------------------------------------------- #
# 4. Test-suite generator (for execution-based verification)
# --------------------------------------------------------------------------- #

TESTSUITE_SYSTEM = (
    _BACKGROUND
    + """

YOUR ROLE: test-suite author. The transformed `Main` will be COMPILED and RUN
(real JDK) against `Solution` implementations you write here, to prove the
validator/normalisation behaves. You produce Java `Solution` files and stdin
cases. You do NOT see the transformed `Main`; design the suite from the problem
alone so it stays valid across rewrites.

How the harness uses what you produce, for each test input:
  - It runs `Main` + your REFERENCE solution -> that output is the CANONICAL
    expected output for that input.
  - Each EQUIVALENT solution (a correct submission that may produce a different
    valid answer, or the same elements in a different order) MUST, after going
    through `Main`, produce output EQUAL to the canonical. (Validator accepts ->
    prints canonical; or normalisation sorts both to the same thing.)
  - Each WRONG solution MUST, after going through `Main`, produce output NOT
    EQUAL to the canonical (validator rejects -> prints the wrong raw answer).

Requirements:
  - Every solution is the COMPLETE contents of `Solution.java`: any needed
    imports at the very top, then a NON-public `class Solution` with EXACTLY the
    method signature the problem/`Main` expects. NO `Main` class. NO `public`
    class. Must compile under Java 17+.
  - Solutions must be deterministic and must not read stdin themselves (`Main`
    reads stdin and calls into `Solution`).
  - Provide at least 4 `test_inputs` in the EXACT stdin format `Main` reads,
    covering normal and edge cases (empty / single element / duplicates / ties /
    all-equal / the optimal answer being empty, as applicable).
  - Provide >= 1 (preferably 2) EQUIVALENT solutions. For ANY_VALID, make at
    least one genuinely choose a DIFFERENT valid answer than the obvious one
    (e.g. a different maximal subset) so the validator is truly exercised. For
    ANY_ORDER, return the same elements in a different/!reversed order.
  - Provide >= 2 WRONG solutions with DISTINCT failure modes (wrong size, a
    violated pairwise constraint, an element not from the input, sub-optimal
    answer, empty/garbage, etc.).
  - The REFERENCE solution must be correct and (for "largest/optimal" problems)
    optimal."""
)

_SOLUTION_ITEM = {
    "type": "object",
    "properties": {
        "name": {"type": "string"},
        "code": {"type": "string", "description": "Full Solution.java contents."},
        "note": {"type": "string"},
    },
    "required": ["name", "code", "note"],
}

TESTSUITE_SCHEMA = {
    "type": "object",
    "properties": {
        "reasoning": {"type": "string"},
        "solution_signature": {
            "type": "string",
            "description": "The exact Solution method signature the harness expects.",
        },
        "reference_solution": {
            "type": "object",
            "properties": {
                "code": {"type": "string"},
                "note": {"type": "string"},
            },
            "required": ["code", "note"],
        },
        "equivalent_solutions": {"type": "array", "items": _SOLUTION_ITEM},
        "wrong_solutions": {"type": "array", "items": _SOLUTION_ITEM},
        "test_inputs": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "stdin": {"type": "string"},
                },
                "required": ["name", "stdin"],
            },
        },
    },
    "required": [
        "reasoning",
        "solution_signature",
        "reference_solution",
        "equivalent_solutions",
        "wrong_solutions",
        "test_inputs",
    ],
}

# =========================================================================== #
#  MULTI-LANGUAGE FLOW (used by the website): description -> per-language
#  validator-embedded driver + execution-verified test suite, for
#  Python / JavaScript / C++ / Java.
# =========================================================================== #

# How the user's solution and the generated driver are combined at run time,
# per language. The transformer must emit a driver that fits this contract, and
# the test-suite must emit solutions that fit it too.
LANG_CONTRACT = {
    "python": (
        "PYTHON (CPython 3). The user's solution source is concatenated ABOVE "
        "your driver in one file, so any function/class it defines is already in "
        "scope. The driver reads stdin (e.g. `import sys; data = sys.stdin."
        "read().split()`), calls the solution's top-level function, validates, "
        "and prints to stdout. Do NOT redefine the solution function in the driver."
    ),
    "javascript": (
        "JAVASCRIPT (Node.js 18). The user's solution source is concatenated "
        "ABOVE your driver in one file, so its functions are in scope. The driver "
        "reads stdin with `require('fs').readFileSync(0,'utf8')`, calls the "
        "solution's function, validates, and prints with console.log."
    ),
    "cpp": (
        "C++ (g++ -std=c++17). The user's solution source is concatenated ABOVE "
        "your driver in one file. The driver provides `int main()`, reads stdin "
        "with cin, calls the solution's function/class, validates, prints with "
        "cout. You may `#include <bits/stdc++.h>` and `using namespace std;` "
        "(duplicate includes across the two parts are harmless)."
    ),
    "java": (
        "JAVA (17+). The driver is the file `Main.java` declaring `public class "
        "Main` with `public static void main(String[] args)`. The user's solution "
        "is a SEPARATE file declaring a NON-public `class Solution`; both compile "
        "together. The driver does `new Solution().<method>(...)`."
    ),
}

# --------------------------------------------------------------------------- #
#  ML classifier (description only, no Java main supplied)
# --------------------------------------------------------------------------- #

ML_CLASSIFIER_SYSTEM = _BACKGROUND + """

YOUR ROLE: classifier. You are given only the PROBLEM DESCRIPTION (no driver
code). Decide whether it is a multiple-outputs question and, if so, whether it is
(A) ANY_ORDER or (B) ANY_VALID, using the element-vs-order test: across two
correct submissions, can the answer ELEMENTS differ (ANY_VALID) or only their
ORDER (ANY_ORDER)? If the answer is unique -> SINGLE (the system will stop)."""

# --------------------------------------------------------------------------- #
#  Spec: shared I/O contract + per-language signatures + shared test inputs
# --------------------------------------------------------------------------- #

SPEC_SYSTEM = _BACKGROUND + """

YOUR ROLE: problem-spec author AND test-coverage critic in one step. From the
description and its category, define a single concrete contract that ALL languages
and ALL test cases will share, plus an EXHAUSTIVE set of stdin test inputs.

Produce:
  - a stdin_format and output_format precise enough to be implemented identically
    in Python, JavaScript, C++ and Java,
  - a canonical function name and, for EACH language, the exact signature the
    user's solution must have (idiomatic: a `class Solution` method for Java; a
    top-level function for Python/JavaScript; a free function or `class Solution`
    for C++),
  - test_inputs in that EXACT stdin format. Because there is no separate coverage
    pass, YOU must make these complete on your own. Tag each input with a `kind`
    and cover ALL FOUR kinds:
      * "normal" — typical, illustrative cases (>= 3).
      * "edge"   — corner cases the validator must handle: empty input, single
        element, duplicates, all-equal, ties between equally-good answers, the
        optimal answer being empty/zero, boundary sizes, and (for ANY_VALID)
        inputs that admit MULTIPLE genuinely-different valid answers so the
        validator is truly exercised (>= 4).
      * "long"   — large but still feasible inputs that exercise the structure at
        scale (e.g. hundreds of elements). Keep them bounded so an efficient
        (roughly O(n^2)) correct solution finishes in a couple of seconds; embed
        the data literally in the stdin string (>= 1).
      * "stress" — the largest inputs you include, to surface performance and
        overflow issues (e.g. values near the stated limits, near-worst-case
        structure). Still bounded to remain feasible for an efficient correct
        solution within a few seconds (>= 1).
    Provide EXACTLY 10 inputs total — covering all four kinds (a few normal,
    several edge, at least one long, at least one stress). Inputs must be
    language-independent stdin strings.

Before finishing, self-review your inputs for gaps and add any missing case.
Report that review in `coverage_assessment` and set `coverage_complete` true only
if you are confident the set exercises every distinct behaviour AND includes all
four kinds (normal/edge/long/stress)."""

_SIG_OBJ = {
    "type": "object",
    "properties": {
        "python": {"type": "string"},
        "javascript": {"type": "string"},
        "cpp": {"type": "string"},
        "java": {"type": "string"},
    },
    "required": ["python", "javascript", "cpp", "java"],
}

SPEC_SCHEMA = {
    "type": "object",
    "properties": {
        "reasoning": {"type": "string"},
        "title": {"type": "string"},
        "stdin_format": {"type": "string"},
        "output_format": {"type": "string"},
        "function_name": {"type": "string"},
        "signatures": _SIG_OBJ,
        "test_inputs": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "stdin": {"type": "string"},
                    "note": {"type": "string"},
                    "kind": {
                        "type": "string",
                        "enum": ["normal", "edge", "long", "stress"],
                        "description": "Category of this test input. Include all four "
                        "kinds across the set.",
                    },
                },
                "required": ["name", "stdin", "note", "kind"],
            },
        },
        "coverage_assessment": {
            "type": "string",
            "description": "Self-review of whether the inputs cover every distinct "
            "behaviour / corner case, and what was added to close gaps.",
        },
        "coverage_complete": {
            "type": "boolean",
            "description": "True only if the inputs exercise every distinct behaviour.",
        },
    },
    "required": [
        "reasoning", "title", "stdin_format", "output_format",
        "function_name", "signatures", "test_inputs",
        "coverage_assessment", "coverage_complete",
    ],
}

# --------------------------------------------------------------------------- #
#  Coverage critic: are the test inputs exhaustive (corner/edge cases)?
# --------------------------------------------------------------------------- #

COVERAGE_SYSTEM = _BACKGROUND + """

YOUR ROLE: coverage critic. You are given the contract and the current stdin test
inputs. Decide whether they cover ALL distinct behaviours a validator must handle
for this problem — especially corner and edge cases (empty, single, duplicates,
all-equal, ties between equally-good answers, the optimal answer being empty/zero,
boundary sizes, inputs that admit MULTIPLE genuinely-different valid answers so
the ANY_VALID validator is exercised). List any MISSING inputs (in the exact stdin
format) with a reason. If coverage is already complete, return an empty list."""

COVERAGE_SCHEMA = {
    "type": "object",
    "properties": {
        "reasoning": {"type": "string"},
        "assessment": {"type": "string"},
        "coverage_complete": {"type": "boolean"},
        "missing_inputs": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "stdin": {"type": "string"},
                    "why": {"type": "string"},
                },
                "required": ["name", "stdin", "why"],
            },
        },
    },
    "required": ["reasoning", "assessment", "coverage_complete", "missing_inputs"],
}

# --------------------------------------------------------------------------- #
#  Per-language transformer (emits the validator-embedded driver)
# --------------------------------------------------------------------------- #

def ml_transformer_system(language: str, category: str = "") -> str:
    return (
        _BACKGROUND
        + "\n\nYOUR ROLE: transformer. Emit a COMPLETE driver program in "
        + f"**{language}** that turns this multiple-outputs question into a "
        "single-answer one for the autograder.\n\n"
        + _reference_example(category)
        + "\n\nLANGUAGE & CALLING CONTRACT:\n" + LANG_CONTRACT[language]
        + """

RULES:
  - Follow the shared stdin_format / output_format from the spec EXACTLY.
  - Call the user's solution via the given signature; never redefine it.
  - ANY_ORDER  -> deterministically normalise (sort every level) before printing.
  - ANY_VALID  -> embed a validator: independently recompute what defines a
    correct answer (size/optimality + ALL structural constraints), validate the
    user's returned value against EVERY constraint and EVERY edge case, then print
    a CANONICAL answer when valid (so it matches the single stored expected
    output) or the user's raw answer when invalid (so it mismatches). Never crash
    on a malformed/empty user answer — treat it as invalid.
    CRITICAL ANY_VALID PITFALL — do NOT reject a valid answer just because it
    differs from the specific canonical answer you reconstructed. There are
    MULTIPLE correct answers by definition; an answer is valid iff it satisfies
    every structural constraint AND is optimal (e.g. its size/cost equals the
    optimum you computed) — NOT iff its elements equal your canonical answer's
    elements. Validate by PROPERTIES (membership, pairwise/structural rules,
    optimal size/cost), never by element-equality against your canonical. When the
    user's answer is valid, print YOUR canonical answer (identical for all valid
    answers, so the single stored expected output matches). Two different optimal
    answers must BOTH be accepted.
  - Output deterministic, in the exact output_format. Return the COMPLETE program.
If given reviewer feedback, fix exactly those issues and return the full file."""
    )


def ml_transformer_schema() -> dict:
    return {
        "type": "object",
        "properties": {
            "reasoning": {"type": "string"},
            "strategy": {
                "type": "string",
                "enum": ["sort_normalize", "validator_function"],
            },
            "driver_code": {"type": "string", "description": "The complete driver program."},
            "validation_notes": {"type": "string"},
        },
        "required": ["reasoning", "strategy", "driver_code", "validation_notes"],
    }


# --------------------------------------------------------------------------- #
#  Per-language test-suite author (solutions only; inputs come from the spec)
# --------------------------------------------------------------------------- #

# A minimal "this is the ONLY shape allowed" example of a bare solution per
# language: just the function/class, no stdin, no main, no top-level execution.
_BARE_SOLUTION_TEMPLATE = {
    "python": (
        "    ```python\n"
        "    from typing import List\n"
        "    def solve(...) -> ...:          # the required signature\n"
        "        # compute and RETURN the answer; do not print, do not read stdin\n"
        "        return answer\n"
        "    ```"
    ),
    "javascript": (
        "    ```javascript\n"
        "    function solve(...) {            // the required signature\n"
        "      // compute and RETURN the answer; do not console.log, no stdin\n"
        "      return answer;\n"
        "    }\n"
        "    ```"
    ),
    "cpp": (
        "    ```cpp\n"
        "    #include <bits/stdc++.h>\n"
        "    using namespace std;\n"
        "    // a free function (or `class Solution`) with the required signature.\n"
        "    // NO main(), NO cin/cout — just compute and return.\n"
        "    vector<...> solve(...) { return answer; }\n"
        "    ```"
    ),
    "java": (
        "    ```java\n"
        "    import java.util.*;\n"
        "    class Solution {                 // non-public, NOT Main\n"
        "        public ... solve(...) {      // the required signature\n"
        "            // compute and RETURN; no Scanner, no main\n"
        "            return answer;\n"
        "        }\n"
        "    }\n"
        "    ```"
    ),
}


def ml_testsuite_system(language: str) -> str:
    return (
        _BACKGROUND
        + f"\n\nYOUR ROLE: test-suite author for **{language}**. The driver will "
        "be compiled and run against the `Solution` submissions you write, on the "
        "SHARED stdin inputs you are given (do not invent new inputs).\n\n"
        "How the harness uses your submissions, per shared input:\n"
        "  - REFERENCE submission -> defines the CANONICAL expected output.\n"
        "  - each EQUIVALENT submission (correct; may pick a different valid answer "
        "or different order) MUST produce the canonical output after the driver.\n"
        "  - each WRONG submission MUST be rejected (differ from canonical) on at "
        "least one input where it is genuinely wrong.\n\n"
        "LANGUAGE & CALLING CONTRACT:\n" + LANG_CONTRACT[language]
        + """

REQUIREMENTS:
  - Each submission defines ONLY the required function/class — nothing else.
    It is a BARE submission, NOT a runnable program:
      * It MUST NOT read stdin (no input()/sys.stdin, no readFileSync/process.stdin,
        no cin/getline/scanf, no Scanner).
      * It MUST NOT define a program entry point (no `if __name__ == ...`,
        no `int main()`, no `public static void main`, no top-level execution
        or printing). The driver — concatenated with your code — supplies main,
        reads stdin, calls your function, and prints. A main()/stdin read in your
        submission collides with the driver and fails to compile/run.
      * It MUST match the given signature exactly and compile cleanly.
      * Java only: a NON-public `class Solution` (never `public class`, never Main).
  - Shape your submissions like this (function body varies):
""" + _BARE_SOLUTION_TEMPLATE[language] + """
  - Provide >= 2 (ideally 3) EQUIVALENT submissions, each a genuinely DIFFERENT
    CORRECT APPROACH to the problem — these are the "all possible solutions" the
    system surfaces, so make them algorithmically distinct (e.g. a different
    strategy / data structure / order of construction), not cosmetic variants of
    each other. Each MUST be genuinely CORRECT: on every shared input it must
    yield the SAME set/multiset of answer elements as the reference (the driver
    will accept it -> canonical output). For ANY_VALID make at least one genuinely
    choose a DIFFERENT valid answer; for ANY_ORDER use a different order. Never
    submit a subtly-broken solution here.
    IMPORTANT — efficiency: every correct submission is run against the LONG and
    STRESS inputs too, under a time limit. Each approach you submit must finish on
    those inputs within a few seconds. If an approach (e.g. an exponential
    brute force) cannot, do NOT submit it as an EQUIVALENT — it would falsely fail
    verification. Submit only approaches efficient enough for the given inputs.
  - Provide >= 2 WRONG submissions with DISTINCT failure modes (wrong size, a
    violated constraint, an element not from the input, sub-optimal, empty/garbage).
  - REQUIRED for any OPTIMISATION problem (largest / smallest / maximum / minimum /
    longest / best — i.e. the answer must be optimal): include AT LEAST ONE wrong
    submission that returns a STRUCTURALLY VALID but SUB-OPTIMAL answer (every
    constraint satisfied, but smaller/worse than the optimum — e.g. returns a valid
    subset that is not the largest). This is the only submission that forces the
    validator to check OPTIMALITY rather than just structural validity, so a
    validator that forgets the size/optimality check is caught by execution. Make
    sure it is sub-optimal on at least one of the SPECIFIC shared inputs given.

CRITICAL — every WRONG submission MUST actually be caught on the inputs you have:
  - A wrong submission is only useful if, on AT LEAST ONE of the SPECIFIC shared
    inputs you were given, it produces a DIFFERENT answer than the reference.
    The driver normalises ORDER away, so a "wrong" answer that contains the SAME
    elements/groups as the correct one (just reordered, re-grouped equivalently,
    or otherwise normalising to the same thing) is NOT wrong — it will be accepted
    and the suite will be rejected as too lenient. Difference must be in the actual
    CONTENT (elements / grouping / size / membership), not the order.
  - Before finalising, mentally EVALUATE each wrong submission on each shared input
    and confirm it diverges from the correct answer on at least one of them. If a
    wrong idea is never triggered by any of the given inputs (e.g. "fails only when
    two differently-spelled anagrams exist" but no such input is present), DISCARD
    that idea and choose a different wrong submission whose failure IS exercised by
    one of the inputs you were given. Do not invent new inputs to trigger it.

OUTPUT: put the reference in `reference_code`, the correct alternatives in the
`equivalent_codes` array, and the wrong ones in the `wrong_codes` array. Each
entry is the COMPLETE source string for one bare solution."""
    )


def ml_testsuite_schema() -> dict:
    # Flat shape (code strings, not nested {name,code,note} objects): smaller
    # models reliably emit arrays of strings via tool calls, but mangle deeply
    # nested objects that each carry a large code blob. The pipeline adapts this
    # back into the internal reference/equivalent/wrong structure.
    return {
        "type": "object",
        "properties": {
            "reasoning": {"type": "string"},
            "reference_code": {
                "type": "string",
                "description": "The COMPLETE bare reference solution (defines only "
                "the required function/class).",
            },
            "equivalent_codes": {
                "type": "array",
                "items": {"type": "string"},
                "description": ">= 2 (ideally 3) alternative CORRECT solutions, each "
                "a genuinely DIFFERENT efficient approach and a complete bare "
                "solution.",
            },
            "wrong_codes": {
                "type": "array",
                "items": {"type": "string"},
                "description": ">= 2 WRONG solutions with distinct failure modes, "
                "each a complete bare solution.",
            },
        },
        "required": ["reasoning", "reference_code", "equivalent_codes", "wrong_codes"],
    }


# --------------------------------------------------------------------------- #
#  Cross-language validator review (after every language's driver is built &
#  execution-verified): one agent reviews ALL FOUR validators together, against
#  the solutions and contract, and checks they are mutually consistent.
# --------------------------------------------------------------------------- #

VALIDATOR_REVIEW_SYSTEM = _BACKGROUND + """

YOUR ROLE: validator reviewer. Several languages have each produced a
validator-embedded driver (the "validator function") for the SAME problem. Review
ONLY the validator function (not the user's solution logic). For every language
you are given: the validator function, its strategy, a summary of how it behaved
when compiled and run, AND THE TEST CASES it must handle — the shared stdin inputs
(tagged normal / edge / long / stress) and the generated reference /
alternative-correct / wrong solutions.

Review each validator function TOGETHER WITH those test cases, in all four
languages: walk the validator through the shared inputs and confirm it ACCEPTS
every reference and alternative-correct solution (prints the canonical output) and
REJECTS every wrong solution on at least one input it is genuinely wrong on. If a
test case would expose a flaw the execution summary did not, call it out.

Review the validators ADVERSARIALLY and HOLISTICALLY:
  1. Per language — is the validator logically correct? It must, for the problem's
     category:
       * ANY_ORDER  -> normalise every level deterministically so any correctly
         ordered answer collapses to the canonical output.
       * ANY_VALID  -> independently recompute what makes an answer correct
         (size/optimality + EVERY structural constraint), ACCEPT every genuinely
         valid answer (including the alternative-correct ones), and REJECT every
         answer that violates ANY constraint. It must never crash on a malformed
         or empty user answer (treat it as invalid), and must be deterministic.
     Flag validators that are too lenient (accept a wrong answer), too strict
     (reject a valid alternative), order-sensitive when they should not be, or
     liable to crash / overflow / time out on the long & stress inputs.
  2. Cross-language — the four validators implement the SAME contract. Flag any
     INCONSISTENCY: different accept/reject decisions for the same answer,
     different canonical output, or one language enforcing a constraint the
     others miss. They must agree.

Use the execution summaries as evidence but reason beyond them (the inputs may
not trigger every flaw). For each language report whether it is OK and, if not,
concrete issues and concrete fix suggestions the transformer can act on. Set
`overall_ok` true only if every language is OK and they are mutually consistent."""

_REVIEW_LANG_ITEM = {
    "type": "object",
    "properties": {
        "language": {
            "type": "string",
            "enum": ["python", "javascript", "cpp", "java"],
        },
        "ok": {"type": "boolean"},
        "issues": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Concrete defects in this language's validator; empty if none.",
        },
        "fix_suggestions": {
            "type": "string",
            "description": "Actionable guidance for the transformer to fix this "
            "language's validator (empty if ok).",
        },
    },
    "required": ["language", "ok", "issues", "fix_suggestions"],
}

VALIDATOR_REVIEW_SCHEMA = {
    "type": "object",
    "properties": {
        "reasoning": {"type": "string"},
        "overall_ok": {"type": "boolean"},
        "summary": {
            "type": "string",
            "description": "One-paragraph verdict the user will see.",
        },
        "cross_language_consistency": {
            "type": "string",
            "description": "Whether the validators agree across languages, and any "
            "inconsistency found.",
        },
        "per_language": {"type": "array", "items": _REVIEW_LANG_ITEM},
    },
    "required": [
        "reasoning", "overall_ok", "summary",
        "cross_language_consistency", "per_language",
    ],
}


# --------------------------------------------------------------------------- #
#  Per-language complete-code review (the final gate): one agent reviews the
#  COMPLETE runnable program — the reference solution PLUS the validator embedded
#  in the driver/main — as a single artifact.
# --------------------------------------------------------------------------- #

def complete_code_review_system(language: str, category: str = "") -> str:
    return (
        _BACKGROUND
        + f"\n\nYOUR ROLE: complete-code reviewer for **{language}**. You are given "
        "the COMPLETE runnable program for this problem: the reference solution "
        "together with the validator embedded in the driver / main class — exactly "
        "what is compiled and run.\n\n"
        "LANGUAGE & CALLING CONTRACT:\n" + LANG_CONTRACT[language]
        + """

Review the WHOLE program end to end, adversarially:
  - Correctness: the program reads stdin in the contract format, calls the
    solution, validates/normalises per the category, and prints the canonical
    output in the exact output format. Trace valid, alternative-valid, invalid,
    and edge inputs through it.
  - Robustness: no crashes / NPE / index errors / division-by-zero / integer
    overflow on empty, single, duplicate, all-equal, tie, or boundary inputs;
    no time-outs on the long & stress inputs; output is deterministic.
  - Contract hygiene: the solution stays a bare function/class (no stray stdin
    read or second entry point), the driver owns the single entry point, and the
    two compose into one clean compilable program.
Report whether the complete program is correct, list concrete issues, and give
actionable fix suggestions the transformer can apply to the driver."""
    )


COMPLETE_CODE_REVIEW_SCHEMA = {
    "type": "object",
    "properties": {
        "reasoning": {"type": "string"},
        "is_correct": {"type": "boolean"},
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
        "issues": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Concrete defects in the complete program; empty if none.",
        },
        "fix_suggestions": {
            "type": "string",
            "description": "Actionable guidance for the transformer to fix the "
            "driver (empty if correct).",
        },
    },
    "required": ["reasoning", "is_correct", "confidence", "issues", "fix_suggestions"],
}


# --------------------------------------------------------------------------- #
#  Single-language validator review (optimised flow): review ONE language's
#  validator function across the test cases — edge/corner coverage and whether it
#  correctly judges the user's returned output. Reuses COMPLETE_CODE_REVIEW_SCHEMA.
# --------------------------------------------------------------------------- #

def validator_function_review_system(language: str, category: str = "") -> str:
    return (
        _BACKGROUND
        + f"\n\nYOUR ROLE: validator reviewer for **{language}**. You are given the "
        "validator function (the validator-embedded driver), the shared test cases "
        "(stdin inputs tagged normal / edge / long / stress), the generated "
        "reference / correct / wrong solutions, and a summary of how the validator "
        "behaved when compiled and run against them.\n\n"
        "Review the validator function, reasoning across the provided test cases:\n"
        "  - COVERAGE: it handles ALL cases, including EDGE and CORNER cases (empty,\n"
        "    single element, duplicates, all-equal, ties between equally-good\n"
        "    answers, the optimal answer being empty/zero, boundary sizes).\n"
        "  - USER-OUTPUT CORRECTNESS: it correctly judges whether the USER'S RETURNED\n"
        "    OUTPUT is correct — ACCEPTs every genuinely valid answer (prints the\n"
        "    canonical output) and REJECTs every invalid one (wrong size, violated\n"
        "    constraint, element not from input, sub-optimal, empty/garbage). It is\n"
        "    neither too lenient nor too strict.\n"
        "  - ROBUSTNESS: never crashes on a malformed/empty answer (treat it as\n"
        "    invalid); output is deterministic.\n\n"
        "LANGUAGE & CALLING CONTRACT:\n" + LANG_CONTRACT[language]
        + "\n\nReport whether the validator is correct, list concrete issues, and "
        "give actionable fix suggestions the transformer can apply."
    )


# --------------------------------------------------------------------------- #
#  Translator (optimised flow): port the verified+reviewed Java validator to the
#  other three languages in ONE call. No execution is run on the translations.
# --------------------------------------------------------------------------- #

TRANSLATE_SYSTEM = _BACKGROUND + """

YOUR ROLE: translator. You are given a VERIFIED and REVIEWED validator-embedded
driver written in a SOURCE language for this problem, the shared I/O contract, and
the per-language solution signatures. Port the SAME validator to each requested
TARGET language.

RULES:
  - Preserve the validator LOGIC EXACTLY: the same independent recomputation, the
    same constraint checks, the same accept/reject decision, and the same canonical
    output. Only the language/syntax changes — never the behaviour.
  - Fill ONLY the driver fields for the TARGET languages you are asked for; leave
    the others empty.
  - Follow each target language's calling contract (for Python/JS/C++ the user's
    solution is concatenated ABOVE your driver in the same file; for Java the driver
    is `Main.java` and the solution is a separate `class Solution`). Your driver
    reads stdin, calls the solution via its signature, validates, and prints in the
    exact output format:

PYTHON:
""" + LANG_CONTRACT["python"] + """

JAVASCRIPT:
""" + LANG_CONTRACT["javascript"] + """

C++:
""" + LANG_CONTRACT["cpp"] + """

JAVA:
""" + LANG_CONTRACT["java"] + """

  - Match the stdin_format / output_format byte-for-byte across all languages, and
    never crash on a malformed/empty user answer (treat it as invalid).
  - Each driver you fill must be a COMPLETE program that runs once combined with
    the user's solution."""

TRANSLATE_SCHEMA = {
    "type": "object",
    "properties": {
        "reasoning": {"type": "string"},
        "python_driver": {"type": "string", "description": "Complete Python driver (if requested)."},
        "javascript_driver": {"type": "string", "description": "Complete Node.js driver (if requested)."},
        "cpp_driver": {"type": "string", "description": "Complete C++ driver (if requested)."},
        "java_driver": {"type": "string", "description": "Complete Java Main.java driver (if requested)."},
    },
    "required": ["reasoning"],
}
