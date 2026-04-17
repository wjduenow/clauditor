"""``clauditor doctor`` — environment diagnostics."""

from __future__ import annotations

import argparse
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

    return 0
