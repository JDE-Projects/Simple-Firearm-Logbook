"""Tests for optional UI extension files and their Api bridge methods."""

from sfl.api import Api
from sfl.launcher import AppDescription
from sfl.services.extension import read_extension_files


def _description(**overrides):
    values = {
        "product_name": "Embedding Logbook",
        "app_id": "Example.EmbeddingLogbook",
        "single_instance_name": "Example_EmbeddingLogbook",
        "version": "1.0.0",
        "update_owner": "example-owner",
        "update_repo": "example-repo",
        "update_link": "https://example.invalid/update",
    }
    values.update(overrides)
    return AppDescription(**values)


def test_read_extension_files_returns_none_when_both_paths_are_unset():
    messages = []

    result = read_extension_files(None, "", messages.append)

    assert result == {"ok": True, "script": None, "stylesheet": None, "errors": []}
    assert messages == []


def test_read_extension_files_returns_utf8_script_and_stylesheet_content(tmp_path):
    script_path = tmp_path / "extension.js"
    stylesheet_path = tmp_path / "extension.css"
    script = "const greeting = 'Bonjour, caf\u00e9';\n"
    stylesheet = ".notice { content: '\u2603'; }\n"
    script_path.write_text(script, encoding="utf-8")
    stylesheet_path.write_text(stylesheet, encoding="utf-8")

    result = read_extension_files(str(script_path), str(stylesheet_path), lambda message: None)

    assert result == {"ok": True, "script": script, "stylesheet": stylesheet, "errors": []}


def test_read_extension_files_loads_stylesheet_when_script_is_missing(tmp_path):
    script_path = tmp_path / "missing.js"
    stylesheet_path = tmp_path / "extension.css"
    stylesheet_path.write_text(".notice { color: teal; }", encoding="utf-8")
    messages = []

    result = read_extension_files(str(script_path), str(stylesheet_path), messages.append)

    assert result["ok"] is True
    assert result["script"] is None
    assert result["stylesheet"] == ".notice { color: teal; }"
    assert len(result["errors"]) == 1
    assert str(script_path) in result["errors"][0]
    assert messages == result["errors"]


def test_read_extension_files_reports_non_utf8_file(tmp_path):
    script_path = tmp_path / "extension.js"
    script_path.write_bytes(b"\xff\xfe")
    messages = []

    result = read_extension_files(str(script_path), None, messages.append)

    assert result["script"] is None
    assert result["stylesheet"] is None
    assert len(result["errors"]) == 1
    assert str(script_path) in result["errors"][0]
    assert "UTF-8" in result["errors"][0]
    assert messages == result["errors"]


def test_api_get_app_info_returns_error_without_description():
    api = Api()

    result = api.get_app_info()

    assert result["ok"] is False
    assert result["error"]


def test_api_get_app_info_returns_description_values():
    api = Api()
    description = _description()
    api.set_app_description(description)

    assert api.get_app_info() == {
        "ok": True,
        "product_name": "Embedding Logbook",
        "update_link": "https://example.invalid/update",
    }


def test_api_get_extension_returns_empty_result_without_description():
    api = Api()

    assert api.get_extension() == {"ok": True, "script": None, "stylesheet": None, "errors": []}


def test_api_get_extension_reads_description_paths(tmp_path):
    script_path = tmp_path / "extension.js"
    stylesheet_path = tmp_path / "extension.css"
    script_path.write_text("window.extensionReady = true;", encoding="utf-8")
    stylesheet_path.write_text("body { color: teal; }", encoding="utf-8")
    api = Api()
    api.set_app_description(
        _description(extension_script=str(script_path), extension_stylesheet=str(stylesheet_path))
    )

    assert api.get_extension() == {
        "ok": True,
        "script": "window.extensionReady = true;",
        "stylesheet": "body { color: teal; }",
        "errors": [],
    }
