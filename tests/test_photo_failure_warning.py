"""
Tests for photo_failure_warning in simple_firearm_logbook.py.

Pins the exact wording shown to the user when photos are refused during
import, so add_photos and add_photos_from_data can never drift from each
other or from the approved copy.
"""

import simple_firearm_logbook as app


def test_no_failures_returns_none():
    assert app.photo_failure_warning({"not_image": 0, "damaged": 0, "too_large": 0}) is None


def test_single_not_image_singular():
    r = app.photo_failure_warning({"not_image": 1, "damaged": 0, "too_large": 0})
    assert r == "1 file wasn't an image and wasn't added."


def test_single_not_image_plural():
    r = app.photo_failure_warning({"not_image": 3, "damaged": 0, "too_large": 0})
    assert r == "3 files weren't images and weren't added."


def test_single_damaged_singular():
    r = app.photo_failure_warning({"not_image": 0, "damaged": 1, "too_large": 0})
    assert r == "1 image was damaged and wasn't added."


def test_single_damaged_plural():
    r = app.photo_failure_warning({"not_image": 0, "damaged": 2, "too_large": 0})
    assert r == "2 images were damaged and weren't added."


def test_single_too_large_singular():
    r = app.photo_failure_warning({"not_image": 0, "damaged": 0, "too_large": 1})
    assert r == "1 image was too large and wasn't added."


def test_single_too_large_plural():
    r = app.photo_failure_warning({"not_image": 0, "damaged": 0, "too_large": 4})
    assert r == "4 images were too large and weren't added."


def test_two_buckets_combined():
    r = app.photo_failure_warning({"not_image": 1, "damaged": 1, "too_large": 0})
    assert r == "2 files weren't added: 1 wasn't an image, 1 was damaged."


def test_two_buckets_combined_plural():
    r = app.photo_failure_warning({"not_image": 2, "damaged": 0, "too_large": 3})
    assert r == "5 files weren't added: 2 weren't images, 3 were too large."


def test_three_buckets_combined():
    r = app.photo_failure_warning({"not_image": 1, "damaged": 2, "too_large": 1})
    assert r == "4 files weren't added: 1 wasn't an image, 2 were damaged, 1 was too large."
