"""Normalise a PostNord account-inbox shipment onto the canonical parcel shape.

The account list carries the same shipment object the app shows — the same
``status`` enum, ``items[].events`` and ``statusText`` as the public tracker —
so the field extractors are shared with :mod:`..tracking.parcels`. The
normaliser itself is not: which fields are trusted differs per source.

Where the account list differs from the tracker — confirmed on a real account
response 2026-09-27 — is in the placement: ``statusText`` sits on each item,
not on the shipment; measurements are ``items[].dimensions`` / ``items[].weight``;
and the pickup point is ``destinationDeliveryPoint.name``. The ETA is read from
the tracker's ``estimatedTimeOfArrival`` key; no active account parcel has
confirmed it yet.
"""
from __future__ import annotations

import logging
from typing import Any

from ..const import ParcelStatus
from ..status import NEW_ISSUE_URL, map_parcel_status
from ..tracking.parcels import (
    _delivered_at,
    _dimensions_cm,
    _flatten_events,
    _latest_event_is_out_for_delivery,
    _pickup_point_name,
    _weight_kg,
    build_history,
    settle_return,
    to_iso_timestamp,
    tracking_url,
)

_LOGGER = logging.getLogger(__name__)

# Keys a populated account shipment is expected to carry. Presence is by key,
# so a present-but-null value stays silent.
_EXPECTED_FIELDS = ("shipmentId", "status", "items")

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
    for key in ("events", "statusText"):
        if "items" in raw and not any(
            isinstance(item, dict) and key in item for item in items or []
        ):
            _warn_once(
                f"items[].{key}",
                "PostNord account shipment is missing the %r field we expected "
                "— the account response may have changed. Please report it "
                "(redacted diagnostics ideal): %s",
            )
    user_data = raw.get("userData")
    direction = user_data.get("direction") if isinstance(user_data, dict) else None
    if direction not in (None, "INCOMING", "OUTGOING"):
        # An enum value, not PII; still counted as incoming so nothing vanishes.
        _warn_once(
            f"userData.direction={direction}",
            "PostNord account shipment has an unseen %r; it is shown as an "
            "incoming parcel. Please report it: %s",
        )


def _first_item(raw: dict) -> dict:
    items = raw.get("items")
    if isinstance(items, list) and items and isinstance(items[0], dict):
        return items[0]
    return {}


def is_outgoing(raw: dict) -> bool:
    """Whether the account sent this parcel rather than receives it.

    Mirrors the PostNord app: only ``userData.direction == "OUTGOING"`` is
    outgoing; anything else, including a missing value, counts as incoming so
    a parcel can never disappear.
    """
    user_data = raw.get("userData")
    return isinstance(user_data, dict) and user_data.get("direction") == "OUTGOING"


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
    status = settle_return(status, events)
    delivered = status is ParcelStatus.DELIVERED

    status_text = _first_item(raw).get("statusText") or raw.get("statusText")
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
        # A single instant, as on the tracker; only a real window fills
        # planned_to. Meaningless once delivered.
        "planned_from": None if delivered else to_iso_timestamp(
            raw.get("estimatedTimeOfArrival") or raw.get("publicTimeOfArrival")
        ),
        "planned_to": None,
        "pickup": status is ParcelStatus.AT_PICKUP_POINT,
        "pickup_point": _pickup_point_name(raw, events),
        "url": tracking_url(barcode),
        "weight": _weight_kg(raw),
        "dimensions": _dimensions_cm(raw),
        "history": build_history(events) if include_history else None,
        "raw": raw,
    }
