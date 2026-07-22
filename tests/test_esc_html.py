"""
Tests for _esc_html in simple_firearm_logbook.py.

Every user-entered field (make, model, notes, disposition details, etc.)
flows through this before it's embedded in the exported HTML report, so
it's the app's only defense against HTML/script injection in exports. It
escapes &, <, >, and " but not single quotes, since the export templates
only ever use double-quoted attributes.
"""

import simple_firearm_logbook as app


def test_ampersand_is_escaped():
    assert app._esc_html("A & B") == "A &amp; B"


def test_less_than_is_escaped():
    assert app._esc_html("<script>") == "&lt;script&gt;"


def test_greater_than_is_escaped():
    assert app._esc_html("a > b") == "a &gt; b"


def test_double_quote_is_escaped():
    assert app._esc_html('say "hi"') == "say &quot;hi&quot;"


def test_script_tag_injection_is_neutralized():
    raw = '<script>alert(1)</script>'
    escaped = app._esc_html(raw)
    assert "<script>" not in escaped
    assert escaped == "&lt;script&gt;alert(1)&lt;/script&gt;"


def test_attribute_breakout_via_quote_is_neutralized():
    raw = '"><img src=x onerror=alert(1)>'
    escaped = app._esc_html(raw)
    assert '"' not in escaped
    assert "<img" not in escaped


def test_ampersand_is_escaped_first_so_entities_are_not_double_escaped():
    # If '&' were escaped after '<'/'>', "&lt;" from a literal '<' would
    # itself get re-escaped into "&amp;lt;". Confirm that doesn't happen.
    assert app._esc_html("<") == "&lt;"


def test_none_input_returns_empty_string():
    assert app._esc_html(None) == ""


def test_empty_string_returns_empty_string():
    assert app._esc_html("") == ""


def test_plain_text_is_unchanged():
    assert app._esc_html("Glock 19") == "Glock 19"


def test_single_quote_is_left_unescaped():
    assert app._esc_html("O'Brien") == "O'Brien"
