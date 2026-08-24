"""Canonical parcel shape, status mapping and list helpers.

Everything in this module is a **pure function** — no I/O, no Home Assistant
objects beyond the config entry's options. That is deliberate: it keeps the
carrier-specific mapping (which you rewrite per carrier) apart from the
coordinator (which is nearly identical everywhere), and it makes the mapping
trivially unit-testable without spinning up HA.

Two things here are carrier-specific: :data:`_STATUS_MAP` and
:func:`normalize_parcel`, both implemented for PostNord's real payload.
Everything else — the timestamp parsing, the history builder, the sort
contract, the delivered filter, the one-shot warnings for unmapped statuses
and unexpected payload shape — is suite-wide machinery and should be left alone.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from homeassistant.config_entries import ConfigEntry

from .const import (
    CONF_DELIVERED_FILTER_AMOUNT,
    CONF_DELIVERED_FILTER_TYPE,
    DEFAULT_DELIVERED_FILTER_AMOUNT,
    DEFAULT_DELIVERED_FILTER_TYPE,
    HISTORY_MAX_EVENTS,
    TRACKING_URL,
    ParcelStatus,
)

_LOGGER = logging.getLogger(__name__)

# Where users report a status we do not map yet. Rewritten by the bootstrap
# script; it must point at the carrier's own repo so the log line is
# copy-pasteable straight into a new issue.
#
# The ``?template=`` parameter matters: without it the link opens a blank form,
# and the report comes back missing the version and the log line we need.
NEW_ISSUE_URL = (
    "https://github.com/ha-parcel-integrations/ha-postnord/issues/new"
    "?template=unrecognised_status.yml"
)

# PostNord's machine ``status`` enum (shared by the shipment, its items, and
# each event) mapped onto the canonical vocabulary. The full set observed in the
# 8.76 Android app is: CREATED, INFORMED, EN_ROUTE, AVAILABLE_FOR_DELIVERY,
# DELIVERY, DELIVERED, DELIVERY_IMPOSSIBLE, EXPECTED_DELAY, RETURNED, STOPPED,
# OTHER. ``OTHER`` is deliberately left unmapped (it is PostNord's own
# catch-all) so it surfaces as ``unknown`` + a one-shot warning rather than
# being mapped wrongly.
_STATUS_MAP: dict[str, ParcelStatus] = {
    "CREATED": ParcelStatus.REGISTERED,
    "INFORMED": ParcelStatus.REGISTERED,
    "EN_ROUTE": ParcelStatus.IN_TRANSIT,
    "AVAILABLE_FOR_DELIVERY": ParcelStatus.AT_PICKUP_POINT,
    "DELIVERY": ParcelStatus.OUT_FOR_DELIVERY,
    "DELIVERED": ParcelStatus.DELIVERED,
    "DELIVERY_IMPOSSIBLE": ParcelStatus.PROBLEM,
    "EXPECTED_DELAY": ParcelStatus.PROBLEM,
    "RETURNED": ParcelStatus.RETURNING,
    "STOPPED": ParcelStatus.PROBLEM,
}

# Status codes we have already warned about, so each unmapped one is logged
# only once per HA session instead of on every poll.
_unmapped_statuses_logged: set[str] = set()


def _warn_unmapped_status(code: str) -> None:
    """Log an unmapped carrier status once, with a copy-paste issue link."""
    if code in _unmapped_statuses_logged:
        return
    _unmapped_statuses_logged.add(code)
    _LOGGER.warning(
        "Unrecognised PostNord status — help us map it. Open an issue "
        "and paste this line: %s\n  status=%s → reported as 'unknown'",
        NEW_ISSUE_URL,
        code,
    )


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


def map_parcel_status(code: str | None) -> ParcelStatus:
    """Map a carrier status code to a canonical :class:`ParcelStatus`.

    ``None`` (a not-yet-scanned parcel) reports ``unknown`` silently; an
    unrecognised code reports ``unknown`` with a one-shot warning.
    """
    if not code:
        return ParcelStatus.UNKNOWN
    mapped = _STATUS_MAP.get(code)
    if mapped is not None:
        return mapped
    _warn_unmapped_status(code)
    return ParcelStatus.UNKNOWN


def map_event_status(code: str | None) -> ParcelStatus | None:
    """Map a history entry's status code to a canonical status, or ``None``.

    Unmapped codes keep ``status: null`` on the history entry (rather than
    ``unknown``, so a consumer can tell "no mapping" from "mapped to unknown")
    and warn once, reusing the parcel-status one-shot set.
    """
    if not code:
        return None
    mapped = _STATUS_MAP.get(code)
    if mapped is not None:
        return mapped
    _warn_unmapped_status(code)
    return None


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
            "status": map_event_status(event.get("status")),
            "raw_status": event.get("eventDescription") or event.get("eventCode"),
        }
        parsed = parse_iso(timestamp)
        if parsed is None:
            unparseable.append(entry)
        else:
            parseable.append((parsed, entry))
    parseable.sort(key=lambda item: item[0])
    ordered = [entry for _, entry in parseable] + unparseable
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
    delivered = status is ParcelStatus.DELIVERED

    # Events live under each item; flatten them into one shipment-level list.
    events = _flatten_events(raw)

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
        "pickup_point": (raw.get("deliveryPoint") or {}).get("name") or None,
        "url": tracking_url(tracking_code),
        "weight": _weight_kg(raw),
        # PostNord's consumer payload reports a total volume, not L×W×H, so we
        # cannot fill the canonical dimensions triple — kept None for parity.
        "dimensions": None,
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


def _delivered_at(events: list[dict]) -> str | None:
    """Return the ISO timestamp of the delivery event, newest if several."""
    times = [
        ts
        for event in events
        if (event.get("status") == "DELIVERED")
        and (ts := to_iso_timestamp(event.get("eventTime")))
    ]
    return max(times) if times else None


def _weight_kg(raw: dict) -> float | None:
    """Return the shipment weight in kilograms, or ``None``.

    PostNord reports weight as ``{"value": "2.5", "unit": "kg"|"g"}`` under
    ``totalWeight`` (falling back to ``assessedWeight``); grams are converted.
    """
    weight = raw.get("totalWeight") or raw.get("assessedWeight") or {}
    if not isinstance(weight, dict):
        return None
    try:
        value = float(weight.get("value"))
    except (TypeError, ValueError):
        return None
    return value / 1000 if str(weight.get("unit")).lower() == "g" else value


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
