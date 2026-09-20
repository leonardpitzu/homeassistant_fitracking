"""Collar LED light."""

import logging
import math

from homeassistant.components.light import ColorMode, LightEntity
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .entity import FiPetEntity

LOGGER = logging.getLogger(__name__)

# Fi's white; used when the collar reports no palette to match against.
DEFAULT_COLOR_CODE = 8


def hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    """Convert "#RRGGBB" or "RRGGBB" to an (r, g, b) tuple."""
    hex_color = hex_color.lstrip("#")
    return tuple(int(hex_color[i : i + 2], 16) for i in (0, 2, 4))


def calculate_distance(color1, color2) -> float:
    """Euclidean distance between two RGB colors."""
    return math.sqrt(sum((c1 - c2) ** 2 for c1, c2 in zip(color1, color2, strict=True)))


def find_closest_color_code(target_color, color_list: dict[int, tuple[int, int, int]]) -> int:
    """Return the collar colour code nearest the requested RGB value."""
    min_distance = float("inf")
    closest_color_code = DEFAULT_COLOR_CODE

    for code, color in color_list.items():
        distance = calculate_distance(target_color, color)
        if distance < min_distance:
            min_distance = distance
            closest_color_code = code

    return closest_color_code


async def async_setup_entry(hass, config_entry, async_add_devices):
    """Add lights for passed config_entry in HA."""
    coordinator = hass.data[DOMAIN][config_entry.entry_id]

    new_devices = []
    for pet in coordinator.data.pets:
        if pet.device is None:
            LOGGER.warning("Skipping pet %s: no collar paired", pet.name)
            continue
        new_devices.append(FiPetLight(coordinator, pet))
    if new_devices:
        async_add_devices(new_devices)


class FiPetLight(FiPetEntity, LightEntity):
    """The coloured LED ring on the collar."""

    _attr_color_mode = ColorMode.RGB
    _attr_supported_color_modes = {ColorMode.RGB}

    @property
    def name(self):
        return f"{self.pet.name} - Collar Light"

    @property
    def unique_id(self):
        return f"{self.pet.pet_id}-light"

    @property
    def _color_map(self) -> dict[int, tuple[int, int, int]]:
        if (device := self.device) is None:
            return {}
        return {color.code: hex_to_rgb(color.hex_code) for color in device.available_led_colors}

    @property
    def is_on(self):
        return bool(self.device and self.device.led_on(dt_util.utcnow()))

    @property
    def rgb_color(self):
        if (device := self.device) is None or not device.led_color_hex:
            return None
        return hex_to_rgb(device.led_color_hex)

    async def async_turn_on(self, **kwargs):
        if (device := self.device) is None or device.module_id is None:
            return
        await self.coordinator.client.async_set_led(device.module_id, True)

        if "rgb_color" in kwargs:
            # Only set when the colour changes; a brightness-only call is a no-op.
            closest = find_closest_color_code(kwargs["rgb_color"], self._color_map)
            await self.coordinator.client.async_set_led_color(device.module_id, closest)
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs):
        if (device := self.device) is None or device.module_id is None:
            return
        await self.coordinator.client.async_set_led(device.module_id, False)
        await self.coordinator.async_request_refresh()
