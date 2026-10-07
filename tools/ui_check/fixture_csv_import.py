"""ui_drive fixture for the CSV import scenario.

Writes one CSV file for the app to import and fills each of the two native Open
dialogs with its path. Writes only inside UI_DRIVE_OUT_DIR and UI_DRIVE_APP_DIR.
"""
import ctypes
import csv
import json
import os
import threading
import time
from ctypes import wintypes

out_dir = os.environ["UI_DRIVE_OUT_DIR"]

APP_TITLE = "Simple Firearm Logbook"
csv_path = os.path.join(out_dir, "ui-check-import.csv")
status_path = os.path.join(out_dir, "dialog_watcher.txt")

with open(csv_path, "w", encoding="utf-8", newline="") as fh:
    writer = csv.writer(fh)
    writer.writerow(["Make", "Model", "Serial Number"])
    writer.writerows(
        [
            ["Glock", "19", "CSV-001"],
            ["Ruger", "10/22", "CSV-002"],
            ["Smith & Wesson", "686", "CSV-003"],
            ["Colt", "", "CSV-ERROR"],
        ]
    )

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


def _write_status(lines):
    with open(status_path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


def _text(hwnd, fn):
    buf = ctypes.create_unicode_buffer(256)
    fn(hwnd, buf, 256)
    return buf.value


def _find_open_dialog():
    """The visible standard dialog owned by the app's main window."""
    found = []

    def visit(hwnd, _):
        if user32.IsWindowVisible(hwnd) and _text(hwnd, user32.GetClassNameW) == "#32770":
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


def _fill_two_dialogs():
    deadline = time.time() + 90
    status = []
    for number in range(1, 3):
        dialog = None
        while time.time() < deadline:
            dialog = _find_open_dialog()
            if dialog:
                break
            time.sleep(0.25)
        if not dialog:
            status.append(f"dialog {number} did not appear")
            _write_status(status)
            return

        time.sleep(1.0)  # Let the dialog finish building its controls.
        edit = _file_name_box(dialog)
        if not edit:
            status.append(f"dialog {number} found, file name box not found")
            _write_status(status)
            return
        user32.SendMessageW(edit, WM_SETTEXT, 0, csv_path)
        user32.PostMessageW(user32.GetDlgItem(dialog, IDOK), BM_CLICK, 0, 0)
        status.append(f"filled dialog {number}")
        _write_status(status)

        while time.time() < deadline and _find_open_dialog() == dialog:
            time.sleep(0.25)

    status.append("filled both dialogs")
    _write_status(status)


threading.Thread(target=_fill_two_dialogs, daemon=True).start()

print(json.dumps({"csv_path": csv_path, "ready": 3, "errors": 1}), flush=True)
while True:
    time.sleep(60)
