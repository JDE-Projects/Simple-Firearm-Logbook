"""ui_drive fixture for the bottom-bar scenario. Saves the app's minimum
window size (1000 x 680) in the copied app's prefs file, so the bottom bar is
checked at its narrowest. Writes only inside UI_DRIVE_APP_DIR."""
import json
import os
import sys
import time

app_dir = os.environ["UI_DRIVE_APP_DIR"]
sys.path.insert(0, app_dir)

from sfl import paths, prefs  # noqa: E402

# The fixture runs from tools\ui_check, so point the app's folder lookup at
# the copied app, where the app itself will look.
paths.app_dir = lambda: app_dir

window = {"x": 200, "y": 150, "width": 1000, "height": 680}
saved = prefs.save_prefs({"window": window})

print(json.dumps({"saved": saved, "window": window}), flush=True)
while True:
    time.sleep(60)
