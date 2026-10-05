import webbrowser

import pytest

from sfl.services.settings import open_url


@pytest.mark.parametrize("url", ["https://jde-projects.com", "https://jde-projects.com/downloads/app"])
def test_open_url_allows_jde_projects_com(monkeypatch, url):
    opened = []
    logs = []
    monkeypatch.setattr(webbrowser, "open", lambda value: opened.append(value))

    result = open_url(logs.append, url)

    assert result == {"ok": True}
    assert opened == [url]


@pytest.mark.parametrize(
    "url",
    [
        "http://jde-projects.com",
        "https://example.com",
        "https://jde-projects.com.evil.com",
        "https://evil.com/jde-projects.com",
        "https://user@jde-projects.com",
        "https://jde-projects.com:8443",
        "",
        None,
    ],
)
def test_open_url_refuses_other_addresses(monkeypatch, url):
    opened = []
    logs = []
    monkeypatch.setattr(webbrowser, "open", lambda value: opened.append(value))

    result = open_url(logs.append, url)

    assert result == {"ok": False}
    assert opened == []
    assert logs == ["open_url refused: only https://jde-projects.com links are allowed"]
