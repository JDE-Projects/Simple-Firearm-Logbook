"""Remove approved files from a PyInstaller app bundle.

Removes GPL-only Qt components and Qt developer tools from _internal\\PySide6. Also
removes the PostgreSQL SQL driver plugin and the libraries it pulls in (LIBPQ and
OpenSSL 3.5), which the app never uses. Python's own OpenSSL files are kept. Every
removal path must resolve inside its anchored folder, and the script exits non-zero if
a listed item is still present afterwards. Trimming is refused unless the installed
PySide6 version matches the version against which the trim list and Qt licenses were checked.
"""

from __future__ import annotations

import argparse
import shutil
from importlib import metadata
from pathlib import Path

# The trim list and licenses/THIRD-PARTY-LICENSES-QT.txt and
# licenses/THIRD-PARTY-LICENSES-CHROMIUM.txt were checked against this version.
CHECKED_PYSIDE6_VERSION = "6.11.2"

# Qt developer tools. QtWebEngineProcess.exe is intentionally not listed.
DEVELOPER_TOOLS = (
    "assistant.exe", "balsam.exe", "balsamui.exe", "designer.exe", "linguist.exe",
    "lrelease.exe", "lupdate.exe", "qmlcachegen.exe", "qmlformat.exe",
    "qmlimportscanner.exe", "qmllint.exe", "qmlls.exe", "qmltyperegistrar.exe",
    "qsb.exe", "rcc.exe", "svgtoqml.exe", "uic.exe",
)

# GPL-only Qt module DLLs.
GPL_DLLS = tuple(
    f"{name}.dll"
    for name in (
        "Qt6CanvasPainter", "Qt6Charts", "Qt6ChartsQml", "Qt6DataVisualization",
        "Qt6DataVisualizationQml", "Qt6Graphs", "Qt6GraphsWidgets", "Qt6HttpServer",
        "Qt6Lottie", "Qt6LottieVectorImageGenerator", "Qt6LottieVectorImageHelpers",
        "Qt6NetworkAuth", "Qt6Quick3D", "Qt6Quick3DAssetImport", "Qt6Quick3DAssetUtils",
        "Qt6Quick3DEffects", "Qt6Quick3DGlslParser", "Qt6Quick3DHelpers",
        "Qt6Quick3DHelpersImpl", "Qt6Quick3DIblBaker", "Qt6Quick3DParticleEffects",
        "Qt6Quick3DParticles", "Qt6Quick3DRuntimeRender", "Qt6Quick3DSpatialAudio",
        "Qt6Quick3DUtils", "Qt6Quick3DXr", "Qt6QuickTimeline",
        "Qt6QuickTimelineBlendTrees", "Qt6VirtualKeyboard", "Qt6VirtualKeyboardQml",
        "Qt6VirtualKeyboardSettings",
    )
)

# GPL-only Qt Python bindings.
PYTHON_BINDINGS = tuple(
    f"{name}{suffix}"
    for name in (
        "QtCanvasPainter", "QtCharts", "QtDataVisualization", "QtGraphs",
        "QtGraphsWidgets", "QtHttpServer", "QtNetworkAuth", "QtQuick3D",
    )
    for suffix in (".pyd", ".pyi")
)

# QML modules, plugins, and the Quick 3D asset importer.
QML_AND_PLUGINS = (
    "qml/QtCharts", "qml/QtDataVisualization", "qml/QtGraphs", "qml/QtQuick3D",
    "qml/QtQuick/Timeline", "qml/QtQuick/VirtualKeyboard",
    "plugins/platforminputcontexts/qtvirtualkeyboardplugin.dll",
    "plugins/qmltooling/qmldbg_quick3dprofiler.dll",
    "plugins/vectorimageformats/qlottievectorimage.dll", "plugins/assetimporters",
)

# Developer leftovers for the removed GPL-only modules.
DEVELOPER_LEFTOVERS = (
    "glue/qtcanvaspainter.cpp", "glue/qtcharts.cpp", "glue/qtdatavisualization.cpp",
    "glue/qtgraphs.cpp", "glue/qtnetworkauth.cpp", "glue/qtquick3d.cpp",
    "doc/qtcanvaspainter.rst", "include/QtCanvasPainter", "include/QtCharts",
    "include/QtDataVisualization", "include/QtGraphs", "include/QtGraphsWidgets",
    "include/QtHttpServer", "include/QtNetworkAuth", "include/QtQuick3D",
    "typesystems/datavisualization_common.xml",
    "typesystems/typesystem_canvaspainter.xml", "typesystems/typesystem_charts.xml",
    "typesystems/typesystem_datavisualization.xml", "typesystems/typesystem_graphs.xml",
    "typesystems/typesystem_graphswidgets.xml", "typesystems/typesystem_httpserver.xml",
    "typesystems/typesystem_networkauth.xml", "typesystems/typesystem_quick3d.xml",
)

# Metatype files for the approved GPL-only modules. The wildcard covers private variants.
METATYPE_PATTERNS = tuple(
    f"metatypes/qt6{name}*_metatypes.json"
    for name in (
        "canvaspainter", "charts", "chartsqml", "datavisualization",
        "datavisualizationqml", "graphs", "graphswidgets", "httpserver", "lottie",
        "lottievectorimagegeneratorprivate", "lottievectorimagehelpers", "networkauth",
        "quick3d", "quick3dassetimport", "quick3dassetutils", "quick3deffects",
        "quick3dglslparserprivate", "quick3dhelpers", "quick3diblbaker",
        "quick3dparticleeffects", "quick3dparticles", "quick3druntimerender",
        "quick3dutils", "quick3dxr", "quicktimeline", "virtualkeyboard",
    )
)

# Qt's PostgreSQL SQL driver. The app uses Python's sqlite3 and never Qt SQL.
UNUSED_SQL_DRIVER = ("plugins/sqldrivers/qsqlpsql.dll",)

LITERAL_PATHS = (
    DEVELOPER_TOOLS + GPL_DLLS + PYTHON_BINDINGS + QML_AND_PLUGINS + DEVELOPER_LEFTOVERS
    + UNUSED_SQL_DRIVER
)

# Files PyInstaller copies into _internal for the PostgreSQL driver, found on the build
# machine's PATH. libssl-3.dll and libcrypto-3.dll belong to Python and are not listed.
INTERNAL_PATHS = ("LIBPQ.dll", "libssl-3-x64.dll", "libcrypto-3-x64.dll")

EXPECTED_INTERNAL_PATH = Path("_internal")
EXPECTED_BUNDLE_PATH = EXPECTED_INTERNAL_PATH / "PySide6"


def _path_exists(path: Path) -> bool:
    return path.exists() or path.is_symlink()


def _is_within(path: Path, directory: Path) -> bool:
    try:
        path.relative_to(directory)
    except ValueError:
        return False
    return True


def _resolve_bundle_folder(app_folder: Path, target_folder: Path | None = None) -> Path:
    """Return a safe resolved PySide6 folder or raise ValueError."""
    app_folder = app_folder.resolve(strict=False)
    target_folder = target_folder or app_folder / EXPECTED_BUNDLE_PATH
    try:
        target_folder = target_folder.resolve(strict=True)
    except OSError as error:
        raise ValueError(f"Qt bundle folder does not exist: {target_folder}") from error

    if not _is_within(target_folder, app_folder):
        raise ValueError(f"Qt bundle folder is outside the app folder: {target_folder}")
    if target_folder.relative_to(app_folder) != EXPECTED_BUNDLE_PATH:
        raise ValueError(f"Qt bundle folder must end in _internal\\PySide6: {target_folder}")
    return target_folder


def _resolve_internal_folder(app_folder: Path) -> Path:
    """Return the safe resolved _internal folder or raise ValueError."""
    app_folder = app_folder.resolve(strict=False)
    internal_folder = app_folder / EXPECTED_INTERNAL_PATH
    try:
        internal_folder = internal_folder.resolve(strict=True)
    except OSError as error:
        raise ValueError(f"Bundle folder does not exist: {internal_folder}") from error

    if not _is_within(internal_folder, app_folder):
        raise ValueError(f"Bundle folder is outside the app folder: {internal_folder}")
    if internal_folder.relative_to(app_folder) != EXPECTED_INTERNAL_PATH:
        raise ValueError(f"Bundle folder must be _internal: {internal_folder}")
    return internal_folder


def _matching_paths(bundle_folder: Path) -> list[Path]:
    """Return approved paths without searching outside the bundle."""
    paths = [bundle_folder.joinpath(*path.split("/")) for path in LITERAL_PATHS]
    metatypes = bundle_folder / "metatypes"
    if _path_exists(metatypes):
        resolved_metatypes = metatypes.resolve(strict=True)
        if not _is_within(resolved_metatypes, bundle_folder):
            raise OSError(f"Refusing to inspect outside the Qt bundle: {metatypes}")
        paths.extend(
            item for pattern in METATYPE_PATTERNS for item in bundle_folder.glob(pattern)
        )
    return paths


def _remove_item(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink()
    else:
        shutil.rmtree(path)


def _display_path(path: Path, bundle_folder: Path) -> str:
    return path.relative_to(bundle_folder).as_posix()


def _trim_folder(
    anchor: Path, candidates: list[Path], label: str, find_remaining
) -> tuple[int, bool, list[Path]]:
    """Remove existing candidates inside anchor. Return removed count, errors, leftovers."""
    removed = 0
    errors = False
    for path in candidates:
        if not _path_exists(path):
            continue
        resolved_path = path.resolve(strict=False)
        if not _is_within(resolved_path, anchor):
            print(f"ERROR: Refusing to remove outside the {label}: {path}")
            errors = True
            continue
        try:
            _remove_item(path)
        except OSError as error:
            print(f"ERROR: Could not remove {_display_path(path, anchor)}: {error}")
            errors = True
        else:
            print(f"Removed: {_display_path(path, anchor)}")
            removed += 1
    remaining = [path for path in find_remaining() if _path_exists(path)]
    return removed, errors, remaining


def trim_bundle(app_folder: Path, target_folder: Path | None = None) -> int:
    """Remove approved paths and return zero only when none remain."""
    try:
        installed_version = metadata.version("PySide6")
    except metadata.PackageNotFoundError:
        print(
            "ERROR: Installed PySide6 version could not be read; trim was refused "
            f"because it was checked against PySide6 {CHECKED_PYSIDE6_VERSION}."
        )
        return 1
    if installed_version != CHECKED_PYSIDE6_VERSION:
        print(
            f"ERROR: Installed PySide6 version is {installed_version}, but the trim was "
            f"checked against {CHECKED_PYSIDE6_VERSION}. Re-check the trim list in "
            "tools/trim_bundle.py and both Qt license files "
            "(licenses/THIRD-PARTY-LICENSES-QT.txt, "
            "licenses/THIRD-PARTY-LICENSES-CHROMIUM.txt) for the new version, then "
            "update CHECKED_PYSIDE6_VERSION."
        )
        return 1

    try:
        bundle_folder = _resolve_bundle_folder(app_folder, target_folder)
        internal_folder = _resolve_internal_folder(app_folder)
        qt_candidates = _matching_paths(bundle_folder)
    except (OSError, ValueError) as error:
        print(f"ERROR: {error}")
        return 1

    def internal_paths() -> list[Path]:
        return [internal_folder / name for name in INTERNAL_PATHS]

    try:
        qt_removed, qt_errors, qt_remaining = _trim_folder(
            bundle_folder, qt_candidates, "Qt bundle", lambda: _matching_paths(bundle_folder)
        )
        in_removed, in_errors, in_remaining = _trim_folder(
            internal_folder, internal_paths(), "app bundle", internal_paths
        )
    except OSError as error:
        print(f"ERROR: {error}")
        return 1

    for path in qt_remaining:
        print(f"ERROR: Listed item still present: {_display_path(path, bundle_folder)}")
    for path in in_remaining:
        print(f"ERROR: Listed item still present: {_display_path(path, internal_folder)}")
    print(f"Removed {qt_removed + in_removed} approved item(s).")
    failed = qt_errors or in_errors or qt_remaining or in_remaining
    return 1 if failed else 0


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "app_folder",
        nargs="?",
        default=r"dist\Simple {Thing} Tool",
        help="PyInstaller app folder to trim",
    )
    args = parser.parse_args(argv)
    raise SystemExit(trim_bundle(Path(args.app_folder)))


if __name__ == "__main__":
    main()
