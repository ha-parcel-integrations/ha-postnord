"""Synthetic PostNord account-inbox payloads.

Shaped after ``jesmak/postnord_tracking``'s reading of the receiver-app
``customer/v2/receiverapp/tracking`` response — the same shipment object the
public tracker returns. **Synthetic, not captured**: replace with a scrubbed
real account response once one is available.
"""
from __future__ import annotations

from ..payloads import delivered_sample, event

ACCOUNT_ACTIVE_CODE = "00000000000000011"
ACCOUNT_DELIVERED_CODE = "00000000000000012"


def account_active(code: str = ACCOUNT_ACTIVE_CODE) -> dict:
    """An account parcel on its way."""
    sample = delivered_sample(code)
    sample["status"] = "EN_ROUTE"
    sample["statusText"] = {"header": "On its way", "body": "In the network."}
    sample["userData"] = {"dateAdded": "2026-04-27T23:10:00Z"}
    sample["items"][0]["status"] = "EN_ROUTE"
    sample["items"][0]["events"] = [
        event("EN_ROUTE", "2026-04-28T15:52:17Z", "At the terminal", "20"),
        event("INFORMED", "2026-04-27T23:03:58Z", "Shipment announced", "68"),
    ]
    return sample


def account_delivered(code: str = ACCOUNT_DELIVERED_CODE) -> dict:
    """An archived, delivered account parcel."""
    sample = delivered_sample(code)
    sample["userData"] = {"dateAdded": "2026-04-27T23:10:00Z"}
    return sample


def tracking_body(active=None, archived=None) -> dict:
    """The whole account tracking response."""
    return {
        "activeShipments": [account_active()] if active is None else active,
        "archivedShipments": [account_delivered()] if archived is None else archived,
        "deletedShipments": [account_active("00000000000000099")],
    }
