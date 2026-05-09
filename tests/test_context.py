"""Tests for :mod:`clauditor.context` — IterationContext sidecar shape."""

from __future__ import annotations

import importlib
import json

import pytest

import clauditor.context as _context_mod

# The pytest plugin imports ``clauditor`` (which transitively imports
# clauditor.context) before coverage starts, so the dataclass decorator
# and module-level constants execute pre-instrumentation. Reload the
# module here so coverage sees those lines. See test_schemas.py for the
# canonical anchor of this pattern (referenced in CLAUDE.md).
importlib.reload(_context_mod)

from clauditor.context import IterationContext  # noqa: E402


def _full_payload() -> dict:
    """Canonical full-fidelity payload (every field non-null)."""
    return {
        "schema_version": 1,
        "harness": "claude-code",
        "provider": "anthropic",
        "model_runner": "claude-sonnet-4-6",
        "model_grader": "claude-sonnet-4-6",
        "system_prompt_source": "explicit",
        "sandbox_mode": "workspace-write",
        "reasoning_tokens": 1024,
        "cost_usd": 0.0234,
    }


def _validate_only_payload() -> dict:
    """Validate-only iteration shape — graders never ran."""
    return {
        "schema_version": 1,
        "harness": "claude-code",
        "provider": None,
        "model_runner": "claude-sonnet-4-6",
        "model_grader": None,
        "system_prompt_source": "skill_md",
        "sandbox_mode": None,
        "reasoning_tokens": None,
        "cost_usd": None,
    }


class TestIterationContextSerialization:
    def test_to_json_first_key_is_schema_version(self) -> None:
        ctx = IterationContext.from_dict(_full_payload())
        raw = ctx.to_json()
        # Inspect the raw text — Python's json module preserves dict
        # insertion order on emit, but the load-bearing contract is
        # that ``schema_version`` literally appears first in the on-
        # disk bytes per .claude/rules/json-schema-version.md.
        first_key_marker = raw.find('"')
        assert raw[first_key_marker : first_key_marker + len('"schema_version"')] == (
            '"schema_version"'
        )

    def test_to_json_emits_all_fields(self) -> None:
        ctx = IterationContext.from_dict(_full_payload())
        data = json.loads(ctx.to_json())
        assert set(data.keys()) == {
            "schema_version",
            "harness",
            "provider",
            "model_runner",
            "model_grader",
            "system_prompt_source",
            "sandbox_mode",
            "reasoning_tokens",
            "cost_usd",
        }

    def test_round_trip_full_payload(self) -> None:
        original = IterationContext.from_dict(_full_payload())
        restored = IterationContext.from_dict(json.loads(original.to_json()))
        assert restored == original

    def test_round_trip_with_nulls(self) -> None:
        original = IterationContext.from_dict(_validate_only_payload())
        restored = IterationContext.from_dict(json.loads(original.to_json()))
        assert restored == original
        assert restored.provider is None
        assert restored.model_grader is None
        assert restored.sandbox_mode is None
        assert restored.reasoning_tokens is None
        assert restored.cost_usd is None

    def test_to_json_preserves_int_cost_as_float(self) -> None:
        # Coverage for the int → float coercion branch on cost_usd.
        payload = _full_payload()
        payload["cost_usd"] = 1  # int, accepted-as-float
        ctx = IterationContext.from_dict(payload)
        assert ctx.cost_usd == 1.0
        assert isinstance(ctx.cost_usd, float)

    def test_default_schema_version_when_missing(self) -> None:
        # Coverage for the schema_version default path.
        payload = _full_payload()
        del payload["schema_version"]
        ctx = IterationContext.from_dict(payload)
        assert ctx.schema_version == 1


class TestIterationContextValidation:
    def test_from_dict_rejects_unknown_harness(self) -> None:
        payload = _full_payload()
        payload["harness"] = "raw-api"
        with pytest.raises(ValueError) as exc:
            IterationContext.from_dict(payload)
        msg = str(exc.value)
        assert "'raw-api'" in msg
        assert "claude-code" in msg
        assert "codex" in msg
        assert "harness" in msg

    def test_from_dict_rejects_unknown_provider(self) -> None:
        payload = _full_payload()
        payload["provider"] = "vertex"
        with pytest.raises(ValueError) as exc:
            IterationContext.from_dict(payload)
        msg = str(exc.value)
        assert "'vertex'" in msg
        assert "anthropic" in msg
        assert "openai" in msg
        assert "provider" in msg

    def test_from_dict_rejects_unknown_system_prompt_source(self) -> None:
        payload = _full_payload()
        payload["system_prompt_source"] = "claude_md"
        with pytest.raises(ValueError) as exc:
            IterationContext.from_dict(payload)
        msg = str(exc.value)
        assert "'claude_md'" in msg
        assert "explicit" in msg
        assert "agents_md" in msg
        assert "skill_md" in msg
        assert "system_prompt_source" in msg

    def test_from_dict_rejects_unknown_sandbox_mode(self) -> None:
        payload = _full_payload()
        payload["sandbox_mode"] = "full-write"
        with pytest.raises(ValueError) as exc:
            IterationContext.from_dict(payload)
        msg = str(exc.value)
        assert "'full-write'" in msg
        assert "read-only" in msg
        assert "workspace-write" in msg
        assert "danger-full-access" in msg
        assert "sandbox_mode" in msg

    def test_from_dict_rejects_bool_for_reasoning_tokens(self) -> None:
        payload = _full_payload()
        payload["reasoning_tokens"] = True
        with pytest.raises(ValueError) as exc:
            IterationContext.from_dict(payload)
        msg = str(exc.value)
        assert "reasoning_tokens" in msg
        assert "bool" in msg

    def test_from_dict_rejects_non_int_reasoning_tokens(self) -> None:
        payload = _full_payload()
        payload["reasoning_tokens"] = "1024"
        with pytest.raises(ValueError) as exc:
            IterationContext.from_dict(payload)
        msg = str(exc.value)
        assert "reasoning_tokens" in msg
        assert "str" in msg

    def test_from_dict_rejects_bool_for_cost_usd(self) -> None:
        payload = _full_payload()
        payload["cost_usd"] = True
        with pytest.raises(ValueError) as exc:
            IterationContext.from_dict(payload)
        msg = str(exc.value)
        assert "cost_usd" in msg
        assert "bool" in msg

    def test_from_dict_rejects_non_numeric_cost_usd(self) -> None:
        payload = _full_payload()
        payload["cost_usd"] = "0.05"
        with pytest.raises(ValueError) as exc:
            IterationContext.from_dict(payload)
        msg = str(exc.value)
        assert "cost_usd" in msg
        assert "str" in msg

    @pytest.mark.parametrize("harness", ["claude-code", "codex"])
    def test_from_dict_accepts_known_harness(self, harness: str) -> None:
        payload = _full_payload()
        payload["harness"] = harness
        ctx = IterationContext.from_dict(payload)
        assert ctx.harness == harness

    @pytest.mark.parametrize("provider", ["anthropic", "openai", None])
    def test_from_dict_accepts_known_provider(self, provider: str | None) -> None:
        payload = _full_payload()
        payload["provider"] = provider
        ctx = IterationContext.from_dict(payload)
        assert ctx.provider == provider

    @pytest.mark.parametrize(
        "source", ["explicit", "agents_md", "skill_md"]
    )
    def test_from_dict_accepts_known_system_prompt_source(self, source: str) -> None:
        payload = _full_payload()
        payload["system_prompt_source"] = source
        ctx = IterationContext.from_dict(payload)
        assert ctx.system_prompt_source == source

    @pytest.mark.parametrize(
        "mode", ["read-only", "workspace-write", "danger-full-access", None]
    )
    def test_from_dict_accepts_known_sandbox_mode(self, mode: str | None) -> None:
        payload = _full_payload()
        payload["sandbox_mode"] = mode
        ctx = IterationContext.from_dict(payload)
        assert ctx.sandbox_mode == mode

    def test_from_dict_accepts_known_literals(self) -> None:
        # Combined sweep — explicit acceptance of every literal-set
        # member at once on the same payload, mirroring the per-axis
        # parametrized cases above.
        for harness in ("claude-code", "codex"):
            for provider in ("anthropic", "openai", None):
                for source in ("explicit", "agents_md", "skill_md"):
                    for mode in (
                        "read-only",
                        "workspace-write",
                        "danger-full-access",
                        None,
                    ):
                        payload = _full_payload()
                        payload["harness"] = harness
                        payload["provider"] = provider
                        payload["system_prompt_source"] = source
                        payload["sandbox_mode"] = mode
                        ctx = IterationContext.from_dict(payload)
                        assert ctx.harness == harness
                        assert ctx.provider == provider
                        assert ctx.system_prompt_source == source
                        assert ctx.sandbox_mode == mode
