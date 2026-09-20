"""Config and options flows for Fi Tracking."""

import logging
from collections.abc import Mapping
from typing import Any

import voluptuous as vol
from homeassistant import config_entries, core, exceptions
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_create_clientsession

from .api import FiAuthError, FiClient, FiConnectionError
from .const import (
    CONF_PASSWORD,
    CONF_POLLING_RATE,
    CONF_USERNAME,
    DEFAULT_POLLING_RATE,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_USERNAME): str,
        vol.Required(CONF_PASSWORD): str,
        vol.Optional(CONF_POLLING_RATE, default=DEFAULT_POLLING_RATE): str,
    }
)

REAUTH_SCHEMA = vol.Schema({vol.Required(CONF_PASSWORD): str})


async def _async_check_credentials(hass: core.HomeAssistant, username: str, password: str) -> None:
    client = FiClient(async_create_clientsession(hass), username, password)
    await client.async_login()


async def validate_input(hass: core.HomeAssistant, data: dict) -> dict[str, str]:
    """Check the polling rate and prove the credentials work."""
    try:
        if int(data[CONF_POLLING_RATE]) < 1:
            raise InvalidPolling
    except (TypeError, ValueError) as err:
        raise InvalidPolling from err

    await _async_check_credentials(hass, data[CONF_USERNAME], data[CONF_PASSWORD])
    return {"title": data[CONF_USERNAME]}


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Fi Tracking."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        """Get the options flow for this handler."""
        return OptionsFlowHandler()

    async def async_step_user(self, user_input=None):
        """Handle the initial step."""
        errors = {}
        if user_input is not None:
            try:
                info = await validate_input(self.hass, user_input)
            except InvalidPolling:
                errors["base"] = "invalid_polling"
            except FiAuthError:
                errors["base"] = "invalid_auth"
            except FiConnectionError:
                errors["base"] = "cannot_connect"
            except Exception:  # pylint: disable=broad-except
                _LOGGER.exception("Unexpected exception")
                errors["base"] = "unknown"
            else:
                await self.async_set_unique_id(user_input[CONF_USERNAME].lower())
                self._abort_if_unique_id_configured()
                return self.async_create_entry(title=info["title"], data=user_input)

        return self.async_show_form(step_id="user", data_schema=DATA_SCHEMA, errors=errors)

    async def async_step_reauth(self, entry_data: Mapping[str, Any]):
        """Fi rejected the stored credentials; ask for the password again."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input=None):
        """Collect a fresh password and revalidate it."""
        errors = {}
        entry = self._get_reauth_entry()
        if user_input is not None:
            try:
                await _async_check_credentials(self.hass, entry.data[CONF_USERNAME], user_input[CONF_PASSWORD])
            except FiAuthError:
                errors["base"] = "invalid_auth"
            except FiConnectionError:
                errors["base"] = "cannot_connect"
            except Exception:  # pylint: disable=broad-except
                _LOGGER.exception("Unexpected exception")
                errors["base"] = "unknown"
            else:
                return self.async_update_reload_and_abort(
                    entry, data_updates={CONF_PASSWORD: user_input[CONF_PASSWORD]}
                )

        return self.async_show_form(step_id="reauth_confirm", data_schema=REAUTH_SCHEMA, errors=errors)


class OptionsFlowHandler(config_entries.OptionsFlow):
    """Handle options."""

    async def async_step_init(self, user_input=None):
        """Manage the options."""
        errors = {}
        if user_input is not None:
            try:
                if int(user_input[CONF_POLLING_RATE]) < 1:
                    raise InvalidPolling
            except (TypeError, ValueError, InvalidPolling):
                errors["base"] = "invalid_polling"
            else:
                return self.async_create_entry(title="", data=user_input)

        # Fall back to the value captured at setup so the form shows what is
        # actually in effect rather than the module default.
        current = self.config_entry.options.get(
            CONF_POLLING_RATE,
            self.config_entry.data.get(CONF_POLLING_RATE, DEFAULT_POLLING_RATE),
        )
        return self.async_show_form(
            step_id="init",
            errors=errors,
            data_schema=vol.Schema(
                {
                    vol.Optional(CONF_POLLING_RATE, default=str(current)): str,
                }
            ),
        )


class InvalidPolling(exceptions.HomeAssistantError):
    """Error to indicate we cannot use the polling rate"""
