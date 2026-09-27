"""Setting up an account entry, alone and beside the tracking hub."""
from unittest.mock import AsyncMock, patch

from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntryState
from homeassistant.helpers import device_registry as dr
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.postnord.account.client import PostNordAccountReauthRequired
from custom_components.postnord.const import (
    CONF_ACCESS_TOKEN,
    CONF_EMAIL,
    CONF_PARCELS,
    CONF_REFRESH_TOKEN,
    CONF_SOURCE,
    DOMAIN,
    SOURCE_ACCOUNT,
)
from custom_components.postnord.diagnostics import async_get_config_entry_diagnostics

from ..payloads import active_sample
from .payloads import tracking_body

EMAIL = "someone@example.com"
_TRACKING = "custom_components.postnord.account.client.PostNordAccountClient.async_get_tracking"


def _buckets():
    body = tracking_body()
    return {k: body[k] for k in ("activeShipments", "archivedShipments")}


def _account(hass) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=f"account:{EMAIL}",
        title=EMAIL,
        data={
            CONF_SOURCE: SOURCE_ACCOUNT,
            CONF_EMAIL: EMAIL,
            CONF_ACCESS_TOKEN: "access-1",
            CONF_REFRESH_TOKEN: "refresh-1",
        },
        options={},
    )
    entry.add_to_hass(hass)
    return entry


def _tracking(hass) -> MockConfigEntry:
    # No data at all: an entry created before the account source existed.
    entry = MockConfigEntry(
        domain=DOMAIN, data={}, unique_id=DOMAIN, options={CONF_PARCELS: []}
    )
    entry.add_to_hass(hass)
    return entry


async def _setup(hass, entry, buckets=None):
    with patch(_TRACKING, new=AsyncMock(return_value=buckets or _buckets())):
        result = await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    return result


async def test_account_entry_loads_without_services(hass):
    entry = _account(hass)
    assert await _setup(hass, entry)
    assert entry.state is ConfigEntryState.LOADED
    assert not hass.services.has_service(DOMAIN, "track_parcel")

    device = next(iter(dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)))
    assert device.name == f"PostNord ({EMAIL})"
    incoming = hass.states.get(f"sensor.postnord_{EMAIL.replace('@', '_').replace('.', '_')}_incoming_parcels")
    assert incoming is not None
    assert incoming.state == "1"

    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_refused_login_at_setup_starts_reauth(hass):
    entry = _account(hass)
    with patch(_TRACKING, new=AsyncMock(side_effect=PostNordAccountReauthRequired("x"))):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress()
    assert any(flow["context"]["source"] == SOURCE_REAUTH for flow in flows)


async def test_refreshed_tokens_are_written_back(hass):
    entry = _account(hass)

    async def rotate(self):
        await self._token_callback(
            {CONF_ACCESS_TOKEN: "access-2", CONF_REFRESH_TOKEN: "refresh-2"}
        )
        return _buckets()

    with patch(_TRACKING, new=rotate):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    assert entry.data[CONF_ACCESS_TOKEN] == "access-2"
    assert entry.data[CONF_REFRESH_TOKEN] == "refresh-2"
    assert entry.data[CONF_SOURCE] == SOURCE_ACCOUNT


async def test_services_survive_an_account_unloading(hass):
    tracking = _tracking(hass)
    account = _account(hass)
    # Setting up the domain loads every entry of it, so patch both sources.
    with (
        patch(
            "custom_components.postnord.tracking.api.PostNordApiClient.async_get_parcel",
            new=AsyncMock(return_value=active_sample()),
        ),
        patch(_TRACKING, new=AsyncMock(return_value=_buckets())),
    ):
        assert await hass.config_entries.async_setup(tracking.entry_id)
        await hass.async_block_till_done()
    assert account.state is ConfigEntryState.LOADED
    assert hass.services.has_service(DOMAIN, "track_parcel")

    assert await hass.config_entries.async_unload(account.entry_id)
    await hass.async_block_till_done()
    assert hass.services.has_service(DOMAIN, "track_parcel")

    # The service only ever writes to the tracking hub.
    await hass.services.async_call(
        DOMAIN, "track_parcel", {"tracking_code": "EXAMPLE111111"}, blocking=True
    )
    assert tracking.options[CONF_PARCELS] == [{"tracking_code": "EXAMPLE111111"}]
    assert CONF_PARCELS not in account.options

    assert await hass.config_entries.async_unload(tracking.entry_id)
    await hass.async_block_till_done()
    assert not hass.services.has_service(DOMAIN, "track_parcel")


async def test_diagnostics_never_carry_credentials_or_pii(hass):
    entry = _account(hass)
    assert await _setup(hass, entry)
    result = await async_get_config_entry_diagnostics(hass, entry)
    dumped = repr(result)
    for secret in ("access-1", "refresh-1", EMAIL, "Jane Doe", "Stockholm", "Example Shop"):
        assert secret not in dumped
    assert "1ecf161be47543b19fce03f953a6e8d2" not in dumped
    assert result["entry_data"][CONF_SOURCE] == SOURCE_ACCOUNT
    assert await hass.config_entries.async_unload(entry.entry_id)
