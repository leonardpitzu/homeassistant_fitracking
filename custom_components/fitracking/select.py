"""Lost-mode switch for the Fi collar."""

import logging

from homeassistant.components.select import SelectEntity

from .const import DOMAIN
from .entity import FiPetEntity

LOGGER = logging.getLogger(__name__)

SAFE = "Safe"
LOST = "Lost"


async def async_setup_entry(hass, config_entry, async_add_devices):
    """Add selects for passed config_entry in HA."""
    coordinator = hass.data[DOMAIN][config_entry.entry_id]

    new_devices = []
    for pet in coordinator.data.pets:
        if pet.device is None:
            LOGGER.warning("Skipping pet %s: no collar paired", pet.name)
            continue
        new_devices.append(FiLostMode(coordinator, pet))
    if new_devices:
        async_add_devices(new_devices)


class FiLostMode(FiPetEntity, SelectEntity):
    """Puts the collar into Fi's lost-dog mode."""

    _attr_options = [SAFE, LOST]

    @property
    def name(self):
        return f"{self.pet.name} Lost Mode"

    @property
    def unique_id(self):
        return f"{self.pet.pet_id}-lost"

    @property
    def current_option(self):
        return LOST if self.pet.is_lost else SAFE

    async def async_select_option(self, option: str) -> None:
        if (device := self.device) is None or device.module_id is None:
            return
        await self.coordinator.client.async_set_lost_mode(device.module_id, option == LOST)
        await self.coordinator.async_request_refresh()
