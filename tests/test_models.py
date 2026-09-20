"""Tests for parsing Fi's GraphQL payloads.

The central guarantee: absent data must stay None. pytryfi zeroed sleep and nap
before parsing and swallowed failures, which made "Fi sent nothing" and "the dog
slept nothing" the same value.
"""

from datetime import UTC, datetime, timedelta

import pytest

from custom_components.fitracking.api.models import (
    Device,
    LedColor,
    Pet,
    parse_behavior_trends,
)

MIDNIGHT = datetime(2026, 9, 19)


def _rest(sleep=None, nap=None, *, empty=False):
    if empty:
        return {"restSummaries": []}
    amounts = []
    if sleep is not None:
        amounts.append({"type": "SLEEP", "duration": sleep})
    if nap is not None:
        amounts.append({"type": "NAP", "duration": nap})
    return {"restSummaries": [{"start": "x", "end": "y", "data": {"sleepAmounts": amounts}}]}


def _detail(**overrides):
    payload = {
        "dailyActivity": {"totalSteps": 435, "stepGoal": 16000, "totalDistance": 0},
        "weeklyActivity": {"totalSteps": 26742, "stepGoal": 112000, "totalDistance": 2546},
        "monthlyActivity": {"totalSteps": 26742, "stepGoal": 480000, "totalDistance": 2546},
        "dailyRest": _rest(0, 0),
        "weeklyRest": _rest(98805, 35943),
        "monthlyRest": _rest(98805, 35943),
    }
    payload.update(overrides)
    return payload


def _pet(detail=None, trends=None):
    pet = Pet.parse_profile({"id": "p1", "name": "Scottie", "breed": {"name": "Corgi"}})
    pet.apply_detail(detail if detail is not None else _detail(), trends, MIDNIGHT)
    return pet


def test_periods_map_to_the_right_aliases():
    """Guards an off-by-name slip: weekly totals must not land under daily."""
    pet = _pet()
    assert pet.stats["DAILY"].steps == 435
    assert pet.stats["WEEKLY"].steps == 26742
    assert pet.stats["DAILY"].sleep_s == 0
    assert pet.stats["WEEKLY"].sleep_s == 98805
    assert pet.stats["MONTHLY"].nap_s == 35943


def test_missing_rest_summary_is_none_not_zero():
    """The regression that hid Fi's daily rest behaviour."""
    pet = _pet(_detail(dailyRest=_rest(empty=True)))
    assert pet.stats["DAILY"].sleep_s is None
    assert pet.stats["DAILY"].nap_s is None


def test_a_real_zero_survives_as_zero():
    """A genuine 0 from Fi must stay distinguishable from absence."""
    assert _pet().stats["DAILY"].sleep_s == 0


def test_absent_activity_section_is_none():
    pet = _pet(_detail(dailyActivity=None))
    assert pet.stats["DAILY"].steps is None
    assert pet.stats["DAILY"].distance_m is None


def test_distance_is_kept_in_metres():
    assert _pet().stats["WEEKLY"].distance_m == 2546.0


def test_overnight_unavailable_is_none():
    """Fi returns UnavailableOvernightRestSummary while the night is unsettled."""
    pet = _pet(_detail(overnightRestSummary={"__typename": "UnavailableOvernightRestSummary"}))
    assert pet.last_night_sleep_s is None


def test_overnight_concrete_is_read():
    pet = _pet(
        _detail(
            overnightRestSummary={
                "__typename": "ConcreteOvernightRestSummary",
                "date": "2026-09-19T12:00:00.000Z",
                "sleepStart": "2026-09-18T20:15:00.000Z",
                "sleepEnd": "2026-09-19T06:00:00.000Z",
                "sleepSeconds": 39823,
            }
        )
    )
    assert pet.last_night_sleep_s == 39823
    assert pet.last_night_start == datetime(2026, 9, 18, 20, 15, tzinfo=UTC)


def test_ongoing_rest_records_when_it_started():
    pet = _pet(
        _detail(
            ongoingActivity={
                "__typename": "OngoingRest",
                "start": "2026-09-19T17:15:57.919Z",
                "position": {"latitude": 45.65, "longitude": 25.6},
                "place": {"name": "Home", "address": "Somewhere"},
            }
        )
    )
    assert pet.resting_since == datetime(2026, 9, 19, 17, 15, 57, 919000, tzinfo=UTC)
    assert pet.latitude == 45.65
    assert pet.place_name == "Home"


def test_walk_uses_the_latest_position():
    pet = _pet(
        _detail(
            ongoingActivity={
                "__typename": "OngoingWalk",
                "start": "2026-09-19T17:15:57.919Z",
                "positions": [
                    {"position": {"latitude": 1.0, "longitude": 2.0}},
                    {"position": {"latitude": 3.0, "longitude": 4.0}},
                ],
            }
        )
    )
    assert (pet.latitude, pet.longitude) == (3.0, 4.0)
    assert pet.resting_since is None


def test_pet_without_a_collar_parses_with_no_device():
    pet = Pet.parse_profile({"id": "p2", "name": "Collarless"})
    assert pet.device is None
    assert pet.is_lost is False


class TestDevice:
    def _device(self, **ops):
        return Device.parse(
            {
                "id": "d1",
                "moduleId": "m1",
                "info": {"buildId": "b1", "batteryPercent": 77},
                "operationParams": {"mode": "NORMAL", "ledEnabled": True, **ops},
                "ledColor": {"hexCode": "#ff0000"},
                "availableLedColors": [{"ledColorCode": 8, "hexCode": "#ffffff"}],
                "lastConnectionState": {"__typename": "ConnectedToBase"},
            }
        )

    def test_basic_fields(self):
        device = self._device(ledOffAt=None)
        assert device.battery_percent == 77
        assert device.build_id == "b1"
        assert device.connection_state_type == "ConnectedToBase"
        assert device.available_led_colors == (LedColor(code=8, hex_code="#ffffff"),)

    def test_missing_is_charging_means_not_charging(self):
        """V2 collars dropped the field entirely."""
        assert self._device(ledOffAt=None).is_charging is False

    def test_led_reads_off_once_fi_auto_extinguished_it(self):
        """Fi leaves ledEnabled true past ledOffAt."""
        now = datetime(2026, 9, 20, 12, 0, tzinfo=UTC)
        device = self._device(ledOffAt="2026-09-20T10:00:00.000Z")
        assert device.led_on(now) is False
        assert device.led_on(now - timedelta(hours=4)) is True

    def test_lost_mode(self):
        assert self._device(mode="LOST_DOG", ledOffAt=None).is_lost is True


class TestBehaviorTrends:
    PAYLOAD = {
        "behaviorTrends": [
            {
                "id": "eating:DAY",
                "chart": {
                    "intervals": [
                        {"intervalType": "NOTHING", "offset": 0},
                        {"intervalType": "EVENT", "offset": 5248},
                        {"intervalType": "NOTHING", "offset": 5948},
                    ]
                },
            },
            {
                "id": "cleaning_self:DAY",
                "chart": {
                    "intervals": [
                        {"intervalType": "EVENT", "offset": 5392},
                        {"intervalType": "EVENT", "offset": 5510},
                    ]
                },
            },
            {"id": "barking:DAY", "chart": {"intervals": []}},
        ]
    }

    def test_only_event_intervals_are_kept(self):
        """NOTHING intervals are the gaps between events, not events."""
        events = parse_behavior_trends(self.PAYLOAD, MIDNIGHT)
        assert len(events["eating"]) == 1
        assert len(events["cleaning_self"]) == 2

    def test_offsets_are_seconds_since_local_midnight(self):
        events = parse_behavior_trends(self.PAYLOAD, MIDNIGHT)
        assert events["eating"][0] == datetime(2026, 9, 19, 1, 27, 28)

    def test_key_strips_the_period_suffix(self):
        """Fi ids look like "cleaning_self:DAY"; the sensor keys on the stem."""
        assert set(parse_behavior_trends(self.PAYLOAD, MIDNIGHT)) == {
            "eating",
            "cleaning_self",
            "barking",
        }

    def test_no_events_is_empty_not_missing(self):
        """A quiet behaviour must still report, so the sensor reads 0 not None."""
        assert parse_behavior_trends(self.PAYLOAD, MIDNIGHT)["barking"] == []

    @pytest.mark.parametrize("payload", [None, {}, {"behaviorTrends": None}])
    def test_empty_response_does_not_raise(self, payload):
        assert parse_behavior_trends(payload, MIDNIGHT) == {}
