"""Tests for the PostNord API client."""
import json
from unittest.mock import AsyncMock, MagicMock

import aiohttp
import pytest

from custom_components.postnord.api import PostNordApiClient, PostNordApiError
from custom_components.postnord.const import TRACKING_BAP_KEY

CODE = "00000000000000002"


def _session_returning(status: int, body: object = None) -> MagicMock:
    response = AsyncMock()
    response.status = status
    if isinstance(body, str):
        response.json = AsyncMock(side_effect=json.JSONDecodeError("x", body, 0))
    else:
        response.json = AsyncMock(return_value=body)
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=response)
    ctx.__aexit__ = AsyncMock(return_value=False)
    session = MagicMock()
    session.get = MagicMock(return_value=ctx)
    return session


def _envelope(shipments: list) -> dict:
    return {"TrackingInformationResponse": {"shipments": shipments}}


async def test_get_parcel_returns_shipment_on_success():
    session = _session_returning(200, _envelope([{"shipmentId": CODE}]))
    client = PostNordApiClient(session)

    parcel = await client.async_get_parcel(CODE)

    assert parcel["shipmentId"] == CODE
    # the tracking code is a query parameter; the fixed web key is a header
    assert session.get.call_args.kwargs["params"]["id"] == CODE
    assert session.get.call_args.kwargs["headers"]["X-Bap-Key"] == TRACKING_BAP_KEY


async def test_get_parcel_returns_none_when_no_shipments():
    """An unknown or not-yet-scanned code returns an empty shipments list."""
    client = PostNordApiClient(_session_returning(200, _envelope([])))
    assert await client.async_get_parcel("00000000000000000") is None


async def test_get_parcel_raises_api_error_on_401():
    """A rejected web key is an upstream problem, surfaced as a plain API error."""
    client = PostNordApiClient(_session_returning(401, {}))
    with pytest.raises(PostNordApiError):
        await client.async_get_parcel(CODE)


async def test_get_parcel_raises_api_error_on_403():
    client = PostNordApiClient(_session_returning(403, {}))
    with pytest.raises(PostNordApiError):
        await client.async_get_parcel(CODE)


async def test_get_parcel_raises_on_error_status():
    client = PostNordApiClient(_session_returning(500, {}))
    with pytest.raises(PostNordApiError):
        await client.async_get_parcel(CODE)


async def test_get_parcel_raises_on_unparseable_body():
    client = PostNordApiClient(_session_returning(200, "not json"))
    with pytest.raises(PostNordApiError):
        await client.async_get_parcel(CODE)


async def test_get_parcel_raises_on_non_object_body():
    client = PostNordApiClient(_session_returning(200, ["not", "a", "dict"]))
    with pytest.raises(PostNordApiError):
        await client.async_get_parcel(CODE)


async def test_get_parcel_raises_on_missing_envelope():
    client = PostNordApiClient(_session_returning(200, {"unexpected": 1}))
    with pytest.raises(PostNordApiError):
        await client.async_get_parcel(CODE)


async def test_get_parcel_propagates_network_error():
    """ClientError is left alone — DataUpdateCoordinator already wraps it."""
    session = MagicMock()
    session.get = MagicMock(side_effect=aiohttp.ClientError("boom"))
    client = PostNordApiClient(session)
    with pytest.raises(aiohttp.ClientError):
        await client.async_get_parcel(CODE)
