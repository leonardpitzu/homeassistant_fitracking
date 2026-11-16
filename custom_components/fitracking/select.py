"""Lost-mode switch for the Fi collar."""

from homeassistant.components.select import SelectEntity

from .const import DOMAIN
from .entity import FiPetEntity, collared_pets

SAFE = "Safe"
LOST = "Lost"


async def async_setup_entry(hass, config_entry, async_add_devices):
    """Add selects for passed config_entry in HA."""
    coordinator = hass.data[DOMAIN][config_entry.entry_id]

    async_add_devices(FiLostMode(coordinator, pet) for pet in collared_pets(coordinator))


class FiLostMode(FiPetEntity, SelectEntity):
    """Puts the collar into Fi's lost-pet mode."""

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
