"""ui_drive fixture for the debug-log scenario. Places five older debug logs
and one unrelated file next to the copied app, then holds the oldest log open
so the app cannot delete it and has to warn. The handle closes when drive.py
ends the run. Writes only inside UI_DRIVE_APP_DIR."""
import json
import os
import time

app_dir = os.environ["UI_DRIVE_APP_DIR"]

stamps = ["01012026_100000", "01022026_100000", "01032026_100000",
          "01042026_100000", "01052026_100000"]
names = [f"Debug_Log_{stamp}.txt" for stamp in stamps]
for name in names:
    with open(os.path.join(app_dir, name), "w", encoding="utf-8") as f:
        f.write("old log\n")
with open(os.path.join(app_dir, "notes.txt"), "w", encoding="utf-8") as f:
    f.write("not a debug log\n")

# An open handle without delete sharing makes os.remove fail on Windows.
held = open(os.path.join(app_dir, names[0]), "rb")

print(json.dumps({"logs": names, "locked": names[0], "other": "notes.txt"}), flush=True)
while True:
    time.sleep(60)
