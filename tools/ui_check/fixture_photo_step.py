"""ui_drive fixture for the photo-step scenario.

Builds one firearm with five ordered JPEGs, removes the fourth photo's stored
file, and builds a second firearm with one photo. Writes only inside
UI_DRIVE_APP_DIR and UI_DRIVE_OUT_DIR.
"""
import base64
import io
import json
import os
import sys
import time

app_dir = os.environ["UI_DRIVE_APP_DIR"]
os.environ["UI_DRIVE_OUT_DIR"]  # The fixture's permitted output location.
sys.path.insert(0, app_dir)

from PIL import Image  # noqa: E402

from sfl import config, paths  # noqa: E402
from sfl.api import Api  # noqa: E402
from sfl.db import open_db  # noqa: E402

# The fixture runs from tools\ui_check, so point the app's folder lookup at
# the copied app, where the app itself will look.
paths.app_dir = lambda: app_dir


def _jpeg(width, height, color):
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), color).save(buffer, "JPEG")
    return base64.b64encode(buffer.getvalue()).decode("ascii")


def _add_photos(api, firearm_id, specs):
    result = api.add_photos_from_data(firearm_id, [
        {"name": f"photo-{index}.jpg", "data": _jpeg(width, height, color)}
        for index, (width, height, color) in enumerate(specs, start=1)
    ])
    if not result.get("ok"):
        raise RuntimeError(f"Couldn't add photos: {result}")
    return result["photos"]


conn = open_db(os.path.join(app_dir, config.DB_FILENAME))
api = Api()
api.set_conn(conn)

firearm_a = api.create_firearm("Photo", "Five", serial_number="PHOTOSTEP-A")
if not firearm_a.get("ok"):
    raise RuntimeError(f"Couldn't create firearm A: {firearm_a}")
widths = [200, 640, 2400, 800, 1000]
photos_a = _add_photos(api, firearm_a["firearm_id"], [
    (200, 150, (200, 60, 60)),
    (640, 480, (60, 200, 60)),
    (2400, 1600, (60, 60, 200)),
    (800, 600, (200, 200, 60)),
    (1000, 750, (200, 60, 200)),
])

# Keep the database row so the UI exercises its missing-photo error path.
os.remove(paths._safe_photo_path(photos_a[3]["filename"]))

firearm_b = api.create_firearm("Photo", "One", serial_number="PHOTOSTEP-B")
if not firearm_b.get("ok"):
    raise RuntimeError(f"Couldn't create firearm B: {firearm_b}")
_add_photos(api, firearm_b["firearm_id"], [(640, 480, (60, 200, 200))])
conn.close()

print(json.dumps({
    "firearm_a_id": firearm_a["firearm_id"],
    "firearm_a_photo_ids": [photo["id"] for photo in photos_a],
    "firearm_b_id": firearm_b["firearm_id"],
    "widths": widths,
}), flush=True)
while True:
    time.sleep(60)
