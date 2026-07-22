"""
Tests for _build_csv_text in simple_firearm_logbook.py.

Builds the CSV export for the full collection. Uses the stdlib csv module's
default dialect, so the behavior under test is really "does it quote fields
that need quoting" (embedded commas, embedded quotes, embedded newlines) and
"does it emit the right header and one row per firearm".
"""

import simple_firearm_logbook as app

_ALL_FIELDS = (
    "log_number", "make", "model", "serial_number", "firearm_type", "caliber",
    "acquisition_date", "acquired_from", "purchase_price", "estimated_value", "notes",
    "disposition_status", "disposition_date", "disposition_to", "disposition_address",
    "disposition_amount", "disposition_notes",
)


def _firearm(**overrides) -> dict:
    """A firearm dict with every field _build_csv_text reads, defaulted to
    an empty string so tests only need to set the fields they care about."""
    row = {field: "" for field in _ALL_FIELDS}
    row.update(overrides)
    return row


# ─────────────────────────────────────────────────────────────
#  Header and row shape
# ─────────────────────────────────────────────────────────────
def test_header_row_matches_expected_columns():
    text = app._build_csv_text([])
    header_line = text.splitlines()[0]
    assert header_line == (
        "Log Number,Make,Model,Serial Number,Type,Caliber,Acquisition Date,"
        "Acquired From,Purchase Price,Estimated Value,Notes,Disposition Status,"
        "Disposition Date,Disposition To,Disposition Address,Disposition Amount,"
        "Disposition Notes"
    )


def test_empty_list_produces_only_the_header():
    text = app._build_csv_text([])
    assert len(text.splitlines()) == 1


def test_one_firearm_produces_one_data_row():
    text = app._build_csv_text([_firearm(log_number="00001", make="Glock", model="19")])
    lines = text.splitlines()
    assert len(lines) == 2
    assert lines[1].startswith("00001,Glock,19,")


def test_two_firearms_produce_two_data_rows_in_order():
    text = app._build_csv_text([
        _firearm(log_number="00001"),
        _firearm(log_number="00002"),
    ])
    lines = text.splitlines()
    assert len(lines) == 3
    assert lines[1].startswith("00001,")
    assert lines[2].startswith("00002,")


# ─────────────────────────────────────────────────────────────
#  Quoting behavior
# ─────────────────────────────────────────────────────────────
def test_embedded_comma_is_quoted():
    text = app._build_csv_text([_firearm(acquired_from="Smith, John")])
    assert '"Smith, John"' in text


def test_embedded_double_quote_is_escaped_by_doubling():
    text = app._build_csv_text([_firearm(notes='He said "hello"')])
    assert '"He said ""hello"""' in text


def test_embedded_newline_is_quoted_and_preserved():
    text = app._build_csv_text([_firearm(notes="line one\nline two")])
    assert '"line one\nline two"' in text


def test_plain_field_is_not_quoted():
    text = app._build_csv_text([_firearm(make="Glock")])
    assert ",Glock," in text
    assert '"Glock"' not in text


def test_rows_are_terminated_with_crlf():
    text = app._build_csv_text([_firearm()])
    assert "\r\n" in text
