"""Tests for the PostNord account coordinator."""
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import UpdateFailed
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.postnord.account.client import (
    PostNordAccountApiError,
    PostNordAccountCompatibilityError,
    PostNordAccountReauthRequired,
)
from custom_components.postnord.account.coordinator import PostNordAccountCoordinator
from custom_components.postnord.const import (
    CONF_DELIVERED_FILTER_AMOUNT,
    CONF_DELIVERED_FILTER_TYPE,
    CONF_SOURCE,
    DOMAIN,
    MID_INTERVAL_MINUTES,
    SOURCE_ACCOUNT,
)

from .payloads import (
    ACCOUNT_ACTIVE_CODE,
    ACCOUNT_DELIVERED_CODE,
    account_active,
    account_delivered,
    tracking_body,
)


def _coordinator(hass, body=None, side_effect=None):
    # Retain by count: the sample deliveries are older than the default 7 days.
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_SOURCE: SOURCE_ACCOUNT},
        options={CONF_DELIVERED_FILTER_TYPE: "parcels", CONF_DELIVERED_FILTER_AMOUNT: 7},
    )
    entry.add_to_hass(hass)
    client = MagicMock()
    if side_effect is not None:
        client.async_get_tracking = AsyncMock(side_effect=side_effect)
    else:
        full = body or tracking_body()
        client.async_get_tracking = AsyncMock(
            return_value={
                "activeShipments": full["activeShipments"],
                "archivedShipments": full["archivedShipments"],
            }
        )
    return PostNordAccountCoordinator(hass, client, entry)


async def test_splits_active_and_delivered(hass):
    coordinator = _coordinator(hass)
    data = await coordinator._async_update_data()
    assert [p["barcode"] for p in data] == [ACCOUNT_ACTIVE_CODE]
    assert [p["barcode"] for p in coordinator.delivered] == [ACCOUNT_DELIVERED_CODE]
    assert coordinator.last_success_time is not None


async def test_never_suspends_and_skips_nothing(hass):
    coordinator = _coordinator(hass)
    await coordinator._async_update_data()
    assert coordinator.update_interval.total_seconds() == MID_INTERVAL_MINUTES * 60
    assert coordinator.current_tier_minutes == MID_INTERVAL_MINUTES
    assert coordinator.delivered_codes == set()


async def test_deleted_shipments_are_never_shown(hass):
    coordinator = _coordinator(hass)
    data = await coordinator._async_update_data()
    codes = {p["barcode"] for p in data + coordinator.delivered}
    assert "00000000000000099" not in codes


async def test_a_shipment_in_both_buckets_is_listed_once(hass):
    coordinator = _coordinator(
        hass, tracking_body(active=[account_active()], archived=[account_active()])
    )
    data = await coordinator._async_update_data()
    assert len(data) == 1


async def test_parcels_without_a_number_are_dropped(hass):
    nameless = account_active()
    del nameless["shipmentId"]
    del nameless["items"][0]["itemId"]
    coordinator = _coordinator(hass, tracking_body(active=[nameless], archived=[]))
    assert await coordinator._async_update_data() == []


async def test_events_fire_after_the_first_refresh(hass):
    coordinator = _coordinator(hass, tracking_body(active=[], archived=[]))
    await coordinator._async_update_data()
    fired = []
    hass.bus.async_listen(f"{DOMAIN}_parcel_registered", fired.append)
    hass.bus.async_listen(f"{DOMAIN}_parcel_delivered", fired.append)
    coordinator._client.async_get_tracking.return_value = {
        "activeShipments": [account_active()],
        "archivedShipments": [],
    }
    await coordinator._async_update_data()
    coordinator._client.async_get_tracking.return_value = {
        "activeShipments": [],
        "archivedShipments": [account_delivered(ACCOUNT_ACTIVE_CODE)],
    }
    await coordinator._async_update_data()
    await hass.async_block_till_done()
    assert [e.event_type for e in fired] == [
        f"{DOMAIN}_parcel_registered",
        f"{DOMAIN}_parcel_delivered",
    ]


async def test_reauth_required_raises_auth_failed(hass):
    coordinator = _coordinator(hass, side_effect=PostNordAccountReauthRequired("x"))
    with pytest.raises(ConfigEntryAuthFailed):
        await coordinator._async_update_data()


async def test_api_error_is_update_failed(hass):
    coordinator = _coordinator(hass, side_effect=PostNordAccountApiError("x"))
    with pytest.raises(UpdateFailed):
        await coordinator._async_update_data()


async def test_compatibility_failure_warns_once_and_is_not_reauth(hass, caplog):
    coordinator = _coordinator(hass, side_effect=PostNordAccountCompatibilityError("x"))
    for _ in range(2):
        with pytest.raises(UpdateFailed):
            await coordinator._async_update_data()
    assert caplog.text.count("app key this integration uses was changed") == 1



def _outgoing(code, delivered=False):
    raw = account_delivered(code) if delivered else account_active(code)
    raw["userData"]["direction"] = "OUTGOING"
    return raw


async def test_outgoing_parcels_live_apart(hass):
    coordinator = _coordinator(
        hass,
        tracking_body(
            active=[account_active(), _outgoing("OUT1")],
            archived=[account_delivered(), _outgoing("OUT2", delivered=True)],
        ),
    )
    data = await coordinator._async_update_data()
    assert [p["barcode"] for p in data] == [ACCOUNT_ACTIVE_CODE]
    assert [p["barcode"] for p in coordinator.delivered] == [ACCOUNT_DELIVERED_CODE]
    assert [p["barcode"] for p in coordinator.outgoing_active] == ["OUT1"]
    assert [p["barcode"] for p in coordinator.outgoing_delivered] == ["OUT2"]


async def test_outgoing_parcel_at_a_pickup_point_is_not_awaiting_pickup(hass):
    waiting = _outgoing("OUT1")
    waiting["status"] = "AVAILABLE_FOR_DELIVERY"
    coordinator = _coordinator(hass, tracking_body(active=[waiting], archived=[]))
    data = await coordinator._async_update_data()
    assert data == []
    assert coordinator.outgoing_active[0]["pickup"] is True


async def test_outgoing_fires_only_the_sender_events(hass):
    coordinator = _coordinator(hass, tracking_body(active=[], archived=[]))
    await coordinator._async_update_data()
    fired = []
    for suffix in (
        "parcel_registered",
        "parcel_delivered",
        "outgoing_parcel_status_changed",
        "outgoing_parcel_delivered",
    ):
        hass.bus.async_listen(f"{DOMAIN}_{suffix}", fired.append)
    # First sighting of an outgoing parcel is never news.
    coordinator._client.async_get_tracking.return_value = {
        "activeShipments": [_outgoing("OUT1")],
        "archivedShipments": [],
    }
    await coordinator._async_update_data()
    coordinator._client.async_get_tracking.return_value = {
        "activeShipments": [],
        "archivedShipments": [_outgoing("OUT1", delivered=True)],
    }
    await coordinator._async_update_data()
    await hass.async_block_till_done()
    assert [e.event_type for e in fired] == [f"{DOMAIN}_outgoing_parcel_delivered"]
