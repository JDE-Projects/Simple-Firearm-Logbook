"""App-level settings and misc bridge helpers: theme preference, opening a
link in the system browser, the GitHub update check, the debug flag, and the
debug log writer."""
import datetime
import json
import os
import urllib.error
import urllib.request

from sfl import config, paths
from sfl.errors import _update_error_reason
from sfl.prefs import load_prefs, save_prefs
from sfl.utils import _redact_username


def _load_theme() -> str:
    theme = load_prefs().get("theme")
    return theme if theme in ("dark", "light") else "dark"


def get_theme():
    return _load_theme()


def save_theme(log, theme: str):
    if theme not in ("dark", "light"):
        return {"ok": False}
    prefs = load_prefs()
    prefs["theme"] = theme
    if save_prefs(prefs):
        log(f"Theme set to {theme}")
        return {"ok": True}
    log("Could not save theme pref")
    return {"ok": False}


def open_url(url: str):
    """Open a link in the system browser, never by navigating the app window."""
    import webbrowser

    webbrowser.open(url)
    return {"ok": True}


def check_update(log):
    """Compare the latest published release to APP_VERSION. Quiet in the UI on
    failure (see _update_error_reason), but always logged when debug is on."""
    result = {"current": config.APP_VERSION, "version": None, "update": False, "offline": False}
    try:
        url = f"https://api.github.com/repos/{config.GITHUB_OWNER}/{config.GITHUB_REPO}/releases/latest"
        req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json"})
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.load(r)
        latest = (data.get("tag_name") or "").lstrip("v")
        result["version"] = latest
        if latest and _is_newer(latest, config.APP_VERSION):
            result["update"] = True
        log(f"check_update: found v{latest}, current v{config.APP_VERSION}")
    except Exception as e:
        result["offline"] = True
        result["reason"] = _update_error_reason(e)
        log(f"check_update failed: {type(e).__name__}: {e}")
    return result


def _is_newer(latest: str, current: str) -> bool:
    def parts(v):
        out = []
        for p in v.split("."):
            try:
                out.append(int(p))
            except ValueError:
                out.append(0)
        return out

    return parts(latest) > parts(current)


def set_debug(on: bool, existing_debug_path):
    """Computes the new debug flag and log-file path for Api.set_debug.
    Returns (debug, debug_path, started), where started is True the one
    time a fresh log file is created, so the caller can log the "Debug log
    started" line after applying the new state."""
    debug = bool(on)
    debug_path = existing_debug_path
    started = False
    if debug and not debug_path:
        stamp = datetime.datetime.now().strftime("%m%d%Y_%H%M%S")
        debug_path = os.path.join(paths.app_dir(), f"Debug_Log_{stamp}.txt")
        started = True
    return debug, debug_path, started


def log(debug: bool, debug_path, msg: str) -> None:
    # Privacy rule for every call site: this app has no credentials, but
    # keep entries to ids, counts, and status words, not free-text notes
    # or personal details the user typed in.
    if not debug or not debug_path:
        return
    try:
        ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(debug_path, "a", encoding="utf-8") as f:
            f.write(f"[{ts}] {_redact_username(msg)}\n")
    except Exception:
        pass
