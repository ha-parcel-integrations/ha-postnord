"""Tests for PostNord sensor property logic."""

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

from custom_components.postnord.const import ParcelStatus
from custom_components.postnord.sensor import (
    PostNordAwaitingPickupSensor,
    PostNordDeliveredParcelsSensor,
    PostNordEnRouteToPickupPointSensor,
    PostNordIncomingParcelsSensor,
    PostNordLastUpdateSensor,
    PostNordNextDeliverySensor,
    PostNordParcelSensor,
)


def _entry(entry_id: str = "e1") -> MagicMock:
    entry = MagicMock()
    entry.entry_id = entry_id
    return entry


def _coordinator(data: list[dict], delivered: list[dict] | None = None) -> MagicMock:
    coordinator = MagicMock()
    coordinator.data = data
    coordinator.delivered = delivered if delivered is not None else []
    return coordinator


def _parcel(
    barcode: str,
    status: ParcelStatus = ParcelStatus.IN_TRANSIT,
    pickup: bool = False,
    planned_from: str | None = None,
    planned_to: str | None = None,
) -> dict:
    return {
        "carrier": "PostNord",
        "barcode": barcode,
        "sender": "Sender",
        "receiver": None,
        "contents": "Running shoes",
        "status": status,
        "pickup": pickup,
        "planned_from": planned_from,
        "planned_to": planned_to,
    }


def test_incoming_counts_and_lists():
    coordinator = _coordinator([_parcel("A"), _parcel("B")])
    sensor = PostNordIncomingParcelsSensor(coordinator, _entry(), lambda _: None, set())
    assert sensor.native_value == 2
    assert len(sensor.extra_state_attributes["parcels"]) == 2


def test_awaiting_pickup_counts_every_parcel_at_pickup_point():
    coordinator = _coordinator(
        [
            _parcel("A", ParcelStatus.AT_PICKUP_POINT, pickup=True),
            _parcel("B", ParcelStatus.IN_TRANSIT, pickup=True),
            _parcel("C", ParcelStatus.AT_PICKUP_POINT, pickup=False),
        ]
    )
    sensor = PostNordAwaitingPickupSensor(coordinator, _entry())
    assert sensor.native_value == 2
    assert len(sensor.extra_state_attributes["parcels"]) == 2


def test_en_route_counts_only_active_pickup_parcels_not_yet_at_the_point():
    coordinator = _coordinator(
        [
            _parcel("A", ParcelStatus.IN_TRANSIT, pickup=True),
            _parcel("B", ParcelStatus.OUT_FOR_DELIVERY, pickup=True),
            _parcel("C", ParcelStatus.IN_TRANSIT, pickup=False),
            _parcel("D", ParcelStatus.AT_PICKUP_POINT, pickup=True),
            _parcel("E", ParcelStatus.RETURNING, pickup=True),
            _parcel("F", ParcelStatus.DELIVERED, pickup=True),
        ]
    )
    sensor = PostNordEnRouteToPickupPointSensor(coordinator, _entry())
    assert sensor.native_value == 2
    assert [p["barcode"] for p in sensor.extra_state_attributes["parcels"]] == [
        "A",
        "B",
    ]


def test_en_route_is_empty_without_parcels():
    sensor = PostNordEnRouteToPickupPointSensor(_coordinator([]), _entry())
    assert sensor.native_value == 0
    assert sensor.extra_state_attributes == {"parcels": []}


def test_awaiting_pickup_is_empty_without_parcels():
    sensor = PostNordAwaitingPickupSensor(_coordinator([]), _entry())
    assert sensor.native_value == 0
    assert sensor.extra_state_attributes == {"parcels": []}


def test_parcel_sensor_status_and_attributes():
    parcel = _parcel("A", status=ParcelStatus.OUT_FOR_DELIVERY)
    sensor = PostNordParcelSensor(_coordinator([parcel]), _entry(), "A")
    assert sensor.native_value == ParcelStatus.OUT_FOR_DELIVERY
    assert sensor.extra_state_attributes["barcode"] == "A"


def test_parcel_sensor_missing_barcode():
    sensor = PostNordParcelSensor(_coordinator([_parcel("A")]), _entry(), "OTHER")
    assert sensor.native_value is None
    assert sensor.extra_state_attributes == {}


def test_next_delivery_picks_earliest():
    coordinator = _coordinator(
        [
            _parcel("A", planned_from="2099-05-02T10:00:00Z"),
            _parcel("B", planned_from="2099-05-01T10:00:00Z"),
        ]
    )
    sensor = PostNordNextDeliverySensor(coordinator, _entry())
    assert sensor.native_value == datetime(2099, 5, 1, 10, 0, tzinfo=UTC)
    assert sensor.extra_state_attributes["barcode"] == "B"
    assert sensor.extra_state_attributes["contents"] == "Running shoes"


def test_next_delivery_none_without_moments():
    sensor = PostNordNextDeliverySensor(_coordinator([_parcel("A")]), _entry())
    assert sensor.native_value is None
    assert sensor.extra_state_attributes == {}


def test_next_delivery_skips_unparseable_moment():
    coordinator = _coordinator(
        [
            _parcel("A", planned_from="not-a-date"),
            _parcel("B", planned_from="2099-05-01T10:00:00Z"),
        ]
    )
    sensor = PostNordNextDeliverySensor(coordinator, _entry())
    assert sensor.extra_state_attributes["barcode"] == "B"


def test_delivered_sensor():
    coordinator = _coordinator(
        [], delivered=[_parcel("D", status=ParcelStatus.DELIVERED)]
    )
    sensor = PostNordDeliveredParcelsSensor(coordinator, _entry())
    assert sensor.native_value == 1
    assert sensor.extra_state_attributes["parcels"][0]["barcode"] == "D"


def test_last_update_sensor():
    coordinator = _coordinator([])
    moment = datetime(2026, 6, 30, 12, 0, tzinfo=UTC)
    coordinator.last_success_time = moment
    sensor = PostNordLastUpdateSensor(coordinator, _entry())
    assert sensor.native_value == moment


def _in(**delta) -> str:
    return (datetime.now(UTC) + timedelta(**delta)).isoformat()


def test_next_delivery_skips_a_window_that_has_ended():
    stale = _parcel("A", planned_from=_in(days=-3), planned_to=_in(days=-2))
    moment_only = _parcel("B", planned_from=_in(hours=-2))
    sensor = PostNordNextDeliverySensor(
        _coordinator([stale, moment_only, _parcel("C", planned_from=_in(days=2))]),
        _entry(),
    )
    assert sensor.extra_state_attributes["barcode"] == "C"


def test_next_delivery_keeps_a_window_that_is_still_running():
    running = _parcel("A", planned_from=_in(hours=-1), planned_to=_in(hours=2))
    sensor = PostNordNextDeliverySensor(_coordinator([running]), _entry())
    assert sensor.extra_state_attributes["barcode"] == "A"
