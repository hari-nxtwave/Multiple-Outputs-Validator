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
      The set / multiset / collection of answer elements is uniquely determined,
      but their ORDER is unspecified. Example: Group Anagrams — the groups, and the
      words inside each group, are fixed, but they may be printed in any order.
      These are resolved by NORMALISING the output: deterministically sort the
      order-free levels of the structure in `main` before printing, so one stored
      expected output (also normalised) always matches a correct submission.

      IMPORTANT — "return ALL ..." is almost always kind (A), NOT kind (B). When a
      problem says return ALL solutions / ALL shortest paths / ALL valid sequences
      / EVERY answer, the COMPLETE collection is uniquely determined: any correct
      submission returns the SAME collection, just possibly in a different order.
      So it is resolved by SORTING the collection, not by a validator. Example:
      Word Ladder II (return all shortest transformation sequences) — the set of
      shortest sequences is fixed; only the order in which they are listed varies,
      so you sort that outer list. Each individual sequence is itself an ORDERED
      path and its internal order MUST be preserved — only sort the dimensions the
      problem leaves unordered.

  (B) RETURN ANY VALID ANSWER ("any valid"):
      The problem asks for ANY ONE (or just SOME) of several answers whose CONTENT
      genuinely differs between correct submissions. Example: Largest Divisible
      Subset — return ANY ONE largest subset; different correct submissions return
      different element sets. Sorting cannot fix this, because the elements
      themselves differ between valid answers. These are resolved by writing a
      VALIDATOR inside `main`: read what the user's `Solution` returned, check it
      satisfies ALL of the problem's constraints (including optimality, e.g.
      "largest"), and then deterministically emit a canonical answer when valid (so
      it matches the stored expected output) or emit something that will NOT match
      when invalid.

  (C) SINGLE OUTPUT:
      The answer is unique (or the order is fully determined by the problem). The
      autograder already works. This system MUST NOT modify these — it only
      handles kinds (A) and (B).

DECISION RULE — "return ALL" vs "return ANY ONE":
  - "return ALL / EVERY ..." (the complete set of answers) -> the collection is
    fixed -> kind (A), ANY_ORDER, resolve by SORTING. PREFER THIS.
  - "return ANY ONE / ANY VALID / SOME ..." (pick one of many) -> content differs
    -> kind (B), ANY_VALID, resolve by a VALIDATOR.
  Only choose (B) when sorting genuinely cannot canonicalise the output because the
  answer CONTENT itself differs between correct submissions. If sorting works,
  ALWAYS prefer (A) — it needs no recomputation, so it is simpler, faster, and
  cannot recurse or stack-overflow.
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

--- "RETURN ALL ..." variant: a list of ORDERED sequences (e.g. Word Ladder II) ---
When the answer is a list of sequences (return ALL shortest transformation
sequences / all paths), the set of sequences is fixed and only the OUTER list
order varies. Sort ONLY the outer list; each sequence is an ordered path, so keep
its internal order. Do NOT write a validator and do NOT recompute the paths — just
sort what the user returned (no recursion, no stack overflow):

```java
import java.util.*;

public class Main {
    public static void main(String[] args) {
        Scanner sc = new Scanner(System.in);
        String startWord = sc.next(), targetWord = sc.next();
        int n = sc.nextInt();
        List<String> list = new ArrayList<>();
        for (int i = 0; i < n; i++) list.add(sc.next());
        Solution sol = new Solution();
        List<List<String>> res = sol.findShortestTransformationPaths(startWord, targetWord, list);
        if (res.size() == 0) { System.out.println("[]"); return; }
        res.sort((a, b) -> {                 // sort the OUTER list only
            for (int i = 0; i < Math.min(a.size(), b.size()); i++) {
                int cmp = a.get(i).compareTo(b.get(i));
                if (cmp != 0) return cmp;
            }
            return Integer.compare(a.size(), b.size());
        });
        for (List<String> ans : res) {        // each path keeps its own order
            for (String s : ans) System.out.print(s + " ");
            System.out.println();
        }
    }
}
```
"""

_REFERENCE_EXAMPLE_ANY_VALID = """\
================ REFERENCE EXAMPLE 2: ANY_VALID ================================
Structure EVERY ANY_VALID driver in TWO clearly-separated parts:

  1. VALIDATE the user's answer — a dedicated `check(...)` method that JUDGES the
     candidate against the problem's constraints and returns a boolean. It checks
     PROPERTIES only (membership in the input, pairwise / adjacency / uniqueness /
     ordering rules, length, and — for an OPTIMISATION problem — that the size/cost
     equals the optimum, computed as a SCALAR via DP/greedy/BFS). `check` MUST NOT
     search for / reconstruct a solution; it only inspects what the user returned.

  2. OUTPUT exactly ONE canonical answer. The autograder stores a SINGLE expected
     output, but different correct users return DIFFERENT valid answers — so every
     valid answer must collapse to the SAME output:
       * valid   -> print a CANONICAL answer computed FROM THE INPUT ONLY, so it is
         identical for every valid submission. Build it directly when a fixed
         construction exists (e.g. a snake path), otherwise with a bounded, pruned,
         DETERMINISTIC search like a reference solver. NEVER print the user's answer
         (or anything derived from it) on the valid branch — that would make two
         correct users emit different outputs and defeat the validator.
       * invalid -> print the user's raw answer, so it differs from the canonical
         and fails to match.
       * empty / null user answer -> handle it (print `[]`, or treat as invalid per
         the problem). Never crash.

---- 2a. OPTIMISATION example: Largest Divisible Subset (return ANY one largest) --
`check` judges the user's list by PROPERTIES (+ optimal SIZE). The canonical output
is rebuilt from the INPUT ONLY via sorted DP, so every valid submission prints it.

```java
import java.util.*;

public class Main {
    static boolean check(List<Integer> ans, int[] arr, int optSize) {
        if (ans == null || ans.size() != optSize) return false;   // must be optimal size
        Set<Integer> pool = new HashSet<>();
        for (int v : arr) pool.add(v);
        for (int i = 0; i < ans.size(); i++) {
            if (!pool.contains(ans.get(i))) return false;         // element from input
            for (int j = i + 1; j < ans.size(); j++) {            // pairwise divisible
                long a = ans.get(i), b = ans.get(j);
                if (a % b != 0 && b % a != 0) return false;
            }
        }
        return true;
    }

    public static void main(String[] args) {
        Scanner sc = new Scanner(System.in);
        int n = sc.nextInt();
        int[] arr = new int[n];
        for (int i = 0; i < n; i++) arr[i] = sc.nextInt();
        List<Integer> ans = new Solution().findLargestChain(arr);

        // Canonical answer + optimum size, from the INPUT ONLY (sorted DP + rebuild).
        Arrays.sort(arr);
        int[] dp = new int[n], prev = new int[n];
        int best = 0, end = -1;
        for (int i = 0; i < n; i++) {
            dp[i] = 1; prev[i] = -1;
            for (int j = 0; j < i; j++)
                if (arr[i] % arr[j] == 0 && dp[j] + 1 > dp[i]) { dp[i] = dp[j] + 1; prev[i] = j; }
            if (dp[i] > best) { best = dp[i]; end = i; }
        }
        List<Integer> canon = new ArrayList<>();
        for (int i = end; i >= 0; i = prev[i]) canon.add(arr[i]);
        Collections.reverse(canon);

        List<Integer> out = check(ans, arr, best) ? canon            // valid -> canonical
                                                  : (ans == null ? new ArrayList<>() : ans); // invalid -> raw
        StringBuilder sb = new StringBuilder();
        for (int x : out) sb.append(x).append(' ');
        System.out.println(sb.toString().trim());
    }
}
```

---- 2b. FEASIBILITY example: any Hamiltonian path in an UNCONSTRAINED R x C grid -
"Return ANY ordering of all cells visiting each once via 4-adjacent moves." `check`
validates the user's path; the canonical is a fixed "snake", built from the input
only (no search). VERIFIED: a different valid path (e.g. column-major snake)
collapses to the SAME canonical, and an invalid path prints itself and mismatches.
USE THE SNAKE ONLY WHEN THE GRID IS UNCONSTRAINED. The moment the problem adds an
ordering constraint (numbered cells visited in order), blocked cells, or fixed
endpoints, the snake will VIOLATE it and your canonical becomes invalid — switch to
the COMPLETE BACKTRACKING canonical of EXAMPLE 2c.

```java
import java.util.*;

public class Main {
    // 1. VALIDATE the user's path by PROPERTIES — never searches for a path.
    static boolean check(List<int[]> path, int r, int c) {
        if (path == null || path.size() != r * c) return false;          // covers every cell once
        boolean[][] seen = new boolean[r][c];
        for (int i = 0; i < path.size(); i++) {
            int[] cell = path.get(i);
            if (cell == null || cell.length != 2) return false;
            int x = cell[0], y = cell[1];
            if (x < 0 || x >= r || y < 0 || y >= c) return false;        // in bounds
            if (seen[x][y]) return false;                                // no repeats
            seen[x][y] = true;
            if (i > 0) {                                                 // 4-adjacent step
                int px = path.get(i - 1)[0], py = path.get(i - 1)[1];
                if (Math.abs(px - x) + Math.abs(py - y) != 1) return false;
            }
        }
        return true;
    }

    // 2. Canonical path from the INPUT ONLY ("snake"): same for every valid answer.
    static List<int[]> canonical(int r, int c) {
        List<int[]> out = new ArrayList<>();
        for (int x = 0; x < r; x++)
            if (x % 2 == 0) for (int y = 0; y < c; y++)      out.add(new int[]{x, y});
            else            for (int y = c - 1; y >= 0; y--) out.add(new int[]{x, y});
        return out;
    }

    public static void main(String[] args) {
        Scanner sc = new Scanner(System.in);
        int r = sc.nextInt(), c = sc.nextInt();
        List<int[]> path = new Solution().findPath(r, c);     // user's candidate

        if (check(path, r, c))                                // valid  -> canonical
            for (int[] cell : canonical(r, c)) System.out.println(cell[0] + " " + cell[1]);
        else if (path == null || path.isEmpty())
            System.out.println("[]");                         // empty user answer
        else
            for (int[] cell : path) System.out.println(cell[0] + " " + cell[1]);  // invalid -> raw
    }
}
```

---- 2c. CONSTRAINED canonical via COMPLETE backtracking (VERIFIED) -------------
When the problem adds constraints that rule out a hard-coded construction like the
snake — e.g. some cells are numbered checkpoints that MUST be visited in increasing
order (a `k` / `window` parameter) — build the canonical the way a reference SOLVER
would: a COMPLETE, deterministic backtracking search (fixed start scan + fixed
direction order) that returns the FIRST valid answer it finds. "Complete" means it
is GUARANTEED to find a valid answer whenever one exists.

DO NOT use a greedy / BFS-stitching heuristic to build the canonical (e.g. "BFS a
path between consecutive checkpoints, then snake-fill the rest"). Heuristics are
NOT complete: they break adjacency where segments join, leave gaps, and return an
empty/invalid path even when a valid tour exists — so the validator then prints
empty and EVERY correct submission mismatches. Use backtracking. (`check` still only
inspects the user's answer; this search is for the OUTPUT canonical only.)

```java
import java.util.*;

public class Main {
    // VALIDATE the user's tour by PROPERTIES (never searches).
    static boolean check(List<List<Integer>> path, int[][] grid, int k) {
        int m = grid.length, n = grid[0].length;
        if (path == null || path.size() != m * n) return false;       // every cell once
        boolean[][] seen = new boolean[m][n];
        int expect = 1;                                               // next checkpoint due
        for (int idx = 0; idx < path.size(); idx++) {
            List<Integer> c = path.get(idx);
            if (c == null || c.size() != 2) return false;
            int x = c.get(0), y = c.get(1);
            if (x < 0 || x >= m || y < 0 || y >= n) return false;     // in bounds
            if (seen[x][y]) return false;                             // no repeats
            seen[x][y] = true;
            if (idx > 0) {                                            // 4-adjacent step
                int px = path.get(idx - 1).get(0), py = path.get(idx - 1).get(1);
                if (Math.abs(px - x) + Math.abs(py - y) != 1) return false;
            }
            if (grid[x][y] != 0) {                                    // checkpoint in order
                if (grid[x][y] != expect) return false;
                expect++;
            }
        }
        return expect == k + 1;                                       // all k, in order
    }

    // CANONICAL via COMPLETE backtracking — deterministic, input-only.
    static int[][] DIRS = {{1, 0}, {-1, 0}, {0, 1}, {0, -1}};
    static List<List<Integer>> canonical(int[][] grid, int k) {
        int m = grid.length, n = grid[0].length;
        for (int i = 0; i < m; i++)
            for (int j = 0; j < n; j++) {
                if (grid[i][j] > 1) continue;                         // can't START past checkpoint 1
                int[][] g = new int[m][n];
                for (int r = 0; r < m; r++) g[r] = grid[r].clone();   // search on a copy
                List<List<Integer>> res = new ArrayList<>();
                if (build(g, i, j, 1, res, m, n)) return res;
            }
        return new ArrayList<>();
    }
    static boolean build(int[][] g, int i, int j, int index, List<List<Integer>> res, int m, int n) {
        if (i < 0 || j < 0 || i >= m || j >= n || g[i][j] == -1
                || (g[i][j] != 0 && g[i][j] != index)) return false;  // off-grid / used / wrong checkpoint
        res.add(List.of(i, j));
        if (res.size() == m * n) return true;                         // all cells placed in order
        int next = (g[i][j] == index) ? index + 1 : index;
        int temp = g[i][j];
        g[i][j] = -1;                                                 // mark used
        for (int[] d : DIRS)
            if (build(g, i + d[0], j + d[1], next, res, m, n)) return true;
        res.remove(res.size() - 1);                                  // backtrack
        g[i][j] = temp;
        return false;
    }

    static void print(List<List<Integer>> p) {
        for (List<Integer> c : p) System.out.println(c.get(0) + " " + c.get(1));
    }
    public static void main(String[] args) {
        Scanner sc = new Scanner(System.in);
        int m = sc.nextInt(), n = sc.nextInt();
        int[][] grid = new int[m][n];
        for (int i = 0; i < m; i++) for (int j = 0; j < n; j++) grid[i][j] = sc.nextInt();
        int k = sc.nextInt();
        List<List<Integer>> path = new Solution().planExhibitTour(grid, k);
        if (check(path, grid, k)) {                                   // valid -> canonical
            List<List<Integer>> canon = canonical(grid, k);
            // SELF-CHECK: the canonical must itself pass check(); if the builder
            // failed (bug / no path found), fall back to the user's valid answer
            // rather than print an invalid/empty canonical.
            print(check(canon, grid, k) ? canon : path);
        } else if (path == null || path.isEmpty()) {
            System.out.println("[]");
        } else {
            print(path);                                              // invalid -> raw -> mismatches
        }
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
  - ANY_VALID -> embed a validator in TWO separated parts. PART 1: a `check(...)`
    that JUDGES the user's returned value against EVERY structural constraint by its
    PROPERTIES (and, for an OPTIMISATION problem, that its size/cost equals the
    optimal SCALAR you compute). `check` must NOT search for / reconstruct a solution
    to validate one — that is slow, can stack-overflow, and wrongly rejects valid
    answers that differ from the one you found. PART 2: output exactly ONE canonical
    answer:
        * on valid   -> print a CANONICAL answer computed FROM THE INPUT ONLY (a
          fixed construction, or a bounded deterministic search like a reference
          solver), identical for every valid submission, so the single stored
          expected output matches all of them. NEVER print the user's answer on the
          valid branch — different correct users return different valid answers, so
          printing the user's answer makes only one of them match;
        * on invalid -> print the user's raw answer (so it mismatches and fails).
    Think carefully about EVERY constraint and EVERY edge case (empty result,
    single element, duplicates, the optimal value being 0/empty, ties, elements
    not in the input, wrong size, etc.). Never crash on a malformed/empty answer —
    treat it as invalid. Keep the canonical-output builder efficient and bounded.
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
code). Read it CAREFULLY and decide whether it is a multiple-outputs question and,
if so, whether it is (A) ANY_ORDER or (B) ANY_VALID, using the element-vs-order
test: across two correct submissions, can the answer ELEMENTS / CONTENT differ
(ANY_VALID) or only their ORDER (ANY_ORDER)? If the answer is unique -> SINGLE (the
system will stop).

Apply the "return ALL" vs "return ANY ONE" decision rule:
  - If the problem asks to return ALL / EVERY answer (all solutions, all shortest
    paths, all valid sequences), the COMPLETE collection is uniquely determined —
    two correct submissions return the SAME collection in a possibly different
    order -> ANY_ORDER (resolved by sorting). This is the common case; prefer it.
    Worked example: Word Ladder II — "return all the shortest transformation
    sequences" -> ANY_ORDER. The set of shortest sequences is fixed; only the order
    of the list varies (each sequence's own order is fixed). It is NOT ANY_VALID.
  - If the problem asks to return ANY ONE / ANY VALID / SOME answer where the
    chosen CONTENT genuinely differs between correct submissions (e.g. "return any
    one largest subset") -> ANY_VALID.
Only pick ANY_VALID when sorting genuinely cannot canonicalise the output."""

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
    CRITICAL SIZE CAP FOR SEARCH-BASED (NP-hard) PROBLEMS: if a CORRECT solution —
    and the validator's canonical builder — must SEARCH (backtracking / DFS) because
    the problem is NP-hard or has no known polynomial algorithm (Hamiltonian
    path/tour, exact cover, graph colouring, subset/partition with constraints,
    ordered-checkpoint tours, etc.), then NO efficient O(n^2) solution exists and a
    backtracking solver blows up EXPONENTIALLY. For these, keep EVERY input SMALL
    enough that backtracking finishes in a couple of seconds — e.g. a grid of at
    most ~5x5 (<= ~25 cells), at most ~12-15 nodes/elements. Do NOT emit a large
    "long"/"stress" instance (a 8x8 / 20x20 grid, hundreds of elements) for such a
    problem — it WILL time out the reference and equivalent solutions and fail
    verification. Make "long"/"stress" stress the STRUCTURE (tricky checkpoint
    placement, near-worst-case branching, forced backtracking) at that small size,
    NOT raw size. Only scale to hundreds of elements when a genuinely polynomial
    correct solution exists.
    Provide EXACTLY 10 inputs total — covering all four kinds (a few normal,
    several edge, at least one long, at least one stress). Inputs must be
    language-independent stdin strings.

Before finishing, silently double-check that the 10 inputs cover every distinct
behaviour and all four kinds (normal/edge/long/stress); if one is weak or
redundant, replace it. Return exactly the 10 final inputs."""

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
    },
    "required": [
        "reasoning", "title", "stdin_format", "output_format",
        "function_name", "signatures", "test_inputs",
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
  - PREFER ANY_ORDER (sort_normalize) WHENEVER IT WORKS. If the correct output is a
    fixed collection that merely may be listed in different orders (including
    "return ALL ..." problems such as all shortest paths/sequences), normalise by
    SORTING — do NOT write a validator. The sort_normalize path only re-orders what
    the user returned; it never recomputes the answer, so it is efficient and
    cannot recurse or stack-overflow. Only use validator_function when the answer
    CONTENT itself can differ between correct submissions (e.g. "return ANY ONE").
  - ANY_ORDER  -> deterministically normalise the user's returned value before
    printing. Sort ONLY the dimensions the problem leaves unordered; PRESERVE the
    internal order of any inherently-ordered sequence. For a list of sequences
    (e.g. a list of transformation paths), sort the OUTER list with a deterministic
    comparator (compare element by element, shorter-first on a prefix tie) and keep
    each inner sequence's element order intact. Sort nested unordered groups at
    every unordered level (e.g. words within an anagram group, then the groups).
    Do NOT recompute the answer and do NOT recurse over the input — just normalise.
  - ANY_VALID  -> embed a validator in TWO clearly-separated parts (see REFERENCE
    EXAMPLE 2):
    PART 1 — VALIDATE (a dedicated `check(...)` that returns a boolean). JUDGE the
    user's returned candidate against EVERY constraint by its PROPERTIES (membership
    in the input, pairwise / adjacency / uniqueness / ordering / length rules, every
    edge case) and, for an OPTIMISATION problem, that its size/cost equals the
    optimum (computed as a SCALAR via DP/greedy/BFS). `check` MUST NOT search for or
    reconstruct a solution — it only inspects what the user returned. Validating by
    searching (recursive DFS/backtracking to find a path/subset, then comparing) is
    a top bug: it is slow, can stack-overflow, and wrongly REJECTS valid answers
    that differ from the one it found.
    PART 2 — OUTPUT exactly ONE canonical answer. The autograder stores a SINGLE
    expected output, but different correct users return DIFFERENT valid answers, so
    every valid answer must collapse to the SAME output:
      * VALID   -> print a CANONICAL answer computed FROM THE INPUT ONLY, so it is
        identical for every valid submission. Build it directly when a fixed
        construction exists (e.g. a snake path, sorted/DP reconstruction); otherwise
        with a COMPLETE, DETERMINISTIC search like a reference solver — backtracking
        / DFS with pruning that returns the first valid answer in a fixed exploration
        order (see EXAMPLE 2c). It depends ONLY on the input, NEVER on the user's
        answer. DO NOT build the canonical with a greedy / BFS-stitching HEURISTIC
        (e.g. "BFS between checkpoints then fill the rest"): heuristics are NOT
        complete — they break adjacency, leave gaps, and return an empty/invalid
        canonical even when a valid answer exists, so the validator prints empty and
        EVERY correct submission mismatches. The canonical builder MUST be complete
        (guaranteed to find a valid answer if one exists).
        CHOOSE THE RIGHT CONSTRUCTION: a direct fixed pattern (snake, sorted order)
        is valid ONLY if it provably satisfies EVERY constraint. If the problem adds
        ORDERING or PLACEMENT constraints — numbered checkpoints that must be visited
        in order, a fixed start/end, blocked/forbidden cells, capacities — a snake or
        other fixed pattern will GENERALLY VIOLATE them, making your canonical itself
        invalid. In that case you MUST use the complete backtracking search of
        EXAMPLE 2c, not the snake of EXAMPLE 2b.
        SELF-CHECK (REQUIRED): the canonical your code builds MUST itself pass your
        own `check(...)`. Mentally (or in code) confirm `check(canonical(input))` is
        TRUE for every input — if it would be false, you picked the wrong
        construction. As a safety net, if the builder returns empty/`check`-failing
        output for a user answer that PASSED `check`, print the user's answer instead.
      * INVALID -> print the user's raw answer (so it differs from the canonical and
        fails to match).
      * empty / null user answer -> handle explicitly (print `[]`, or treat as
        invalid per the problem). Never crash.
    FORBIDDEN on the valid branch: printing the user's returned value or anything
    derived from it. NEVER write `if (valid) { print the user's answer }` — two
    correct users would then emit different outputs and only one matches the single
    stored expected output, defeating the validator.
  - CORRECTNESS BEFORE SPEED for the canonical builder: a COMPLETE backtracking /
    DFS search that always finds a valid answer (when one exists) is REQUIRED — never
    trade it for a faster heuristic that is sometimes wrong. The spec keeps inputs
    bounded so a pruned backtracking search is feasible; prune as soon as a constraint
    is violated (and search on a COPY of the input so `check` still sees the original).
    `check` itself stays cheap (a linear/quadratic scan, plus at most one scalar
    DP/BFS for the optimum). Only if recursion depth could genuinely overflow the
    stack on the stress inputs, convert the SAME complete search to an explicit stack
    — keep it complete, do not downgrade to a heuristic.
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
       * ANY_VALID  -> JUDGE the user's answer: a `check` step verifies it against
         EVERY structural constraint (and, for an OPTIMISATION problem, that its
         size/cost equals the optimal SCALAR), ACCEPTING every genuinely valid answer
         (including alternative-correct ones) and REJECTING every answer that
         violates ANY constraint, WITHOUT searching for a solution to validate one.
         On the VALID branch it MUST print a CANONICAL answer derived FROM THE INPUT
         ONLY (identical for every valid answer) — if it instead prints the user's
         answer or anything derived from it, that is a HARD FAIL (two correct users
         would emit different outputs against one stored expected output). It must
         never crash on a malformed/empty answer (treat as invalid), be
         deterministic, and not time out / overflow building the canonical.
         NOTE: a COMPLETE backtracking/DFS that scans starts and neighbours in a
         FIXED order and returns the first valid answer IS deterministic and a pure
         function of the input (it never reads the user's answer) — do NOT flag that
         as non-deterministic or as varying between submissions; only flag REAL
         non-determinism (randomness, hash-iteration order, reading the user's answer).
     Flag validators that are too lenient (accept a wrong answer), too strict
     (reject a valid alternative — ESPECIALLY by reconstructing one solution and
     comparing for equality), that SEARCH for / reconstruct a full solution instead
     of judging the user's (recursive DFS/backtracking), or are liable to crash /
     overflow / time out on the long & stress inputs.
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
        "  - JUDGE, DON'T SOLVE (validation): the `check`/validation step must CHECK the\n"
        "    user's answer against the constraints (and, for optimisation, a single\n"
        "    recomputed optimal SCALAR). Flag it if VALIDATION searches for /\n"
        "    reconstructs a solution then accepts only by equality to it (that wrongly\n"
        "    rejects other valid answers). (Computing a canonical for the OUTPUT is\n"
        "    fine — see below.)\n"
        "  - DETERMINISTIC OUTPUT (ANY_VALID, HARD FAIL): on the VALID branch it MUST\n"
        "    print a CANONICAL answer derived FROM THE INPUT ONLY (a fixed construction,\n"
        "    a sorted/DP reconstruction, or a bounded deterministic search) — identical\n"
        "    for every valid answer. If the valid branch prints the user's returned\n"
        "    answer (or ANYTHING derived from it), it is BROKEN and you MUST mark it\n"
        "    not-ok: different correct users would emit different outputs, but the\n"
        "    autograder stores ONE expected output, so only one valid user would pass.\n"
        "    Also flag a canonical OUTPUT builder that is a greedy / BFS-stitching\n"
        "    HEURISTIC (incomplete — can return empty/invalid even when a valid answer\n"
        "    exists, so the driver prints empty and every correct submission mismatches):\n"
        "    it MUST be a COMPLETE search (backtracking/DFS) or a proven direct\n"
        "    construction. Also flag one that could time out / stack-overflow on the\n"
        "    long & stress inputs. The invalid branch prints the user's answer.\n"
        "  - NOT A BUG — fixed-order complete search IS deterministic: a COMPLETE\n"
        "    backtracking/DFS that scans start positions and neighbours in a FIXED order\n"
        "    and returns the FIRST valid answer is DETERMINISTIC and a pure function of\n"
        "    the INPUT. `canonical()` does NOT read the user's answer, so the SAME grid\n"
        "    always yields the SAME canonical regardless of which valid path the user\n"
        "    returned (row-major vs column-major submissions BOTH get the one canonical\n"
        "    the search finds — they match). This is exactly the 'complete search with a\n"
        "    fixed tiebreaker' form and is CORRECT — do NOT flag it as non-deterministic\n"
        "    or as 'varying between submissions'. Flag non-determinism ONLY when it is\n"
        "    REAL: randomness, time-dependence, iterating a HashSet/HashMap to build the\n"
        "    output (hash-order), or actually reading the user's answer to construct the\n"
        "    canonical. If you cannot point to one of those, the canonical is fine.\n"
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
