"""Tests for _update_error_reason: mapping a check_update exception to a
short, plain-language reason for the UI. Pure function, no network calls;
exceptions are constructed by hand to simulate what urllib/ssl/socket/json
would raise.
"""
import errno
import json
import socket
import ssl
import urllib.error

import pytest

from simple_firearm_logbook import _update_error_reason


def test_ssl_cert_verification_error_wrapped_in_url_error():
    cause = ssl.SSLCertVerificationError("certificate verify failed")
    exc = urllib.error.URLError(cause)
    reason = _update_error_reason(exc)
    assert "certificate could not be verified" in reason


def test_ssl_eof_error_wrapped_in_url_error():
    cause = ssl.SSLEOFError("EOF occurred in violation of protocol")
    exc = urllib.error.URLError(cause)
    reason = _update_error_reason(exc)
    assert "cut off during the handshake" in reason


def test_ssl_zero_return_error_wrapped_in_url_error():
    cause = ssl.SSLZeroReturnError("TLS/SSL connection has been closed")
    exc = urllib.error.URLError(cause)
    reason = _update_error_reason(exc)
    assert "cut off during the handshake" in reason


def test_generic_ssl_error_wrapped_in_url_error():
    cause = ssl.SSLError("decryption failed or bad record mac")
    exc = urllib.error.URLError(cause)
    reason = _update_error_reason(exc)
    assert "secure connection to GitHub failed" in reason


def test_http_error_403_reports_rate_limit():
    exc = urllib.error.HTTPError(
        "https://api.github.com/repos/x/y/releases/latest", 403, "Forbidden", {}, None
    )
    reason = _update_error_reason(exc)
    assert "rate-limiting" in reason


def test_http_error_404_reports_no_release():
    exc = urllib.error.HTTPError(
        "https://api.github.com/repos/x/y/releases/latest", 404, "Not Found", {}, None
    )
    reason = _update_error_reason(exc)
    assert "No published release was found" in reason


@pytest.mark.parametrize("code", [500, 502, 503])
def test_http_error_5xx_reports_github_trouble(code):
    exc = urllib.error.HTTPError(
        "https://api.github.com/repos/x/y/releases/latest", code, "Server Error", {}, None
    )
    reason = _update_error_reason(exc)
    assert "GitHub is having trouble on its end" in reason
    assert str(code) in reason


def test_http_error_other_status_reports_plain_code():
    exc = urllib.error.HTTPError(
        "https://api.github.com/repos/x/y/releases/latest", 418, "I'm a teapot", {}, None
    )
    reason = _update_error_reason(exc)
    assert "HTTP 418" in reason


def test_gaierror_wrapped_in_url_error():
    cause = socket.gaierror("[Errno 11001] getaddrinfo failed")
    exc = urllib.error.URLError(cause)
    reason = _update_error_reason(exc)
    assert "could not be looked up" in reason


def test_timeout_wrapped_in_url_error():
    exc = urllib.error.URLError(socket.timeout("timed out"))
    reason = _update_error_reason(exc)
    assert reason == "GitHub didn't respond in time."


def test_timeout_error_wrapped_in_url_error():
    exc = urllib.error.URLError(TimeoutError("timed out"))
    reason = _update_error_reason(exc)
    assert reason == "GitHub didn't respond in time."


def test_connection_refused_wrapped_in_url_error():
    cause = ConnectionRefusedError("[Errno 111] Connection refused")
    exc = urllib.error.URLError(cause)
    reason = _update_error_reason(exc)
    assert "firewall or proxy" in reason


def test_connection_reset_wrapped_in_url_error():
    cause = ConnectionResetError("[Errno 104] Connection reset by peer")
    exc = urllib.error.URLError(cause)
    reason = _update_error_reason(exc)
    assert "firewall or proxy" in reason


def test_network_unreachable_wrapped_in_url_error():
    cause = OSError(errno.ENETUNREACH, "Network is unreachable")
    exc = urllib.error.URLError(cause)
    reason = _update_error_reason(exc)
    assert reason == "No network connection."


def test_plain_url_error_wrapping_socket_error():
    cause = OSError(errno.EACCES, "Permission denied")
    exc = urllib.error.URLError(cause)
    reason = _update_error_reason(exc)
    assert reason == "Couldn't reach GitHub. Check the internet connection."


def test_json_decode_error():
    try:
        json.loads("<html>not json</html>")
    except json.JSONDecodeError as e:
        reason = _update_error_reason(e)
    assert "guest wifi sign-in page" in reason


def test_unknown_exception_falls_back_to_class_and_message():
    exc = ValueError("something unexpected")
    reason = _update_error_reason(exc)
    assert reason == "ValueError: something unexpected"


def test_unknown_exception_message_truncated():
    exc = ValueError("x" * 200)
    reason = _update_error_reason(exc)
    assert len(reason) <= 120
    assert reason.endswith("...")
