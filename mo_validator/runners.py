"""Per-language compile/run support for the generated validator programs.

Each :class:`Runner` knows how to take a *driver* (the validator-embedded
program) and a *solution* (a user submission), lay them out on disk, compile if
needed, and run with stdin.

Calling contract per language (the agents generate code to match this):
  - python / javascript / cpp: the SOLUTION source is concatenated ABOVE the
    DRIVER source into a single file, so the solution's function/class is in
    scope for the driver. The driver calls it directly.
  - java: the driver is `Main.java` (public class Main) and the solution is
    `Solution.java` (package-private class Solution); both are compiled together.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from .javatools import find_jdk

COMPILE_TIMEOUT = 60
RUN_TIMEOUT = 8.0


@dataclass
class CompileResult:
    ok: bool
    message: str = ""


@dataclass
class RunResult:
    ok: bool
    output: str = ""
    error: str = ""


def normalize_output(text: str) -> str:
    """Autograder-style normalisation: rstrip each line, strip blank edges."""
    lines = [ln.rstrip() for ln in text.replace("\r\n", "\n").split("\n")]
    return "\n".join(lines).strip()


class Runner:
    key: str
    name: str
    highlight: str          # Prism.js language id for the frontend
    solution_hint: str      # how the user writes their solution in this language

    def available(self) -> bool:
        raise NotImplementedError

    def prepare(self, workdir: Path, driver_src: str, solution_src: str) -> CompileResult:
        raise NotImplementedError

    def run(self, workdir: Path, stdin: str, timeout: float = RUN_TIMEOUT) -> RunResult:
        raise NotImplementedError

    def combined_program(self, driver_src: str, solution_src: str) -> str:
        """A complete, runnable program (solution + driver, with entry point).

        This is exactly what the harness compiles/runs, surfaced for the UI so a
        user can copy one self-contained program (including the driver's
        main/entry point) per language.
        """
        raise NotImplementedError


def _exec(cmd: list[str], cwd: Path, stdin: str | None, timeout: float) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd, cwd=cwd, input=stdin, capture_output=True, text=True, timeout=timeout
    )


class _ConcatRunner(Runner):
    """python / javascript / cpp: solution + driver in one file."""

    filename: str

    def _compile_cmd(self, workdir: Path) -> list[str] | None:
        return None

    def _run_cmd(self, workdir: Path) -> list[str]:
        raise NotImplementedError

    def _assemble(self, solution_src: str, driver_src: str) -> str:
        """Combine solution + driver into the single source file to run."""
        return solution_src.rstrip() + "\n\n" + driver_src.strip() + "\n"

    def combined_program(self, driver_src: str, solution_src: str) -> str:
        return self._assemble(solution_src, driver_src)

    def prepare(self, workdir: Path, driver_src: str, solution_src: str) -> CompileResult:
        (workdir / self.filename).write_text(
            self._assemble(solution_src, driver_src), encoding="utf-8"
        )
        cmd = self._compile_cmd(workdir)
        if cmd is None:
            return CompileResult(True)
        try:
            proc = _exec(cmd, workdir, None, COMPILE_TIMEOUT)
        except subprocess.TimeoutExpired:
            return CompileResult(False, "compilation timed out")
        if proc.returncode != 0:
            return CompileResult(False, (proc.stderr or proc.stdout).strip())
        return CompileResult(True)

    def run(self, workdir: Path, stdin: str, timeout: float = RUN_TIMEOUT) -> RunResult:
        try:
            proc = _exec(self._run_cmd(workdir), workdir, stdin, timeout)
        except subprocess.TimeoutExpired:
            return RunResult(False, "", f"timed out after {timeout}s")
        if proc.returncode != 0:
            return RunResult(False, proc.stdout, (proc.stderr or "non-zero exit").strip())
        return RunResult(True, proc.stdout, "")


class PythonRunner(_ConcatRunner):
    key, name, highlight = "python", "Python", "python"
    solution_hint = "Define the required top-level function(s); the driver calls them."
    filename = "prog.py"

    def available(self) -> bool:
        return _which("python3") is not None

    def _run_cmd(self, workdir: Path) -> list[str]:
        return ["python3", self.filename]


class JavaScriptRunner(_ConcatRunner):
    key, name, highlight = "javascript", "JavaScript", "javascript"
    solution_hint = "Define the required function(s) (Node.js); the driver calls them."
    filename = "prog.js"

    def available(self) -> bool:
        return _which("node") is not None

    def _assemble(self, solution_src: str, driver_src: str) -> str:
        # Wrap the driver in an IIFE so its top-level `const`/`let` declarations
        # are function-scoped and cannot collide with identifiers the (separately
        # generated) solution declares at module scope. The solution's top-level
        # functions remain reachable from inside the IIFE via closure.
        return (
            solution_src.rstrip()
            + "\n\n;(function () {\n"
            + driver_src.strip()
            + "\n})();\n"
        )

    def _run_cmd(self, workdir: Path) -> list[str]:
        return ["node", self.filename]


class CppRunner(_ConcatRunner):
    key, name, highlight = "cpp", "C++", "cpp"
    solution_hint = "Define the required function/class; the driver's main() calls it."
    filename = "prog.cpp"

    def available(self) -> bool:
        return _which("g++") is not None

    def _assemble(self, solution_src: str, driver_src: str) -> str:
        # Strip any stray main() the model added to the solution; the driver owns
        # main, and two definitions fail with "redefinition of main".
        solution_src = sanitize_solution("cpp", solution_src)
        return solution_src.rstrip() + "\n\n" + driver_src.strip() + "\n"

    def _compile_cmd(self, workdir: Path) -> list[str]:
        return ["g++", "-O2", "-std=c++17", "-w", self.filename, "-o", "prog"]

    def _run_cmd(self, workdir: Path) -> list[str]:
        return ["./prog"]


class JavaRunner(Runner):
    key, name, highlight = "java", "Java", "java"
    solution_hint = "Write a non-public `class Solution` with the required method."

    def available(self) -> bool:
        return find_jdk() is not None

    def combined_program(self, driver_src: str, solution_src: str) -> str:
        # Two compilation units (Main + Solution). The driver (Main) holds the
        # `public static void main`. Show both, clearly delimited.
        return (
            "// ===== Main.java (driver — contains `public static void main`) =====\n"
            + driver_src.strip()
            + "\n\n// ===== Solution.java (the user's solution) =====\n"
            + solution_src.strip()
            + "\n"
        )

    def prepare(self, workdir: Path, driver_src: str, solution_src: str) -> CompileResult:
        jdk = find_jdk()
        if jdk is None:
            return CompileResult(False, "no JDK found")
        (workdir / "Main.java").write_text(driver_src, encoding="utf-8")
        (workdir / "Solution.java").write_text(solution_src, encoding="utf-8")
        try:
            proc = _exec([jdk.javac, "Main.java", "Solution.java"], workdir, None, COMPILE_TIMEOUT)
        except subprocess.TimeoutExpired:
            return CompileResult(False, "compilation timed out")
        if proc.returncode != 0:
            return CompileResult(False, (proc.stderr or proc.stdout).strip())
        return CompileResult(True)

    def run(self, workdir: Path, stdin: str, timeout: float = RUN_TIMEOUT) -> RunResult:
        jdk = find_jdk()
        try:
            proc = _exec([jdk.java, "-cp", ".", "Main"], workdir, stdin, timeout)
        except subprocess.TimeoutExpired:
            return RunResult(False, "", f"timed out after {timeout}s")
        if proc.returncode != 0:
            return RunResult(False, proc.stdout, (proc.stderr or "non-zero exit").strip())
        return RunResult(True, proc.stdout, "")


def _which(name: str) -> str | None:
    import shutil
    return shutil.which(name)


# Patterns a *solution* must NOT contain: it has to be a bare function/class, with
# no stdin reading and no program entry point, because the driver supplies those.
# (regex, human reason) per language.
_SOLUTION_FORBIDDEN: dict[str, list[tuple[str, str]]] = {
    "python": [
        (r"\bsys\.stdin\b", "reads stdin (sys.stdin); the driver does that"),
        (r"(?<!\w)input\s*\(", "reads stdin (input()); the driver does that"),
        (r"if\s+__name__\s*==", "has a __main__ entry point; remove it"),
    ],
    "javascript": [
        (r"readFileSync\s*\(\s*0", "reads stdin (readFileSync(0)); the driver does that"),
        (r"\bprocess\.stdin\b", "reads stdin (process.stdin); the driver does that"),
        (r"\bprocess\.argv\b", "reads argv; the driver supplies input"),
        (r"\brequire\s*\(\s*['\"]readline", "uses readline; the driver reads input"),
    ],
    "cpp": [
        (r"\bint\s+main\s*\(", "defines main(); only the driver may define main"),
        (r"\bstd::cin\b|\bcin\s*>>", "reads stdin (cin); the driver does that"),
        (r"\bgetline\s*\(", "reads stdin (getline); the driver does that"),
        (r"\bscanf\s*\(", "reads stdin (scanf); the driver does that"),
    ],
    "java": [
        (r"\bpublic\s+class\b", "declares a public class; Solution must be non-public"),
        (r"\bstatic\s+void\s+main\b", "defines main(); only Main may define main"),
        (r"\bclass\s+Main\b", "declares class Main; that name belongs to the driver"),
    ],
}


def lint_solution(language: str, code: str) -> list[str]:
    """Return reasons a solution violates the bare-function calling contract."""
    import re
    reasons: list[str] = []
    for pattern, reason in _SOLUTION_FORBIDDEN.get(language, []):
        if re.search(pattern, code):
            reasons.append(reason)
    return reasons


def sanitize_solution(language: str, code: str) -> str:
    """Best-effort cleanup applied to a solution before it is concatenated.

    Currently strips a stray C++ ``main`` (the driver owns it). Kept in sync with
    what the runners actually assemble, so lint checks see the same code.
    """
    if language == "cpp":
        return _strip_cpp_entry_point(code)
    return code


def _strip_cpp_entry_point(src: str) -> str:
    """Remove a stray ``int main(...) { ... }`` from a C++ solution.

    The driver supplies ``main``; some models insist on adding one to the
    solution too, which then fails to compile with "redefinition of main". We
    excise the whole definition with brace matching that ignores braces inside
    strings, char literals, and comments.
    """
    import re
    m = re.search(r"\bint\s+main\s*\([^)]*\)\s*", src)
    if not m:
        return src
    i = m.end()
    if i >= len(src) or src[i] != "{":  # a declaration, not a definition -> leave it
        return src
    depth, j, n = 0, i, len(src)
    while j < n:
        c = src[j]
        if c == "/" and j + 1 < n and src[j + 1] == "/":          # line comment
            nl = src.find("\n", j)
            j = n if nl == -1 else nl
            continue
        if c == "/" and j + 1 < n and src[j + 1] == "*":          # block comment
            end = src.find("*/", j + 2)
            j = n if end == -1 else end + 2
            continue
        if c in "\"'":                                            # string/char literal
            quote, j = c, j + 1
            while j < n:
                if src[j] == "\\":
                    j += 2
                    continue
                if src[j] == quote:
                    j += 1
                    break
                j += 1
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return (src[:m.start()].rstrip() + "\n" + src[j + 1:].lstrip()).strip() + "\n"
        j += 1
    return src  # unbalanced; leave untouched


_RUNNERS: dict[str, Runner] = {
    r.key: r for r in (PythonRunner(), JavaScriptRunner(), CppRunner(), JavaRunner())
}

ALL_LANGUAGES = list(_RUNNERS.keys())


def get_runner(key: str) -> Runner | None:
    return _RUNNERS.get(key)


def available_languages() -> list[str]:
    return [k for k, r in _RUNNERS.items() if r.available()]
