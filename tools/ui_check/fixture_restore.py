"""ui_drive fixture for the restore scenario. Builds a logbook in the copied
app folder with one firearm, writes a backup zip of it, then adds a second
firearm to the live logbook so a restore has something to undo. While the
app runs, a watcher fills in the app's Windows Open dialog with the backup's
path the first time one appears, the same way a person would type it.
Writes only inside UI_DRIVE_APP_DIR and UI_DRIVE_OUT_DIR."""
import ctypes
import json
import os
import sys
import threading
import time
from ctypes import wintypes

app_dir = os.environ["UI_DRIVE_APP_DIR"]
out_dir = os.environ["UI_DRIVE_OUT_DIR"]
sys.path.insert(0, app_dir)

from sfl import config, paths  # noqa: E402
from sfl.api import Api  # noqa: E402
from sfl.db import open_db  # noqa: E402
from sfl.services import backup  # noqa: E402

# The fixture runs from tools\ui_check, so point the app's folder lookup at
# the copied app, where the app itself will look.
paths.app_dir = lambda: app_dir

APP_TITLE = "Simple Firearm Logbook"
backup_path = os.path.join(out_dir, "ui-check-backup.zip")

conn = open_db(os.path.join(app_dir, config.DB_FILENAME))
api = Api()
api.set_conn(conn)
api.create_firearm("Smith & Wesson", "686", serial_number="INBACKUP")
written = backup._write_backup_zip(conn, backup_path, lambda message: None)
api.create_firearm("Glock", "19", serial_number="AFTERBACKUP")
conn.close()

user32 = ctypes.WinDLL("user32", use_last_error=True)
user32.FindWindowExW.restype = wintypes.HWND
user32.FindWindowExW.argtypes = [wintypes.HWND, wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR]
user32.GetWindow.restype = wintypes.HWND
user32.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
user32.GetDlgItem.restype = wintypes.HWND
user32.GetDlgItem.argtypes = [wintypes.HWND, ctypes.c_int]
user32.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPCWSTR]
user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.IsWindowVisible.argtypes = [wintypes.HWND]
EnumProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

GW_OWNER = 4
WM_SETTEXT = 0x000C
BM_CLICK = 0x00F5
IDOK = 1


def _text(hwnd, fn):
    buf = ctypes.create_unicode_buffer(256)
    fn(hwnd, buf, 256)
    return buf.value


def _find_open_dialog():
    """The visible standard dialog owned by the app's main window."""
    found = []

    def visit(hwnd, _):
        if (user32.IsWindowVisible(hwnd) and _text(hwnd, user32.GetClassNameW) == "#32770"):
            owner = user32.GetWindow(hwnd, GW_OWNER)
            if owner and _text(owner, user32.GetWindowTextW) == APP_TITLE:
                found.append(hwnd)
                return False
        return True

    user32.EnumWindows(EnumProc(visit), 0)
    return found[0] if found else None


def _file_name_box(dialog):
    combo_ex = user32.FindWindowExW(dialog, None, "ComboBoxEx32", None)
    if combo_ex:
        combo = user32.FindWindowExW(combo_ex, None, "ComboBox", None)
        if combo:
            edit = user32.FindWindowExW(combo, None, "Edit", None)
            if edit:
                return edit
    return user32.FindWindowExW(dialog, None, "Edit", None)


def _fill_dialog_once():
    status_path = os.path.join(out_dir, "dialog_watcher.txt")
    deadline = time.time() + 90
    while time.time() < deadline:
        dialog = _find_open_dialog()
        if dialog:
            time.sleep(1.0)  # let the dialog finish building its controls
            edit = _file_name_box(dialog)
            if not edit:
                with open(status_path, "w", encoding="utf-8") as f:
                    f.write("dialog found, file name box not found\n")
                return
            user32.SendMessageW(edit, WM_SETTEXT, 0, backup_path)
            user32.PostMessageW(user32.GetDlgItem(dialog, IDOK), BM_CLICK, 0, 0)
            with open(status_path, "w", encoding="utf-8") as f:
                f.write("filled\n")
            return
        time.sleep(0.25)
    with open(status_path, "w", encoding="utf-8") as f:
        f.write("no dialog appeared\n")


threading.Thread(target=_fill_dialog_once, daemon=True).start()

print(json.dumps({"backup_ok": bool(written.get("ok"))}), flush=True)
while True:
    time.sleep(60)
