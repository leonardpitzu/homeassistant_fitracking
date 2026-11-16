"""Tests for the Fi Tracking sensor platform."""

from dataclasses import fields
from decimal import ROUND_HALF_UP, Decimal
from types import SimpleNamespace

import pytest
from homeassistant.components.sensor import SensorStateClass

from custom_components.fitracking.api.models import (
    PERIODS,
    PHASE_NIGHT_SLEEP,
    PHASE_NO_DATA,
    Pet,
    Stats,
    build_day_phases,
)
from custom_components.fitracking.const import (
    CONNECTION_ICONS,
    CONNECTION_OFFLINE,
    CONNECTION_STATES,
    SENSOR_STATS_BY_TIME,
    SENSOR_STATS_BY_TYPE,
)
from custom_components.fitracking.sensor import STAT_META, PetBehaviorSensor, PetDayPhaseSensor, scale_stat

STATS_FIELDS = {field.name for field in fields(Stats)}


def test_every_connection_state_has_an_icon():
    """The sensor's options are the icon keys, so a gap would break its icon."""
    assert set(CONNECTION_STATES.values()) | {CONNECTION_OFFLINE} == set(CONNECTION_ICONS)


def test_fi_typenames_are_not_the_state():
    """`ConnectedToUser` is Fi's GraphQL type, not something to show a user."""
    assert not any(state.startswith("ConnectedTo") for state in CONNECTION_ICONS)


def test_every_stat_type_has_metadata():
    assert set(SENSOR_STATS_BY_TYPE) == set(STAT_META)


def test_goal_is_exposed():
    """Upstream shipped a TODO comment instead of the goal sensors."""
    assert "GOAL" in SENSOR_STATS_BY_TYPE


@pytest.mark.parametrize("stat_type", SENSOR_STATS_BY_TYPE)
def test_metadata_field_exists_on_stats(stat_type):
    """Every stat must address a real attribute of the Stats model."""
    assert STAT_META[stat_type]["field"] in STATS_FIELDS


def test_all_stats_fields_are_covered():
    assert {meta["field"] for meta in STAT_META.values()} == STATS_FIELDS


def test_periods_match_the_model():
    """The sensor's period names key directly into Pet.stats."""
    assert set(SENSOR_STATS_BY_TIME) == set(PERIODS)


@pytest.mark.parametrize(
    ("stat_type", "raw", "expected"),
    [
        ("STEPS", 12049, 12049),
        ("GOAL", 12000, 12000),
        ("DISTANCE", 1430, 1.43),  # metres -> km
        ("SLEEP", 5454, 91),  # seconds -> whole minutes
        ("SLEEP", 5430, 91),  # a half rounds up, as the display rounds it
        ("SLEEP", 5429, 90),
        ("NAP", 3441, 57),
        ("ACTIVE", 491, 8.18),  # left as it was
    ],
)
def test_unit_scaling(stat_type, raw, expected):
    assert scale_stat(STAT_META[stat_type], raw) == expected


def test_whole_minutes_show_what_the_display_always_showed():
    """Cards and badges showed the old two-decimal value at 0 decimals, halves up.

    Every second of a 24-hour day must read the same as it did on screen.
    """
    for seconds in range(24 * 3600 + 1):
        stored_before = Decimal(str(round(seconds / 60, 2)))
        shown_before = int(stored_before.quantize(Decimal(1), rounding=ROUND_HALF_UP))
        assert scale_stat(STAT_META["SLEEP"], seconds) == shown_before, seconds


def test_icons_are_distinct():
    """Upstream returned mdi:map-marker-distance for every stat, sleep included."""
    icons = [meta["icon"] for meta in STAT_META.values()]
    assert len(icons) == len(set(icons))


def test_every_stat_reports_a_state_class():
    """Without a state class there are no long-term statistics."""
    assert all(meta["state_class"] is not None for meta in STAT_META.values())


def test_no_sensor_is_a_counter():
    """Fi revises these downward after first reporting them.

    HA's reset tolerance is relative, so a small absolute revision early in a
    period is a large enough drop to read as a counter reset and inflate the sum.
    """
    state_classes = [meta["state_class"] for meta in STAT_META.values()]
    # HA's entity metaclass turns _attr_state_class into a descriptor, so the
    # value only resolves through the public property on an instance.
    state_classes.append(object.__new__(PetBehaviorSensor).state_class)
    assert all(state_class is SensorStateClass.MEASUREMENT for state_class in state_classes)


def test_day_phase_holds_the_night_over_midnight():
    """Today's bar reports nothing in its first minute, since the minute in
    progress never counts. The night does not blink to unknown meanwhile."""
    pet = Pet(pet_id="p1", name="Rex")
    coordinator = SimpleNamespace(data=SimpleNamespace(get_pet=lambda _: pet))
    sensor = PetDayPhaseSensor(coordinator, pet)

    def bar(seconds):
        return {"segmentedTimeline": {"intervals": [{"intervalType": "EVENT", "offset": 0, "length": seconds}]}}

    night = (0, 1440)

    # 23:59 yesterday, asleep since bedtime.
    pet.day_segments = build_day_phases(bar(86400), None, None, (1200, 1440), 1439)
    assert sensor.native_value == PHASE_NIGHT_SLEEP

    # 00:00:30 -- today's bar has its rest, padded into the future, but the
    # minute in progress is all there is and it never counts.
    rest = bar(700)
    pet.day_segments = build_day_phases(rest, None, night, None, 0)
    assert {segment.phase for segment in pet.day_segments} == {PHASE_NO_DATA}
    assert sensor.native_value == PHASE_NIGHT_SLEEP

    # 00:01 -- today's first minute is in.
    pet.day_segments = build_day_phases(rest, None, night, None, 1)
    assert sensor.native_value == PHASE_NIGHT_SLEEP


def test_day_phase_is_unknown_until_fi_reports_anything():
    pet = Pet(pet_id="p1", name="Rex")
    coordinator = SimpleNamespace(data=SimpleNamespace(get_pet=lambda _: pet))
    assert PetDayPhaseSensor(coordinator, pet).native_value is None
