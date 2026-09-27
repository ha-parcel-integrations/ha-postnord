"""Tests for the PostNord account normaliser."""
import logging

import pytest

from custom_components.postnord.account.parcels import (
    account_barcode,
    normalize_account_parcel,
)
from custom_components.postnord.const import ParcelStatus
from custom_components.postnord.status import map_parcel_status

from ..payloads import event
from ..tracking.test_parcels import CANONICAL_KEYS
from .payloads import (
    ACCOUNT_ACTIVE_CODE,
    account_active,
    account_delivered,
    captured_service_point_delivery,
)


def test_publishes_exactly_the_canonical_keys():
    assert list(normalize_account_parcel(account_active())) == CANONICAL_KEYS


def test_active_parcel():
    parcel = normalize_account_parcel(account_active())
    assert parcel["barcode"] == ACCOUNT_ACTIVE_CODE
    assert parcel["status"] == ParcelStatus.IN_TRANSIT
    assert parcel["raw_status"] == "On its way"
    assert parcel["sender"] == "Example Shop"
    assert parcel["receiver"] == "Jane Doe"
    assert parcel["delivered"] is False
    assert parcel["delivered_at"] is None
    assert parcel["weight"] == 1.25
    assert parcel["url"].endswith(ACCOUNT_ACTIVE_CODE)
    assert parcel["history"] is None


def test_eta_is_a_point_estimate_until_delivered():
    raw = account_active()
    raw["estimatedTimeOfArrival"] = "2026-04-29T13:00:00Z"
    parcel = normalize_account_parcel(raw)
    assert parcel["planned_from"] == "2026-04-29T13:00:00Z"
    assert parcel["planned_to"] is None
    raw["status"] = "DELIVERED"
    assert normalize_account_parcel(raw)["planned_from"] is None


def test_public_time_of_arrival_is_the_eta_fallback():
    raw = account_active()
    raw["publicTimeOfArrival"] = "2026-04-30T09:00:00Z"
    assert normalize_account_parcel(raw)["planned_from"] == "2026-04-30T09:00:00Z"


def test_missing_expected_field_warns_once(caplog):
    raw = account_active()
    del raw["status"]
    del raw["items"][0]["events"]
    with caplog.at_level(logging.WARNING):
        normalize_account_parcel(raw)
        normalize_account_parcel(raw)
    assert caplog.text.count("'status'") == 1
    assert caplog.text.count("'items[].events'") == 1
    assert caplog.text.count("'items[].statusText'") == 1


def test_captured_service_point_delivery():
    """The real account shape, scrubbed — see payloads.py."""
    raw = captured_service_point_delivery()
    parcel = normalize_account_parcel(raw, include_history=True)
    assert parcel["status"] == ParcelStatus.DELIVERED
    assert parcel["raw_status"] == "The shipment item has been delivered to the recipient"
    assert parcel["delivered_at"] == "2026-09-23T14:27:00Z"
    assert parcel["sender"] == "Example Shop"
    assert parcel["receiver"] is None
    assert parcel["pickup_point"] == "EXAMPLE SERVICE POINT"
    assert parcel["pickup"] is False
    assert parcel["weight"] == 0.2
    assert parcel["dimensions"] == {
        "length": 28.0, "width": 20.0, "height": 2.0, "text": "28 x 20 x 2 cm",
    }
    assert [e["status"] for e in parcel["history"]][-4:] == [
        ParcelStatus.OUT_FOR_DELIVERY,
        ParcelStatus.AT_PICKUP_POINT,
        ParcelStatus.AT_PICKUP_POINT,  # the text-message event (OTHER)
        ParcelStatus.DELIVERED,
    ]
    assert all(entry["status"] is not None for entry in parcel["history"])


def test_raw_is_the_untouched_shipment():
    raw = captured_service_point_delivery()
    assert normalize_account_parcel(raw)["raw"] == captured_service_point_delivery()


def test_captured_shape_raises_no_missing_field_warning(caplog):
    with caplog.at_level(logging.WARNING):
        normalize_account_parcel(captured_service_point_delivery())
    assert "missing" not in caplog.text


def test_unseen_direction_warns_once_and_stays_listed(caplog):
    raw = account_active()
    raw["userData"]["direction"] = "OUTGOING"
    with caplog.at_level(logging.WARNING):
        normalize_account_parcel(raw)
        parcel = normalize_account_parcel(raw)
    assert caplog.text.count("direction=OUTGOING") == 1
    assert parcel["barcode"] == ACCOUNT_ACTIVE_CODE


def test_delivered_parcel_takes_the_delivery_event_time():
    parcel = normalize_account_parcel(account_delivered(), include_history=True)
    assert parcel["delivered"] is True
    assert parcel["delivered_at"] == "2026-04-29T13:12:42Z"
    assert len(parcel["history"]) == 4


def test_delivered_without_event_falls_back_to_delivery_date():
    raw = account_delivered()
    raw["items"][0]["events"] = []
    raw["deliveryDate"] = "2026-04-29T12:00:00Z"
    assert normalize_account_parcel(raw)["delivered_at"] == "2026-04-29T12:00:00Z"


def test_status_falls_back_to_the_item():
    raw = account_active()
    del raw["status"]
    assert normalize_account_parcel(raw)["status"] == ParcelStatus.IN_TRANSIT


def test_out_for_delivery_event_code_upgrades_en_route():
    raw = account_active()
    raw["items"][0]["events"].insert(
        0, event("EN_ROUTE", "2026-04-29T07:00:00Z", "Delivery in progress", "113")
    )
    assert normalize_account_parcel(raw)["status"] == ParcelStatus.OUT_FOR_DELIVERY


def test_pickup_state():
    raw = account_active()
    raw["status"] = "AVAILABLE_FOR_DELIVERY"
    parcel = normalize_account_parcel(raw)
    assert parcel["status"] == ParcelStatus.AT_PICKUP_POINT
    assert parcel["pickup"] is True


def test_item_weight_fallback():
    raw = account_active()
    del raw["totalWeight"]
    raw["items"][0]["weight"] = {"value": "500", "unit": "g"}
    assert normalize_account_parcel(raw)["weight"] == 0.5


def test_parties_that_are_not_objects():
    raw = account_active()
    raw["consignor"] = "Example Shop"
    raw["consignee"] = None
    parcel = normalize_account_parcel(raw)
    assert parcel["sender"] is None
    assert parcel["receiver"] is None


def test_barcode_falls_back_to_the_item_id():
    raw = account_active()
    del raw["shipmentId"]
    raw["items"][0]["itemId"] = "ITEM1"
    assert account_barcode(raw) == "ITEM1"
    assert account_barcode({}) is None


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ("DELAYED", ParcelStatus.PROBLEM),
        ("DELIVERY_REFUSED", ParcelStatus.RETURNING),
    ],
)
def test_account_literals_are_in_the_shared_map(code, expected):
    assert map_parcel_status(code) == expected


def test_account_capabilities_match_what_the_normaliser_fills():
    """The docs-site table must not claim a field this source leaves None."""
    from custom_components.postnord.const import (
        CAPABILITIES_BY_VARIANT,
        KNOWN_CAPABILITIES,
    )

    claimed = CAPABILITIES_BY_VARIANT["Account"]
    assert claimed <= KNOWN_CAPABILITIES
    delivered = normalize_account_parcel(
        captured_service_point_delivery(), include_history=True
    )
    active = account_active()
    active["estimatedTimeOfArrival"] = "2026-04-29T13:00:00Z"
    on_the_way = normalize_account_parcel(active)
    filled = {
        "weight": delivered["weight"],
        "dimensions": delivered["dimensions"],
        "pickup_point": delivered["pickup_point"],
        "url": delivered["url"],
        "history": delivered["history"],
        "delivery_window": on_the_way["planned_from"],
    }
    assert {field for field, value in filled.items() if value is not None} == claimed


def test_finished_return_is_delivered_and_keeps_its_text():
    raw = account_active()
    raw["status"] = "RETURNED"
    raw["statusText"] = {"header": "Returned to sender"}
    raw["items"][0]["events"] += [
        event("RETURNED", "2026-05-07T04:42:01Z", "Returned to the sender"),
        event("DELIVERED", "2026-05-13T20:55:00Z", "Delivered with Power Of Attorney", "21"),
    ]
    parcel = normalize_account_parcel(raw)
    assert parcel["status"] == ParcelStatus.DELIVERED
    assert parcel["delivered"] is True
    assert parcel["delivered_at"] == "2026-05-13T20:55:00Z"
    assert parcel["raw_status"] == "Returned to sender"


def test_return_in_flight_stays_returning():
    raw = account_active()
    raw["status"] = "RETURNED"
    raw["items"][0]["events"].append(
        event("RETURNED", "2026-05-07T04:42:01Z", "Returned to the sender")
    )
    assert normalize_account_parcel(raw)["status"] == ParcelStatus.RETURNING


def test_pickup_point_falls_back_to_where_the_parcel_waited():
    raw = captured_service_point_delivery()
    del raw["destinationDeliveryPoint"]
    assert normalize_account_parcel(raw)["pickup_point"] == "EXAMPLE SERVICE POINT"
