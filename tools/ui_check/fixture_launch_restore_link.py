"""Plant a logbook with a broken record link and a checked automatic-launch backup.

The live database passes SQLite's integrity check but has a photo whose
firearm does not exist, so launch must treat it as damaged and restore.

The fixture dismisses the native completion message while the app starts and
records its text in the ui_drive output folder.  It writes only to the copied
app folder and that output folder.
"""
import ctypes
import json
import os
import sqlite3
import sys
import time
from ctypes import wintypes

app_dir = os.environ["UI_DRIVE_APP_DIR"]
out_dir = os.environ["UI_DRIVE_OUT_DIR"]
sys.path.insert(0, app_dir)

from sfl import config, paths  # noqa: E402
from sfl.db import open_db  # noqa: E402
from sfl.services import launch_backup  # noqa: E402

paths.app_dir = lambda: app_dir
db_path = os.path.join(app_dir, config.DB_FILENAME)

conn = open_db(db_path)
conn.execute(
    "INSERT INTO firearms (log_number, make, model, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
    ("00001", "Glock", "19", "now", "now"),
)
conn.commit()
backup = launch_backup.create_launch_backup(conn, db_path, config.SCHEMA_VERSION)
conn.close()
if not backup.ok:
    raise SystemExit(f"could not create checked launch backup: {backup.detail}")

# Break a record link: a photo row whose firearm does not exist.
broken = sqlite3.connect(db_path)
broken.execute("PRAGMA foreign_keys = OFF")
broken.execute("INSERT INTO photos (firearm_id, filename, seq, is_primary) VALUES (999, 'ghost.jpg', 1, 1)")
broken.commit()
if broken.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
    raise SystemExit("broken-link step should leave integrity_check passing")
if broken.execute("PRAGMA foreign_key_check").fetchone() is None:
    raise SystemExit("broken-link step did not create a broken record link")
broken.close()

user32 = ctypes.WinDLL("user32", use_last_error=True)
EnumProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
user32.EnumWindows.argtypes = [EnumProc, wintypes.LPARAM]
user32.EnumWindows.restype = wintypes.BOOL
user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
user32.GetWindowTextLengthW.restype = ctypes.c_int
user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetDlgItem.argtypes = [wintypes.HWND, ctypes.c_int]
user32.GetDlgItem.restype = wintypes.HWND
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]

APP_TITLE = "Simple Firearm Logbook"
IDCANCEL = 2
WM_COMMAND = 0x0111


def _text(hwnd):
    length = user32.GetWindowTextLengthW(hwnd)
    buffer = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buffer, len(buffer))
    return buffer.value


def _find_restore_box():
    found = []

    def visit(hwnd, _):
        process_id = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(process_id))
        class_name = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, class_name, len(class_name))
        if (process_id.value != os.getpid() and class_name.value == "#32770"
                and _text(hwnd) == APP_TITLE):
            found.append(hwnd)
            return False
        return True

    user32.EnumWindows(EnumProc(visit), 0)
    return found[0] if found else None


user32.IsWindow.argtypes = [wintypes.HWND]
user32.IsWindow.restype = wintypes.BOOL
user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]

print(json.dumps({"firearm": "Glock 19"}), flush=True)
recorded = False
closed = False
while True:
    if not closed:
        dialog = _find_restore_box()
        if dialog:
            if not recorded:
                message = _text(user32.GetDlgItem(dialog, 0xFFFF))
                with open(os.path.join(out_dir, "restore_box.txt"), "w", encoding="utf-8") as file:
                    file.write(message)
                recorded = True
            # An OK-only message box ignores IDOK; IDCANCEL closes it and the
            # box still returns IDOK. Repeat until the box is gone.
            time.sleep(0.3)
            if user32.IsWindow(dialog):
                user32.PostMessageW(dialog, WM_COMMAND, IDCANCEL, 0)
        elif recorded:
            closed = True
    time.sleep(0.2)
