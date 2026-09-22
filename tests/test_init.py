"""End-to-end setup of the config entry against a mocked Fi client."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.fitracking import STALE_GRACE
from custom_components.fitracking.api.exceptions import FiConnectionError
from custom_components.fitracking.api.models import Base, Device, FiData, LedColor, Pet, Stats
from custom_components.fitracking.const import DOMAIN

ENTRY_DATA = {"username": "me@example.com", "password": "secret", "polling": "10"}


def _fi_data() -> FiData:
    pet = Pet(
        pet_id="p1",
        name="Scottie",
        breed="Corgi",
        photo_url="https://example.invalid/scottie.jpg",
        device=Device(
            device_id="d1",
            module_id="m1",
            build_id="b1",
            battery_percent=77,
            is_charging=False,
            led_enabled=False,
            led_color_hex="#ff0000",
            available_led_colors=(LedColor(code=8, hex_code="#ffffff"),),
            connection_state_type="ConnectedToBase",
            mode="NORMAL",
        ),
        activity_type="OngoingRest",
        latitude=45.65,
        longitude=25.6,
        place_name="Home",
        place_address="Somewhere",
        resting_since=datetime(2026, 9, 19, 17, 15, tzinfo=UTC),
        last_night_sleep_s=39823,
        stats={
            "DAILY": Stats(steps=435, goal=16000, distance_m=0.0, sleep_s=None, nap_s=None),
            "WEEKLY": Stats(steps=26742, goal=112000, distance_m=2546.0, sleep_s=98805, nap_s=35943),
            "MONTHLY": Stats(steps=26742, goal=480000, distance_m=2546.0, sleep_s=98805, nap_s=35943),
        },
        behavior_events={"barking": [datetime(2026, 9, 20, 1, 27, 28)]},
    )
    return FiData(pets=[pet], bases=[Base(base_id="b1", name="Kitchen", online=True)])


@pytest.fixture
def mock_client():
    with patch("custom_components.fitracking.FiClient", autospec=True) as client_cls:
        client = client_cls.return_value
        client.async_login = AsyncMock(return_value="u1")
        client.async_get_data = AsyncMock(return_value=_fi_data())
        yield client


async def _setup(hass) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, data=ENTRY_DATA)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_entry_sets_up(hass, mock_client):
    entry = await _setup(hass)
    assert entry.state is entry.state.LOADED
    assert mock_client.async_login.await_count == 1


async def test_entry_unloads_cleanly(hass, mock_client):
    entry = await _setup(hass)
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert DOMAIN not in hass.data or entry.entry_id not in hass.data[DOMAIN]


@pytest.mark.parametrize(
    ("entity_id", "expected"),
    [
        ("sensor.scottie_collar_battery_level", "77"),
        ("sensor.scottie_weekly_sleep", "1646.75"),  # 98805s -> minutes
        ("sensor.scottie_last_night_sleep", "663.72"),
        ("sensor.scottie_connected_to", "base"),
        ("sensor.scottie_current_place_name", "Home"),
        ("sensor.kitchen_base", "Online"),
        ("binary_sensor.scottie_collar_battery_charging", "off"),
        ("select.scottie_lost_mode", "Safe"),
    ],
)
async def test_entity_states(hass, mock_client, entity_id, expected):
    await _setup(hass)
    state = hass.states.get(entity_id)
    assert state is not None, f"{entity_id} was never created"
    assert state.state == expected


async def test_absent_rest_reads_unknown_not_zero(hass, mock_client):
    """The whole point of the rewrite: no fabricated zeros."""
    await _setup(hass)
    assert hass.states.get("sensor.scottie_daily_sleep").state == "unknown"


async def test_tracker_reports_position(hass, mock_client):
    await _setup(hass)
    state = hass.states.get("device_tracker.scottie_tracker")
    assert state.attributes["latitude"] == 45.65
    assert state.attributes["longitude"] == 25.6


async def test_a_brief_outage_keeps_the_last_good_state(hass, mock_client):
    """Fi's gateway 502s for tens of seconds; entities should ride that out."""
    entry = await _setup(hass)
    coordinator = hass.data[DOMAIN][entry.entry_id]

    mock_client.async_get_data.side_effect = FiConnectionError("Fi returned HTTP 502")
    await coordinator.async_refresh()
    await hass.async_block_till_done()

    assert coordinator.last_update_success
    assert hass.states.get("sensor.scottie_collar_battery_level").state == "77"


async def test_a_sustained_outage_still_goes_unavailable(hass, mock_client):
    """The grace is a window, not a mute button."""
    entry = await _setup(hass)
    coordinator = hass.data[DOMAIN][entry.entry_id]
    coordinator._last_success -= STALE_GRACE

    mock_client.async_get_data.side_effect = FiConnectionError("Fi returned HTTP 502")
    await coordinator.async_refresh()
    await hass.async_block_till_done()

    assert not coordinator.last_update_success
    assert hass.states.get("sensor.scottie_collar_battery_level").state == "unavailable"


async def test_behavior_sensor_counts_events(hass, mock_client):
    await _setup(hass)
    state = hass.states.get("sensor.scottie_barking")
    assert state.state == "1"
    assert state.attributes["last_event"] == "2026-09-20T01:27:28"
