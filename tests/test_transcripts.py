"""Tests for clauditor.transcripts redaction logic."""

from __future__ import annotations

import copy

from clauditor.transcripts import redact


class TestKeyBasedScrub:
    def test_env_var_api_key(self):
        scrubbed, count = redact({"OPENAI_API_KEY": "abc"})
        assert scrubbed == {"OPENAI_API_KEY": "[REDACTED]"}
        assert count == 1

    def test_case_insensitive(self):
        scrubbed, count = redact({"openai_api_key": "abc"})
        assert scrubbed == {"openai_api_key": "[REDACTED]"}
        assert count == 1

    def test_nested_dict(self):
        scrubbed, count = redact({"env": {"GITHUB_TOKEN": "ghp_whatever"}})
        assert scrubbed == {"env": {"GITHUB_TOKEN": "[REDACTED]"}}
        assert count == 1

    def test_list_traversal(self):
        scrubbed, count = redact([{"API_KEY": "x"}])
        assert scrubbed == [{"API_KEY": "[REDACTED]"}]
        assert count == 1

    def test_exact_auth_key(self):
        scrubbed, count = redact({"AUTH": "xyz"})
        assert scrubbed == {"AUTH": "[REDACTED]"}
        assert count == 1

    def test_bare_sensitive_keys(self):
        # Stream events and MCP tool args often use bare names like
        # `token`, `password`, `secret` — must be scrubbed, case-insensitive.
        for key in ("token", "password", "secret", "key", "credentials"):
            scrubbed, count = redact({key: "hunter2"})
            assert scrubbed == {key: "[REDACTED]"}, key
            assert count == 1, key

    def test_suffix_matches(self):
        for key in (
            "MY_KEY",
            "MY_TOKEN",
            "MY_SECRET",
            "MY_PASSWORD",
            "MY_PASSPHRASE",
            "MY_CREDENTIAL",
        ):
            scrubbed, count = redact({key: "v"})
            assert scrubbed == {key: "[REDACTED]"}
            assert count == 1

    def test_unrelated_key_untouched(self):
        scrubbed, count = redact({"name": "alice"})
        assert scrubbed == {"name": "alice"}
        assert count == 0


class TestRegexScrub:
    def test_openai_key(self):
        scrubbed, count = redact("my key is sk-proj-abcdefghijklmnopqrstuv")
        assert scrubbed == "my key is [REDACTED]"
        assert count == 1

    def test_openai_plain_sk(self):
        scrubbed, count = redact("sk-abcdefghijklmnopqrstuvwx")
        assert scrubbed == "[REDACTED]"
        assert count == 1

    def test_anthropic_long_key_fully_scrubbed(self):
        # sk-ant-api03-<long-body-with-dashes-and-underscores> shape.
        # The old regex stopped at the first dash after `api03`, leaking
        # the tail. The widened char class must consume the whole token.
        key = "sk-ant-api03-" + "A" * 20 + "-" + "B" * 20 + "_" + "C" * 20
        scrubbed, count = redact(f"before {key} after")
        assert scrubbed == "before [REDACTED] after"
        assert count == 1
        assert "A" * 20 not in scrubbed
        assert "B" * 20 not in scrubbed
        assert "C" * 20 not in scrubbed

    def test_github_pat_positive(self):
        token = "ghp_" + "A" * 40
        scrubbed, count = redact(f"token={token}")
        assert scrubbed == "token=[REDACTED]"
        assert count == 1

    def test_github_pat_too_short_negative(self):
        scrubbed, count = redact("ghp_short")
        assert scrubbed == "ghp_short"
        assert count == 0

    def test_github_pat_long_form(self):
        token = "github_pat_" + "A" * 82
        scrubbed, count = redact(token)
        assert scrubbed == "[REDACTED]"
        assert count == 1

    def test_aws_akia(self):
        scrubbed, count = redact("AKIAIOSFODNN7EXAMPLE")
        assert scrubbed == "[REDACTED]"
        assert count == 1

    def test_aws_asia(self):
        scrubbed, count = redact("ASIAIOSFODNN7EXAMPLE")
        assert scrubbed == "[REDACTED]"
        assert count == 1

    def test_bearer(self):
        scrubbed, count = redact("Authorization: Bearer abcdefghijklmnopqrstuvwxyz")
        assert scrubbed == "Authorization: [REDACTED]"
        assert count == 1

    def test_slack_xoxb(self):
        scrubbed, count = redact("xoxb-1234567890-abcdef")
        assert scrubbed == "[REDACTED]"
        assert count == 1

    def test_matched_span_only(self):
        scrubbed, count = redact("prefix sk-live-xxxxxxxxxxxxxxxxxxxx suffix")
        assert scrubbed == "prefix [REDACTED] suffix"
        assert count == 1

    def test_plain_text_passthrough(self):
        scrubbed, count = redact("hello world")
        assert scrubbed == "hello world"
        assert count == 0


class TestCombined:
    def test_mixed_key_and_regex(self):
        obj = {
            "API_KEY": "anything",
            "log": "saw AKIAIOSFODNN7EXAMPLE in output",
        }
        scrubbed, count = redact(obj)
        assert scrubbed == {
            "API_KEY": "[REDACTED]",
            "log": "saw [REDACTED] in output",
        }
        assert count == 2

    def test_none_int_bool_untouched(self):
        obj = {"a": None, "b": 42, "c": True, "d": 1.5}
        scrubbed, count = redact(obj)
        assert scrubbed == obj
        assert count == 0

    def test_input_not_mutated(self):
        original = {
            "API_KEY": "xyz",
            "nested": [{"GITHUB_TOKEN": "ghp_" + "A" * 40}],
            "msg": "Bearer abcdefghijklmnopqrstuvwxyz",
        }
        snapshot = copy.deepcopy(original)
        scrubbed, count = redact(original)
        assert original == snapshot
        assert scrubbed != original
        assert count == 3

    def test_count_zero_when_no_matches(self):
        scrubbed, count = redact({"name": "alice", "msg": "hello"})
        assert count == 0
        assert scrubbed == {"name": "alice", "msg": "hello"}

    def test_deeply_nested(self):
        obj = {"a": {"b": {"c": [{"DB_PASSWORD": "p"}]}}}
        scrubbed, count = redact(obj)
        assert scrubbed == {"a": {"b": {"c": [{"DB_PASSWORD": "[REDACTED]"}]}}}
        assert count == 1

    def test_non_string_dict_key(self):
        # non-string keys (e.g. ints) must not crash the key check
        scrubbed, count = redact({1: "plain", 2: "AKIAIOSFODNN7EXAMPLE"})
        assert scrubbed == {1: "plain", 2: "[REDACTED]"}
        assert count == 1

    def test_tuple_treated_like_list(self):
        # tuples are JSON-compatible via json module only as lists, but
        # redact should still handle them by walking elements.
        scrubbed, count = redact(("hello", {"API_KEY": "v"}))
        assert count == 1
        # returned structure need not preserve tuple type; list is fine
        assert scrubbed[1] == {"API_KEY": "[REDACTED]"}
