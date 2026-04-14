"""Redaction helpers for execution transcripts.

Pure logic; no I/O. The public entry point is :func:`redact`, which walks a
JSON-compatible Python value and returns a new structure with secrets
replaced by the literal string ``"[REDACTED]"``, along with a count of how
many replacements were made.

Two complementary scrub strategies run during the walk:

1. **Key-based scrubbing.** When a dict key (case-insensitive) matches one
   of the sensitive suffixes — ``*_KEY``, ``*_TOKEN``, ``*_SECRET``,
   ``*_PASSWORD``, ``*_PASSPHRASE``, ``*_CREDENTIAL`` — or is exactly
   ``AUTH`` / ``API_KEY``, the entire value is replaced.

2. **Regex-based scrubbing inside string values.** String leaves are
   scanned for known secret shapes and only the matched span is replaced.
   The regex set:

   - OpenAI / Anthropic-style keys: ``sk-[A-Za-z0-9_\\-]{20,}`` (matches
     ``sk-proj-...``, ``sk-ant-api03-...``, etc. through trailing dashes
     and underscores, so long keys are not truncated mid-secret)
   - GitHub classic PAT: ``ghp_[A-Za-z0-9]{36,}``
   - GitHub fine-grained PAT: ``github_pat_[A-Za-z0-9_]{80,}``
   - AWS access key ids: ``AKIA[0-9A-Z]{16}``, ``ASIA[0-9A-Z]{16}``
   - Bearer tokens: ``Bearer\\s+[A-Za-z0-9._\\-]{20,}``
   - Slack tokens: ``xox[abprs]-[A-Za-z0-9-]{10,}``

See ``plans/super/26-execution-transcripts.md`` §US-001 for the
authoritative specification and decisions DEC-003, DEC-007, DEC-010.
"""

from __future__ import annotations

import re
from typing import Any

_REDACTED = "[REDACTED]"

_SENSITIVE_KEY_SUFFIXES = (
    "_KEY",
    "_TOKEN",
    "_SECRET",
    "_PASSWORD",
    "_PASSPHRASE",
    "_CREDENTIAL",
)

_SENSITIVE_EXACT_KEYS = frozenset(
    {
        "AUTH",
        "API_KEY",
        "KEY",
        "TOKEN",
        "SECRET",
        "PASSWORD",
        "PASSPHRASE",
        "CREDENTIAL",
        "CREDENTIALS",
    }
)

_SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"sk-[A-Za-z0-9_\-]{20,}"),
    re.compile(r"ghp_[A-Za-z0-9]{36,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{80,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"ASIA[0-9A-Z]{16}"),
    re.compile(r"Bearer\s+[A-Za-z0-9._\-]{20,}"),
    re.compile(r"xox[abprs]-[A-Za-z0-9-]{10,}"),
)


def _is_sensitive_key(key: Any) -> bool:
    if not isinstance(key, str):
        return False
    upper = key.upper()
    if upper in _SENSITIVE_EXACT_KEYS:
        return True
    return any(upper.endswith(suffix) for suffix in _SENSITIVE_KEY_SUFFIXES)


def _scrub_string(value: str) -> tuple[str, int]:
    count = 0
    result = value
    for pattern in _SECRET_PATTERNS:
        result, n = pattern.subn(_REDACTED, result)
        count += n
    return result, count


def redact(obj: Any) -> tuple[Any, int]:
    """Return ``(scrubbed_copy, count)`` for a JSON-compatible value.

    The input is never mutated; nested containers are rebuilt. ``count``
    is the total number of key-based plus regex-based replacements made
    anywhere in the walk.
    """

    if isinstance(obj, dict):
        new_dict: dict[Any, Any] = {}
        total = 0
        for key, value in obj.items():
            if _is_sensitive_key(key):
                new_dict[key] = _REDACTED
                total += 1
                continue
            scrubbed, n = redact(value)
            new_dict[key] = scrubbed
            total += n
        return new_dict, total

    if isinstance(obj, (list, tuple)):
        new_list: list[Any] = []
        total = 0
        for item in obj:
            scrubbed, n = redact(item)
            new_list.append(scrubbed)
            total += n
        return new_list, total

    if isinstance(obj, str):
        return _scrub_string(obj)

    return obj, 0
