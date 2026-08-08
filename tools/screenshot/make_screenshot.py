#!/usr/bin/env python3
"""Regenerate screenshots/simple_firearm_logbook-light-dark.png.

Simple Firearm Logbook is a desktop app: at screenshot time there is no web
server and no Python backend to talk to. Its UI is a self-contained HTML file
that degrades gracefully outside pywebview, so this tool serves the page and
its assets from a temp folder, seeds an invented collection straight into the
state the backend would normally fill, and drives the page's own render
function to produce the picture.

Nothing here touches the working copy except the final screenshot. The UI
file, icon, and fonts folder are copied into a temp folder; the real files are
only read until the composed image is written.

    python tools/screenshot/make_screenshot.py

Options:
    --keep            leave the temp folder in place for inspection
    --build-tools P   path to the build-tools repo (default: sibling folder)
"""

import http.server
import json
import os
import re
import shutil
import socket
import socketserver
import subprocess
import sys
import tempfile
import threading

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import scene  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(HERE))

OUT_IMAGE = os.path.join(
    REPO_ROOT, "screenshots", "simple_firearm_logbook-light-dark.png"
)

# Each theme is laid out at this size and captured at half scale, giving two
# 900x425 halves and the 1800x425 composite used by the README. The height
# closes just below the table with the bottom bar still visible.
LAYOUT_WIDTH = 1800
LAYOUT_HEIGHT = 850
CAPTURE_SCALE = 0.5


def fail(message: str) -> None:
    print(f"ERROR: {message}", file=sys.stderr)
    sys.exit(1)


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def read_app_version() -> str:
    path = os.path.join(REPO_ROOT, "simple_firearm_logbook.py")
    with open(path, encoding="utf-8") as source_file:
        source = source_file.read()
    match = re.search(r'APP_VERSION\s*=\s*"([^"]+)"', source)
    if not match:
        fail(f"could not find APP_VERSION in {path}")
    return match.group(1)


def stage_ui(temp_dir: str) -> None:
    """Copy just what the page needs into temp_dir."""
    shutil.copy2(
        os.path.join(REPO_ROOT, "simple_firearm_logbook-UI.html"),
        os.path.join(temp_dir, "index.html"),
    )
    shutil.copy2(os.path.join(REPO_ROOT, "simple_firearm_logbook.png"), temp_dir)
    shutil.copytree(
        os.path.join(REPO_ROOT, "fonts"), os.path.join(temp_dir, "fonts")
    )


def build_setup_script(version: str) -> str:
    """Seed the backend-owned list and drive this page's render path.

    boot() only runs on the pywebviewready event, which never fires in a plain
    browser. The backend normally fills FIREARMS before renderTable() runs, so
    setup mirrors that path and also exposes the disposed sample row.
    """
    return (
        f"STATE.version = {json.dumps(version)};"
        f"FIREARMS = {json.dumps(scene.FIREARMS)};"
        "showDisposed = true;"
        "document.getElementById('showDisposedToggle').checked = true;"
        f"document.getElementById('verLabel').textContent = 'v' + {json.dumps(version)};"
        "if (typeof renderTable === 'function') renderTable();"
    )


def write_capture_config(temp_dir: str, port: int, version: str) -> str:
    config = {
        "url": f"http://127.0.0.1:{port}/index.html",
        "width": LAYOUT_WIDTH,
        "height": LAYOUT_HEIGHT,
        "scale": CAPTURE_SCALE,
        "outDir": "shots",
        "waitFor": "typeof renderTable === 'function'",
        "setup": build_setup_script(version),
        "settleMs": 500,
        "shots": [
            {"name": "light", "script": "applyTheme('light')"},
            {"name": "dark", "script": "applyTheme('dark')"},
        ],
    }
    path = os.path.join(temp_dir, "shots.json")
    with open(path, "w", encoding="utf-8") as config_file:
        json.dump(config, config_file, indent=2)
    return path


def run(cmd: list[str], label: str) -> None:
    result = subprocess.run(cmd, cwd=REPO_ROOT, check=False)
    if result.returncode != 0:
        fail(f"{label} failed with exit code {result.returncode}")


def main(argv: list[str]) -> None:
    keep = "--keep" in argv
    build_tools = os.path.join(os.path.dirname(REPO_ROOT), "build-tools")
    if "--build-tools" in argv:
        index = argv.index("--build-tools") + 1
        if index >= len(argv):
            fail("--build-tools needs a path after it")
        build_tools = argv[index]

    capture_script = os.path.join(build_tools, "screenshot", "capture.mjs")
    compose_script = os.path.join(build_tools, "screenshot", "compose.py")
    for path in (capture_script, compose_script):
        if not os.path.exists(path):
            fail(f"missing {path}. Pass --build-tools with the repo path.")

    version = read_app_version()
    temp_dir = tempfile.mkdtemp(prefix="sfl-screenshot-")
    httpd = None

    try:
        stage_ui(temp_dir)
        port = free_port()

        class Handler(http.server.SimpleHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def __init__(self, *args, **kwargs):
                super().__init__(*args, directory=temp_dir, **kwargs)

        httpd = socketserver.TCPServer(("127.0.0.1", port), Handler)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()

        config_path = write_capture_config(temp_dir, port, version)
        run(["node", capture_script, config_path], "capture")

        shots_dir = os.path.join(temp_dir, "shots")
        run(
            [
                sys.executable,
                compose_script,
                OUT_IMAGE,
                os.path.join(shots_dir, "light.png"),
                os.path.join(shots_dir, "dark.png"),
            ],
            "compose",
        )
    finally:
        if httpd is not None:
            httpd.shutdown()
        if keep:
            print(f"temp folder kept at {temp_dir}")
        else:
            shutil.rmtree(temp_dir, ignore_errors=True)
            if os.path.exists(temp_dir):
                print(f"WARNING: could not remove {temp_dir}", file=sys.stderr)

    print(f"seeded version: v{version}")
    print(f"updated {OUT_IMAGE}")


if __name__ == "__main__":
    main(sys.argv[1:])
