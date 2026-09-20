"""Sensors for the Fi collar."""

import logging

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import PERCENTAGE, UnitOfLength, UnitOfTime
from homeassistant.helpers.icon import icon_for_battery_level

from .const import BEHAVIOR_META, DOMAIN, SENSOR_STATS_BY_TIME, SENSOR_STATS_BY_TYPE
from .entity import FiBaseEntity, FiPetEntity

LOGGER = logging.getLogger(__name__)

# "field" names the attribute on api.Stats holding this metric.
# Nothing here is total_increasing: Fi revises these downward after first
# reporting them, and HA's 10% reset tolerance is relative, so a small absolute
# revision early in a period reads as a counter reset.
STAT_META = {
    "STEPS": {
        "field": "steps",
        "icon": "mdi:shoe-print",
        "unit": "steps",
        "divisor": 1,
        "precision": 0,
        "device_class": None,
        "state_class": SensorStateClass.MEASUREMENT,
    },
    "DISTANCE": {
        "field": "distance_m",
        "icon": "mdi:map-marker-distance",
        "unit": UnitOfLength.KILOMETERS,
        "divisor": 1000,
        "precision": 2,
        "device_class": SensorDeviceClass.DISTANCE,
        "state_class": SensorStateClass.MEASUREMENT,
    },
    "SLEEP": {
        "field": "sleep_s",
        "icon": "mdi:sleep",
        "unit": UnitOfTime.MINUTES,
        "divisor": 60,
        "precision": 0,
        "device_class": SensorDeviceClass.DURATION,
        # Fi also reclassifies rest between nap and sleep, moving minutes
        # between these two after they were first reported.
        "state_class": SensorStateClass.MEASUREMENT,
    },
    "NAP": {
        "field": "nap_s",
        "icon": "mdi:power-sleep",
        "unit": UnitOfTime.MINUTES,
        "divisor": 60,
        "precision": 0,
        "device_class": SensorDeviceClass.DURATION,
        "state_class": SensorStateClass.MEASUREMENT,
    },
    "GOAL": {
        "field": "goal",
        "icon": "mdi:target",
        "unit": "steps",
        "divisor": 1,
        "precision": 0,
        "device_class": None,
        "state_class": SensorStateClass.MEASUREMENT,
    },
}

GENERIC_SENSORS = {
    "Activity Type": "mdi:run",
    "Current Place Name": "mdi:map-marker-radius",
    "Current Place Address": "mdi:map-marker",
    "Connected To": "mdi:human-greeting-proximity",
}


async def async_setup_entry(hass, config_entry, async_add_devices):
    """Add sensors for passed config_entry in HA."""
    coordinator = hass.data[DOMAIN][config_entry.entry_id]

    new_devices = []
    for pet in coordinator.data.pets:
        if pet.device is None:
            LOGGER.warning("Skipping pet %s: no collar paired", pet.name)
            continue
        new_devices.append(FiBatterySensor(coordinator, pet))
        new_devices.append(PetLastNightSleepSensor(coordinator, pet))
        new_devices.append(PetRestingSinceSensor(coordinator, pet))
        for stat_type in SENSOR_STATS_BY_TYPE:
            for stat_time in SENSOR_STATS_BY_TIME:
                new_devices.append(PetStatsSensor(coordinator, pet, stat_type, stat_time))
        for generic in GENERIC_SENSORS:
            new_devices.append(PetGenericSensor(coordinator, pet, generic))
        for behavior in BEHAVIOR_META:
            new_devices.append(PetBehaviorSensor(coordinator, pet, behavior))

    for base in coordinator.data.bases:
        new_devices.append(FiBaseSensor(coordinator, base))

    if new_devices:
        async_add_devices(new_devices)


class FiBaseSensor(FiBaseEntity, SensorEntity):
    """Online state of a Fi base."""

    _attr_icon = "mdi:wifi"

    @property
    def name(self):
        return f"{self.base.name} Base"

    @property
    def unique_id(self):
        return f"{self.base.base_id}-base"

    @property
    def native_value(self):
        return "Online" if self.base.online else "Offline"


class PetGenericSensor(FiPetEntity, SensorEntity):
    """Textual state: activity, place and connection source."""

    def __init__(self, coordinator, pet, stat_type):
        super().__init__(coordinator, pet)
        self._stat_type = stat_type

    @property
    def name(self):
        return f"{self.pet.name} {self._stat_type.title()}"

    @property
    def unique_id(self):
        formatted = self._stat_type.lower().replace(" ", "-")
        return f"{self.pet.pet_id}-{formatted}"

    @property
    def icon(self):
        return GENERIC_SENSORS[self._stat_type]

    @property
    def native_value(self):
        if self._stat_type == "Activity Type":
            return self.pet.activity_type
        if self._stat_type == "Current Place Name":
            return self.pet.place_name
        if self._stat_type == "Current Place Address":
            return self.pet.place_address
        return self.device.connection_state_type if self.device else None


class PetBehaviorSensor(FiPetEntity, SensorEntity):
    """Count of Fi-detected behaviour events today, with their times attached."""

    # Resets to 0 at local midnight like every other daily stat, and Fi revises
    # its counts, so this is not total_increasing either. For a per-day view,
    # chart the daily max.
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "events"

    def __init__(self, coordinator, pet, behavior):
        super().__init__(coordinator, pet)
        self._behavior = behavior

    @property
    def _events(self):
        return self.pet.behavior_events.get(self._behavior, [])

    @property
    def name(self):
        return f"{self.pet.name} {BEHAVIOR_META[self._behavior]['name']}"

    @property
    def unique_id(self):
        return f"{self.pet.pet_id}-behavior-{self._behavior}"

    @property
    def icon(self):
        return BEHAVIOR_META[self._behavior]["icon"]

    @property
    def native_value(self):
        return len(self._events)

    @property
    def extra_state_attributes(self):
        events = self._events
        return {
            "events": [event.isoformat() for event in events],
            "last_event": events[-1].isoformat() if events else None,
        }


class PetStatsSensor(FiPetEntity, SensorEntity):
    """One activity or rest metric for one period."""

    def __init__(self, coordinator, pet, stat_type, stat_time):
        super().__init__(coordinator, pet)
        self._stat_type = stat_type
        self._stat_time = stat_time

    @property
    def _meta(self):
        return STAT_META[self._stat_type]

    @property
    def name(self):
        return f"{self.pet.name} {self._stat_time.title()} {self._stat_type.title()}"

    @property
    def unique_id(self):
        return f"{self.pet.pet_id}-{self._stat_time.lower()}-{self._stat_type.lower()}"

    @property
    def device_class(self):
        return self._meta["device_class"]

    @property
    def state_class(self):
        return self._meta["state_class"]

    @property
    def suggested_display_precision(self):
        return self._meta["precision"]

    @property
    def icon(self):
        return self._meta["icon"]

    @property
    def native_unit_of_measurement(self):
        return self._meta["unit"]

    @property
    def native_value(self):
        stats = self.pet.stats.get(self._stat_time)
        if stats is None:
            return None
        raw = getattr(stats, self._meta["field"])
        if raw is None:
            return None
        divisor = self._meta["divisor"]
        return raw if divisor == 1 else round(raw / divisor, 2)


class PetLastNightSleepSensor(FiPetEntity, SensorEntity):
    """Fi's settled overnight sleep total.

    Fi attributes a rest session to the day it started and keeps topping that
    bucket up while it runs, so the daily sleep sensor reads 0 whenever the
    current session began yesterday. This is the figure the Fi app shows.
    """

    _attr_icon = "mdi:weather-night"
    _attr_native_unit_of_measurement = UnitOfTime.MINUTES
    _attr_device_class = SensorDeviceClass.DURATION
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 0

    @property
    def name(self):
        return f"{self.pet.name} Last Night Sleep"

    @property
    def unique_id(self):
        return f"{self.pet.pet_id}-last-night-sleep"

    @property
    def native_value(self):
        seconds = self.pet.last_night_sleep_s
        return None if seconds is None else round(seconds / 60, 2)

    @property
    def extra_state_attributes(self):
        return {
            "sleep_start": self.pet.last_night_start.isoformat() if self.pet.last_night_start else None,
            "sleep_end": self.pet.last_night_end.isoformat() if self.pet.last_night_end else None,
        }


class PetRestingSinceSensor(FiPetEntity, SensorEntity):
    """When the in-progress rest session began, if the pet is resting now."""

    _attr_icon = "mdi:bed-clock"
    _attr_device_class = SensorDeviceClass.TIMESTAMP

    @property
    def name(self):
        return f"{self.pet.name} Resting Since"

    @property
    def unique_id(self):
        return f"{self.pet.pet_id}-resting-since"

    @property
    def native_value(self):
        return self.pet.resting_since


class FiBatterySensor(FiPetEntity, SensorEntity):
    """Collar battery level."""

    _attr_device_class = SensorDeviceClass.BATTERY
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def name(self):
        return f"{self.pet.name} Collar Battery Level"

    @property
    def unique_id(self):
        return f"{self.pet.pet_id}-battery"

    @property
    def native_value(self):
        return self.device.battery_percent if self.device else None

    @property
    def icon(self):
        if (device := self.device) is None or device.battery_percent is None:
            return "mdi:battery-unknown"
        return icon_for_battery_level(battery_level=device.battery_percent, charging=device.is_charging)
