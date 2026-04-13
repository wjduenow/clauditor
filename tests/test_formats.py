"""Tests for the format registry module."""

import pytest

from clauditor.formats import (
    FORMAT_REGISTRY,
    FormatDef,
    get_format,
    list_formats,
    validate_value,
)


class TestGetFormat:
    def test_valid_format(self):
        fmt = get_format("email")
        assert fmt is not None
        assert isinstance(fmt, FormatDef)
        assert fmt.name == "email"

    def test_invalid_format(self):
        assert get_format("nonexistent") is None

    def test_all_registry_entries_accessible(self):
        for name in FORMAT_REGISTRY:
            assert get_format(name) is not None


class TestListFormats:
    def test_returns_sorted_list(self):
        names = list_formats()
        assert names == sorted(names)

    def test_correct_count(self):
        names = list_formats()
        assert len(names) == len(FORMAT_REGISTRY)
        assert len(names) >= 19

    def test_contains_expected_formats(self):
        names = list_formats()
        expected = [
            "phone_us", "phone_intl", "email", "url", "date_iso", "date_us",
            "time_24h", "time_12h", "currency_usd", "currency_eur",
            "zip_us", "zip_uk", "percentage", "ipv4", "uuid", "hex_color",
            "latitude", "longitude", "star_rating", "domain",
        ]
        for name in expected:
            assert name in names, f"Missing format: {name}"


class TestAllPatternsCompile:
    def test_patterns_are_compiled(self):
        """All patterns should be pre-compiled at import time."""
        import re

        for name, fmt in FORMAT_REGISTRY.items():
            assert isinstance(fmt.pattern, re.Pattern), (
                f"{name}.pattern is not compiled"
            )
            assert isinstance(fmt.extract_pattern, re.Pattern), (
                f"{name}.extract_pattern is not compiled"
            )


class TestValidateValue:
    """Test validate_value against known good and bad inputs for each format."""

    @pytest.mark.parametrize("value", [
        "(408) 298-5437",
        "408-298-5437",
        "408.298.5437",
        "4082985437",
    ])
    def test_phone_us_valid(self, value):
        assert validate_value(value, "phone_us")

    def test_phone_us_invalid(self):
        assert not validate_value("call for hours", "phone_us")
        assert not validate_value("123", "phone_us")

    @pytest.mark.parametrize("value", [
        "+1 4082985437",
        "+44 7911123456",
        "+353 861234567",
    ])
    def test_phone_intl_valid(self, value):
        assert validate_value(value, "phone_intl")

    def test_phone_intl_invalid(self):
        assert not validate_value("4082985437", "phone_intl")

    @pytest.mark.parametrize("value", [
        "user@example.com",
        "first.last@company.co.uk",
        "test+tag@gmail.com",
    ])
    def test_email_valid(self, value):
        assert validate_value(value, "email")

    def test_email_invalid(self):
        assert not validate_value("not-an-email", "email")
        assert not validate_value("@missing.com", "email")

    @pytest.mark.parametrize("value", [
        "https://example.com",
        "http://example.com/path?q=1",
        "https://sub.domain.co.uk/page",
    ])
    def test_url_valid(self, value):
        assert validate_value(value, "url")

    def test_url_invalid(self):
        assert not validate_value("ftp://example.com", "url")
        assert not validate_value("not a url", "url")

    @pytest.mark.parametrize("value", [
        "2026-04-10",
        "2000-01-01",
    ])
    def test_date_iso_valid(self, value):
        assert validate_value(value, "date_iso")

    def test_date_iso_invalid(self):
        assert not validate_value("04/10/2026", "date_iso")

    @pytest.mark.parametrize("value", [
        "4/10/2026",
        "04/10/26",
        "12/31/2025",
    ])
    def test_date_us_valid(self, value):
        assert validate_value(value, "date_us")

    def test_date_us_invalid(self):
        assert not validate_value("2026-04-10", "date_us")

    @pytest.mark.parametrize("value", [
        "14:30",
        "14:30:00",
        "0:00",
    ])
    def test_time_24h_valid(self, value):
        assert validate_value(value, "time_24h")

    def test_time_24h_invalid(self):
        assert not validate_value("2:30 PM", "time_24h")

    @pytest.mark.parametrize("value", [
        "2:30 PM",
        "2:30pm",
        "12:00 AM",
    ])
    def test_time_12h_valid(self, value):
        assert validate_value(value, "time_12h")

    def test_time_12h_invalid(self):
        assert not validate_value("14:30", "time_12h")

    @pytest.mark.parametrize("value", [
        "$50",
        "$1,234.56",
        "$0.99",
    ])
    def test_currency_usd_valid(self, value):
        assert validate_value(value, "currency_usd")

    def test_currency_usd_invalid(self):
        assert not validate_value("50 dollars", "currency_usd")
        assert not validate_value("€50", "currency_usd")

    @pytest.mark.parametrize("value", [
        "€50",
        "€1.234,56",
    ])
    def test_currency_eur_valid(self, value):
        assert validate_value(value, "currency_eur")

    def test_currency_eur_invalid(self):
        assert not validate_value("$50", "currency_eur")

    @pytest.mark.parametrize("value", [
        "95110",
        "95110-1234",
    ])
    def test_zip_us_valid(self, value):
        assert validate_value(value, "zip_us")

    def test_zip_us_invalid(self):
        assert not validate_value("ABCDE", "zip_us")

    @pytest.mark.parametrize("value", [
        "SW1A 1AA",
        "EC1A 1BB",
        "W1A 0AX",
    ])
    def test_zip_uk_valid(self, value):
        assert validate_value(value, "zip_uk")

    def test_zip_uk_invalid(self):
        assert not validate_value("95110", "zip_uk")

    @pytest.mark.parametrize("value", [
        "95%",
        "99.9%",
        "0%",
    ])
    def test_percentage_valid(self, value):
        assert validate_value(value, "percentage")

    def test_percentage_invalid(self):
        assert not validate_value("ninety-five percent", "percentage")

    @pytest.mark.parametrize("value", [
        "192.168.1.1",
        "10.0.0.1",
        "255.255.255.0",
    ])
    def test_ipv4_valid(self, value):
        assert validate_value(value, "ipv4")

    def test_ipv4_invalid(self):
        assert not validate_value("not.an.ip", "ipv4")

    def test_uuid_valid(self):
        assert validate_value(
            "550e8400-e29b-41d4-a716-446655440000", "uuid"
        )

    def test_uuid_invalid(self):
        assert not validate_value("not-a-uuid", "uuid")

    def test_hex_color_valid(self):
        assert validate_value("#FF5733", "hex_color")
        assert validate_value("#aabbcc", "hex_color")

    def test_hex_color_invalid(self):
        assert not validate_value("#FFF", "hex_color")
        assert not validate_value("red", "hex_color")

    def test_latitude_valid(self):
        assert validate_value("37.3382", "latitude")
        assert validate_value("-33.8688", "latitude")

    def test_latitude_invalid(self):
        assert not validate_value("north", "latitude")

    def test_longitude_valid(self):
        assert validate_value("-121.8863", "longitude")
        assert validate_value("151.2093", "longitude")

    def test_longitude_invalid(self):
        assert not validate_value("west", "longitude")

    def test_star_rating_valid(self):
        assert validate_value("4.5 stars", "star_rating")
        assert validate_value("3 ★", "star_rating")
        assert validate_value("5 star", "star_rating")

    def test_star_rating_invalid(self):
        assert not validate_value("excellent", "star_rating")

    @pytest.mark.parametrize("value", [
        "paesanosj.com",
        "sub.example.co.uk",
        "a-b.io",
    ])
    def test_domain_valid(self, value):
        assert validate_value(value, "domain")

    @pytest.mark.parametrize("value", [
        "https://paesanosj.com",
        "paesanosj",
        ".com",
        "example..com",
        "example.com/path",
        "192.168.1.1",  # numeric TLD — ipv4-shaped input must not match
        "example.1",  # single-char, digit-only TLD
        "example.a",  # single-char TLD (too short)
    ])
    def test_domain_invalid(self, value):
        assert not validate_value(value, "domain")

    def test_unknown_format(self):
        assert not validate_value("anything", "nonexistent")


class TestDomainFieldRequirement:
    def test_field_requirement_with_domain_format(self):
        from clauditor.schemas import FieldRequirement
        fr = FieldRequirement(name="website", format="domain")
        assert fr.format == "domain"
        assert FORMAT_REGISTRY["domain"].pattern is not None


# ---------------------------------------------------------------------------
# Per-entry strict/extract invariant tests (one class per FORMAT_REGISTRY
# entry). Each class verifies:
#   1. strict pattern fullmatches canonical values
#   2. strict pattern rejects malformed values
#   3. extract pattern finds the value inside surrounding prose
#   4. extract pattern rejects pure noise
#   5. invariant: validate_value(X) True → extract_pattern.findall finds X
# ---------------------------------------------------------------------------


def _assert_extract_contains(fmt_name: str, value: str) -> None:
    """Helper asserting the extract pattern finds `value` inside prose."""
    fmt = FORMAT_REGISTRY[fmt_name]
    prose = f"prefix blah {value} suffix blah"
    matches = fmt.extract_pattern.findall(prose)
    # findall returns full matches (we only use non-capturing groups).
    assert any(value == m or value in m for m in matches), (
        f"{fmt_name}: extract_pattern did not find {value!r} in prose; "
        f"got matches={matches!r}"
    )


class TestPhoneUsFormat:
    name = "phone_us"
    canonical = ["(408) 298-5437", "408-298-5437", "408.298.5437", "4082985437"]
    malformed = ["call for hours", "123", "abcdefghij"]

    def test_strict_accepts_canonical(self):
        fmt = FORMAT_REGISTRY[self.name]
        for v in self.canonical:
            assert fmt.pattern.fullmatch(v)

    def test_strict_rejects_malformed(self):
        fmt = FORMAT_REGISTRY[self.name]
        for v in self.malformed:
            assert fmt.pattern.fullmatch(v) is None

    def test_extract_finds_in_prose(self):
        fmt = FORMAT_REGISTRY[self.name]
        assert fmt.extract_pattern.findall("call me at 408-298-5437 today")

    def test_extract_rejects_pure_noise(self):
        fmt = FORMAT_REGISTRY[self.name]
        assert not fmt.extract_pattern.findall("just some harmless words here")

    def test_invariant_validate_implies_extract(self):
        for v in self.canonical:
            assert validate_value(v, self.name)
            _assert_extract_contains(self.name, v)


class TestPhoneIntlFormat:
    name = "phone_intl"
    canonical = ["+1 4082985437", "+44 7911123456", "+353 861234567"]
    malformed = ["4082985437", "+", "not a phone"]

    def test_strict_accepts_canonical(self):
        fmt = FORMAT_REGISTRY[self.name]
        for v in self.canonical:
            assert fmt.pattern.fullmatch(v)

    def test_strict_rejects_malformed(self):
        fmt = FORMAT_REGISTRY[self.name]
        for v in self.malformed:
            assert fmt.pattern.fullmatch(v) is None

    def test_extract_finds_in_prose(self):
        fmt = FORMAT_REGISTRY[self.name]
        assert fmt.extract_pattern.findall("call +44 7911123456 please")

    def test_extract_rejects_pure_noise(self):
        fmt = FORMAT_REGISTRY[self.name]
        assert not fmt.extract_pattern.findall("no numbers at all here")

    def test_invariant_validate_implies_extract(self):
        for v in self.canonical:
            assert validate_value(v, self.name)
            _assert_extract_contains(self.name, v)


class TestEmailFormat:
    name = "email"
    canonical = ["user@example.com", "first.last@company.co.uk", "test+tag@gmail.com"]
    malformed = ["not-an-email", "@missing.com", "no-at-sign.com"]

    def test_strict_accepts_canonical(self):
        fmt = FORMAT_REGISTRY[self.name]
        for v in self.canonical:
            assert fmt.pattern.fullmatch(v)

    def test_strict_rejects_malformed(self):
        fmt = FORMAT_REGISTRY[self.name]
        for v in self.malformed:
            assert fmt.pattern.fullmatch(v) is None

    def test_extract_finds_in_prose(self):
        fmt = FORMAT_REGISTRY[self.name]
        assert fmt.extract_pattern.findall("write to user@example.com today")

    def test_extract_rejects_pure_noise(self):
        fmt = FORMAT_REGISTRY[self.name]
        assert not fmt.extract_pattern.findall("no email address in here")

    def test_invariant_validate_implies_extract(self):
        for v in self.canonical:
            assert validate_value(v, self.name)
            _assert_extract_contains(self.name, v)


class TestUrlFormat:
    name = "url"
    canonical = [
        "https://example.com",
        "http://example.com/path?q=1",
        "https://sub.domain.co.uk/page",
    ]
    malformed = ["ftp://example.com", "example.com", "not a url"]

    def test_strict_accepts_canonical(self):
        fmt = FORMAT_REGISTRY[self.name]
        for v in self.canonical:
            assert fmt.pattern.fullmatch(v)

    def test_strict_rejects_malformed(self):
        fmt = FORMAT_REGISTRY[self.name]
        for v in self.malformed:
            assert fmt.pattern.fullmatch(v) is None

    def test_extract_finds_in_prose(self):
        fmt = FORMAT_REGISTRY[self.name]
        matches = fmt.extract_pattern.findall("see https://example.com for more")
        assert any("https://example.com" in m for m in matches)

    def test_extract_rejects_pure_noise(self):
        fmt = FORMAT_REGISTRY[self.name]
        assert not fmt.extract_pattern.findall("plain text with no link")

    def test_invariant_validate_implies_extract(self):
        for v in self.canonical:
            assert validate_value(v, self.name)
            _assert_extract_contains(self.name, v)


class TestDomainFormat:
    name = "domain"
    canonical = ["paesanosj.com", "sub.example.co.uk", "a-b.io"]
    malformed = ["https://paesanosj.com", "paesanosj", ".com", "192.168.1.1"]

    def test_strict_accepts_canonical(self):
        fmt = FORMAT_REGISTRY[self.name]
        for v in self.canonical:
            assert fmt.pattern.fullmatch(v)

    def test_strict_rejects_malformed(self):
        fmt = FORMAT_REGISTRY[self.name]
        for v in self.malformed:
            assert fmt.pattern.fullmatch(v) is None

    def test_extract_finds_in_prose(self):
        fmt = FORMAT_REGISTRY[self.name]
        matches = fmt.extract_pattern.findall("visit paesanosj.com today")
        assert any("paesanosj.com" in m for m in matches)

    def test_extract_rejects_pure_noise(self):
        fmt = FORMAT_REGISTRY[self.name]
        assert not fmt.extract_pattern.findall("plain words without a host")

    def test_invariant_validate_implies_extract(self):
        for v in self.canonical:
            assert validate_value(v, self.name)
            _assert_extract_contains(self.name, v)


class TestDateIsoFormat:
    name = "date_iso"
    canonical = ["2026-04-10", "2000-01-01"]
    malformed = ["04/10/2026", "2026/04/10", "not a date"]

    def test_strict_accepts_canonical(self):
        fmt = FORMAT_REGISTRY[self.name]
        for v in self.canonical:
            assert fmt.pattern.fullmatch(v)

    def test_strict_rejects_malformed(self):
        fmt = FORMAT_REGISTRY[self.name]
        for v in self.malformed:
            assert fmt.pattern.fullmatch(v) is None

    def test_extract_finds_in_prose(self):
        fmt = FORMAT_REGISTRY[self.name]
        assert fmt.extract_pattern.findall("due on 2026-04-10 please")

    def test_extract_rejects_pure_noise(self):
        fmt = FORMAT_REGISTRY[self.name]
        assert not fmt.extract_pattern.findall("no date in this sentence")

    def test_invariant_validate_implies_extract(self):
        for v in self.canonical:
            assert validate_value(v, self.name)
            _assert_extract_contains(self.name, v)


class TestDateUsFormat:
    name = "date_us"
    canonical = ["4/10/2026", "04/10/26", "12/31/2025"]
    malformed = ["2026-04-10", "not a date"]

    def test_strict_accepts_canonical(self):
        fmt = FORMAT_REGISTRY[self.name]
        for v in self.canonical:
            assert fmt.pattern.fullmatch(v)

    def test_strict_rejects_malformed(self):
        fmt = FORMAT_REGISTRY[self.name]
        for v in self.malformed:
            assert fmt.pattern.fullmatch(v) is None

    def test_extract_finds_in_prose(self):
        fmt = FORMAT_REGISTRY[self.name]
        assert fmt.extract_pattern.findall("due 4/10/2026 ok")

    def test_extract_rejects_pure_noise(self):
        fmt = FORMAT_REGISTRY[self.name]
        assert not fmt.extract_pattern.findall("no date here at all")

    def test_invariant_validate_implies_extract(self):
        for v in self.canonical:
            assert validate_value(v, self.name)
            _assert_extract_contains(self.name, v)


class TestTime24hFormat:
    name = "time_24h"
    canonical = ["14:30", "14:30:00", "0:00"]
    malformed = ["2:30 PM", "not a time"]

    def test_strict_accepts_canonical(self):
        fmt = FORMAT_REGISTRY[self.name]
        for v in self.canonical:
            assert fmt.pattern.fullmatch(v)

    def test_strict_rejects_malformed(self):
        fmt = FORMAT_REGISTRY[self.name]
        for v in self.malformed:
            assert fmt.pattern.fullmatch(v) is None

    def test_extract_finds_in_prose(self):
        fmt = FORMAT_REGISTRY[self.name]
        assert fmt.extract_pattern.findall("meet at 14:30 sharp")

    def test_extract_rejects_pure_noise(self):
        fmt = FORMAT_REGISTRY[self.name]
        assert not fmt.extract_pattern.findall("no clock reading in here")

    def test_invariant_validate_implies_extract(self):
        for v in self.canonical:
            assert validate_value(v, self.name)
            _assert_extract_contains(self.name, v)


class TestTime12hFormat:
    name = "time_12h"
    canonical = ["2:30 PM", "2:30pm", "12:00 AM"]
    malformed = ["14:30", "not a time"]

    def test_strict_accepts_canonical(self):
        fmt = FORMAT_REGISTRY[self.name]
        for v in self.canonical:
            assert fmt.pattern.fullmatch(v)

    def test_strict_rejects_malformed(self):
        fmt = FORMAT_REGISTRY[self.name]
        for v in self.malformed:
            assert fmt.pattern.fullmatch(v) is None

    def test_extract_finds_in_prose(self):
        fmt = FORMAT_REGISTRY[self.name]
        assert fmt.extract_pattern.findall("meet at 2:30 PM please")

    def test_extract_rejects_pure_noise(self):
        fmt = FORMAT_REGISTRY[self.name]
        assert not fmt.extract_pattern.findall("no clock reading in here")

    def test_invariant_validate_implies_extract(self):
        for v in self.canonical:
            assert validate_value(v, self.name)
            _assert_extract_contains(self.name, v)


class TestCurrencyUsdFormat:
    name = "currency_usd"
    canonical = ["$50", "$1,234.56", "$0.99"]
    malformed = ["50 dollars", "€50", "USD 50"]

    def test_strict_accepts_canonical(self):
        fmt = FORMAT_REGISTRY[self.name]
        for v in self.canonical:
            assert fmt.pattern.fullmatch(v)

    def test_strict_rejects_malformed(self):
        fmt = FORMAT_REGISTRY[self.name]
        for v in self.malformed:
            assert fmt.pattern.fullmatch(v) is None

    def test_extract_finds_in_prose(self):
        fmt = FORMAT_REGISTRY[self.name]
        assert fmt.extract_pattern.findall("costs $1,234.56 total")

    def test_extract_rejects_pure_noise(self):
        fmt = FORMAT_REGISTRY[self.name]
        assert not fmt.extract_pattern.findall("no price mentioned here")

    def test_invariant_validate_implies_extract(self):
        for v in self.canonical:
            assert validate_value(v, self.name)
            _assert_extract_contains(self.name, v)


class TestCurrencyEurFormat:
    name = "currency_eur"
    canonical = ["€50", "€1.234,56"]
    malformed = ["$50", "50 EUR"]

    def test_strict_accepts_canonical(self):
        fmt = FORMAT_REGISTRY[self.name]
        for v in self.canonical:
            assert fmt.pattern.fullmatch(v)

    def test_strict_rejects_malformed(self):
        fmt = FORMAT_REGISTRY[self.name]
        for v in self.malformed:
            assert fmt.pattern.fullmatch(v) is None

    def test_extract_finds_in_prose(self):
        fmt = FORMAT_REGISTRY[self.name]
        assert fmt.extract_pattern.findall("price is €50 total")

    def test_extract_rejects_pure_noise(self):
        fmt = FORMAT_REGISTRY[self.name]
        assert not fmt.extract_pattern.findall("no price mentioned here")

    def test_invariant_validate_implies_extract(self):
        for v in self.canonical:
            assert validate_value(v, self.name)
            _assert_extract_contains(self.name, v)


class TestZipUsFormat:
    name = "zip_us"
    canonical = ["95110", "95110-1234"]
    malformed = ["ABCDE", "9511", "notazip"]

    def test_strict_accepts_canonical(self):
        fmt = FORMAT_REGISTRY[self.name]
        for v in self.canonical:
            assert fmt.pattern.fullmatch(v)

    def test_strict_rejects_malformed(self):
        fmt = FORMAT_REGISTRY[self.name]
        for v in self.malformed:
            assert fmt.pattern.fullmatch(v) is None

    def test_extract_finds_in_prose(self):
        fmt = FORMAT_REGISTRY[self.name]
        assert fmt.extract_pattern.findall("ship to 95110 today")

    def test_extract_rejects_pure_noise(self):
        fmt = FORMAT_REGISTRY[self.name]
        assert not fmt.extract_pattern.findall("no zip code in here")

    def test_invariant_validate_implies_extract(self):
        for v in self.canonical:
            assert validate_value(v, self.name)
            _assert_extract_contains(self.name, v)


class TestZipUkFormat:
    name = "zip_uk"
    canonical = ["SW1A 1AA", "EC1A 1BB", "W1A 0AX"]
    malformed = ["95110", "not a postcode"]

    def test_strict_accepts_canonical(self):
        fmt = FORMAT_REGISTRY[self.name]
        for v in self.canonical:
            assert fmt.pattern.fullmatch(v)

    def test_strict_rejects_malformed(self):
        fmt = FORMAT_REGISTRY[self.name]
        for v in self.malformed:
            assert fmt.pattern.fullmatch(v) is None

    def test_extract_finds_in_prose(self):
        fmt = FORMAT_REGISTRY[self.name]
        assert fmt.extract_pattern.findall("ship to SW1A 1AA please")

    def test_extract_rejects_pure_noise(self):
        fmt = FORMAT_REGISTRY[self.name]
        assert not fmt.extract_pattern.findall("no postcode in here")

    def test_invariant_validate_implies_extract(self):
        for v in self.canonical:
            assert validate_value(v, self.name)
            _assert_extract_contains(self.name, v)


class TestPercentageFormat:
    name = "percentage"
    canonical = ["95%", "99.9%", "0%"]
    malformed = ["ninety-five percent", "95", "percent"]

    def test_strict_accepts_canonical(self):
        fmt = FORMAT_REGISTRY[self.name]
        for v in self.canonical:
            assert fmt.pattern.fullmatch(v)

    def test_strict_rejects_malformed(self):
        fmt = FORMAT_REGISTRY[self.name]
        for v in self.malformed:
            assert fmt.pattern.fullmatch(v) is None

    def test_extract_finds_in_prose(self):
        fmt = FORMAT_REGISTRY[self.name]
        assert fmt.extract_pattern.findall("reached 99.9% uptime today")

    def test_extract_rejects_pure_noise(self):
        fmt = FORMAT_REGISTRY[self.name]
        assert not fmt.extract_pattern.findall("plain words only here")

    def test_invariant_validate_implies_extract(self):
        for v in self.canonical:
            assert validate_value(v, self.name)
            _assert_extract_contains(self.name, v)


class TestIpv4Format:
    name = "ipv4"
    canonical = ["192.168.1.1", "10.0.0.1", "255.255.255.0"]
    malformed = ["not.an.ip", "1.2.3", "abcd"]

    def test_strict_accepts_canonical(self):
        fmt = FORMAT_REGISTRY[self.name]
        for v in self.canonical:
            assert fmt.pattern.fullmatch(v)

    def test_strict_rejects_malformed(self):
        fmt = FORMAT_REGISTRY[self.name]
        for v in self.malformed:
            assert fmt.pattern.fullmatch(v) is None

    def test_extract_finds_in_prose(self):
        fmt = FORMAT_REGISTRY[self.name]
        assert fmt.extract_pattern.findall("reach 192.168.1.1 now")

    def test_extract_rejects_pure_noise(self):
        fmt = FORMAT_REGISTRY[self.name]
        assert not fmt.extract_pattern.findall("no address here at all")

    def test_invariant_validate_implies_extract(self):
        for v in self.canonical:
            assert validate_value(v, self.name)
            _assert_extract_contains(self.name, v)


class TestUuidFormat:
    name = "uuid"
    canonical = ["550e8400-e29b-41d4-a716-446655440000"]
    malformed = ["not-a-uuid", "550e8400", "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"]

    def test_strict_accepts_canonical(self):
        fmt = FORMAT_REGISTRY[self.name]
        for v in self.canonical:
            assert fmt.pattern.fullmatch(v)

    def test_strict_rejects_malformed(self):
        fmt = FORMAT_REGISTRY[self.name]
        for v in self.malformed:
            assert fmt.pattern.fullmatch(v) is None

    def test_extract_finds_in_prose(self):
        fmt = FORMAT_REGISTRY[self.name]
        assert fmt.extract_pattern.findall(
            "id=550e8400-e29b-41d4-a716-446655440000 foo"
        )

    def test_extract_rejects_pure_noise(self):
        fmt = FORMAT_REGISTRY[self.name]
        assert not fmt.extract_pattern.findall("no identifier mentioned here")

    def test_invariant_validate_implies_extract(self):
        for v in self.canonical:
            assert validate_value(v, self.name)
            _assert_extract_contains(self.name, v)


class TestHexColorFormat:
    name = "hex_color"
    canonical = ["#FF5733", "#aabbcc"]
    malformed = ["#FFF", "red", "FF5733"]

    def test_strict_accepts_canonical(self):
        fmt = FORMAT_REGISTRY[self.name]
        for v in self.canonical:
            assert fmt.pattern.fullmatch(v)

    def test_strict_rejects_malformed(self):
        fmt = FORMAT_REGISTRY[self.name]
        for v in self.malformed:
            assert fmt.pattern.fullmatch(v) is None

    def test_extract_finds_in_prose(self):
        fmt = FORMAT_REGISTRY[self.name]
        assert fmt.extract_pattern.findall("use #FF5733 for the header")

    def test_extract_rejects_pure_noise(self):
        fmt = FORMAT_REGISTRY[self.name]
        assert not fmt.extract_pattern.findall("no color mentioned here")

    def test_invariant_validate_implies_extract(self):
        for v in self.canonical:
            assert validate_value(v, self.name)
            _assert_extract_contains(self.name, v)


class TestLatitudeFormat:
    name = "latitude"
    canonical = ["37.3382", "-33.8688"]
    malformed = ["north", "37", "abc"]

    def test_strict_accepts_canonical(self):
        fmt = FORMAT_REGISTRY[self.name]
        for v in self.canonical:
            assert fmt.pattern.fullmatch(v)

    def test_strict_rejects_malformed(self):
        fmt = FORMAT_REGISTRY[self.name]
        for v in self.malformed:
            assert fmt.pattern.fullmatch(v) is None

    def test_extract_finds_in_prose(self):
        fmt = FORMAT_REGISTRY[self.name]
        assert fmt.extract_pattern.findall("at lat 37.3382 approximately")

    def test_extract_rejects_pure_noise(self):
        fmt = FORMAT_REGISTRY[self.name]
        assert not fmt.extract_pattern.findall("no coordinates in here")

    def test_invariant_validate_implies_extract(self):
        for v in self.canonical:
            assert validate_value(v, self.name)
            _assert_extract_contains(self.name, v)


class TestLongitudeFormat:
    name = "longitude"
    canonical = ["-121.8863", "151.2093"]
    malformed = ["west", "121", "abc"]

    def test_strict_accepts_canonical(self):
        fmt = FORMAT_REGISTRY[self.name]
        for v in self.canonical:
            assert fmt.pattern.fullmatch(v)

    def test_strict_rejects_malformed(self):
        fmt = FORMAT_REGISTRY[self.name]
        for v in self.malformed:
            assert fmt.pattern.fullmatch(v) is None

    def test_extract_finds_in_prose(self):
        fmt = FORMAT_REGISTRY[self.name]
        assert fmt.extract_pattern.findall("at lon -121.8863 approximately")

    def test_extract_rejects_pure_noise(self):
        fmt = FORMAT_REGISTRY[self.name]
        assert not fmt.extract_pattern.findall("no coordinates in here")

    def test_invariant_validate_implies_extract(self):
        for v in self.canonical:
            assert validate_value(v, self.name)
            _assert_extract_contains(self.name, v)


class TestStarRatingFormat:
    name = "star_rating"
    canonical = ["4.5 stars", "3 ★", "5 star"]
    malformed = ["excellent", "five stars", "rating"]

    def test_strict_accepts_canonical(self):
        fmt = FORMAT_REGISTRY[self.name]
        for v in self.canonical:
            assert fmt.pattern.fullmatch(v)

    def test_strict_rejects_malformed(self):
        fmt = FORMAT_REGISTRY[self.name]
        for v in self.malformed:
            assert fmt.pattern.fullmatch(v) is None

    def test_extract_finds_in_prose(self):
        fmt = FORMAT_REGISTRY[self.name]
        assert fmt.extract_pattern.findall("earned 4.5 stars overall")

    def test_extract_rejects_pure_noise(self):
        fmt = FORMAT_REGISTRY[self.name]
        assert not fmt.extract_pattern.findall("no rating mentioned here")

    def test_invariant_validate_implies_extract(self):
        for v in self.canonical:
            assert validate_value(v, self.name)
            _assert_extract_contains(self.name, v)


class TestEveryRegistryEntryHasATestClass:
    """Meta-test: make sure we didn't forget any entry."""

    def test_one_class_per_entry(self):
        import sys
        mod = sys.modules[__name__]
        covered = set()
        for attr in dir(mod):
            obj = getattr(mod, attr)
            name = getattr(obj, "name", None)
            if (
                isinstance(obj, type)
                and attr.startswith("Test")
                and attr.endswith("Format")
                and isinstance(name, str)
                and name in FORMAT_REGISTRY
            ):
                covered.add(name)
        missing = set(FORMAT_REGISTRY) - covered
        assert not missing, f"Missing per-entry test classes for: {missing}"
