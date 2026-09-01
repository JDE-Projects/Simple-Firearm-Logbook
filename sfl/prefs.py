"""Preferences: a small local JSON file next to the app, not stored in the
db. Holds the theme choice and saved window geometry."""
import json

from sfl import paths


def load_prefs() -> dict:
    """Load the full prefs dict. Tolerant of a missing or corrupt file."""
    try:
        with open(paths._pref_path(), "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_prefs(prefs: dict) -> bool:
    try:
        with open(paths._pref_path(), "w", encoding="utf-8") as f:
            json.dump(prefs, f)
        return True
    except Exception:
        return False
