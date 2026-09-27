"""Client for the PostNord receiver-app account inbox.

Deliberately isolated from the public tracking client: it uses a user-owned
token pair and the app's revocable gateway key. Responses are returned as raw
dictionaries; normalisation belongs in ``parcels.py``.
"""
from __future__ import annotations

import base64
import hashlib
import secrets
from collections.abc import Awaitable, Callable
from http import HTTPStatus
from typing import Any
from urllib.parse import parse_qs, quote, unquote, urlencode, urlparse

import aiohttp

from ..const import (
    ACCOUNT_API_KEY,
    ACCOUNT_AUTHORIZE_URL,
    ACCOUNT_CLIENT_ID,
    ACCOUNT_CONTEXT,
    ACCOUNT_LOCALE,
    ACCOUNT_REDIRECT_URI,
    ACCOUNT_SCOPES,
    ACCOUNT_TOKEN_URL,
    ACCOUNT_TRACKING_URL,
    ACCOUNT_USERINFO_URL,
    CONF_ACCESS_TOKEN,
    CONF_REFRESH_TOKEN,
)

TokenCallback = Callable[[dict[str, str]], Awaitable[None]]

_UNAUTHORIZED = (HTTPStatus.UNAUTHORIZED, HTTPStatus.FORBIDDEN)


class PostNordAccountApiError(Exception):
    """An unexpected account API response, without sensitive response data."""

    def __init__(self, detail: str, *, status_code: int | None = None) -> None:
        """Store safe failure metadata without retaining the response body."""
        super().__init__(detail)
        self.status_code = status_code


class PostNordAccountInvalidRedirect(PostNordAccountApiError):
    """The pasted text is not this login's redirect, or its code was refused."""


class PostNordAccountReauthRequired(PostNordAccountApiError):
    """The stored refresh token no longer yields an access token."""


class PostNordAccountCompatibilityError(PostNordAccountApiError):
    """A freshly refreshed token is still refused: the app key was rotated."""


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def parse_redirect_url(value: str) -> tuple[str | None, str | None]:
    """Pull ``code``/``state`` out of a pasted ``com.postnord.app://`` redirect.

    Users copy it from a network panel's Location header as often as from the
    request list, so a ``Location:`` label, quotes and whitespace around the
    URL are tolerated.
    """
    text = (value or "").strip().strip("\"'")
    if text.lower().startswith("location:"):
        text = text[len("location:"):].strip().strip("\"'")
    params = parse_qs(urlparse(text).query)
    code = params.get("code", [None])[0]
    state = params.get("state", [None])[0]
    return (unquote(code) if code else None, unquote(state) if state else None)


class PostNordLogin:
    """One browser login with PKCE: the link to open, and the code exchange.

    The verifier and state live only in this object, which the config flow
    keeps for its own lifetime — a retry after a bad paste must reuse them, or
    the tab the user already has open stops matching.
    """

    def __init__(self) -> None:
        """Generate a fresh PKCE verifier and state."""
        self._verifier = _b64(secrets.token_bytes(64))
        self.state = _b64(secrets.token_bytes(16))

    @property
    def authorize_url(self) -> str:
        """Return the authorization URL the user opens in a browser."""
        params = {
            "client_id": ACCOUNT_CLIENT_ID,
            "redirect_uri": ACCOUNT_REDIRECT_URI,
            "response_type": "code",
            "scope": " ".join(ACCOUNT_SCOPES),
            "code_challenge": _b64(hashlib.sha256(self._verifier.encode()).digest()),
            "code_challenge_method": "S256",
            "state": self.state,
        }
        return f"{ACCOUNT_AUTHORIZE_URL}?{urlencode(params, quote_via=quote)}"

    async def async_exchange(
        self, session: aiohttp.ClientSession, pasted: str
    ) -> dict[str, str]:
        """Exchange the pasted redirect's code for a token pair."""
        code, state = parse_redirect_url(pasted)
        if not code or state != self.state:
            raise PostNordAccountInvalidRedirect("not this login's redirect")
        status, body = await _post_token(
            session,
            {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": ACCOUNT_REDIRECT_URI,
                "client_id": ACCOUNT_CLIENT_ID,
                "code_verifier": self._verifier,
            },
        )
        if status >= HTTPStatus.INTERNAL_SERVER_ERROR:
            raise PostNordAccountApiError("login unavailable", status_code=status)
        tokens = _tokens(body)
        if tokens is None:
            # An expired or already used code is a 400 invalid_grant.
            raise PostNordAccountInvalidRedirect("code refused", status_code=status)
        return tokens


async def _post_token(
    session: aiohttp.ClientSession, data: dict[str, str]
) -> tuple[int, Any]:
    try:
        async with session.post(
            ACCOUNT_TOKEN_URL, data=data, headers={"Accept": "application/json"}
        ) as response:
            status = response.status
            if status >= HTTPStatus.INTERNAL_SERVER_ERROR:
                return status, None
            try:
                return status, await response.json(content_type=None)
            except ValueError:
                return status, None
    except (aiohttp.ClientError, TimeoutError) as err:
        raise PostNordAccountApiError("token endpoint unreachable") from err


def _tokens(body: Any) -> dict[str, str] | None:
    """Extract the only token fields persisted by the integration."""
    if not isinstance(body, dict):
        return None
    access_token = body.get("access_token")
    refresh_token = body.get("refresh_token")
    if not isinstance(access_token, str) or not access_token:
        return None
    if not isinstance(refresh_token, str) or not refresh_token:
        return None
    return {CONF_ACCESS_TOKEN: access_token, CONF_REFRESH_TOKEN: refresh_token}


class PostNordAccountClient:
    """PostNord receiver-app account client with refresh-on-401/403."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        *,
        access_token: str | None,
        refresh_token: str | None,
        token_callback: TokenCallback | None = None,
    ) -> None:
        """Initialise the client with entry-owned tokens and a persistence hook."""
        self._session = session
        self._access_token = access_token
        self._refresh_token = refresh_token
        self._token_callback = token_callback

    async def async_get_email(self) -> str:
        """Return the logged-in account's email from OpenID userinfo."""
        body = await self._get(ACCOUNT_USERINFO_URL, {}, gateway=False)
        email = body.get("email") if isinstance(body, dict) else None
        if not isinstance(email, str) or not email.strip():
            raise PostNordAccountApiError("userinfo carries no email")
        return email.strip()

    async def async_get_tracking(self) -> dict[str, list[dict[str, Any]]]:
        """Return the account's ``activeShipments`` and ``archivedShipments``.

        ``summary=false`` matters: the summary form answers archived parcels
        with an id only, without events or dates. A missing bucket is a shape
        failure, never an empty inbox.
        """
        body = await self._get(ACCOUNT_TRACKING_URL, {"summary": "false"})
        if not isinstance(body, dict):
            raise PostNordAccountApiError("tracking body is not an object")
        buckets: dict[str, list[dict[str, Any]]] = {}
        for bucket in ("activeShipments", "archivedShipments"):
            items = body.get(bucket)
            if not isinstance(items, list):
                raise PostNordAccountApiError(f"tracking body lacks {bucket}")
            buckets[bucket] = [item for item in items if isinstance(item, dict)]
        return buckets

    async def _get(
        self, url: str, params: dict[str, str], *, gateway: bool = True
    ) -> Any:
        status, body = await self._fetch(url, params, gateway)
        if status not in _UNAUTHORIZED:
            return self._checked(status, body)
        await self._async_refresh()
        status, body = await self._fetch(url, params, gateway)
        if status in _UNAUTHORIZED:
            # The token was just renewed, so what is refused is the app key.
            raise PostNordAccountCompatibilityError(
                "refreshed token refused", status_code=status
            )
        return self._checked(status, body)

    @staticmethod
    def _checked(status: int, body: Any) -> Any:
        if status != HTTPStatus.OK:
            raise PostNordAccountApiError("unexpected status", status_code=status)
        return body

    async def _fetch(
        self, url: str, params: dict[str, str], gateway: bool
    ) -> tuple[int, Any]:
        headers = {
            "Authorization": f"Bearer {self._access_token}",
            "Accept": "application/json",
            "Accept-Language": ACCOUNT_LOCALE,
        }
        if gateway:
            params = {**params, "apikey": ACCOUNT_API_KEY}
            headers["context"] = ACCOUNT_CONTEXT
        try:
            async with self._session.get(
                url, params=params, headers=headers
            ) as response:
                if response.status != HTTPStatus.OK:
                    return response.status, None
                try:
                    return response.status, await response.json(content_type=None)
                except ValueError as err:
                    raise PostNordAccountApiError("unparseable body") from err
        except (aiohttp.ClientError, TimeoutError) as err:
            raise PostNordAccountApiError("account API unreachable") from err

    async def _async_refresh(self) -> None:
        if not self._refresh_token:
            raise PostNordAccountReauthRequired("no refresh token")
        status, body = await _post_token(
            self._session,
            {
                "grant_type": "refresh_token",
                "refresh_token": self._refresh_token,
                "client_id": ACCOUNT_CLIENT_ID,
            },
        )
        # A server error says nothing about the token: retry next poll.
        if status >= HTTPStatus.INTERNAL_SERVER_ERROR:
            raise PostNordAccountApiError("refresh unavailable", status_code=status)
        access_token = body.get("access_token") if isinstance(body, dict) else None
        if not isinstance(access_token, str) or not access_token:
            raise PostNordAccountReauthRequired("refresh refused", status_code=status)
        self._access_token = access_token
        # A rotated refresh token must replace the old one, or the next
        # refresh fails; PostNord may also keep the old one and omit it.
        new_refresh = body.get("refresh_token")
        if isinstance(new_refresh, str) and new_refresh:
            self._refresh_token = new_refresh
        if self._token_callback is not None:
            await self._token_callback(
                {
                    CONF_ACCESS_TOKEN: self._access_token,
                    CONF_REFRESH_TOKEN: self._refresh_token,
                }
            )
