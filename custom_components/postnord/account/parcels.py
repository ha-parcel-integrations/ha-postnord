"""Normalise a PostNord account-inbox shipment onto the canonical parcel shape.

The account list carries the same shipment object the app shows — the same
``status`` enum, ``items[].events`` and ``statusText`` as the public tracker —
so the field extractors are shared with :mod:`..tracking.parcels`. The
normaliser itself is not: which fields are trusted differs per source.

``planned_from``, ``pickup_point`` and ``dimensions`` stay ``None`` here until a
real active account parcel has shown where PostNord puts them; the first
sighting of a candidate key logs a one-shot WARNING (key names only) instead.
"""
from __future__ import annotations

import logging
from typing import Any

from ..const import ParcelStatus
from ..status import NEW_ISSUE_URL, map_parcel_status
from ..tracking.parcels import (
    _delivered_at,
    _flatten_events,
    _latest_event_is_out_for_delivery,
    _quantity,
    _weight_kg,
    build_history,
    to_iso_timestamp,
    tracking_url,
)

_LOGGER = logging.getLogger(__name__)

# Keys a populated account shipment is expected to carry. Presence is by key,
# so a present-but-null value stays silent.
_EXPECTED_FIELDS = ("shipmentId", "status", "statusText", "items")

# Candidate keys for fields this source does not publish yet.
_UNCONFIRMED_FIELDS = (
    "estimatedTimeOfArrival",
    "publicTimeOfArrival",
    "destinationDeliveryPoint",
    "deliveryPoint",
)

_warned_fields: set[str] = set()


def _warn_once(field: str, message: str) -> None:
    if field in _warned_fields:
        return
    _warned_fields.add(field)
    _LOGGER.warning(message, field, NEW_ISSUE_URL)


def check_account_shape(raw: dict) -> None:
    """One-shot WARNINGs for a missing expected key or an unconfirmed one."""
    for field in _EXPECTED_FIELDS:
        if field not in raw:
            _warn_once(
                field,
                "PostNord account shipment is missing the %r field we expected "
                "— the account response may have changed. Please report it "
                "(redacted diagnostics ideal): %s",
            )
    items = raw.get("items")
    if "items" in raw and not any(
        isinstance(item, dict) and "events" in item for item in items or []
    ):
        _warn_once(
            "items[].events",
            "PostNord account shipment is missing the %r field we expected — "
            "the account response may have changed. Please report it "
            "(redacted diagnostics ideal): %s",
        )
    for field in _UNCONFIRMED_FIELDS:
        if raw.get(field):
            _warn_once(
                field,
                "PostNord account shipment carries %r, which this integration "
                "does not use yet. Please help confirm its shape by opening an "
                "issue with redacted diagnostics: %s",
            )


def _first_item(raw: dict) -> dict:
    items = raw.get("items")
    if isinstance(items, list) and items and isinstance(items[0], dict):
        return items[0]
    return {}


def _account_weight_kg(raw: dict) -> float | None:
    """Shipment weight, falling back to the first item's own ``weight``."""
    weight = _weight_kg(raw)
    if weight is not None:
        return weight
    return _quantity(_first_item(raw).get("weight"), {"kg": 1, "g": 0.001})


def account_barcode(raw: dict) -> str | None:
    """Return the shipment's tracking number, or its first item's id."""
    barcode = raw.get("shipmentId") or _first_item(raw).get("itemId")
    return str(barcode) if barcode else None


def normalize_account_parcel(raw: dict, *, include_history: bool = False) -> dict:
    """Return the canonical parcel dict for one account shipment."""
    check_account_shape(raw)

    barcode = account_barcode(raw)
    status_code = raw.get("status") or _first_item(raw).get("status")
    status = map_parcel_status(status_code)

    events = _flatten_events(raw)
    if status is ParcelStatus.IN_TRANSIT and _latest_event_is_out_for_delivery(events):
        status = ParcelStatus.OUT_FOR_DELIVERY
    delivered = status is ParcelStatus.DELIVERED

    status_text = raw.get("statusText")
    raw_status = status_text.get("header") if isinstance(status_text, dict) else None

    consignor: Any = raw.get("consignor")
    consignee: Any = raw.get("consignee")

    return {
        "carrier": "PostNord",
        "barcode": barcode,
        "sender": (consignor.get("name") or None) if isinstance(consignor, dict) else None,
        "receiver": (consignee.get("name") or None) if isinstance(consignee, dict) else None,
        "status": status,
        "raw_status": raw_status or status_code,
        "delivered": delivered,
        "delivered_at": (
            (_delivered_at(events) or to_iso_timestamp(raw.get("deliveryDate")))
            if delivered
            else None
        ),
        "planned_from": None,
        "planned_to": None,
        "pickup": status is ParcelStatus.AT_PICKUP_POINT,
        "pickup_point": None,
        "url": tracking_url(barcode),
        "weight": _account_weight_kg(raw),
        "dimensions": None,
        "history": build_history(events) if include_history else None,
        "raw": raw,
    }
