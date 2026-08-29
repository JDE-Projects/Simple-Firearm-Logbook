"""
Tests for _redact_username in simple_firearm_logbook.py.

The debug log promises never to contain the user's name. This scrubs the
username segment out of any \\Users\\<name>\\ or /Users/<name>/ path before
a line is written, including inside exception messages that embed a full
path.
"""

import simple_firearm_logbook as app


def test_windows_backslash_path_has_username_replaced():
    text = r"C:\Users\John\Documents\form.pdf"
    result = app._redact_username(text)
    assert "John" not in result
    assert r"\Users\<user>\Documents\form.pdf" in result


def test_forward_slash_path_has_username_replaced():
    text = "/Users/John/x"
    result = app._redact_username(text)
    assert "John" not in result
    assert "/Users/<user>/x" in result


def test_text_with_no_users_path_is_unchanged():
    text = "Added 3 photo(s) to firearm 5"
    assert app._redact_username(text) == text
