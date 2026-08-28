"""
Tests for the photo import decompression-bomb guards in
simple_firearm_logbook.py.

Two independent guards protect add_photos, both failing loudly (counted as
failed, never silently dropped): a pixel-count ceiling PIL itself enforces
on every Image.open() call, and a file-size pre-check that refuses to even
open an oversize source.
"""

import simple_firearm_logbook as app
from PIL import Image


def test_max_image_pixels_is_set_to_64_megapixel_ceiling():
    assert Image.MAX_IMAGE_PIXELS == 64_000_000


def test_photo_source_too_large_flags_a_file_over_the_limit(monkeypatch):
    monkeypatch.setattr(app.os.path, "getsize", lambda path: app.MAX_IMAGE_BYTES + 1)
    assert app._photo_source_too_large("fake.jpg") is True


def test_photo_source_too_large_allows_a_file_at_or_under_the_limit(monkeypatch):
    monkeypatch.setattr(app.os.path, "getsize", lambda path: app.MAX_IMAGE_BYTES)
    assert app._photo_source_too_large("fake.jpg") is False
