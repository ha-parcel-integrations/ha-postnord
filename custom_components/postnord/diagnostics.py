"""Diagnostics support for the PostNord parcel tracker integration."""
from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from . import PostNordConfigEntry

# Diagnostics are pasted into public issues, so redact anything that
# identifies a person, an address or a specific parcel. Over-redacting is
# cheap; under-redacting leaks a user's home address into a GitHub thread.
#
# Field names redacted from diagnostics. PostNord's raw payload carries the
# sender under ``consignor`` and the recipient under ``consignee`` (each a
# ``{name, address:{city, postCode, countryCode}}`` block), plus the canonical
# fields we publish. There is no user credential to redact — the integration is
# keyless (a fixed built-in web key, not stored on the entry).
TO_REDACT = {
    # canonical fields we publish ourselves
    "tracking_code",
    "barcode",
    "sender",
    "receiver",
    "url",
    # PostNord raw payload blocks
    "consignor",
    "consignee",
    "recipient",
    "address",
    "postalCode",
    "postCode",
    "postal_code",
    "city",
    "street",
    "email",
    "name",
    "signature",
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: PostNordConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for the PostNord config entry."""
    coordinator = entry.runtime_data.coordinator

    return {
        "entry_options": async_redact_data(dict(entry.options), TO_REDACT),
        "counts": {
            "incoming_active": len(coordinator.data or []),
            "delivered": len(coordinator.delivered or []),
        },
        "incoming": async_redact_data(coordinator.data or [], TO_REDACT),
        "delivered": async_redact_data(coordinator.delivered or [], TO_REDACT),
    }
