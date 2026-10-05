"""ui_drive fixture for the window-fit scenario. Saves a window position far
larger than any screen in the copied app's prefs file, so the app has to fit
it to the screen on launch. Writes only inside UI_DRIVE_APP_DIR."""
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

# The title bar point the app checks (x + 100, y + 30) stays on the main
# screen; the width and height are far past any real screen.
window = {"x": 200, "y": 150, "width": 6000, "height": 4000}
saved = prefs.save_prefs({"window": window})

print(json.dumps({"saved": saved, "window": window}), flush=True)
while True:
    time.sleep(60)
