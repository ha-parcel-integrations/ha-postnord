"""Coordinator for the account inbox source.

Exposes the same surface as the tracking coordinator — ``data`` is the active
incoming parcels, ``delivered`` the retained delivered ones — so the sensor,
calendar and diagnostics platforms need no source switch. Parcels the account
sent live apart in ``outgoing_active`` / ``outgoing_delivered``, as on bpost.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from ..const import (
    CONF_INCLUDE_HISTORY,
    DEFAULT_INCLUDE_HISTORY,
    DOMAIN,
    MID_INTERVAL_MINUTES,
    ParcelStatus,
)
from ..events import (
    fire_incoming_change_events,
    fire_outgoing_change_events,
    snapshot_delivery_times,
    snapshot_states,
)
from ..status import NEW_ISSUE_URL
from ..tracking.parcels import apply_delivered_filter, sort_parcels_by_ts
from .client import (
    PostNordAccountApiError,
    PostNordAccountClient,
    PostNordAccountCompatibilityError,
    PostNordAccountReauthRequired,
)
from .parcels import is_outgoing, normalize_account_parcel

_LOGGER = logging.getLogger(__name__)

_compatibility_warned = False


def _warn_compatibility() -> None:
    """Warn once when PostNord stops accepting the app's gateway key."""
    global _compatibility_warned
    if _compatibility_warned:
        return
    _compatibility_warned = True
    _LOGGER.warning(
        "PostNord refused a freshly renewed account token, which means the "
        "app key this integration uses was changed. Logging in again will not "
        "fix it — please report it at %s",
        NEW_ISSUE_URL,
    )


class PostNordAccountCoordinator(DataUpdateCoordinator[list[dict]]):
    """Refresh the whole account inbox; accounts never suspend polling."""

    def __init__(
        self, hass: HomeAssistant, client: PostNordAccountClient, entry: ConfigEntry
    ) -> None:
        """Initialise a continuously-polled account inbox coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} account",
            update_interval=timedelta(minutes=MID_INTERVAL_MINUTES),
        )
        self._client = client
        self.delivered: list[dict] = []
        self.outgoing_active: list[dict] = []
        self.outgoing_delivered: list[dict] = []
        # None on the first update deliberately suppresses historical events.
        self._known_state: dict[str, ParcelStatus] | None = None
        self._known_delivery_times: dict[str, tuple[str | None, str | None]] | None = None
        self._known_outgoing_state: dict[str, ParcelStatus] | None = None
        self._cached_device_id: str | None = None
        # The inbox is one batched call, so no parcel is ever skipped.
        self._delivered_codes: set[str] = set()
        self.last_success_time: datetime | None = None
        self.current_tier_minutes: int | None = MID_INTERVAL_MINUTES

    @property
    def delivered_codes(self) -> set[str]:
        """Account polls are batched; no individual parcel is skipped."""
        return self._delivered_codes

    def _device_id(self) -> str | None:
        """Resolve the account device id for event and device-trigger payloads."""
        if self._cached_device_id is not None:
            return self._cached_device_id
        registry = dr.async_get(self.hass)
        device = next(
            iter(dr.async_entries_for_config_entry(registry, self.config_entry.entry_id)),
            None,
        )
        if device is not None:
            self._cached_device_id = device.id
        return self._cached_device_id

    async def _async_update_data(self) -> list[dict]:
        try:
            buckets = await self._client.async_get_tracking()
        except PostNordAccountReauthRequired as err:
            raise ConfigEntryAuthFailed("PostNord account needs a new login") from err
        except PostNordAccountCompatibilityError as err:
            _warn_compatibility()
            raise UpdateFailed("PostNord refused the app key") from err
        except PostNordAccountApiError as err:
            raise UpdateFailed("Unable to update the PostNord account inbox") from err

        include_history = bool(
            self.config_entry.options.get(CONF_INCLUDE_HISTORY, DEFAULT_INCLUDE_HISTORY)
        )
        # Parcels the user deleted in the app sit in ``deletedShipments``,
        # which is deliberately never read.
        # The same shipment can surface in both buckets while PostNord archives
        # it; keep the first, which is the active one.
        incoming: dict[str, dict] = {}
        outgoing: dict[str, dict] = {}
        for raw in buckets["activeShipments"] + buckets["archivedShipments"]:
            parcel = normalize_account_parcel(raw, include_history=include_history)
            target = outgoing if is_outgoing(raw) else incoming
            if parcel["barcode"] and parcel["barcode"] not in target:
                target[parcel["barcode"]] = parcel

        self.outgoing_active = sort_parcels_by_ts(
            [p for p in outgoing.values() if not p["delivered"]], "planned_from"
        )
        self.outgoing_delivered = apply_delivered_filter(
            sort_parcels_by_ts(
                [p for p in outgoing.values() if p["delivered"]],
                "delivered_at",
                descending=True,
            ),
            self.config_entry,
        )
        fire_outgoing_change_events(
            self.hass,
            list(outgoing.values()),
            self._known_outgoing_state,
            self._device_id(),
        )
        self._known_outgoing_state = snapshot_states(list(outgoing.values()))

        active = [p for p in incoming.values() if not p["delivered"]]
        delivered = [p for p in incoming.values() if p["delivered"]]
        self.delivered = apply_delivered_filter(
            sort_parcels_by_ts(delivered, "delivered_at", descending=True),
            self.config_entry,
        )
        normalized_active = sort_parcels_by_ts(active, "planned_from")

        # Active + delivered, combined so the transition to delivered is
        # visible in one set — same shape the tracking coordinator diffs.
        seen = normalized_active + self.delivered
        fire_incoming_change_events(
            self.hass,
            seen,
            self._known_state,
            self._known_delivery_times,
            self._device_id(),
        )
        self._known_state = snapshot_states(seen)
        self._known_delivery_times = snapshot_delivery_times(seen)

        self.last_success_time = datetime.now(timezone.utc)
        return normalized_active
