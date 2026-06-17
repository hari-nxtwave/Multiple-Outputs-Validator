"""Locate a JDK and provide compile/run helpers for the generated programs.

Discovery order: ``PATH`` -> ``$JAVA_HOME/bin`` -> a portable JDK unpacked under
``~/.local/jdk/jdk-*``. Returns ``None`` when no JDK is found so the pipeline can
fall back to agentic-only verification.
"""

from __future__ import annotations

import glob
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Jdk:
    javac: str
    java: str


def find_jdk() -> Jdk | None:
    javac = shutil.which("javac")
    java = shutil.which("java")
    if javac and java:
        return Jdk(javac, java)

    candidates: list[str] = []
    if os.environ.get("JAVA_HOME"):
        candidates.append(os.environ["JAVA_HOME"])
    candidates += sorted(glob.glob(os.path.expanduser("~/.local/jdk/jdk-*")))

    for home in candidates:
        jc = Path(home) / "bin" / "javac"
        jv = Path(home) / "bin" / "java"
        if jc.exists() and jv.exists():
            return Jdk(str(jc), str(jv))
    return None


@dataclass
class CompileResult:
    ok: bool
    blames_main: bool        # error referenced Main.java
    blames_solution: bool    # error referenced Solution.java
    message: str


def compile_pair(jdk: Jdk, workdir: Path, main_src: str, solution_src: str) -> CompileResult:
    """Write Main.java + Solution.java into *workdir* and compile both."""
    (workdir / "Main.java").write_text(main_src, encoding="utf-8")
    (workdir / "Solution.java").write_text(solution_src, encoding="utf-8")
    proc = subprocess.run(
        [jdk.javac, "Main.java", "Solution.java"],
        cwd=workdir,
        capture_output=True,
        text=True,
        timeout=60,
    )
    if proc.returncode == 0:
        return CompileResult(True, False, False, "")
    err = proc.stderr or proc.stdout
    return CompileResult(
        ok=False,
        blames_main="Main.java" in err,
        blames_solution="Solution.java" in err,
        message=err.strip(),
    )


@dataclass
class RunResult:
    ok: bool
    output: str
    error: str


def run_main(jdk: Jdk, workdir: Path, stdin: str, timeout: float = 8.0) -> RunResult:
    """Run the compiled ``Main`` in *workdir* with *stdin*."""
    try:
        proc = subprocess.run(
            [jdk.java, "-cp", ".", "Main"],
            cwd=workdir,
            input=stdin,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return RunResult(False, "", f"timed out after {timeout}s")
    if proc.returncode != 0:
        return RunResult(False, proc.stdout, (proc.stderr or "non-zero exit").strip())
    return RunResult(True, proc.stdout, "")


def normalize_output(text: str) -> str:
    """Autograder-style normalisation: rstrip each line, strip blank edges."""
    lines = [ln.rstrip() for ln in text.replace("\r\n", "\n").split("\n")]
    return "\n".join(lines).strip()
