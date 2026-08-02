"""Tests for the local JSON preferences store."""

import json

import simple_firearm_logbook as app


def _use_pref_path(monkeypatch, tmp_path):
    pref_path = tmp_path / "simple_firearm_logbook.pref"
    monkeypatch.setattr(app, "_pref_path", lambda: str(pref_path))
    return pref_path


def test_load_prefs_returns_empty_dict_when_file_is_missing(monkeypatch, tmp_path):
    _use_pref_path(monkeypatch, tmp_path)

    assert app.load_prefs() == {}


def test_load_prefs_returns_saved_dict(monkeypatch, tmp_path):
    pref_path = _use_pref_path(monkeypatch, tmp_path)
    pref_path.write_text('{"theme": "light"}', encoding="utf-8")

    assert app.load_prefs() == {"theme": "light"}


def test_load_prefs_returns_empty_dict_for_corrupt_json(monkeypatch, tmp_path):
    pref_path = _use_pref_path(monkeypatch, tmp_path)
    pref_path.write_text("not json", encoding="utf-8")

    assert app.load_prefs() == {}


def test_load_prefs_returns_empty_dict_for_non_object_json(monkeypatch, tmp_path):
    pref_path = _use_pref_path(monkeypatch, tmp_path)
    pref_path.write_text('["light"]', encoding="utf-8")

    assert app.load_prefs() == {}


def test_save_prefs_writes_json_and_reports_success(monkeypatch, tmp_path):
    pref_path = _use_pref_path(monkeypatch, tmp_path)
    prefs = {"theme": "dark", "window": {"x": 10, "y": 20}}

    assert app.save_prefs(prefs) is True
    assert json.loads(pref_path.read_text(encoding="utf-8")) == prefs


def test_save_prefs_reports_failure_when_target_cannot_be_opened(monkeypatch, tmp_path):
    monkeypatch.setattr(app, "_pref_path", lambda: str(tmp_path))

    assert app.save_prefs({"theme": "light"}) is False


def test_save_theme_preserves_existing_window_preferences(monkeypatch, tmp_path):
    pref_path = _use_pref_path(monkeypatch, tmp_path)
    window = {"x": 10, "y": 20, "width": 900, "height": 700}
    pref_path.write_text(json.dumps({"theme": "dark", "window": window}), encoding="utf-8")
    api = object.__new__(app.Api)
    api.log = lambda _message: None

    assert api.save_theme("light") == {"ok": True}
    assert app.load_prefs() == {"theme": "light", "window": window}
