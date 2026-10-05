"""ui_drive fixture for the debug-write-fail scenario. Waits for the first
debug log the app creates, then locks a large byte range of it so the app's
next write to that file fails. Only that first log is locked, so a log
started afterwards works normally. The lock ends when drive.py ends the run.
Writes only inside UI_DRIVE_APP_DIR."""
import json
import msvcrt
import os
import re
import time

app_dir = os.environ["UI_DRIVE_APP_DIR"]
pattern = re.compile(r"^Debug_Log_\d{8}_\d{6}(?:_\d+)?\.txt$")


def debug_logs():
    return {name for name in os.listdir(app_dir) if pattern.match(name)}


before = debug_logs()
print(json.dumps({"logs_before": sorted(before)}), flush=True)

held = None
while held is None:
    new = sorted(debug_logs() - before)
    if new:
        held = open(os.path.join(app_dir, new[0]), "rb")
        # A byte-range lock reaching well past the end of the file makes
        # every append from another handle fail.
        msvcrt.locking(held.fileno(), msvcrt.LK_NBLCK, 50_000_000)
    else:
        time.sleep(0.1)
while True:
    time.sleep(60)
