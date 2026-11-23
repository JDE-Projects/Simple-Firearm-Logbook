"""Preferences: a small local JSON file next to the app, not stored in the
db. Holds the theme choice and saved window geometry.

Every setting lives in this one file, so callers always read, merge, and
write through load_prefs / save_prefs. Writes go to a temporary file that is
then swapped in with os.replace, so a crash mid-write leaves the previous
file whole. A file that opens but will not parse is kept aside, not wiped;
one that cannot be opened (locked, no access) is left in place. Log lines
carry describe_error(e) only, never a path or the error's own message."""
import json
import os
import tempfile
import time

from sfl import paths
from sfl.debug_log import describe_error


def _log_quietly(log, msg: str) -> None:
    if log is None:
        return
    try:
        log(msg)
    except Exception:
        pass


def load_prefs(log=None) -> dict:
    """Full prefs dict. Missing file: first run. Damaged file: kept aside, logged."""
    path = paths._pref_path()
    try:
        with open(path, "rb") as f:
            raw = f.read()
    except FileNotFoundError:
        return {}
    except OSError as e:
        # Couldn't be read (locked, no access): not damaged, so left in place.
        _log_quietly(log, f"Settings file not read: {describe_error(e)}")
        return {}
    try:
        data = json.loads(raw.decode("utf-8"))
        if isinstance(data, dict):
            return data
        raise ValueError("prefs root is not an object")
    except Exception as e:
        reason = describe_error(e)
        try:
            aside = f"{path}.corrupt-{time.strftime('%Y%m%d-%H%M%S')}"
            os.replace(path, aside)
            _log_quietly(log, f"Settings file unreadable, kept aside: {reason}")
        except Exception as move_error:
            _log_quietly(log, f"Settings file unreadable ({reason}), not kept "
                              f"aside: {describe_error(move_error)}")
        return {}


def save_prefs(prefs: dict, log=None) -> bool:
    """Write atomically. A False result must surface a visible error where
    the user is waiting on the save."""
    path = paths._pref_path()
    try:
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path) or ".",
                                   prefix=".pref_", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(prefs, f)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, path)
            return True
        except Exception:
            try:
                os.remove(tmp)
            except OSError as e:
                _log_quietly(log, "Temporary settings file not removed: "
                                  f"{describe_error(e)}")
            raise
    except Exception as e:
        _log_quietly(log, f"Could not save settings: {describe_error(e)}")
        return False
