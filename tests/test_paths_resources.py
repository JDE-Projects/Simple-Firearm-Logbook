"""Tests for package resources and the embedding-safe data directory."""
import os
from pathlib import Path

from sfl import paths


def test_resource_path_finds_html_icon_and_fonts_outside_working_directory(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    resource_names = [
        "simple_firearm_logbook-UI.html",
        "simple_firearm_logbook.png",
        *[f"fonts/{font.name}" for font in Path(paths.resource_path("fonts")).glob("*.ttf")],
    ]

    assert all(os.path.isfile(paths.resource_path(name)) for name in resource_names)


def test_app_dir_uses_entry_script_folder_when_unfrozen(monkeypatch, tmp_path):
    entry_script = tmp_path / "embedding_app.py"
    monkeypatch.setattr(paths.sys, "argv", [str(entry_script)])
    monkeypatch.setattr(paths.sys, "frozen", False, raising=False)

    assert paths.app_dir() == str(tmp_path)


def test_app_dir_uses_executable_folder_when_frozen(monkeypatch, tmp_path):
    executable = tmp_path / "Embedding Logbook.exe"
    monkeypatch.setattr(paths.sys, "frozen", True, raising=False)
    monkeypatch.setattr(paths.sys, "executable", str(executable))

    assert paths.app_dir() == str(tmp_path)
