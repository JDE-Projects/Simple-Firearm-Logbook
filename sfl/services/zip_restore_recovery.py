"""Crash-safe recovery for archive restores performed beside the data files."""
import json
import os
import re
import shutil

from sfl import config

RECORD_FILENAME = "simple_firearm_logbook.zip_restore.json"
STAGING_NAME_RE = re.compile(r"^\.sfl_zip_restore_stage-[0-9a-f]{8}\Z", re.ASCII)
ASIDE_NAME_RE = re.compile(r"^\.sfl_zip_restore_aside-[0-9a-f]{8}\Z", re.ASCII)
PARTS = (
    ("database", config.DB_FILENAME, "file"),
    ("journal", config.DB_FILENAME + "-journal", "file"),
    ("photos", config.PHOTOS_DIRNAME, "dir"),
    ("attachments", config.ATTACHMENTS_DIRNAME, "dir"),
)


def record_path(folder):
    return os.path.join(folder, RECORD_FILENAME)


def _path(folder, name):
    return os.path.join(folder, name)


def _exists_kind(path, kind):
    return os.path.isfile(path) if kind == "file" else os.path.isdir(path)


def _present(path):
    return os.path.lexists(path)


def _fsync_file(path):
    with open(path, "r+b") as file:
        os.fsync(file.fileno())


def write_record(folder, record):
    path = record_path(folder)
    temporary = path + ".tmp"
    with open(temporary, "w", encoding="utf-8", newline="\n") as file:
        json.dump(record, file, sort_keys=True)
        file.flush()
        os.fsync(file.fileno())
    os.replace(temporary, path)


def _valid_record(record):
    required = {"stage", "staging_name", "aside_name", "parts"}
    if not isinstance(record, dict) or set(record) != required:
        return False
    if record["stage"] not in {"prepared", "aborting", "rolled_back", "swapped"}:
        return False
    if not isinstance(record["staging_name"], str) or not isinstance(record["aside_name"], str):
        return False
    if not STAGING_NAME_RE.fullmatch(record["staging_name"]) or not ASIDE_NAME_RE.fullmatch(record["aside_name"]):
        return False
    if record["staging_name"].rsplit("-", 1)[1] != record["aside_name"].rsplit("-", 1)[1]:
        return False
    if not isinstance(record["parts"], dict) or set(record["parts"]) != {part[0] for part in PARTS}:
        return False
    for name, _, _ in PARTS:
        flags = record["parts"][name]
        if not isinstance(flags, dict) or set(flags) != {"live", "staged"}:
            return False
        if not all(isinstance(flags[key], bool) for key in flags):
            return False
    return True


def read_record(folder):
    try:
        with open(record_path(folder), encoding="utf-8") as file:
            record = json.load(file)
    except Exception:
        return None
    return record if _valid_record(record) else None


def record_names(folder):
    record = read_record(folder)
    return {record["staging_name"], record["aside_name"]} if record else set()


def make_record(folder, staging_dir):
    staging_name = os.path.basename(staging_dir)
    token = staging_name.rsplit("-", 1)[1]
    aside_name = f".sfl_zip_restore_aside-{token}"
    parts = {}
    for name, filename, kind in PARTS:
        parts[name] = {
            "live": _exists_kind(_path(folder, filename), kind),
            "staged": _exists_kind(_path(staging_dir, filename), kind),
        }
    return {"stage": "prepared", "staging_name": staging_name, "aside_name": aside_name, "parts": parts}


def _delete(path):
    if not os.path.lexists(path):
        return True
    try:
        if os.path.isdir(path) and not os.path.islink(path):
            shutil.rmtree(path)
        else:
            os.remove(path)
        return True
    except OSError:
        return False


def _cleanup(folder, record):
    if not _delete(_path(folder, record["aside_name"])):
        return False
    if not _delete(_path(folder, record["staging_name"])):
        return False
    return _delete(record_path(folder))


def _live_matches(folder, record, flag):
    for name, filename, kind in PARTS:
        expected = record["parts"][name][flag]
        path = _path(folder, filename)
        if expected != _exists_kind(path, kind) or (not expected and _present(path)):
            return False
    return True


def undo(folder, record):
    staging = _path(folder, record["staging_name"])
    aside = _path(folder, record["aside_name"])
    if not os.path.isdir(staging) or not os.path.isdir(aside):
        return False
    # First preserve every staged item currently live.  Never replace or delete it.
    for name, filename, kind in reversed(PARTS):
        flags = record["parts"][name]
        live, staged, original = _path(folder, filename), _path(staging, filename), _path(aside, filename)
        if _exists_kind(live, kind) and flags["staged"] and (not flags["live"] or _exists_kind(original, kind)):
            if _present(staged):
                return False
            os.replace(live, staged)
        elif _present(live) and not _exists_kind(live, kind):
            return False
    # Then put originals back.  A live original means a previous recovery completed this part.
    for name, filename, kind in PARTS:
        flags = record["parts"][name]
        live, original = _path(folder, filename), _path(aside, filename)
        if _exists_kind(original, kind):
            if _present(live):
                return False
            os.replace(original, live)
        elif flags["live"] and not _exists_kind(live, kind):
            return False
        elif _present(original):
            return False
    if not _live_matches(folder, record, "live"):
        return False
    write_record(folder, {**record, "stage": "rolled_back"})
    return _cleanup(folder, record)


def complete_swapped(folder, record):
    if not _live_matches(folder, record, "staged"):
        return "mismatch"
    return "cleaned" if _cleanup(folder, record) else "cleanup_pending"


def recover(folder, log):
    """Return none, finished, undone, cleanup_pending, or stopped.  Exceptions intentionally stop launch."""
    path = record_path(folder)
    if not os.path.exists(path):
        _remove_unrecorded(folder, log)
        return "none"
    record = read_record(folder)
    if record is None:
        return "stopped"
    if record["stage"] in {"prepared", "aborting"}:
        if record["stage"] == "prepared":
            write_record(folder, {**record, "stage": "aborting"})
            record = {**record, "stage": "aborting"}
        if undo(folder, record):
            return "undone"
        completed = read_record(folder)
        if completed and completed["stage"] == "rolled_back" and _live_matches(folder, completed, "live"):
            return "cleanup_pending"
        return "stopped"
    if record["stage"] == "rolled_back":
        return "undone" if _cleanup(folder, record) else "cleanup_pending"
    completion = complete_swapped(folder, record)
    if completion == "cleaned":
        return "finished"
    return "cleanup_pending" if completion == "cleanup_pending" else "stopped"


def _remove_unrecorded(folder, log):
    try:
        entries = list(os.scandir(folder))
    except OSError as error:
        log(f"ZIP restore recovery: couldn't scan staging folders ({type(error).__name__})")
        return
    for entry in entries:
        if not (STAGING_NAME_RE.fullmatch(entry.name) or ASIDE_NAME_RE.fullmatch(entry.name)):
            continue
        try:
            if entry.is_symlink() or getattr(entry, "is_junction", lambda: False)() or not entry.is_dir(follow_symlinks=False):
                continue
            if not _delete(entry.path):
                log(f"ZIP restore recovery: couldn't remove preview folder {entry.name}")
        except OSError:
            log(f"ZIP restore recovery: couldn't inspect preview folder {entry.name}")
