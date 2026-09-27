"""Config flow for the PostNord parcel tracker integration."""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .account.client import (
    PostNordAccountApiError,
    PostNordAccountClient,
    PostNordAccountInvalidRedirect,
    PostNordLogin,
)
from .const import (
    ACCOUNT_REDIRECT_DOCS_URL,
    CONF_ACCESS_TOKEN,
    CONF_DELIVERED_FILTER_AMOUNT,
    CONF_DELIVERED_FILTER_TYPE,
    CONF_EMAIL,
    CONF_INCLUDE_HISTORY,
    CONF_PARCELS,
    CONF_REDIRECT_URL,
    CONF_REFRESH_TOKEN,
    CONF_SOURCE,
    CONF_TRACKING_CODE,
    DEFAULT_DELIVERED_FILTER_AMOUNT,
    DEFAULT_DELIVERED_FILTER_TYPE,
    DEFAULT_INCLUDE_HISTORY,
    DOMAIN,
    SOURCE_ACCOUNT,
    SOURCE_TRACKING,
)

_REDIRECT_SCHEMA = vol.Schema({vol.Required(CONF_REDIRECT_URL): str})

_DEFAULT_OPTIONS = {
    CONF_DELIVERED_FILTER_TYPE: DEFAULT_DELIVERED_FILTER_TYPE,
    CONF_DELIVERED_FILTER_AMOUNT: DEFAULT_DELIVERED_FILTER_AMOUNT,
    CONF_INCLUDE_HISTORY: DEFAULT_INCLUDE_HISTORY,
}

_LOGGER = logging.getLogger(__name__)


def normalize_tracking_code(value: str) -> str:
    """Return the tracking code upper-cased with separators stripped.

    Mirrors what a consumer site's own sanitiser does (uppercase, drop
    everything that is not ``A-Z0-9``), so codes pasted with spaces or dashes
    still work.
    """
    return re.sub(r"[^A-Z0-9]+", "", (value or "").upper())


def valid_tracking_code(value: str) -> bool:
    """Accept every non-empty code.

    PostNord's tracking-code shapes vary too much across SE/DK/NO/FI and are
    not fully confirmed, and an unrecognised code just comes back "not found"
    from the API anyway.
    """
    return bool(value)


def _current_parcels(entry: ConfigEntry) -> list[dict[str, str]]:
    """Return a mutable copy of the tracked parcels list."""
    return [dict(item) for item in entry.options.get(CONF_PARCELS, [])]


class PostNordConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the UI-driven configuration flow for the PostNord integration."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: ConfigEntry,
    ) -> PostNordOptionsFlowHandler:
        """Return the options flow handler."""
        return PostNordOptionsFlowHandler()

    def __init__(self) -> None:
        """Initialise per-flow login state — never persisted, never reused."""
        self._login: PostNordLogin | None = None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Choose between tracking codes and an account inbox."""
        return self.async_show_menu(
            step_id="user", menu_options=[SOURCE_ACCOUNT, SOURCE_TRACKING]
        )

    async def async_step_tracking(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Create the keyless PostNord tracking hub — one instance, no credential.

        PostNord tracking is per tracking-code and needs no account or key (the
        public endpoint authenticates with a fixed built-in web key), so setup
        just confirms. Parcels are added afterwards via the options flow, the
        ``postnord.track_parcel`` service or a dashboard button. The fixed
        unique id keeps it to one hub now that account entries sit beside it.
        """
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()

        if user_input is not None:
            return self.async_create_entry(
                title="PostNord",
                data={CONF_SOURCE: SOURCE_TRACKING},
                options={CONF_PARCELS: [], **_DEFAULT_OPTIONS},
            )

        return self.async_show_form(step_id=SOURCE_TRACKING)

    async def async_step_account(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show the login link and take the pasted redirect URL back."""
        errors: dict[str, str] = {}
        if user_input is not None:
            result = await self._async_login(user_input[CONF_REDIRECT_URL])
            if isinstance(result, str):
                errors["base"] = result
            else:
                email, tokens = result
                await self.async_set_unique_id(f"account:{email.lower()}")
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=email,
                    data={CONF_SOURCE: SOURCE_ACCOUNT, CONF_EMAIL: email, **tokens},
                    options=dict(_DEFAULT_OPTIONS),
                )

        return self.async_show_form(
            step_id=SOURCE_ACCOUNT,
            data_schema=_REDIRECT_SCHEMA,
            errors=errors,
            description_placeholders=self._placeholders(),
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Start reauthentication after PostNord refused the stored tokens."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Repeat the browser login and update the entry's tokens."""
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            result = await self._async_login(user_input[CONF_REDIRECT_URL])
            if isinstance(result, str):
                errors["base"] = result
            else:
                email, tokens = result
                await self.async_set_unique_id(f"account:{email.lower()}")
                self._abort_if_unique_id_mismatch()
                return self.async_update_reload_and_abort(
                    entry, data_updates={CONF_EMAIL: email, **tokens}
                )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=_REDIRECT_SCHEMA,
            errors=errors,
            description_placeholders={
                **self._placeholders(),
                CONF_EMAIL: entry.data.get(CONF_EMAIL, ""),
            },
        )

    def _placeholders(self) -> dict[str, str]:
        # One login per flow: a retry after a bad paste must keep the URL the
        # user already has open.
        if self._login is None:
            self._login = PostNordLogin()
        return {
            "authorize_url": self._login.authorize_url,
            "docs_url": ACCOUNT_REDIRECT_DOCS_URL,
        }

    async def _async_login(
        self, pasted: str
    ) -> tuple[str, dict[str, str]] | str:
        """Exchange the pasted redirect; return (email, tokens) or an error key."""
        if self._login is None:
            self._login = PostNordLogin()
        session = async_get_clientsession(self.hass)
        try:
            tokens = await self._login.async_exchange(session, pasted)
            email = await PostNordAccountClient(
                session,
                access_token=tokens[CONF_ACCESS_TOKEN],
                refresh_token=tokens[CONF_REFRESH_TOKEN],
            ).async_get_email()
        except PostNordAccountInvalidRedirect:
            return "invalid_redirect"
        except PostNordAccountApiError:
            _LOGGER.debug("PostNord account login failed", exc_info=True)
            return "cannot_connect"
        return email, tokens


class PostNordOptionsFlowHandler(OptionsFlow):
    """Manage tracked parcels, delivered retention and history in one sectioned form.

    Mirrors the other suite carriers' section layout (here: ``parcels`` /
    ``delivered`` / ``history``). Changes apply live via HA's
    options-update listener (which refreshes the coordinator), so new/removed
    per-parcel sensors appear and disappear immediately.
    """

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Offer parcel management separately from integration settings.

        An account entry imports its parcels by itself, so it has no parcel
        list to manage.
        """
        menu_options = ["settings"]
        if self.config_entry.data.get(CONF_SOURCE, SOURCE_TRACKING) == SOURCE_TRACKING:
            menu_options.insert(0, "parcels")
        return self.async_show_menu(step_id="init", menu_options=menu_options)

    async def async_step_parcels(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show and handle the complete tracked-code list."""
        errors: dict[str, str] = {}
        if user_input is not None:
            codes = list(
                dict.fromkeys(
                    normalize_tracking_code(code)
                    for code in user_input.get("tracking_codes", [])
                    if normalize_tracking_code(code)
                )
            )
            if any(not valid_tracking_code(code) for code in codes):
                errors["base"] = "invalid_tracking_code"
            else:
                return self.async_create_entry(
                    title="",
                    data={
                        **self.config_entry.options,
                        CONF_PARCELS: [{CONF_TRACKING_CODE: code} for code in codes],
                    },
                )

        current_codes = [
            parcel[CONF_TRACKING_CODE] for parcel in _current_parcels(self.config_entry)
        ]
        schema = vol.Schema(
            {
                vol.Optional("tracking_codes"): selector.TextSelector(
                    selector.TextSelectorConfig(multiple=True)
                )
            }
        )
        return self.async_show_form(
            step_id="parcels",
            data_schema=self.add_suggested_values_to_schema(
                schema, {"tracking_codes": current_codes}
            ),
            errors=errors,
        )

    async def async_step_settings(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show and handle non-parcel integration settings."""
        if user_input is not None:
            return self.async_create_entry(
                title="",
                data={
                    **self.config_entry.options,
                    CONF_DELIVERED_FILTER_TYPE: user_input[CONF_DELIVERED_FILTER_TYPE],
                    CONF_DELIVERED_FILTER_AMOUNT: int(
                        user_input[CONF_DELIVERED_FILTER_AMOUNT]
                    ),
                    CONF_INCLUDE_HISTORY: bool(user_input[CONF_INCLUDE_HISTORY]),
                },
            )

        current = self.config_entry.options
        return self.async_show_form(
            step_id="settings",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_DELIVERED_FILTER_TYPE,
                        default=current.get(
                            CONF_DELIVERED_FILTER_TYPE, DEFAULT_DELIVERED_FILTER_TYPE
                        ),
                    ): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=["days", "parcels"],
                            translation_key=CONF_DELIVERED_FILTER_TYPE,
                            mode=selector.SelectSelectorMode.LIST,
                        )
                    ),
                    vol.Required(
                        CONF_DELIVERED_FILTER_AMOUNT,
                        default=current.get(
                            CONF_DELIVERED_FILTER_AMOUNT,
                            DEFAULT_DELIVERED_FILTER_AMOUNT,
                        ),
                    ): selector.NumberSelector(
                        selector.NumberSelectorConfig(
                            min=1, max=365, step=1, mode=selector.NumberSelectorMode.BOX
                        )
                    ),
                    vol.Required(
                        CONF_INCLUDE_HISTORY,
                        default=current.get(
                            CONF_INCLUDE_HISTORY, DEFAULT_INCLUDE_HISTORY
                        ),
                    ): selector.BooleanSelector(),
                }
            ),
        )
