"""App-level settings and misc bridge helpers: theme preference, opening a
link in the system browser, and the GitHub update check."""
import json
import urllib.error
import urllib.parse
import urllib.request

from sfl import config
from sfl.errors import _update_error_reason
from sfl.prefs import load_prefs, save_prefs


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


def open_url(log, url):
    """Open a link in the system browser, never by navigating the app window."""
    if not isinstance(url, str):
        log("open_url refused: only https://jde-projects.com links are allowed")
        return {"ok": False}
    try:
        parts = urllib.parse.urlsplit(url)
    except ValueError:
        log("open_url refused: only https://jde-projects.com links are allowed")
        return {"ok": False}
    if parts.scheme != "https" or parts.netloc != "jde-projects.com":
        log("open_url refused: only https://jde-projects.com links are allowed")
        return {"ok": False}

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
