"""Sample PostNord API payloads shared by the test modules.

These are the per-shipment dicts ``PostNordApiClient.async_get_parcel`` returns
(i.e. ``TrackingInformationResponse.shipments[0]``), shaped after a real,
redacted PostNord response captured 2026-07-30 — the ``recipientview`` and
``findByIdentifier`` endpoints share the same envelope. Kept in one module so a
payload-shape fix has exactly one home.
"""
from __future__ import annotations

ACTIVE_CODE = "00000000000000001"
DELIVERED_CODE = "00000000000000002"


def event(status: str, event_time: str, description: str, code: str = "00") -> dict:
    """One entry of a shipment item's event timeline.

    PostNord events carry a machine ``status`` (same enum as the shipment), an
    ISO ``eventTime``, a numeric ``eventCode`` and human ``eventDescription``.
    """
    return {
        "status": status,
        "eventTime": event_time,
        "eventCode": code,
        "eventDescription": description,
    }


def delivered_sample(code: str = DELIVERED_CODE) -> dict:
    """A representative tracking response for a delivered parcel."""
    return {
        "shipmentId": code,
        "assessedNumberOfItems": 1,
        "service": {"code": "18", "name": "PostNord Parcel", "issuerCode": "Z11"},
        "consignor": {"name": "Example Shop", "address": {"countryCode": "SE"}},
        "consignee": {
            "name": "Jane Doe",
            "address": {"city": "Stockholm", "countryCode": "SE", "postCode": "111 22"},
        },
        "status": "DELIVERED",
        "statusText": {
            "header": "Delivered",
            "body": "The shipment has been delivered.",
        },
        "estimatedTimeOfArrival": None,
        "totalWeight": {"value": "1.25", "unit": "kg"},
        "items": [
            {
                "itemId": code,
                "status": "DELIVERED",
                "events": [
                    event("DELIVERED", "2026-04-29T13:12:42Z", "Delivered", "40"),
                    event("DELIVERY", "2026-04-29T08:46:00Z", "Out for delivery", "30"),
                    event("EN_ROUTE", "2026-04-28T15:52:17Z", "At the terminal", "20"),
                    event("INFORMED", "2026-04-27T23:03:58Z", "Shipment announced", "68"),
                ],
            }
        ],
    }


def active_sample(code: str = ACTIVE_CODE) -> dict:
    """An out-for-delivery parcel with an ETA."""
    sample = delivered_sample(code)
    sample.update(
        {
            "status": "DELIVERY",
            "statusText": {"header": "Out for delivery", "body": "On the vehicle."},
            "estimatedTimeOfArrival": "2026-04-29T13:00:00Z",
        }
    )
    sample["items"][0]["status"] = "DELIVERY"
    sample["items"][0]["events"] = sample["items"][0]["events"][1:]
    return sample


def pickup_sample(code: str = ACTIVE_CODE) -> dict:
    """A parcel waiting at a pickup point."""
    sample = active_sample(code)
    sample.update(
        {
            "status": "AVAILABLE_FOR_DELIVERY",
            "statusText": {"header": "Ready for collection", "body": "Collect it."},
            "estimatedTimeOfArrival": None,
            "deliveryPoint": {"name": "Example Point Central Station"},
        }
    )
    sample["items"][0]["status"] = "AVAILABLE_FOR_DELIVERY"
    return sample
