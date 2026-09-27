"""Canonical parcel shape, status mapping and list helpers.

Everything in this module is a **pure function** — no I/O, no Home Assistant
objects beyond the config entry's options. That is deliberate: it keeps the
carrier-specific mapping (which you rewrite per carrier) apart from the
coordinator (which is nearly identical everywhere), and it makes the mapping
trivially unit-testable without spinning up HA.

The carrier-specific part is :func:`normalize_parcel`, implemented for
PostNord's real payload; the status map it uses lives in :mod:`..status`.
Everything else — the timestamp parsing, the history builder, the sort
contract, the delivered filter, the one-shot warnings for unmapped statuses
and unexpected payload shape — is suite-wide machinery and should be left alone.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from homeassistant.config_entries import ConfigEntry

from ..const import (
    CONF_DELIVERED_FILTER_AMOUNT,
    CONF_DELIVERED_FILTER_TYPE,
    DEFAULT_DELIVERED_FILTER_AMOUNT,
    DEFAULT_DELIVERED_FILTER_TYPE,
    HISTORY_MAX_EVENTS,
    TRACKING_URL,
    ParcelStatus,
)
from ..status import (
    NEW_ISSUE_URL,
    OTHER_STATUS,
    OUT_FOR_DELIVERY_EVENT_CODE,
    map_event_status,
    map_parcel_status,
)

_LOGGER = logging.getLogger(__name__)

# Pre-release data collection. The keyless ``recipientview`` *populated* shape
# has never been diffed against the captured ``findByIdentifier`` sample — empty
# responses are byte-identical, but a filled-in one has not been seen. So when a
# real shipment comes back missing a field we map, log the field name **once** at
# WARNING with the issue link — **keys only, never values** (``consignor`` /
# ``consignee`` carry PII). A real Nordic parcel then self-reports any reshape
# instead of the parser falling back silently. Remove once the populated shape is
# confirmed against real payloads.
_shape_fields_logged: set[str] = set()

# Top-level keys a populated shipment carries in our captured sample. Presence is
# tested by key (not truthiness) so a present-but-null field — a genuinely empty
# value, e.g. no ETA yet — does not warn; only a *missing* key, the signal that
# ``recipientview`` reshaped, does.
#
# ``estimatedTimeOfArrival`` is checked separately (see ``check_shipment_shape``):
# real delivered shipments confirmed 2026-08-24 drop the key entirely rather than
# nulling it, which the captured sample got wrong — an ETA is meaningless once
# delivered, so a *delivered* shipment omitting it is the real shape, not a gap.
_EXPECTED_FIELDS = (
    "consignor",
    "consignee",
    "statusText",
    "totalWeight",
)


def _warn_missing_field(field: str) -> None:
    """Log a mapped field absent from a populated shipment, once."""
    if field in _shape_fields_logged:
        return
    _shape_fields_logged.add(field)
    _LOGGER.warning(
        "PostNord shipment is missing the %r field we expected to map — the "
        "response shape may differ from our sample. Please help us confirm it by "
        "opening an issue and pasting this line (redacted diagnostics ideal): %s",
        field,
        NEW_ISSUE_URL,
    )


def check_shipment_shape(raw: dict) -> None:
    """One-shot WARNING per mapped field absent from a *populated* shipment.

    Skips the coordinator's pending placeholder (a bare ``{"shipmentId": code}``
    with no ``status``): only a shipment the API actually populated is checked, so
    an unscanned/unknown parcel never warns.
    """
    if "status" not in raw:
        return
    for field in _EXPECTED_FIELDS:
        if field not in raw:
            _warn_missing_field(field)
    if raw.get("status") != "DELIVERED" and "estimatedTimeOfArrival" not in raw:
        _warn_missing_field("estimatedTimeOfArrival")
    items = raw.get("items")
    if not items or not any(
        isinstance(item, dict) and "events" in item for item in items
    ):
        _warn_missing_field("items[].events")


def _event_status(event: dict) -> ParcelStatus | None:
    """Map one event to a canonical status, honouring the out-for-delivery code."""
    if str(event.get("eventCode")) == OUT_FOR_DELIVERY_EVENT_CODE:
        return ParcelStatus.OUT_FOR_DELIVERY
    return map_event_status(event.get("status"))


def parse_iso(value: str | None) -> datetime | None:
    """Parse an ISO 8601 string to an aware datetime, or ``None`` on failure.

    Naive values are treated as UTC so a list always sorts without crashing on
    a mixed set.
    """
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def to_iso_timestamp(value: Any) -> str | None:
    """Return an ISO 8601 string for an API timestamp field.

    Numbers are treated as **epoch milliseconds** — the common case for the
    consumer APIs in this suite. Strings pass through untouched; their
    consumers are guarded by :func:`parse_iso`. Adjust the numeric branch if
    your carrier stamps in seconds.
    """
    if value is None:
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(value / 1000, tz=timezone.utc).isoformat()
        except (OverflowError, OSError, ValueError):
            return None
    return str(value)


def format_dimensions(
    length: float | None, width: float | None, height: float | None
) -> dict[str, Any] | None:
    """Return the canonical ``dimensions`` dict, or ``None`` when incomplete.

    Units contract: **centimetres**, with ``text`` pre-formatted as
    ``"L x W x H cm"`` (integer values, lowercase ``x``) so dashboards can show
    a dimension without doing their own formatting. Convert before calling if
    the carrier reports millimetres or inches.
    """
    if length is None or width is None or height is None:
        return None
    return {
        "length": length,
        "width": width,
        "height": height,
        "text": f"{int(length)} x {int(width)} x {int(height)} cm",
    }


def build_history(
    events: list | None, *, max_events: int = HISTORY_MAX_EVENTS
) -> list[dict]:
    """Build the canonical ``history`` list from the carrier's event list.

    Each entry is ``{timestamp, status, raw_status}`` — identical across all
    suite carriers, and top-level (not under ``raw``) so it survives the
    aggregator's ``strip_raw()``. ``raw_status`` is the carrier's own text, or
    its event code when the API has no human-readable text. Sorted oldest →
    newest and capped to the most recent ``max_events``.

    PostNord events (from ``items[].events``) carry ``eventTime`` (ISO 8601), a
    machine ``status`` from the same enum as the shipment, and human
    ``eventDescription`` text — so, unlike most carriers here, ``raw_status`` is
    real prose rather than a bare code.
    """
    parseable: list[tuple[datetime, dict]] = []
    unparseable: list[dict] = []
    for event in events or []:
        if not isinstance(event, dict):
            continue
        timestamp = to_iso_timestamp(event.get("eventTime"))
        if not timestamp:
            continue
        entry = {
            "timestamp": timestamp,
            "status": _event_status(event),
            "raw_status": event.get("eventDescription") or event.get("eventCode"),
        }
        if event.get("status") == OTHER_STATUS:
            entry["_carry"] = True
        parsed = parse_iso(timestamp)
        if parsed is None:
            unparseable.append(entry)
        else:
            parseable.append((parsed, entry))
    parseable.sort(key=lambda item: item[0])
    ordered = [entry for _, entry in parseable] + unparseable
    # A notification or intermediate scan (``OTHER``) does not move the parcel,
    # so it keeps the status the parcel already had.
    previous: ParcelStatus | None = None
    for entry in ordered:
        if entry.pop("_carry", False) and entry["status"] is None:
            entry["status"] = previous
        previous = entry["status"] or previous
    return ordered[-max_events:]


def tracking_url(tracking_code: str | None) -> str | None:
    """Construct the consumer tracking deep-link for a parcel."""
    if not tracking_code:
        return None
    return TRACKING_URL.format(tracking_code=tracking_code)


def normalize_parcel(raw: dict, *, include_history: bool = False) -> dict:
    """Return a carrier-agnostic parcel dict with the payload under ``raw``.

    The field lookups map PostNord's real ``recipientview`` payload onto the
    canonical shape. The **keys of the returned dict are the contract**: every
    carrier in the suite returns exactly these, in this order, and the aggregator
    and cross-carrier dashboards depend on it. A key is ``None`` when PostNord
    does not expose it — never omitted.

    Rules worth keeping when you rewrite the body:

    * ``status`` is canonical, ``raw_status`` is the carrier's own text.
    * A delivered parcel has ``delivered_at`` set and ``planned_from`` /
      ``planned_to`` cleared — the ETA is meaningless once it has arrived.
    * ``planned_to`` is ``None`` for a point estimate; only fill it when the
      carrier genuinely reports a *window*.
    * ``weight`` is kilograms, ``dimensions`` centimetres (see
      :func:`format_dimensions`).
    * ``history`` is ``None`` when the option is off — the key still exists.
    """
    # Pre-release: a populated shipment missing a field we map self-reports the
    # recipientview reshape gap (see check_shipment_shape).
    check_shipment_shape(raw)

    tracking_code = raw.get("shipmentId")
    status_code = raw.get("status")
    status = map_parcel_status(status_code)

    # Events live under each item; flatten them into one shipment-level list.
    events = _flatten_events(raw)

    if status is ParcelStatus.IN_TRANSIT and _latest_event_is_out_for_delivery(events):
        status = ParcelStatus.OUT_FOR_DELIVERY
    status = settle_return(status, events)
    delivered = status is ParcelStatus.DELIVERED

    # ETA is a single instant (``estimatedTimeOfArrival``), so it is a point
    # estimate — ``planned_to`` stays None (only real windows fill it).
    planned_from = to_iso_timestamp(raw.get("estimatedTimeOfArrival"))

    # A ``statusText`` is ``{"header": ..., "body": ...}`` — the header is the
    # short human line; fall back to the machine code when it is absent.
    status_text = raw.get("statusText") or {}
    raw_status = status_text.get("header") if isinstance(status_text, dict) else None

    return {
        "carrier": "PostNord",
        "barcode": tracking_code,
        "sender": (raw.get("consignor") or {}).get("name") or None,
        "receiver": (raw.get("consignee") or {}).get("name") or None,
        "status": status,
        "raw_status": raw_status or status_code,
        "delivered": delivered,
        "delivered_at": _delivered_at(events) if delivered else None,
        "planned_from": None if delivered else planned_from,
        "planned_to": None,
        "pickup": status is ParcelStatus.AT_PICKUP_POINT,
        "pickup_point": _pickup_point_name(raw, events),
        "url": tracking_url(tracking_code),
        "weight": _weight_kg(raw),
        "dimensions": _dimensions_cm(raw),
        "history": build_history(events) if include_history else None,
        "raw": raw,
    }


def _flatten_events(raw: dict) -> list[dict]:
    """Collect every item's events into one shipment-level list."""
    events: list[dict] = []
    for item in raw.get("items") or []:
        if isinstance(item, dict):
            events.extend(e for e in (item.get("events") or []) if isinstance(e, dict))
    return events


def _newest_event(events: list[dict]) -> dict | None:
    """Return the newest event by ``eventTime``, or ``None``."""
    dated = [
        (parsed, event)
        for event in events
        if (parsed := parse_iso(to_iso_timestamp(event.get("eventTime")))) is not None
    ]
    return max(dated, key=lambda item: item[0])[1] if dated else None


def settle_return(status: ParcelStatus, events: list[dict]) -> ParcelStatus:
    """Report a return that arrived back at the sender as ``delivered``.

    PostNord ends a return leg with a ``DELIVERED`` event while the shipment
    keeps saying ``RETURNED``. Left ``returning``, a finished return would sit
    among the active parcels forever; ``raw_status`` still names the return.
    """
    if status is not ParcelStatus.RETURNING:
        return status
    newest = _newest_event(events)
    if newest is not None and newest.get("status") == "DELIVERED":
        return ParcelStatus.DELIVERED
    return status


def _latest_event_is_out_for_delivery(events: list[dict]) -> bool:
    """Whether the newest event is PostNord's "delivery in progress" code."""
    dated = [
        (parsed, event)
        for event in events
        if (parsed := parse_iso(to_iso_timestamp(event.get("eventTime")))) is not None
    ]
    if not dated:
        return False
    newest = max(dated, key=lambda item: item[0])[1]
    return str(newest.get("eventCode")) == OUT_FOR_DELIVERY_EVENT_CODE


def _delivered_at(events: list[dict]) -> str | None:
    """Return the ISO timestamp of the delivery event, newest if several."""
    times = [
        ts
        for event in events
        if (event.get("status") == "DELIVERED")
        and (ts := to_iso_timestamp(event.get("eventTime")))
    ]
    return max(times) if times else None


def _measurement(raw: dict, key: str) -> dict:
    """Return the first item's ``statedMeasurement``/``assessedMeasurement``."""
    for item in raw.get("items") or []:
        if isinstance(item, dict) and isinstance(item.get(key), dict):
            return item[key]
    return {}


def _quantity(quantity: Any, factors: dict[str, float]) -> float | None:
    """Convert a ``{"value": "2.5", "unit": ...}`` quantity using ``factors``."""
    if not isinstance(quantity, dict):
        return None
    factor = factors.get(str(quantity.get("unit")).lower())
    try:
        value = float(quantity.get("value"))
    except (TypeError, ValueError):
        return None
    return value * factor if factor is not None else None


def _weight_kg(raw: dict) -> float | None:
    """Return the shipment weight in kilograms, or ``None``.

    PostNord reports weight as ``{"value": "2.5", "unit": "kg"|"g"}`` under
    ``totalWeight``. Before the carrier has totalled it, the sender's declared
    weight (``statedMeasurement``) and then PostNord's own measured weight
    (``assessedMeasurement``) on the first item are used instead.
    """
    factors = {"kg": 1, "g": 0.001}
    for weight in (
        raw.get("totalWeight"),
        raw.get("assessedWeight"),
        _measurement(raw, "statedMeasurement").get("weight"),
        _measurement(raw, "assessedMeasurement").get("weight"),
        _first_item_value(raw, "weight"),
    ):
        value = _quantity(weight, factors)
        if value is not None:
            return value
    return None


def _first_item_value(raw: dict, key: str) -> Any:
    """Return ``items[0][key]``, or ``None``."""
    items = raw.get("items")
    if isinstance(items, list) and items and isinstance(items[0], dict):
        return items[0].get(key)
    return None


def _dimensions_cm(raw: dict) -> dict[str, Any] | None:
    """Return the canonical dimensions from the item's L×W×H, or ``None``.

    PostNord reports each axis in metres, under ``statedMeasurement`` on the
    tracker and ``dimensions`` on the account list; the contract is
    centimetres.
    """
    for key in ("statedMeasurement", "dimensions"):
        measured = _measurement(raw, key)
        axes = [
            _quantity(measured.get(axis), {"m": 100, "cm": 1, "mm": 0.1})
            for axis in ("length", "width", "height")
        ]
        if all(axis is not None for axis in axes):
            return format_dimensions(*(round(axis, 1) for axis in axes))
    return None


def _pickup_point_name(raw: dict, events: list[dict]) -> str | None:
    """Return the pickup point's name, or ``None``.

    The shipment names it under ``destinationDeliveryPoint`` (seen on the
    account list) or ``deliveryPoint``; failing both, the location of the
    newest "available for pickup" event is where the parcel actually waited.
    """
    for key in ("destinationDeliveryPoint", "deliveryPoint"):
        point = raw.get(key)
        if isinstance(point, dict) and point.get("name"):
            return point["name"]
    waiting = [
        (parsed, event)
        for event in events
        if event.get("status") == "AVAILABLE_FOR_DELIVERY"
        and (parsed := parse_iso(to_iso_timestamp(event.get("eventTime")))) is not None
    ]
    if not waiting:
        return None
    location = max(waiting, key=lambda item: item[0])[1].get("location")
    return (location.get("locationName") or None) if isinstance(location, dict) else None


def sort_parcels_by_ts(
    parcels: list[dict], key_field: str, *, descending: bool = False
) -> list[dict]:
    """Return normalised parcels sorted by the ISO timestamp at ``key_field``.

    The suite's sort contract: incoming/outgoing ascending on ``planned_from``,
    delivered descending on ``delivered_at``. Parcels whose value is missing or
    unparseable always sort to the end, regardless of ``descending``.
    """
    with_ts: list[tuple[datetime, dict]] = []
    without_ts: list[dict] = []
    for parcel in parcels:
        parsed = parse_iso(parcel.get(key_field))
        if parsed is None:
            without_ts.append(parcel)
        else:
            with_ts.append((parsed, parcel))
    with_ts.sort(key=lambda item: item[0], reverse=descending)
    return [parcel for _, parcel in with_ts] + without_ts


def apply_delivered_filter(parcels: list[dict], entry: ConfigEntry) -> list[dict]:
    """Trim the delivered list per the entry's retention option.

    ``parcels`` must already be sorted newest-first. ``days`` keeps deliveries
    from the last N days (an unparseable ``delivered_at`` is kept rather than
    silently dropped); the ``parcels`` type keeps the N most recent. Parcels
    stay *tracked* either way — this only controls what the delivered sensor
    shows.
    """
    options = entry.options
    filter_type = options.get(
        CONF_DELIVERED_FILTER_TYPE, DEFAULT_DELIVERED_FILTER_TYPE
    )
    amount = int(
        options.get(CONF_DELIVERED_FILTER_AMOUNT, DEFAULT_DELIVERED_FILTER_AMOUNT)
    )
    if filter_type == "days":
        cutoff = datetime.now(timezone.utc) - timedelta(days=amount)
        return [
            parcel
            for parcel in parcels
            if (parsed := parse_iso(parcel.get("delivered_at"))) is None
            or parsed >= cutoff
        ]
    return parcels[:amount]
