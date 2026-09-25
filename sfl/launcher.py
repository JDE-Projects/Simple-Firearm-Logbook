"""Configurable application startup for the package and embedding apps."""
import ctypes
import os
import sys
from collections.abc import Callable
from dataclasses import dataclass

import webview

from sfl import config, platform_win
from sfl.api import Api
from sfl.db import NewerSchemaError, open_db
from sfl.paths import app_dir, resource_path


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
    extension_script: str | None = None
    extension_stylesheet: str | None = None


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
    description = description or DEFAULT_APP_DESCRIPTION
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

    if (
        not platform_win._acquire_single_instance(description.single_instance_name)
        and platform_win._focus_existing_window(description.product_name)
    ):
        sys.exit(0)

    if sys.platform == "win32":
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(description.app_id)
        except Exception:  # noqa: BLE001, S110 - app identity is best effort.
            pass

    folder = app_dir()
    if not platform_win._writable_check(folder):
        platform_win._show_write_error(folder, description.product_name)
        sys.exit(1)

    api = description.api_class()
    api.set_app_description(description)
    try:
        conn = description.open_database(os.path.join(folder, config.DB_FILENAME))
    except NewerSchemaError:
        platform_win._show_newer_schema_error(description.product_name)
        sys.exit(1)

    api.set_conn(conn)
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

    def _on_window_closing():
        platform_win._save_geometry(win)
        return True

    win.events.closing += _on_window_closing
    try:
        webview.start(gui="qt", icon=resource_path("simple_firearm_logbook.png"))
    except TypeError:
        webview.start(gui="qt")
    api.close_conn()
