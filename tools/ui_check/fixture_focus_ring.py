"""ui_drive fixture for the focus-ring scenario. Builds a logbook in the copied
app folder with enough firearms that the log view scrolls, which is what
makes it a Tab stop. Writes only inside UI_DRIVE_APP_DIR."""
import json
import os
import sys

app_dir = os.environ["UI_DRIVE_APP_DIR"]
sys.path.insert(0, app_dir)

from sfl import config, paths  # noqa: E402
from sfl.api import Api  # noqa: E402
from sfl.db import open_db  # noqa: E402

# The fixture runs from tools\ui_check, so point the app's folder lookup at
# the copied app, where the app itself will look.
paths.app_dir = lambda: app_dir

conn = open_db(os.path.join(app_dir, config.DB_FILENAME))
api = Api()
api.set_conn(conn)
for number in range(1, 41):
    api.create_firearm("Make", f"Model {number}", serial_number=f"FOCUS{number:02d}")
conn.close()

print(json.dumps({"firearms": 40}), flush=True)
