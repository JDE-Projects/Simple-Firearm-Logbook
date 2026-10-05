"""Coverage for the approved Qt bundle trimming list and safety checks."""

import importlib.util
import os
from pathlib import Path

import pytest


@pytest.fixture
def trimmer():
    script = Path(__file__).parents[1] / "tools" / "trim_bundle.py"
    spec = importlib.util.spec_from_file_location("trim_bundle", script)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


@pytest.fixture(autouse=True)
def checked_pyside6_version(trimmer, monkeypatch):
    monkeypatch.setattr(
        trimmer.metadata, "version", lambda _: trimmer.CHECKED_PYSIDE6_VERSION
    )


def _bundle_folder(tmp_path):
    bundle = tmp_path / "app" / "_internal" / "PySide6"
    bundle.mkdir(parents=True)
    return bundle


def _write_file(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("test", encoding="utf-8")


def _link_folder(link, target):
    """Point link at target: a junction on Windows (no admin rights needed),
    a symbolic link elsewhere."""
    link.parent.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        import _winapi

        _winapi.CreateJunction(str(target), str(link))
    else:
        link.symlink_to(target, target_is_directory=True)


def test_removes_approved_items_and_leaves_neighbours(tmp_path, trimmer, capsys):
    bundle = _bundle_folder(tmp_path)
    _write_file(bundle / "assistant.exe")
    _write_file(bundle / "Qt6Charts.dll")
    _write_file(bundle / "QtCharts.pyd")
    _write_file(bundle / "qml" / "QtCharts" / "qmldir")
    _write_file(bundle / "plugins" / "assetimporters" / "importer.dll")
    _write_file(bundle / "metatypes" / "qt6quick3d_metatypes.json")
    _write_file(bundle / "QtWebEngineProcess.exe")
    _write_file(bundle / "Qt6Quick.dll")

    assert trimmer.trim_bundle(bundle.parents[1]) == 0

    assert not (bundle / "assistant.exe").exists()
    assert not (bundle / "Qt6Charts.dll").exists()
    assert not (bundle / "QtCharts.pyd").exists()
    assert not (bundle / "qml" / "QtCharts").exists()
    assert not (bundle / "plugins" / "assetimporters").exists()
    assert not (bundle / "metatypes" / "qt6quick3d_metatypes.json").exists()
    assert (bundle / "QtWebEngineProcess.exe").exists()
    assert (bundle / "Qt6Quick.dll").exists()
    assert "Removed 6 approved item(s)." in capsys.readouterr().out


def test_matching_version_trims_as_before(tmp_path, trimmer):
    bundle = _bundle_folder(tmp_path)
    approved = bundle / "assistant.exe"
    neighbour = bundle / "QtWebEngineProcess.exe"
    _write_file(approved)
    _write_file(neighbour)

    assert trimmer.trim_bundle(bundle.parents[1]) == 0

    assert not approved.exists()
    assert neighbour.exists()


def test_refuses_different_pyside6_version_without_removing_items(
    tmp_path, trimmer, monkeypatch, capsys
):
    bundle = _bundle_folder(tmp_path)
    approved = bundle / "assistant.exe"
    _write_file(approved)
    monkeypatch.setattr(trimmer.metadata, "version", lambda _: "6.12.0")

    assert trimmer.trim_bundle(bundle.parents[1]) == 1

    assert approved.exists()
    output = capsys.readouterr().out
    assert "6.12.0" in output
    assert trimmer.CHECKED_PYSIDE6_VERSION in output


def test_refuses_when_pyside6_version_cannot_be_read(tmp_path, trimmer, monkeypatch):
    bundle = _bundle_folder(tmp_path)
    approved = bundle / "assistant.exe"
    _write_file(approved)

    def version_not_found(_):
        raise trimmer.metadata.PackageNotFoundError

    monkeypatch.setattr(trimmer.metadata, "version", version_not_found)

    assert trimmer.trim_bundle(bundle.parents[1]) == 1

    assert approved.exists()


def test_tolerates_already_absent_items(tmp_path, trimmer, capsys):
    bundle = _bundle_folder(tmp_path)

    assert trimmer.trim_bundle(bundle.parents[1]) == 0

    assert capsys.readouterr().out == "Removed 0 approved item(s).\n"


def test_refuses_outside_or_wrong_qt_folder(tmp_path, trimmer, capsys):
    bundle = _bundle_folder(tmp_path)
    app = bundle.parents[1]
    outside = tmp_path / "outside" / "_internal" / "PySide6"
    outside.mkdir(parents=True)
    wrong_folder = app / "_internal" / "NotPySide6"
    wrong_folder.mkdir(parents=True)

    assert trimmer.trim_bundle(app, outside) == 1
    assert "outside the app folder" in capsys.readouterr().out
    assert trimmer.trim_bundle(app, wrong_folder) == 1
    assert "must end in _internal\\PySide6" in capsys.readouterr().out


def test_refuses_listed_links_that_point_outside(tmp_path, trimmer, capsys):
    bundle = _bundle_folder(tmp_path)
    internal = bundle.parent
    outside = tmp_path / "outside"
    _write_file(outside / "keep.txt")
    _link_folder(bundle / "qml" / "QtCharts", outside)
    _link_folder(internal / "LIBPQ.dll", outside)

    assert trimmer.trim_bundle(internal.parent) == 1

    assert (outside / "keep.txt").exists()
    assert (bundle / "qml" / "QtCharts").exists()
    assert (internal / "LIBPQ.dll").exists()
    output = capsys.readouterr().out
    assert "Refusing to remove outside the Qt bundle" in output
    assert "Refusing to remove outside the app bundle" in output
    assert "Listed item still present: qml/QtCharts" in output
    assert "Listed item still present: LIBPQ.dll" in output


def test_exits_nonzero_when_an_item_cannot_be_removed(
    tmp_path, trimmer, monkeypatch, capsys
):
    bundle = _bundle_folder(tmp_path)
    blocked = bundle / "assistant.exe"
    _write_file(blocked)

    def fail_remove(path):
        raise OSError("simulated locked file")

    monkeypatch.setattr(trimmer, "_remove_item", fail_remove)

    with pytest.raises(SystemExit) as exit_info:
        trimmer.main([str(bundle.parents[1])])

    assert exit_info.value.code == 1
    assert blocked.exists()
    output = capsys.readouterr().out
    assert "assistant.exe" in output
    assert "simulated locked file" in output


def test_removes_postgres_driver_and_its_libraries(tmp_path, trimmer, capsys):
    bundle = _bundle_folder(tmp_path)
    internal = bundle.parent
    _write_file(bundle / "plugins" / "sqldrivers" / "qsqlpsql.dll")
    _write_file(bundle / "plugins" / "sqldrivers" / "qsqlite.dll")
    for name in ("LIBPQ.dll", "libssl-3-x64.dll", "libcrypto-3-x64.dll"):
        _write_file(internal / name)
    for name in ("libssl-3.dll", "libcrypto-3.dll", "python313.dll"):
        _write_file(internal / name)

    assert trimmer.trim_bundle(internal.parent) == 0

    assert not (bundle / "plugins" / "sqldrivers" / "qsqlpsql.dll").exists()
    assert (bundle / "plugins" / "sqldrivers" / "qsqlite.dll").exists()
    for name in ("LIBPQ.dll", "libssl-3-x64.dll", "libcrypto-3-x64.dll"):
        assert not (internal / name).exists()
    for name in ("libssl-3.dll", "libcrypto-3.dll", "python313.dll"):
        assert (internal / name).exists()
    output = capsys.readouterr().out
    assert "Removed: LIBPQ.dll" in output
    assert "Removed: plugins/sqldrivers/qsqlpsql.dll" in output
    assert "Removed 4 approved item(s)." in output


def test_internal_items_absent_is_fine(tmp_path, trimmer, capsys):
    bundle = _bundle_folder(tmp_path)
    _write_file(bundle.parent / "libssl-3.dll")

    assert trimmer.trim_bundle(bundle.parents[1]) == 0

    assert capsys.readouterr().out == "Removed 0 approved item(s).\n"


def test_exits_nonzero_when_an_internal_item_cannot_be_removed(
    tmp_path, trimmer, monkeypatch, capsys
):
    bundle = _bundle_folder(tmp_path)
    blocked = bundle.parent / "LIBPQ.dll"
    _write_file(blocked)

    def fail_remove(path):
        raise OSError("simulated locked file")

    monkeypatch.setattr(trimmer, "_remove_item", fail_remove)

    assert trimmer.trim_bundle(bundle.parents[1]) == 1

    assert blocked.exists()
    output = capsys.readouterr().out
    assert "Could not remove LIBPQ.dll" in output
    assert "Listed item still present: LIBPQ.dll" in output

