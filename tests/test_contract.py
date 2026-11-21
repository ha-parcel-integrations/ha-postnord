"""The parcel contract's checks, run over both sources' normalised samples."""

from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from custom_components.postnord.account.parcels import normalize_account_parcel
from custom_components.postnord.const import ParcelStatus
from custom_components.postnord.tracking.parcels import (
    is_pickup,
    normalize_parcel,
    planned_for_status,
    planned_window,
)

from .account.payloads import account_active
from .payloads import active_sample, delivered_sample, event, pickup_sample

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

_AMS = ZoneInfo("Europe/Amsterdam")


def _finished_return(sample):
    raw = sample()
    raw["status"] = "RETURNED"
    raw["items"][0]["events"] += [
        event("RETURNED", "2026-05-07T04:42:01Z", "Returned to the sender"),
        event("DELIVERED", "2026-05-13T20:55:00Z", "Delivered back", "21"),
    ]
    return raw


def _return_in_flight(sample):
    raw = sample()
    raw["status"] = "RETURNED"
    raw["items"][0]["events"].append(
        event("RETURNED", "2026-05-07T04:42:01Z", "Returned to the sender")
    )
    return raw


def _collected():
    raw = pickup_sample()
    raw["status"] = "DELIVERED"
    return raw


SAMPLES = {
    "tracked_active": lambda: normalize_parcel(active_sample()),
    "tracked_at_pickup": lambda: normalize_parcel(pickup_sample()),
    "tracked_collected": lambda: normalize_parcel(_collected()),
    "tracked_delivered": lambda: normalize_parcel(delivered_sample()),
    "tracked_returning": lambda: normalize_parcel(_return_in_flight(active_sample)),
    "tracked_finished_return": lambda: normalize_parcel(
        _finished_return(delivered_sample)
    ),
    "account_active": lambda: normalize_account_parcel(account_active()),
    "account_returning": lambda: normalize_account_parcel(
        _return_in_flight(account_active)
    ),
    "account_finished_return": lambda: normalize_account_parcel(
        _finished_return(account_active)
    ),
}


@pytest.fixture(params=SAMPLES)
def parcel(request, amsterdam) -> dict:
    return SAMPLES[request.param]()


def test_keys_and_order(parcel):
    assert list(parcel) == CANONICAL_KEYS


def test_delivered_is_the_status_or_a_finished_return(parcel):
    if parcel["status"] is ParcelStatus.DELIVERED:
        assert parcel["delivered"] is True
    elif parcel["status"] is not ParcelStatus.RETURNING:
        assert parcel["delivered"] is False


def test_delivered_at_only_when_delivered(parcel):
    if not parcel["delivered"]:
        assert parcel["delivered_at"] is None


def test_planned_window_is_ordered_and_anchored(parcel):
    if parcel["planned_to"] is not None:
        assert parcel["planned_from"] is not None
        assert datetime.fromisoformat(parcel["planned_to"]) > datetime.fromisoformat(
            parcel["planned_from"]
        )


def test_planned_is_empty_when_the_parcel_is_done_or_waiting(parcel):
    if parcel["status"] in (
        ParcelStatus.DELIVERED,
        ParcelStatus.AT_PICKUP_POINT,
        ParcelStatus.RETURNING,
    ):
        assert parcel["planned_from"] is None
        assert parcel["planned_to"] is None


def test_pickup_is_a_bool_and_the_point_needs_it(parcel):
    assert isinstance(parcel["pickup"], bool)
    if not parcel["pickup"]:
        assert parcel["pickup_point"] is None


def test_a_collected_pickup_parcel_stays_pickup():
    parcel = SAMPLES["tracked_collected"]()
    assert parcel["status"] is ParcelStatus.DELIVERED
    assert parcel["pickup"] is True


@pytest.mark.parametrize("name", ["tracked_finished_return", "account_finished_return"])
def test_a_return_back_at_the_sender_is_finished_but_stays_returning(name, amsterdam):
    parcel = SAMPLES[name]()
    assert parcel["status"] is ParcelStatus.RETURNING
    assert parcel["delivered"] is True
    assert parcel["delivered_at"] == "2026-05-13T20:55:00Z"


@pytest.mark.parametrize("name", ["tracked_returning", "account_returning"])
def test_a_return_still_on_its_way_is_not_finished(name, amsterdam):
    parcel = SAMPLES[name]()
    assert parcel["status"] is ParcelStatus.RETURNING
    assert parcel["delivered"] is False


def test_an_eta_runs_to_the_end_of_its_local_day(amsterdam):
    parcel = SAMPLES["tracked_active"]()
    assert parcel["planned_to"] == "2026-04-29T23:59:59+02:00"


@pytest.mark.parametrize(
    ("start", "end", "expected"),
    [
        (
            "2026-04-29T13:00:00+02:00",
            "2026-04-29T15:00:00+02:00",
            ("2026-04-29T13:00:00+02:00", "2026-04-29T15:00:00+02:00"),
        ),
        (
            "2026-04-29T13:00:00+02:00",
            None,
            ("2026-04-29T13:00:00+02:00", "2026-04-29T23:59:59+02:00"),
        ),
        (
            "2026-04-29T13:00:00+02:00",
            "2026-04-29T13:00:00+02:00",
            ("2026-04-29T13:00:00+02:00", "2026-04-29T23:59:59+02:00"),
        ),
        (
            "2026-04-29T13:00:00",
            None,
            ("2026-04-29T13:00:00+02:00", "2026-04-29T23:59:59+02:00"),
        ),
        (
            "2026-04-29",
            None,
            ("2026-04-29T00:00:00+02:00", "2026-04-29T23:59:59+02:00"),
        ),
        (
            date(2026, 4, 29),
            date(2026, 4, 29),
            ("2026-04-29T00:00:00+02:00", "2026-04-29T23:59:59+02:00"),
        ),
        (
            "2026-04-29",
            "2026-05-01",
            ("2026-04-29T00:00:00+02:00", "2026-05-01T23:59:59+02:00"),
        ),
        (
            "2026-04-29T15:00:00+02:00",
            "2026-04-29T13:00:00+02:00",
            ("2026-04-29T13:00:00+02:00", "2026-04-29T15:00:00+02:00"),
        ),
        (
            None,
            "2026-04-29T13:00:00+02:00",
            ("2026-04-29T13:00:00+02:00", "2026-04-29T23:59:59+02:00"),
        ),
        (
            datetime(2026, 4, 29, 13, 0),
            datetime(2026, 4, 29, 15, 0),
            ("2026-04-29T13:00:00+02:00", "2026-04-29T15:00:00+02:00"),
        ),
        (None, None, (None, None)),
        ("garbage", None, (None, None)),
        (12, 13, (None, None)),
    ],
)
def test_planned_window_shapes(start, end, expected):
    assert planned_window(start, end, zone=_AMS) == expected


@pytest.mark.parametrize(
    "status",
    [ParcelStatus.AT_PICKUP_POINT, ParcelStatus.RETURNING, ParcelStatus.DELIVERED],
)
def test_planned_for_status_empties_the_window(status):
    assert planned_for_status(status, "a", "b") == (None, None)


def test_planned_for_status_keeps_the_window_otherwise():
    for status in (
        ParcelStatus.REGISTERED,
        ParcelStatus.IN_TRANSIT,
        ParcelStatus.OUT_FOR_DELIVERY,
        ParcelStatus.PROBLEM,
        ParcelStatus.UNKNOWN,
    ):
        assert planned_for_status(status, "a", "b") == ("a", "b")


def test_is_pickup_sources():
    assert is_pickup(True, ParcelStatus.IN_TRANSIT) is True
    assert is_pickup(None, ParcelStatus.AT_PICKUP_POINT) is True
    assert is_pickup(None, ParcelStatus.DELIVERED, [None, ParcelStatus.AT_PICKUP_POINT])
    assert is_pickup(False, ParcelStatus.IN_TRANSIT, [ParcelStatus.IN_TRANSIT]) is False
    assert is_pickup(None, ParcelStatus.DELIVERED) is False
