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
        "sub_type": "",
        "acquisition_date": "2024-01-15",
        "acquired_from": "Example Sporting Goods",
        "purchase_price": "",
        "estimated_value": "",
        "insured_value": "",
        "storage_location": "",
        "notes": "",
        "held_in_trust": 0,
        "trust_name": "",
        "is_nfa": 0,
        "nfa_form_type": "",
        "nfa_stamp_date": "",
        "disposition_status": "Owned",
        "disposition_date": "",
        "disposition_to": "",
        "disposition_address": "",
        "disposition_amount": "",
        "disposition_notes": "",
        "photo_count": 0,
        "primary_photo_filename": None,
        "attachment_count": 0,
        "attachment_bytes": 0,
    }
    record.update(overrides)
    return record


# A believable small collection that exercises the list's badges and columns:
# a trust-held revolver, a trust-held NFA short-barreled rifle (Trust + NFA
# badges stack), a sold shotgun, and a few with attached documents so the Docs
# column is populated. All invented; never real serials or personal data.
FIREARMS = [
    _firearm(
        1, "Ruger", "10/22 Carbine", ".22 LR", "Rifle", "359-21084",
        attachment_count=1, attachment_bytes=214_000,
    ),
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
    _firearm(
        4, "Smith & Wesson", "Model 686", ".357 Magnum", "Revolver", "AKR5590",
        held_in_trust=1, trust_name="Smith Family Revocable Trust",
        attachment_count=2, attachment_bytes=1_680_000,
    ),
    _firearm(5, "Savage Arms", "Axis II", ".308 Winchester", "Rifle", "J348820"),
    _firearm(
        6, "BCM", "RECCE-11 SBR", "5.56 NATO", "Rifle", "BCM11-0447",
        sub_type="Short-Barreled Rifle",
        held_in_trust=1, trust_name="Smith Family Revocable Trust",
        is_nfa=1, nfa_form_type="Form 1", nfa_stamp_date="2024-06-12",
        attachment_count=1, attachment_bytes=530_000,
    ),
    _firearm(7, "Sig Sauer", "P365", "9mm Luger", "Pistol", "66A112233"),
]
