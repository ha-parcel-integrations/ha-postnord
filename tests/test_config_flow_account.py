"""Tests for the account source's config flow: paste login and reauth."""
from urllib.parse import parse_qs, urlparse

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.postnord.const import (
    ACCOUNT_REDIRECT_URI,
    ACCOUNT_TOKEN_URL,
    ACCOUNT_USERINFO_URL,
    CONF_ACCESS_TOKEN,
    CONF_EMAIL,
    CONF_REDIRECT_URL,
    CONF_REFRESH_TOKEN,
    CONF_SOURCE,
    DOMAIN,
    SOURCE_ACCOUNT,
)

EMAIL = "Someone@Example.com"
TOKENS = {"access_token": "access-1", "refresh_token": "refresh-1"}


def _state(result) -> str:
    url = result["description_placeholders"]["authorize_url"]
    return parse_qs(urlparse(url).query)["state"][0]


def _redirect(result, code="the-code") -> dict:
    return {CONF_REDIRECT_URL: f"{ACCOUNT_REDIRECT_URI}?code={code}&state={_state(result)}"}


async def _account_step(hass):
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    return await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": SOURCE_ACCOUNT}
    )


def _mock_login(aioclient_mock, email=EMAIL):
    aioclient_mock.post(ACCOUNT_TOKEN_URL, json=TOKENS)
    aioclient_mock.get(ACCOUNT_USERINFO_URL, json={"email": email})


async def test_account_step_shows_the_login_link(hass):
    result = await _account_step(hass)
    assert result["type"] == "form"
    assert result["step_id"] == SOURCE_ACCOUNT
    placeholders = result["description_placeholders"]
    assert placeholders["authorize_url"].startswith("https://account.postnord.com/auth?")
    assert placeholders["docs_url"].endswith("finding-the-redirect-url.md")


async def test_account_flow_creates_entry(hass, aioclient_mock):
    _mock_login(aioclient_mock)
    result = await _account_step(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], _redirect(result)
    )
    assert result["type"] == "create_entry"
    assert result["title"] == EMAIL
    assert result["data"] == {
        CONF_SOURCE: SOURCE_ACCOUNT,
        CONF_EMAIL: EMAIL,
        CONF_ACCESS_TOKEN: "access-1",
        CONF_REFRESH_TOKEN: "refresh-1",
    }
    assert result["result"].unique_id == "account:someone@example.com"


async def test_bad_paste_keeps_the_same_login_link(hass, aioclient_mock):
    result = await _account_step(hass)
    first_url = result["description_placeholders"]["authorize_url"]
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_REDIRECT_URL: f"{ACCOUNT_REDIRECT_URI}?code=x&state=someone-else"},
    )
    assert result["type"] == "form"
    assert result["errors"] == {"base": "invalid_redirect"}
    assert result["description_placeholders"]["authorize_url"] == first_url
    assert aioclient_mock.call_count == 0


async def test_expired_code_is_invalid_redirect(hass, aioclient_mock):
    aioclient_mock.post(ACCOUNT_TOKEN_URL, status=400, json={"error": "invalid_grant"})
    result = await _account_step(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], _redirect(result)
    )
    assert result["errors"] == {"base": "invalid_redirect"}


async def test_unreachable_login_is_cannot_connect(hass, aioclient_mock):
    aioclient_mock.post(ACCOUNT_TOKEN_URL, status=503)
    result = await _account_step(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], _redirect(result)
    )
    assert result["errors"] == {"base": "cannot_connect"}


async def test_same_account_in_other_casing_is_rejected(hass, aioclient_mock):
    MockConfigEntry(
        domain=DOMAIN,
        unique_id="account:someone@example.com",
        data={CONF_SOURCE: SOURCE_ACCOUNT},
    ).add_to_hass(hass)
    _mock_login(aioclient_mock, email="SOMEONE@example.com")
    result = await _account_step(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], _redirect(result)
    )
    assert result["type"] == "abort"
    assert result["reason"] == "already_configured"


async def test_account_allowed_beside_the_tracking_hub(hass, aioclient_mock):
    MockConfigEntry(domain=DOMAIN, unique_id=DOMAIN).add_to_hass(hass)
    _mock_login(aioclient_mock)
    result = await _account_step(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], _redirect(result)
    )
    assert result["type"] == "create_entry"


def _account_entry(hass) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="account:someone@example.com",
        data={
            CONF_SOURCE: SOURCE_ACCOUNT,
            CONF_EMAIL: EMAIL,
            CONF_ACCESS_TOKEN: "old-access",
            CONF_REFRESH_TOKEN: "old-refresh",
        },
    )
    entry.add_to_hass(hass)
    return entry


async def test_reauth_updates_the_tokens(hass, aioclient_mock):
    entry = _account_entry(hass)
    _mock_login(aioclient_mock)
    result = await entry.start_reauth_flow(hass)
    assert result["step_id"] == "reauth_confirm"
    assert result["description_placeholders"][CONF_EMAIL] == EMAIL
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], _redirect(result)
    )
    assert result["type"] == "abort"
    assert result["reason"] == "reauth_successful"
    assert entry.data[CONF_ACCESS_TOKEN] == "access-1"
    assert entry.data[CONF_REFRESH_TOKEN] == "refresh-1"


async def test_reauth_with_another_account_is_refused(hass, aioclient_mock):
    entry = _account_entry(hass)
    _mock_login(aioclient_mock, email="other@example.com")
    result = await entry.start_reauth_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], _redirect(result)
    )
    assert result["type"] == "abort"
    assert result["reason"] == "unique_id_mismatch"
    assert entry.data[CONF_ACCESS_TOKEN] == "old-access"


async def test_reauth_bad_paste_shows_the_error(hass, aioclient_mock):
    entry = _account_entry(hass)
    result = await entry.start_reauth_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_REDIRECT_URL: "nonsense"}
    )
    assert result["type"] == "form"
    assert result["errors"] == {"base": "invalid_redirect"}
