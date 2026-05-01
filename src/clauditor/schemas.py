"""Eval spec and schema definitions for skill output validation.

Loads eval.json files that define what a skill's output should look like.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal


@dataclass(frozen=True)
class AssertionKeySpec:
    """Per-assertion-type key invariant (DEC-008 of #61, DEC-012 of #67).

    Single source of truth for which assertion-dict keys each
    ``type`` value in :data:`ASSERTION_TYPE_REQUIRED_KEYS` accepts.
    ``required`` keys must be present; ``optional`` keys are
    allowed but the handler falls back to a safe default when
    they are omitted. Any key outside the union of ``required``,
    ``optional``, and the metadata set ``{"id", "type", "name"}``
    is rejected by ``_require_assertion_keys``. ``field_types``
    declares the expected native JSON type for each payload key
    (``str`` or ``int``); the loader validator enforces
    ``isinstance(val, expected)`` per-key at load time (DEC-012
    of #67) so string-typed ints like ``{"length": "500"}`` are
    rejected loudly. Consumed by the loader-side validator and
    the ``propose-eval`` prompt builder; kept in lockstep with
    the ``_ASSERTION_HANDLERS`` dispatch table in
    :mod:`clauditor.assertions` via a test-side drift guard.
    """

    required: frozenset[str]
    optional: frozenset[str] = frozenset()
    field_types: dict[str, type] = field(default_factory=dict)


# Single source of truth (DEC-008 of #61, DEC-001/DEC-012 of #67):
# every assertion ``type`` string accepted by
# :func:`clauditor.assertions.run_assertions` maps to the set of keys
# its handler reads from the assertion dict. The split between
# ``required`` and ``optional`` mirrors handler runtime behavior — if
# the handler reads ``.get(key, <default>)`` and the default is a
# sensible value (e.g. ``1`` for a minimum count), the key is
# optional; if the default is a sentinel that makes the assertion
# vacuous (e.g. ``""`` for a regex pattern, ``0`` for a length
# threshold), the key is required. ``field_types`` declares each
# payload key's expected native JSON type (``str`` or ``int``).
# Must stay in lockstep with ``_ASSERTION_HANDLERS`` in
# :mod:`clauditor.assertions`; the drift guard lives in
# ``tests/test_schemas.py::TestAssertionKeySpec``
# (``test_handler_signature_agrees_with_constant``).
ASSERTION_TYPE_REQUIRED_KEYS: dict[str, AssertionKeySpec] = {
    "contains": AssertionKeySpec(
        required=frozenset({"needle"}),
        field_types={"needle": str},
    ),
    "not_contains": AssertionKeySpec(
        required=frozenset({"needle"}),
        field_types={"needle": str},
    ),
    "regex": AssertionKeySpec(
        required=frozenset({"pattern"}),
        field_types={"pattern": str},
    ),
    "min_count": AssertionKeySpec(
        required=frozenset({"pattern", "count"}),
        field_types={"pattern": str, "count": int},
    ),
    "min_length": AssertionKeySpec(
        required=frozenset({"length"}),
        field_types={"length": int},
    ),
    "max_length": AssertionKeySpec(
        required=frozenset({"length"}),
        field_types={"length": int},
    ),
    "has_urls": AssertionKeySpec(
        required=frozenset(),
        optional=frozenset({"count"}),
        field_types={"count": int},
    ),
    "has_entries": AssertionKeySpec(
        required=frozenset(),
        optional=frozenset({"count"}),
        field_types={"count": int},
    ),
    "urls_reachable": AssertionKeySpec(
        required=frozenset(),
        optional=frozenset({"count"}),
        field_types={"count": int},
    ),
    "has_format": AssertionKeySpec(
        required=frozenset({"format"}),
        optional=frozenset({"count"}),
        field_types={"format": str, "count": int},
    ),
}


# Per-type drift-hint table (DEC-009 of #67). Sibling to
# :data:`ASSERTION_TYPE_REQUIRED_KEYS`. For each assertion ``type``,
# a map of ``common-wrong-key → correct-key-for-this-type``.
# Consulted by ``_require_assertion_keys`` when flagging an unknown
# key: emits `" — did you mean {suggestion!r}?"` if the wrong key
# is hinted for that specific type, else no suffix. Keyed per-type
# because ``pattern`` is a VALID key for ``regex``/``min_count``
# but should suggest ``needle`` on ``contains``/``not_contains``.
_ASSERTION_DRIFT_HINTS: dict[str, dict[str, str]] = {
    "contains":       {"value": "needle", "pattern": "needle"},
    "not_contains":   {"value": "needle", "pattern": "needle"},
    "regex":          {"value": "pattern"},
    "min_count":      {"value": "pattern", "minimum": "count",
                       "min_count": "count", "threshold": "count"},
    "min_length":     {"value": "length", "min": "length"},
    "max_length":     {"value": "length", "max": "length"},
    "has_urls":       {"value": "count", "minimum": "count",
                       "min_count": "count", "threshold": "count"},
    "has_entries":    {"value": "count", "minimum": "count",
                       "min_count": "count", "threshold": "count"},
    "urls_reachable": {"value": "count", "minimum": "count",
                       "min_count": "count", "threshold": "count"},
    "has_format":     {"value": "count", "minimum": "count",
                       "min_count": "count"},
}


@dataclass
class FieldRequirement:
    """A required field in a structured entry (venue, event, etc.).

    The ``format`` field accepts a registered format name
    (e.g. ``"phone_us"``, ``"domain"``) from
    :data:`clauditor.formats.FORMAT_REGISTRY`. Unknown names raise
    ``ValueError`` at construction time. When no registry entry fits,
    author the invariant as an L1 ``type: regex`` assertion instead —
    the registry-only contract keeps format names as stable
    identifiers across history so trend reports don't churn when a
    pattern changes. (Reversal of DEC-007, see #99.)

    ``id`` is a stable identifier scoped to the enclosing skill (DEC-001,
    ticket #25). It is required on all fields loaded from disk via
    ``EvalSpec.from_file()``; in-memory construction defaults to an empty
    string to keep unit-test fixtures terse.
    """

    name: str
    required: bool = True
    format: str | None = None  # Registry key only — see FORMAT_REGISTRY
    id: str = ""  # Stable id, required via from_file() (DEC-001)

    def __post_init__(self) -> None:
        if self.format is None:
            return
        if self.format == "":
            raise ValueError(
                f"FieldRequirement(name={self.name!r}): format may not be "
                f"an empty string (use None to disable format validation)."
            )
        from clauditor.formats import FORMAT_REGISTRY
        if self.format not in FORMAT_REGISTRY:
            raise ValueError(
                f"FieldRequirement(name={self.name!r}): format "
                f"{self.format!r} is not a registered format name. "
                f"Valid formats: {sorted(FORMAT_REGISTRY)}. "
                f"For custom patterns, use an L1 'regex' assertion "
                f"instead (see #99)."
            )


@dataclass
class TierRequirement:
    """A tier within a section, grouping fields with a label and threshold."""

    label: str
    description: str = ""
    min_entries: int = 0
    max_entries: int | None = None
    fields: list[FieldRequirement] = field(default_factory=list)


@dataclass
class SectionRequirement:
    """A required section in the output (e.g., 'Venues', 'Events')."""

    name: str
    tiers: list[TierRequirement] = field(default_factory=list)


@dataclass
class TriggerTests:
    """Test queries for trigger precision testing."""

    should_trigger: list[str] = field(default_factory=list)
    should_not_trigger: list[str] = field(default_factory=list)


@dataclass
class GradeThresholds:
    """Thresholds for pass/fail determination in quality grading."""

    min_pass_rate: float = 0.7
    min_mean_score: float = 0.5


@dataclass
class VarianceConfig:
    """Configuration for variance measurement."""

    n_runs: int = 5
    min_stability: float = 0.8


def criterion_text(entry: object) -> str:
    """Return the human-readable text of a grading criterion.

    ``EvalSpec.grading_criteria`` tolerates both plain strings (for in-memory
    test fixtures) and the canonical ``{"id": ..., "criterion": ...}`` dict
    loaded from disk (DEC-001 / #25). Consumers go through this helper so
    either shape works transparently.
    """
    if isinstance(entry, dict):
        return str(entry.get("criterion", ""))
    return str(entry)


def _resolve_field_format(field_dict: dict) -> str | None:
    """Resolve the ``format`` value for a field entry during spec load."""
    if "pattern" in field_dict:
        raise ValueError(
            f"Field {field_dict.get('name')!r}: use 'format', not 'pattern'"
        )
    return field_dict.get("format")


@dataclass
class EvalSpec:
    """Complete evaluation specification for a skill.

    Loaded from an eval.json file alongside the skill's .md file.
    """

    skill_name: str
    description: str = ""
    test_args: str = ""  # Pre-filled args to skip interactive Q&A
    # Natural-language user-query context handed to the blind A/B judge
    # (see `blind_compare_from_spec`). Distinct from `test_args`, which is
    # the skill-runner CLI arg string. Optional at load time, but required
    # by the blind-compare helper when that code path is used.
    user_prompt: str | None = None
    system_prompt: str | None = None
    input_files: list[str] = field(default_factory=list)  # Resolved absolute paths
    assertions: list[dict] = field(default_factory=list)  # Layer 1 checks
    sections: list[SectionRequirement] = field(default_factory=list)  # Layer 2 schema
    # Layer 3 rubric. Each entry is either a plain string (for ergonomic
    # in-memory construction in tests) or a dict ``{"id": str, "criterion":
    # str}`` when loaded via ``from_file`` per DEC-001 (#25). Consumers must
    # normalize via ``criterion_text()``.
    grading_criteria: list = field(default_factory=list)
    # DEC-004a of #146 (US-003 scope): nullable migration. Field type
    # promoted from ``str`` to ``str | None`` so #145-vintage specs
    # that emit ``"grading_model": null`` round-trip cleanly. The
    # dataclass default is preserved (``"claude-sonnet-4-6"``) — the
    # default-flip from a hardcoded string to ``None`` (so the
    # provider-aware ``_resolve_grading_model`` helper from US-001
    # can pick a sensible per-provider default) is deferred until a
    # follow-up story normalizes the ~10 production call sites that
    # read ``eval_spec.grading_model`` directly via patterns like
    # ``args.model or spec.eval_spec.grading_model``. ``_validate_provider_model``
    # in ``quality_grader.py`` continues to guard the live runtime
    # invariants. Bool guard at load time per
    # ``.claude/rules/constant-with-type-info.md``.
    grading_model: str | None = "claude-sonnet-4-6"
    output_file: str | None = None  # Single output file path
    output_files: list[str] = field(default_factory=list)  # Multiple file paths/globs
    trigger_tests: TriggerTests | None = None
    variance: VarianceConfig | None = None
    grade_thresholds: GradeThresholds | None = None
    # DEC-005: escape hatch for the interactive-hang heuristic. Default
    # is ``True`` so every pre-existing eval.json keeps the detector on.
    # Set to ``False`` in an eval spec to opt a specific skill out when
    # the heuristic consistently mis-classifies its output.
    allow_hang_heuristic: bool = True
    # DEC-002 / DEC-003 / DEC-008 / DEC-014 of #64: optional per-spec
    # runner timeout (seconds). ``None`` means "unset" — the runner
    # falls back to the CLI override if present, else to its own
    # ``self.timeout`` default (300s). Positive int only; bool is
    # explicitly rejected at load time per
    # ``.claude/rules/constant-with-type-info.md``.
    timeout: int | None = None
    # DEC-012 / DEC-017 of #86: per-spec transport selector for the
    # Anthropic call. One of ``"api"``, ``"cli"``, ``"auto"``. The
    # default ``"auto"`` preserves DEC-001's subscription-first
    # behavior (picks CLI when the ``claude`` binary is on PATH).
    # Four-layer precedence per ``.claude/rules/spec-cli-precedence.md``:
    # CLI ``--transport`` > ``CLAUDITOR_TRANSPORT`` env > this field >
    # default. Validated at load time against the literal set; non-
    # string / bool values rejected per
    # ``.claude/rules/constant-with-type-info.md``.
    transport: str = "auto"
    # Tier 1.5 of GitHub #103: when ``True``, clauditor sets
    # ``CLAUDE_CODE_DISABLE_BACKGROUND_TASKS=1`` in the skill
    # subprocess env, forcing ``Task(run_in_background=true)`` calls
    # synchronous. Resolves the output-truncation gap for the
    # parallel-fanout case without modifying the skill; does NOT
    # substitute for async-fidelity evaluation (see
    # ``docs/adr/transport-research-103.md`` for the tradeoff). Three-
    # level precedence per ``.claude/rules/spec-cli-precedence.md``:
    # CLI ``--sync-tasks`` > this field > default ``False``. Bool-
    # guarded at load time per
    # ``.claude/rules/constant-with-type-info.md``.
    sync_tasks: bool = False
    # DEC-001 of #146 (split into a/b — see Refinement Log): per-spec
    # grading provider selector. US-002 lands DEC-001a — accept the
    # new ``"auto"`` literal value alongside the existing
    # ``"anthropic"`` and ``"openai"``, while keeping the dataclass
    # default at ``None``. Default flip from ``None`` → ``"auto"``
    # is deferred to a follow-up story after US-005 (CLI seam) and
    # US-006 (orchestrator normalization) eliminate the call sites
    # that rely on the falsy-``None`` ``or "anthropic"`` short-circuit.
    # ``"auto"`` will be the subscription-first resolution token (the
    # ``_resolve_grading_provider`` helper from US-001 / US-004 infers
    # Anthropic vs OpenAI from the resolved ``grading_model`` prefix).
    # ``"anthropic"`` and ``"openai"`` pin a specific backend.
    # Validated at load time against the literal set; non-string /
    # bool / unknown-string values rejected per
    # ``.claude/rules/constant-with-type-info.md``. The full
    # four-layer precedence resolver (CLI ``--grading-provider`` >
    # ``CLAUDITOR_GRADING_PROVIDER`` env > this field > default
    # ``"auto"``) lives in US-004 of #146.
    grading_provider: Literal["anthropic", "openai", "auto"] | None = None

    @classmethod
    def from_file(cls, path: str | Path) -> EvalSpec:
        """Load an eval spec from a JSON file.

        Thin wrapper around :meth:`from_dict`: opens the file, decodes JSON,
        and delegates validation/construction to ``from_dict``. The file's
        parent directory is passed as ``spec_dir`` so that ``input_files``
        path resolution (strict containment relative to the spec dir)
        matches the previous behavior.
        """
        path = Path(path)
        with path.open() as f:
            data = json.load(f)
        # Preserve the prior behavior where a missing ``skill_name`` in the
        # JSON defaults to the file stem. Injected via a new dict so the
        # caller's data is not mutated (non-mutating rule applies to the
        # input they own on disk, but defensive here too).
        if isinstance(data, dict) and "skill_name" not in data:
            data = {"skill_name": path.stem, **data}
        return cls.from_dict(data, spec_dir=path.parent.resolve())

    @classmethod
    def from_dict(cls, data: dict, spec_dir: Path) -> EvalSpec:
        """Construct an :class:`EvalSpec` from an in-memory dict.

        ``spec_dir`` is used for ``input_files`` path resolution (strict
        containment, no absolute paths, no traversal out of ``spec_dir``).
        All validation currently performed by :meth:`from_file` lives here;
        ``from_file`` is a thin loader wrapper.

        Raises ``ValueError`` on any structural problem in ``data`` — see
        the ``from_file`` test suite for the full error matrix.
        """
        # Top-level shape guard: a JSON file whose top value is a list,
        # scalar, or null would otherwise crash with AttributeError on
        # the first `.get()` call below (review #53).
        if not isinstance(data, dict):
            raise ValueError(
                "EvalSpec: top-level JSON value must be an object, "
                f"got {type(data).__name__}"
            )
        skill_name = data.get("skill_name", "")
        # Path resolution split (intentional): `input_files` are pre-existing
        # static assets and resolve HERE at load time, relative to
        # ``spec_dir``, with strict source-containment. `output_files` are
        # runtime artifacts and resolve at run time against the runner's
        # effective CWD (staging dir when inputs are declared, else
        # project_dir) — see `spec.py` `_collect_outputs` / `effective_cwd`.
        # Any new path-bearing field must pick a side of this split.
        raw_input_files = data.get("input_files", [])
        resolved_input_files: list[str] = []
        input_basenames: list[str] = []
        for i, entry in enumerate(raw_input_files):
            if not isinstance(entry, str) or entry == "":
                raise ValueError(
                    f"EvalSpec(skill_name={skill_name!r}): "
                    f"input_files[{i}]={entry!r} — must be a non-empty string"
                )
            if Path(entry).is_absolute():
                raise ValueError(
                    f"EvalSpec(skill_name={skill_name!r}): "
                    f"input_files[{i}]={entry!r} — absolute paths not allowed"
                )
            try:
                candidate = (spec_dir / entry).resolve(strict=True)
            except FileNotFoundError as e:
                raise ValueError(
                    f"EvalSpec(skill_name={skill_name!r}): "
                    f"input_files[{i}]={entry!r} — file not found under {spec_dir}"
                ) from e
            if not candidate.is_relative_to(spec_dir):
                raise ValueError(
                    f"EvalSpec(skill_name={skill_name!r}): "
                    f"input_files[{i}]={entry!r} — escapes spec directory"
                )
            if not candidate.is_file():
                raise ValueError(
                    f"EvalSpec(skill_name={skill_name!r}): "
                    f"input_files[{i}]={entry!r} — not a regular file"
                )
            resolved_input_files.append(str(candidate))
            input_basenames.append(candidate.name)
        for i, name_i in enumerate(input_basenames):
            for j in range(i + 1, len(input_basenames)):
                if input_basenames[j] == name_i:
                    raise ValueError(
                        f"EvalSpec(skill_name={skill_name!r}): "
                        f"input_files entries {i} and {j} share destination "
                        f"basename {name_i!r}"
                    )

        raw_output_files = data.get("output_files", [])
        input_basename_set = set(input_basenames)
        for pat in raw_output_files:
            pat_name = Path(pat).name
            if pat_name in input_basename_set:
                raise ValueError(
                    f"EvalSpec(skill_name={skill_name!r}): "
                    f"output_files pattern {pat!r} collides with "
                    f"input_files basename {pat_name!r}"
                )

        # DEC-001 / #25: every L1 assertion, L2 field, and L3 criterion must
        # carry a stable string ``id`` that is unique within the skill. These
        # ids are the load-bearing key for the assertion-auditor's per-result
        # persistence (US-002/003) — position-based matching would break
        # history on any spec edit.
        seen_ids: set[str] = set()

        def _require_id(entry: object, ctx: str) -> str:
            if not isinstance(entry, dict):
                raise ValueError(
                    f"EvalSpec(skill_name={skill_name!r}): {ctx} — "
                    f"expected object, got {type(entry).__name__}"
                )
            if "id" not in entry:
                raise ValueError(
                    f"EvalSpec(skill_name={skill_name!r}): {ctx}: missing 'id'"
                )
            raw = entry["id"]
            if not isinstance(raw, str) or raw == "":
                raise ValueError(
                    f"EvalSpec(skill_name={skill_name!r}): {ctx}: "
                    f"'id' must be a non-empty string, got {raw!r}"
                )
            if raw in seen_ids:
                raise ValueError(
                    f"EvalSpec(skill_name={skill_name!r}): {ctx}: "
                    f"duplicate id {raw!r}"
                )
            seen_ids.add(raw)
            return raw

        def _require_assertion_keys(entry: dict, ctx: str) -> None:
            """Hard-validate per-assertion required and allowed keys.

            DEC-001 / DEC-002 / DEC-008 of #61 and DEC-001 / DEC-009 /
            DEC-012 of #67: every assertion dict must carry a known
            ``type`` value and exactly the keys named by
            :data:`ASSERTION_TYPE_REQUIRED_KEYS` for that type (plus
            the always-allowed ``id``, ``type``, ``name`` metadata
            keys). Each payload key is additionally type-checked
            against ``spec.field_types`` so string-typed ints
            (``{"length": "500"}``) reject at load time. Unknown
            keys, missing required keys, and wrong-typed keys all
            raise ``ValueError`` — strict rejection per
            ``.claude/rules/pre-llm-contract-hard-validate.md``.
            Unknown-key errors consult
            :data:`_ASSERTION_DRIFT_HINTS` for a per-type
            ``"did you mean X?"`` hint so hand-authors get a quick
            migration nudge when renaming legacy aliases
            (``value``, ``min``, ``max``, ``threshold``,
            ``minimum``; ``pattern`` is a drift alias on
            ``contains`` / ``not_contains`` only — it is a VALID
            key for ``regex`` and ``min_count``).

            Error-path order: (a) unknown/missing type →
            (b) unknown key → (c) missing required → (d) wrong
            type. Unknown-key fires before missing-required so a
            user who wrote an old alias (``value`` on ``contains``)
            gets the actionable ``"did you mean 'needle'?"`` hint
            instead of the opaque ``"missing required key 'needle'"``
            that would hide the rename from them. Each branch
            raises at the first violation; no cascading noise.
            """
            # (a) Unknown or missing ``type``.
            type_val = entry.get("type")
            if (
                not isinstance(type_val, str)
                or type_val not in ASSERTION_TYPE_REQUIRED_KEYS
            ):
                raise ValueError(
                    f"EvalSpec(skill_name={skill_name!r}): {ctx}: "
                    f"unknown or missing 'type' (got {type_val!r})"
                )
            spec = ASSERTION_TYPE_REQUIRED_KEYS[type_val]
            # (b) Unknown keys — fires before missing-required so
            # legacy-alias migrations surface the drift hint that
            # names the correct replacement key.
            allowed = (
                {"id", "type", "name"}
                | set(spec.required)
                | set(spec.optional)
            )
            for key in entry:
                if key in allowed:
                    continue
                suggestion = _ASSERTION_DRIFT_HINTS.get(type_val, {}).get(key)
                hint = (
                    f" — did you mean {suggestion!r}?"
                    if suggestion is not None
                    else ""
                )
                raise ValueError(
                    f"EvalSpec(skill_name={skill_name!r}): {ctx} "
                    f"(type={type_val!r}): unknown key {key!r}{hint}"
                )
            # (c) Missing required keys.
            for key in sorted(spec.required):
                if key not in entry or entry[key] is None:
                    raise ValueError(
                        f"EvalSpec(skill_name={skill_name!r}): {ctx} "
                        f"(type={type_val!r}): missing required key {key!r}"
                    )
            # (d) Wrong-typed payload keys. Check every declared
            # ``field_types`` entry present in the user's dict.
            # Two subtleties:
            #   1. A present-but-``None`` optional key is rejected
            #      — ``a.get("count", 1)`` does NOT substitute for
            #      ``None`` at runtime, so accepting it at load
            #      time would crash the handler downstream. The
            #      ``None`` branch produces a friendlier error
            #      ("must be int, not null …") than the generic
            #      ``isinstance`` miss would.
            #   2. ``bool`` is a subclass of ``int`` in Python, so
            #      a raw ``isinstance`` check would silently
            #      accept ``{"count": True}``. The int branch
            #      guards with ``not isinstance(val, bool)``.
            for key, expected in spec.field_types.items():
                if key not in entry:
                    continue
                val = entry[key]
                if val is None:
                    raise ValueError(
                        f"EvalSpec(skill_name={skill_name!r}): {ctx} "
                        f"(type={type_val!r}): key {key!r} must be "
                        f"{expected.__name__}, not null (omit the "
                        f"key to use the default)"
                    )
                ok = isinstance(val, expected) and not (
                    expected is int and isinstance(val, bool)
                )
                if not ok:
                    raise ValueError(
                        f"EvalSpec(skill_name={skill_name!r}): {ctx} "
                        f"(type={type_val!r}): key {key!r} must be "
                        f"{expected.__name__}, got "
                        f"{type(val).__name__} {val!r}"
                    )
            # (e) ``has_format.format`` must be a known registry key
            # (#99). Catches doomed-at-runtime regex values that
            # would otherwise fail late with "Unknown format: <regex>"
            # after the skill already spent tokens producing output.
            # Mirrors the FieldRequirement.__post_init__ contract so
            # L1 and L2 share one contract.
            if type_val == "has_format":
                from clauditor.formats import FORMAT_REGISTRY

                fmt_val = entry.get("format")
                if (
                    isinstance(fmt_val, str)
                    and fmt_val not in FORMAT_REGISTRY
                ):
                    raise ValueError(
                        f"EvalSpec(skill_name={skill_name!r}): {ctx} "
                        f"(type='has_format'): format {fmt_val!r} is "
                        f"not a registered format name. Valid "
                        f"formats: {sorted(FORMAT_REGISTRY)}. For "
                        f"custom patterns, use an L1 'regex' "
                        f"assertion instead (see #99)."
                    )

        raw_assertions = data.get("assertions", [])
        if not isinstance(raw_assertions, list):
            raise ValueError(
                f"EvalSpec(skill_name={skill_name!r}): 'assertions' "
                f"must be a list, got {type(raw_assertions).__name__}"
            )
        for i, a in enumerate(raw_assertions):
            _require_id(a, f"assertions[{i}]")
            _require_assertion_keys(a, f"assertions[{i}]")

        sections = []
        for si, s in enumerate(data.get("sections", [])):
            if "tiers" in s:
                # New tiered format
                tiers = []
                for ti, t in enumerate(s["tiers"]):
                    tier_fields = []
                    for fi, f in enumerate(t.get("fields", [])):
                        fid = _require_id(
                            f,
                            f"sections[{si}].tiers[{ti}].fields[{fi}]",
                        )
                        tier_fields.append(
                            FieldRequirement(
                                name=f["name"],
                                required=f.get("required", True),
                                format=_resolve_field_format(f),
                                id=fid,
                            )
                        )
                    tiers.append(
                        TierRequirement(
                            label=t["label"],
                            description=t.get("description", ""),
                            min_entries=t.get("min_entries", 0),
                            max_entries=t.get("max_entries"),
                            fields=tier_fields,
                        )
                    )
            elif "fields" in s:
                raise ValueError(
                    f"EvalSpec(skill_name={skill_name!r}): "
                    f"sections[{si}] has flat 'fields' without 'tiers' — "
                    "wrap fields inside a tiers[] entry"
                )
            else:
                raise ValueError(
                    f"EvalSpec(skill_name={skill_name!r}): "
                    f"sections[{si}] is missing 'tiers'"
                )
            sections.append(
                SectionRequirement(
                    name=s["name"],
                    tiers=tiers,
                )
            )

        raw_criteria = data.get("grading_criteria", [])
        if not isinstance(raw_criteria, list):
            raise ValueError(
                f"EvalSpec(skill_name={skill_name!r}): "
                f"'grading_criteria' must be a list, got "
                f"{type(raw_criteria).__name__}"
            )
        for i, c in enumerate(raw_criteria):
            _require_id(c, f"grading_criteria[{i}]")
            crit = c.get("criterion")
            if not isinstance(crit, str) or crit == "":
                raise ValueError(
                    f"EvalSpec(skill_name={skill_name!r}): "
                    f"grading_criteria[{i}]: 'criterion' must be a non-empty string"
                )

        user_prompt = data.get("user_prompt")
        if user_prompt is not None:
            if not isinstance(user_prompt, str) or not user_prompt.strip():
                raise ValueError(
                    f"EvalSpec(skill_name={skill_name!r}): user_prompt "
                    f"must be a non-empty, non-whitespace string, "
                    f"got {user_prompt!r}"
                )

        system_prompt = data.get("system_prompt")
        if system_prompt is not None:
            if not isinstance(system_prompt, str) or not system_prompt.strip():
                raise ValueError(
                    f"EvalSpec(skill_name={skill_name!r}): system_prompt "
                    f"must be a non-empty, non-whitespace string, "
                    f"got {system_prompt!r}"
                )

        # DEC-005: optional per-eval escape hatch for the
        # interactive-hang heuristic. Absent → default True (back-compat).
        # Present → must be a real bool (reject "false", 0, None, etc.)
        # — this is a load-bearing behavioral switch, not a truthy flag.
        allow_hang_heuristic: bool = True
        if "allow_hang_heuristic" in data:
            raw_flag = data["allow_hang_heuristic"]
            if not isinstance(raw_flag, bool):
                raise ValueError(
                    f"EvalSpec(skill_name={skill_name!r}): "
                    "allow_hang_heuristic must be a bool (true or false)"
                )
            allow_hang_heuristic = raw_flag

        # DEC-002 / DEC-003 / DEC-008 / DEC-014 of #64: optional
        # per-spec runner timeout override. ``None`` / missing →
        # "unset" (runner falls back). Must be a positive int;
        # ``bool`` is an ``int`` subclass in Python, so the check
        # is ``isinstance(val, int) and not isinstance(val, bool)``
        # per ``.claude/rules/constant-with-type-info.md``.
        timeout: int | None = None
        if "timeout" in data and data["timeout"] is not None:
            raw_timeout = data["timeout"]
            if not isinstance(raw_timeout, int) or isinstance(
                raw_timeout, bool
            ):
                raise ValueError(
                    f"EvalSpec(skill_name={skill_name!r}): "
                    f"'timeout' must be an int, got "
                    f"{type(raw_timeout).__name__} {raw_timeout!r}"
                )
            if raw_timeout <= 0:
                raise ValueError(
                    f"EvalSpec(skill_name={skill_name!r}): "
                    f"'timeout' must be > 0, got {raw_timeout}"
                )
            timeout = raw_timeout

        # DEC-012 of #86: optional per-spec transport selector.
        # Missing → default ``"auto"`` (back-compat). Must be a
        # non-bool string in the literal set ``{"api", "cli", "auto"}``.
        # Explicit ``null`` is rejected (use omission for default).
        # Bool guard first per ``.claude/rules/constant-with-type-info.md``.
        transport: str = "auto"
        if "transport" in data:
            raw_transport = data["transport"]
            if raw_transport is None:
                raise ValueError(
                    f"EvalSpec(skill_name={skill_name!r}): "
                    "'transport' must be one of "
                    "'api', 'cli', 'auto', got null "
                    "(omit the key to use the default 'auto')"
                )
            if isinstance(raw_transport, bool) or not isinstance(
                raw_transport, str
            ):
                raise ValueError(
                    f"EvalSpec(skill_name={skill_name!r}): "
                    f"'transport' must be a string, got "
                    f"{type(raw_transport).__name__} {raw_transport!r}"
                )
            if raw_transport not in ("api", "cli", "auto"):
                raise ValueError(
                    f"EvalSpec(skill_name={skill_name!r}): "
                    f"'transport' must be one of 'api', 'cli', 'auto', "
                    f"got {raw_transport!r}"
                )
            transport = raw_transport

        # Tier 1.5 of GitHub #103: optional per-spec sync-tasks
        # opt-in. Missing → default ``False``. Must be a real bool
        # per ``.claude/rules/constant-with-type-info.md`` — a
        # truthy string or int is rejected explicitly so an author
        # who typos ``"sync_tasks": "true"`` sees a crisp error
        # rather than silent truthy-coercion to ``True``.
        sync_tasks: bool = False
        if "sync_tasks" in data:
            raw_sync_tasks = data["sync_tasks"]
            if not isinstance(raw_sync_tasks, bool):
                raise ValueError(
                    f"EvalSpec(skill_name={skill_name!r}): "
                    "'sync_tasks' must be a bool (true or false), got "
                    f"{type(raw_sync_tasks).__name__} "
                    f"{raw_sync_tasks!r}"
                )
            sync_tasks = raw_sync_tasks

        # DEC-001a of #146 (US-002 scope): per-spec grading provider
        # selector. Adds ``"auto"`` to the accepted literal set
        # alongside ``"anthropic"`` and ``"openai"``; missing or
        # explicit ``null`` continues to round-trip as ``None`` per
        # #145's behavior. The default-flip from ``None`` to
        # ``"auto"`` is deferred until US-005 / US-006 normalize the
        # downstream call sites that currently rely on the falsy-
        # ``None`` ``or "anthropic"`` short-circuit. Bool guard first
        # per ``.claude/rules/constant-with-type-info.md``.
        grading_provider: Literal["anthropic", "openai", "auto"] | None = None
        if "grading_provider" in data:
            raw_grading_provider = data["grading_provider"]
            if raw_grading_provider is None:
                grading_provider = None
            elif (
                isinstance(raw_grading_provider, bool)
                or not isinstance(raw_grading_provider, str)
                or raw_grading_provider
                not in ("anthropic", "openai", "auto")
            ):
                raise ValueError(
                    f"EvalSpec(skill_name={skill_name!r}): "
                    "'grading_provider' must be one of 'anthropic', "
                    f"'openai', 'auto' (or null), got "
                    f"{type(raw_grading_provider).__name__} "
                    f"{raw_grading_provider!r}"
                )
            else:
                grading_provider = raw_grading_provider

        trigger_tests = None
        if "trigger_tests" in data:
            tt = data["trigger_tests"]
            trigger_tests = TriggerTests(
                should_trigger=tt.get("should_trigger", []),
                should_not_trigger=tt.get("should_not_trigger", []),
            )

        variance = None
        if "variance" in data:
            v = data["variance"]
            variance = VarianceConfig(
                n_runs=v.get("n_runs", 5),
                min_stability=v.get("min_stability", 0.8),
            )

        grade_thresholds = None
        if "grade_thresholds" in data:
            gt = data["grade_thresholds"]
            grade_thresholds = GradeThresholds(
                min_pass_rate=gt.get("min_pass_rate", 0.7),
                min_mean_score=gt.get("min_mean_score", 0.5),
            )

        # DEC-004a of #146 (US-003 scope): nullable migration for
        # ``grading_model``. Default preserved (``"claude-sonnet-4-6"``);
        # explicit ``null`` accepted and round-trips as ``None`` so
        # #145-vintage specs that emit ``"grading_model": null`` keep
        # loading cleanly. Bool guard first per
        # ``.claude/rules/constant-with-type-info.md``.
        grading_model: str | None = "claude-sonnet-4-6"
        if "grading_model" in data:
            raw_grading_model = data["grading_model"]
            if raw_grading_model is None:
                grading_model = None
            elif isinstance(raw_grading_model, bool) or not isinstance(
                raw_grading_model, str
            ):
                raise ValueError(
                    f"EvalSpec(skill_name={skill_name!r}): "
                    "'grading_model' must be a string or null, got "
                    f"{type(raw_grading_model).__name__} "
                    f"{raw_grading_model!r}"
                )
            else:
                grading_model = raw_grading_model

        return cls(
            skill_name=skill_name,
            description=data.get("description", ""),
            test_args=data.get("test_args", ""),
            user_prompt=user_prompt,
            system_prompt=system_prompt,
            input_files=resolved_input_files,
            assertions=data.get("assertions", []),
            sections=sections,
            grading_criteria=data.get("grading_criteria", []),
            grading_model=grading_model,
            output_file=data.get("output_file"),
            output_files=data.get("output_files", []),
            trigger_tests=trigger_tests,
            variance=variance,
            grade_thresholds=grade_thresholds,
            allow_hang_heuristic=allow_hang_heuristic,
            timeout=timeout,
            transport=transport,
            sync_tasks=sync_tasks,
            grading_provider=grading_provider,
        )

    def to_dict(self) -> dict:
        """Serialize to a dict (for JSON output)."""
        result: dict = {
            "skill_name": self.skill_name,
            "description": self.description,
            "test_args": self.test_args,
            **(
                {"user_prompt": self.user_prompt}
                if self.user_prompt is not None
                else {}
            ),
            **(
                {"system_prompt": self.system_prompt}
                if self.system_prompt is not None
                else {}
            ),
            "input_files": self.input_files,
            "assertions": self.assertions,
            "sections": [
                {
                    "name": s.name,
                    "tiers": [
                        {
                            "label": t.label,
                            **(
                                {"description": t.description}
                                if t.description
                                else {}
                            ),
                            "min_entries": t.min_entries,
                            **(
                                {"max_entries": t.max_entries}
                                if t.max_entries is not None
                                else {}
                            ),
                            "fields": [
                                {
                                    **({"id": f.id} if f.id else {}),
                                    "name": f.name,
                                    "required": f.required,
                                    **(
                                        {"format": f.format}
                                        if f.format
                                        else {}
                                    ),
                                }
                                for f in t.fields
                            ],
                        }
                        for t in s.tiers
                    ],
                }
                for s in self.sections
            ],
            "grading_criteria": self.grading_criteria,
            # DEC-004a of #146 (US-003 scope): nullable migration. Field
            # is now ``str | None``; default ``"claude-sonnet-4-6"``
            # preserved. Emit unconditionally (including when ``None``,
            # serialized as JSON ``null``) so explicit-null specs round-
            # trip cleanly through the new from_dict accept-``None``
            # branch and the existing default keeps emitting verbatim.
            "grading_model": self.grading_model,
        }
        if not self.allow_hang_heuristic:
            # Emit only on non-default to keep diffs minimal; omission
            # at load time means "default True" per from_dict.
            result["allow_hang_heuristic"] = False
        if self.transport != "auto":
            # DEC-012 of #86: emit only on non-default. Omission at
            # load time means default "auto" per from_dict.
            result["transport"] = self.transport
        if self.sync_tasks:
            # Tier 1.5 of GitHub #103: emit only on non-default.
            # Omission at load time means default ``False``.
            result["sync_tasks"] = True
        # DEC-001a of #146: default still ``None`` (default-flip to
        # ``"auto"`` deferred until US-005 / US-006 normalize call
        # sites). Emit only on non-default, matching #145 behavior.
        if self.grading_provider is not None:
            result["grading_provider"] = self.grading_provider
        if self.output_file is not None:
            result["output_file"] = self.output_file
        if self.output_files:
            result["output_files"] = self.output_files
        if self.trigger_tests is not None:
            result["trigger_tests"] = {
                "should_trigger": self.trigger_tests.should_trigger,
                "should_not_trigger": self.trigger_tests.should_not_trigger,
            }
        if self.variance is not None:
            result["variance"] = {
                "n_runs": self.variance.n_runs,
                "min_stability": self.variance.min_stability,
            }
        if self.grade_thresholds is not None:
            result["grade_thresholds"] = {
                "min_pass_rate": self.grade_thresholds.min_pass_rate,
                "min_mean_score": self.grade_thresholds.min_mean_score,
            }
        return result
