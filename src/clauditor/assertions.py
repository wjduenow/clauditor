"""Layer 1: Deterministic assertions against skill output.

No API calls, no LLM — just regex, string matching, and counting.
"""

from __future__ import annotations

import ipaddress
import re
import socket
import urllib.parse
import urllib.request
from dataclasses import dataclass, field


@dataclass
class AssertionResult:
    """Result of a single assertion check."""

    name: str
    passed: bool
    message: str
    evidence: str | None = None

    def __bool__(self) -> bool:
        return self.passed


@dataclass
class AssertionSet:
    """A collection of assertion results from checking skill output."""

    results: list[AssertionResult] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(r.passed for r in self.results)

    @property
    def failed(self) -> list[AssertionResult]:
        return [r for r in self.results if not r.passed]

    @property
    def pass_rate(self) -> float:
        if not self.results:
            return 0.0
        return sum(1 for r in self.results if r.passed) / len(self.results)

    def summary(self) -> str:
        total = len(self.results)
        passed = sum(1 for r in self.results if r.passed)
        lines = [f"{passed}/{total} assertions passed ({self.pass_rate:.0%})"]
        for r in self.failed:
            lines.append(f"  FAIL: {r.name} — {r.message}")
        return "\n".join(lines)


def assert_contains(output: str, value: str) -> AssertionResult:
    """Check that output contains a substring."""
    found = value in output
    return AssertionResult(
        name=f"contains:{value[:40]}",
        passed=found,
        message=f"Found '{value[:40]}'" if found else f"Missing '{value[:40]}'",
    )


def assert_not_contains(output: str, value: str) -> AssertionResult:
    """Check that output does NOT contain a substring."""
    found = value in output
    return AssertionResult(
        name=f"not_contains:{value[:40]}",
        passed=not found,
        message="Correctly absent" if not found else f"Unexpected '{value[:40]}' found",
    )


def assert_regex(output: str, pattern: str) -> AssertionResult:
    """Check that output matches a regex pattern."""
    match = re.search(pattern, output)
    return AssertionResult(
        name=f"regex:{pattern[:40]}",
        passed=match is not None,
        message="Pattern matched" if match else f"Pattern not found: {pattern[:40]}",
        evidence=match.group(0)[:100] if match else None,
    )


def assert_min_count(output: str, pattern: str, minimum: int) -> AssertionResult:
    """Check that a pattern appears at least N times."""
    matches = re.findall(pattern, output)
    count = len(matches)
    return AssertionResult(
        name=f"min_count:{pattern[:30]}≥{minimum}",
        passed=count >= minimum,
        message=f"Found {count} matches (need ≥{minimum})",
    )


def assert_min_length(output: str, minimum: int) -> AssertionResult:
    """Check that output is at least N characters."""
    length = len(output)
    return AssertionResult(
        name=f"min_length≥{minimum}",
        passed=length >= minimum,
        message=f"Length {length} (need ≥{minimum})",
    )


def assert_max_length(output: str, maximum: int) -> AssertionResult:
    """Check that output is at most N characters."""
    length = len(output)
    return AssertionResult(
        name=f"max_length≤{maximum}",
        passed=length <= maximum,
        message=f"Length {length} (need ≤{maximum})",
    )


def assert_has_urls(output: str, minimum: int = 1) -> AssertionResult:
    """Check that output contains at least N URLs."""
    urls = re.findall(r"https?://[^\s\)\"'>]+", output)
    count = len(urls)
    return AssertionResult(
        name=f"has_urls≥{minimum}",
        passed=count >= minimum,
        message=f"Found {count} URLs (need ≥{minimum})",
        evidence="; ".join(urls[:5]) if urls else None,
    )


def assert_has_entries(output: str, minimum: int = 1) -> AssertionResult:
    """Check that output contains numbered entries (e.g., **1. Name**)."""
    entries = re.findall(r"\*\*\d+\.\s+", output)
    count = len(entries)
    return AssertionResult(
        name=f"has_entries≥{minimum}",
        passed=count >= minimum,
        message=f"Found {count} numbered entries (need ≥{minimum})",
    )


def _is_private_ip(hostname: str) -> bool:
    """Check if a hostname resolves to a private/reserved IP address."""
    try:
        infos = socket.getaddrinfo(hostname, None, socket.AF_UNSPEC)
        for info in infos:
            addr = ipaddress.ip_address(info[4][0])
            if addr.is_private or addr.is_loopback or addr.is_link_local:
                return True
    except (socket.gaierror, ValueError):
        return True  # Can't resolve = treat as unsafe
    return False


def _is_safe_url(url: str) -> bool:
    """Check if a URL is safe to request (no SSRF risk)."""
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return False
    hostname = parsed.hostname
    if not hostname:
        return False
    return not _is_private_ip(hostname)


def _check_url(url: str, timeout: int = 5) -> tuple[str, int | str]:
    """Send HEAD request, return (url, status_code_or_error_string)."""
    try:
        import httpx

        resp = httpx.head(url, timeout=timeout, follow_redirects=True)
        return (url, resp.status_code)
    except ImportError:
        pass
    except Exception:
        pass  # Fall through to urllib

    try:
        req = urllib.request.Request(url, method="HEAD")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return (url, resp.status)
    except urllib.error.HTTPError as e:
        return (url, e.code)
    except Exception as e:
        return (url, type(e).__name__)


def assert_urls_reachable(output: str, minimum: int = 1) -> AssertionResult:
    """Check that at least N URLs in the output are reachable (HTTP 2xx)."""
    urls = re.findall(r"https?://[^\s\)\"'>]+", output)
    if not urls:
        return AssertionResult(
            name=f"urls_reachable≥{minimum}",
            passed=0 >= minimum,
            message=f"Found 0 URLs to check (need ≥{minimum})",
        )

    statuses: list[str] = []
    reachable = 0
    for url in urls:
        if not _is_safe_url(url):
            statuses.append(f"{url}: blocked")
            continue
        url, status = _check_url(url)
        if isinstance(status, int) and 200 <= status < 300:
            reachable += 1
            statuses.append(f"{url}: {status}")
        else:
            statuses.append(f"{url}: {status}")

    return AssertionResult(
        name=f"urls_reachable≥{minimum}",
        passed=reachable >= minimum,
        message=f"{reachable}/{len(urls)} URLs reachable (need ≥{minimum})",
        evidence="; ".join(statuses[:5]),
    )


def assert_has_format(
    output: str, format_name: str, minimum: int = 1,
) -> AssertionResult:
    """Check that output contains at least N matches of a named format."""
    from clauditor.formats import get_format

    fmt = get_format(format_name)
    if fmt is None:
        return AssertionResult(
            name=f"has_format:{format_name}",
            passed=False,
            message=f"Unknown format: {format_name}",
        )

    matches = fmt.extract_pattern.findall(output)
    count = len(matches)
    return AssertionResult(
        name=f"has_format:{format_name}≥{minimum}",
        passed=count >= minimum,
        message=f"Found {count} {format_name} matches (need ≥{minimum})",
        evidence="; ".join(str(m) for m in matches[:5]) if matches else None,
    )


def run_assertions(output: str, assertions: list[dict]) -> AssertionSet:
    """Run a list of assertion dicts against output.

    Each dict has: {"type": "contains", "value": "Venues"} etc.
    Supported types: contains, not_contains, regex, min_count,
    min_length, max_length, has_urls, has_entries.
    """
    results = AssertionSet()
    for a in assertions:
        atype = a["type"]
        value = a.get("value", "")

        if atype == "contains":
            results.results.append(assert_contains(output, value))
        elif atype == "not_contains":
            results.results.append(assert_not_contains(output, value))
        elif atype == "regex":
            results.results.append(assert_regex(output, value))
        elif atype == "min_count":
            results.results.append(assert_min_count(output, value, a.get("minimum", 1)))
        elif atype == "min_length":
            results.results.append(assert_min_length(output, int(value)))
        elif atype == "max_length":
            results.results.append(assert_max_length(output, int(value)))
        elif atype == "has_urls":
            results.results.append(assert_has_urls(output, int(value) if value else 1))
        elif atype == "has_entries":
            results.results.append(
                assert_has_entries(output, int(value) if value else 1)
            )
        elif atype == "urls_reachable":
            results.results.append(
                assert_urls_reachable(output, int(value) if value else 1)
            )
        elif atype == "has_format":
            results.results.append(
                assert_has_format(
                    output, a.get("format", ""), int(value) if value else 1
                )
            )
        else:
            results.results.append(
                AssertionResult(
                    name=f"unknown:{atype}",
                    passed=False,
                    message=f"Unknown assertion type: {atype}",
                )
            )

    return results
