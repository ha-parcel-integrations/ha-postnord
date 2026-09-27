"""PostNord's status vocabulary, shared by both parcel sources.

The tracker (``recipientview``) and the account inbox report the same machine
``status`` enum, so the map lives one level up and neither source can drift
from it. Normalisation is *not* shared — each source shapes its own payload.
"""
from __future__ import annotations

import logging

from .const import ParcelStatus

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
# being mapped wrongly. The account inbox adds two literals of the same enum:
# ``DELAYED`` (bucketed like ``EXPECTED_DELAY``) and ``DELIVERY_REFUSED`` (a
# return leg in flight, so ``returning``).
OTHER_STATUS = "OTHER"

# Event codes the PostNord app shows as "extended retention time": the pickup
# point keeps the parcel longer, so it is still waiting to be collected,
# whatever the event's own status says.
EXTENDED_RETENTION_EVENT_CODES = frozenset({"45", "z2F"})

STATUS_MAP: dict[str, ParcelStatus] = {
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
    "DELAYED": ParcelStatus.PROBLEM,
    "DELIVERY_REFUSED": ParcelStatus.RETURNING,
}

# PostNord reports "The delivery of the shipment item is in progress" as event
# code 113 under the generic ``EN_ROUTE`` status; it is the real out-for-delivery
# signal, which the machine ``status`` enum never surfaces for it.
OUT_FOR_DELIVERY_EVENT_CODE = "113"

# Status codes we have already warned about, so each unmapped one is logged
# only once per HA session instead of on every poll.
_unmapped_statuses_logged: set[str] = set()


def warn_unmapped_status(code: str) -> None:
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


def map_parcel_status(code: str | None) -> ParcelStatus:
    """Map a carrier status code to a canonical :class:`ParcelStatus`.

    ``None`` (a not-yet-scanned parcel) reports ``unknown`` silently; an
    unrecognised code reports ``unknown`` with a one-shot warning.
    """
    if not code:
        return ParcelStatus.UNKNOWN
    mapped = STATUS_MAP.get(code)
    if mapped is not None:
        return mapped
    warn_unmapped_status(code)
    return ParcelStatus.UNKNOWN


def map_event_status(code: str | None) -> ParcelStatus | None:
    """Map a history entry's status code to a canonical status, or ``None``.

    Unmapped codes keep ``status: null`` on the history entry (rather than
    ``unknown``, so a consumer can tell "no mapping" from "mapped to unknown")
    and warn once, reusing the parcel-status one-shot set.
    """
    if not code:
        return None
    mapped = STATUS_MAP.get(code)
    if mapped is not None:
        return mapped
    # ``OTHER`` on an event is a notification or an intermediate scan, not an
    # unknown status: the history builder carries the previous status over it.
    if code != OTHER_STATUS:
        warn_unmapped_status(code)
    return None
