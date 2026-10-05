import json
import os
import re
import threading
from datetime import datetime


# Apps copy this file in as app/debug_log.py. It only writes and prunes log files;
# the app keeps its own redaction function (passed in as `redact`) so secrets
# never have to be taught to this shared file.
class DebugLog:
    # Full file name only: Debug_Log_MMDDYYYY_HHMMSS.txt, with an optional
    # _2, _3, ... suffix for a same-second name clash. Matched strictly so
    # pruning never touches a file it didn't create.
    _PATTERN = re.compile(r"^Debug_Log_(\d{8})_(\d{6})(?:_(\d+))?\.txt$")

    def __init__(self, log_dir, app_title, redact=None, on_warning=None,
                 max_bytes=5 * 1024 * 1024, keep_older=3):
        self.log_dir = log_dir
        self.app_title = app_title
        self._redact_fn = redact if redact is not None else (lambda text: text)
        self.on_warning = on_warning
        self.max_bytes = max_bytes
        self.keep_older = keep_older

        self._on = False
        self._path = None
        self._current_size = 0
        self._lock = threading.Lock()
        # File names we've already warned about once this session, so a
        # locked file that can't be deleted doesn't nag on every prune.
        self._warned_names = set()

    def _redact(self, text):
        return self._redact_fn(text)

    def _warn(self, message):
        # A broken warning callback must never stop the app or the log write
        # that triggered it, so any failure here is swallowed.
        if self.on_warning is None:
            return
        try:
            self.on_warning(message)
        except Exception:
            pass

    def _emit(self, warnings):
        # Every warning reaches the app's UI. While logging is on it is also
        # written into the active log, so the file keeps a trace of it. A write
        # failure turns logging off first, so this never loops.
        for message in warnings:
            self._warn(message)
            if self._on:
                self.log("WARNING", message)

    def is_enabled(self):
        return self._on

    def set_enabled(self, on):
        on = bool(on)
        warnings = []
        ok = True
        with self._lock:
            if on and not self._path:
                new_path = self._next_stamped_path()
                # Prune first, counting the file we're about to create as the
                # active one (it doesn't exist yet, so it's naturally left alone).
                warnings.extend(self._prune_locked(new_path))
                size = self._write_new_file(new_path)
                if size is None:
                    warnings.append(
                        f"Debug log: could not create {os.path.basename(new_path)}. Logging stayed off."
                    )
                    self._on = False
                    ok = False
                else:
                    self._path = new_path
                    self._current_size = size
                    self._on = True
            else:
                self._on = on
        self._emit(warnings)
        return ok

    def prune(self):
        with self._lock:
            warnings = self._prune_locked(self._path)
        self._emit(warnings)

    def log(self, label, content=""):
        warnings = []
        with self._lock:
            if self._on and self._path:
                ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
                entry_text = self._format_entry(ts, label, content)
                entry_bytes = entry_text.encode("utf-8")
                available = self.max_bytes - self._current_size
                fatal = False

                if len(entry_bytes) > available:
                    # Doesn't fit in the active file: roll over to a new one.
                    # The new file is created before pruning, so a failed
                    # rollover never deletes the file that was just filled.
                    new_path = self._next_stamped_path()
                    size = self._write_new_file(new_path)
                    if size is None:
                        # Clear the path so turning logging back on starts a
                        # fresh file instead of reusing the old one.
                        self._on = False
                        self._path = None
                        fatal = True
                        warnings.append(
                            f"Debug log: could not start a new log file ({os.path.basename(new_path)}). Logging turned off."
                        )
                    else:
                        self._path = new_path
                        self._current_size = size
                        warnings.extend(self._prune_locked(new_path))
                        available = self.max_bytes - self._current_size
                        if len(entry_bytes) > available:
                            # Even a fresh file can't hold this entry: cut it
                            # to fit so the file can never exceed the cap.
                            entry_text = self._truncate_entry(ts, label, content, available)
                            entry_bytes = entry_text.encode("utf-8")

                if not fatal:
                    try:
                        with open(self._path, "a", encoding="utf-8", newline="") as f:
                            f.write(entry_text)
                        self._current_size += len(entry_bytes)
                    except OSError as e:
                        name = os.path.basename(self._path)
                        self._on = False
                        self._path = None
                        warnings.append(f"Debug log: write failed for {name} ({e.strerror or e}). Logging turned off.")
        self._emit(warnings)

    def _format_core(self, ts, label, content):
        parts = [f"[{ts}] {self._redact(str(label))}\n"]
        if content:
            if isinstance(content, (dict, list)):
                content = json.dumps(content, indent=2, default=str)
            parts.append(f"{self._redact(str(content))}\n")
        return "".join(parts)

    def _format_entry(self, ts, label, content):
        return self._format_core(ts, label, content) + "\n"

    def _truncate_entry(self, ts, label, content, budget):
        core = self._format_core(ts, label, content)
        marker = "[entry truncated]\n"
        trailer = marker + "\n"
        trailer_bytes = trailer.encode("utf-8")
        core_budget = max(0, budget - len(trailer_bytes))
        # Slice on bytes, then decode with errors ignored so a multi-byte
        # character split by the cut is dropped rather than left broken.
        core_text = core.encode("utf-8")[:core_budget].decode("utf-8", errors="ignore")
        return core_text + trailer

    def _next_stamped_path(self):
        stamp = datetime.now().strftime("%m%d%Y_%H%M%S")
        path = os.path.join(self.log_dir, f"Debug_Log_{stamp}.txt")
        if not os.path.exists(path):
            return path
        n = 2
        while True:
            path = os.path.join(self.log_dir, f"Debug_Log_{stamp}_{n}.txt")
            if not os.path.exists(path):
                return path
            n += 1

    def _write_new_file(self, path):
        header = (
            f"=== {self.app_title} debug log ===\n"
            f"Started: {datetime.now().isoformat()}\n" + "=" * 60 + "\n\n"
        )
        # "x" creates the file only if nothing exists at that name, so a link
        # swapped in after the name was picked is refused, not followed.
        try:
            with open(path, "x", encoding="utf-8", newline="") as f:
                f.write(header)
            return len(header.encode("utf-8"))
        except OSError:
            return None

    def _prune_locked(self, active_path):
        try:
            entries = list(os.scandir(self.log_dir))
        except OSError as e:
            return [f"Debug log: could not check {self.log_dir} for old logs ({e.strerror or e})."]

        active_norm = os.path.normcase(os.path.abspath(active_path)) if active_path else None

        matched = []
        for entry in entries:
            try:
                if entry.is_symlink():
                    continue
                if not entry.is_file(follow_symlinks=False):
                    continue
            except OSError:
                continue

            m = self._PATTERN.match(entry.name)
            if not m:
                continue
            datepart, timepart, suffix = m.groups()
            try:
                dt = datetime.strptime(datepart + timepart, "%m%d%Y%H%M%S")
            except ValueError:
                continue
            try:
                size = entry.stat(follow_symlinks=False).st_size
            except OSError:
                continue

            if active_norm is not None and os.path.normcase(os.path.abspath(entry.path)) == active_norm:
                continue

            matched.append((dt, int(suffix) if suffix else 0, entry.path, size))

        # Newest first, ordered by the date in the name, never the name as text.
        matched.sort(key=lambda item: (item[0], item[1]), reverse=True)

        ceiling = self.keep_older * self.max_bytes
        kept_count = 0
        kept_size = 0
        to_delete = []
        for dt, n, path, size in matched:
            if kept_count < self.keep_older and kept_size + size <= ceiling:
                kept_count += 1
                kept_size += size
            else:
                to_delete.append((dt, n, path))

        # Delete oldest first.
        to_delete.sort(key=lambda item: (item[0], item[1]))

        warnings = []
        for _dt, _n, path in to_delete:
            try:
                os.remove(path)
            except OSError:
                name = os.path.basename(path)
                if name not in self._warned_names:
                    self._warned_names.add(name)
                    warnings.append(
                        f"Debug log: could not delete old log {name} (it may be open in another program). Will retry later."
                    )
        return warnings
