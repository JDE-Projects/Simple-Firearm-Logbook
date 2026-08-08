#!/usr/bin/env python3
"""The invented firearm collection shown in the README screenshot.

None of this is real inventory or personal data. The version shown in the
image always comes from simple_firearm_logbook.py, never from this fixture.
"""


def _firearm(
    firearm_id: int,
    make: str,
    model: str,
    caliber: str,
    firearm_type: str,
    serial_number: str,
    **overrides,
) -> dict:
    record = {
        "id": firearm_id,
        "log_number": f"{firearm_id:05d}",
        "make": make,
        "model": model,
        "serial_number": serial_number,
        "firearm_type": firearm_type,
        "caliber": caliber,
        "acquisition_date": "2024-01-15",
        "acquired_from": "Example Sporting Goods",
        "purchase_price": "",
        "estimated_value": "",
        "notes": "",
        "disposition_status": "Owned",
        "disposition_date": "",
        "disposition_to": "",
        "disposition_address": "",
        "disposition_amount": "",
        "disposition_notes": "",
        "photo_count": 0,
        "primary_photo_filename": None,
    }
    record.update(overrides)
    return record


FIREARMS = [
    _firearm(1, "Ruger", "10/22 Carbine", ".22 LR", "Rifle", "359-21084"),
    _firearm(2, "Glock", "19 Gen 5", "9mm Luger", "Pistol", "BSAT422"),
    _firearm(
        3,
        "Remington",
        "870 Express",
        "12 Gauge",
        "Shotgun",
        "RS482910",
        disposition_status="Sold",
        disposition_date="2025-11-08",
        disposition_to="Sample Buyer",
        disposition_amount="425.00",
    ),
    _firearm(4, "Smith & Wesson", "Model 686", ".357 Magnum", "Revolver", "AKR5590"),
    _firearm(5, "Savage Arms", "Axis II", ".308 Winchester", "Rifle", "J348820"),
    _firearm(6, "Mossberg", "500 Field", "20 Gauge", "Shotgun", "P114472"),
    _firearm(7, "Sig Sauer", "P365", "9mm Luger", "Pistol", "66A112233"),
]
