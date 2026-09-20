"""The Fi Tracking integration."""

import logging
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_create_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import FiAuthError, FiClient, FiConnectionError, FiData
from .const import (
    CONF_PASSWORD,
    CONF_POLLING_RATE,
    CONF_USERNAME,
    DEFAULT_POLLING_RATE,
    DOMAIN,
    PLATFORMS,
)

LOGGER = logging.getLogger(__name__)

# Setup is config-entry only; async_setup_entry seeds hass.data itself.
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    # Fi authenticates with a cookie, so this entry gets its own jar rather
    # than sharing Home Assistant's pooled session.
    session = async_create_clientsession(hass)
    client = FiClient(session, entry.data[CONF_USERNAME], entry.data[CONF_PASSWORD])

    try:
        await client.async_login()
    except FiAuthError as err:
        raise ConfigEntryAuthFailed(str(err)) from err
    except FiConnectionError as err:
        raise ConfigEntryNotReady(str(err)) from err

    # Options take precedence over the value captured at setup, so the options
    # flow actually takes effect (upstream only ever read entry.data).
    polling_rate = entry.options.get(CONF_POLLING_RATE, entry.data.get(CONF_POLLING_RATE, DEFAULT_POLLING_RATE))

    coordinator = FiDataUpdateCoordinator(hass, client, int(polling_rate))
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = coordinator

    entry.async_on_unload(entry.add_update_listener(async_reload_entry))

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the entry so a changed polling rate takes effect immediately."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)

    return unload_ok


class FiDataUpdateCoordinator(DataUpdateCoordinator[FiData]):
    """Refreshes every pet and base on the configured interval."""

    def __init__(self, hass: HomeAssistant, client: FiClient, polling_rate: int) -> None:
        self.client = client
        super().__init__(
            hass,
            LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=polling_rate),
        )

    async def _async_update_data(self) -> FiData:
        try:
            return await self.client.async_get_data(dt_util.start_of_local_day())
        except FiAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except FiConnectionError as err:
            raise UpdateFailed(str(err)) from err
