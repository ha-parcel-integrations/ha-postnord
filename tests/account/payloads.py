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


def captured_service_point_delivery() -> dict:
    """A real archived account shipment, 2026-09-27, scrubbed.

    Delivered to a service point. Ids, the order reference, the consignee's
    address and the service-point names are replaced; the structure, status
    codes, event codes, units and timestamps are as captured. Note what differs
    from the tracker: ``statusText`` sits on the item, the shipment only has a
    ``statusBody`` string, the consignee has no ``name``, and measurements are
    ``items[].dimensions`` / ``items[].weight``.
    """
    code = "00000000000000021"
    point = "EXAMPLE SERVICE POINT"

    def ev(code_, time, desc, status, loc=None):
        e = {"eventCode": code_, "eventDescription": desc, "eventTime": time, "status": status}
        if loc is not None:
            e["location"] = loc
        return e

    hub = {"countryCode": "SE", "locationId": "175", "locationName": "EXAMPLE PAKETTERMINAL", "locationType": "HUB"}
    route = {"countryCode": "SE", "locationId": "1000001", "locationName": "Example City", "locationType": "SLINGA"}
    sp = {"countryCode": "SE", "locationId": "200001", "locationName": point, "locationType": "SERVICE_POINT"}
    transport = "The shipment item is under transportation."
    return {
        "additionalServices": [{"code": "77", "name": "Optional Service Point"}],
        "archivedStatus": "ARCHIVED",
        "consignee": {"address": {"city": "EXAMPLE CITY", "countryCode": "SE", "postCode": "11111"}},
        "consigneeEmailExists": True,
        "consigneePhoneNumberExists": True,
        "consignor": {"address": {"countryCode": "SE", "postCode": "22222"}, "name": "Example Shop"},
        "deliveryDate": "2026-09-23T14:27:00Z",
        "destinationDeliveryPoint": {"name": point},
        "items": [
            {
                "customerReference": "ORDER-0001",
                "deliveryImageAvailable": False,
                "deviationImageAvailable": False,
                "dimensions": {
                    "height": {"unit": "m", "value": "0.02"},
                    "length": {"unit": "m", "value": "0.28"},
                    "width": {"unit": "m", "value": "0.2"},
                },
                "environmentalInfo": {"swanEco": True},
                "events": [
                    ev("68", "2026-09-21T11:44:07Z", "We have received a notification from your shipper that they are preparing an item for you.", "INFORMED", {"locationName": "PostNord"}),
                    ev("31", "2026-09-21T16:32:00Z", transport, "EN_ROUTE", hub),
                    ev("z3D", "2026-09-21T18:40:00Z", transport, "OTHER", hub),
                    ev("z6O", "2026-09-22T03:17:01Z", transport, "OTHER", hub),
                    ev("355", "2026-09-23T00:06:00Z", transport, "EN_ROUTE", hub),
                    ev("z114", "2026-09-23T05:00:09Z", "The shipment item has arrived at the distribution terminal.", "EN_ROUTE", route),
                    ev("113", "2026-09-23T05:00:10Z", "The delivery of the shipment item is in progress.", "EN_ROUTE", route),
                    ev("1", "2026-09-23T07:34:00Z", "The shipment item has been delivered to a service point.", "AVAILABLE_FOR_DELIVERY", sp),
                    ev("z82", "2026-09-23T07:35:26Z", "A text message notification has been sent to the recipient.", "OTHER", {"countryCode": "SE", "locationId": "3", "locationType": "HUB"}),
                    ev("21", "2026-09-23T14:27:00Z", "The shipment item has been delivered.", "DELIVERED", sp),
                ],
                "itemId": code,
                "returnDate": "2026-09-30T10:00:00Z",
                "status": "DELIVERED",
                "statusText": {
                    "body": "The shipment item was delivered on 9/23/2026 at 2:27 PM",
                    "header": "The shipment item has been delivered to the recipient",
                },
                "userData": {"hasSubmittedExperienceFeedback": False, "manuallyMarkedAsDelivered": False},
                "weight": {"unit": "kg", "value": "0.2"},
            }
        ],
        "service": {"code": "19", "name": "PostNord Service Point"},
        "shipmentId": code,
        "status": "DELIVERED",
        "statusBody": "The shipment was delivered on 9/23/2026 at 2:27 PM",
        "userData": {
            "dateAdded": "2026-09-27T17:23:37.881Z",
            "dateArchived": "2026-09-27T17:23:38.403Z",
            "direction": "INCOMING",
            "hasBeenAutoArchived": True,
            "searchString": code,
        },
    }
