"""Device tracker for the Fi collar."""

import logging

from homeassistant.components.device_tracker import SourceType, TrackerEntity

from .const import DOMAIN
from .entity import FiPetEntity

LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass, config_entry, async_add_devices):
    """Add trackers for passed config_entry in HA."""
    coordinator = hass.data[DOMAIN][config_entry.entry_id]

    new_devices = []
    for pet in coordinator.data.pets:
        if pet.device is None:
            LOGGER.warning("Skipping pet %s: no collar paired", pet.name)
            continue
        new_devices.append(FiPetTracker(coordinator, pet))
    if new_devices:
        async_add_devices(new_devices)


class FiPetTracker(FiPetEntity, TrackerEntity):
    """GPS position reported by the collar."""

    _attr_source_type = SourceType.GPS

    @property
    def name(self):
        return f"{self.pet.name} Tracker"

    @property
    def unique_id(self):
        return f"{self.pet.pet_id}-tracker"

    @property
    def entity_picture(self):
        return self.pet.photo_url

    @property
    def latitude(self):
        return self.pet.latitude

    @property
    def longitude(self):
        return self.pet.longitude
