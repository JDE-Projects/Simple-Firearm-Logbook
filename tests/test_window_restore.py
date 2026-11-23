"""Tests for _restore_geometry's decisions: when it moves the window and to
where. The Win32 calls are replaced with a fake that records each move, so
these check the logic, not real screen placement (a real-window check on a
small screen covers that).
"""

import types

import pytest

from sfl import platform_win as pw

HWND = 1234
DEFAULT_RECT = (100, -63, 1280, 820)   # x, y, width, height as Qt placed it


class _Fn:
    """A stand-in Win32 function that accepts argtypes/restype assignment."""

    def __init__(self, impl):
        self._impl = impl

    def __call__(self, *args):
        return self._impl(*args)


class _FakeUser32:
    def __init__(self, rect, minimized=False, maximized=False, saved_monitor=True):
        self.rect = list(rect)
        self.minimized = minimized
        self.maximized = maximized
        self.saved_monitor = saved_monitor
        self.moves = []
        self.GetWindowRect = _Fn(self._get_window_rect)
        self.SetWindowPos = _Fn(self._set_window_pos)
        self.IsIconic = _Fn(lambda hwnd: self.minimized)
        self.IsZoomed = _Fn(lambda hwnd: self.maximized)
        self.MonitorFromWindow = _Fn(lambda hwnd, flags: 1)
        self.MonitorFromPoint = _Fn(lambda point, flags: 1 if self.saved_monitor else 0)

    def _get_window_rect(self, hwnd, rect_ref):
        x, y, w, h = self.rect
        r = rect_ref._obj
        r.left, r.top, r.right, r.bottom = x, y, x + w, y + h
        return True

    def _set_window_pos(self, hwnd, after, x, y, w, h, flags):
        self.moves.append((x, y, w, h))
        self.rect = [x, y, w, h]
        return True


@pytest.fixture
def desktop(monkeypatch):
    """Patch the Win32 layer; returns a function that sets up one scenario."""

    def setup(work, rect=DEFAULT_RECT, saved=None, handle_after=0, **user32_state):
        fake = _FakeUser32(rect, **user32_state)
        lookups = {"count": 0}

        def own_window_handle(title):
            lookups["count"] += 1
            return HWND if lookups["count"] > handle_after else None

        monkeypatch.setattr(pw, "_restore_claimed", False)
        monkeypatch.setattr(pw.ctypes, "windll", types.SimpleNamespace(user32=fake), raising=False)
        monkeypatch.setattr(pw, "_own_window_handle", own_window_handle)
        monkeypatch.setattr(pw, "_monitor_work_area", lambda hmonitor: work)
        monkeypatch.setattr(pw, "_frame_insets", lambda hwnd: (0, 0, 0, 0))
        monkeypatch.setattr(pw.time, "sleep", lambda seconds: None)
        prefs_data = {} if saved is None else {"window": saved}
        monkeypatch.setattr(pw.prefs, "load_prefs", lambda log=None: prefs_data)
        return fake

    return setup


WIN = types.SimpleNamespace(title="Simple Firearm Logbook")
SMALL_WORK = (0, 0, 1024, 728)
LARGE_WORK = (0, 0, 1920, 1040)


def _fully_on_screen(rect, work):
    x, y, w, h = rect
    left, top, right, bottom = work
    return x >= left and y >= top and x + w <= right and y + h <= bottom


def test_no_saved_position_on_small_screen_fits_window_on_screen(desktop):
    fake = desktop(SMALL_WORK)
    pw._restore_geometry(WIN)
    assert fake.moves == [(0, 0, 1024, 728)]
    assert _fully_on_screen(fake.rect, SMALL_WORK)


def test_no_saved_position_on_large_screen_leaves_default_size(desktop):
    fake = desktop(LARGE_WORK, rect=(320, 110, 1280, 820))
    pw._restore_geometry(WIN)
    assert fake.moves == []


def test_window_kept_larger_than_screen_keeps_title_bar_on_screen(desktop):
    # The window's minimum size can stop the shrink; Qt then keeps it larger
    # than the screen. Its top-left corner, and so its title bar, stays on.
    fake = desktop(SMALL_WORK)
    fake.SetWindowPos = _Fn(lambda hwnd, after, x, y, w, h, flags: fake.moves.append((x, y, w, h)) or True)
    pw._restore_geometry(WIN)
    x, y, _w, _h = fake.moves[-1]
    assert (x, y) == (0, 0)


def test_saved_position_is_restored_fitted(desktop):
    fake = desktop(LARGE_WORK, saved={"x": 1700, "y": 50, "width": 1280, "height": 820})
    pw._restore_geometry(WIN)
    assert fake.moves == [(640, 50, 1280, 820)]


def test_saved_position_on_disconnected_monitor_still_fits_window(desktop):
    fake = desktop(
        SMALL_WORK, saved={"x": 3000, "y": 50, "width": 1280, "height": 820}, saved_monitor=False,
    )
    pw._restore_geometry(WIN)
    assert fake.moves == [(0, 0, 1024, 728)]


def test_malformed_saved_position_still_fits_window(desktop):
    fake = desktop(SMALL_WORK, saved={"x": "100", "y": 0, "width": 1280, "height": 820})
    pw._restore_geometry(WIN)
    assert fake.moves == [(0, 0, 1024, 728)]


def test_runs_once_per_launch(desktop):
    # A restore from the taskbar fires `shown` again; it must not move the window.
    fake = desktop(SMALL_WORK)
    pw._restore_geometry(WIN)
    fake.rect = [500, -40, 1280, 820]
    pw._restore_geometry(WIN)
    assert fake.moves == [(0, 0, 1024, 728)]


def test_waits_for_window_handle(desktop):
    fake = desktop(SMALL_WORK, handle_after=3)
    pw._restore_geometry(WIN)
    assert fake.moves == [(0, 0, 1024, 728)]


def test_gives_up_quietly_when_window_never_found(desktop):
    fake = desktop(SMALL_WORK, handle_after=10_000)
    pw._restore_geometry(WIN)
    assert fake.moves == []


@pytest.mark.parametrize("state", [{"minimized": True}, {"maximized": True}])
def test_leaves_minimized_or_maximized_window_alone(desktop, state):
    fake = desktop(SMALL_WORK, saved={"x": 0, "y": 0, "width": 800, "height": 600}, **state)
    pw._restore_geometry(WIN)
    assert fake.moves == []
