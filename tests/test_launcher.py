"""Tests for configurable package startup."""
import os

import pytest

from sfl import config, launcher
from sfl.db import NewerSchemaError


def test_default_description_matches_current_app_values():
    description = launcher.DEFAULT_APP_DESCRIPTION

    assert description.product_name == "Simple Firearm Logbook"
    assert description.app_id == "JDEProjects.SimpleFirearmLogbook"
    assert description.single_instance_name == "JDE_SimpleFirearmLogbook_SingleInstance"
    assert description.version == config.APP_VERSION
    assert description.update_owner == "JDE-Projects"
    assert description.update_repo == "Simple-Firearm-Logbook"
    assert description.update_link == (
        "https://github.com/JDE-Projects/Simple-Firearm-Logbook/blob/main/README.md#updating"
    )
    assert description.api_class is launcher.Api
    assert description.open_database is launcher.open_db
    assert description.extension_script is None
    assert description.extension_stylesheet is None


def test_run_uses_custom_api_and_database_opener_and_sets_config(monkeypatch, tmp_path):
    calls = {}
    monkeypatch.setattr(config, "APP_VERSION", config.APP_VERSION)
    monkeypatch.setattr(config, "GITHUB_OWNER", config.GITHUB_OWNER)
    monkeypatch.setattr(config, "GITHUB_REPO", config.GITHUB_REPO)

    class FakeEvent:
        def __iadd__(self, callback):
            calls.setdefault("callbacks", []).append(callback)
            return self

    class FakeWindow:
        title = "Embedding Logbook"
        events = type("Events", (), {"shown": FakeEvent(), "closing": FakeEvent()})()

    class FakeApi:
        def set_app_description(self, description):
            calls["description"] = description

        def set_conn(self, conn):
            calls["conn"] = conn

        def set_window(self, win):
            calls["window"] = win

        def close_conn(self):
            calls["closed"] = True

    def open_database(path):
        calls["db_path"] = path
        return "connection"

    description = launcher.AppDescription(
        product_name="Embedding Logbook",
        app_id="Example.EmbeddingLogbook",
        single_instance_name="Example_EmbeddingLogbook",
        version="9.8.7",
        update_owner="example-owner",
        update_repo="example-repo",
        update_link="https://example.invalid/update",
        api_class=FakeApi,
        open_database=open_database,
    )
    monkeypatch.setattr(launcher, "app_dir", lambda: str(tmp_path))
    monkeypatch.setattr(
        launcher.platform_win,
        "_acquire_single_instance",
        lambda name: calls.setdefault("mutex", name) and True,
    )
    monkeypatch.setattr(launcher.platform_win, "_writable_check", lambda folder: folder == str(tmp_path))
    monkeypatch.setattr(launcher.platform_win, "_restore_geometry", lambda win: None)
    monkeypatch.setattr(launcher.platform_win, "_save_geometry", lambda win: None)
    monkeypatch.setattr(
        launcher.webview,
        "create_window",
        lambda *args, **kwargs: calls.setdefault("window_args", (args, kwargs)) and FakeWindow(),
    )
    monkeypatch.setattr(launcher.webview, "start", lambda **kwargs: calls.setdefault("start", kwargs))

    launcher.run(description)

    assert calls["mutex"] == "Example_EmbeddingLogbook"
    assert calls["db_path"] == os.path.join(str(tmp_path), config.DB_FILENAME)
    assert calls["conn"] == "connection"
    assert calls["description"] is description
    assert calls["window_args"][0][0] == "Embedding Logbook"
    assert calls["start"]["gui"] == "qt"
    assert calls["closed"] is True
    assert config.APP_VERSION == "9.8.7"
    assert config.GITHUB_OWNER == "example-owner"
    assert config.GITHUB_REPO == "example-repo"


def test_run_reports_newer_schema_from_custom_database_opener(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "APP_VERSION", config.APP_VERSION)
    monkeypatch.setattr(config, "GITHUB_OWNER", config.GITHUB_OWNER)
    monkeypatch.setattr(config, "GITHUB_REPO", config.GITHUB_REPO)
    description = launcher.AppDescription(
        product_name="Embedding Logbook",
        app_id="Example.EmbeddingLogbook",
        single_instance_name="Example_EmbeddingLogbook",
        version="1.0.0",
        update_owner="example-owner",
        update_repo="example-repo",
        update_link="https://example.invalid/update",
        open_database=lambda path: (_ for _ in ()).throw(NewerSchemaError()),
    )
    calls = []
    monkeypatch.setattr(launcher, "app_dir", lambda: str(tmp_path))
    monkeypatch.setattr(launcher.platform_win, "_acquire_single_instance", lambda name: True)
    monkeypatch.setattr(launcher.platform_win, "_writable_check", lambda folder: True)
    monkeypatch.setattr(launcher.platform_win, "_show_newer_schema_error", lambda name: calls.append(name))

    with pytest.raises(SystemExit) as exited:
        launcher.run(description)

    assert exited.value.code == 1
    assert calls == ["Embedding Logbook"]
