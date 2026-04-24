"""Capture provenance sidecars: persist ``skill_args`` alongside a capture.

``clauditor capture`` writes the skill's stdout to ``<skill>.txt`` (primary
path ``tests/eval/captured/<skill>.txt``; fallback
``.clauditor/captures/<skill>.txt``). Downstream consumers — notably
``clauditor propose-eval`` — need to know *which args* the capture was
invoked with so the proposed ``EvalSpec.test_args`` re-runs the skill
under the same conditions. Issue #117: without this, the proposer
emitted a shape-only placeholder that dropped flags like
``--depth quick`` and produced a self-defeating ``validate`` re-run.

This module owns the sidecar format: a JSON file named
``<skill>.capture.json`` sitting next to the capture text file, with
``schema_version`` as the first key per
``.claude/rules/json-schema-version.md``.

Pure compute + thin I/O per ``.claude/rules/pure-compute-vs-io-split.md``:

* :class:`CaptureProvenance` is a frozen dataclass with ``to_json`` /
  ``from_json`` serializers.
* :func:`sidecar_path_for` derives the sidecar path from a capture text
  path — no I/O.
* :func:`write_capture_provenance` is the only function that writes.
* :func:`read_capture_provenance` tolerates a missing sidecar (returns
  ``None``) and skips-and-warns on schema mismatch rather than raising,
  matching the audit loader's schema-version handling.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

_SCHEMA_VERSION = 1

_SIDECAR_SUFFIX = ".capture.json"


@dataclass(frozen=True)
class CaptureProvenance:
    """Minimal record of how a capture was produced.

    ``skill_args`` is the space-joined argument string exactly as
    ``clauditor capture`` passed it to the runner (i.e. the string the
    skill actually saw). Storing the joined form — rather than a list —
    keeps ``test_args`` insertion trivial: the proposer just threads the
    string verbatim into ``EvalSpec.test_args`` and ``validate`` sends
    the same prompt to Claude that ``capture`` did.

    *Caveat on shell quoting:* ``clauditor capture`` uses a plain
    ``" ".join(args.skill_args)`` to render the string, which loses
    whatever shell quoting the user typed. An argument like
    ``"La Jolla, CA"`` round-trips as the bare substring
    ``La Jolla, CA`` embedded in the space-joined result. For Claude's
    slash-command parser — which reads the whole trailing string as a
    natural-language tail — this is fine and matches what ``capture``
    itself sent to the skill. Args containing newlines or the literal
    closing tag ``</capture_args>`` would break the prompt, but those
    cases are self-inflicted by the user and are out of scope for this
    fix.

    ``captured_at`` is an ISO-8601 UTC timestamp with a trailing ``Z``.
    It is informational — no downstream consumer keys on it — but
    tracking provenance age is useful for humans auditing sidecar
    staleness after a skill's SKILL.md evolves.
    """

    skill_name: str
    skill_args: str
    captured_at: str
    schema_version: int = _SCHEMA_VERSION

    def to_json(self) -> str:
        """Serialize with ``schema_version`` as the first top-level key."""
        payload = {
            "schema_version": self.schema_version,
            "skill_name": self.skill_name,
            "skill_args": self.skill_args,
            "captured_at": self.captured_at,
        }
        return json.dumps(payload, indent=2) + "\n"


def sidecar_path_for(capture_txt_path: Path) -> Path:
    """Return the ``.capture.json`` sidecar path next to a capture ``.txt``.

    ``greeter.txt`` → ``greeter.capture.json``. Uses ``with_suffix``
    semantics (drops the final suffix, appends ``.capture.json``) so a
    ``--versioned`` capture like ``greeter-2026-04-24.txt`` becomes
    ``greeter-2026-04-24.capture.json`` — the date stays in the stem.
    """
    return capture_txt_path.with_suffix(_SIDECAR_SUFFIX)


def write_capture_provenance(
    capture_txt_path: Path,
    *,
    skill_name: str,
    skill_args: str,
) -> Path:
    """Write a :class:`CaptureProvenance` sidecar next to the capture file.

    Returns the sidecar path. Creates the parent directory if missing
    (the caller will already have done so for the ``.txt``, but this is
    defensive for non-standard capture targets).

    ``captured_at`` is stamped from :func:`datetime.now(timezone.utc)`
    so the timestamp is always in UTC with a ``Z`` suffix. Callers that
    need a deterministic timestamp (tests, replay tooling) should patch
    this module's clock rather than pre-computing the timestamp at the
    call site — keeps the writer's signature small.
    """
    stamp = datetime.now(UTC).isoformat(timespec="seconds")
    # ``datetime.isoformat`` returns ``+00:00`` for UTC; normalize to the
    # conventional ``Z`` suffix so the wire format matches what a human
    # would expect from an ISO-8601 UTC timestamp.
    if stamp.endswith("+00:00"):
        stamp = stamp[: -len("+00:00")] + "Z"
    record = CaptureProvenance(
        skill_name=skill_name,
        skill_args=skill_args,
        captured_at=stamp,
    )
    sidecar = sidecar_path_for(capture_txt_path)
    sidecar.parent.mkdir(parents=True, exist_ok=True)
    sidecar.write_text(record.to_json(), encoding="utf-8")
    return sidecar


def read_capture_provenance(
    capture_txt_path: Path,
) -> CaptureProvenance | None:
    """Return the sidecar for a capture file, or ``None`` if missing/invalid.

    Tolerates three failure modes without raising:

    * Sidecar does not exist → ``None`` silently.
    * Sidecar exists but has the wrong ``schema_version`` → ``None``
      with a stderr warning (mirrors
      :func:`clauditor.audit._check_schema_version`).
    * Sidecar exists but is malformed JSON, missing keys, or has
      wrong-typed values → ``None`` with a stderr warning.

    The tolerance is deliberate: a corrupt sidecar should degrade
    ``propose-eval`` to the pre-#117 shape-only behavior (plus a
    stderr warning) rather than hard-failing a command that has real
    value even without the captured args.
    """
    sidecar = sidecar_path_for(capture_txt_path)
    if not sidecar.is_file():
        return None

    try:
        raw = sidecar.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        print(
            f"clauditor.capture_provenance: could not read {sidecar}: "
            f"{exc} — skipping",
            file=sys.stderr,
        )
        return None

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        print(
            f"clauditor.capture_provenance: {sidecar} is not valid JSON: "
            f"{exc} — skipping",
            file=sys.stderr,
        )
        return None

    if not isinstance(data, dict):
        print(
            f"clauditor.capture_provenance: {sidecar} top-level JSON "
            f"must be an object, got {type(data).__name__} — skipping",
            file=sys.stderr,
        )
        return None

    version = data.get("schema_version")
    if version != _SCHEMA_VERSION:
        print(
            f"clauditor.capture_provenance: {sidecar} has "
            f"schema_version={version!r}, expected {_SCHEMA_VERSION} — "
            "skipping",
            file=sys.stderr,
        )
        return None

    skill_name = data.get("skill_name")
    skill_args = data.get("skill_args")
    captured_at = data.get("captured_at")
    if not isinstance(skill_name, str) or not isinstance(skill_args, str):
        print(
            f"clauditor.capture_provenance: {sidecar} is missing required "
            "string fields (skill_name, skill_args) — skipping",
            file=sys.stderr,
        )
        return None
    # ``captured_at`` is informational; a missing or wrong-typed value
    # should not disqualify an otherwise-valid sidecar. Coerce to the
    # empty string so callers can treat it as "unknown".
    if not isinstance(captured_at, str):
        captured_at = ""

    return CaptureProvenance(
        skill_name=skill_name,
        skill_args=skill_args,
        captured_at=captured_at,
    )
