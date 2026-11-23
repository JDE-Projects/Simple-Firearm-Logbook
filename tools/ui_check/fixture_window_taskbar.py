"""ui_drive fixture for the window-taskbar scenario. Saves a window position,
then once the app has placed its window: moves the window somewhere else,
minimizes it, and restores it, the way a person sends it to the taskbar and
clicks it back. Records the window's frame (physical pixels) along the way.

Writes only inside UI_DRIVE_APP_DIR and UI_DRIVE_OUT_DIR.
"""
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

from sfl import paths, prefs  # noqa: E402

paths.app_dir = lambda: app_dir

APP_TITLE = "Simple Firearm Logbook"
samples_path = os.path.join(out_dir, "window_taskbar_samples.json")

user32 = ctypes.WinDLL("user32", use_last_error=True)
# Per-monitor DPI aware, so GetWindowRect reports the same physical pixels the
# app saves and restores.
user32.SetProcessDpiAwarenessContext.argtypes = [ctypes.c_void_p]
user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
scale = user32.GetDpiForSystem() / 96

# A normal saved spot near the top-left, and a different spot to move to.
window = {"x": round(40 * scale), "y": round(40 * scale),
          "width": round(1100 * scale), "height": round(760 * scale)}
moved = {"x": round(300 * scale), "y": round(150 * scale)}
saved = prefs.save_prefs({"window": window})

user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
user32.FindWindowW.restype = wintypes.HWND
user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                ctypes.c_int, ctypes.c_int, wintypes.UINT]
user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
SW_MINIMIZE, SW_RESTORE = 6, 9
SWP_NOSIZE, SWP_NOZORDER = 0x0001, 0x0004


def _rect(hwnd, label, samples):
    r = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    samples.append({"step": label, "x": r.left, "y": r.top,
                    "width": r.right - r.left, "height": r.bottom - r.top})
    with open(samples_path, "w", encoding="utf-8") as fh:
        json.dump(samples, fh)


def _drive():
    samples = []
    deadline = time.time() + 60
    hwnd = None
    while time.time() < deadline and not hwnd:
        hwnd = user32.FindWindowW(None, APP_TITLE)
        time.sleep(0.1)
    if not hwnd:
        return
    time.sleep(4)   # let the app's own placement finish
    _rect(hwnd, "placed", samples)
    user32.SetWindowPos(hwnd, None, moved["x"], moved["y"], 0, 0, SWP_NOSIZE | SWP_NOZORDER)
    time.sleep(1)
    _rect(hwnd, "moved", samples)
    user32.ShowWindow(hwnd, SW_MINIMIZE)
    time.sleep(1.5)
    user32.ShowWindow(hwnd, SW_RESTORE)
    for i in range(8):
        time.sleep(0.25)
        _rect(hwnd, f"restored+{(i + 1) * 0.25:.2f}s", samples)


threading.Thread(target=_drive, daemon=True).start()

print(json.dumps({"saved": saved, "window": window, "moved": moved, "scale": scale}), flush=True)
while True:
    time.sleep(60)
