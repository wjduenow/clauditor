"""Tests for ``clauditor doctor`` transport-related checks (US-007).

Focused on DEC-021 of ``plans/super/86-claude-cli-transport.md``:

- ``api-key-available`` presence check.
- ``claude-transport-available`` presence check.
- Summary line ``Effective default transport: <api|cli|none>``.
- No probe invocation (``claude -p --help`` or similar) is ever
  spawned — the doctor stays read-only.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from clauditor.cli import main


class TestDoctorTransportChecks:
    """DEC-021 — two presence checks + summary line, no probe."""

    def test_api_key_check_ok_when_key_set(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-value")
        rc = main(["doctor"])
        assert rc == 0
        out = capsys.readouterr().out
        lines = [
            line for line in out.splitlines()
            if "api-key-available" in line
        ]
        assert len(lines) == 1
        assert lines[0].startswith("[ok]")
        assert "ANTHROPIC_API_KEY" in lines[0]

    def test_api_key_check_info_when_key_unset(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        rc = main(["doctor"])
        assert rc == 0
        out = capsys.readouterr().out
        lines = [
            line for line in out.splitlines()
            if "api-key-available" in line
        ]
        assert len(lines) == 1
        # DEC-021: info (not failure) when unset.
        assert lines[0].startswith("[info]")

    def test_api_key_check_info_when_key_whitespace(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Whitespace-only key counts as unset."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "   ")
        rc = main(["doctor"])
        assert rc == 0
        out = capsys.readouterr().out
        lines = [
            line for line in out.splitlines()
            if "api-key-available" in line
        ]
        assert len(lines) == 1
        assert lines[0].startswith("[info]")

    def test_cli_transport_check_ok_when_claude_on_path(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        with patch("shutil.which", return_value="/usr/local/bin/claude"):
            rc = main(["doctor"])
        assert rc == 0
        out = capsys.readouterr().out
        lines = [
            line for line in out.splitlines()
            if "claude-transport-available" in line
        ]
        assert len(lines) == 1
        assert lines[0].startswith("[ok]")
        assert "/usr/local/bin/claude" in lines[0]

    def test_cli_transport_check_info_when_claude_missing(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        with patch("shutil.which", return_value=None):
            rc = main(["doctor"])
        assert rc == 0
        out = capsys.readouterr().out
        lines = [
            line for line in out.splitlines()
            if "claude-transport-available" in line
        ]
        assert len(lines) == 1
        # DEC-021: info (not failure) when unavailable.
        assert lines[0].startswith("[info]")

    def test_summary_line_always_present(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        rc = main(["doctor"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "Effective default transport:" in out

    def test_summary_cli_when_claude_on_path(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """DEC-001 subscription-first: auto→cli when ``claude`` present."""
        monkeypatch.delenv("CLAUDITOR_TRANSPORT", raising=False)
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        with patch("shutil.which", return_value="/usr/local/bin/claude"):
            rc = main(["doctor"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "Effective default transport: cli" in out

    def test_summary_api_when_only_api_key_available(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """auto resolves to api when claude binary missing but key is set."""
        monkeypatch.delenv("CLAUDITOR_TRANSPORT", raising=False)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-xyz")
        with patch("shutil.which", return_value=None):
            rc = main(["doctor"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "Effective default transport: api" in out

    def test_summary_none_when_neither_available(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """No key and no binary → ``none`` — neither transport usable."""
        monkeypatch.delenv("CLAUDITOR_TRANSPORT", raising=False)
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        with patch("shutil.which", return_value=None):
            rc = main(["doctor"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "Effective default transport: none" in out

    def test_summary_honors_env_override_api(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """CLAUDITOR_TRANSPORT=api pins api even with claude on PATH."""
        monkeypatch.setenv("CLAUDITOR_TRANSPORT", "api")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-xyz")
        with patch("shutil.which", return_value="/usr/local/bin/claude"):
            rc = main(["doctor"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "Effective default transport: api" in out

    def test_summary_honors_env_override_cli(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """CLAUDITOR_TRANSPORT=cli pins cli even when key is set."""
        monkeypatch.setenv("CLAUDITOR_TRANSPORT", "cli")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-xyz")
        with patch("shutil.which", return_value="/usr/local/bin/claude"):
            rc = main(["doctor"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "Effective default transport: cli" in out

    def test_summary_env_cli_without_binary_reports_none(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """CLAUDITOR_TRANSPORT=cli but binary missing → ``none``."""
        monkeypatch.setenv("CLAUDITOR_TRANSPORT", "cli")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-xyz")
        with patch("shutil.which", return_value=None):
            rc = main(["doctor"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "Effective default transport: none" in out

    def test_summary_env_api_without_key_reports_none(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """CLAUDITOR_TRANSPORT=api but no key → ``none``."""
        monkeypatch.setenv("CLAUDITOR_TRANSPORT", "api")
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        with patch("shutil.which", return_value="/usr/local/bin/claude"):
            rc = main(["doctor"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "Effective default transport: none" in out

    def test_summary_invalid_env_value_does_not_raise(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """A bad ``CLAUDITOR_TRANSPORT`` must not crash doctor."""
        monkeypatch.setenv("CLAUDITOR_TRANSPORT", "sdk")
        rc = main(["doctor"])
        assert rc == 0
        out = capsys.readouterr().out
        # Something plausible is printed (either "none" or a
        # fallback) — the key invariant is doctor stays green.
        assert "Effective default transport:" in out

    def test_doctor_never_spawns_probe_subprocess(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """DEC-021: no probe — no subprocess invocation targeting claude.

        Any ``subprocess.Popen`` / ``subprocess.run`` call whose args
        include ``claude`` would be a spec-violating probe. Patch
        both and assert zero calls reference the CLI.
        """
        popen_calls: list[tuple] = []
        run_calls: list[tuple] = []

        # Stub that records the call and raises to guarantee no real
        # subprocess even if a probe slips in.
        def fake_popen(*args, **kwargs):
            popen_calls.append((args, kwargs))
            raise AssertionError(
                f"doctor spawned subprocess.Popen: args={args!r}"
            )

        def fake_run(*args, **kwargs):
            run_calls.append((args, kwargs))
            raise AssertionError(
                f"doctor spawned subprocess.run: args={args!r}"
            )

        monkeypatch.setattr("subprocess.Popen", fake_popen)
        monkeypatch.setattr("subprocess.run", fake_run)

        rc = main(["doctor"])
        assert rc == 0
        # Neither recorded any calls — doctor is read-only.
        assert popen_calls == []
        assert run_calls == []

    def test_summary_line_follows_check_block(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Summary is printed AFTER the check grid so it's visually
        distinct at the bottom of the output."""
        rc = main(["doctor"])
        assert rc == 0
        out = capsys.readouterr().out
        lines = out.splitlines()
        summary_idx = next(
            i for i, line in enumerate(lines)
            if line.startswith("Effective default transport:")
        )
        # Summary is the last non-empty line, or at least comes after
        # every check line.
        check_idxs = [
            i for i, line in enumerate(lines)
            if line.startswith(("[ok]", "[warn]", "[fail]", "[info]"))
        ]
        assert check_idxs
        assert summary_idx > max(check_idxs)
