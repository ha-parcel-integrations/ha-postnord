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
from .payloads import ACCOUNT_ACTIVE_CODE, account_active, account_delivered


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


def test_unconfirmed_fields_stay_none():
    raw = account_active()
    raw["estimatedTimeOfArrival"] = "2026-04-29T13:00:00Z"
    raw["destinationDeliveryPoint"] = {"name": "Example Point"}
    raw["items"][0]["statedMeasurement"] = {
        "length": {"value": "0.3", "unit": "m"},
        "width": {"value": "0.2", "unit": "m"},
        "height": {"value": "0.1", "unit": "m"},
    }
    parcel = normalize_account_parcel(raw)
    assert parcel["planned_from"] is None
    assert parcel["planned_to"] is None
    assert parcel["pickup_point"] is None
    assert parcel["dimensions"] is None


def test_unconfirmed_field_sighting_warns_once_with_key_only(caplog):
    raw = account_active()
    raw["destinationDeliveryPoint"] = {"name": "Secret Street 1"}
    with caplog.at_level(logging.WARNING):
        normalize_account_parcel(raw)
        normalize_account_parcel(raw)
    warnings = [r for r in caplog.records if "destinationDeliveryPoint" in r.getMessage()]
    assert len(warnings) == 1
    assert "Secret Street" not in caplog.text


def test_missing_expected_field_warns_once(caplog):
    raw = account_active()
    del raw["statusText"]
    del raw["items"][0]["events"]
    with caplog.at_level(logging.WARNING):
        normalize_account_parcel(raw)
        normalize_account_parcel(raw)
    assert caplog.text.count("'statusText'") == 1
    assert caplog.text.count("'items[].events'") == 1


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
    parcel = normalize_account_parcel(account_active(), include_history=True)
    for field in KNOWN_CAPABILITIES:
        populated = field == "delivery_window" and parcel["planned_from"] is not None
        populated = populated or (field != "delivery_window" and parcel[field] is not None)
        assert populated == (field in claimed), field
