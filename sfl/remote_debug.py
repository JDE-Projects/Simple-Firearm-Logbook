# Remove Qt's remote-debugging switches from a built app at startup, so the
# exe never opens Qt's remote-control port. Call it as the first line of
# launcher.run(). Source runs are left alone because test tooling needs that
# port.
#
# Needs: import os, shlex, sys
# Call:  strip_remote_debugging(os.environ, sys.argv, getattr(sys, "frozen", False))

import shlex


def _is_remote_debugging_switch(token):
    # Chromium on Windows accepts "--", "-" or "/" before a switch name and
    # ignores its case, so every spelling is matched.
    for prefix in ("--", "-", "/"):
        if token.startswith(prefix):
            return token[len(prefix):].lower().startswith("remote-debugging-")
    return False


def _drop_remote_debugging(tokens):
    """Return tokens without remote-debugging switches, including a value
    given as the following token (--remote-debugging-port 9222)."""
    kept = []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        index += 1
        if _is_remote_debugging_switch(token):
            if "=" not in token and index < len(tokens) and not tokens[index].startswith(("-", "/")):
                index += 1
        else:
            kept.append(token)
    return kept


def strip_remote_debugging(environ, argv, frozen):
    """Remove Qt remote-debugging controls from frozen application launches,
    so the built app never opens Qt's remote-control port. Source runs are
    left alone: the real-window smoke check depends on that port."""
    if not frozen:
        return

    environ.pop("QTWEBENGINE_REMOTE_DEBUGGING", None)

    flags = environ.get("QTWEBENGINE_CHROMIUM_FLAGS")
    if flags is not None:
        try:
            lexer = shlex.shlex(flags, posix=False)
            lexer.whitespace_split = True
            tokens = list(lexer)
        except ValueError:
            # Unbalanced quote: split on spaces rather than fail at startup.
            tokens = flags.split()
        kept = _drop_remote_debugging(tokens)
        if kept:
            environ["QTWEBENGINE_CHROMIUM_FLAGS"] = " ".join(kept)
        else:
            del environ["QTWEBENGINE_CHROMIUM_FLAGS"]

    argv[1:] = _drop_remote_debugging(argv[1:])
