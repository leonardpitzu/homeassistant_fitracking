"""Device tracker for the Fi collar."""

from homeassistant.components.device_tracker import SourceType, TrackerEntity

from .const import DOMAIN
from .entity import FiPetEntity, collared_pets


async def async_setup_entry(hass, config_entry, async_add_devices):
    """Add trackers for passed config_entry in HA."""
    coordinator = hass.data[DOMAIN][config_entry.entry_id]

    async_add_devices(FiPetTracker(coordinator, pet) for pet in collared_pets(coordinator))


class FiPetTracker(FiPetEntity, TrackerEntity):
    """GPS position reported by the collar."""

    _attr_source_type = SourceType.GPS

    @property
    def force_update(self) -> bool:
        """Write the fix only when it changed.

        TrackerEntity forces every write of a non-polling entity, and it does
        so in a property that ignores `_attr_force_update`, so it has to be
        overridden here. Forced, every poll was a recorder row and reset
        `last_changed`, which broke `for:` durations. A new position still
        differs in latitude or longitude and is written as usual; a repeat
        only moves `last_reported`.
        """
        return False

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
