"""Agentic validator generator for multiple-outputs coding questions.

This package converts a *multiple-outputs* coding question (one where the
expected answer is not unique) into a single deterministic question by either
normalising the program output (when order does not matter) or by embedding a
validator function inside the Java `main` (when several genuinely different
answers are valid).

It only operates on multiple-outputs questions; ordinary single-answer
questions are rejected.
"""

__all__ = ["__version__"]
__version__ = "0.1.0"
