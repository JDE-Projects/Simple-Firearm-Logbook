"""ui_drive fixture for the smoke scenario. Builds a logbook in the copied app
folder with one firearm, two photos, and two documents, then holds one photo
file and one document file open so the app cannot remove them from disk. The
handles close when drive.py ends the run. Writes only inside UI_DRIVE_APP_DIR
and UI_DRIVE_OUT_DIR."""
import base64
import io
import json
import os
import sys
import time

app_dir = os.environ["UI_DRIVE_APP_DIR"]
out_dir = os.environ["UI_DRIVE_OUT_DIR"]
sys.path.insert(0, app_dir)

from PIL import Image  # noqa: E402

from sfl import config, paths  # noqa: E402
from sfl.api import Api  # noqa: E402
from sfl.db import open_db  # noqa: E402

# The fixture runs from tools\ui_check, so point the app's folder lookup at
# the copied app, where the app itself will look.
paths.app_dir = lambda: app_dir


def _jpeg(color):
    buf = io.BytesIO()
    Image.new("RGB", (64, 48), color).save(buf, "JPEG")
    return base64.b64encode(buf.getvalue()).decode("ascii")


conn = open_db(os.path.join(app_dir, config.DB_FILENAME))
api = Api()
api.set_conn(conn)

created = api.create_firearm("Glock", "19", serial_number="UICHECK1")
firearm_id = created["firearm_id"]

photos = api.add_photos_from_data(firearm_id, [
    {"name": "free.jpg", "data": _jpeg((200, 60, 60))},
    {"name": "locked.jpg", "data": _jpeg((60, 60, 200))},
])["photos"]

doc_sources = []
for name in ("free-receipt.txt", "locked-receipt.txt"):
    src = os.path.join(out_dir, name)
    with open(src, "w", encoding="utf-8") as f:
        f.write("ui check document\n")
    doc_sources.append(src)
api.add_attachments(firearm_id, doc_sources)
attachments = api.get_firearm(firearm_id)["attachments"]
conn.close()

# An open handle without delete sharing makes os.remove fail on Windows.
held = [
    open(paths._safe_photo_path(photos[1]["filename"]), "rb"),
    open(paths._safe_attachment_path(attachments[1]["filename"]), "rb"),
]

print(json.dumps({
    "firearm_id": firearm_id,
    "photo_free": photos[0]["id"],
    "photo_locked": photos[1]["id"],
    "doc_free": attachments[0]["id"],
    "doc_locked": attachments[1]["id"],
}), flush=True)
while True:
    time.sleep(60)
