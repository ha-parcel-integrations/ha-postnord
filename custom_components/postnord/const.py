"""Constants for the PostNord parcel tracker integration."""
from enum import StrEnum

from homeassistant.const import Platform

DOMAIN = "postnord"


class ParcelStatus(StrEnum):
    """Carrier-agnostic parcel status.

    **Do not extend or rename these members.** Every integration in the parcel
    suite publishes exactly this vocabulary on the ``status`` field of each
    normalised parcel, so cross-carrier automations and the aggregator can
    target ``status: out_for_delivery`` regardless of carrier. Listed in
    roughly the order a parcel moves through.
    """

    REGISTERED = "registered"               # Sender announced the parcel; not handed over yet
    IN_TRANSIT = "in_transit"               # In the carrier's network
    OUT_FOR_DELIVERY = "out_for_delivery"   # On a delivery vehicle today
    AT_PICKUP_POINT = "at_pickup_point"     # Ready to collect at a pickup location
    DELIVERED = "delivered"                 # Handed over
    RETURNING = "returning"                 # Failed delivery, going back to sender
    PROBLEM = "problem"                     # Carrier reports an exception/issue
    UNKNOWN = "unknown"                     # Raw status we have not mapped yet


PLATFORMS = [Platform.BUTTON, Platform.CALENDAR, Platform.SENSOR]

# Every optional key the parcel contract defines. CAPABILITIES below must be a
# subset of this — it exists so a typo in CAPABILITIES fails a test instead of
# silently dropping this carrier off a table on the docs site.
KNOWN_CAPABILITIES = frozenset(
    {"weight", "dimensions", "delivery_window", "pickup_point", "url", "history"}
)

# Which optional contract fields this carrier's API actually populates — feeds
# the comparison table on the docs site. Keep in lockstep with
# normalize_parcel() in parcels.py: everything not listed here comes back as a
# literal None there. PostNord's consumer payload reports a total volume, not
# an L×W×H triple, so dimensions is the only field it can't fill.
CAPABILITIES = frozenset({"weight", "delivery_window", "pickup_point", "url", "history"})

# PostNord's public "Track & Trace" REST API (``recipientview``). It is
# **keyless** for the caller: authentication is a fixed public web-client
# identifier sent in the ``X-Bap-Key`` header (see ``TRACKING_BAP_KEY``); there
# is no per-user credential to register.
#
# * ``X-Bap-Key`` is a fixed, public, human-readable web-client tag, not a
#   rotating per-app secret — the durable, Cainiao-class end of the shared-key
#   spectrum. Its value is a **strict whitelist**: an unknown value returns
#   HTTP 403.
# * Response is a ``TrackingInformationResponse`` envelope served as
#   ``application/json`` (parsed with ``content_type=None`` to be safe). An
#   unknown/not-yet-scanned code returns HTTP 200 with an **empty**
#   ``shipments`` list (optionally a ``compositeFault``) — signalled as ``None``.
# * One backend spans DK/SE/NO/FI; ``locale`` only switches the language of the
#   human status text, not which country is resolved.
TRACKING_API_URL = (
    "https://api2.postnord.com/rest/shipment/v5/trackandtrace/recipientview"
)
TRACKING_URL = "https://www.postnord.com/en/track-and-trace?shipmentId={tracking_code}"

# Query parameters for the tracking call. ``locale`` is fixed to English so the
# raw human status text is stable for the status map; the canonical ``status``
# comes from the machine ``status`` enum, not this text.
TRACKING_LOCALE = "en"

# The fixed public web-client key sent in the ``X-Bap-Key`` header. The endpoint
# 403s on any other value, so there is nothing for the user to supply. If it ever
# stops being accepted, the API client logs a one-shot warning pointing at the
# issue tracker.
TRACKING_BAP_KEY = "web-ncp"

# Tracked parcels live in the config entry options as a list of
# ``{tracking_code}`` dicts — this carrier has no account or parcel feed, so the
# user enters the codes themselves. Kept as dicts so future per-parcel fields
# slot in without an options migration.
CONF_PARCELS = "parcels"
CONF_TRACKING_CODE = "tracking_code"

# Delivered-parcels retention: keep delivered parcels visible for the last N
# days, or keep only the N most recent — identical across the suite.
CONF_DELIVERED_FILTER_TYPE = "delivered_filter_type"
CONF_DELIVERED_FILTER_AMOUNT = "delivered_filter_amount"
DEFAULT_DELIVERED_FILTER_TYPE = "days"
DEFAULT_DELIVERED_FILTER_AMOUNT = 7

# Dynamic, status-driven polling — unconditional, no user-facing interval
# option. See carrier-research/dynamic-polling.md for the full algorithm and
# the reasoning behind it.
#
# Quiet window: no polling between these local hours except the two anchors
# below, for overnight / end-of-day catch-up.
QUIET_WINDOW_START_HOUR = 0
QUIET_WINDOW_END_HOUR = 6

# Cadence while polling is active (minutes). Hot = at least one tracked,
# not-yet-delivered parcel is out_for_delivery within HOT_LOOKAHEAD_HOURS of
# its planned_from (or has no planned_from at all); mid = anything else still
# in flight. This is a barcode-based coordinator (Section 2.1): when every
# tracked parcel is delivered, or nothing is tracked, polling stops entirely
# instead of falling to the mid tier — see coordinator.py's
# ``_hottest_tier_minutes``.
HOT_INTERVAL_MINUTES = 15
MID_INTERVAL_MINUTES = 45
HOT_LOOKAHEAD_HOURS = 1

# Small, stable per-install offset added to every computed interval so
# different installs don't all hit an anchor or tier boundary at the same
# second. Deterministic (hash of the config entry id), not random.
STAGGER_MINUTES = 7

# Per-parcel status history is opt-in and off by default, identical across the
# suite. Keep it off by default even when — as here — the timeline arrives in
# the same response and costs no extra request: it is a large attribute, and on
# carriers that need a second call per parcel the cost is real.
CONF_INCLUDE_HISTORY = "include_history"
DEFAULT_INCLUDE_HISTORY = False

# Cap each parcel's history to the most recent N events so the attribute stays
# well under HA's ~16 KB state-attribute limit.
HISTORY_MAX_EVENTS = 20
