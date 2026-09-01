"""Translate a check_update failure into a short, plain-language reason to
show in the UI."""
import errno
import json
import socket
import ssl
import urllib.error


def _update_error_reason(exc: BaseException) -> str:
    """Turn a check_update exception into a short, plain-language reason to
    show in the UI. Pure and network-free: takes the already-raised exception,
    never touches the network itself.

    Each branch is specific to a failure that can actually cause it, and
    names a next step where there is a sensible one. Subclasses are checked
    before their parents: SSLCertVerificationError and SSLEOFError/
    SSLZeroReturnError before the generic ssl.SSLError, and the specific
    ConnectionError subclasses and socket.gaierror before the generic OSError
    branch (socket.timeout is an alias of TimeoutError, and both are OSError
    subclasses)."""
    # HTTPError is a URLError subclass but carries its own .code, so classify
    # it before unwrapping anything.
    if isinstance(exc, urllib.error.HTTPError):
        if exc.code == 403:
            return (
                "GitHub is rate-limiting update checks from this network. "
                "Try again later."
            )
        if exc.code == 404:
            return "No published release was found."
        if 500 <= exc.code < 600:
            return f"GitHub is having trouble on its end (HTTP {exc.code})."
        return f"GitHub returned an error (HTTP {exc.code})."

    if isinstance(exc, json.JSONDecodeError):
        return (
            "GitHub returned something unexpected. This often means a proxy "
            "or a guest wifi sign-in page answered instead."
        )

    # A plain URLError wraps the underlying cause (ssl.SSLError, socket.timeout,
    # a DNS/socket OSError, ...) in its .reason; unwrap it to classify the
    # actual cause, but remember it came from a URLError for the fallback below.
    is_url_error = isinstance(exc, urllib.error.URLError)
    cause = exc.reason if is_url_error and exc.reason is not None else exc

    if isinstance(cause, ssl.SSLCertVerificationError):
        return (
            "GitHub's certificate could not be verified. This usually means "
            "antivirus or a network filter is inspecting HTTPS traffic."
        )
    if isinstance(cause, (ssl.SSLEOFError, ssl.SSLZeroReturnError)):
        return "The secure connection was cut off during the handshake with GitHub."
    if isinstance(cause, ssl.SSLError):
        return "The secure connection to GitHub failed."
    if isinstance(cause, socket.gaierror):
        return (
            "The address for api.github.com could not be looked up. Check "
            "DNS or the internet connection."
        )
    if isinstance(cause, (socket.timeout, TimeoutError)):
        return "GitHub didn't respond in time."
    if isinstance(cause, (ConnectionRefusedError, ConnectionResetError)):
        return (
            "The connection was refused or reset. A firewall or proxy may "
            "be blocking it."
        )
    if isinstance(cause, OSError) and getattr(cause, "errno", None) == errno.ENETUNREACH:
        return "No network connection."
    if is_url_error:
        return "Couldn't reach GitHub. Check the internet connection."

    text = f"{type(exc).__name__}: {exc}"
    if len(text) > 120:
        text = text[:117] + "..."
    return text
