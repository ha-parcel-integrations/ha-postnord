"""PostNord parcel tracker custom component for Home Assistant."""
from __future__ import annotations

import logging
from dataclasses import dataclass

from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .account.client import PostNordAccountClient
from .account.coordinator import PostNordAccountCoordinator
from .const import (
    CONF_ACCESS_TOKEN,
    CONF_REFRESH_TOKEN,
    CONF_SOURCE,
    DOMAIN,
    PLATFORMS,
    SOURCE_ACCOUNT,
    SOURCE_TRACKING,
)
from .services import async_setup_services, async_unload_services
from .tracking.api import PostNordApiClient
from .tracking.coordinator import PostNordCoordinator

_LOGGER = logging.getLogger(__name__)


@dataclass
class PostNordData:
    """Runtime data attached to the PostNord config entry."""

    client: PostNordApiClient | PostNordAccountClient
    coordinator: PostNordCoordinator | PostNordAccountCoordinator


type PostNordConfigEntry = ConfigEntry[PostNordData]


async def async_setup_entry(hass: HomeAssistant, entry: PostNordConfigEntry) -> bool:
    """Set up PostNord from a config entry."""
    is_account = entry.data.get(CONF_SOURCE, SOURCE_TRACKING) == SOURCE_ACCOUNT
    if is_account:
        async def async_store_tokens(tokens: dict[str, str]) -> None:
            hass.config_entries.async_update_entry(
                entry,
                data={
                    **entry.data,
                    CONF_ACCESS_TOKEN: tokens[CONF_ACCESS_TOKEN],
                    CONF_REFRESH_TOKEN: tokens[CONF_REFRESH_TOKEN],
                },
            )

        client = PostNordAccountClient(
            async_get_clientsession(hass),
            access_token=entry.data.get(CONF_ACCESS_TOKEN),
            refresh_token=entry.data.get(CONF_REFRESH_TOKEN),
            token_callback=async_store_tokens,
        )
        coordinator = PostNordAccountCoordinator(hass, client, entry)
    else:
        # Keyless: the client sends PostNord's own fixed public web-client key
        # (``X-Bap-Key``), so there is no per-user credential on the entry.
        # Entries that predate CONF_SOURCE are tracking hubs.
        client = PostNordApiClient(async_get_clientsession(hass))
        coordinator = PostNordCoordinator(hass, client, entry)

    # Fetch initial data here, before forwarding to platforms. Raising
    # ConfigEntryNotReady from a forwarded platform is too late for HA to catch
    # cleanly (it logs a warning and half-sets-up the entry); doing the first
    # refresh here lets a transient failure fail the whole entry so HA retries
    # it with backoff.
    await coordinator.async_config_entry_first_refresh()

    entry.runtime_data = PostNordData(client=client, coordinator=coordinator)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Apply option changes (added/removed parcels, history) live via a
    # coordinator refresh — no reload — so per-parcel sensors appear and
    # disappear immediately. The update listener does NOT reload, so it does
    # not trip the config-entry-listener deprecation. This is also the resume
    # path after polling fully suspended: adding a parcel back triggers this
    # refresh, which recomputes the tier and re-arms scheduling.
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))

    if not is_account:
        async_setup_services(hass)

    return True


async def _async_options_updated(
    hass: HomeAssistant, entry: PostNordConfigEntry
) -> None:
    """Apply changed options by refreshing the coordinator."""
    await entry.runtime_data.coordinator.async_request_refresh()


async def async_unload_entry(hass: HomeAssistant, entry: PostNordConfigEntry) -> bool:
    """Unload the PostNord config entry."""
    if not await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        return False
    # The services belong to the tracking hub; an account entry unloading must
    # not take them away from a tracking hub that is still loaded.
    others_loaded = any(
        other.entry_id != entry.entry_id
        and other.state is ConfigEntryState.LOADED
        and other.data.get(CONF_SOURCE, SOURCE_TRACKING) == SOURCE_TRACKING
        for other in hass.config_entries.async_entries(DOMAIN)
    )
    if entry.data.get(CONF_SOURCE, SOURCE_TRACKING) == SOURCE_TRACKING and not others_loaded:
        async_unload_services(hass)
    return True
