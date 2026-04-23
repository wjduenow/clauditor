"""``clauditor doctor`` — environment diagnostics."""

from __future__ import annotations

import argparse
import os
import sys
from importlib.resources import as_file, files
from pathlib import Path


def add_parser(subparsers: argparse._SubParsersAction) -> None:
    """Register the ``doctor`` subparser."""
    subparsers.add_parser(
        "doctor",
        help=(
            "Report environment diagnostics "
            "(Python, SDK, claude CLI, plugin, install)"
        ),
    )


def _is_pep660_editable() -> bool:
    """True if clauditor's dist-info advertises a PEP 660 editable install.

    Modern editable installs (pip 21.3+, uv, hatch) write a
    ``direct_url.json`` into the dist-info with ``dir_info.editable: true``
    even when the installed ``.py`` files are NOT symlinks (import-hook or
    ``.pth``-based installs). The plain ``origin.is_symlink()`` check in
    ``cmd_doctor`` misses those; this helper provides a more reliable
    primary signal, falling back to the symlink check in the caller.
    """
    import importlib.metadata
    import json

    try:
        dist = importlib.metadata.distribution("clauditor")
        direct_url = dist.read_text("direct_url.json")
    except (importlib.metadata.PackageNotFoundError, OSError):
        return False
    if not direct_url:
        return False
    try:
        data = json.loads(direct_url)
    except json.JSONDecodeError:
        return False
    return bool(data.get("dir_info", {}).get("editable"))


def cmd_doctor(args: argparse.Namespace) -> int:
    """Read-only environment diagnostics (DEC-005/008/013/014).

    Always exits 0 — this is a reporting tool, not a CI gate.
    """
    import importlib.metadata
    import importlib.util
    import shutil

    checks: list[tuple[str, str, str]] = []

    py_version = sys.version_info
    if py_version >= (3, 11):
        checks.append(
            (
                "python",
                "ok",
                f"Python {py_version.major}.{py_version.minor}.{py_version.micro}",
            )
        )
    else:
        checks.append(
            (
                "python",
                "fail",
                f"Python {py_version.major}.{py_version.minor} < 3.11 (required)",
            )
        )

    if importlib.util.find_spec("anthropic") is not None:
        checks.append(("anthropic", "ok", "SDK importable"))
    else:
        checks.append(
            (
                "anthropic",
                "warn",
                "SDK not installed (required only for Layer 2/3 grading)",
            )
        )

    claude_path = shutil.which("claude")
    if claude_path:
        checks.append(("claude-cli", "ok", claude_path))
    else:
        checks.append(
            ("claude-cli", "fail", "`claude` not found on PATH")
        )

    # DEC-021 of plans/super/86-claude-cli-transport.md: two presence
    # checks, no probe. ``info`` status (not ``fail``) when a transport
    # is unavailable — having only one of the two is a valid config,
    # not an error. No ``claude -p --help`` probe: stale-auth scenarios
    # (exactly what doctor is meant to diagnose) can make a probe hang
    # or fail unpredictably, producing worse UX than "binary present,
    # auth state unknown".
    api_key_value = os.environ.get("ANTHROPIC_API_KEY")
    api_key_available = (
        api_key_value is not None and api_key_value.strip() != ""
    )
    if api_key_available:
        checks.append(
            ("api-key-available", "ok", "ANTHROPIC_API_KEY is set")
        )
    else:
        cli_suffix = " (CLI transport still usable)" if claude_path else ""
        checks.append(
            (
                "api-key-available",
                "info",
                f"ANTHROPIC_API_KEY not set{cli_suffix}",
            )
        )

    cli_transport_available = claude_path is not None
    if cli_transport_available:
        checks.append(
            (
                "claude-transport-available",
                "ok",
                f"`claude` on PATH at {claude_path}",
            )
        )
    else:
        api_suffix = " (API transport still usable)" if api_key_available else ""
        checks.append(
            (
                "claude-transport-available",
                "info",
                f"`claude` not on PATH{api_suffix}",
            )
        )

    try:
        # Version-agnostic lookup: `entry_points(group=...)` is 3.10+, but
        # `doctor` must keep working even when the Python-version check
        # itself is about to fail, so fall back to filtering manually.
        eps = importlib.metadata.entry_points()
        if hasattr(eps, "select"):
            eps = eps.select(group="pytest11")
        else:
            eps = [
                ep for ep in eps
                if getattr(ep, "group", None) == "pytest11"
            ]
        names = [ep.name for ep in eps]
        if "clauditor" in names:
            checks.append(
                ("pytest-plugin", "ok", "clauditor registered under pytest11")
            )
        else:
            checks.append(
                (
                    "pytest-plugin",
                    "fail",
                    f"clauditor not registered (found: {names})",
                )
            )
    except Exception as e:  # pragma: no cover - defensive
        checks.append(("pytest-plugin", "fail", f"entry_points lookup failed: {e}"))

    spec = importlib.util.find_spec("clauditor")
    if spec is not None and spec.origin is not None:
        origin = Path(spec.origin).resolve()
        # Editable installs: PEP 660 metadata is the primary signal
        # (catches import-hook / .pth installs where the .py file is
        # not a symlink); fall back to symlink detection for older
        # pip (<21.3) installs that still used symlinks directly.
        if _is_pep660_editable():
            checks.append(("editable-install", "ok", str(origin.parent)))
        elif "site-packages" in origin.parts and not origin.is_symlink():
            checks.append(
                (
                    "editable-install",
                    "warn",
                    f"clauditor installed non-editable at {origin.parent} "
                    f"— source edits will not propagate",
                )
            )
        else:
            checks.append(("editable-install", "ok", str(origin.parent)))
    else:
        checks.append(("editable-install", "fail", "clauditor package not importable"))

    # DEC-013: inspect the /clauditor skill symlink and report its health.
    try:
        from clauditor import setup as setup_module
        from clauditor.cli.setup import check_clauditor_skill_symlink

        traversable = files("clauditor") / "skills" / "clauditor"
        with as_file(traversable) as pkg_skill_root_path:
            pkg_skill_root = Path(pkg_skill_root_path).resolve()
            project_root = setup_module.find_project_root(Path.cwd())
            checks.append(
                check_clauditor_skill_symlink(project_root, pkg_skill_root)
            )
    except Exception as e:  # pragma: no cover - defensive
        checks.append(
            ("clauditor-skill-symlink", "warn", f"check failed: {e}")
        )

    width = max(len(name) for name, _, _ in checks)
    for name, status, detail in checks:
        tag = f"[{status}]"
        print(f"{tag:<7} {name:<{width}}  {detail}")

    # DEC-021: summary line — the effective default transport under a
    # "no CLI flag, no spec field" invocation. Honors the
    # ``CLAUDITOR_TRANSPORT`` env layer because that is the only
    # remaining non-default input an operator might have set; the CLI
    # flag and spec field are intentionally excluded because they are
    # per-command, not environmental. A final ``"auto"`` is resolved
    # via ``claude`` binary presence per DEC-001 subscription-first.
    # Reports ``"none"`` when the chosen transport's prerequisite is
    # missing (``"api"`` without a key, or ``"cli"`` without the
    # binary on PATH).
    from clauditor._anthropic import resolve_transport

    env_transport = os.environ.get("CLAUDITOR_TRANSPORT")
    if env_transport is not None and env_transport.strip() == "":
        env_transport = None
    try:
        resolved = resolve_transport(None, env_transport, None)
    except ValueError:
        # Invalid ``CLAUDITOR_TRANSPORT`` value; the resolution would
        # raise at any real call site. Report ``"none"`` here so the
        # summary is always printable.
        resolved = "auto"
        effective = "none"
    else:
        if resolved == "auto":
            resolved = "cli" if cli_transport_available else "api"
        if resolved == "cli":
            effective = "cli" if cli_transport_available else "none"
        elif resolved == "api":
            effective = "api" if api_key_available else "none"
        else:  # pragma: no cover - defensive
            effective = "none"

    print(f"Effective default transport: {effective}")

    return 0
