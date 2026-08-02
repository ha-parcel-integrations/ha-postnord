"""PostNord public "Track & Trace" API client.

Talks to the keyless ``recipientview`` endpoint
(``api2.postnord.com/rest/shipment/v5/trackandtrace``). Authentication is a
fixed public web-client key sent in the ``X-Bap-Key`` header (see
``const.TRACKING_BAP_KEY``) — there is no per-user credential. The contract the
coordinator relies on:

* ``async_get_parcel`` returns the raw per-shipment dict on success,
* returns ``None`` when PostNord reports the code as unknown or not yet scanned
  (an empty ``shipments`` list — a normal, expected state, never an error),
* raises :class:`PostNordApiError` for any unexpected response — a 5xx outage,
  or a 401/403 that would mean PostNord retired the shared web key — which setup
  maps to ``ConfigEntryNotReady`` (retry with backoff),
* lets ``aiohttp.ClientError`` propagate untouched — ``DataUpdateCoordinator``
  already wraps those into ``UpdateFailed``.

There is no auth-error/reauth split: the key is not user-supplied, so a 401/403
is a (rare) upstream change, retried like any other outage, not a user problem.
"""
from __future__ import annotations

import logging
from http import HTTPStatus
from typing import Any

import aiohttp

from .const import TRACKING_API_URL, TRACKING_BAP_KEY, TRACKING_LOCALE

_LOGGER = logging.getLogger(__name__)

_NEW_ISSUE_URL = "https://github.com/ha-parcel-integrations/ha-postnord/issues/new"

# One-shot guard: if the shared web key ever starts returning 401/403, warn once
# (with a copy-paste issue link) rather than on every poll.
_web_key_warned = False


class PostNordApiError(Exception):
    """Raised when a PostNord API call returns an unexpected response."""

    def __init__(self, detail: str) -> None:
        """Store the detail that triggered the error."""
        super().__init__(f"PostNord API request failed: {detail}")
        self.detail = detail


class PostNordApiClient:
    """Client for the keyless public PostNord tracking endpoint.

    Sends the fixed ``X-Bap-Key`` web-client key on every request; one backend
    spans DK/SE/NO/FI.
    """

    def __init__(self, session: aiohttp.ClientSession) -> None:
        """Initialise the client with an aiohttp session."""
        self._session = session

    async def async_get_parcel(self, tracking_code: str) -> dict[str, Any] | None:
        """Fetch one shipment's tracking details.

        Returns the shipment dict for a known parcel, or ``None`` when PostNord
        reports it as unknown/not-yet-scanned (an empty ``shipments`` list).
        """
        params = {"id": tracking_code, "locale": TRACKING_LOCALE}
        headers = {"X-Bap-Key": TRACKING_BAP_KEY}
        async with self._session.get(
            TRACKING_API_URL, params=params, headers=headers
        ) as response:
            if response.status in (HTTPStatus.UNAUTHORIZED, HTTPStatus.FORBIDDEN):
                _warn_web_key_rejected(response.status)
                raise PostNordApiError(
                    f"HTTP {response.status} — the shared PostNord web key may "
                    "have been retired"
                )
            if response.status != HTTPStatus.OK:
                raise PostNordApiError(f"HTTP {response.status}")
            try:
                # content_type=None: the endpoint has historically served JSON as
                # text/plain, which aiohttp would otherwise refuse to parse.
                payload = await response.json(content_type=None)
            except ValueError as err:
                raise PostNordApiError(f"unparseable body ({err})") from err

        if not isinstance(payload, dict):
            raise PostNordApiError("unexpected body (not a JSON object)")

        envelope = payload.get("TrackingInformationResponse")
        if not isinstance(envelope, dict):
            raise PostNordApiError("missing TrackingInformationResponse envelope")

        shipments = envelope.get("shipments")
        if not shipments:
            # Empty list (optionally with a compositeFault) is how an unknown or
            # not-yet-scanned code is reported — a normal state, not an error.
            return None
        if not isinstance(shipments, list) or not isinstance(shipments[0], dict):
            raise PostNordApiError("unexpected shipments shape")
        return shipments[0]


def _warn_web_key_rejected(status: int) -> None:
    """Warn once when the shared public web key stops being accepted."""
    global _web_key_warned
    if _web_key_warned:
        return
    _web_key_warned = True
    _LOGGER.warning(
        "PostNord rejected the shared web tracking key (HTTP %s). It may have "
        "been retired — please report this at %s",
        status,
        _NEW_ISSUE_URL,
    )
