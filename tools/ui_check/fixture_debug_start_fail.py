"""ui_drive fixture for the debug-start-fail scenario. Places a broken
junction at every debug log name the app could pick in the next four
minutes. A broken junction does not count as existing, so the app picks that
name, but the file cannot be created there, so turning on the debug log
fails. Writes only inside UI_DRIVE_APP_DIR."""
import _winapi
import json
import os
import time
from datetime import datetime, timedelta

app_dir = os.environ["UI_DRIVE_APP_DIR"]

target = os.path.join(app_dir, "missing-junction-target")
os.mkdir(target)
start = datetime.now()
names = []
for second in range(240):
    stamp = (start + timedelta(seconds=second)).strftime("%m%d%Y_%H%M%S")
    name = f"Debug_Log_{stamp}.txt"
    _winapi.CreateJunction(target, os.path.join(app_dir, name))
    names.append(name)
# Removing the target leaves every junction pointing at nothing.
os.rmdir(target)

print(json.dumps({"junctions": len(names), "first": names[0], "last": names[-1]}), flush=True)
while True:
    time.sleep(60)
