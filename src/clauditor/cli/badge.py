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
import sys
from pathlib import Path

from clauditor import _git
from clauditor.badge import (
    build_markdown_image,
    compute_badge,
    discover_iteration,
    load_iteration_sidecars,
)
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

# Upper bound on a ``--style`` value (DEC-023). 512 chars is generous
# for any reasonable shields.io field (even inline SVG data URLs stay
# well under that when they show up in practice).
_STYLE_VALUE_MAX_LEN: int = 512


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
        if (child / skill_name).is_dir():
            found.append(n)
    return sorted(found)


def _write_badge_json(
    target: Path,
    payload: dict,
    *,
    force: bool,
    iteration: int | None,
    verbose: bool,
) -> int:
    """Write the badge JSON payload to ``target``.

    Handles the DEC-011 overwrite-policy check: if ``target`` exists
    and ``force`` is ``False``, print the error to stderr and return
    exit 1 without writing. On a successful write, optionally prints
    the DEC-018 stderr info line when ``verbose=True``.

    ``iteration=None`` renders the verbose line with a
    ``(no iteration)`` fragment to signal the DEC-001 lightgrey
    placeholder path; otherwise ``(iteration N)``.

    Returns the exit code the caller should surface (0 on success,
    1 on collision).
    """
    if target.exists() and not force:
        print(
            f"ERROR: {target} already exists (pass --force to overwrite)",
            file=sys.stderr,
        )
        return 1

    # For non-default paths (passed via --output) the parent dir was
    # validated already (DEC-022). For the default path we create the
    # parent chain unconditionally; re-running after a pruned tree
    # should not error.
    target.parent.mkdir(parents=True, exist_ok=True)

    try:
        target.write_text(
            json.dumps(payload, indent=2) + "\n",
            encoding="utf-8",
        )
    except OSError as exc:
        print(
            f"ERROR: could not write {target}: {exc}",
            file=sys.stderr,
        )
        return 1

    if verbose:
        tail = (
            f"(iteration {iteration})"
            if iteration is not None
            else "(no iteration)"
        )
        print(
            f"clauditor.badge: wrote {target} {tail}",
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
        fallback_msg = (
            f"warning: git auto-detect failed; using placeholder "
            f"{repo_slug}/{branch} — pass --repo USER/REPO to override"
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

    # Parse --style flags up-front (DEC-015 / DEC-023). A bad value
    # here blocks before any sidecar read / disk write.
    style_overrides: dict[str, str] = {}
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
        if key not in _ALLOWED_STYLE_KEYS:
            print(
                f"warning: clauditor.badge: unknown --style key {key!r} — "
                "passing through anyway",
                file=sys.stderr,
            )
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

    # Parse --from-iteration to int here so validation errors surface
    # before any disk walk. argparse takes the raw string to give the
    # error message a clean shape.
    explicit_iter: int | None = None
    if args.from_iteration is not None:
        try:
            explicit_iter = int(args.from_iteration)
            if explicit_iter < 1:
                raise ValueError("must be >= 1")
        except ValueError as exc:
            print(
                f"ERROR: --from-iteration must be a positive integer, "
                f"got {args.from_iteration!r}: {exc}",
                file=sys.stderr,
            )
            return 2

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

    # Code path 4 — happy path. Compute and dispatch on --url-only.
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
    # Warn so users notice the spec is under-specified.
    if badge.color == "lightgrey":
        print(
            "warning: eval spec declares 0 L1 assertions — wrote "
            "lightgrey 'no data' badge",
            file=sys.stderr,
        )

    if args.url_only:
        return _render_url_only(
            args=args,
            project_dir=project_dir,
            skill_name=skill_name,
        )

    return _write_badge_json(
        target_path,
        badge.to_endpoint_json(),
        force=args.force,
        iteration=iteration_n,
        verbose=args.verbose,
    )


def _handle_no_iteration(
    *,
    target_path: Path,
    skill_name: str,
    args: argparse.Namespace,
    style_overrides: dict[str, str],
    project_dir: Path,
) -> int:
    """DEC-001: no iteration → lightgrey placeholder + exit 0.

    When ``--url-only`` is set, just render the Markdown image line
    (no JSON write). Otherwise compose the lightgrey Badge, emit the
    DEC-021 placeholder warning, and route the write through
    :func:`_write_badge_json` (which still respects DEC-011 ``--force``
    — the placeholder does NOT clobber a "real" badge silently).
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
    print(
        f"warning: no iteration found for skill {skill_name} — "
        "wrote lightgrey placeholder (run 'clauditor grade' to populate)",
        file=sys.stderr,
    )
    return _write_badge_json(
        target_path,
        badge.to_endpoint_json(),
        force=args.force,
        iteration=None,
        verbose=args.verbose,
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
