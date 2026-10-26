import os
import sqlite3

import pytest

from sfl import config
from sfl.services import zip_restore_recovery as recovery


def _database(path, make):
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE firearms (make TEXT)")
    conn.execute("INSERT INTO firearms VALUES (?)", (make,))
    conn.commit()
    conn.close()


def _row(path):
    conn = sqlite3.connect(path)
    try:
        return conn.execute("SELECT make FROM firearms").fetchone()[0]
    finally:
        conn.close()


def _tree(folder, token="a1b2c3d4"):
    stage = folder / f".sfl_zip_restore_stage-{token}"
    aside = folder / f".sfl_zip_restore_aside-{token}"
    stage.mkdir()
    _database(folder / config.DB_FILENAME, "old")
    _database(stage / config.DB_FILENAME, "new")
    for root, prefix in ((folder, b"old"), (stage, b"new")):
        (root / config.PHOTOS_DIRNAME).mkdir()
        (root / config.PHOTOS_DIRNAME / "photo.jpg").write_bytes(prefix + b"-photo")
        (root / config.ATTACHMENTS_DIRNAME).mkdir()
        (root / config.ATTACHMENTS_DIRNAME / "doc.pdf").write_bytes(prefix + b"-doc")
    record = recovery.make_record(str(folder), str(stage))
    return stage, aside, record


def _contents(folder):
    return (
        _row(folder / config.DB_FILENAME),
        (folder / config.PHOTOS_DIRNAME / "photo.jpg").read_bytes(),
        (folder / config.ATTACHMENTS_DIRNAME / "doc.pdf").read_bytes(),
    )


def _forward(folder, record):
    aside = folder / record["aside_name"]
    aside.mkdir()
    recovery.write_record(str(folder), record)
    for name, filename, _ in recovery.PARTS:
        if record["parts"][name]["live"]:
            os.replace(folder / filename, aside / filename)
    for name, filename, _ in recovery.PARTS:
        if record["parts"][name]["staged"]:
            os.replace(folder / record["staging_name"] / filename, folder / filename)


def test_undo_uses_staged_flag_not_staging_path(tmp_path):
    stage, aside, record = _tree(tmp_path)
    _forward(tmp_path, record)
    record["parts"]["photos"]["staged"] = False
    # A live photo not recorded as staged must not be moved into staging.
    assert recovery.undo(str(tmp_path), record) is False
    assert (tmp_path / config.PHOTOS_DIRNAME / "photo.jpg").read_bytes() == b"new-photo"


@pytest.mark.parametrize("step", range(1, 7))
def test_interrupted_forward_move_recovers_old_contents(tmp_path, monkeypatch, step):
    stage, aside, record = _tree(tmp_path)
    aside.mkdir()
    recovery.write_record(str(tmp_path), record)
    moves = [(tmp_path / filename, aside / filename) for name, filename, _ in recovery.PARTS if record["parts"][name]["live"]]
    moves += [(stage / filename, tmp_path / filename) for name, filename, _ in recovery.PARTS if record["parts"][name]["staged"]]
    original = recovery.os.replace
    calls = 0

    def interrupt_after_move(source, destination):
        nonlocal calls
        original(source, destination)
        calls += 1
        if calls == step:
            raise KeyboardInterrupt

    monkeypatch.setattr(recovery.os, "replace", interrupt_after_move)
    with pytest.raises(KeyboardInterrupt):
        for source, destination in moves:
            recovery.os.replace(source, destination)
    monkeypatch.setattr(recovery.os, "replace", original)
    assert recovery.recover(str(tmp_path), lambda _: None) == "undone"
    assert _contents(tmp_path) == ("old", b"old-photo", b"old-doc")


def test_interrupted_swapped_record_recovers_new_contents(tmp_path):
    stage, _, record = _tree(tmp_path)
    _forward(tmp_path, record)
    recovery.write_record(str(tmp_path), {**record, "stage": "swapped"})
    assert recovery.recover(str(tmp_path), lambda _: None) == "finished"
    assert _contents(tmp_path) == ("new", b"new-photo", b"new-doc")
    assert not stage.exists()


@pytest.mark.parametrize("move_number", range(1, 7))
def test_interrupted_undo_retries_to_old_contents(tmp_path, monkeypatch, move_number):
    _, _, record = _tree(tmp_path)
    _forward(tmp_path, record)
    recovery.write_record(str(tmp_path), {**record, "stage": "aborting"})
    original = recovery.os.replace
    calls = 0

    def crash_after_move(source, destination):
        nonlocal calls
        original(source, destination)
        calls += 1
        if calls == move_number:
            raise KeyboardInterrupt

    monkeypatch.setattr(recovery.os, "replace", crash_after_move)
    with pytest.raises(KeyboardInterrupt):
        recovery.recover(str(tmp_path), lambda _: None)
    monkeypatch.setattr(recovery.os, "replace", original)
    assert recovery.recover(str(tmp_path), lambda _: None) == "undone"
    assert _contents(tmp_path) == ("old", b"old-photo", b"old-doc")


def test_rolled_back_record_and_failed_cleanup_retry(tmp_path, monkeypatch):
    _, _, record = _tree(tmp_path)
    _forward(tmp_path, record)
    original_delete = recovery._delete
    calls = 0

    def delete_then_fail(path):
        nonlocal calls
        calls += 1
        if calls == 2:
            return False
        return original_delete(path)

    monkeypatch.setattr(recovery, "_delete", delete_then_fail)
    assert recovery.recover(str(tmp_path), lambda _: None) == "cleanup_pending"
    monkeypatch.setattr(recovery, "_delete", original_delete)
    assert recovery.recover(str(tmp_path), lambda _: None) == "undone"
    assert _contents(tmp_path) == ("old", b"old-photo", b"old-doc")


def test_swapped_cleanup_pending_is_retryable_and_live_contents_stay_new(tmp_path, monkeypatch):
    _, _, record = _tree(tmp_path)
    _forward(tmp_path, record)
    record = {**record, "stage": "swapped"}
    recovery.write_record(str(tmp_path), record)
    monkeypatch.setattr(recovery, "_delete", lambda path: False)
    assert recovery.complete_swapped(str(tmp_path), record) == "cleanup_pending"
    assert _contents(tmp_path) == ("new", b"new-photo", b"new-doc")


def test_swapped_missing_restored_part_stops_and_keeps_originals(tmp_path):
    _, aside, record = _tree(tmp_path)
    _forward(tmp_path, record)
    record = {**record, "stage": "swapped"}
    recovery.write_record(str(tmp_path), record)
    os.remove(tmp_path / config.DB_FILENAME)
    assert recovery.recover(str(tmp_path), lambda _: None) == "stopped"
    assert _row(aside / config.DB_FILENAME) == "old"


def test_collision_and_missing_part_stop_without_deleting(tmp_path):
    stage, aside, record = _tree(tmp_path)
    _forward(tmp_path, record)
    (stage / config.PHOTOS_DIRNAME).mkdir()
    assert recovery.undo(str(tmp_path), record) is False
    assert (aside / config.DB_FILENAME).is_file()
    os.rmdir(stage / config.PHOTOS_DIRNAME)
    os.remove(aside / config.ATTACHMENTS_DIRNAME / "doc.pdf")
    os.rmdir(aside / config.ATTACHMENTS_DIRNAME)
    assert recovery.undo(str(tmp_path), record) is False
    assert (tmp_path / recovery.RECORD_FILENAME).is_file()
    assert _row(tmp_path / config.DB_FILENAME) == "old"


def test_unrecorded_staging_removed_but_link_left(tmp_path):
    plain = tmp_path / ".sfl_zip_restore_stage-a1b2c3d4"
    plain.mkdir()
    target = tmp_path / "target"
    target.mkdir()
    link = tmp_path / ".sfl_zip_restore_aside-b1c2d3e4"
    try:
        os.symlink(target, link, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks unavailable")
    assert recovery.recover(str(tmp_path), lambda _: None) == "none"
    assert not plain.exists()
    assert link.is_symlink()


@pytest.mark.parametrize("contents", [b"{", b"[]"])
def test_invalid_or_unreadable_record_stops(tmp_path, contents):
    (tmp_path / recovery.RECORD_FILENAME).write_bytes(contents)
    assert recovery.recover(str(tmp_path), lambda _: None) == "stopped"

