"""
Tests for _fmt_price_html in simple_firearm_logbook.py.

Formats a stored price string (already normalized by parse_decimal_optional
at entry time, but this function has to cope with whatever's actually in
the database) for display in an exported report: "Not set" for blank, a
"$1,234.56"-style string for a valid amount, and a raw HTML-escaped
passthrough for anything that isn't parseable as a Decimal rather than
raising. parse_decimal_optional itself never stores a negative value, but
this function still has to render one sanely if a negative ever lands in
the database some other way (e.g. a hand-edited row), with the sign
outside the currency symbol ("-$5.00", not "$-5.00").
"""

import simple_firearm_logbook as app


def test_blank_string_returns_not_set():
    assert app._fmt_price_html("") == "Not set"


def test_none_returns_not_set():
    assert app._fmt_price_html(None) == "Not set"


def test_whole_number_is_formatted_with_two_decimals():
    assert app._fmt_price_html("500") == "$500.00"


def test_thousands_separator_is_added():
    assert app._fmt_price_html("1234.5") == "$1,234.50"


def test_zero_is_formatted_not_treated_as_blank():
    assert app._fmt_price_html("0") == "$0.00"


def test_zero_point_zero_zero_is_formatted():
    assert app._fmt_price_html("0.00") == "$0.00"


def test_negative_amount_has_sign_outside_currency_symbol():
    assert app._fmt_price_html("-5") == "-$5.00"


def test_negative_amount_with_thousands_separator():
    assert app._fmt_price_html("-1234.5") == "-$1,234.50"


def test_negative_zero_is_formatted_as_plain_zero():
    assert app._fmt_price_html("-0") == "$0.00"


def test_unparseable_value_falls_back_to_escaped_raw_text():
    assert app._fmt_price_html("not a number") == "not a number"


def test_unparseable_value_with_html_is_escaped_on_fallback():
    assert app._fmt_price_html("<script>") == "&lt;script&gt;"
