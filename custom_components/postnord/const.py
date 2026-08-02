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

# Refresh interval (minutes) controls how often the coordinator polls the
# carrier. Default 30 min keeps the load on a consumer endpoint gentle; the
# minimum is 15 min for the same reason.
#
# Deliberate divergence from the HA Core rule that polling intervals are not
# user-configurable: that rule targets core integrations, and in a HACS parcel
# tracker a tunable cadence is a wanted feature. Generate with
# ``--interval fixed`` instead when the carrier throttles or soft-bans unusual
# traffic — that drops the option entirely and hard-codes the cadence, so users
# cannot dial it down to something that gets them blocked.
CONF_REFRESH_INTERVAL = "refresh_interval"
REFRESH_INTERVAL_OPTIONS = (15, 30, 60, 120, 240)
DEFAULT_REFRESH_INTERVAL = 30

# Per-parcel status history is opt-in and off by default, identical across the
# suite. Keep it off by default even when — as here — the timeline arrives in
# the same response and costs no extra request: it is a large attribute, and on
# carriers that need a second call per parcel the cost is real.
CONF_INCLUDE_HISTORY = "include_history"
DEFAULT_INCLUDE_HISTORY = False

# Cap each parcel's history to the most recent N events so the attribute stays
# well under HA's ~16 KB state-attribute limit.
HISTORY_MAX_EVENTS = 20
