"""Tests for configurable package startup."""
import os
import sys

import pytest

from sfl import config, launcher, startup


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
    assert description.prepare_database is launcher.startup.prepare_database
    assert description.schema_version == config.SCHEMA_VERSION
    assert description.extension_script is None
    assert description.extension_stylesheet is None


def test_run_strips_remote_debugging_before_startup(monkeypatch):
    calls = []

    def strip(environ, argv, frozen):
        calls.append((environ, argv, frozen))
        raise RuntimeError("stop after first startup action")

    monkeypatch.setattr(launcher, "strip_remote_debugging", strip)

    with pytest.raises(RuntimeError, match="stop after first startup action"):
        launcher.run()

    assert calls == [(os.environ, sys.argv, getattr(sys, "frozen", False))]


@pytest.mark.parametrize(("before", "after"), [(None, "1"), ("0", "0")])
def test_run_turns_off_qt_shader_disk_cache_unless_already_set(monkeypatch, before, after):
    # setenv first so monkeypatch restores the original value afterwards,
    # including when the variable was never set.
    monkeypatch.setenv("QT_DISABLE_SHADER_DISK_CACHE", before or "")
    if before is None:
        monkeypatch.delenv("QT_DISABLE_SHADER_DISK_CACHE")
    seen = []

    def strip(environ, argv, frozen):
        seen.append(environ.get("QT_DISABLE_SHADER_DISK_CACHE"))
        raise RuntimeError("stop after first startup action")

    monkeypatch.setattr(launcher, "strip_remote_debugging", strip)

    with pytest.raises(RuntimeError, match="stop after first startup action"):
        launcher.run()

    assert seen == [after]


def test_run_uses_custom_api_and_database_preparer_and_sets_config(monkeypatch, tmp_path):
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
        events = type("Events", (), {"shown": FakeEvent(), "loaded": FakeEvent(), "closing": FakeEvent()})()

    class FakeApi:
        def set_app_description(self, description):
            calls["description"] = description

        def set_conn(self, conn):
            calls["conn"] = conn

        def set_window(self, win):
            calls["window"] = win

        def _log(self, msg):
            calls.setdefault("log", []).append(msg)

        def prune_debug_logs(self):
            calls["pruned"] = True

        def flush_debug_warnings(self):
            calls["warnings_flushed"] = True

        def add_launch_notice(self, message):
            calls.setdefault("notices", []).append(message)

        def close_restore(self):
            calls.setdefault("order", []).append("close_restore")

        def close_conn(self):
            calls["closed"] = True
            calls.setdefault("order", []).append("close_conn")

    def prepare_database(api, path, schema_version):
        calls.setdefault("order", []).append("prepare")
        calls["db_path"] = path
        calls["schema_version"] = schema_version
        return startup.PreparedDatabase("ready", "connection", "test connection", ("launch warning",), "restored")

    description = launcher.AppDescription(
        product_name="Embedding Logbook",
        app_id="Example.EmbeddingLogbook",
        single_instance_name="Example_EmbeddingLogbook",
        version="9.8.7",
        update_owner="example-owner",
        update_repo="example-repo",
        update_link="https://example.invalid/update",
        api_class=FakeApi,
        prepare_database=prepare_database,
    )
    monkeypatch.setattr(launcher, "app_dir", lambda: str(tmp_path))
    monkeypatch.setattr(
        launcher.platform_win,
        "_acquire_single_instance",
        lambda name: calls.setdefault("mutex", name) and True,
    )
    monkeypatch.setattr(launcher.platform_win, "_writable_check", lambda folder: folder == str(tmp_path))
    monkeypatch.setattr(launcher.platform_win, "_acquire_folder_lock", lambda folder: True)
    monkeypatch.setattr(
        launcher.zip_restore_recovery,
        "recover",
        lambda folder, log: (calls.setdefault("order", []).append("zip_recovery"), "finished")[1],
    )
    monkeypatch.setattr(launcher.platform_win, "_restore_geometry", lambda win: None)
    monkeypatch.setattr(launcher.platform_win, "_save_geometry", lambda win: None)
    monkeypatch.setattr(
        launcher.platform_win, "_show_restore_complete_info", lambda *args: calls.setdefault("order", []).append("notice")
    )
    monkeypatch.setattr(
        launcher.launch_restore, "clear_record", lambda *args: calls.setdefault("order", []).append("clear_record")
    )
    monkeypatch.setattr(
        launcher.webview,
        "create_window",
        lambda *args, **kwargs: (calls.setdefault("window_args", (args, kwargs)), calls.setdefault("order", []).append("window"), FakeWindow())[-1],
    )
    monkeypatch.setattr(launcher.webview, "start", lambda **kwargs: calls.setdefault("start", kwargs))

    launcher.run(description)

    assert calls["mutex"] == "Example_EmbeddingLogbook"
    assert calls["db_path"] == os.path.join(str(tmp_path), config.DB_FILENAME)
    assert calls["schema_version"] == config.SCHEMA_VERSION
    assert calls["conn"] == "connection"
    assert calls["description"] is description
    assert calls["window_args"][0][0] == "Embedding Logbook"
    assert calls["start"]["gui"] == "qt"
    assert calls["closed"] is True
    assert calls["pruned"] is True
    assert calls["notices"] == ["The backup restore that was interrupted has finished.", "launch warning"]
    assert calls["order"] == ["zip_recovery", "prepare", "notice", "clear_record", "window", "close_restore", "close_conn"]
    assert config.APP_VERSION == "9.8.7"
    assert config.GITHUB_OWNER == "example-owner"
    assert config.GITHUB_REPO == "example-repo"


@pytest.mark.parametrize(
    ("outcome", "message_name", "message_args"),
    [
        ("unavailable", "_show_database_unavailable_error", ("Embedding Logbook",)),
        ("damaged", "_show_database_damaged_error", ("Embedding Logbook",)),
        ("damaged_no_backup", "_show_damaged_no_backup_error", ("Embedding Logbook",)),
        ("restore_failed", "_show_restore_failed_error", ("Embedding Logbook",)),
        ("restore_stopped", "_show_restore_stopped_error", ("Embedding Logbook",)),
        ("encrypted", "_show_encrypted_database_error", ("Embedding Logbook",)),
        ("pro_stamped", "_show_newer_schema_error", ("Embedding Logbook", True)),
        ("newer", "_show_newer_schema_error", ("Embedding Logbook", False)),
        ("upgrade_blocked", "_show_upgrade_blocked_error", ("Embedding Logbook",)),
    ],
)
def test_run_reports_non_ready_database_outcome(monkeypatch, tmp_path, outcome, message_name, message_args):
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
        prepare_database=lambda api, path, schema_version: startup.PreparedDatabase(outcome, None, "test"),
    )
    calls = []
    monkeypatch.setattr(launcher, "app_dir", lambda: str(tmp_path))
    monkeypatch.setattr(launcher.platform_win, "_acquire_single_instance", lambda name: True)
    monkeypatch.setattr(launcher.platform_win, "_writable_check", lambda folder: True)
    monkeypatch.setattr(launcher.platform_win, "_acquire_folder_lock", lambda folder: True)
    monkeypatch.setattr(launcher.platform_win, message_name, lambda *args: calls.append(args))

    with pytest.raises(SystemExit) as exited:
        launcher.run(description)

    assert exited.value.code == 1
    if outcome in {"unavailable", "damaged", "restore_failed"}:
        assert calls == [(os.path.join(str(tmp_path), config.DB_FILENAME), *message_args)]
    elif outcome == "upgrade_blocked":
        assert calls == [(os.path.join(str(tmp_path), config.BACKUPS_DIRNAME), *message_args)]
    elif outcome == "damaged_no_backup":
        assert calls == [(
            os.path.join(str(tmp_path), config.DB_FILENAME),
            os.path.join(str(tmp_path), config.BACKUPS_DIRNAME),
            *message_args,
        )]
    elif outcome == "restore_stopped":
        assert calls == [(str(tmp_path), *message_args)]
    else:
        assert calls == [message_args]


def test_run_stops_when_single_instance_cannot_be_focused(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(launcher.platform_win, "_acquire_single_instance", lambda name: False)
    monkeypatch.setattr(launcher.platform_win, "_focus_existing_window", lambda name: False)
    monkeypatch.setattr(launcher, "app_dir", lambda: str(tmp_path))
    monkeypatch.setattr(launcher.platform_win, "_show_already_running_error", lambda *args: calls.append(args))

    with pytest.raises(SystemExit) as exited:
        launcher.run()

    assert exited.value.code == 1
    assert calls == [(str(tmp_path), "Simple Firearm Logbook")]


def test_run_stops_when_folder_lock_is_refused(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(launcher, "app_dir", lambda: str(tmp_path))
    monkeypatch.setattr(launcher.platform_win, "_acquire_single_instance", lambda name: True)
    monkeypatch.setattr(launcher.platform_win, "_writable_check", lambda folder: True)
    monkeypatch.setattr(launcher.platform_win, "_acquire_folder_lock", lambda folder: False)
    monkeypatch.setattr(launcher.platform_win, "_show_already_running_error", lambda *args: calls.append(args))

    with pytest.raises(SystemExit) as exited:
        launcher.run()

    assert exited.value.code == 1
    assert calls == [(str(tmp_path), "Simple Firearm Logbook")]


def test_both_restore_records_present_stops_launch(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "APP_VERSION", config.APP_VERSION)
    monkeypatch.setattr(config, "GITHUB_OWNER", config.GITHUB_OWNER)
    monkeypatch.setattr(config, "GITHUB_REPO", config.GITHUB_REPO)
    zip_record = tmp_path / "simple_firearm_logbook.zip_restore.json"
    normal_record = tmp_path / "simple_firearm_logbook.restore.json"
    zip_record.write_text("{}", encoding="utf-8")
    normal_record.write_text("{}", encoding="utf-8")
    shown = []
    monkeypatch.setattr(launcher, "app_dir", lambda: str(tmp_path))
    monkeypatch.setattr(launcher.platform_win, "_acquire_single_instance", lambda name: True)
    monkeypatch.setattr(launcher.platform_win, "_writable_check", lambda folder: True)
    monkeypatch.setattr(launcher.platform_win, "_acquire_folder_lock", lambda folder: True)
    monkeypatch.setattr(launcher.platform_win, "_show_zip_restore_stopped_error", lambda *args: shown.append(args))
    monkeypatch.setattr(launcher.zip_restore_recovery, "recover", lambda *args: pytest.fail("recover should not run"))
    description = launcher.AppDescription(
        "Embedding Logbook", "Example.EmbeddingLogbook", "Example_EmbeddingLogbook", "1.0.0",
        "example-owner", "example-repo", "https://example.invalid/update",
        prepare_database=lambda *args: pytest.fail("prepare_database should not run"),
    )

    with pytest.raises(SystemExit) as exited:
        launcher.run(description)

    assert exited.value.code == 1
    assert shown == [(str(tmp_path), "Embedding Logbook")]
    assert zip_record.exists()
    assert normal_record.exists()


def test_cleanup_pending_launch_carries_on_with_notice(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "APP_VERSION", config.APP_VERSION)
    monkeypatch.setattr(config, "GITHUB_OWNER", config.GITHUB_OWNER)
    monkeypatch.setattr(config, "GITHUB_REPO", config.GITHUB_REPO)
    calls = {"notices": [], "logs": []}

    class FakeApi:
        def set_app_description(self, description):
            pass

        def prune_debug_logs(self):
            pass

        def _log(self, message):
            calls["logs"].append(message)

        def add_launch_notice(self, message):
            calls["notices"].append(message)

    def prepare_database(api, path, schema_version):
        calls["prepared"] = (api, path, schema_version)
        return startup.PreparedDatabase("unavailable", None, "test")

    description = launcher.AppDescription(
        "Embedding Logbook", "Example.EmbeddingLogbook", "Example_EmbeddingLogbook", "1.0.0",
        "example-owner", "example-repo", "https://example.invalid/update",
        api_class=FakeApi, prepare_database=prepare_database,
    )
    monkeypatch.setattr(launcher, "app_dir", lambda: str(tmp_path))
    monkeypatch.setattr(launcher.platform_win, "_acquire_single_instance", lambda name: True)
    monkeypatch.setattr(launcher.platform_win, "_writable_check", lambda folder: True)
    monkeypatch.setattr(launcher.platform_win, "_acquire_folder_lock", lambda folder: True)
    monkeypatch.setattr(launcher.zip_restore_recovery, "recover", lambda *args: "cleanup_pending")
    monkeypatch.setattr(launcher.platform_win, "_show_database_unavailable_error", lambda *args: calls.setdefault("shown", []).append(args))

    with pytest.raises(SystemExit) as exited:
        launcher.run(description)

    assert exited.value.code == 1
    assert calls["prepared"][1:] == (os.path.join(str(tmp_path), config.DB_FILENAME), config.SCHEMA_VERSION)
    assert calls["notices"] == [
        "Some leftover files from an interrupted backup restore couldn't be removed from the data folder yet. The app will try again next time it opens."
    ]


@pytest.mark.parametrize(
    ("opened_by_pro", "expected_message"),
    [
        (
            True,
            (
                "This logbook has been opened in Simple Firearm Logbook Pro, which saves it in a "
                "format this app can't read.\n\nOpen it in Simple Firearm Logbook Pro."
            ),
        ),
        (
            False,
            (
                "This data file was created by a newer version of Example Logbook than this one.\n\n"
                "Update to the latest version of the app to open it."
            ),
        ),
    ],
)
def test_newer_schema_error_message(monkeypatch, opened_by_pro, expected_message):
    calls = []

    class User32:
        @staticmethod
        def MessageBoxW(*args):
            calls.append(args)

    monkeypatch.setattr(
        launcher.platform_win.ctypes,
        "windll",
        type("Windll", (), {"user32": User32()})(),
        raising=False,
    )

    launcher.platform_win._show_newer_schema_error("Example Logbook", opened_by_pro)

    assert calls == [(0, expected_message, "Example Logbook", 0x10)]
