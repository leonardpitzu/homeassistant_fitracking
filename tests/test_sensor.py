"""Tests for the Fi Tracking sensor platform."""

from dataclasses import fields

import pytest
from homeassistant.components.sensor import SensorStateClass

from custom_components.fitracking.api.models import PERIODS, Stats
from custom_components.fitracking.const import (
    CONNECTION_ICONS,
    CONNECTION_OFFLINE,
    CONNECTION_STATES,
    SENSOR_STATS_BY_TIME,
    SENSOR_STATS_BY_TYPE,
)
from custom_components.fitracking.sensor import STAT_META, PetBehaviorSensor

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
        ("SLEEP", 5454, 90.9),  # seconds -> minutes
        ("NAP", 3441, 57.35),
    ],
)
def test_unit_scaling(stat_type, raw, expected):
    meta = STAT_META[stat_type]
    value = raw if meta["divisor"] == 1 else round(raw / meta["divisor"], 2)
    assert value == expected


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
