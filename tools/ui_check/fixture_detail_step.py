"""ui_drive fixture for the detail-step scenario.

Builds five firearms in the copied app folder: four owned, one Sold, and one
NFA record. Writes only inside UI_DRIVE_APP_DIR and UI_DRIVE_OUT_DIR.
"""
import json
import os
import sys
import time

app_dir = os.environ["UI_DRIVE_APP_DIR"]
os.environ["UI_DRIVE_OUT_DIR"]  # The fixture's permitted output location.
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

records = [
    ("beretta", "Beretta", "92FS", {}),
    ("colt", "Colt", "Python", {"is_nfa": 1, "nfa_form_type": "Form 4"}),
    ("glock", "Glock", "19", {}),
    ("ruger", "Ruger", "10/22", {}),
    ("sig", "Sig Sauer", "P320", {}),
]
ids = {}
for key, make, model, extra in records:
    created = api.create_firearm(make, model, serial_number=f"DETAIL-{key.upper()}", **extra)
    if not created.get("ok"):
        raise RuntimeError(f"Couldn't create {key}: {created}")
    ids[key] = created["firearm_id"]

sold = api.update_disposition(ids["ruger"], "Sold", date="2025-01-01", to="UI check buyer")
if not sold.get("ok"):
    raise RuntimeError(f"Couldn't mark Ruger as Sold: {sold}")
conn.close()

print(json.dumps({"ids": ids}), flush=True)
while True:
    time.sleep(60)
