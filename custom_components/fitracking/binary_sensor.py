"""Binary sensors for the Fi collar."""

import logging

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)

from .const import DOMAIN
from .entity import FiPetEntity

LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass, config_entry, async_add_devices):
    """Add binary sensors for passed config_entry in HA."""
    coordinator = hass.data[DOMAIN][config_entry.entry_id]

    new_devices = []
    for pet in coordinator.data.pets:
        if pet.device is None:
            LOGGER.warning("Skipping pet %s: no collar paired", pet.name)
            continue
        new_devices.append(FiBatteryChargingBinarySensor(coordinator, pet))
    if new_devices:
        async_add_devices(new_devices)


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
