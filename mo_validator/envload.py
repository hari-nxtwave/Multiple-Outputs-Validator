"""Load environment variables from a local .env file (python-dotenv).

Imported for its side effect by :mod:`mo_validator.agent`, so any entrypoint
(CLI or website) that constructs an :class:`Agent` automatically picks up
``MO_API_KEY`` (the gateway key) and the optional ``MO_BASE_URL`` / ``MO_MODEL``
overrides without a manual ``export``.

Precedence: a real ``.env`` wins over the committed ``.env.example`` fallback,
and anything already exported in the shell wins over both (override=False).
"""

from __future__ import annotations

from pathlib import Path

try:
    from dotenv import load_dotenv
except ModuleNotFoundError:  # python-dotenv optional; manual export still works
    def load_dotenv(*_a, **_k):  # type: ignore
        return False

_ROOT = Path(__file__).resolve().parent.parent

for _name in (".env", ".env.example"):
    _path = _ROOT / _name
    if _path.exists():
        load_dotenv(_path, override=False)
