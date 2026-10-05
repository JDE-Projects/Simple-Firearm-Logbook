import builtins
import json

from sfl import debug_log, paths
from sfl.api import Api


class FakeWindow:
    def __init__(self):
        self.calls = []

    def evaluate_js(self, script):
        self.calls.append(script)


def test_set_debug_creates_redacted_log(monkeypatch, tmp_path):
    monkeypatch.setattr(paths, "app_dir", lambda: str(tmp_path))
    api = Api()

    assert api.set_debug(True) == {"ok": True}
    api.log(r"C:\Users\John\Documents\record.pdf")

    logs = list(tmp_path.glob("Debug_Log_*.txt"))
    assert len(logs) == 1
    contents = logs[0].read_text(encoding="utf-8")
    assert "Debug log started" in contents
    assert "John" not in contents
    assert r"C:\Users\<user>\Documents\record.pdf" in contents


def test_debug_warning_queues_then_flushes_and_uses_safe_javascript():
    api = Api()
    window = FakeWindow()
    api.set_window(window)
    message = 'A "quoted" warning\nwith a new line'

    api._on_debug_warning(message)
    assert window.calls == []

    api.flush_debug_warnings()
    assert window.calls == [f"showToast({json.dumps(message)});"]

    api._on_debug_warning("shown immediately")
    assert window.calls[-1] == f"showToast({json.dumps('shown immediately')});"


def test_write_failure_turns_logging_off_and_warns(monkeypatch, tmp_path):
    monkeypatch.setattr(paths, "app_dir", lambda: str(tmp_path))
    api = Api()
    window = FakeWindow()
    api.set_window(window)
    api.flush_debug_warnings()
    assert api.set_debug(True) == {"ok": True}

    real_open = builtins.open

    def fake_open(file, mode="r", *args, **kwargs):
        if "a" in mode:
            raise OSError("simulated disk failure")
        return real_open(file, mode, *args, **kwargs)

    monkeypatch.setattr(debug_log, "open", fake_open, raising=False)
    api.log("write failure")

    assert api._debug_log.is_enabled() is False
    assert len(window.calls) == 1
    assert "Debug log: write failed" in window.calls[0]
