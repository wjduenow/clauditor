"""Tests for Layer 1 deterministic assertions."""

import importlib

import clauditor.assertions as _assertions_mod

importlib.reload(_assertions_mod)

from unittest.mock import MagicMock, patch  # noqa: E402

from clauditor.assertions import (  # noqa: E402
    AssertionResult,
    AssertionSet,
    _check_url,
    _is_private_ip,
    _is_safe_url,
    assert_contains,
    assert_has_entries,
    assert_has_format,
    assert_has_urls,
    assert_max_length,
    assert_min_count,
    assert_min_length,
    assert_not_contains,
    assert_regex,
    assert_urls_reachable,
    run_assertions,
)

SAMPLE_OUTPUT = """
🎯 **Top 5 kids' activities near Cupertino, CA** (ages 4-6, Free to $$, 25mi)

---
**Venues** (open on target dates):

**1. Children's Discovery Museum** — Museum (Indoor), $$
📍 180 Woz Way, San Jose, CA 95110 (~11mi)
🕐 9:30am-4:30pm daily (spring break hours)
👶 Best for ages: 0-10
🌟 Hands-on exhibits including mammoth bones, water play, and art studio.
🌐 [cdm.org](https://www.cdm.org/) | 📞 (408) 298-5437

**2. Deer Hollow Farm** — Nature/Farm, Free
📍 22500 Cristo Rey Dr, Cupertino, CA 94040 (~4mi)
🕐 Tue 8am-4pm; Wed 8am-1pm
👶 Best for ages: 2-8
🌟 Free working farm with goats, chickens, sheep.
🌐 [deerhollowfarm.org](https://www.deerhollowfarm.org/) | 📞 (650) 903-6331

**3. Happy Hollow Park & Zoo** — Zoo/Amusement, $$
📍 748 Story Rd, San Jose, CA 95112 (~13mi)
🕐 Wed-Sun 10am-4pm
👶 Best for ages: 2-8
🌟 Kid-friendly rides, petting zoo, animal encounters.
🌐 [happyhollow.org](https://happyhollow.org/) | 📞 (408) 794-6400

---
**Events** (happening on target dates):

**4. Police Read Along** — EVENT, Free
📍 Cupertino Library, 10800 Torre Ave (~1mi)
📅 Wednesday, April 8 • 10:30am-11:00am
🌟 Sheriff's Deputy reads books and shares safety tips.
🎟️ [sccld.org](https://sccld.org/locations/cupertino/)
"""


class TestContains:
    def test_found(self):
        result = assert_contains(SAMPLE_OUTPUT, "Venues")
        assert result.passed

    def test_missing(self):
        result = assert_contains(SAMPLE_OUTPUT, "Nonexistent Section")
        assert not result.passed


class TestNotContains:
    def test_absent(self):
        result = assert_not_contains(SAMPLE_OUTPUT, "ERROR")
        assert result.passed

    def test_present(self):
        result = assert_not_contains(SAMPLE_OUTPUT, "Venues")
        assert not result.passed


class TestRegex:
    def test_match(self):
        result = assert_regex(SAMPLE_OUTPUT, r"\*\*\d+\.\s+")
        assert result.passed
        assert result.evidence is not None

    def test_no_match(self):
        result = assert_regex(SAMPLE_OUTPUT, r"ZZZZZ\d+")
        assert not result.passed


class TestMinCount:
    def test_enough(self):
        result = assert_min_count(SAMPLE_OUTPUT, r"\*\*\d+\.", 3)
        assert result.passed

    def test_not_enough(self):
        result = assert_min_count(SAMPLE_OUTPUT, r"\*\*\d+\.", 10)
        assert not result.passed


class TestMinLength:
    def test_long_enough(self):
        result = assert_min_length(SAMPLE_OUTPUT, 100)
        assert result.passed

    def test_too_short(self):
        result = assert_min_length("short", 100)
        assert not result.passed


class TestHasUrls:
    def test_has_urls(self):
        result = assert_has_urls(SAMPLE_OUTPUT, minimum=3)
        assert result.passed

    def test_not_enough_urls(self):
        result = assert_has_urls("no urls here", minimum=1)
        assert not result.passed


class TestHasEntries:
    def test_has_entries(self):
        result = assert_has_entries(SAMPLE_OUTPUT, minimum=3)
        assert result.passed

    def test_not_enough(self):
        result = assert_has_entries(SAMPLE_OUTPUT, minimum=10)
        assert not result.passed


class TestRunAssertions:
    def test_all_pass(self):
        assertions = [
            {"type": "contains", "value": "Venues"},
            {"type": "contains", "value": "Events"},
            {"type": "has_urls", "value": "3"},
            {"type": "has_entries", "value": "3"},
            {"type": "not_contains", "value": "ERROR"},
            {"type": "min_length", "value": "500"},
        ]
        results = run_assertions(SAMPLE_OUTPUT, assertions)
        assert results.passed
        assert results.pass_rate == 1.0

    def test_mixed_results(self):
        assertions = [
            {"type": "contains", "value": "Venues"},
            {"type": "contains", "value": "Nonexistent"},
        ]
        results = run_assertions(SAMPLE_OUTPUT, assertions)
        assert not results.passed
        assert results.pass_rate == 0.5
        assert len(results.failed) == 1

    def test_unknown_type(self):
        results = run_assertions(SAMPLE_OUTPUT, [{"type": "bogus", "value": "x"}])
        assert not results.passed

    def test_summary(self):
        assertions = [
            {"type": "contains", "value": "Venues"},
            {"type": "contains", "value": "Missing"},
        ]
        results = run_assertions(SAMPLE_OUTPUT, assertions)
        summary = results.summary()
        assert "1/2" in summary
        assert "FAIL" in summary


class TestAssertionSet:
    def test_empty(self):
        s = AssertionSet()
        assert s.pass_rate == 0.0
        assert s.passed  # no assertions = nothing failed (vacuous truth)

    def test_all_passed(self):
        s = AssertionSet(
            results=[
                assert_contains("hello world", "hello"),
                assert_contains("hello world", "world"),
            ]
        )
        assert s.passed
        assert s.pass_rate == 1.0
        assert len(s.failed) == 0

    def test_pass_rate_mixed(self):
        s = AssertionSet(
            results=[
                assert_contains("hello", "hello"),
                assert_contains("hello", "missing"),
                assert_contains("hello", "also_missing"),
            ]
        )
        assert not s.passed
        assert abs(s.pass_rate - 1 / 3) < 0.01

    def test_failed_returns_only_failures(self):
        s = AssertionSet(
            results=[
                assert_contains("hello", "hello"),
                assert_contains("hello", "nope"),
            ]
        )
        failed = s.failed
        assert len(failed) == 1
        assert not failed[0].passed

    def test_summary_format(self):
        s = AssertionSet(
            results=[
                assert_contains("hello", "hello"),
                assert_contains("hello", "nope"),
            ]
        )
        summary = s.summary()
        assert "1/2" in summary
        assert "50%" in summary
        assert "FAIL" in summary
        assert "nope" in summary


def _fail(name: str, msg: str = "nope", evidence: str | None = None) -> AssertionResult:
    return AssertionResult(
        name=name, passed=False, message=msg, kind="presence", evidence=evidence
    )


def _pass(name: str) -> AssertionResult:
    return AssertionResult(name=name, passed=True, message="ok", kind="presence")


class TestGroupedSummary:
    """DEC-004, DEC-012: grouped_summary collapses field:suffix failures."""

    def test_empty_set_returns_empty_list(self):
        assert AssertionSet().grouped_summary() == []

    def test_all_passing_returns_empty_list(self):
        s = AssertionSet(
            results=[
                _pass(f"section:Restaurants/default[{i}].website:format")
                for i in range(3)
            ]
        )
        assert s.grouped_summary() == []

    def test_six_website_format_failures_collapse_to_one_line(self):
        s = AssertionSet(
            results=[
                _fail(
                    f"section:Restaurants/default[{i}].website:format",
                    evidence="paesanosj.com",
                )
                for i in range(6)
            ]
        )
        lines = s.grouped_summary()
        assert len(lines) == 1
        assert "6/6" in lines[0]
        assert "Restaurants/default[*].website:format" in lines[0]
        assert "paesanosj.com" in lines[0]

    def test_presence_failures_use_synthetic_suffix(self):
        s = AssertionSet(
            results=[
                _fail(f"section:Restaurants/default[{i}].name", msg="missing name")
                for i in range(3)
            ]
        )
        lines = s.grouped_summary()
        assert len(lines) == 1
        assert "3/3" in lines[0]
        assert ".name:presence" in lines[0]

    def test_mixed_groups_one_line_each(self):
        results = []
        for i in range(3):
            results.append(
                _fail(
                    f"section:Restaurants/default[{i}].website:format",
                    evidence="x.com",
                )
            )
        for i in range(2):
            results.append(
                _fail(
                    f"section:Restaurants/default[{i}].phone:pattern",
                    evidence="bad",
                )
            )
        results.append(
            _fail("section:Restaurants/default[0].name", msg="missing")
        )
        lines = AssertionSet(results=results).grouped_summary()
        assert len(lines) == 3
        joined = "\n".join(lines)
        assert "3/3 Restaurants/default[*].website:format" in joined
        assert "2/2 Restaurants/default[*].phone:pattern" in joined
        assert "1/1 Restaurants/default[*].name:presence" in joined

    def test_partial_group_shows_failed_over_total(self):
        results = [
            _pass("section:Restaurants/default[0].website:format"),
            _fail(
                "section:Restaurants/default[1].website:format",
                evidence="bad.com",
            ),
            _fail(
                "section:Restaurants/default[2].website:format",
                evidence="also.bad",
            ),
        ]
        lines = AssertionSet(results=results).grouped_summary()
        assert len(lines) == 1
        assert "2/3" in lines[0]

    def test_non_structured_names_passthrough(self):
        s = AssertionSet(
            results=[
                _fail("has_urls", msg="Found 0 URLs"),
                _fail(
                    "section:Venues/default[0].name:presence",
                    msg="missing",
                ),
            ]
        )
        lines = s.grouped_summary()
        assert len(lines) == 2
        # structured group first (preserves insertion order of groups seen)
        # passthroughs appended after
        assert any("has_urls" in line for line in lines)

    def test_summary_unchanged_regression(self):
        """Existing summary() output is untouched by grouped_summary addition."""
        s = AssertionSet(
            results=[
                _fail("section:Restaurants/default[0].website:format", msg="bad"),
                _fail("section:Restaurants/default[1].website:format", msg="bad"),
            ]
        )
        out = s.summary()
        assert "0/2" in out
        assert out.count("FAIL:") == 2


class TestMaxLength:
    def test_pass(self):
        result = assert_max_length("short", 100)
        assert result.passed
        assert "5" in result.message

    def test_fail(self):
        result = assert_max_length("this is too long", 5)
        assert not result.passed

    def test_exact(self):
        result = assert_max_length("12345", 5)
        assert result.passed


class TestAssertionResultBool:
    def test_bool_true(self):
        r = AssertionResult(name="test", passed=True, message="ok", kind="custom")
        assert bool(r) is True

    def test_bool_false(self):
        r = AssertionResult(name="test", passed=False, message="fail", kind="custom")
        assert bool(r) is False


class TestRunAssertionsEdgeCases:
    def test_empty_assertions(self):
        result = run_assertions("anything", [])
        assert isinstance(result, AssertionSet)
        assert result.passed  # vacuous truth
        assert len(result.results) == 0

    def test_unknown_type_message(self):
        result = run_assertions("text", [{"type": "bogus", "value": "x"}])
        assert not result.passed
        assert len(result.results) == 1
        assert "Unknown assertion type" in result.results[0].message
        assert result.results[0].name == "unknown:bogus"

    def test_max_length_via_run(self):
        result = run_assertions("short", [{"type": "max_length", "value": "100"}])
        assert result.passed

    def test_regex_via_run(self):
        result = run_assertions("hello 123", [{"type": "regex", "value": r"\d+"}])
        assert result.passed

    def test_min_count_via_run(self):
        assertion = {"type": "min_count", "value": "a", "minimum": 3}
        result = run_assertions("aaa", [assertion])
        assert result.passed

    def test_has_urls_via_run(self):
        result = run_assertions(
            "visit https://example.com",
            [{"type": "has_urls", "value": "1"}],
        )
        assert result.passed

    def test_has_entries_via_run(self):
        result = run_assertions(
            "**1. Item** **2. Item**",
            [{"type": "has_entries", "value": "2"}],
        )
        assert result.passed

    def test_urls_reachable_via_run(self):
        with patch(
            "clauditor.assertions._is_safe_url", return_value=True
        ), patch(
            "clauditor.assertions._check_url",
            return_value=("https://example.com", 200),
        ):
            result = run_assertions(
                "visit https://example.com",
                [{"type": "urls_reachable", "value": "1"}],
            )
            assert result.passed

    def test_has_format_via_run(self):
        result = run_assertions(
            "contact user@example.com or admin@test.org",
            [{"type": "has_format", "format": "email", "value": "2"}],
        )
        assert result.passed


class TestSsrfProtection:
    """Tests for SSRF protection helpers."""

    def test_private_ip_loopback(self):
        with patch(
            "clauditor.assertions.socket.getaddrinfo"
        ) as mock_dns:
            mock_dns.return_value = [
                (2, 1, 6, "", ("127.0.0.1", 0))
            ]
            assert _is_private_ip("localhost")

    def test_private_ip_rfc1918(self):
        with patch(
            "clauditor.assertions.socket.getaddrinfo"
        ) as mock_dns:
            mock_dns.return_value = [
                (2, 1, 6, "", ("10.0.0.1", 0))
            ]
            assert _is_private_ip("internal.corp")

    def test_private_ip_link_local(self):
        with patch(
            "clauditor.assertions.socket.getaddrinfo"
        ) as mock_dns:
            mock_dns.return_value = [
                (2, 1, 6, "", ("169.254.169.254", 0))
            ]
            assert _is_private_ip("metadata.cloud")

    def test_public_ip(self):
        with patch(
            "clauditor.assertions.socket.getaddrinfo"
        ) as mock_dns:
            mock_dns.return_value = [
                (2, 1, 6, "", ("93.184.216.34", 0))
            ]
            assert not _is_private_ip("example.com")

    def test_dns_failure_treated_as_unsafe(self):
        import socket as _socket

        with patch(
            "clauditor.assertions.socket.getaddrinfo",
            side_effect=_socket.gaierror("DNS failed"),
        ):
            assert _is_private_ip("nonexistent.invalid")

    def test_safe_url_http(self):
        with patch(
            "clauditor.assertions._is_private_ip",
            return_value=False,
        ):
            assert _is_safe_url("https://example.com/path")

    def test_unsafe_url_file_scheme(self):
        assert not _is_safe_url("file:///etc/passwd")

    def test_unsafe_url_no_host(self):
        assert not _is_safe_url("http://")

    def test_unsafe_url_private_ip(self):
        with patch(
            "clauditor.assertions._is_private_ip",
            return_value=True,
        ):
            assert not _is_safe_url("http://10.0.0.1/admin")


class TestCheckUrl:
    """Tests for _check_url with SSRF-safe redirect following."""

    def test_urllib_success(self):
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        with patch.dict("sys.modules", {"httpx": None}), patch(
            "clauditor.assertions.urllib.request.build_opener"
        ) as mock_opener:
            mock_opener.return_value.open.return_value = mock_resp
            url, status = _check_url("https://example.com")
            assert status == 200

    def test_urllib_http_error_404(self):
        import urllib.error

        with patch.dict("sys.modules", {"httpx": None}), patch(
            "clauditor.assertions.urllib.request.build_opener"
        ) as mock_opener:
            mock_opener.return_value.open.side_effect = (
                urllib.error.HTTPError(
                    "https://example.com", 404, "Not Found",
                    {}, None,
                )
            )
            url, status = _check_url("https://example.com")
            assert status == 404

    def test_urllib_redirect_to_private_blocked(self):
        import urllib.error

        headers = MagicMock()
        headers.get.return_value = "http://127.0.0.1/evil"
        with patch.dict("sys.modules", {"httpx": None}), patch(
            "clauditor.assertions.urllib.request.build_opener"
        ) as mock_opener:
            mock_opener.return_value.open.side_effect = (
                urllib.error.HTTPError(
                    "https://example.com", 302, "Found",
                    headers, None,
                )
            )
            url, status = _check_url("https://example.com")
            assert status == "blocked"

    def test_urllib_redirect_no_location(self):
        import urllib.error

        headers = MagicMock()
        headers.get.return_value = None
        with patch.dict("sys.modules", {"httpx": None}), patch(
            "clauditor.assertions.urllib.request.build_opener"
        ) as mock_opener:
            mock_opener.return_value.open.side_effect = (
                urllib.error.HTTPError(
                    "https://example.com", 301, "Moved",
                    headers, None,
                )
            )
            url, status = _check_url("https://example.com")
            assert status == 301

    def test_urllib_generic_exception(self):
        with patch.dict("sys.modules", {"httpx": None}), patch(
            "clauditor.assertions.urllib.request.build_opener"
        ) as mock_opener:
            mock_opener.return_value.open.side_effect = (
                TimeoutError("timed out")
            )
            url, status = _check_url("https://example.com")
            assert status == "TimeoutError"

    def test_httpx_success(self):
        mock_httpx = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_httpx.head.return_value = mock_resp
        with patch.dict(
            "sys.modules", {"httpx": mock_httpx}
        ):
            url, status = _check_url("https://example.com")
            assert status == 200

    def test_httpx_redirect_to_private_blocked(self):
        mock_httpx = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 302
        mock_resp.headers = {"location": "http://127.0.0.1/evil"}
        mock_httpx.head.return_value = mock_resp
        with patch.dict(
            "sys.modules", {"httpx": mock_httpx}
        ):
            url, status = _check_url("https://example.com")
            assert status == "blocked"

    def test_httpx_redirect_no_location(self):
        mock_httpx = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 301
        mock_resp.headers = {}
        mock_httpx.head.return_value = mock_resp
        with patch.dict(
            "sys.modules", {"httpx": mock_httpx}
        ):
            url, status = _check_url("https://example.com")
            assert status == 301

    def test_httpx_too_many_redirects(self):
        mock_httpx = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 302
        mock_resp.headers = {
            "location": "https://example.com/loop"
        }
        mock_httpx.head.return_value = mock_resp
        with patch.dict(
            "sys.modules", {"httpx": mock_httpx}
        ), patch(
            "clauditor.assertions._is_safe_url",
            return_value=True,
        ):
            url, status = _check_url("https://example.com")
            assert status == "TooManyRedirects"

    def test_httpx_exception(self):
        mock_httpx = MagicMock()
        mock_httpx.head.side_effect = ConnectionError("fail")
        with patch.dict(
            "sys.modules", {"httpx": mock_httpx}
        ):
            url, status = _check_url("https://example.com")
            assert status == "ConnectionError"

    def test_urllib_too_many_redirects(self):
        import urllib.error

        headers = MagicMock()
        headers.get.return_value = "https://example.com/loop"
        with patch.dict("sys.modules", {"httpx": None}), patch(
            "clauditor.assertions.urllib.request.build_opener"
        ) as mock_opener, patch(
            "clauditor.assertions._is_safe_url",
            return_value=True,
        ):
            mock_opener.return_value.open.side_effect = (
                urllib.error.HTTPError(
                    "https://example.com", 302, "Found",
                    headers, None,
                )
            )
            url, status = _check_url("https://example.com")
            assert status == "TooManyRedirects"


class TestUrlsReachable:
    """Tests for urls_reachable assertion with mocked HTTP."""

    def test_all_ok(self):
        output = (
            "Visit https://a.com and https://b.com"
        )
        with patch(
            "clauditor.assertions._is_safe_url", return_value=True
        ), patch(
            "clauditor.assertions._check_url",
            side_effect=[
                ("https://a.com", 200),
                ("https://b.com", 200),
            ],
        ):
            result = assert_urls_reachable(output, minimum=2)
            assert result.passed
            assert "2/2" in result.message

    def test_below_threshold(self):
        output = "Visit https://a.com and https://b.com"
        with patch(
            "clauditor.assertions._is_safe_url", return_value=True
        ), patch(
            "clauditor.assertions._check_url",
            side_effect=[
                ("https://a.com", 200),
                ("https://b.com", 404),
            ],
        ):
            result = assert_urls_reachable(output, minimum=2)
            assert not result.passed
            assert "1/2" in result.message

    def test_ssrf_blocked(self):
        output = "Visit http://127.0.0.1/admin"
        with patch(
            "clauditor.assertions._is_safe_url", return_value=False
        ):
            result = assert_urls_reachable(output, minimum=1)
            assert not result.passed
            assert "blocked" in result.evidence

    def test_timeout(self):
        output = "Visit https://slow.example.com"
        with patch(
            "clauditor.assertions._is_safe_url", return_value=True
        ), patch(
            "clauditor.assertions._check_url",
            return_value=(
                "https://slow.example.com", "TimeoutError"
            ),
        ):
            result = assert_urls_reachable(output, minimum=1)
            assert not result.passed

    def test_no_urls(self):
        result = assert_urls_reachable(
            "no urls here", minimum=1
        )
        assert not result.passed
        assert "0 URLs" in result.message

    def test_no_urls_zero_threshold(self):
        result = assert_urls_reachable(
            "no urls here", minimum=0
        )
        assert result.passed


class TestHasFormat:
    """Tests for has_format assertion."""

    def test_found(self):
        output = (
            "Contact user@example.com, admin@test.org, "
            "support@help.io"
        )
        result = assert_has_format(output, "email", minimum=3)
        assert result.passed
        assert "3" in result.message

    def test_insufficient(self):
        output = "Contact user@example.com"
        result = assert_has_format(output, "email", minimum=3)
        assert not result.passed

    def test_unknown_format(self):
        result = assert_has_format("text", "bogus_format")
        assert not result.passed
        assert "Unknown format" in result.message

    def test_evidence_limited(self):
        output = " ".join(
            f"u{i}@example.com" for i in range(10)
        )
        result = assert_has_format(output, "email", minimum=1)
        assert result.passed
        # Evidence should show at most 5
        assert result.evidence is not None
        assert result.evidence.count("@") <= 5

    def test_phone_us(self):
        output = "Call (408) 298-5437 or (650) 123-4567"
        result = assert_has_format(output, "phone_us", minimum=2)
        assert result.passed

    def test_url_format(self):
        output = (
            "See https://example.com and "
            "http://test.org/page"
        )
        result = assert_has_format(output, "url", minimum=2)
        assert result.passed


class TestAssertionKind:
    """US-001: AssertionResult.kind is required and enumerated."""

    def test_kind_required_typeerror(self):
        import pytest

        with pytest.raises(TypeError):
            AssertionResult(name="x", passed=True, message="m")  # type: ignore[call-arg]

    def test_contains_kind_presence(self):
        assert assert_contains("hello world", "hello").kind == "presence"

    def test_not_contains_kind_presence(self):
        assert assert_not_contains("abc", "zz").kind == "presence"

    def test_regex_kind_pattern(self):
        assert assert_regex("abc123", r"\d+").kind == "pattern"

    def test_min_count_kind_count(self):
        assert assert_min_count("a a a", r"a", 1).kind == "count"

    def test_min_length_kind_count(self):
        assert assert_min_length("hello", 1).kind == "count"

    def test_max_length_kind_count(self):
        assert assert_max_length("hi", 100).kind == "count"

    def test_has_urls_kind_count(self):
        assert assert_has_urls("https://example.com", 1).kind == "count"

    def test_has_entries_kind_count(self):
        assert assert_has_entries("**1. Foo**", 1).kind == "count"

    def test_urls_reachable_no_urls_kind_reachability(self):
        # Short-circuit branch when no URLs present still reports the
        # reachability kind — both code paths describe the same check.
        r = assert_urls_reachable("no urls here", 0)
        assert r.kind == "reachability"

    def test_urls_reachable_with_urls_kind_reachability(self):
        with patch(
            "clauditor.assertions._check_url",
            return_value=("https://example.com", 200),
        ):
            r = assert_urls_reachable("see https://example.com", 1)
        assert r.kind == "reachability"

    def test_has_format_unknown_kind_format(self):
        r = assert_has_format("x", "this_format_does_not_exist_xyz")
        assert r.kind == "format"

    def test_has_format_known_kind_count(self):
        r = assert_has_format("(408) 298-5437", "phone_us", minimum=1)
        assert r.kind == "count"

    def test_unknown_run_assertions_kind_custom(self):
        rs = run_assertions("t", [{"type": "nope"}])
        assert rs.results[0].kind == "custom"

    def test_all_enum_values_constructable(self):
        for k in (
            "presence",
            "format",
            "pattern",
            "count",
            "count_max",
            "reachability",
            "custom",
        ):
            r = AssertionResult(name="n", passed=True, message="m", kind=k)
            assert r.kind == k

    def test_grouped_summary_still_groups_by_name(self):
        """Regression: kind is supplementary; grouping key continues to use name."""
        s = AssertionSet(
            results=[
                AssertionResult(
                    name=f"section:S/default[{i}].website:format",
                    passed=False,
                    message="bad",
                    kind="format",
                    evidence="x",
                )
                for i in range(3)
            ]
        )
        lines = s.grouped_summary()
        assert len(lines) == 1
        assert "3/3" in lines[0]

    def test_summary_output_unchanged_regression(self):
        s = AssertionSet(
            results=[
                AssertionResult(
                    name="a", passed=True, message="ok", kind="presence"
                ),
                AssertionResult(
                    name="b", passed=False, message="no", kind="presence"
                ),
            ]
        )
        out = s.summary()
        assert "1/2" in out
        assert "FAIL: b" in out


class TestAssertionSetJson:
    """US-002: serialize AssertionSet to JSON keyed by stable spec ids."""

    def test_assertion_set_roundtrip_json(self):
        original = AssertionSet(
            results=[
                AssertionResult(
                    id="has-venues",
                    name="contains:Venues",
                    passed=True,
                    message="Found 'Venues'",
                    kind="presence",
                    evidence=None,
                ),
                AssertionResult(
                    id="min-length",
                    name="min_length>=100",
                    passed=False,
                    message="Length 12 (need >=100)",
                    kind="count",
                ),
            ],
            input_tokens=0,
            output_tokens=0,
        )
        payload = original.to_json()
        assert payload["results"][0]["id"] == "has-venues"
        assert payload["results"][1]["passed"] is False

        restored = AssertionSet.from_json(payload)
        assert len(restored.results) == 2
        assert restored.results[0].id == "has-venues"
        assert restored.results[0].passed is True
        assert restored.results[1].id == "min-length"
        assert restored.results[1].kind == "count"

    def test_assertion_set_json_uses_stable_id(self):
        """run_assertions stamps the spec ``id`` onto every result so
        assertions.json is keyed by id, not by list position."""
        assertions = [
            {"id": "venues", "type": "contains", "value": "Venues"},
            {"id": "min-len", "type": "min_length", "value": "10"},
        ]
        result_set = run_assertions(SAMPLE_OUTPUT, assertions)
        payload = result_set.to_json()
        ids = [r["id"] for r in payload["results"]]
        assert ids == ["venues", "min-len"]
        # And every result_set entry carries the id directly too.
        assert [r.id for r in result_set.results] == ["venues", "min-len"]


class TestTranscriptPathField:
    """US-002: AssertionResult.transcript_path round-trip."""

    def _sample(self, **overrides) -> AssertionResult:
        defaults = dict(
            name="contains:Venues",
            passed=True,
            message="Found 'Venues'",
            kind="presence",
            id="venues",
        )
        defaults.update(overrides)
        return AssertionResult(**defaults)

    def test_default_is_none(self):
        r = self._sample()
        assert r.transcript_path is None

    def test_to_json_dict_always_emits_key(self):
        r = self._sample()
        payload = r.to_json_dict()
        assert "transcript_path" in payload
        assert payload["transcript_path"] is None

    def test_round_trip_with_path_set(self):
        r = self._sample(transcript_path="runs/run-0/output.jsonl")
        payload = r.to_json_dict()
        assert payload["transcript_path"] == "runs/run-0/output.jsonl"
        restored = AssertionResult.from_json_dict(payload)
        assert restored.transcript_path == "runs/run-0/output.jsonl"
        assert restored.id == "venues"
        assert restored.kind == "presence"

    def test_round_trip_with_path_absent(self):
        r = self._sample()
        payload = r.to_json_dict()
        restored = AssertionResult.from_json_dict(payload)
        assert restored.transcript_path is None

    def test_from_json_dict_missing_key_tolerant(self):
        """Older fixtures lacking the key load with transcript_path=None."""
        legacy = {
            "id": "venues",
            "name": "contains:Venues",
            "passed": True,
            "message": "Found 'Venues'",
            "kind": "presence",
            "evidence": None,
            "raw_data": None,
            # no transcript_path key at all
        }
        restored = AssertionResult.from_json_dict(legacy)
        assert restored.transcript_path is None
        assert restored.id == "venues"

    def test_assertion_set_round_trip_threads_transcript_path(self):
        results = [
            AssertionResult(
                id="a1",
                name="contains:X",
                passed=True,
                message="ok",
                kind="presence",
                transcript_path="runs/run-0/output.jsonl",
            ),
            AssertionResult(
                id="a2",
                name="min_length>=5",
                passed=True,
                message="ok",
                kind="count",
                transcript_path=None,
            ),
        ]
        payload = AssertionSet(results=results).to_json()
        assert payload["results"][0]["transcript_path"] == (
            "runs/run-0/output.jsonl"
        )
        assert payload["results"][1]["transcript_path"] is None
        restored = AssertionSet.from_json(payload)
        assert restored.results[0].transcript_path == (
            "runs/run-0/output.jsonl"
        )
        assert restored.results[1].transcript_path is None

    def test_assertion_set_from_json_back_compat(self):
        """Legacy assertions.json without transcript_path loads cleanly."""
        legacy_payload = {
            "input_tokens": 0,
            "output_tokens": 0,
            "results": [
                {
                    "id": "a1",
                    "name": "contains:X",
                    "passed": True,
                    "message": "ok",
                    "kind": "presence",
                    "evidence": None,
                    "raw_data": None,
                },
            ],
        }
        restored = AssertionSet.from_json(legacy_payload)
        assert restored.results[0].transcript_path is None
        assert restored.results[0].id == "a1"
