"""``clauditor badge`` — generate a shields.io endpoint JSON from sidecars.

Thin CLI I/O layer wrapping the pure compute in :mod:`clauditor.badge`
(see ``.claude/rules/pure-compute-vs-io-split.md``). This module owns
the argparse surface, sidecar-file reads, git subprocess calls (via
:mod:`clauditor._git`), output writes, stderr progress/warning lines,
and the DEC-025 exit-code mapping. The pure module owns badge
aggregation, color classification, message formatting, and Markdown
image URL building.

Exit codes (DEC-025 — non-LLM, 0/1/2 taxonomy per
``.claude/rules/llm-cli-exit-code-taxonomy.md`` "does not apply"
clause):

- ``0`` — success: badge JSON written (including DEC-001 / DEC-007
  lightgrey placeholder writes) or Markdown image line printed via
  ``--url-only``.
- ``1`` — runtime failure: corrupt iteration (DEC-008 — iteration
  exists but ``assertions.json`` is missing), existing file without
  ``--force`` (DEC-011), explicit ``--from-iteration N`` not found
  (DEC-016), disk I/O error on write.
- ``2`` — input-validation failure: bad skill spec load (missing or
  unreadable SKILL.md), mutually exclusive flags (DEC-014), ``--output``
  parent-dir check failure (DEC-022), ``--style`` malformed or
  validation failure (DEC-015 / DEC-023).
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import sys
from pathlib import Path

from clauditor import _git
from clauditor.badge import (
    build_markdown_image,
    compute_badge,
    discover_iteration,
    load_iteration_sidecars,
)
from clauditor.cli import _positive_int
from clauditor.spec import SkillSpec

# ---------------------------------------------------------------------------
# --style key whitelist (DEC-015).
#
# The accepted shields.io endpoint-JSON passthrough keys. Unknown keys
# are NOT rejected — they warn to stderr and still land in the JSON
# (shields.io silently ignores what it doesn't know, so the badge
# still renders).
# ---------------------------------------------------------------------------

_ALLOWED_STYLE_KEYS: frozenset[str] = frozenset(
    {"style", "logoSvg", "logoColor", "labelColor", "cacheSeconds", "link"}
)

# Shields.io style keys whose values are typed as integers in the
# endpoint schema (review pass 3, C3-1). A string-typed value in
# these slots is not guaranteed to be honored by shields.io; the
# CLI coerces to ``int`` at serialization and rejects non-numeric
# input with exit 2.
_INT_STYLE_KEYS: frozenset[str] = frozenset({"cacheSeconds"})

# Keys that are canonical top-level fields in the badge JSON and
# MUST NOT be overwritten by ``--style`` passthroughs (Copilot PR
# review, 2026-04-22). ``Badge.to_endpoint_json`` emits the
# style_overrides dict AFTER the canonical fields, so a user passing
# ``--style schemaVersion=2`` or ``--style label=hijacked`` would
# silently clobber the shields.io-required fields and break the
# badge. Reject at the CLI parse boundary with exit 2.
_RESERVED_STYLE_KEYS: frozenset[str] = frozenset(
    {"schemaVersion", "label", "message", "color", "clauditor"}
)

# Upper bound on a ``--style`` value (DEC-023). 512 chars is generous
# for any reasonable shields.io field (even inline SVG data URLs stay
# well under that when they show up in practice).
_STYLE_VALUE_MAX_LEN: int = 512

# Characters that break Markdown ``![alt](url)`` syntax when interpolated
# into the alt-text slot. Rejected at the CLI layer so users get exit 2
# instead of a silently-broken badge line (review pass 1, B-3).
_LABEL_FORBIDDEN_CHARS: frozenset[str] = frozenset(
    {"[", "]", "(", ")", "\n", "\r"}
)


def add_parser(subparsers: argparse._SubParsersAction) -> None:
    """Register the ``badge`` subparser."""
    p = subparsers.add_parser(
        "badge",
        help=(
            "Generate a shields.io endpoint JSON from a skill's latest "
            "iteration sidecars"
        ),
    )
    p.add_argument(
        "skill",
        help="Path to a SKILL.md file",
    )
    p.add_argument(
        "--from-iteration",
        type=_positive_int,
        default=None,
        metavar="N",
        help=(
            "Read sidecars from iteration N instead of the latest "
            "(DEC-016 — missing N exits 1)"
        ),
    )
    p.add_argument(
        "--output",
        default=None,
        metavar="PATH",
        help=(
            "Write the badge JSON to PATH; mutually exclusive with "
            "--url-only (defaults to <project>/.clauditor/badges/"
            "<skill>.json)"
        ),
    )
    p.add_argument(
        "--url-only",
        action="store_true",
        help=(
            "Print the Markdown image line to stdout; do NOT write a "
            "badge JSON file"
        ),
    )
    p.add_argument(
        "--force",
        action="store_true",
        help=(
            "Overwrite the target badge JSON if it already exists "
            "(required by DEC-011)"
        ),
    )
    p.add_argument(
        "--repo",
        default=None,
        metavar="USER/REPO",
        help=(
            "Override the git-detected origin slug for --url-only "
            "(DEC-002 placeholder fallback)"
        ),
    )
    p.add_argument(
        "--branch",
        default=None,
        metavar="NAME",
        help=(
            "Override the git-detected default branch for --url-only "
            "(DEC-002 placeholder fallback)"
        ),
    )
    p.add_argument(
        "--label",
        default="clauditor",
        metavar="TEXT",
        help='Badge label text (default: "clauditor")',
    )
    p.add_argument(
        "--style",
        action="append",
        default=None,
        metavar="KEY=VALUE",
        help=(
            "Shields.io style passthrough; may be repeated. Whitelist: "
            "style, logoSvg, logoColor, labelColor, cacheSeconds, link "
            "(DEC-015). Unknown keys warn but still emit."
        ),
    )
    p.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help=(
            "On success, print a stderr info line naming the written "
            "path and iteration (DEC-018)"
        ),
    )


# ---------------------------------------------------------------------------
# CLI-local helpers.
# ---------------------------------------------------------------------------


def _parse_style_arg(raw: str) -> tuple[str, str]:
    """Split a ``KEY=VALUE`` ``--style`` entry into its two halves.

    Raises :class:`ValueError` when the input does not contain exactly
    one ``=`` separator (either missing entirely, or the input is
    empty). Trailing-only-separator inputs like ``"key="`` parse as
    ``("key", "")`` — a legitimate "clear the value" shape for
    shields.io passthrough.
    """
    if "=" not in raw:
        raise ValueError(f"--style must be KEY=VALUE, got: {raw!r}")
    key, value = raw.split("=", 1)
    if not key:
        raise ValueError(f"--style must be KEY=VALUE, got: {raw!r}")
    return key, value


def _validate_style_value(key: str, value: str) -> None:
    """Reject a ``--style`` value per DEC-023.

    - Control characters (via ``str.isprintable()``) are rejected as a
      catch-all for ``\\x00-\\x1f`` and ``\\x7f``.
    - Values longer than :data:`_STYLE_VALUE_MAX_LEN` are rejected.

    Raises :class:`ValueError` with a message suitable for stderr
    surfacing when either check fails. Empty values are accepted
    (they are valid shields.io passthrough — "clear this field").
    """
    if value and not value.isprintable():
        raise ValueError(
            f"--style value for {key!r} is invalid: contains control "
            "characters"
        )
    if len(value) > _STYLE_VALUE_MAX_LEN:
        raise ValueError(
            f"--style value for {key!r} is invalid: length "
            f"{len(value)} exceeds max {_STYLE_VALUE_MAX_LEN}"
        )


def _validate_label(label: str) -> None:
    """Reject label values that would break Markdown ``![label](...)`` syntax.

    Raises :class:`ValueError` for:

    - ``[``, ``]``, ``(``, ``)``, or newline characters: these are
      structural Markdown chars that, if interpolated verbatim into
      the alt-text slot, close it early or break the URL portion.
    - Empty or whitespace-only labels: ``![]`` renders accessibility-
      hostile empty alt-text (review pass 3, N3-2). Non-default labels
      are the common case for distinguishing skills in a catalog, so
      an explicit rejection is cheaper than a silent degradation.
    - Values longer than :data:`_STYLE_VALUE_MAX_LEN` — same cap as
      ``--style`` passthrough values for belt-and-suspenders.
    """
    if not label.strip():
        raise ValueError("--label must not be empty or whitespace-only")
    for ch in label:
        if ch in _LABEL_FORBIDDEN_CHARS:
            # Name the offending char + show a truncated preview so
            # the user can locate the problem quickly when the label
            # is pasted from elsewhere (review pass 2, C2-1).
            preview = label if len(label) <= 60 else label[:57] + "..."
            raise ValueError(
                f"--label contains forbidden char {ch!r} (Markdown "
                f"alt-text syntax — '[', ']', '(', ')', or newline): "
                f"{preview!r}"
            )
    if len(label) > _STYLE_VALUE_MAX_LEN:
        raise ValueError(
            f"--label is too long ({len(label)} chars, max "
            f"{_STYLE_VALUE_MAX_LEN})"
        )


def _now_iso_z() -> str:
    """Return the current UTC time in the DEC-012 ``Z``-suffix form.

    Python's ``datetime.now(timezone.utc).isoformat()`` produces
    ``+00:00``; we normalize to ``Z`` at this single seam so every
    badge JSON renders consistently.
    """
    raw = datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds")
    return raw.replace("+00:00", "Z")


def _list_available_iterations(project_dir: Path, skill_name: str) -> list[int]:
    """Return sorted list of iteration numbers containing ``skill_name/``.

    Used for DEC-016's "available iterations" stderr message when an
    explicit ``--from-iteration N`` lookup fails. Falls back to an
    empty list when ``.clauditor/`` is absent or contains no matching
    iteration dirs — the caller renders ``"none"`` in that case.
    """
    clauditor_dir = project_dir / ".clauditor"
    if not clauditor_dir.is_dir():
        return []
    found: list[int] = []
    for child in clauditor_dir.iterdir():
        if not child.is_dir() or not child.name.startswith("iteration-"):
            continue
        suffix = child.name[len("iteration-") :]
        try:
            n = int(suffix)
        except ValueError:
            continue
        if n < 1:
            continue  # iteration-0 / iteration--1 shapes don't count
        if (child / skill_name).is_dir():
            found.append(n)
    return sorted(found)


def _extension_path_for(target: Path) -> Path:
    """Return the ``<target>.clauditor.json`` sibling path.

    Uses :meth:`pathlib.Path.with_suffix` to replace the final
    extension so both ``demo.json`` and ``demo.svg`` (or a suffix-less
    path) all produce ``demo.clauditor.json`` in the same directory.
    """
    return target.with_suffix(".clauditor.json")


def _atomic_write_json(target: Path, payload: dict) -> None:
    """Write ``payload`` to ``target`` via tempfile + ``os.replace``.

    Raises ``OSError`` on failure with best-effort cleanup of the
    sibling tempfile; the existing ``target`` (if any) is left
    untouched in the failure case.

    ``ensure_ascii=False`` writes the UTF-8 middle-dot glyph in
    messages verbatim (review pass 3, C3-4) so the on-disk bytes
    match the sample JSON in ``docs/badges.md``.
    """
    tmp_target = target.with_name(f".{target.name}.tmp")
    try:
        tmp_target.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        os.replace(tmp_target, target)
    except OSError:
        try:
            tmp_target.unlink()
        except OSError:
            pass
        raise


def _write_badge_sidecars(
    target: Path,
    badge,
    *,
    force: bool,
    iteration: int | None,
    verbose: bool,
    post_write_warning: str | None = None,
) -> int:
    """Write the badge sidecar pair (shields.io + clauditor extension).

    The shields.io endpoint strictly validates its schema and rejects
    any unknown top-level key with an ``invalid properties: <key>``
    SVG response. The extension block therefore cannot be embedded;
    it lives in a sibling ``<target>.clauditor.json`` file. Readers:

    - ``<target>`` is what shields.io fetches. Minimal shape
      (``schemaVersion``, ``label``, ``message``, ``color``, plus
      any whitelisted ``--style`` passthroughs).
    - ``<target>.clauditor.json`` carries the per-layer breakdown,
      thresholds, iteration number, and ``generated_at`` timestamp
      for trend-audit / forensic consumers.

    DEC-011 overwrite policy applies to BOTH files as a set. Either
    target existing without ``--force`` fails the whole write.

    Atomic publication (review pass 2, C2-3): each file is written
    to a sibling tempfile, then ``os.replace`` publishes it. On
    partial failure (e.g. first succeeds, second fails), the tmp
    files are cleaned up and the error is surfaced — but the first
    file's replace may already have landed. This is a known
    non-atomicity across two files and is acceptable because badge
    artifacts are fully regenerable. Document the pair semantics in
    ``.claude/rules/dual-version-external-schema-embed.md``.
    """
    extension_target = _extension_path_for(target)

    if target.exists() and not force:
        print(
            f"ERROR: {target} already exists (pass --force to overwrite)",
            file=sys.stderr,
        )
        return 1
    if extension_target.exists() and not force:
        print(
            f"ERROR: {extension_target} already exists (pass --force to "
            "overwrite)",
            file=sys.stderr,
        )
        return 1

    # For non-default paths (passed via --output) the parent dir was
    # validated already (DEC-022). For the default path we create the
    # parent chain unconditionally; re-running after a pruned tree
    # should not error.
    target.parent.mkdir(parents=True, exist_ok=True)

    try:
        _atomic_write_json(target, badge.to_endpoint_json())
        _atomic_write_json(
            extension_target, badge.to_clauditor_extension_json()
        )
    except OSError as exc:
        print(
            f"ERROR: could not write badge sidecars: {exc}",
            file=sys.stderr,
        )
        return 1

    if post_write_warning is not None:
        print(post_write_warning, file=sys.stderr)

    if verbose:
        tail = (
            f"(iteration {iteration})"
            if iteration is not None
            else "(no iteration)"
        )
        print(
            f"clauditor.badge: wrote {target} + {extension_target.name} {tail}",
            file=sys.stderr,
        )
    return 0


def _resolve_url_only_slug_and_branch(
    args: argparse.Namespace,
    project_dir: Path,
) -> tuple[str, str]:
    """Resolve the ``--url-only`` repo slug and branch (DEC-002).

    Precedence:

    1. ``--repo USER/REPO`` and ``--branch NAME`` win when passed.
    2. Otherwise :func:`clauditor._git.get_repo_slug` and
       :func:`clauditor._git.get_default_branch` are invoked.
    3. Missing values fall back to ``"USER/REPO"`` / ``"main"``.

    When ANY auto-detect call returns ``None`` AND the caller did
    not pass the matching explicit override, emit the DEC-021 stderr
    warning naming the placeholder. The warning lists BOTH fallback
    values in one line so the user can paste a single ``--repo`` /
    ``--branch`` invocation to fix it.
    """
    repo_slug = args.repo
    branch = args.branch

    slug_auto_failed = False
    branch_auto_failed = False

    if repo_slug is None:
        detected_slug = _git.get_repo_slug(project_dir)
        if detected_slug is None:
            repo_slug = "USER/REPO"
            slug_auto_failed = True
        else:
            repo_slug = detected_slug

    if branch is None:
        detected_branch = _git.get_default_branch(project_dir)
        if detected_branch is None:
            branch = "main"
            branch_auto_failed = True
        else:
            branch = detected_branch

    # DEC-021: warn loudly when the user is relying on auto-detect
    # AND any detection fell through. We only warn when the slug
    # auto-detect failed; a branch auto-detect failure with a
    # successful slug still falls back to "main" (a reasonable
    # default on GitHub), so the warning stays quiet unless the
    # slug itself had to be replaced.
    if slug_auto_failed:
        # When BOTH auto-detects fell through, mention both flags so
        # the user has the complete remediation in one line (Copilot
        # PR review, 2026-04-22 — the prior message only named --repo
        # even when --branch was also replaced with the placeholder).
        override_hint = (
            "pass --repo USER/REPO --branch NAME to override"
            if branch_auto_failed
            else "pass --repo USER/REPO to override"
        )
        fallback_msg = (
            f"warning: git auto-detect failed; using placeholder "
            f"{repo_slug}/{branch} — {override_hint}"
        )
        print(fallback_msg, file=sys.stderr)
    elif branch_auto_failed:
        # Quietly defaulting to main when we DID detect a slug — but
        # surface it anyway so the user knows where the branch came
        # from when the remote happens to not use main.
        print(
            "warning: git default-branch auto-detect failed; using "
            "'main' — pass --branch NAME to override",
            file=sys.stderr,
        )

    return repo_slug, branch


# ---------------------------------------------------------------------------
# Public entry point.
# ---------------------------------------------------------------------------


def cmd_badge(args: argparse.Namespace) -> int:
    """Entry point for ``clauditor badge``.

    Follows the four-branch shape described in US-004 of
    ``plans/super/77-clauditor-badge.md``:

    1. No iteration found and ``--from-iteration`` NOT passed →
       DEC-001 lightgrey placeholder, exit 0 (respects ``--force``).
    2. Explicit ``--from-iteration N`` but N not found → DEC-016
       exit 1 with available-iterations list.
    3. Iteration found, ``assertions.json`` missing → DEC-008 corrupt
       iteration, exit 1.
    4. Iteration found, sidecars loaded → happy path; classify via
       :func:`clauditor.badge.compute_badge` and either write JSON
       or print the ``--url-only`` Markdown image.
    """
    # DEC-014 mutual exclusion — must run before SkillSpec load so a
    # misconfigured call does not pay the load cost.
    if args.url_only and args.output is not None:
        print(
            "ERROR: --url-only and --output are mutually exclusive",
            file=sys.stderr,
        )
        return 2

    # Label validation (review pass 1, B-3). Reject chars that break
    # Markdown ``![label](url)`` syntax up-front so a user typo does
    # not produce a silently-broken badge line.
    try:
        _validate_label(args.label)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    # Parse --style flags up-front (DEC-015 / DEC-023). A bad value
    # here blocks before any sidecar read / disk write.
    style_overrides: dict[str, str | int] = {}
    for raw in args.style or []:
        try:
            key, value = _parse_style_arg(raw)
        except ValueError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 2
        try:
            _validate_style_value(key, value)
        except ValueError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 2
        # Reserved-key collision guard: a passthrough key that
        # matches a canonical shields.io / clauditor top-level
        # field would silently clobber the real value inside
        # ``Badge.to_endpoint_json`` (Copilot PR review,
        # 2026-04-22).
        if key in _RESERVED_STYLE_KEYS:
            print(
                f"ERROR: --style {key}=... would overwrite the "
                f"canonical badge field {key!r}. Use --label for "
                f"the label text; other reserved fields "
                f"(schemaVersion, message, color, clauditor) are "
                f"not user-overridable.",
                file=sys.stderr,
            )
            return 2
        if key not in _ALLOWED_STYLE_KEYS:
            print(
                f"warning: clauditor.badge: unknown --style key {key!r} — "
                "passing through anyway",
                file=sys.stderr,
            )
        # Coerce integer-typed style keys (review pass 3, C3-1).
        # Shields.io's endpoint schema types ``cacheSeconds`` as int;
        # a string slot is not guaranteed to be honored.
        if key in _INT_STYLE_KEYS:
            try:
                style_overrides[key] = int(value)
            except ValueError:
                print(
                    f"ERROR: --style {key} must be an integer, got "
                    f"{value!r}",
                    file=sys.stderr,
                )
                return 2
        else:
            style_overrides[key] = value

    # Skill spec load. Any load error is a pre-call input error → 2.
    skill_path = Path(args.skill)
    if not skill_path.exists():
        print(
            f"ERROR: skill file not found: {skill_path}",
            file=sys.stderr,
        )
        return 2
    if not skill_path.is_file():
        print(
            f"ERROR: skill path is not a regular file: {skill_path}",
            file=sys.stderr,
        )
        return 2
    try:
        spec = SkillSpec.from_file(skill_path)
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        print(
            f"ERROR: could not load skill spec {skill_path}: {exc}",
            file=sys.stderr,
        )
        return 2

    skill_name = spec.skill_name
    project_dir = Path.cwd()

    # argparse ``type=_positive_int`` already rejected non-int / <=0
    # --from-iteration values with exit 2 before we got here (pass 1
    # review B-2 — move validation ahead of the SKILL.md load cost).
    explicit_iter: int | None = args.from_iteration

    # Resolve the output path target. DEC-005 accepts absolute paths;
    # DEC-022 validates parent-dir existence when the user supplied
    # --output. The default .clauditor/badges/<skill>.json is created
    # unconditionally via mkdir below.
    if args.output is not None:
        target_path = Path(args.output)
        parent = target_path.parent.resolve(strict=False)
        if not parent.is_dir():
            print(
                f"ERROR: --output parent directory does not exist: {parent}",
                file=sys.stderr,
            )
            return 2
    else:
        target_path = (
            project_dir / ".clauditor" / "badges" / f"{skill_name}.json"
        )

    # -----------------------------------------------------------------
    # Sidecar discovery + branch on the four code paths.
    # -----------------------------------------------------------------
    discovered = discover_iteration(
        project_dir=project_dir,
        skill_name=skill_name,
        explicit=explicit_iter,
    )

    if discovered is None and explicit_iter is None:
        # Code path 1 — DEC-001 lightgrey placeholder (exit 0 after
        # write).
        return _handle_no_iteration(
            target_path=target_path,
            skill_name=skill_name,
            args=args,
            style_overrides=style_overrides,
            project_dir=project_dir,
        )

    if discovered is None and explicit_iter is not None:
        # Code path 2 — DEC-016 explicit-missing iteration.
        available = _list_available_iterations(project_dir, skill_name)
        if available:
            rendered = ", ".join(str(n) for n in available)
        else:
            rendered = "none"
        print(
            f"ERROR: iteration {explicit_iter} not found for skill "
            f"{skill_name}. Available iterations with this skill: "
            f"{rendered}",
            file=sys.stderr,
        )
        return 1

    # From here, discovered is not None.
    assert discovered is not None
    iteration_n, iter_skill_dir = discovered

    sidecars = load_iteration_sidecars(iter_skill_dir)

    if sidecars.assertions_missing:
        # Code path 3 — DEC-008 corrupt iteration.
        print(
            f"ERROR: iteration {iteration_n} for skill {skill_name} "
            "is corrupt — assertions.json is missing. Re-run "
            "'clauditor validate' to regenerate.",
            file=sys.stderr,
        )
        return 1

    # Code path 4 — happy path. Short-circuit --url-only BEFORE
    # compute_badge (review pass 2, C2-4) — the Markdown image URL
    # does not depend on sidecar data, so computing a Badge we'll
    # discard is wasted work.
    if args.url_only:
        return _render_url_only(
            args=args,
            project_dir=project_dir,
            skill_name=skill_name,
        )

    badge = compute_badge(
        assertions=sidecars.assertions,
        grading=sidecars.grading,
        variance=sidecars.variance,
        skill_name=skill_name,
        iteration=iteration_n,
        generated_at=_now_iso_z(),
        label=args.label,
        style_overrides=style_overrides,
    )

    # DEC-021 sibling to DEC-001: if the badge came out lightgrey with
    # an iteration loaded, the L1 spec has zero assertions (DEC-007).
    # The warning is deferred to post-write so a collision-without-
    # --force exit 1 does not leave a false "wrote ..." trail on
    # stderr (review pass 2, N2-4).
    post_write_warning: str | None = None
    if badge.color == "lightgrey":
        post_write_warning = (
            "warning: eval spec declares 0 L1 assertions — wrote "
            "lightgrey 'no data' badge"
        )

    return _write_badge_sidecars(
        target_path,
        badge,
        force=args.force,
        iteration=iteration_n,
        verbose=args.verbose,
        post_write_warning=post_write_warning,
    )


def _handle_no_iteration(
    *,
    target_path: Path,
    skill_name: str,
    args: argparse.Namespace,
    style_overrides: dict[str, str | int],
    project_dir: Path,
) -> int:
    """DEC-001: no iteration → lightgrey placeholder + exit 0.

    When ``--url-only`` is set, just render the Markdown image line
    (no JSON write). Otherwise compose the lightgrey Badge, emit the
    DEC-021 placeholder warning, and route the write through
    :func:`_write_badge_sidecars` (which still respects DEC-011
    ``--force`` — the placeholder does NOT clobber a "real" badge
    silently).
    """
    if args.url_only:
        # --url-only doesn't depend on sidecar state — render and go.
        return _render_url_only(
            args=args,
            project_dir=project_dir,
            skill_name=skill_name,
        )

    badge = compute_badge(
        assertions=None,
        grading=None,
        variance=None,
        skill_name=skill_name,
        iteration=None,
        generated_at=_now_iso_z(),
        label=args.label,
        style_overrides=style_overrides,
    )
    # The "wrote lightgrey placeholder" stderr line is deferred to
    # post-write so a collision-without-force exit 1 doesn't leave a
    # false trail (review pass 2, N2-4).
    return _write_badge_sidecars(
        target_path,
        badge,
        force=args.force,
        iteration=None,
        verbose=args.verbose,
        post_write_warning=(
            f"warning: no iteration found for skill {skill_name} — "
            "wrote lightgrey placeholder (run 'clauditor grade' to "
            "populate)"
        ),
    )


def _render_url_only(
    *,
    args: argparse.Namespace,
    project_dir: Path,
    skill_name: str,
) -> int:
    """DEC-002: print the shields.io Markdown image line to stdout."""
    repo_slug, branch = _resolve_url_only_slug_and_branch(args, project_dir)
    # The raw-content URL always points at the default badge JSON
    # location; --output is mutually exclusive with --url-only so we
    # do not need to reconcile against a user-provided path.
    output_relpath = f".clauditor/badges/{skill_name}.json"
    markdown = build_markdown_image(
        skill_name=skill_name,
        repo_slug=repo_slug,
        branch=branch,
        output_relpath=output_relpath,
        label=args.label,
    )
    print(markdown)
    return 0
