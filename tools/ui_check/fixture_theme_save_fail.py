"""ui_drive fixture for the theme-save-fail scenario. Writes a settings file
with the dark theme, then keeps it open. Python opens files on Windows
without delete sharing, so the app can still read the file but cannot swap a
new one into its place, and every settings save fails. The handle closes when
drive.py ends the run. Writes only inside UI_DRIVE_APP_DIR."""
import json
import os
import time

app_dir = os.environ["UI_DRIVE_APP_DIR"]
path = os.path.join(app_dir, "simple_firearm_logbook.pref")
with open(path, "w", encoding="utf-8") as f:
    json.dump({"theme": "dark"}, f)

held = open(path, "rb")
print(json.dumps({"pref_held": True}), flush=True)
while True:
    time.sleep(60)
