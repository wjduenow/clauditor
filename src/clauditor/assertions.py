"""Layer 1: Deterministic assertions against skill output.

No API calls, no LLM — just regex, string matching, and counting.
"""

from __future__ import annotations

import ipaddress
import re
import socket
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Literal

AssertionKind = Literal[
    "presence",
    "format",
    "pattern",
    "count",
    "count_max",
    "reachability",
    "custom",
]


@dataclass
class AssertionResult:
    """Result of a single assertion check."""

    name: str
    passed: bool
    message: str
    kind: AssertionKind
    evidence: str | None = None
    raw_data: dict | None = None
    id: str | None = None
    transcript_path: str | None = None

    def __bool__(self) -> bool:
        return self.passed

    def to_json_dict(self) -> dict:
        """Return a JSON-safe dict for per-iteration persistence.

        Keyed on the stable spec ``id`` (DEC-001). ``name`` is retained as
        a secondary human label but the id is the load-bearing identifier
        for the assertion-auditor (US-002).
        """
        return {
            "id": self.id,
            "name": self.name,
            "passed": self.passed,
            "message": self.message,
            "kind": self.kind,
            "evidence": self.evidence,
            "raw_data": self.raw_data,
            "transcript_path": self.transcript_path,
        }

    @classmethod
    def from_json_dict(cls, data: dict) -> AssertionResult:
        """Inverse of :meth:`to_json_dict`."""
        return cls(
            id=data.get("id"),
            name=data["name"],
            passed=bool(data["passed"]),
            message=data["message"],
            kind=data["kind"],
            evidence=data.get("evidence"),
            raw_data=data.get("raw_data"),
            transcript_path=data["transcript_path"],
        )


@dataclass
class AssertionSet:
    """A collection of assertion results from checking skill output."""

    results: list[AssertionResult] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0

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

    def to_json(self) -> dict:
        """Return a JSON-safe dict for persistence in ``assertions.json``.

        The top-level dict carries ``input_tokens``/``output_tokens`` so
        Layer 1 cost can be reconstructed from disk, plus a ``results``
        list where each entry is an :meth:`AssertionResult.to_json_dict`
        (keyed by the stable spec id from DEC-001).
        """
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "results": [r.to_json_dict() for r in self.results],
        }

    @classmethod
    def from_json(cls, data: dict) -> AssertionSet:
        """Inverse of :meth:`to_json` — used by tests and the auditor."""
        results = [
            AssertionResult.from_json_dict(r) for r in data.get("results", [])
        ]
        return cls(
            results=results,
            input_tokens=int(data.get("input_tokens", 0) or 0),
            output_tokens=int(data.get("output_tokens", 0) or 0),
        )

    def grouped_summary(self) -> list[str]:
        """Collapse repeated field/suffix failures into one line per group.

        Structured assertion names of form
        ``section:{Section}/{tier}[{i}].{field}[:{suffix}]`` are grouped by
        ``(section, tier, field, suffix_or_"presence")`` so that N identical
        failures across entries surface as a single summary line. Non-structured
        names pass through unchanged.
        """
        structured_re = re.compile(
            r"section:([^/]+)/([^\[]+)\[(\d+)\]\.([^:]+?)(?::(.+))?$"
        )
        groups: dict[tuple, dict] = {}
        order: list[tuple] = []
        passthrough: list[AssertionResult] = []

        for r in self.results:
            m = structured_re.fullmatch(r.name)
            if not m:
                passthrough.append(r)
                continue
            section, tier, _idx, fld, suffix = m.groups()
            key = (section, tier, fld, suffix or "presence")
            if key not in groups:
                groups[key] = {"total": 0, "failed": 0, "first_fail": None}
                order.append(key)
            g = groups[key]
            g["total"] += 1
            if not r.passed:
                g["failed"] += 1
                if g["first_fail"] is None:
                    g["first_fail"] = r

        lines: list[str] = []
        for key in order:
            g = groups[key]
            if g["failed"] == 0:
                continue
            r = g["first_fail"]
            detail = r.evidence if r.evidence else r.message
            section, tier, fld, suffix = key
            lines.append(
                f"{g['failed']}/{g['total']} {section}/{tier}[*].{fld}:{suffix} "
                f"failed: {detail}"
            )
        for r in passthrough:
            if not r.passed:
                lines.append(f"FAIL: {r.name} — {r.message}")
        return lines


def assert_contains(output: str, value: str) -> AssertionResult:
    """Check that output contains a substring."""
    found = value in output
    return AssertionResult(
        name=f"contains:{value[:40]}",
        passed=found,
        message=f"Found '{value[:40]}'" if found else f"Missing '{value[:40]}'",
        kind="presence",
    )


def assert_not_contains(output: str, value: str) -> AssertionResult:
    """Check that output does NOT contain a substring."""
    found = value in output
    return AssertionResult(
        name=f"not_contains:{value[:40]}",
        passed=not found,
        message="Correctly absent" if not found else f"Unexpected '{value[:40]}' found",
        kind="presence",
    )


def assert_regex(output: str, pattern: str) -> AssertionResult:
    """Check that output matches a regex pattern."""
    match = re.search(pattern, output)
    return AssertionResult(
        name=f"regex:{pattern[:40]}",
        passed=match is not None,
        message="Pattern matched" if match else f"Pattern not found: {pattern[:40]}",
        kind="pattern",
        evidence=match.group(0)[:100] if match else None,
    )


def assert_min_count(output: str, pattern: str, minimum: int) -> AssertionResult:
    """Check that a pattern appears at least N times."""
    matches = re.findall(pattern, output)
    count = len(matches)
    return AssertionResult(
        name=f"min_count:{pattern[:30]}>={minimum}",
        passed=count >= minimum,
        message=f"Found {count} matches (need >={minimum})",
        kind="count",
    )


def assert_min_length(output: str, minimum: int) -> AssertionResult:
    """Check that output is at least N characters."""
    length = len(output)
    return AssertionResult(
        name=f"min_length>={minimum}",
        passed=length >= minimum,
        message=f"Length {length} (need >={minimum})",
        kind="count",
    )


def assert_max_length(output: str, maximum: int) -> AssertionResult:
    """Check that output is at most N characters."""
    length = len(output)
    return AssertionResult(
        name=f"max_length<={maximum}",
        passed=length <= maximum,
        message=f"Length {length} (need <={maximum})",
        kind="count",
    )


def assert_has_urls(output: str, minimum: int = 1) -> AssertionResult:
    """Check that output contains at least N URLs."""
    urls = re.findall(r"https?://[^\s\)\"'>]+", output)
    count = len(urls)
    return AssertionResult(
        name=f"has_urls>={minimum}",
        passed=count >= minimum,
        message=f"Found {count} URLs (need >={minimum})",
        kind="count",
        evidence="; ".join(urls[:5]) if urls else None,
    )


def assert_has_entries(output: str, minimum: int = 1) -> AssertionResult:
    """Check that output contains numbered entries (e.g., **1. Name**)."""
    entries = re.findall(r"\*\*\d+\.\s+", output)
    count = len(entries)
    return AssertionResult(
        name=f"has_entries>={minimum}",
        passed=count >= minimum,
        message=f"Found {count} numbered entries (need >={minimum})",
        kind="count",
    )


def _is_private_ip(hostname: str) -> bool:
    """Check if a hostname resolves to a non-global IP address."""
    try:
        infos = socket.getaddrinfo(hostname, None, socket.AF_UNSPEC)
        for info in infos:
            addr = ipaddress.ip_address(info[4][0])
            if not addr.is_global:
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


_MAX_REDIRECTS = 5


def _check_url(url: str, timeout: int = 5) -> tuple[str, int | str]:
    """Send HEAD request with SSRF-safe redirect following."""
    try:
        import httpx
    except ImportError:
        httpx = None  # type: ignore[assignment]

    if httpx is not None:
        current = url
        try:
            for _ in range(_MAX_REDIRECTS + 1):
                resp = httpx.head(
                    current, timeout=timeout, follow_redirects=False
                )
                if 300 <= resp.status_code < 400:
                    location = resp.headers.get("location")
                    if not location:
                        return (url, resp.status_code)
                    next_url = urllib.parse.urljoin(current, location)
                    if not _is_safe_url(next_url):
                        return (url, "blocked")
                    current = next_url
                    continue
                return (url, resp.status_code)
            return (url, "TooManyRedirects")
        except Exception as e:
            return (url, type(e).__name__)

    class _NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, hdrs, newurl):
            return None

    opener = urllib.request.build_opener(_NoRedirect)
    current = url
    try:
        for _ in range(_MAX_REDIRECTS + 1):
            req = urllib.request.Request(current, method="HEAD")
            try:
                with opener.open(req, timeout=timeout) as resp:
                    return (url, resp.status)
            except urllib.error.HTTPError as e:
                if 300 <= e.code < 400:
                    location = e.headers.get("Location")
                    if not location:
                        return (url, e.code)
                    next_url = urllib.parse.urljoin(
                        current, location
                    )
                    if not _is_safe_url(next_url):
                        return (url, "blocked")
                    current = next_url
                    continue
                return (url, e.code)
        return (url, "TooManyRedirects")
    except Exception as e:
        return (url, type(e).__name__)


_MAX_URL_CHECKS = 20


def assert_urls_reachable(output: str, minimum: int = 1) -> AssertionResult:
    """Check that at least N URLs in the output are reachable (HTTP 2xx)."""
    urls = list(dict.fromkeys(re.findall(r"https?://[^\s\)\"'>]+", output)))
    if not urls:
        return AssertionResult(
            name=f"urls_reachable>={minimum}",
            passed=0 >= minimum,
            message=f"Found 0 URLs to check (need >={minimum})",
            kind="reachability",
        )

    statuses: list[str] = []
    reachable = 0
    check_urls = urls[:_MAX_URL_CHECKS]
    for url in check_urls:
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
        name=f"urls_reachable>={minimum}",
        passed=reachable >= minimum,
        message=f"{reachable}/{len(urls)} URLs reachable (need >={minimum})",
        kind="reachability",
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
            kind="format",
        )

    matches = fmt.extract_pattern.findall(output)
    count = len(matches)
    return AssertionResult(
        name=f"has_format:{format_name}>={minimum}",
        passed=count >= minimum,
        message=f"Found {count} {format_name} matches (need >={minimum})",
        kind="count",
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
        spec_id = a.get("id")
        before = len(results.results)

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
                    kind="custom",
                )
            )

        # Stamp the stable spec id onto every result produced by this
        # assertion dict (US-002). Most assertion dicts produce exactly
        # one result, but loop defensively across any added since
        # `before` in case helpers grow to emit multiple.
        if spec_id is not None:
            for r in results.results[before:]:
                r.id = spec_id

    return results
