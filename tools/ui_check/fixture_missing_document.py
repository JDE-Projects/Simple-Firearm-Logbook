"""ui_drive fixture for the missing-document scenario. Builds a logbook in the
copied app folder with one firearm and one document whose name contains an
ampersand, then deletes the document's stored file so opening it takes the
missing-file path. Writes only inside UI_DRIVE_APP_DIR and UI_DRIVE_OUT_DIR."""
import json
import os
import sys
import time

app_dir = os.environ["UI_DRIVE_APP_DIR"]
out_dir = os.environ["UI_DRIVE_OUT_DIR"]
sys.path.insert(0, app_dir)

from sfl import config, paths  # noqa: E402
from sfl.api import Api  # noqa: E402
from sfl.db import open_db  # noqa: E402

# The fixture runs from tools\ui_check, so point the app's folder lookup at
# the copied app, where the app itself will look.
paths.app_dir = lambda: app_dir

source = os.path.join(out_dir, "Bill of sale & receipt.txt")
with open(source, "w", encoding="utf-8") as f:
    f.write("ui check document\n")

conn = open_db(os.path.join(app_dir, config.DB_FILENAME))
api = Api()
api.set_conn(conn)
firearm_id = api.create_firearm("Ruger", "10/22", serial_number="DOCCHECK")["firearm_id"]
added = api.add_attachments(firearm_id, [source])
attachment = added["attachments"][0]
os.remove(os.path.join(app_dir, attachment["filename"]))
conn.close()

print(json.dumps({
    "firearm_id": firearm_id,
    "attachment_id": attachment["id"],
    "label": attachment["label"],
}), flush=True)
while True:
    time.sleep(60)
