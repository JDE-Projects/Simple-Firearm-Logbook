"""
Tests for parse_decimal_optional in simple_firearm_logbook.py.

This is the guard around every currency field in the app (purchase price,
estimated value, disposition amount). It has to accept the messy strings a
user types ($ signs, thousands commas), normalize them to a plain 2-decimal
string so the value never drifts as a float, and reject anything that isn't
a real non-negative finite amount, including Decimal special values like
NaN and Infinity that would otherwise slip past the parse and blow up later
at the negativity check or the quantize call. "-0" and any negative that
rounds to zero (e.g. "-0.001") are treated as zero, not rejected, since
that's what someone typing "-0" means; a genuinely negative amount, even
one very close to zero, is still rejected.
"""

import simple_firearm_logbook as app


# ─────────────────────────────────────────────────────────────
#  Blank input
# ─────────────────────────────────────────────────────────────
def test_blank_string_returns_empty_with_no_error():
    assert app.parse_decimal_optional("") == ("", None)


def test_whitespace_only_returns_empty_with_no_error():
    assert app.parse_decimal_optional("   ") == ("", None)


def test_none_returns_empty_with_no_error():
    assert app.parse_decimal_optional(None) == ("", None)


# ─────────────────────────────────────────────────────────────
#  Plain values and stripping
# ─────────────────────────────────────────────────────────────
def test_plain_integer_amount():
    assert app.parse_decimal_optional("100") == ("100.00", None)


def test_plain_decimal_amount():
    assert app.parse_decimal_optional("42.5") == ("42.50", None)


def test_dollar_sign_is_stripped():
    assert app.parse_decimal_optional("$500") == ("500.00", None)


def test_thousands_comma_is_stripped():
    assert app.parse_decimal_optional("1,234.56") == ("1234.56", None)


def test_dollar_sign_and_comma_together():
    assert app.parse_decimal_optional("$1,234.56") == ("1234.56", None)


def test_surrounding_whitespace_is_stripped():
    assert app.parse_decimal_optional("  250  ") == ("250.00", None)


def test_result_is_a_plain_string_not_a_float():
    value, error = app.parse_decimal_optional("19.99")
    assert error is None
    assert isinstance(value, str)


# ─────────────────────────────────────────────────────────────
#  Rounding: ROUND_HALF_UP, not Decimal's banker's rounding
# ─────────────────────────────────────────────────────────────
def test_1_005_rounds_up_to_1_01():
    assert app.parse_decimal_optional("1.005") == ("1.01", None)


def test_1_015_rounds_up_to_1_02():
    assert app.parse_decimal_optional("1.015") == ("1.02", None)


def test_1_025_rounds_up_to_1_03():
    # Banker's rounding (ROUND_HALF_EVEN) would round this down to 1.02.
    assert app.parse_decimal_optional("1.025") == ("1.03", None)


def test_ordinary_rounding_still_rounds_down_correctly():
    assert app.parse_decimal_optional("1.004") == ("1.00", None)


# ─────────────────────────────────────────────────────────────
#  Negative zero: normalized to plain zero, not rejected
# ─────────────────────────────────────────────────────────────
def test_negative_zero_normalizes_to_plain_zero():
    # "-0" means zero to the person typing it, not an error.
    assert app.parse_decimal_optional("-0") == ("0.00", None)


def test_small_negative_that_quantizes_to_zero_normalizes_to_plain_zero():
    assert app.parse_decimal_optional("-0.001") == ("0.00", None)


def test_negative_zero_with_trailing_decimals_normalizes_to_plain_zero():
    assert app.parse_decimal_optional("-0.00") == ("0.00", None)


# ─────────────────────────────────────────────────────────────
#  Negatives rejected
# ─────────────────────────────────────────────────────────────
def test_negative_amount_is_rejected():
    value, error = app.parse_decimal_optional("-5")
    assert value is None
    assert error == "Amount must be zero or greater."


def test_negative_amount_with_dollar_sign_is_rejected():
    value, error = app.parse_decimal_optional("-$5.00")
    assert value is None
    assert error == "Amount must be zero or greater."


def test_small_negative_that_rounds_away_from_zero_is_still_rejected():
    # Rounds to -0.01, a genuinely negative amount, not zero.
    value, error = app.parse_decimal_optional("-0.005")
    assert value is None
    assert error == "Amount must be zero or greater."


# ─────────────────────────────────────────────────────────────
#  Garbage input
# ─────────────────────────────────────────────────────────────
def test_non_numeric_garbage_is_rejected():
    value, error = app.parse_decimal_optional("not a number")
    assert value is None
    assert error == "Enter a valid amount."


def test_multiple_decimal_points_is_rejected():
    value, error = app.parse_decimal_optional("1.2.3")
    assert value is None
    assert error == "Enter a valid amount."


# ─────────────────────────────────────────────────────────────
#  Non-finite Decimal values (the confirmed bug)
# ─────────────────────────────────────────────────────────────
def test_nan_is_rejected_not_raised():
    value, error = app.parse_decimal_optional("NaN")
    assert value is None
    assert error == "Enter a valid amount."


def test_lowercase_nan_is_rejected():
    value, error = app.parse_decimal_optional("nan")
    assert value is None
    assert error == "Enter a valid amount."


def test_infinity_is_rejected_not_raised():
    value, error = app.parse_decimal_optional("Infinity")
    assert value is None
    assert error == "Enter a valid amount."


def test_negative_infinity_is_rejected():
    value, error = app.parse_decimal_optional("-Infinity")
    assert value is None
    assert error == "Enter a valid amount."


def test_mixed_case_infinity_is_rejected():
    value, error = app.parse_decimal_optional("iNFiniTY")
    assert value is None
    assert error == "Enter a valid amount."


def test_signaling_nan_is_rejected():
    value, error = app.parse_decimal_optional("sNaN")
    assert value is None
    assert error == "Enter a valid amount."
