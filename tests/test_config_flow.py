"""Tests for the PostNord config and options flow."""
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.postnord.config_flow import (
    normalize_tracking_code,
    valid_tracking_code,
)
from custom_components.postnord.const import (
    CONF_DELIVERED_FILTER_AMOUNT,
    CONF_DELIVERED_FILTER_TYPE,
    CONF_INCLUDE_HISTORY,
    CONF_PARCELS,
    CONF_SOURCE,
    CONF_TRACKING_CODE,
    DOMAIN,
    SOURCE_ACCOUNT,
    SOURCE_TRACKING,
)


def test_normalize_tracking_code_strips_and_uppercases():
    assert normalize_tracking_code("example 123-456") == "EXAMPLE123456"
    assert normalize_tracking_code("") == ""
    assert normalize_tracking_code(None) == ""


def test_valid_tracking_code_accepts_any_non_empty_code():
    assert valid_tracking_code("EXAMPLE123456")
    assert valid_tracking_code("ABC")
    assert valid_tracking_code("A" * 31)
    assert not valid_tracking_code("")


async def test_user_flow_offers_both_sources(hass):
    """Setup opens on a menu choosing the account inbox or tracking codes."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    assert result["type"] == "menu"
    assert result["step_id"] == "user"
    assert result["menu_options"] == [SOURCE_ACCOUNT, SOURCE_TRACKING]


async def _tracking_step(hass):
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    return await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": SOURCE_TRACKING}
    )


async def test_tracking_flow_creates_keyless_hub(hass):
    """Confirming creates the hub with no stored credential."""
    result = await _tracking_step(hass)
    assert result["type"] == "form"
    assert result["step_id"] == SOURCE_TRACKING
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {}
    )
    assert result["type"] == "create_entry"
    assert result["title"] == "PostNord"
    assert result["data"] == {CONF_SOURCE: SOURCE_TRACKING}
    assert result["options"][CONF_PARCELS] == []


async def test_second_tracking_hub_rejected(hass):
    """The fixed unique id keeps it to one tracking hub."""
    MockConfigEntry(domain=DOMAIN, unique_id=DOMAIN).add_to_hass(hass)
    result = await _tracking_step(hass)
    assert result["type"] == "abort"
    assert result["reason"] == "already_configured"


async def test_tracking_hub_allowed_beside_an_account(hass):
    MockConfigEntry(
        domain=DOMAIN,
        unique_id="account:someone@example.com",
        data={CONF_SOURCE: SOURCE_ACCOUNT},
    ).add_to_hass(hass)
    result = await _tracking_step(hass)
    assert result["type"] == "form"


def _hub(parcels: list[dict]) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        unique_id=DOMAIN,
        options={CONF_PARCELS: parcels},
    )


def _init_input(
    *, add="", remove=None, history=False,
    filter_type="days", amount=7,
) -> dict:
    """Build the sectioned options-form submission."""
    parcels: dict = {"add": add}
    if remove is not None:
        parcels["remove"] = remove
    return {
        "parcels": parcels,
        "delivered": {
            CONF_DELIVERED_FILTER_TYPE: filter_type,
            CONF_DELIVERED_FILTER_AMOUNT: amount,
        },
        "history": {CONF_INCLUDE_HISTORY: history},
    }


async def _open_options_step(hass, entry, step_id: str):
    """Start the options flow and select one of its two top-level routes."""
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] == "menu"
    assert result["menu_options"] == ["parcels", "settings"]
    return await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": step_id}
    )


async def test_options_parcel_list_can_be_cleared(hass):
    """A submitted empty list removes the final manually tracked parcel."""
    entry = MockConfigEntry(domain=DOMAIN, options={CONF_PARCELS: [{CONF_TRACKING_CODE: "EXAMPLE111111"}]})
    entry.add_to_hass(hass)
    result = await _open_options_step(hass, entry, "parcels")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"tracking_codes": []}
    )
    assert result["type"] == "create_entry"
    assert result["data"][CONF_PARCELS] == []


async def test_options_settings_preserve_parcel_list(hass):
    """Saving settings must never replace the manually tracked parcel list."""
    parcels = [{CONF_TRACKING_CODE: "EXAMPLE111111"}]
    entry = MockConfigEntry(domain=DOMAIN, options={CONF_PARCELS: parcels})
    entry.add_to_hass(hass)
    result = await _open_options_step(hass, entry, "settings")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_DELIVERED_FILTER_TYPE: "days", CONF_DELIVERED_FILTER_AMOUNT: 7, CONF_INCLUDE_HISTORY: False}
    )
    assert result["type"] == "create_entry"
    assert result["data"][CONF_PARCELS] == parcels


async def test_options_account_entry_offers_settings_only(hass):
    """An account imports its parcels, so there is no parcel list to manage."""
    entry = MockConfigEntry(
        domain=DOMAIN, data={CONF_SOURCE: SOURCE_ACCOUNT}, options={}
    )
    entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] == "menu"
    assert result["menu_options"] == ["settings"]
