"""Tests for the local JSON preferences store."""

import json

import simple_firearm_logbook as app
from sfl import paths
from sfl import prefs as prefs_module

# Placed in the settings folder's name so a log line that leaks the file
# path shows up in the assertions.
MARKER = "MARKERWORD"


def _use_pref_path(monkeypatch, tmp_path):
    folder = tmp_path / MARKER
    folder.mkdir(exist_ok=True)
    pref_path = folder / "simple_firearm_logbook.pref"
    monkeypatch.setattr(paths, "_pref_path", lambda: str(pref_path))
    return pref_path


def _assert_no_path_in(lines):
    for line in lines:
        assert MARKER not in line


def test_load_prefs_returns_empty_dict_when_file_is_missing(monkeypatch, tmp_path):
    _use_pref_path(monkeypatch, tmp_path)

    assert app.load_prefs() == {}


def test_load_prefs_returns_saved_dict(monkeypatch, tmp_path):
    pref_path = _use_pref_path(monkeypatch, tmp_path)
    pref_path.write_text('{"theme": "light"}', encoding="utf-8")

    assert app.load_prefs() == {"theme": "light"}


def test_load_prefs_keeps_a_damaged_file_aside_and_logs_it(monkeypatch, tmp_path):
    pref_path = _use_pref_path(monkeypatch, tmp_path)
    pref_path.write_text("not json", encoding="utf-8")
    logged = []

    assert app.load_prefs(logged.append) == {}

    assert not pref_path.exists()
    kept = list(pref_path.parent.glob(pref_path.name + ".corrupt-*"))
    assert len(kept) == 1
    assert kept[0].read_text(encoding="utf-8") == "not json"
    assert logged == ["Settings file unreadable, kept aside: JSONDecodeError"]
    _assert_no_path_in(logged)


def test_load_prefs_keeps_non_object_json_aside(monkeypatch, tmp_path):
    pref_path = _use_pref_path(monkeypatch, tmp_path)
    pref_path.write_text('["light"]', encoding="utf-8")
    logged = []

    assert app.load_prefs(logged.append) == {}

    kept = list(pref_path.parent.glob(pref_path.name + ".corrupt-*"))
    assert [k.read_text(encoding="utf-8") for k in kept] == ['["light"]']
    assert logged == ["Settings file unreadable, kept aside: ValueError"]


def test_load_prefs_leaves_a_locked_file_in_place(monkeypatch, tmp_path):
    pref_path = _use_pref_path(monkeypatch, tmp_path)
    pref_path.write_text('{"theme": "light"}', encoding="utf-8")
    logged = []

    def locked_open(path, *args, **kwargs):
        raise PermissionError(13, "Permission denied", path)

    monkeypatch.setattr(prefs_module, "open", locked_open, raising=False)

    assert app.load_prefs(logged.append) == {}

    monkeypatch.delattr(prefs_module, "open")
    assert pref_path.read_text(encoding="utf-8") == '{"theme": "light"}'
    assert list(pref_path.parent.glob("*.corrupt-*")) == []
    assert logged == ["Settings file not read: PermissionError: Permission denied"]
    _assert_no_path_in(logged)


def test_save_prefs_failed_write_leaves_existing_settings_unchanged(monkeypatch, tmp_path):
    pref_path = _use_pref_path(monkeypatch, tmp_path)
    original = '{"theme": "dark", "window": {"x": 1, "y": 2, "width": 3, "height": 4}}'
    pref_path.write_text(original, encoding="utf-8")
    logged = []

    def failing_replace(src, dst):
        raise PermissionError(13, "Permission denied", dst)

    monkeypatch.setattr(prefs_module.os, "replace", failing_replace)

    assert app.save_prefs({"theme": "light"}, logged.append) is False

    monkeypatch.undo()
    assert pref_path.read_text(encoding="utf-8") == original
    assert [p.name for p in pref_path.parent.iterdir()] == [pref_path.name]
    assert logged == ["Could not save settings: PermissionError: Permission denied"]
    _assert_no_path_in(logged)


def test_save_prefs_writes_json_and_reports_success(monkeypatch, tmp_path):
    pref_path = _use_pref_path(monkeypatch, tmp_path)
    prefs = {"theme": "dark", "window": {"x": 10, "y": 20}}

    assert app.save_prefs(prefs) is True
    assert json.loads(pref_path.read_text(encoding="utf-8")) == prefs
    assert [p.name for p in pref_path.parent.iterdir()] == [pref_path.name]


def test_save_prefs_reports_failure_when_target_cannot_be_opened(monkeypatch, tmp_path):
    monkeypatch.setattr(paths, "_pref_path", lambda: str(tmp_path))

    assert app.save_prefs({"theme": "light"}) is False


def test_save_theme_preserves_existing_window_preferences(monkeypatch, tmp_path):
    pref_path = _use_pref_path(monkeypatch, tmp_path)
    window = {"x": 10, "y": 20, "width": 900, "height": 700}
    pref_path.write_text(json.dumps({"theme": "dark", "window": window}), encoding="utf-8")
    api = object.__new__(app.Api)
    api._log = lambda _message: None

    assert api.save_theme("light") == {"ok": True}
    assert app.load_prefs() == {"theme": "light", "window": window}
