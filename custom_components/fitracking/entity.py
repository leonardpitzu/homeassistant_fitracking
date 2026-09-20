"""Shared entity plumbing for Fi Tracking."""

from __future__ import annotations

from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import Base, Device, Pet
from .const import DOMAIN, MANUFACTURER


class FiPetEntity(CoordinatorEntity):
    """Base for anything hanging off a pet's collar."""

    def __init__(self, coordinator, pet: Pet) -> None:
        super().__init__(coordinator)
        self._pet_id = pet.pet_id
        # Kept so naming survives a refresh that briefly drops the pet.
        self._fallback = pet

    @property
    def pet(self) -> Pet:
        return self.coordinator.data.get_pet(self._pet_id) or self._fallback

    @property
    def device(self) -> Device | None:
        return self.pet.device

    @property
    def device_info(self):
        return {
            "identifiers": {(DOMAIN, self.pet.pet_id)},
            "name": self.pet.name,
            "manufacturer": MANUFACTURER,
            "model": self.pet.breed,
            "sw_version": self.device.build_id if self.device else None,
        }


class FiBaseEntity(CoordinatorEntity):
    """Base for a Fi charging base."""

    def __init__(self, coordinator, base: Base) -> None:
        super().__init__(coordinator)
        self._base_id = base.base_id
        self._fallback = base

    @property
    def base(self) -> Base:
        return self.coordinator.data.get_base(self._base_id) or self._fallback

    @property
    def device_info(self):
        return {
            "identifiers": {(DOMAIN, self.base.base_id)},
            "name": self.base.name,
            "manufacturer": MANUFACTURER,
            "model": "Fi Base",
        }
