"""Tests for the pure parcel-mapping helpers.

These need no Home Assistant instance — the whole point of keeping
``parcels.py`` free of I/O is that the carrier-specific mapping (the part you
rewrite per carrier) can be tested as plain functions.
"""
from datetime import datetime, timedelta, timezone

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.postnord.const import (
    CONF_DELIVERED_FILTER_AMOUNT,
    CONF_DELIVERED_FILTER_TYPE,
    DOMAIN,
    ParcelStatus,
)
from custom_components.postnord.parcels import (
    _shape_fields_logged,
    apply_delivered_filter,
    build_history,
    check_shipment_shape,
    format_dimensions,
    map_event_status,
    map_parcel_status,
    normalize_parcel,
    parse_iso,
    sort_parcels_by_ts,
    to_iso_timestamp,
)

from .payloads import active_sample, delivered_sample, event, pickup_sample


def _events(sample: dict) -> list[dict]:
    """The flat event list PostNord nests under the shipment's first item."""
    return sample["items"][0]["events"]


# ---------------------------------------------------------------------------
# map_parcel_status / map_event_status
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "code,expected",
    [
        ("CREATED", ParcelStatus.REGISTERED),
        ("INFORMED", ParcelStatus.REGISTERED),
        ("EN_ROUTE", ParcelStatus.IN_TRANSIT),
        ("AVAILABLE_FOR_DELIVERY", ParcelStatus.AT_PICKUP_POINT),
        ("DELIVERY", ParcelStatus.OUT_FOR_DELIVERY),
        ("DELIVERED", ParcelStatus.DELIVERED),
        ("DELIVERY_IMPOSSIBLE", ParcelStatus.PROBLEM),
        ("EXPECTED_DELAY", ParcelStatus.PROBLEM),
        ("RETURNED", ParcelStatus.RETURNING),
        ("STOPPED", ParcelStatus.PROBLEM),
    ],
)
def test_map_parcel_status_known(code, expected):
    assert map_parcel_status(code) == expected


def test_map_parcel_status_missing_is_unknown():
    assert map_parcel_status(None) == ParcelStatus.UNKNOWN
    assert map_parcel_status("") == ParcelStatus.UNKNOWN


def test_map_parcel_status_unmapped_is_unknown():
    # OTHER is PostNord's own catch-all — deliberately left unmapped.
    assert map_parcel_status("OTHER") == ParcelStatus.UNKNOWN


def test_map_event_status_missing_and_unmapped_are_none():
    """History keeps ``null`` rather than ``unknown`` so consumers can tell
    "no mapping" from "mapped to unknown"."""
    assert map_event_status(None) is None
    assert map_event_status("OTHER") is None
    assert map_event_status("DELIVERED") == ParcelStatus.DELIVERED


def test_unmapped_status_warns_only_once(caplog):
    assert map_parcel_status("ABDUCTED") == ParcelStatus.UNKNOWN
    assert map_parcel_status("ABDUCTED") == ParcelStatus.UNKNOWN
    assert caplog.text.count("ABDUCTED") == 1
    assert "issues/new" in caplog.text


# ---------------------------------------------------------------------------
# timestamp helpers
# ---------------------------------------------------------------------------


def test_parse_iso_handles_z_naive_and_garbage():
    assert parse_iso("2026-04-29T13:12:42Z").tzinfo is not None
    # A naive value is assumed UTC so mixed lists still sort.
    assert parse_iso("2026-04-29T13:12:42").tzinfo == timezone.utc
    assert parse_iso("not-a-date") is None
    assert parse_iso(None) is None


def test_to_iso_timestamp_passes_strings_through():
    assert to_iso_timestamp("2026-04-29T13:12:42Z") == "2026-04-29T13:12:42Z"
    assert to_iso_timestamp(None) is None


def test_format_dimensions_needs_all_three_axes():
    assert format_dimensions(30, 20, 10) == {
        "length": 30,
        "width": 20,
        "height": 10,
        "text": "30 x 20 x 10 cm",
    }
    assert format_dimensions(30, None, 10) is None


# ---------------------------------------------------------------------------
# build_history
# ---------------------------------------------------------------------------


def test_build_history_orders_oldest_to_newest():
    history = build_history(_events(delivered_sample()))
    assert len(history) == 4
    assert history[0]["raw_status"] == "Shipment announced"
    assert history[0]["status"] == ParcelStatus.REGISTERED
    assert history[-1]["status"] == ParcelStatus.DELIVERED


def test_build_history_caps_to_max_events():
    events = [
        event("EN_ROUTE", f"2026-04-{day:02d}T10:00:00Z", "moved")
        for day in range(1, 26)
    ]
    assert len(build_history(events, max_events=20)) == 20


def test_build_history_handles_missing_and_malformed():
    assert build_history(None) == []
    assert build_history([{"status": "EN_ROUTE"}]) == []  # no eventTime
    assert build_history(["not-a-dict"]) == []


def test_build_history_keeps_unparseable_timestamp_last():
    history = build_history(
        [
            event("INFORMED", "2026-04-24T10:00:00Z", "fine"),
            event("EN_ROUTE", "not-a-date", "odd"),
        ]
    )
    assert [entry["raw_status"] for entry in history] == ["fine", "odd"]


def test_build_history_falls_back_to_event_code_without_text():
    history = build_history([event("EN_ROUTE", "2026-04-24T10:00:00Z", "", code="20")])
    assert history[0]["raw_status"] == "20"


# ---------------------------------------------------------------------------
# normalize_parcel — the canonical contract
# ---------------------------------------------------------------------------

CANONICAL_KEYS = [
    "carrier",
    "barcode",
    "sender",
    "receiver",
    "status",
    "raw_status",
    "delivered",
    "delivered_at",
    "planned_from",
    "planned_to",
    "pickup",
    "pickup_point",
    "url",
    "weight",
    "dimensions",
    "history",
    "raw",
]


def test_normalize_publishes_exactly_the_canonical_keys():
    """The aggregator and cross-carrier dashboards depend on this key set."""
    assert list(normalize_parcel(delivered_sample())) == CANONICAL_KEYS


def test_normalize_delivered_parcel():
    parcel = normalize_parcel(delivered_sample())
    assert parcel["carrier"] == "PostNord"
    assert parcel["barcode"] == "00000000000000002"
    assert parcel["sender"] == "Example Shop"
    assert parcel["receiver"] == "Jane Doe"
    assert parcel["status"] == ParcelStatus.DELIVERED
    assert parcel["raw_status"] == "Delivered"
    assert parcel["delivered"] is True
    assert parcel["delivered_at"] == "2026-04-29T13:12:42Z"
    assert parcel["planned_from"] is None
    assert parcel["planned_to"] is None
    assert (
        parcel["url"]
        == "https://www.postnord.com/en/track-and-trace?shipmentId=00000000000000002"
    )
    assert parcel["weight"] == 1.25
    # PostNord's consumer payload has no L×W×H.
    assert parcel["dimensions"] is None
    assert parcel["history"] is None  # opt-in, default off


def test_normalize_history_is_opt_in():
    parcel = normalize_parcel(delivered_sample(), include_history=True)
    assert len(parcel["history"]) == 4
    assert parcel["history"][0]["status"] == ParcelStatus.REGISTERED


def test_normalize_active_parcel_has_point_eta():
    parcel = normalize_parcel(active_sample())
    assert parcel["status"] == ParcelStatus.OUT_FOR_DELIVERY
    assert parcel["delivered"] is False
    assert parcel["planned_from"] == "2026-04-29T13:00:00Z"
    # PostNord's ETA is a single instant, so there is never a window end.
    assert parcel["planned_to"] is None


def test_normalize_weight_converts_grams():
    raw = active_sample()
    raw["totalWeight"] = {"value": "2500", "unit": "g"}
    assert normalize_parcel(raw)["weight"] == 2.5


def test_normalize_pickup_parcel():
    parcel = normalize_parcel(pickup_sample())
    assert parcel["status"] == ParcelStatus.AT_PICKUP_POINT
    assert parcel["pickup"] is True
    assert parcel["pickup_point"] == "Example Point Central Station"


def test_normalize_pending_placeholder():
    """A tracked-but-not-yet-scanned code still yields a full parcel dict."""
    parcel = normalize_parcel({"shipmentId": "00000000000000009"})
    assert parcel["status"] == ParcelStatus.UNKNOWN
    assert parcel["delivered"] is False
    assert parcel["raw_status"] is None
    assert parcel["weight"] is None
    assert parcel["dimensions"] is None
    assert parcel["history"] is None


def test_normalize_blank_fields_become_none():
    raw = active_sample()
    raw["consignor"] = {}
    raw["consignee"] = {}
    parcel = normalize_parcel(raw)
    assert parcel["sender"] is None
    assert parcel["receiver"] is None


def test_normalize_keeps_raw_payload():
    raw = active_sample()
    assert normalize_parcel(raw)["raw"] is raw


def test_normalize_falls_back_to_status_code_without_text():
    raw = active_sample()
    raw["statusText"] = None
    assert normalize_parcel(raw)["raw_status"] == "DELIVERY"


# ---------------------------------------------------------------------------
# check_shipment_shape (pre-release populated-shape self-report)
# ---------------------------------------------------------------------------


def test_populated_shape_matches_sample_is_silent(caplog):
    """A well-formed shipment (present-but-null fields included) never warns."""
    _shape_fields_logged.clear()
    # delivered_sample carries estimatedTimeOfArrival present-but-None.
    normalize_parcel(delivered_sample())
    assert "response shape may differ" not in caplog.text


def test_pending_placeholder_does_not_warn_shape(caplog):
    """The coordinator's bare {"shipmentId": code} placeholder is not a shipment."""
    _shape_fields_logged.clear()
    normalize_parcel({"shipmentId": "00000000000000009"})
    assert "response shape may differ" not in caplog.text


def test_missing_mapped_field_warns_once(caplog):
    """A populated shipment missing a mapped key self-reports the reshape once."""
    _shape_fields_logged.clear()
    raw = active_sample()
    del raw["consignor"]
    normalize_parcel(raw)
    normalize_parcel(raw)
    assert caplog.text.count("'consignor'") == 1
    assert "issues/new" in caplog.text


def test_missing_item_events_warns(caplog):
    """Items without an events timeline surface as the items[].events gap."""
    _shape_fields_logged.clear()
    raw = active_sample()
    raw["items"] = [{"itemId": "x", "status": "DELIVERY"}]
    check_shipment_shape(raw)
    assert "items[].events" in caplog.text


# ---------------------------------------------------------------------------
# sort_parcels_by_ts
# ---------------------------------------------------------------------------


def test_sort_parcels_ascending_puts_unparseable_last():
    parcels = [
        {"barcode": "a", "planned_from": "2026-05-02T10:00:00Z"},
        {"barcode": "b", "planned_from": None},
        {"barcode": "c", "planned_from": "2026-05-01T10:00:00Z"},
    ]
    ordered = [p["barcode"] for p in sort_parcels_by_ts(parcels, "planned_from")]
    assert ordered == ["c", "a", "b"]


def test_sort_parcels_descending_still_puts_unparseable_last():
    parcels = [
        {"barcode": "a", "delivered_at": "2026-05-02T10:00:00Z"},
        {"barcode": "b", "delivered_at": "nonsense"},
        {"barcode": "c", "delivered_at": "2026-05-01T10:00:00Z"},
    ]
    ordered = [
        p["barcode"]
        for p in sort_parcels_by_ts(parcels, "delivered_at", descending=True)
    ]
    assert ordered == ["a", "c", "b"]


# ---------------------------------------------------------------------------
# apply_delivered_filter
# ---------------------------------------------------------------------------


def _entry(filter_type: str, amount: int) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        data={},
        options={
            CONF_DELIVERED_FILTER_TYPE: filter_type,
            CONF_DELIVERED_FILTER_AMOUNT: amount,
        },
        unique_id=DOMAIN,
    )


def _delivered_pair() -> list[dict]:
    now = datetime.now(timezone.utc)
    return [
        {"barcode": "RECENT", "delivered_at": (now - timedelta(days=1)).isoformat()},
        {"barcode": "OLD", "delivered_at": (now - timedelta(days=30)).isoformat()},
    ]


def test_delivered_filter_by_days():
    kept = apply_delivered_filter(_delivered_pair(), _entry("days", 7))
    assert [p["barcode"] for p in kept] == ["RECENT"]


def test_delivered_filter_by_count():
    parcels = _delivered_pair()
    assert apply_delivered_filter(parcels, _entry("parcels", 1)) == parcels[:1]


def test_delivered_filter_keeps_unparseable_timestamp():
    """Better to show a parcel with a broken date than to silently drop it."""
    parcels = [{"barcode": "WEIRD", "delivered_at": "nonsense"}]
    assert apply_delivered_filter(parcels, _entry("days", 7)) == parcels
