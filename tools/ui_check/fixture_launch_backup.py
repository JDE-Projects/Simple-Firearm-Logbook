"""Plant a valid logbook while blocking its launch-backup folder."""
import json
import os
import sys
import time

app_dir = os.environ["UI_DRIVE_APP_DIR"]
sys.path.insert(0, app_dir)

from sfl import config, paths  # noqa: E402
from sfl.db import open_db  # noqa: E402

paths.app_dir = lambda: app_dir
conn = open_db(os.path.join(app_dir, config.DB_FILENAME))
conn.execute(
    "INSERT INTO firearms (log_number, make, model, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
    ("00001", "Glock", "19", "now", "now"),
)
conn.commit()
conn.close()

with open(os.path.join(app_dir, config.BACKUPS_DIRNAME), "w", encoding="utf-8") as file:
    file.write("blocks backup folder creation")

print(json.dumps({"firearm": "Glock 19"}), flush=True)
while True:
    time.sleep(60)
