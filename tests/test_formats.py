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
