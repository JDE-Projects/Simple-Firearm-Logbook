# Apps place this file in tests/; it tests the copied sfl/debug_log.py module.
import builtins
import glob
import json
import os
import re

from sfl.debug_log import DebugLog


def _stamped_name(stamp, suffix=None):
    return f"Debug_Log_{stamp}" + (f"_{suffix}" if suffix else "") + ".txt"


def make_log(tmp_path, stamp, size=50, suffix=None):
    path = tmp_path / _stamped_name(stamp, suffix)
    path.write_bytes(b"x" * size)
    return path


def read(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def test_active_file_never_exceeds_cap_and_rolls_over(tmp_path):
    dbg = DebugLog(str(tmp_path), "Test App", max_bytes=2000, keep_older=3)
    assert dbg.set_enabled(True)

    for i in range(60):
        dbg.log(f"entry {i}", "x" * 40)
        assert os.path.getsize(dbg._path) <= 2000

    logs = sorted(glob.glob(str(tmp_path / "Debug_Log_*.txt")))
    assert len(logs) > 1


def test_oversized_entry_is_truncated_and_marked(tmp_path):
    dbg = DebugLog(str(tmp_path), "Test App", max_bytes=500, keep_older=3)
    assert dbg.set_enabled(True)

    dbg.log("big", "A" * 5000)

    assert os.path.getsize(dbg._path) <= 500
    assert "[entry truncated]" in read(dbg._path)


def test_prune_keeps_newest_deletes_oldest_first(tmp_path):
    make_log(tmp_path, "01012026_010000", size=50)
    make_log(tmp_path, "01022026_010000", size=50)
    make_log(tmp_path, "01032026_010000", size=50)
    make_log(tmp_path, "01042026_010000", size=50)
    make_log(tmp_path, "01052026_010000", size=50)

    dbg = DebugLog(str(tmp_path), "Test App", max_bytes=2000, keep_older=3)
    dbg.prune()

    remaining = {os.path.basename(p) for p in glob.glob(str(tmp_path / "Debug_Log_*.txt"))}
    assert remaining == {
        _stamped_name("01032026_010000"),
        _stamped_name("01042026_010000"),
        _stamped_name("01052026_010000"),
    }


def test_prune_size_ceiling_deletes_oversized_old_file_under_count_limit(tmp_path):
    make_log(tmp_path, "01032026_010000", size=100)   # newest
    make_log(tmp_path, "01022026_010000", size=100)
    make_log(tmp_path, "01012026_010000", size=8000)  # oldest, alone exceeds the ceiling

    dbg = DebugLog(str(tmp_path), "Test App", max_bytes=2000, keep_older=3)  # ceiling = 6000
    dbg.prune()

    remaining = {os.path.basename(p) for p in glob.glob(str(tmp_path / "Debug_Log_*.txt"))}
    assert remaining == {
        _stamped_name("01032026_010000"),
        _stamped_name("01022026_010000"),
    }


def test_prune_orders_by_parsed_date_not_text(tmp_path):
    # "01012026" sorts before "12312025" as text, but January 2026 is later.
    make_log(tmp_path, "12312025_010000", size=50)
    make_log(tmp_path, "01012026_010000", size=50)

    dbg = DebugLog(str(tmp_path), "Test App", max_bytes=2000, keep_older=1)
    dbg.prune()

    remaining = {os.path.basename(p) for p in glob.glob(str(tmp_path / "Debug_Log_*.txt"))}
    assert remaining == {_stamped_name("01012026_010000")}


def test_same_second_name_clash_gets_suffix(tmp_path, monkeypatch):
    import sfl.debug_log as debug_log_module

    class FixedDateTime(debug_log_module.datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 1, 1, 12, 0, 0)

    monkeypatch.setattr(debug_log_module, "datetime", FixedDateTime)

    make_log(tmp_path, "01012026_120000", size=10)

    dbg = DebugLog(str(tmp_path), "Test App", max_bytes=2000, keep_older=3)
    assert dbg.set_enabled(True)

    assert os.path.basename(dbg._path) == _stamped_name("01012026_120000", suffix="2")


def test_locked_file_is_skipped_others_still_deleted(tmp_path, monkeypatch):
    locked = make_log(tmp_path, "01012026_010000", size=50)
    make_log(tmp_path, "01022026_010000", size=50)
    make_log(tmp_path, "01032026_010000", size=50)
    make_log(tmp_path, "01042026_010000", size=50)

    real_remove = os.remove

    def fake_remove(path):
        if os.path.basename(path) == os.path.basename(str(locked)):
            raise PermissionError("file is open in another program")
        real_remove(path)

    monkeypatch.setattr("sfl.debug_log.os.remove", fake_remove)

    warnings = []
    dbg = DebugLog(
        str(tmp_path), "Test App", max_bytes=2000, keep_older=1,
        on_warning=warnings.append,
    )
    dbg.prune()

    remaining = {os.path.basename(p) for p in glob.glob(str(tmp_path / "Debug_Log_*.txt"))}
    # The locked (oldest) file survives; only one other should remain (keep_older=1).
    assert _stamped_name("01012026_010000") in remaining
    assert len(remaining) == 2
    assert len(warnings) == 1

    # Retried on the next prune, still only warns once.
    dbg.prune()
    assert len(warnings) == 1


def test_files_outside_pattern_are_left_alone(tmp_path):
    make_log(tmp_path, "01012026_010000", size=50)  # a real, prunable log

    (tmp_path / "Debug_Log_notes.txt").write_text("not a log", encoding="utf-8")
    (tmp_path / "Debug_Log_13452025_000000.txt").write_text("bad date", encoding="utf-8")
    (tmp_path / "notes.txt").write_text("unrelated", encoding="utf-8")
    # A different stamp than the real log above: on a case-insensitive
    # filesystem (Windows/NTFS default), the same stamp would collide with it.
    (tmp_path / "debug_log_01072026_010000.txt").write_text("wrong case", encoding="utf-8")

    subfolder = tmp_path / "Debug_Log_01052026_010000.txt"
    subfolder.mkdir()
    (subfolder / "Debug_Log_01062026_010000.txt").write_text("inside a folder", encoding="utf-8")

    dbg = DebugLog(str(tmp_path), "Test App", max_bytes=2000, keep_older=0)
    dbg.prune()

    assert (tmp_path / "Debug_Log_notes.txt").exists()
    assert (tmp_path / "Debug_Log_13452025_000000.txt").exists()
    assert (tmp_path / "notes.txt").exists()
    assert (tmp_path / "debug_log_01072026_010000.txt").exists()
    assert subfolder.exists()
    assert (subfolder / "Debug_Log_01062026_010000.txt").exists()

    # The one real log matched the pattern and had a valid date, so with
    # keep_older=0 it was eligible for deletion and should be gone.
    assert not (tmp_path / _stamped_name("01012026_010000")).exists()


def test_active_file_is_never_deleted(tmp_path):
    make_log(tmp_path, "01012026_010000", size=50)

    dbg = DebugLog(str(tmp_path), "Test App", max_bytes=2000, keep_older=0)
    assert dbg.set_enabled(True)
    active_path = dbg._path

    dbg.prune()

    assert os.path.exists(active_path)


def test_write_failure_turns_logging_off_and_warns_without_raising(tmp_path, monkeypatch):
    import sfl.debug_log as debug_log_module

    warnings = []
    dbg = DebugLog(str(tmp_path), "Test App", max_bytes=2000, keep_older=3, on_warning=warnings.append)
    assert dbg.set_enabled(True)

    real_open = builtins.open

    def fake_open(file, mode="r", *args, **kwargs):
        if "a" in mode:
            raise OSError("simulated disk failure")
        return real_open(file, mode, *args, **kwargs)

    monkeypatch.setattr(debug_log_module, "open", fake_open, raising=False)

    dbg.log("should fail")  # must not raise

    assert dbg.is_enabled() is False
    assert len(warnings) == 1


def test_write_failure_warning_names_file_and_plain_reason(tmp_path, monkeypatch):
    import sfl.debug_log as debug_log_module

    warnings = []
    dbg = DebugLog(str(tmp_path), "Test App", on_warning=warnings.append)
    assert dbg.set_enabled(True)
    name = os.path.basename(dbg._path)

    def fake_open(file, mode="r", *args, **kwargs):
        raise PermissionError(13, "Permission denied", file)

    monkeypatch.setattr(debug_log_module, "open", fake_open, raising=False)
    dbg.log("should fail")

    assert warnings == [f"Debug log: write failed for {name} (Permission denied). Logging turned off."]

def test_redact_is_applied_to_label_and_content(tmp_path):
    def redact(text):
        return re.sub(r"secret\w*", "[redacted]", text)

    dbg = DebugLog(str(tmp_path), "Test App", redact=redact, max_bytes=2000, keep_older=3)
    assert dbg.set_enabled(True)

    dbg.log("has a secretlabel", {"password": "secretvalue"})

    contents = read(dbg._path)
    assert "secretlabel" not in contents
    assert "secretvalue" not in contents
    assert "[redacted]" in contents


def test_log_dumps_dict_and_list_as_indented_json(tmp_path):
    dbg = DebugLog(str(tmp_path), "Test App", max_bytes=2000, keep_older=3)
    assert dbg.set_enabled(True)

    payload = {"a": 1, "b": [1, 2, 3]}
    dbg.log("payload", payload)

    contents = read(dbg._path)
    assert json.dumps(payload, indent=2) in contents


def test_header_and_timestamp_format(tmp_path):
    dbg = DebugLog(str(tmp_path), "Test App", max_bytes=2000, keep_older=3)
    assert dbg.set_enabled(True)
    dbg.log("hello")

    contents = read(dbg._path)
    assert "=== Test App debug log ===" in contents
    assert re.search(r"\[\d{2}:\d{2}:\d{2}\.\d{3}\] hello", contents)


def test_prune_warning_is_also_written_into_active_log(tmp_path, monkeypatch):
    locked = make_log(tmp_path, "01012026_010000", size=50)

    def fake_remove(path):
        raise PermissionError("file is open in another program")

    monkeypatch.setattr("sfl.debug_log.os.remove", fake_remove)

    warnings = []
    dbg = DebugLog(str(tmp_path), "Test App", max_bytes=2000, keep_older=0, on_warning=warnings.append)
    assert dbg.set_enabled(True)

    assert len(warnings) == 1
    assert os.path.basename(str(locked)) in read(dbg._path)


def test_reenable_after_write_failure_starts_a_fresh_file(tmp_path, monkeypatch):
    import sfl.debug_log as debug_log_module

    dbg = DebugLog(str(tmp_path), "Test App", max_bytes=2000, keep_older=3)
    assert dbg.set_enabled(True)
    first_path = dbg._path

    real_open = builtins.open

    def fake_open(file, mode="r", *args, **kwargs):
        if "a" in mode:
            raise OSError("simulated disk failure")
        return real_open(file, mode, *args, **kwargs)

    monkeypatch.setattr(debug_log_module, "open", fake_open, raising=False)
    dbg.log("should fail")
    monkeypatch.undo()

    assert dbg.set_enabled(True)
    assert dbg._path != first_path
    dbg.log("after recovery")
    assert "after recovery" in read(dbg._path)


def test_failed_rollover_keeps_the_active_file_before_pruning(tmp_path, monkeypatch):
    warnings = []
    dbg = DebugLog(
        str(tmp_path), "Test App", max_bytes=2000, keep_older=3,
        on_warning=warnings.append,
    )
    assert dbg.set_enabled(True)
    active_path = dbg._path
    for stamp in ("12312026_010000", "12312026_020000", "12312026_030000"):
        make_log(tmp_path, stamp, size=50)

    monkeypatch.setattr(dbg, "_write_new_file", lambda path: None)
    dbg.log("roll over", "x" * 3000)

    assert os.path.exists(active_path)
    assert dbg.is_enabled() is False
    assert len(warnings) == 1


def test_existing_picked_name_is_not_overwritten(tmp_path, monkeypatch):
    existing = tmp_path / _stamped_name("01012026_010000")
    existing.write_text("keep this content", encoding="utf-8")
    warnings = []
    dbg = DebugLog(str(tmp_path), "Test App", on_warning=warnings.append)
    monkeypatch.setattr(dbg, "_next_stamped_path", lambda: str(existing))

    assert dbg.set_enabled(True) is False

    assert read(existing) == "keep this content"
    assert dbg.is_enabled() is False
    assert len(warnings) == 1
