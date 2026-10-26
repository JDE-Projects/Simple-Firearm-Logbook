"""ui_drive fixture for the photo-drop scenario. Builds one firearm without
photos in the copied app folder, then supplies JPEG, PNG, and ICO bytes for a
real browser drop. Writes only inside UI_DRIVE_APP_DIR and UI_DRIVE_OUT_DIR."""
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


def _image_bytes(image_format, color):
    buf = io.BytesIO()
    Image.new("RGB", (64, 48), color).save(buf, image_format)
    return base64.b64encode(buf.getvalue()).decode("ascii")


conn = open_db(os.path.join(app_dir, config.DB_FILENAME))
api = Api()
api.set_conn(conn)
created = api.create_firearm("Glock", "19", serial_number="PHOTODROP1")
conn.close()

print(json.dumps({
    "firearm_id": created["firearm_id"],
    "front_jpeg": _image_bytes("JPEG", (200, 60, 60)),
    "side_png": _image_bytes("PNG", (60, 200, 60)),
    "disguised_ico": _image_bytes("ICO", (60, 60, 200)),
}), flush=True)
while True:
    time.sleep(60)
