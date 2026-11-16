"""Binary sensors for the Fi collar."""

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)

from .const import DOMAIN
from .entity import FiPetEntity, collared_pets


async def async_setup_entry(hass, config_entry, async_add_devices):
    """Add binary sensors for passed config_entry in HA."""
    coordinator = hass.data[DOMAIN][config_entry.entry_id]

    async_add_devices(FiBatteryChargingBinarySensor(coordinator, pet) for pet in collared_pets(coordinator))


class FiBatteryChargingBinarySensor(FiPetEntity, BinarySensorEntity):
    """Whether the collar is sitting on its base."""

    _attr_device_class = BinarySensorDeviceClass.BATTERY_CHARGING

    @property
    def name(self):
        return f"{self.pet.name} Collar Battery Charging"

    @property
    def unique_id(self):
        return f"{self.pet.pet_id}-battery-charging"

    @property
    def is_on(self):
        return bool(self.device and self.device.is_charging)

    @property
    def icon(self):
        return "mdi:power-plug" if self.is_on else "mdi:power-plug-off"
