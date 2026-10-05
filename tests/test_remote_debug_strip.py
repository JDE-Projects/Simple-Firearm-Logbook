from sfl.remote_debug import strip_remote_debugging


def test_frozen_removes_remote_debugging_environment_and_keeps_other_flags():
    environ = {
        "QTWEBENGINE_REMOTE_DEBUGGING": "9222",
        "QTWEBENGINE_CHROMIUM_FLAGS": (
            '--disable-gpu --user-agent="Simple SFTP Test" '
            '--remote-debugging-port=9222 --remote-debugging-port 9223 --enable-logging'
        ),
    }
    argv = ["simple_sftp_client.exe"]

    strip_remote_debugging(environ, argv, frozen=True)

    assert "QTWEBENGINE_REMOTE_DEBUGGING" not in environ
    assert environ["QTWEBENGINE_CHROMIUM_FLAGS"] == (
        '--disable-gpu --user-agent="Simple SFTP Test" --enable-logging'
    )


def test_frozen_deletes_flags_variable_when_only_remote_debugging_switches_remain():
    environ = {
        "QTWEBENGINE_CHROMIUM_FLAGS": "--remote-debugging-port=9222 --remote-debugging-port 9223",
    }
    argv = ["simple_sftp_client.exe"]

    strip_remote_debugging(environ, argv, frozen=True)

    assert "QTWEBENGINE_CHROMIUM_FLAGS" not in environ


def test_frozen_removes_remote_debugging_pipe():
    environ = {"QTWEBENGINE_CHROMIUM_FLAGS": "--remote-debugging-pipe --disable-gpu"}
    argv = ["simple_sftp_client.exe"]

    strip_remote_debugging(environ, argv, frozen=True)

    assert environ["QTWEBENGINE_CHROMIUM_FLAGS"] == "--disable-gpu"


def test_frozen_removes_both_argv_forms_without_touching_program_or_other_arguments():
    environ = {}
    argv = [
        "simple_sftp_client.exe", "--profile", "default",
        "--remote-debugging-port=9222", "--remote-debugging-address", "127.0.0.1",
        "--disable-gpu",
    ]

    strip_remote_debugging(environ, argv, frozen=True)

    assert argv == ["simple_sftp_client.exe", "--profile", "default", "--disable-gpu"]


def test_not_frozen_leaves_environment_and_argv_unchanged():
    environ = {
        "QTWEBENGINE_REMOTE_DEBUGGING": "9222",
        "QTWEBENGINE_CHROMIUM_FLAGS": (
            '--disable-gpu --user-agent="Simple SFTP Test" '
            '--remote-debugging-port=9222 --remote-debugging-port 9223 --remote-debugging-pipe'
        ),
    }
    argv = [
        "simple_sftp_client.py", "--remote-debugging-port=9222",
        "--remote-debugging-address", "127.0.0.1", "--disable-gpu",
    ]
    original_environ = environ.copy()
    original_argv = argv.copy()

    strip_remote_debugging(environ, argv, frozen=False)

    assert environ == original_environ
    assert argv == original_argv


def test_frozen_removes_every_windows_switch_spelling():
    environ = {
        "QTWEBENGINE_CHROMIUM_FLAGS": (
            "-remote-debugging-port=9222 /remote-debugging-port=9223 "
            "--REMOTE-DEBUGGING-PORT=9224 --disable-gpu"
        ),
    }
    argv = ["simple_sftp_client.exe", "-Remote-Debugging-Port", "9225", "/remote-debugging-pipe"]

    strip_remote_debugging(environ, argv, frozen=True)

    assert environ["QTWEBENGINE_CHROMIUM_FLAGS"] == "--disable-gpu"
    assert argv == ["simple_sftp_client.exe"]


def test_frozen_unbalanced_quote_does_not_raise():
    environ = {
        "QTWEBENGINE_CHROMIUM_FLAGS": '--user-agent="broken --remote-debugging-port=9222 --disable-gpu',
    }
    argv = ["simple_sftp_client.exe"]

    strip_remote_debugging(environ, argv, frozen=True)

    assert environ["QTWEBENGINE_CHROMIUM_FLAGS"] == '--user-agent="broken --disable-gpu'
