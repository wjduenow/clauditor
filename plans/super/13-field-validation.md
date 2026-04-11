# Super Plan: #13 — Field-Level Validation Assertions

## Meta
- **Ticket:** https://github.com/wjduenow/clauditor/issues/13
- **Branch:** `feature/13-field-validation`
- **Phase:** `detailing`
- **Sessions:** 1
- **Last session:** 2026-04-10

---

## Discovery

### Ticket Summary

**What:** Add field-level validation capabilities to strengthen output validation without LLM costs. The ticket specifically requests:
1. A `urls_reachable` assertion (Layer 1) — HTTP HEAD check that extracted URLs resolve
2. Field pattern enforcement (Layer 2) — validate each extracted field value against `FieldRequirement.pattern` regex

**Why:** Current validation has a gap between Layer 1 (whole-output string matching) and Layer 3 (expensive LLM grading). URLs can be syntactically valid but 404; phone fields can contain garbage that passes whole-output regex checks. These are cheap, deterministic checks that belong in Layers 1-2.

**User extension:** The user wants a **generalized data-type validation system** — not just phone/URL but a broad set of recognizable output formats (email, date, currency, etc.) that can be validated cheaply.

### Codebase Findings

**Layer 1 — assertions.py (179 lines)**
- 8 assertion types: `contains`, `not_contains`, `regex`, `min_count`, `min_length`, `max_length`, `has_urls`, `has_entries`
- Pattern: each type is a standalone function returning `AssertionResult`, dispatched via if-elif chain in `run_assertions()` (line 139)
- `has_urls` already extracts URLs via regex (line 118: `r"https?://[^\s\)\"'>]+"`) but only counts them — no reachability check
- Zero external dependencies (no `requests`, no `httpx`, no `aiohttp`)

**Layer 2 — grader.py (221 lines)**
- `grade_extraction()` (line 81) validates extracted data against schema
- Currently only checks field presence via `entry.has_field()` (line 109)
- `FieldRequirement.pattern` exists in schemas.py (line 19) but is **never enforced** — the field is loaded from JSON and serialized back, but grader.py ignores it entirely
- Pattern enforcement insertion point: lines 104-124, after the `has_value` check

**schemas.py (236 lines)**
- `FieldRequirement` (line 14): `name`, `required`, `pattern` (optional regex string)
- No `type` or `format` field exists yet — pattern is the only validation mechanism

**Dependencies (pyproject.toml)**
- Zero runtime dependencies; `anthropic` is optional (grader extra)
- No HTTP client library in deps — `urls_reachable` needs one

### Proposed Scope

The ticket asks for two things; the user asks us to think broader. I propose three work streams:

1. **URL reachability** — New Layer 1 assertion `urls_reachable` that HTTP-HEAD-checks URLs
2. **Field pattern enforcement** — Wire up the existing `FieldRequirement.pattern` in `grade_extraction()`
3. **Data format validators** — A registry of named format patterns (email, phone, date, currency, URL, etc.) that can be referenced from `FieldRequirement` by name instead of inline regex, usable in both Layer 1 (whole-output format scanning) and Layer 2 (per-field validation)

### Scoping Questions

**Q1: HTTP client for URL reachability**
The project currently has zero runtime dependencies. Adding `httpx` or `aiohttp` would be the first.

- **(A)** Use stdlib `urllib.request` — no new dependency, synchronous, limited but sufficient for HEAD requests
- **(B)** Add `httpx` as an optional dependency (like anthropic) — async-native, production-grade, but adds a dep
- **(C)** Add `aiohttp` as optional — async, lighter than httpx, but less ergonomic
- **(D)** Use `urllib` as default, with optional `httpx` if installed — best of both

**Q2: Scope of format validators**
How broad should the built-in format registry be?

- **(A)** Minimal — just the formats mentioned in the ticket: `phone_us`, `url` (4-5 formats)
- **(B)** Moderate — common structured data types: `phone_us`, `email`, `url`, `date_iso`, `currency_usd`, `zip_us`, `time_24h`, `percentage` (~8-10 formats)
- **(C)** Broad — everything in (B) plus international variants and domain patterns: `phone_intl`, `date_various`, `currency_intl`, `ipv4`, `ipv6`, `uuid`, `hex_color`, `latitude`, `longitude`, `ssn_masked` (~15-20 formats)
- **(D)** Extensible — ship (B) as built-ins plus let eval specs register custom named patterns

**Q3: How should named formats integrate with FieldRequirement?**

- **(A)** New `format` field on `FieldRequirement` — `{"name": "phone", "format": "phone_us"}` — separate from `pattern` (regex). `format` is a named preset; `pattern` is custom regex. If both set, both must match.
- **(B)** Overload `pattern` — allow `pattern` to accept either a regex string or a format name like `"phone_us"`. Detect by prefix or registry lookup.
- **(C)** Replace `pattern` with `format` entirely — `format` accepts either a named preset or an inline regex (auto-detected by trying registry first, falling back to regex)

**Q4: Layer 1 format-scanning assertions**
Beyond `has_urls` and `urls_reachable`, should we add format-aware counting assertions?

- **(A)** Yes — add `has_emails`, `has_phones`, `has_dates`, etc. as distinct assertion types
- **(B)** Yes, but generalized — add a single `has_format` assertion type: `{"type": "has_format", "format": "email", "value": "3"}` that uses the format registry
- **(C)** No — keep Layer 1 for raw string checks, let Layer 2 handle format validation per-field

**Q5: URL reachability — failure semantics**
When a URL returns non-2xx or times out:

- **(A)** Binary pass/fail — count reachable URLs, fail if below threshold
- **(B)** Graded — report per-URL status (2xx, 3xx redirect, 4xx, 5xx, timeout) in evidence, let user set which codes count as "reachable"
- **(C)** Start with (A), make (B) a follow-up issue

### Scoping Answers

| Q | Answer | Decision |
|---|--------|----------|
| Q1 | **(D)** | `urllib.request` default, optional `httpx` if installed |
| Q2 | **(C)** | Broad format registry (~15-20 built-in formats) |
| Q3 | **(A)** | New `format` field on `FieldRequirement`, separate from `pattern` |
| Q4 | **(B)** | Generalized `has_format` assertion: `{"type": "has_format", "format": "email", "value": "3"}` |
| Q5 | **(C)** | Binary pass/fail now, graded per-URL status as follow-up |

---

## Architecture Review

### DEC-001: Zero-dep HTTP with optional upgrade
`urls_reachable` uses `urllib.request.urlopen` (HEAD) by default. If `httpx` is installed, uses `httpx.head()` instead. No new required dependency added to pyproject.toml. `httpx` could optionally be added to `[project.optional-dependencies]` for discoverability.

### DEC-002: Format registry as module
New `formats.py` module containing a `FORMAT_REGISTRY: dict[str, FormatDef]` mapping format names to `FormatDef(name, pattern, description)`. ~15-20 built-in entries. Pure data + one lookup function. No classes needed beyond the dataclass.

### DEC-003: FieldRequirement gains `format` field
`format: str | None = None` added alongside existing `pattern`. `format` references a registry name; `pattern` is inline regex. If both set, both must match. Schema loading and serialization updated.

### DEC-004: Generalized `has_format` assertion
Single new assertion type in Layer 1: `{"type": "has_format", "format": "email", "value": "3"}`. Looks up format in registry, runs its regex against full output, counts matches. Replaces the need for individual `has_emails`, `has_phones`, etc.

### DEC-005: Pattern enforcement in grade_extraction
After the existing `has_value` check (grader.py:109), add pattern/format validation. If `field_req.format` is set, resolve to regex from registry. If `field_req.pattern` is set, use it directly. Apply `re.fullmatch()` against the extracted field value. Failed matches produce an AssertionResult with the value as evidence.

### Review Ratings

| Area | Rating | Key Finding |
|------|--------|-------------|
| Security | **CONCERN** | SSRF risk in `urls_reachable` (private IPs, metadata endpoints); ReDoS risk from user-supplied regex in `pattern` field |
| Performance | **PASS** | HTTP HEAD with timeout is lightweight; regex on short field values is negligible |
| Data Model | **PASS** | `format` field is backward compatible; must update `to_dict()` serialization |
| API Design | **PASS** | New assertion formats consistent with existing style |
| Observability | **PASS** | AssertionResult evidence field already carries diagnostic data |
| Testing | **CONCERN** | `urls_reachable` needs HTTP mocking; no mocking lib in deps (stdlib `unittest.mock` sufficient) |

### Security Mitigations Required

1. **SSRF protection for `urls_reachable`:**
   - Block private IP ranges (RFC1918: 10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16)
   - Block loopback (127.0.0.0/8) and link-local (169.254.0.0/16)
   - Reject `file://` scheme — only allow `http://` and `https://`
   - Limit redirects (max 3, same-scheme only)
   - Set socket timeout (5s)

2. **ReDoS protection for `pattern` field:**
   - Use `re.fullmatch()` with a compilation-time check
   - Set a reasonable timeout via signal or thread-based guard for pathological patterns
   - Named `format` patterns in registry are pre-validated (safe by construction)

### Serialization Fix

`schemas.py` `to_dict()` must serialize the new `format` field alongside `pattern`:
```python
**({"format": f.format} if f.format else {}),
```

---

## Refinement Log

### Session 1 — 2026-04-10

**DEC-006: ReDoS mitigation — pragmatic approach**
Pre-compile patterns at load time; let `re.error` surface invalid regex. No timeout wrapper. Named `format` patterns are safe by construction (pre-validated in registry). Inline `pattern` is the eval author's responsibility. Rationale: eval specs are developer-authored, not untrusted input.

**DEC-007: SSRF mitigation for `urls_reachable`**
Block private IPs (RFC1918, loopback, link-local, metadata endpoints), reject non-HTTP(S) schemes, 5s timeout, max 3 redirects. This is defensive because skill output *is* LLM-generated and could contain internal URLs via hallucination or prompt injection.

**Resolved concerns:**
- Security (ReDoS): resolved via DEC-006 — pragmatic, no timeout
- Security (SSRF): resolved via DEC-007 — denylist + timeout
- Testing (HTTP mocking): use stdlib `unittest.mock.patch` on `urllib.request.urlopen`
- Data model (serialization): add `format` to `to_dict()` alongside `pattern`

## Detailed Breakdown

### US-001: Format Registry Module

**Description:** Create `formats.py` with a `FORMAT_REGISTRY` mapping ~15-20 named format patterns. Each entry is a `FormatDef` dataclass with `name`, `pattern` (compiled regex), `description`, and an `extract_pattern` (for Layer 1 scanning, may differ from the strict `pattern` used for fullmatch validation).

**Traces to:** DEC-002, DEC-006

**Built-in formats:**
- `phone_us` — `\(\d{3}\) \d{3}-\d{4}`
- `phone_intl` — `\+\d{1,3}[\s-]?\d{4,14}`
- `email` — `[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}`
- `url` — `https?://[^\s\)\"'>]+`
- `date_iso` — `\d{4}-\d{2}-\d{2}`
- `date_us` — `\d{1,2}/\d{1,2}/\d{2,4}`
- `time_24h` — `\d{1,2}:\d{2}(:\d{2})?`
- `time_12h` — `\d{1,2}:\d{2}\s?[AaPp][Mm]`
- `currency_usd` — `\$[\d,]+(\.\d{2})?`
- `currency_eur` — `€[\d.,]+`
- `zip_us` — `\d{5}(-\d{4})?`
- `zip_uk` — `[A-Z]{1,2}\d[A-Z\d]?\s?\d[A-Z]{2}`
- `percentage` — `\d+(\.\d+)?%`
- `ipv4` — `\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}`
- `uuid` — `[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}`
- `hex_color` — `#[0-9a-fA-F]{6}`
- `latitude` — `-?\d{1,2}\.\d+`
- `longitude` — `-?\d{1,3}\.\d+`
- `star_rating` — `\d(\.\d)?\s?(stars?|★|⭐)`

Public API: `get_format(name) -> FormatDef | None`, `list_formats() -> list[str]`, `validate_value(value, format_name) -> bool`.

**Acceptance Criteria:**
- [ ] `FormatDef` dataclass with `name`, `pattern`, `description`, `extract_pattern`
- [ ] `FORMAT_REGISTRY` dict with ~15-20 entries
- [ ] `get_format()`, `list_formats()`, `validate_value()` functions
- [ ] All patterns pre-compiled at import time; invalid patterns raise immediately
- [ ] `uv run ruff check src/ tests/` passes
- [ ] `uv run pytest --cov=clauditor --cov-report=term-missing` passes

**Done when:** `from clauditor.formats import get_format; get_format("email")` returns a valid `FormatDef` and `validate_value("test@example.com", "email")` returns `True`.

**Files:**
- `src/clauditor/formats.py` (new)
- `tests/test_formats.py` (new)

**Depends on:** none

**TDD:**
- `test_get_format_valid` — known format returns FormatDef
- `test_get_format_invalid` — unknown name returns None
- `test_list_formats` — returns sorted list of all names
- `test_validate_value_match` — valid values for each format
- `test_validate_value_no_match` — invalid values rejected
- `test_all_patterns_compile` — ensure no regex errors at import

---

### US-002: `format` Field on FieldRequirement + Schema Loading

**Description:** Add `format: str | None = None` to `FieldRequirement`. Update `from_file()` to load it from JSON and `to_dict()` to serialize it. Backward compatible — existing eval.json files without `format` continue to work.

**Traces to:** DEC-003

**Acceptance Criteria:**
- [ ] `FieldRequirement` has `format` field defaulting to `None`
- [ ] `EvalSpec.from_file()` reads `format` from field dicts
- [ ] `EvalSpec.to_dict()` serializes `format` when present (conditional, like `pattern`)
- [ ] Round-trip test: load JSON with `format`, serialize back, verify preserved
- [ ] Existing tests continue to pass (backward compat)
- [ ] `uv run pytest --cov=clauditor --cov-report=term-missing` passes

**Done when:** `FieldRequirement(name="phone", format="phone_us")` serializes to `{"name": "phone", "required": true, "format": "phone_us"}` and loads back identically.

**Files:**
- `src/clauditor/schemas.py` (modify: FieldRequirement, from_file, to_dict)
- `tests/test_schemas.py` (add format round-trip tests)

**Depends on:** none

---

### US-003: Field Pattern + Format Enforcement in grade_extraction

**Description:** After the existing `has_value` check in `grade_extraction()` (grader.py:109), add validation against `field_req.pattern` (inline regex) and `field_req.format` (registry lookup). Use `re.fullmatch()` for strict matching. If both are set, both must match. Failed matches produce an `AssertionResult` with the extracted value as evidence.

**Traces to:** DEC-003, DEC-005, DEC-006

**Acceptance Criteria:**
- [ ] When `field_req.pattern` is set and field has a value, `re.fullmatch(pattern, value)` is applied
- [ ] When `field_req.format` is set, format is resolved via `get_format()` and its pattern applied via `re.fullmatch()`
- [ ] If both `pattern` and `format` are set, both must match (AND logic)
- [ ] Failed pattern/format produces `AssertionResult(passed=False)` with value as evidence
- [ ] Invalid `format` name produces a failing assertion (not a crash)
- [ ] Invalid `pattern` regex produces a failing assertion with `re.error` message
- [ ] Assertion names follow convention: `section:{Name}/{tier}[{i}].{field}:pattern` and `:format`
- [ ] `uv run pytest --cov=clauditor --cov-report=term-missing` passes

**Done when:** A FieldRequirement with `pattern=r"\(\d{3}\) \d{3}-\d{4}"` correctly fails an entry where phone is `"call for hours"` and passes one where phone is `"(408) 298-5437"`.

**Files:**
- `src/clauditor/grader.py` (modify: grade_extraction)
- `tests/test_grader.py` (add pattern/format enforcement tests)

**Depends on:** US-001 (format registry), US-002 (format field on FieldRequirement)

**TDD:**
- `test_grade_extraction_pattern_match` — value matches inline pattern → pass
- `test_grade_extraction_pattern_mismatch` — value doesn't match → fail with evidence
- `test_grade_extraction_format_match` — value matches named format → pass
- `test_grade_extraction_format_mismatch` — value doesn't match → fail
- `test_grade_extraction_format_and_pattern` — both set, both must pass
- `test_grade_extraction_unknown_format` — unknown name → fail assertion
- `test_grade_extraction_invalid_pattern` — bad regex → fail assertion
- `test_grade_extraction_pattern_on_optional_missing` — optional field missing, pattern set → skip (no fail)

---

### US-004: `has_format` Layer 1 Assertion

**Description:** Add a generalized `has_format` assertion type to Layer 1. Spec format: `{"type": "has_format", "format": "email", "value": "3"}`. Looks up the format's `extract_pattern` in the registry, runs `re.findall()` against the full output, counts matches. Fails if fewer than the threshold.

**Traces to:** DEC-004

**Acceptance Criteria:**
- [ ] New `assert_has_format(output, format_name, minimum)` function in assertions.py
- [ ] Dispatched from `run_assertions()` for type `"has_format"`
- [ ] Uses `extract_pattern` from format registry (not `pattern`, which is for fullmatch)
- [ ] Unknown format name returns a failing assertion (not a crash)
- [ ] Assertion name: `has_format:{format_name}≥{minimum}`
- [ ] Evidence: first 5 matches joined by "; "
- [ ] `uv run pytest --cov=clauditor --cov-report=term-missing` passes

**Done when:** `{"type": "has_format", "format": "email", "value": "2"}` against output containing 3 email addresses passes with count 3.

**Files:**
- `src/clauditor/assertions.py` (modify: add function + dispatch case)
- `tests/test_assertions.py` (add TestHasFormat class)

**Depends on:** US-001 (format registry)

**TDD:**
- `test_has_format_found` — output has enough matches → pass
- `test_has_format_insufficient` — output has fewer than threshold → fail
- `test_has_format_unknown` — unknown format name → fail
- `test_has_format_evidence` — evidence contains first 5 matches
- `test_has_format_via_run_assertions` — end-to-end through dispatcher

---

### US-005: `urls_reachable` Layer 1 Assertion

**Description:** Add `urls_reachable` assertion to Layer 1. Extracts URLs from output (reusing `has_urls` regex), sends HTTP HEAD requests with SSRF protections, counts 2xx responses. Uses `urllib.request` by default, `httpx` if installed. Spec: `{"type": "urls_reachable", "value": "3"}`.

**Traces to:** DEC-001, DEC-007

**Acceptance Criteria:**
- [ ] New `assert_urls_reachable(output, minimum)` function in assertions.py
- [ ] Dispatched from `run_assertions()` for type `"urls_reachable"`
- [ ] SSRF protections: block private IPs (10/8, 172.16/12, 192.168/16), loopback (127/8), link-local (169.254/16), metadata (169.254.169.254)
- [ ] Only `http://` and `https://` schemes allowed
- [ ] 5-second timeout per request
- [ ] Max 3 redirects
- [ ] Uses `httpx.head()` if httpx installed, else `urllib.request.urlopen` with HEAD method
- [ ] Evidence: per-URL status ("url: 200", "url: timeout", "url: blocked")
- [ ] Assertion name: `urls_reachable≥{minimum}`
- [ ] `uv run pytest --cov=clauditor --cov-report=term-missing` passes

**Done when:** Output with 3 real URLs and 1 fake URL, threshold 3 → passes if 3 return 2xx. Blocked IPs (127.0.0.1, 169.254.169.254) never receive requests.

**Files:**
- `src/clauditor/assertions.py` (modify: add function + dispatch case + SSRF helper)
- `tests/test_assertions.py` (add TestUrlsReachable class with mocked HTTP)

**Depends on:** none

**TDD:**
- `test_urls_reachable_all_ok` — mock all URLs returning 200 → pass
- `test_urls_reachable_below_threshold` — some 404 → fail
- `test_urls_reachable_ssrf_blocked` — private IP URL → blocked, not requested
- `test_urls_reachable_timeout` — mock timeout → counted as unreachable
- `test_urls_reachable_no_urls` — output has no URLs, threshold 1 → fail
- `test_urls_reachable_httpx_fallback` — test urllib path when httpx not available
- `test_urls_reachable_via_run_assertions` — end-to-end through dispatcher

---

### US-006: Quality Gate

**Description:** Run code reviewer across the full changeset. Run linter and full test suite. Fix all real bugs found.

**Traces to:** all decisions

**Acceptance Criteria:**
- [ ] `uv run ruff check src/ tests/` passes with zero warnings
- [ ] `uv run pytest --cov=clauditor --cov-report=term-missing` passes with ≥80% coverage
- [ ] All new public functions have consistent naming and signatures
- [ ] No security issues in final code (SSRF protections verified)
- [ ] No dead code or unused imports

**Done when:** All quality checks pass, no regressions.

**Files:** Any files touched by US-001 through US-005

**Depends on:** US-001, US-002, US-003, US-004, US-005

---

### US-007: Patterns & Memory

**Description:** Update project documentation and conventions with new patterns learned. Record any reusable insights from this implementation.

**Traces to:** all decisions

**Acceptance Criteria:**
- [ ] Any new conventions documented
- [ ] Memory updated with relevant insights

**Done when:** Documentation reflects the new validation capabilities.

**Files:** docs/, CLAUDE.md, or memory as appropriate

**Depends on:** US-006

## Beads Manifest

*Pending.*
