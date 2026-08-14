"""
Tests for optimize_image_to_jpeg in simple_firearm_logbook.py.

On import, every photo is scaled down (long edge only, never upscaled) and
re-encoded as JPEG, so a big phone photo shrinks a lot while a serial-number
close-up stays readable. The contract that matters:
  - the long edge is capped at PHOTO_MAX_EDGE, aspect ratio preserved;
  - a smaller image is left at its own size (no upscaling);
  - the output is always a JPEG;
  - the recorded EXIF rotation is baked in, so nothing is stored sideways;
  - transparency is flattened (JPEG can't hold an alpha channel);
  - the source file is never modified;
  - an unreadable/non-image source raises, so the caller can refuse to store it.
"""

import pytest
from PIL import Image

import simple_firearm_logbook as app


def _make_image(path, size, mode="RGB", color=(120, 60, 30), exif=None):
    img = Image.new(mode, size, color)
    if exif is not None:
        img.save(path, exif=exif)
    else:
        img.save(path)


def test_large_image_is_scaled_to_max_edge(tmp_path):
    src = str(tmp_path / "big.png")
    out = str(tmp_path / "out.jpg")
    _make_image(src, (4000, 2000))
    app.optimize_image_to_jpeg(src, out)
    with Image.open(out) as result:
        assert max(result.size) == app.PHOTO_MAX_EDGE
        # Aspect ratio (2:1) preserved.
        assert result.size == (app.PHOTO_MAX_EDGE, app.PHOTO_MAX_EDGE // 2)


def test_tall_image_caps_the_long_edge_not_the_short_one(tmp_path):
    src = str(tmp_path / "tall.png")
    out = str(tmp_path / "out.jpg")
    _make_image(src, (1000, 5000))
    app.optimize_image_to_jpeg(src, out)
    with Image.open(out) as result:
        assert max(result.size) == app.PHOTO_MAX_EDGE
        assert result.size[1] == app.PHOTO_MAX_EDGE


def test_small_image_is_not_upscaled(tmp_path):
    src = str(tmp_path / "small.png")
    out = str(tmp_path / "out.jpg")
    _make_image(src, (800, 600))
    app.optimize_image_to_jpeg(src, out)
    with Image.open(out) as result:
        assert result.size == (800, 600)


def test_output_is_always_jpeg(tmp_path):
    src = str(tmp_path / "in.png")
    out = str(tmp_path / "out.jpg")
    _make_image(src, (500, 500))
    app.optimize_image_to_jpeg(src, out)
    with Image.open(out) as result:
        assert result.format == "JPEG"


def test_transparency_is_flattened_without_error(tmp_path):
    src = str(tmp_path / "alpha.png")
    out = str(tmp_path / "out.jpg")
    # A semi-transparent RGBA image would make JPEG choke if not flattened.
    _make_image(src, (300, 300), mode="RGBA", color=(200, 50, 50, 40))
    app.optimize_image_to_jpeg(src, out)
    with Image.open(out) as result:
        assert result.mode == "RGB"
        assert result.format == "JPEG"


def test_exif_orientation_is_baked_in(tmp_path):
    src = str(tmp_path / "rotated.jpg")
    out = str(tmp_path / "out.jpg")
    # Orientation tag 6 means "rotate 90 for display", so a stored 100x200 image
    # should come out 200x100 after the rotation is applied.
    base = Image.new("RGB", (100, 200), (10, 20, 30))
    exif = base.getexif()
    exif[0x0112] = 6
    base.save(src, exif=exif.tobytes())
    app.optimize_image_to_jpeg(src, out)
    with Image.open(out) as result:
        assert result.size == (200, 100)


def test_source_file_is_never_modified(tmp_path):
    src = str(tmp_path / "orig.png")
    out = str(tmp_path / "out.jpg")
    _make_image(src, (3000, 3000))
    before = open(src, "rb").read()
    app.optimize_image_to_jpeg(src, out)
    after = open(src, "rb").read()
    assert before == after


def test_non_image_source_raises(tmp_path):
    src = str(tmp_path / "notanimage.jpg")
    out = str(tmp_path / "out.jpg")
    with open(src, "w", encoding="utf-8") as f:
        f.write("this is not image data")
    with pytest.raises(Exception):
        app.optimize_image_to_jpeg(src, out)
