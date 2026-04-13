"""Named format patterns for field-level validation.

Provides a registry of common data formats (email, phone, URL, etc.)
with pre-compiled regex patterns for both fullmatch validation and
findall extraction.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class FormatDef:
    """A named format with compiled patterns for validation and extraction."""

    name: str
    pattern: re.Pattern[str]  # For re.fullmatch (strict, anchored)
    description: str
    extract_pattern: re.Pattern[str]  # For re.findall (scanning)


def _def(
    name: str,
    pattern: str,
    description: str,
    extract: str | None = None,
) -> FormatDef:
    """Build a FormatDef, compiling both patterns at module load time."""
    return FormatDef(
        name=name,
        pattern=re.compile(pattern),
        description=description,
        extract_pattern=re.compile(extract or pattern),
    )


FORMAT_REGISTRY: dict[str, FormatDef] = {f.name: f for f in [
    # strict === extract: the pattern has no anchors and the token shape
    # (3-3-4 digits with optional separators) is already self-delimiting,
    # so the same regex works for fullmatch of a canonical value and
    # findall inside prose like "call me at 408-298-5437 today".
    _def(
        "phone_us",
        r"\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}",
        "US phone number, e.g. (408) 298-5437",
    ),
    # strict === extract: the leading '+' and country code make this
    # self-delimiting in prose, and fullmatch of a canonical value works
    # with the same unanchored pattern.
    _def(
        "phone_intl",
        r"\+\d{1,3}[\s.-]?\d{4,14}",
        "International phone number, e.g. +44 7911123456",
    ),
    # strict === extract: email local@domain shape is self-delimiting
    # (the '@' plus TLD requirement), so the same pattern handles both
    # fullmatch of a canonical address and findall inside a sentence.
    _def(
        "email",
        r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}",
        "Email address, e.g. user@example.com",
    ),
    # strict === extract: the required "https?://" prefix plus the
    # explicit non-whitespace/non-quote terminator set make the URL
    # naturally self-delimiting in prose.
    _def(
        "url",
        r"https?://[^\s\)\"'>]+",
        (
            "HTTP(S) URL with scheme. Note: LLMs often emit bare domains "
            "when pulling display text from markdown links "
            "(e.g. '[paesanosj.com](https://paesanosj.com/)' → "
            "'paesanosj.com'). Use the 'domain' format if you want to "
            "accept those too."
        ),
    ),
    # strict === extract: labels plus a >=2-char alphabetic TLD make the
    # shape self-delimiting. Fullmatch correctly rejects values with a
    # scheme or path, and findall locates bare domains inside prose.
    _def(
        "domain",
        # Two or more labels; final label must be alphabetic and >=2 chars
        # so numeric/IP-like strings (e.g. 192.168.1.1) don't validate.
        r"(?i)[a-z0-9](?:[a-z0-9-]*[a-z0-9])?"
        r"(?:\.[a-z0-9](?:[a-z0-9-]*[a-z0-9])?)*\.[a-z]{2,}",
        "Bare domain (no scheme), e.g. example.com or sub.example.co.uk",
    ),
    # strict === extract: fixed-width YYYY-MM-DD is self-delimiting.
    _def(
        "date_iso",
        r"\d{4}-\d{2}-\d{2}",
        "ISO 8601 date, e.g. 2026-04-10",
    ),
    # strict === extract: M/D/Y shape with '/' separators is
    # self-delimiting in prose.
    _def(
        "date_us",
        r"\d{1,2}/\d{1,2}/\d{2,4}",
        "US date format, e.g. 4/10/2026 or 04/10/26",
    ),
    # strict === extract: HH:MM[:SS] with ':' separator is
    # self-delimiting in prose.
    _def(
        "time_24h",
        r"\d{1,2}:\d{2}(?::\d{2})?",
        "24-hour time, e.g. 14:30 or 14:30:00",
    ),
    # strict === extract: HH:MM with required AM/PM suffix is
    # self-delimiting in prose.
    _def(
        "time_12h",
        r"\d{1,2}:\d{2}\s?[AaPp][Mm]",
        "12-hour time, e.g. 2:30 PM or 2:30pm",
    ),
    # strict === extract: leading '$' sigil + lookahead requiring at
    # least one digit makes the token self-delimiting in prose and
    # rejects nonsense like "$,,,".
    _def(
        "currency_usd",
        r"\$(?=[\d,]*\d)[\d,]+(?:\.\d{2})?",
        "US dollar amount, e.g. $1,234.56 or $50",
    ),
    # strict === extract: leading '€' sigil + lookahead requiring at
    # least one digit makes the token self-delimiting in prose and
    # rejects nonsense like "€...".
    _def(
        "currency_eur",
        r"€(?=[\d.,]*\d)[\d.,]+",
        "Euro amount, e.g. €1.234,56",
    ),
    # strict === extract: 5 digits (optional -4) is a self-delimiting
    # numeric token; findall will locate it inside prose without
    # grabbing adjacent characters.
    _def(
        "zip_us",
        r"\d{5}(?:-\d{4})?",
        "US ZIP code, e.g. 95110 or 95110-1234",
    ),
    # strict === extract (explicit): an explicit extract= is provided
    # for clarity, identical to the strict pattern. The UK postcode
    # alternation of letters/digits is already self-delimiting, so no
    # looser extract shape is needed.
    _def(
        "zip_uk",
        r"[A-Z]{1,2}\d[A-Z\d]?\s?\d[A-Z]{2}",
        "UK postcode, e.g. SW1A 1AA",
        extract=r"[A-Z]{1,2}\d[A-Z\d]?\s?\d[A-Z]{2}",
    ),
    # strict === extract: trailing '%' sigil makes the token
    # self-delimiting in prose.
    _def(
        "percentage",
        r"\d+(?:\.\d+)?%",
        "Percentage, e.g. 95% or 99.9%",
    ),
    # strict === extract: four dot-separated numeric octets are
    # self-delimiting in prose.
    _def(
        "ipv4",
        r"\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}",
        "IPv4 address, e.g. 192.168.1.1",
    ),
    # strict === extract (explicit): the canonical 8-4-4-4-12 hex shape
    # is self-delimiting; the explicit extract= is kept for clarity and
    # parity with the strict pattern.
    _def(
        "uuid",
        r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}",
        "UUID, e.g. 550e8400-e29b-41d4-a716-446655440000",
        extract=r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}",
    ),
    # strict === extract: leading '#' sigil + fixed 6 hex chars is
    # self-delimiting in prose.
    _def(
        "hex_color",
        r"#[0-9a-fA-F]{6}",
        "Hex color code, e.g. #FF5733",
    ),
    # strict === extract: a signed decimal with at least one fractional
    # digit is self-delimiting enough for prose extraction. Both
    # fullmatch and findall use the same shape.
    _def(
        "latitude",
        r"-?\d{1,2}\.\d+",
        "Latitude coordinate, e.g. 37.3382",
    ),
    # strict === extract: same rationale as latitude, with a wider
    # integer-part (3 digits) for values like -121.8863.
    _def(
        "longitude",
        r"-?\d{1,3}\.\d+",
        "Longitude coordinate, e.g. -121.8863",
    ),
    # strict === extract: the required "star(s)"/★/⭐ suffix makes the
    # token self-delimiting in prose.
    _def(
        "star_rating",
        r"\d(?:\.\d)?\s?(?:stars?|★|⭐)",
        "Star rating, e.g. 4.5 stars or 3 ★",
    ),
]}


def get_format(name: str) -> FormatDef | None:
    """Look up a format by name. Returns None if not found."""
    return FORMAT_REGISTRY.get(name)


def list_formats() -> list[str]:
    """Return sorted list of all registered format names."""
    return sorted(FORMAT_REGISTRY)


def validate_value(value: str, format_name: str) -> bool:
    """Validate a value against a named format using fullmatch.

    Returns False if the format name is unknown.
    """
    fmt = FORMAT_REGISTRY.get(format_name)
    if fmt is None:
        return False
    return fmt.pattern.fullmatch(value) is not None
