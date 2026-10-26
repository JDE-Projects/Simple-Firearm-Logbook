"""ui_drive fixture for the window-size scenario. Saves a window size smaller
than the app's default but above its minimum, then records the app window's
real frame size (physical pixels) a few times after it appears.

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
samples_path = os.path.join(out_dir, "window_size_samples.json")

user32 = ctypes.WinDLL("user32", use_last_error=True)
# Per-monitor DPI aware, so GetWindowRect reports the same physical pixels the
# app saves and restores.
user32.SetProcessDpiAwarenessContext.argtypes = [ctypes.c_void_p]
user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
scale = user32.GetDpiForSystem() / 96

# 1100 x 760 logical is above the 1000 x 680 minimum (plus frame) and below
# the 1280 x 820 default, at any scaling.
window = {"x": 40, "y": 40, "width": round(1100 * scale), "height": round(760 * scale)}
saved = prefs.save_prefs({"window": window})

user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
user32.FindWindowW.restype = wintypes.HWND
user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]


def _sample():
    samples = []
    deadline = time.time() + 60
    hwnd = None
    while time.time() < deadline and not hwnd:
        hwnd = user32.FindWindowW(None, APP_TITLE)
        time.sleep(0.1)
    start = time.time()
    while hwnd and time.time() - start < 6:
        r = wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(r))
        samples.append({"t": round(time.time() - start, 2), "x": r.left, "y": r.top,
                        "width": r.right - r.left, "height": r.bottom - r.top})
        with open(samples_path, "w", encoding="utf-8") as fh:
            json.dump(samples, fh)
        time.sleep(0.25)


threading.Thread(target=_sample, daemon=True).start()

print(json.dumps({"saved": saved, "window": window, "scale": scale}), flush=True)
while True:
    time.sleep(60)
