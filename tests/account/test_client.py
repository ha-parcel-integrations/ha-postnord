"""Tests for the PostNord account client and the pasted-redirect login."""
from urllib.parse import parse_qs, urlparse

import pytest
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from custom_components.postnord.account.client import (
    PostNordAccountApiError,
    PostNordAccountClient,
    PostNordAccountCompatibilityError,
    PostNordAccountInvalidRedirect,
    PostNordAccountReauthRequired,
    PostNordLogin,
    parse_redirect_url,
)
from custom_components.postnord.const import (
    ACCOUNT_API_KEY,
    ACCOUNT_CLIENT_ID,
    ACCOUNT_REDIRECT_URI,
    ACCOUNT_TOKEN_URL,
    ACCOUNT_TRACKING_URL,
    ACCOUNT_USERINFO_URL,
)

from .payloads import tracking_body

TOKENS = {"access_token": "access-1", "refresh_token": "refresh-1"}


def _redirect(login: PostNordLogin, code: str = "the-code") -> str:
    return f"{ACCOUNT_REDIRECT_URI}?code={code}&state={login.state}"


def test_authorize_url_carries_pkce_and_state():
    login = PostNordLogin()
    params = parse_qs(urlparse(login.authorize_url).query)
    assert params["client_id"] == [ACCOUNT_CLIENT_ID]
    assert params["redirect_uri"] == [ACCOUNT_REDIRECT_URI]
    assert params["code_challenge_method"] == ["S256"]
    assert params["state"] == [login.state]
    assert "offline_access" in params["scope"][0]


def test_two_logins_never_share_state():
    assert PostNordLogin().state != PostNordLogin().state


@pytest.mark.parametrize(
    "pasted",
    [
        "com.postnord.app://redirect?code=abc&state=xyz",
        "  com.postnord.app://redirect?code=abc&state=xyz\n",
        '"com.postnord.app://redirect?code=abc&state=xyz"',
        "Location: com.postnord.app://redirect?code=abc&state=xyz",
    ],
)
def test_parse_redirect_url_tolerates_how_users_paste(pasted):
    assert parse_redirect_url(pasted) == ("abc", "xyz")


def test_parse_redirect_url_without_code():
    assert parse_redirect_url("https://example.com/") == (None, None)
    assert parse_redirect_url("") == (None, None)


async def test_exchange_returns_tokens(hass, aioclient_mock):
    aioclient_mock.post(ACCOUNT_TOKEN_URL, json=TOKENS)
    login = PostNordLogin()
    tokens = await login.async_exchange(async_get_clientsession(hass), _redirect(login))
    assert tokens == TOKENS
    sent = aioclient_mock.mock_calls[0][2]
    assert sent["grant_type"] == "authorization_code"
    assert sent["code"] == "the-code"
    assert sent["code_verifier"]


async def test_exchange_rejects_a_foreign_state(hass, aioclient_mock):
    login = PostNordLogin()
    with pytest.raises(PostNordAccountInvalidRedirect):
        await login.async_exchange(
            async_get_clientsession(hass),
            f"{ACCOUNT_REDIRECT_URI}?code=abc&state=someone-else",
        )
    assert aioclient_mock.call_count == 0


async def test_exchange_rejects_a_missing_code(hass, aioclient_mock):
    login = PostNordLogin()
    with pytest.raises(PostNordAccountInvalidRedirect):
        await login.async_exchange(
            async_get_clientsession(hass), f"{ACCOUNT_REDIRECT_URI}?state={login.state}"
        )


async def test_exchange_expired_code_is_an_invalid_redirect(hass, aioclient_mock):
    aioclient_mock.post(ACCOUNT_TOKEN_URL, status=400, json={"error": "invalid_grant"})
    login = PostNordLogin()
    with pytest.raises(PostNordAccountInvalidRedirect):
        await login.async_exchange(async_get_clientsession(hass), _redirect(login))


async def test_exchange_server_error_is_not_blamed_on_the_paste(hass, aioclient_mock):
    aioclient_mock.post(ACCOUNT_TOKEN_URL, status=503)
    login = PostNordLogin()
    with pytest.raises(PostNordAccountApiError) as err:
        await login.async_exchange(async_get_clientsession(hass), _redirect(login))
    assert not isinstance(err.value, PostNordAccountInvalidRedirect)


async def test_exchange_unreachable(hass, aioclient_mock):
    aioclient_mock.post(ACCOUNT_TOKEN_URL, exc=TimeoutError())
    login = PostNordLogin()
    with pytest.raises(PostNordAccountApiError):
        await login.async_exchange(async_get_clientsession(hass), _redirect(login))


async def test_exchange_unparseable_token_body(hass, aioclient_mock):
    aioclient_mock.post(ACCOUNT_TOKEN_URL, text="not json")
    login = PostNordLogin()
    with pytest.raises(PostNordAccountInvalidRedirect):
        await login.async_exchange(async_get_clientsession(hass), _redirect(login))


def _client(hass, callback=None, refresh_token="refresh-1"):
    return PostNordAccountClient(
        async_get_clientsession(hass),
        access_token="access-1",
        refresh_token=refresh_token,
        token_callback=callback,
    )


async def test_tracking_sends_key_context_and_bearer(hass, aioclient_mock):
    aioclient_mock.get(ACCOUNT_TRACKING_URL, json=tracking_body())
    buckets = await _client(hass).async_get_tracking()
    assert set(buckets) == {"activeShipments", "archivedShipments"}
    _, url, _, headers = aioclient_mock.mock_calls[0]
    assert url.query["apikey"] == ACCOUNT_API_KEY
    assert url.query["summary"] == "false"
    assert headers["Authorization"] == "Bearer access-1"
    assert headers["context"] == "mobileapp"


async def test_missing_bucket_is_a_shape_failure(hass, aioclient_mock):
    aioclient_mock.get(ACCOUNT_TRACKING_URL, json={"activeShipments": []})
    with pytest.raises(PostNordAccountApiError):
        await _client(hass).async_get_tracking()


async def test_non_object_body_is_a_shape_failure(hass, aioclient_mock):
    aioclient_mock.get(ACCOUNT_TRACKING_URL, json=[])
    with pytest.raises(PostNordAccountApiError):
        await _client(hass).async_get_tracking()


async def test_unparseable_body(hass, aioclient_mock):
    aioclient_mock.get(ACCOUNT_TRACKING_URL, text="<html>")
    with pytest.raises(PostNordAccountApiError):
        await _client(hass).async_get_tracking()


async def test_unexpected_status(hass, aioclient_mock):
    aioclient_mock.get(ACCOUNT_TRACKING_URL, status=500)
    with pytest.raises(PostNordAccountApiError) as err:
        await _client(hass).async_get_tracking()
    assert err.value.status_code == 500


async def test_unreachable(hass, aioclient_mock):
    aioclient_mock.get(ACCOUNT_TRACKING_URL, exc=TimeoutError())
    with pytest.raises(PostNordAccountApiError):
        await _client(hass).async_get_tracking()


async def test_401_refreshes_once_and_persists_rotated_tokens(hass, aioclient_mock):
    stored = []

    async def callback(tokens):
        stored.append(tokens)

    aioclient_mock.get(ACCOUNT_TRACKING_URL, status=401)
    aioclient_mock.post(
        ACCOUNT_TOKEN_URL, json={"access_token": "access-2", "refresh_token": "refresh-2"}
    )
    client = _client(hass, callback)
    # The mocked GET keeps answering 401, so swap it for a 200 after refresh.
    original = client._fetch
    calls = 0

    async def fetch(url, params, gateway):
        nonlocal calls
        calls += 1
        if calls == 1:
            return await original(url, params, gateway)
        assert client._access_token == "access-2"
        return 200, tracking_body()

    client._fetch = fetch
    await client.async_get_tracking()
    assert stored == [{"access_token": "access-2", "refresh_token": "refresh-2"}]
    sent = aioclient_mock.mock_calls[1][2]
    assert sent["grant_type"] == "refresh_token"
    assert sent["refresh_token"] == "refresh-1"


async def test_refresh_keeps_the_old_refresh_token_when_none_returned(
    hass, aioclient_mock
):
    stored = []

    async def callback(tokens):
        stored.append(tokens)

    aioclient_mock.post(ACCOUNT_TOKEN_URL, json={"access_token": "access-2"})
    client = _client(hass, callback)
    await client._async_refresh()
    assert stored == [{"access_token": "access-2", "refresh_token": "refresh-1"}]


async def test_refused_refresh_needs_reauth(hass, aioclient_mock):
    aioclient_mock.get(ACCOUNT_TRACKING_URL, status=401)
    aioclient_mock.post(ACCOUNT_TOKEN_URL, status=400, json={"error": "invalid_grant"})
    with pytest.raises(PostNordAccountReauthRequired):
        await _client(hass).async_get_tracking()


async def test_missing_refresh_token_needs_reauth(hass, aioclient_mock):
    aioclient_mock.get(ACCOUNT_TRACKING_URL, status=403)
    with pytest.raises(PostNordAccountReauthRequired):
        await _client(hass, refresh_token=None).async_get_tracking()


async def test_refresh_server_error_is_not_reauth(hass, aioclient_mock):
    aioclient_mock.get(ACCOUNT_TRACKING_URL, status=401)
    aioclient_mock.post(ACCOUNT_TOKEN_URL, status=502)
    with pytest.raises(PostNordAccountApiError) as err:
        await _client(hass).async_get_tracking()
    assert not isinstance(err.value, PostNordAccountReauthRequired)


async def test_refused_after_refresh_is_a_compatibility_failure(hass, aioclient_mock):
    """A token renewed a moment ago is fine; what is refused is the app key."""
    aioclient_mock.get(ACCOUNT_TRACKING_URL, status=403)
    aioclient_mock.post(ACCOUNT_TOKEN_URL, json=TOKENS)
    with pytest.raises(PostNordAccountCompatibilityError):
        await _client(hass).async_get_tracking()


async def test_email_from_userinfo(hass, aioclient_mock):
    aioclient_mock.get(ACCOUNT_USERINFO_URL, json={"email": " someone@example.com "})
    assert await _client(hass).async_get_email() == "someone@example.com"
    _, url, _, headers = aioclient_mock.mock_calls[0]
    # userinfo is not on the gateway: no app key, no context header.
    assert "apikey" not in url.query
    assert "context" not in headers


async def test_userinfo_without_email(hass, aioclient_mock):
    aioclient_mock.get(ACCOUNT_USERINFO_URL, json={"sub": "x"})
    with pytest.raises(PostNordAccountApiError):
        await _client(hass).async_get_email()
