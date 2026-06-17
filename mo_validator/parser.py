"""Parse a question file into a description + the Java ``main`` function.

The expected input is a plain text / Markdown file that contains the problem
description (which states whether outputs may be returned "in any order" / "any
valid answer") and a fenced ```java code block holding the ``Main`` class.

The parser is deliberately tolerant: it pulls every fenced code block out, picks
the one that defines ``public static void main`` as the main program, and treats
the remaining prose as the description.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_FENCE_RE = re.compile(r"```[ \t]*([a-zA-Z0-9_+-]*)[ \t]*\n(.*?)```", re.DOTALL)


@dataclass
class Question:
    description: str
    main_java: str
    other_code: list[str]
    raw: str

    def as_prompt_context(self) -> str:
        parts = [
            "## Problem description\n" + self.description.strip(),
            "\n## Provided Java `Main` (current judge program)\n"
            "```java\n" + self.main_java.strip() + "\n```",
        ]
        for i, code in enumerate(self.other_code, 1):
            parts.append(f"\n## Additional code block {i}\n```java\n{code.strip()}\n```")
        return "\n".join(parts)


def parse_question(text: str) -> Question:
    """Split *text* into a :class:`Question`. Raises ``ValueError`` if no main."""
    blocks = _FENCE_RE.findall(text)
    java_blocks = [body for lang, body in blocks if lang.lower() in ("", "java")]

    main_java = ""
    other_code: list[str] = []
    for body in java_blocks:
        if "static void main" in body and not main_java:
            main_java = body
        else:
            other_code.append(body)

    if not main_java:
        raise ValueError(
            "Could not find a Java `main` function in the question. Include the "
            "judge program inside a ```java code block that defines "
            "`public static void main`."
        )

    # Description = everything outside fenced code blocks.
    description = _FENCE_RE.sub("", text).strip()
    if not description:
        description = text.strip()

    return Question(
        description=description,
        main_java=main_java,
        other_code=other_code,
        raw=text,
    )
