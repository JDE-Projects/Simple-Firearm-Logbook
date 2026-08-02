"""Tests for the update-check version comparison helper."""

import pytest

from simple_firearm_logbook import Api


@pytest.mark.parametrize(
    ("latest", "current"),
    [
        ("1.3.1", "1.3.0"),
        ("1.4.0", "1.3.9"),
        ("2.0.0", "1.99.99"),
    ],
)
def test_newer_versions_are_detected(latest, current):
    assert Api._is_newer(latest, current) is True


@pytest.mark.parametrize(
    ("latest", "current"),
    [
        ("1.3.0", "1.3.0"),
        ("1.2.9", "1.3.0"),
        ("0.99.99", "1.0.0"),
    ],
)
def test_equal_or_older_versions_are_not_updates(latest, current):
    assert Api._is_newer(latest, current) is False


def test_non_numeric_version_part_uses_existing_zero_fallback():
    assert Api._is_newer("1.preview.1", "1.0.0") is True
