"""Configurable application startup for the package and embedding apps."""
import ctypes
import os
import sys
from collections.abc import Callable
from dataclasses import dataclass, field

import webview

from sfl import config, platform_win, startup
from sfl.api import Api
from sfl.db import open_db
from sfl.paths import app_dir, resource_path
from sfl.remote_debug import strip_remote_debugging
from sfl.services import launch_restore, zip_restore_recovery
from sfl.storage import Storage, install as install_storage


@dataclass(frozen=True)
class AppDescription:
    """Values an embedding app supplies to identify and start the logbook."""

    product_name: str
    app_id: str
    single_instance_name: str
    version: str
    update_owner: str
    update_repo: str
    update_link: str
    api_class: type = Api
    open_database: Callable = open_db
    prepare_database: Callable = startup.prepare_database
    schema_version: int = config.SCHEMA_VERSION
    extension_script: str | None = None
    extension_stylesheet: str | None = None
    import_extension_fields: tuple = ()
    storage: Storage = field(default_factory=Storage)


DEFAULT_APP_DESCRIPTION = AppDescription(
    product_name="Simple Firearm Logbook",
    app_id="JDEProjects.SimpleFirearmLogbook",
    single_instance_name="JDE_SimpleFirearmLogbook_SingleInstance",
    version=config.APP_VERSION,
    update_owner="JDE-Projects",
    update_repo="Simple-Firearm-Logbook",
    update_link="https://github.com/JDE-Projects/Simple-Firearm-Logbook/blob/main/README.md#updating",
)


def run(description: AppDescription | None = None) -> None:
    """Start the logbook using the supplied application description."""
    # Stops Qt leaving an empty %LOCALAPPDATA%\<exe name>\cache\qtpipelinecache-*
    # folder the uninstaller never removes. Must run before the window is created.
    os.environ.setdefault("QT_DISABLE_SHADER_DISK_CACHE", "1")
    strip_remote_debugging(os.environ, sys.argv, getattr(sys, "frozen", False))
    description = description or DEFAULT_APP_DESCRIPTION
    install_storage(description.storage)
    config.APP_VERSION = description.version
    config.GITHUB_OWNER = description.update_owner
    config.GITHUB_REPO = description.update_repo

    # Use the Windows certificate store for TLS instead of the bundled CA list,
    # so antivirus/network filters that inject their own root cert (common on
    # managed laptops) don't break the update check. Runs before the Api object
    # exists, so there's no logger yet to record a fallback; if truststore is
    # missing or fails, urllib silently keeps using its default bundled CA list.
    try:
        import truststore
        truststore.inject_into_ssl()
    except Exception:  # noqa: BLE001, S110 - preserve the existing TLS fallback.
        pass

    if not platform_win._acquire_single_instance(description.single_instance_name):
        if platform_win._focus_existing_window(description.product_name):
            sys.exit(0)
        platform_win._show_already_running_error(app_dir(), description.product_name)
        sys.exit(1)

    if sys.platform == "win32":
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(description.app_id)
        except Exception:  # noqa: BLE001, S110 - app identity is best effort.
            pass

    folder = app_dir()
    if not platform_win._writable_check(folder):
        platform_win._show_write_error(folder, description.product_name)
        sys.exit(1)
    if not platform_win._acquire_folder_lock(folder):
        platform_win._show_already_running_error(folder, description.product_name)
        sys.exit(1)

    api = description.api_class()
    api.set_app_description(description)
    api.prune_debug_logs()
    db_path = os.path.join(folder, config.DB_FILENAME)
    if os.path.exists(zip_restore_recovery.record_path(folder)) and os.path.exists(launch_restore._record_path(db_path)):
        zip_recovery = "stopped"
    else:
        try:
            zip_recovery = zip_restore_recovery.recover(folder, api._log)
        except Exception as error:
            api._log(f"ZIP restore recovery stopped unexpectedly ({type(error).__name__})")
            zip_recovery = "stopped"
    if zip_recovery == "stopped":
        platform_win._show_zip_restore_stopped_error(folder, description.product_name)
        sys.exit(1)
    if zip_recovery == "finished":
        api.add_launch_notice("The backup restore that was interrupted has finished.")
    elif zip_recovery == "undone":
        api.add_launch_notice("The backup restore that was interrupted was undone. Your logbook is as it was before.")
    elif zip_recovery == "cleanup_pending":
        api._log("ZIP restore recovery: live data is correct; leftover cleanup will retry next launch")
        api.add_launch_notice("Some leftover files from an interrupted backup restore couldn't be removed from the data folder yet. The app will try again next time it opens.")
    prepared = description.prepare_database(api, db_path, description.schema_version)
    if prepared.outcome != "ready":
        if prepared.outcome == "unavailable":
            platform_win._show_database_unavailable_error(db_path, description.product_name)
        elif prepared.outcome == "damaged":
            platform_win._show_database_damaged_error(db_path, description.product_name)
        elif prepared.outcome == "damaged_no_backup":
            platform_win._show_damaged_no_backup_error(db_path, os.path.join(folder, config.BACKUPS_DIRNAME), description.product_name)
        elif prepared.outcome == "restore_failed":
            platform_win._show_restore_failed_error(db_path, description.product_name)
        elif prepared.outcome == "restore_stopped":
            platform_win._show_restore_stopped_error(folder, description.product_name)
        elif prepared.outcome == "encrypted":
            platform_win._show_encrypted_database_error(description.product_name)
        elif prepared.outcome == "pro_stamped":
            platform_win._show_newer_schema_error(description.product_name, True)
        elif prepared.outcome == "newer":
            platform_win._show_newer_schema_error(description.product_name, False)
        elif prepared.outcome == "upgrade_blocked":
            platform_win._show_upgrade_blocked_error(
                os.path.join(folder, config.BACKUPS_DIRNAME), description.product_name
            )
        else:
            platform_win._show_database_unavailable_error(db_path, description.product_name)
        sys.exit(1)

    api.set_conn(prepared.conn)
    if prepared.restore_notice is not None:
        platform_win._show_restore_complete_info(prepared.restore_notice, description.product_name)
        launch_restore.clear_record(api, db_path)
    for notice in prepared.notices:
        api.add_launch_notice(notice)
    win = webview.create_window(
        description.product_name,
        url=resource_path("simple_firearm_logbook-UI.html"),
        js_api=api,
        width=1280,
        height=820,
        min_size=(1000, 680),
        background_color="#0a0e14",
    )
    api.set_window(win)
    win.events.shown += lambda: platform_win._restore_geometry(win)
    win.events.loaded += api.flush_debug_warnings

    def _on_window_closing():
        platform_win._save_geometry(win)
        return True

    win.events.closing += _on_window_closing
    try:
        webview.start(gui="qt", icon=resource_path("simple_firearm_logbook.png"))
    except TypeError:
        webview.start(gui="qt")
    api.close_restore()
    api.close_conn()
