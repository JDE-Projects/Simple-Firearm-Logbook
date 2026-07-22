"""
Tests for parse_iso_date_optional in simple_firearm_logbook.py.

Guards every optional yyyy-mm-dd date field (acquisition and disposition
dates). Blank input must pass through untouched, a valid ISO date must be
accepted as-is, and anything malformed or calendar-impossible (like
February 30th) must come back as (None, error) rather than raising.
"""

import simple_firearm_logbook as app


# ─────────────────────────────────────────────────────────────
#  Blank input
# ─────────────────────────────────────────────────────────────
def test_blank_string_returns_empty_with_no_error():
    assert app.parse_iso_date_optional("") == ("", None)


def test_whitespace_only_returns_empty_with_no_error():
    assert app.parse_iso_date_optional("   ") == ("", None)


def test_none_returns_empty_with_no_error():
    assert app.parse_iso_date_optional(None) == ("", None)


# ─────────────────────────────────────────────────────────────
#  Valid dates
# ─────────────────────────────────────────────────────────────
def test_valid_date_is_returned_unchanged():
    assert app.parse_iso_date_optional("2026-07-22") == ("2026-07-22", None)


def test_valid_date_with_surrounding_whitespace_is_stripped():
    assert app.parse_iso_date_optional("  2026-01-01  ") == ("2026-01-01", None)


def test_valid_leap_day_is_accepted():
    assert app.parse_iso_date_optional("2024-02-29") == ("2024-02-29", None)


# ─────────────────────────────────────────────────────────────
#  Malformed input
# ─────────────────────────────────────────────────────────────
def test_slash_separated_date_is_rejected():
    value, error = app.parse_iso_date_optional("2026/07/22")
    assert value is None
    assert error == "Enter a valid date."


def test_garbage_text_is_rejected():
    value, error = app.parse_iso_date_optional("not a date")
    assert value is None
    assert error == "Enter a valid date."


def test_missing_day_is_rejected():
    value, error = app.parse_iso_date_optional("2026-07")
    assert value is None
    assert error == "Enter a valid date."


# ─────────────────────────────────────────────────────────────
#  Calendar-impossible dates
# ─────────────────────────────────────────────────────────────
def test_february_30th_is_rejected():
    value, error = app.parse_iso_date_optional("2026-02-30")
    assert value is None
    assert error == "Enter a valid date."


def test_non_leap_year_february_29th_is_rejected():
    value, error = app.parse_iso_date_optional("2026-02-29")
    assert value is None
    assert error == "Enter a valid date."


def test_month_13_is_rejected():
    value, error = app.parse_iso_date_optional("2026-13-01")
    assert value is None
    assert error == "Enter a valid date."
