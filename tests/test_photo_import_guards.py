"""
Tests for the photo import decompression-bomb guards in
simple_firearm_logbook.py.

Two independent guards protect add_photos, both failing loudly (counted as
failed, never silently dropped): a pixel-count ceiling PIL itself enforces on
every Image.open() call, and file-size pre-checks (bytes, and for the
drag-and-drop path, the still-encoded base64 length) that refuse to even open
an oversize source.

MAX_IMAGE_PIXELS is set to 32,000,000, not the ~64 megapixel ceiling itself:
Pillow only raises DecompressionBombError above 2x MAX_IMAGE_PIXELS (it just
warns between 1x and 2x), so this is what makes the hard rejection land at
~64 megapixels instead of quietly doubling to ~128.
"""

import base64
import warnings

import simple_firearm_logbook as app
from PIL import Image


def test_max_image_pixels_set_so_the_real_ceiling_lands_at_64_megapixels():
    assert Image.MAX_IMAGE_PIXELS == 32_000_000


def test_pillow_rejects_just_over_the_64_megapixel_ceiling():
    # Exercises Pillow's own check directly, without allocating a giant
    # image: dimensions whose product is just over 2x MAX_IMAGE_PIXELS.
    dims = (8001, 8001)  # 64,016,001 pixels
    assert dims[0] * dims[1] > 64_000_000
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", Image.DecompressionBombWarning)
        try:
            Image._decompression_bomb_check(dims)
        except Image.DecompressionBombError:
            pass
        else:
            raise AssertionError("expected DecompressionBombError")


def test_pillow_allows_just_under_the_64_megapixel_ceiling():
    dims = (7999, 7999)  # 63,984,001 pixels
    assert dims[0] * dims[1] < 64_000_000
    with warnings.catch_warnings():
        # A DecompressionBombWarning is fine and expected between 1x and 2x
        # MAX_IMAGE_PIXELS; only the Error matters here.
        warnings.simplefilter("ignore", Image.DecompressionBombWarning)
        Image._decompression_bomb_check(dims)


def test_photo_source_too_large_flags_a_file_over_the_limit(monkeypatch):
    monkeypatch.setattr(app.os.path, "getsize", lambda path: app.MAX_IMAGE_BYTES + 1)
    assert app._photo_source_too_large("fake.jpg") is True


def test_photo_source_too_large_allows_a_file_at_or_under_the_limit(monkeypatch):
    monkeypatch.setattr(app.os.path, "getsize", lambda path: app.MAX_IMAGE_BYTES)
    assert app._photo_source_too_large("fake.jpg") is False


def test_photo_b64_too_large_flags_an_encoded_payload_over_the_limit(monkeypatch):
    # Byte counts chosen as multiples of 3 so base64 needs no padding and
    # the (len * 3) // 4 estimate lines up exactly with the real size.
    monkeypatch.setattr(app, "MAX_IMAGE_BYTES", 9)
    raw = base64.b64encode(b"x" * 12).decode("ascii")
    assert app._photo_b64_too_large(raw) is True


def test_photo_b64_too_large_allows_an_encoded_payload_at_or_under_the_limit(monkeypatch):
    monkeypatch.setattr(app, "MAX_IMAGE_BYTES", 9)
    raw = base64.b64encode(b"x" * 9).decode("ascii")
    assert app._photo_b64_too_large(raw) is False
