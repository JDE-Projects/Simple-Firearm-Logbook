"""
Tests for sanitize_filename and _safe_photo_path in simple_firearm_logbook.py.

Together these are the path-traversal guard around every photo file the app
reads or writes: sanitize_filename strips characters Windows won't allow in
a file name (this also strips '/' and '\\', so it happens to remove path
separators too), and _safe_photo_path is the actual boundary check, resolving
a stored filename against the app folder and refusing anything that would
land outside it. The app stores photo filenames as "photos/<name>" relative
to the app folder (not a fixed photos-only boundary), so the guard's real
promise is: nothing may resolve outside app_dir().
"""

import os

import simple_firearm_logbook as app


# ─────────────────────────────────────────────────────────────
#  sanitize_filename
# ─────────────────────────────────────────────────────────────
def test_strips_windows_invalid_characters():
    assert app.sanitize_filename('a<b>c:d"e|f?g*h.jpg') == "abcdefgh.jpg"


def test_strips_forward_and_back_slashes():
    assert app.sanitize_filename("../../secret.txt") == "....secret.txt"


def test_leaves_ordinary_filename_untouched():
    assert app.sanitize_filename("photo-01.jpg") == "photo-01.jpg"


def test_strips_leading_and_trailing_whitespace():
    assert app.sanitize_filename("  photo.jpg  ") == "photo.jpg"


def test_empty_string_returns_empty_string():
    assert app.sanitize_filename("") == ""


# ─────────────────────────────────────────────────────────────
#  _safe_photo_path: paths inside the app folder are accepted
# ─────────────────────────────────────────────────────────────
def test_ordinary_relative_photo_path_resolves_inside_app_dir():
    base = os.path.realpath(app.app_dir())
    full = app._safe_photo_path("photos/pic.jpg")
    assert full == os.path.join(base, "photos", "pic.jpg")


def test_bare_filename_at_app_dir_root_is_accepted():
    base = os.path.realpath(app.app_dir())
    full = app._safe_photo_path("pic.jpg")
    assert full == os.path.join(base, "pic.jpg")


# ─────────────────────────────────────────────────────────────
#  _safe_photo_path: escapes are refused
# ─────────────────────────────────────────────────────────────
def test_dot_dot_traversal_is_refused():
    assert app._safe_photo_path("../../secret.txt") is None


def test_dot_dot_traversal_nested_under_photos_is_refused():
    assert app._safe_photo_path("photos/../../secret.txt") is None


def test_windows_absolute_path_is_refused():
    assert app._safe_photo_path(r"C:\Windows\System32\cmd.exe") is None


def test_unc_path_is_refused():
    assert app._safe_photo_path(r"\\evil-server\share\file.jpg") is None


def test_forward_slash_absolute_path_is_refused():
    assert app._safe_photo_path("/etc/passwd") is None


def test_traversal_that_lands_back_inside_app_dir_is_accepted():
    # This does not escape app_dir at all (it just detours through the
    # parent and back down), so the boundary check correctly allows it.
    base = os.path.realpath(app.app_dir())
    parent_name = os.path.basename(base)
    full = app._safe_photo_path(f"../{parent_name}/photos/pic.jpg")
    assert full == os.path.join(base, "photos", "pic.jpg")
