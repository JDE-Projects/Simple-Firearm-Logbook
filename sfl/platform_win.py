"""Windows-only platform glue: window geometry persistence (save/restore via
Win32), single-instance enforcement, and the startup error message boxes.

Save and restore the ABSOLUTE window frame rectangle via Win32, found by the
window title but filtered to a window owned by this process (see
`_own_window_handle` below). GetWindowRect (save) and SetWindowPos (restore)
share one frame-based, physical-pixel coordinate space, so the rect
round-trips exactly at any DPI or monitor layout. Do NOT pass x/y into
create_window and do NOT use window.move: pywebview's Qt backend applies
those pre-show and relative to the primary screen, so the window lands on
the wrong monitor, drifts down by the title-bar height each launch, and
slides sideways at non-100% scaling.
"""
import ctypes
import ctypes.wintypes as wintypes
import os

from sfl import prefs


def _win32():
    u = ctypes.windll.user32
    u.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    u.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                               ctypes.c_int, ctypes.c_int, wintypes.UINT]
    return u


def _own_window_handle(title):
    """HWND of our own top-level window with this title.

    FindWindowW matches by title across the whole desktop, so with a second
    instance open it can return the other copy's window. Enumerate instead and
    keep only a window owned by this process.
    """
    try:
        u = ctypes.windll.user32
        WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        u.EnumWindows.argtypes = [WNDENUMPROC, wintypes.LPARAM]
        u.EnumWindows.restype = wintypes.BOOL
        u.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        u.GetWindowThreadProcessId.restype = wintypes.DWORD
        u.GetWindowTextLengthW.argtypes = [wintypes.HWND]
        u.GetWindowTextLengthW.restype = ctypes.c_int
        u.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        u.GetWindowTextW.restype = ctypes.c_int
        u.IsWindowVisible.argtypes = [wintypes.HWND]
        u.IsWindowVisible.restype = wintypes.BOOL

        own_pid = os.getpid()
        found = {"hwnd": None}

        def _callback(hwnd, lparam):
            if not u.IsWindowVisible(hwnd):
                return True
            pid = wintypes.DWORD()
            u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value != own_pid:
                return True
            length = u.GetWindowTextLengthW(hwnd)
            if length <= 0:
                return True
            buf = ctypes.create_unicode_buffer(length + 1)
            u.GetWindowTextW(hwnd, buf, length + 1)
            if buf.value != title:
                return True
            found["hwnd"] = hwnd
            return False   # stop enumerating, we found it

        proc = WNDENUMPROC(_callback)   # kept alive for the duration of the call below
        u.EnumWindows(proc, 0)
        return found["hwnd"]
    except Exception:
        return None


def _save_geometry(win) -> None:
    """Save the absolute frame rect (physical px) via Win32. Wire to `closing`.
    Wrapped end to end so a failure here can never block the window from closing."""
    try:
        u = _win32()
        hwnd = _own_window_handle(win.title)
        if not hwnd:
            return
        r = wintypes.RECT()
        if not u.GetWindowRect(hwnd, ctypes.byref(r)):
            return
        x, y, w, h = r.left, r.top, r.right - r.left, r.bottom - r.top
        if x <= -30000 or y <= -30000:   # minimized sentinel, not a real spot
            return
        if w <= 0 or h <= 0:
            return
        prefs_data = prefs.load_prefs()
        prefs_data["window"] = {"x": x, "y": y, "width": w, "height": h}
        prefs.save_prefs(prefs_data)
    except Exception:
        pass


def _restore_geometry(win) -> None:
    """Restore the saved frame rect via Win32. Wire to `shown` (after the OS
    window exists). Validate before applying; never raise."""
    try:
        geo = prefs.load_prefs().get("window")
        if not isinstance(geo, dict):
            return
        x, y, w, h = geo.get("x"), geo.get("y"), geo.get("width"), geo.get("height")
        for v in (x, y, w, h):
            if not isinstance(v, int) or isinstance(v, bool):
                return
        if w <= 0 or h <= 0:
            return
        # Is a point in the title bar still on a connected monitor?
        point = wintypes.POINT(x + 100, y + 30)
        user32 = ctypes.windll.user32
        user32.MonitorFromPoint.argtypes = [wintypes.POINT, wintypes.DWORD]
        user32.MonitorFromPoint.restype = wintypes.HMONITOR
        if not user32.MonitorFromPoint(point, 0):   # MONITOR_DEFAULTTONULL
            return
        u = _win32()
        hwnd = _own_window_handle(win.title)
        if not hwnd:
            return
        SWP_NOZORDER, SWP_NOACTIVATE = 0x0004, 0x0010
        u.SetWindowPos(hwnd, None, x, y, w, h, SWP_NOZORDER | SWP_NOACTIVATE)
    except Exception:
        pass


def _writable_check(folder: str) -> bool:
    """Try creating and deleting a temp file next to the exe."""
    try:
        test_path = os.path.join(folder, f".wtest_{os.getpid()}.tmp")
        with open(test_path, "w", encoding="utf-8") as f:
            f.write("x")
        os.remove(test_path)
        return True
    except Exception:
        return False


def _show_write_error(folder: str, product_name: str = "Simple Firearm Logbook"):
    msg = (
        f"{product_name} keeps its data in a file next to the app, "
        f"but this folder isn't writable:\n\n{folder}\n\n"
        "This often happens when the app is placed in Program Files. Move it "
        "to a writable folder (like your Desktop or Documents) and try again."
    )
    try:
        ctypes.windll.user32.MessageBoxW(0, msg, product_name, 0x10)  # MB_ICONERROR
    except Exception:
        pass


def _show_newer_schema_error(product_name: str = "Simple Firearm Logbook", opened_by_pro=False):
    if opened_by_pro:
        msg = (
            "This logbook has been opened in Simple Firearm Logbook Pro, which saves it in a "
            "format this app can't read.\n\nOpen it in Simple Firearm Logbook Pro."
        )
    else:
        msg = (
            f"This data file was created by a newer version of {product_name} than this one.\n\n"
            "Update to the latest version of the app to open it."
        )
    try:
        ctypes.windll.user32.MessageBoxW(0, msg, product_name, 0x10)  # MB_ICONERROR
    except Exception:
        pass


_mutex_handle = None   # module-level: must live for the process lifetime


def _acquire_single_instance(mutex_name: str) -> bool:
    # Name convention: "JDE_Simple{Thing}Tool_SingleInstance"
    # Session-local (no "Global\" prefix): each Windows session (e.g. RDP,
    # fast user switching) gets its own instance instead of colliding across users.
    global _mutex_handle
    try:
        # use_last_error=True: ctypes.windll's GetLastError() can be clobbered
        # by ctypes-internal calls, so read the error via ctypes.get_last_error() instead.
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        _mutex_handle = kernel32.CreateMutexW(None, False, mutex_name)
        return ctypes.get_last_error() != 183   # ERROR_ALREADY_EXISTS
    except Exception:
        return True   # fail open: never block launch over a mutex error


def _focus_existing_window(app_title: str) -> bool:
    # Best-effort only: any failure here must not stop the caller from deciding what to do next.
    try:
        user32 = ctypes.windll.user32
        found = {"hwnd": None}

        WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

        def _enum_proc(hwnd, lparam):
            if not user32.IsWindowVisible(hwnd):
                return True
            length = user32.GetWindowTextLengthW(hwnd)
            if length == 0:
                return True
            buf = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buf, length + 1)
            # Exact match only: a prefix match could hit an unrelated window
            # (e.g. a browser tab starting with the app name). A miss falls
            # through to a normal launch anyway.
            if buf.value == app_title:
                found["hwnd"] = hwnd
                return False   # stop enumerating, match found
            return True

        user32.EnumWindows(WNDENUMPROC(_enum_proc), 0)

        hwnd = found["hwnd"]
        if not hwnd:
            return False

        SW_RESTORE = 9
        if user32.IsIconic(hwnd):
            user32.ShowWindow(hwnd, SW_RESTORE)
        user32.SetForegroundWindow(hwnd)
        return True
    except Exception:
        return False
