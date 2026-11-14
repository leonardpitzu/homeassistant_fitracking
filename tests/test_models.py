"""Tests for parsing Fi's GraphQL payloads.

The central guarantee: absent data must stay None. pytryfi zeroed sleep and nap
before parsing and swallowed failures, which made "Fi sent nothing" and "the dog
slept nothing" the same value.
"""

from datetime import UTC, datetime, timedelta

import pytest

from custom_components.fitracking.api.models import (
    DAY_MINUTES,
    PHASE_ACTIVE,
    PHASE_DAY_SLEEP,
    PHASE_IDLE,
    PHASE_NIGHT_SLEEP,
    PHASE_NO_DATA,
    PHASE_OFFLINE,
    Device,
    LedColor,
    Pet,
    parse_behavior_trends,
)

# Aware, like dt_util.start_of_local_day(): Fi's timestamps are aware too, and
# the day timeline is built by subtracting the two.
MIDNIGHT = datetime(2026, 9, 19, tzinfo=UTC)
# Late enough that every fixture below lies in the past.
NOW = MIDNIGHT + timedelta(hours=23, minutes=59)


def _rest(sleep=None, nap=None, *, empty=False):
    if empty:
        return {"restSummary": None}
    return {"restSummary": {"sleepSecondsTotal": sleep, "napSecondsTotal": nap}}


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


def _pet(detail=None, trends=None, now=NOW, **root):
    pet = Pet.parse_profile({"id": "p1", "name": "Scottie", "breed": {"name": "Corgi"}})
    payload = {
        "pet": detail if detail is not None else _detail(),
        "getPetHealthTrendsForPet": trends,
    }
    pet.apply_detail(payload | root, MIDNIGHT, now)
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


def test_the_old_session_bucketed_shape_is_not_read():
    """restSummaryFeed files a whole night under the day it began; restFeed clips it.

    Reverting the query without the parser would silently restore the bug, so a
    payload in the old shape must read as absent rather than as data.
    """
    legacy = {"restSummaries": [{"data": {"sleepAmounts": [{"type": "SLEEP", "duration": 40306}]}}]}
    pet = _pet(_detail(dailyRest=legacy))
    assert pet.stats["DAILY"].sleep_s is None


def test_a_real_zero_survives_as_zero():
    """A genuine 0 from Fi must stay distinguishable from absence."""
    assert _pet().stats["DAILY"].sleep_s == 0


def test_absent_activity_section_is_none():
    pet = _pet(_detail(dailyActivity=None))
    assert pet.stats["DAILY"].steps is None
    assert pet.stats["DAILY"].distance_m is None


def test_distance_is_kept_in_metres():
    assert _pet().stats["WEEKLY"].distance_m == 2546.0


def _overnight(seconds, *, start="2026-09-18T20:15:00.000Z", end="2026-09-19T06:00:00.000Z"):
    return {
        "__typename": "ConcreteOvernightRestSummary",
        "date": "2026-09-19T12:00:00.000Z",
        "sleepStart": start,
        "sleepEnd": end,
        "sleepSeconds": seconds,
    }


UNAVAILABLE = {"__typename": "UnavailableOvernightRestSummary"}
# The night before last, settled the morning before MIDNIGHT.
PRIOR_NIGHT = _overnight(39823, start="2026-09-17T20:15:00.000Z", end="2026-09-18T06:00:00.000Z")


def test_overnight_unavailable_is_none():
    """Fi returns UnavailableOvernightRestSummary while the night is unsettled."""
    pet = _pet(_detail(lastNight=UNAVAILABLE, priorNight=UNAVAILABLE))
    assert pet.last_night_sleep_s is None


def test_overnight_concrete_is_read():
    pet = _pet(_detail(lastNight=_overnight(39823)))
    assert pet.last_night_sleep_s == 39823
    assert pet.last_night_start == datetime(2026, 9, 18, 20, 15, tzinfo=UTC)


def test_overnight_falls_back_while_the_current_night_runs():
    """Between local midnight and waking, yesterday's night is still in progress."""
    pet = _pet(_detail(lastNight=UNAVAILABLE, priorNight=_overnight(39823)))
    assert pet.last_night_sleep_s == 39823


def test_overnight_prefers_the_newer_night():
    pet = _pet(_detail(lastNight=_overnight(40306), priorNight=_overnight(58982)))
    assert pet.last_night_sleep_s == 40306


def test_a_night_whose_end_follows_the_collar_is_still_running():
    """Fi reports the night in progress with sleepEnd at the latest upload.

    Read as settled, `Last Night Sleep` showed tonight's partial total all
    night, growing every minute, instead of the last night that had ended.
    """
    now = MIDNIGHT + timedelta(hours=2, minutes=12, seconds=26)
    running = _overnight(20000, start="2026-09-18T20:38:00.000Z", end="2026-09-19T02:12:15.000Z")
    pet = _pet(_detail(lastNight=running, priorNight=PRIOR_NIGHT), now=now)
    assert pet.night_in_progress
    assert pet.last_night_sleep_s == 39823


def test_a_night_is_settled_once_its_end_stops_moving():
    now = MIDNIGHT + timedelta(hours=8)
    woke = _overnight(40306, start="2026-09-18T20:38:00.000Z", end="2026-09-19T07:43:56.000Z")
    pet = _pet(_detail(lastNight=woke, priorNight=PRIOR_NIGHT), now=now)
    assert not pet.night_in_progress
    assert pet.last_night_sleep_s == 40306
    assert pet.last_night_end == datetime(2026, 9, 19, 7, 43, 56, tzinfo=UTC)


def test_a_brief_stir_on_waking_does_not_settle_the_night():
    """Fi's end stands still while the dog stirs; under the tolerance it is still night."""
    now = MIDNIGHT + timedelta(hours=7, minutes=50)
    stirring = _overnight(40306, start="2026-09-18T20:38:00.000Z", end="2026-09-19T07:43:56.000Z")
    pet = _pet(_detail(lastNight=stirring, priorNight=PRIOR_NIGHT), now=now)
    assert pet.night_in_progress
    assert pet.last_night_sleep_s == 39823


class TestNightEndVersusQuietCollar:
    """Fi's end stops moving when the dog gets up, and when the collar cannot upload.

    Replays the shape of 23 Oct: uploads stop, the collar reads offline about
    three minutes later, comes back on the Base for a few minutes without
    uploading, drops out again, and the night carries on once it returns.
    """

    START = "2026-09-18T20:38:00.000Z"

    def _poll(self, pet, at, connection, end, seconds=20000):
        pet.apply_profile(
            {
                "id": "p1",
                "name": "Scottie",
                "device": {"id": "d1", "lastConnectionState": connection and {"__typename": connection}},
            }
        )
        night = _overnight(seconds, start=self.START, end=end)
        pet.apply_detail({"pet": _detail(lastNight=night, priorNight=PRIOR_NIGHT)}, MIDNIGHT, MIDNIGHT + at)
        return pet

    def _fresh(self):
        return Pet.parse_profile({"id": "p1", "name": "Scottie"})

    def _running_until_01_32(self):
        pet = self._fresh()
        self._poll(pet, timedelta(hours=1, minutes=30), "ConnectedToBase", "2026-09-19T01:30:00.000Z")
        return self._poll(
            pet, timedelta(hours=1, minutes=32, seconds=10), "ConnectedToBase", "2026-09-19T01:32:10.000Z"
        )

    def assert_running(self, pet):
        assert pet.night_in_progress
        assert pet.last_night_sleep_s == 39823

    def test_a_collar_out_of_reach_does_not_end_the_night(self):
        pet = self._running_until_01_32()
        for minute in (35, 50, 59):
            self._poll(pet, timedelta(hours=1, minutes=minute), "UnknownConnectivity", "2026-09-19T01:32:10.000Z")
            self.assert_running(pet)

    def test_a_collar_back_but_not_yet_uploading_does_not_end_it_either(self):
        pet = self._running_until_01_32()
        self._poll(pet, timedelta(hours=1, minutes=35), "UnknownConnectivity", "2026-09-19T01:32:10.000Z")
        self._poll(pet, timedelta(hours=2, minutes=1), "ConnectedToBase", "2026-09-19T01:32:10.000Z")
        self._poll(pet, timedelta(hours=2, minutes=4), "ConnectedToBase", "2026-09-19T01:32:10.000Z")
        self.assert_running(pet)
        self._poll(pet, timedelta(hours=2, minutes=5), "UnknownConnectivity", "2026-09-19T01:32:10.000Z")
        self._poll(pet, timedelta(hours=2, minutes=24), "ConnectedToBase", "2026-09-19T01:32:10.000Z")
        self.assert_running(pet)
        self._poll(pet, timedelta(hours=2, minutes=25), "ConnectedToBase", "2026-09-19T02:24:28.000Z")
        self.assert_running(pet)

    def test_a_reachable_collar_fi_stopped_extending_means_the_dog_is_up(self):
        """A collar reachable for 15 minutes after the end would have moved it."""
        pet = self._fresh()
        self._poll(pet, timedelta(hours=7, minutes=43, seconds=56), "ConnectedToBase", "2026-09-19T07:43:56.000Z")
        self._poll(pet, timedelta(hours=7, minutes=58), "ConnectedToBase", "2026-09-19T07:43:56.000Z", 40306)
        self.assert_running(pet)
        self._poll(pet, timedelta(hours=7, minutes=59), "ConnectedToBase", "2026-09-19T07:43:56.000Z", 40306)
        assert not pet.night_in_progress
        assert pet.last_night_sleep_s == 40306

    def test_the_wait_counts_from_the_collar_coming_back(self):
        pet = self._running_until_01_32()
        self._poll(pet, timedelta(hours=1, minutes=35), "UnknownConnectivity", "2026-09-19T01:32:10.000Z")
        self._poll(pet, timedelta(hours=2, minutes=1), "ConnectedToBase", "2026-09-19T01:32:10.000Z")
        self._poll(pet, timedelta(hours=2, minutes=15), "ConnectedToBase", "2026-09-19T01:32:10.000Z")
        self.assert_running(pet)
        self._poll(pet, timedelta(hours=2, minutes=16), "ConnectedToBase", "2026-09-19T01:32:10.000Z")
        assert not pet.night_in_progress

    def test_an_ended_night_stays_ended_when_the_collar_drops_out_later(self):
        pet = self._fresh()
        self._poll(pet, timedelta(hours=7, minutes=43, seconds=56), "ConnectedToBase", "2026-09-19T07:43:56.000Z")
        self._poll(pet, timedelta(hours=8), "ConnectedToBase", "2026-09-19T07:43:56.000Z", 40306)
        self._poll(pet, timedelta(hours=22), "UnknownConnectivity", "2026-09-19T07:43:56.000Z", 40306)
        assert not pet.night_in_progress
        assert pet.last_night_sleep_s == 40306

    def test_fis_trim_after_the_end_does_not_reopen_the_night(self):
        pet = self._fresh()
        self._poll(pet, timedelta(hours=7, minutes=44), "ConnectedToBase", "2026-09-19T07:44:04.000Z")
        self._poll(pet, timedelta(hours=8), "ConnectedToBase", "2026-09-19T07:44:04.000Z")
        self._poll(pet, timedelta(hours=8, minutes=1), "UnknownConnectivity", "2026-09-19T07:43:56.000Z", 40306)
        assert not pet.night_in_progress
        assert pet.last_night_sleep_s == 40306

    def test_a_restart_does_not_hold_back_a_night_that_ended_hours_ago(self):
        pet = self._poll(self._fresh(), timedelta(hours=14), "ConnectedToBase", "2026-09-19T07:43:56.000Z", 40306)
        assert not pet.night_in_progress
        assert pet.last_night_sleep_s == 40306

    def test_a_night_taken_as_ended_runs_again_when_fi_moves_its_end(self):
        """A restart cannot know the collar was just out of reach; Fi settles it."""
        pet = self._poll(self._fresh(), timedelta(hours=2, minutes=2), "ConnectedToBase", "2026-09-19T01:32:10.000Z")
        assert not pet.night_in_progress
        self._poll(pet, timedelta(hours=2, minutes=25), "ConnectedToBase", "2026-09-19T02:24:28.000Z")
        self.assert_running(pet)

    def test_a_restart_with_the_collar_out_of_reach_waits_for_it(self):
        pet = self._poll(self._fresh(), timedelta(hours=14), "UnknownConnectivity", "2026-09-19T07:43:56.000Z", 40306)
        self.assert_running(pet)
        self._poll(pet, timedelta(hours=14, minutes=5), "ConnectedToWifi", "2026-09-19T07:43:56.000Z", 40306)
        self.assert_running(pet)
        self._poll(pet, timedelta(hours=14, minutes=20), "ConnectedToWifi", "2026-09-19T07:43:56.000Z", 40306)
        assert pet.last_night_sleep_s == 40306

    def test_no_connection_report_is_no_news(self):
        pet = self._running_until_01_32()
        self._poll(pet, timedelta(hours=1, minutes=35), "UnknownConnectivity", "2026-09-19T01:32:10.000Z")
        self._poll(pet, timedelta(hours=2), None, "2026-09-19T01:32:10.000Z")
        self.assert_running(pet)

    def test_the_night_before_last_ignores_the_collar(self):
        pet = self._fresh()
        pet.apply_profile(
            {"id": "p1", "name": "Scottie", "device": {"id": "d1", "lastConnectionState": {"__typename": "X"}}}
        )
        pet.apply_detail({"pet": _detail(lastNight=UNAVAILABLE, priorNight=PRIOR_NIGHT)}, MIDNIGHT, NOW)
        assert pet.last_night_sleep_s == 39823


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


def _timeline(*intervals):
    return {"segmentedTimeline": {"intervals": list(intervals)}}


def _interval(kind, start_min, minutes):
    return {"intervalType": kind, "offset": start_min * 60, "length": minutes * 60}


class TestDayPhases:
    """The 1440-minute bar: every minute of the local day gets exactly one phase."""

    def _pet_with(self, rest=None, activity=None, now=NOW, **overrides):
        return _pet(
            _detail(**overrides),
            now=now,
            restTimeline=rest,
            activityTimeline=activity,
        )

    def test_rest_events_are_placed_where_they_happened(self):
        pet = self._pet_with(rest=_timeline(_interval("EVENT", 60, 120)))
        placed = [item for item in pet.day_segments if item.phase == PHASE_DAY_SLEEP]
        assert [(item.start_min, item.minutes) for item in placed] == [(60, 120)]

    def test_phases_always_total_a_full_day(self):
        pet = self._pet_with(
            rest=_timeline(_interval("EVENT", 0, 400), _interval("NOTHING", 400, 200)),
            activity=_timeline(_interval("EVENT", 450, 30)),
        )
        assert sum(item.minutes for item in pet.day_segments) == DAY_MINUTES

    def test_minutes_fi_has_not_reported_are_not_called_idle(self):
        """Anything past the collar's last report is unknown, not idle time."""
        pet = self._pet_with(rest=_timeline(_interval("EVENT", 0, 600)))
        tail = pet.day_segments[-1]
        assert (tail.phase, tail.start_min, tail.minutes) == (PHASE_NO_DATA, 600, 840)

    def test_rest_wins_where_a_padded_activity_stub_overlaps_it(self):
        """Fi stretches short events to a fixed render width, straddling real rest."""
        pet = self._pet_with(
            rest=_timeline(_interval("EVENT", 0, 480)),
            activity=_timeline(_interval("EVENT", 100, 12)),
        )
        assert all(item.phase != PHASE_ACTIVE for item in pet.day_segments)

    def test_activity_outside_rest_survives(self):
        pet = self._pet_with(
            rest=_timeline(_interval("EVENT", 0, 480)),
            activity=_timeline(_interval("EVENT", 600, 30)),
        )
        active = [item for item in pet.day_segments if item.phase == PHASE_ACTIVE]
        assert [(item.start_min, item.minutes) for item in active] == [(600, 30)]

    def test_last_nights_session_is_split_out_of_the_days_rest(self):
        """Only the part of the night after local midnight belongs to today."""
        pet = self._pet_with(
            rest=_timeline(_interval("EVENT", 0, 600)),
            lastNight=_overnight(40306, start="2026-09-18T21:19:00.000Z", end="2026-09-19T05:33:00.000Z"),
        )
        by_phase = {item.phase: item for item in pet.day_segments}
        assert (by_phase[PHASE_NIGHT_SLEEP].start_min, by_phase[PHASE_NIGHT_SLEEP].minutes) == (0, 333)
        assert by_phase[PHASE_DAY_SLEEP].start_min == 333

    def test_rest_outside_the_night_window_stays_day_sleep(self):
        pet = self._pet_with(
            rest=_timeline(_interval("EVENT", 700, 60)),
            lastNight=_overnight(40306, start="2026-09-18T21:19:00.000Z", end="2026-09-19T05:33:00.000Z"),
        )
        assert all(item.phase != PHASE_NIGHT_SLEEP for item in pet.day_segments)

    def test_a_night_still_running_counts_as_night_sleep(self):
        """Fi answers Unavailable until the night ends: the dog is still in it.

        Without this the early hours read as day rest every single morning, and
        then silently turn into night sleep once Fi settles the night.
        """
        pet = self._pet_with(
            rest=_timeline(_interval("EVENT", 0, 180)),
            lastNight=UNAVAILABLE,
            priorNight=_overnight(40306, start="2026-09-17T21:19:00.000Z", end="2026-09-18T05:33:00.000Z"),
        )
        assert [(item.phase, item.minutes) for item in pet.day_segments][0] == (PHASE_NIGHT_SLEEP, 180)

    def _two_am(self, rest_minutes):
        """02:12:26, inside a night Fi reports with its end at the 02:12:15 upload."""
        return self._pet_with(
            rest=_timeline(_interval("EVENT", 0, rest_minutes)),
            lastNight=_overnight(20000, start="2026-09-18T20:38:00.000Z", end="2026-09-19T02:12:15.000Z"),
            priorNight=_overnight(40306, start="2026-09-17T21:19:00.000Z", end="2026-09-18T05:33:00.000Z"),
            now=MIDNIGHT + timedelta(hours=2, minutes=12, seconds=26),
        )

    def test_a_night_reported_with_a_moving_end_is_all_night_sleep(self):
        """The running night used to be cut at its provisional end, and Fi's
        padding past it read as day sleep: the state flipped every minute."""
        segments = self._two_am(143).day_segments
        assert [(item.phase, item.minutes) for item in segments][0] == (PHASE_NIGHT_SLEEP, 132)
        assert all(item.phase != PHASE_DAY_SLEEP for item in segments)

    def test_nothing_after_now_is_reported(self):
        """Fi pads the ongoing rest to its render width, minutes into the future."""
        tail = self._two_am(143).day_segments[-1]
        assert (tail.phase, tail.start_min) == (PHASE_NO_DATA, 132)

    def test_a_padded_and_an_unpadded_answer_build_the_same_bar(self):
        """Fi alternates the two within seconds; each switch was a recorder row."""
        assert self._two_am(132).day_segments == self._two_am(143).day_segments

    def _rest_until(self, rest_end, activity_end, now_min):
        """A night in progress whose rest bar reached `rest_end`, the activity bar `activity_end`."""
        return self._pet_with(
            rest=_timeline(_interval("EVENT", 0, rest_end)),
            activity=_timeline(_interval("NOTHING", 0, activity_end)),
            lastNight=UNAVAILABLE,
            now=MIDNIGHT + timedelta(minutes=now_min, seconds=1),
        )

    def test_a_rest_bar_a_minute_behind_is_not_a_stir(self):
        """Fi moves the activity bar on ~10 s before the rest bar.

        For that one poll a rest still running ended a minute short, and the
        new minute read as idle: 206 one-poll blips in 30 days.
        """
        segments = self._rest_until(28, 29, 29).day_segments
        assert [(item.phase, item.start_min, item.minutes) for item in segments] == [
            (PHASE_NIGHT_SLEEP, 0, 28),
            (PHASE_NO_DATA, 28, DAY_MINUTES - 28),
        ]

    def test_a_dog_that_got_up_shows_a_minute_later(self):
        """Holding the newest minute back delays a real stir; it does not hide it."""
        segments = self._rest_until(28, 30, 30).day_segments
        assert [(item.phase, item.start_min, item.minutes) for item in segments][:2] == [
            (PHASE_NIGHT_SLEEP, 0, 28),
            (PHASE_IDLE, 28, 2),
        ]

    def test_padding_cannot_paint_past_now_either(self):
        """A padded stub must not reach into minutes that have not happened."""
        pet = self._pet_with(
            rest=_timeline(_interval("EVENT", 0, 100)),
            activity=_timeline(_interval("EVENT", 100, 12)),
            now=MIDNIGHT + timedelta(minutes=105),
        )
        assert [(item.phase, item.start_min, item.minutes) for item in pet.day_segments][-2:] == [
            (PHASE_ACTIVE, 100, 5),
            (PHASE_NO_DATA, 105, DAY_MINUTES - 105),
        ]

    def test_a_night_fi_never_reported_is_not_assumed_to_be_running(self):
        """Absent is not the same as unsettled; only Unavailable means in progress."""
        pet = self._pet_with(rest=_timeline(_interval("EVENT", 0, 180)))
        assert pet.day_segments[0].phase == PHASE_DAY_SLEEP

    def test_tonights_rest_is_the_next_night_starting(self):
        """A day is night, day, next night: the evening block closes it."""
        pet = self._pet_with(rest=_timeline(_interval("EVENT", 20 * 60, 180)), lastNight=_overnight(40000))
        night = [item for item in pet.day_segments if item.phase == PHASE_NIGHT_SLEEP]
        assert [(item.start_min, item.minutes) for item in night] == [(1200, 180)]

    def test_a_brief_stir_does_not_end_the_evening_night(self):
        """Fi's padded stubs would otherwise chop the evening into naps."""
        pet = self._pet_with(
            rest=_timeline(_interval("EVENT", 19 * 60, 120), _interval("EVENT", 21 * 60 + 6, 114)),
            activity=_timeline(_interval("EVENT", 21 * 60, 6)),
            lastNight=_overnight(40000),
        )
        night = [item for item in pet.day_segments if item.phase == PHASE_NIGHT_SLEEP]
        assert [(item.start_min, item.minutes) for item in night] == [(1140, 120), (1266, 114)]

    def test_an_afternoon_nap_in_progress_is_not_the_night(self):
        """Rest reaching the last report is only the night once bedtime is near."""
        pet = self._pet_with(rest=_timeline(_interval("EVENT", 13 * 60, 120)), lastNight=_overnight(40000))
        assert all(item.phase != PHASE_NIGHT_SLEEP for item in pet.day_segments)

    def test_an_evening_nap_the_dog_got_up_from_is_not_the_night(self):
        pet = self._pet_with(
            rest=_timeline(_interval("EVENT", 19 * 60, 60)),
            activity=_timeline(_interval("EVENT", 20 * 60 + 30, 60)),
            lastNight=_overnight(40000),
        )
        assert all(item.phase != PHASE_NIGHT_SLEEP for item in pet.day_segments)

    def test_going_to_bed_earlier_than_last_night_still_counts(self):
        """Last night began 20:15, so an 18:30 start is within the margin."""
        pet = self._pet_with(rest=_timeline(_interval("EVENT", 18 * 60 + 30, 240)), lastNight=_overnight(40000))
        night = [item for item in pet.day_segments if item.phase == PHASE_NIGHT_SLEEP]
        assert [(item.start_min, item.minutes) for item in night] == [(1110, 240)]

    def test_an_unbroken_afternoon_does_not_drag_the_night_backwards(self):
        """Rest from 16:00 to the last report is night only from the margin on."""
        pet = self._pet_with(rest=_timeline(_interval("EVENT", 16 * 60, 420)), lastNight=_overnight(40000))
        by_phase = {item.phase: item for item in pet.day_segments}
        assert by_phase[PHASE_DAY_SLEEP].start_min == 960
        assert by_phase[PHASE_NIGHT_SLEEP].start_min == 18 * 60 + 15

    def test_without_a_settled_night_the_evening_is_not_guessed_at(self):
        """There is nothing to anchor a bedtime on, so no night is invented."""
        pet = self._pet_with(rest=_timeline(_interval("EVENT", 20 * 60, 180)))
        assert all(item.phase != PHASE_NIGHT_SLEEP for item in pet.day_segments)

    def test_a_charging_collar_reports_nothing_rather_than_idling(self):
        pet = self._pet_with(rest=_timeline(_interval("EVENT", 0, 60), _interval("DEVICE_OFF", 60, 30)))
        offline = [item for item in pet.day_segments if item.phase == PHASE_OFFLINE]
        assert [(item.start_min, item.minutes) for item in offline] == [(60, 30)]

    def test_no_timeline_produces_no_bar(self):
        """An empty bar must be distinguishable from a day spent doing nothing."""
        assert self._pet_with().day_segments == ()


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
        events = parse_behavior_trends(self.PAYLOAD, MIDNIGHT, NOW)
        assert len(events["eating"]) == 1
        assert len(events["cleaning_self"]) == 2

    def test_offsets_are_seconds_since_local_midnight(self):
        events = parse_behavior_trends(self.PAYLOAD, MIDNIGHT, NOW)
        assert events["eating"][0] == datetime(2026, 9, 19, 1, 27, 28, tzinfo=UTC)

    def test_key_strips_the_period_suffix(self):
        """Fi ids look like "cleaning_self:DAY"; the sensor keys on the stem."""
        assert set(parse_behavior_trends(self.PAYLOAD, MIDNIGHT, NOW)) == {
            "eating",
            "cleaning_self",
            "barking",
        }

    def test_no_events_is_empty_not_missing(self):
        """A quiet behaviour must still report, so the sensor reads 0 not None."""
        assert parse_behavior_trends(self.PAYLOAD, MIDNIGHT, NOW)["barking"] == []

    @pytest.mark.parametrize("payload", [None, {}, {"behaviorTrends": None}])
    def test_empty_response_does_not_raise(self, payload):
        assert parse_behavior_trends(payload, MIDNIGHT, NOW) == {}

    def test_events_are_in_time_order(self):
        """Fi's order varies between answers; each reshuffle was a recorder row."""
        payload = {
            "behaviorTrends": [
                {
                    "id": "cleaning_self:DAY",
                    "chart": {
                        "intervals": [
                            {"intervalType": "EVENT", "offset": 5510},
                            {"intervalType": "EVENT", "offset": 5392},
                        ]
                    },
                },
            ]
        }
        events = parse_behavior_trends(payload, MIDNIGHT, NOW)["cleaning_self"]
        assert events == sorted(events)

    def test_yesterdays_chart_just_after_midnight_is_not_today(self):
        """For a few minutes Fi still serves yesterday's DAY chart.

        Its offsets landed later today, so every sensor briefly read
        yesterday's count -- the very value the README says to chart as the
        daily max.
        """
        events = parse_behavior_trends(self.PAYLOAD, MIDNIGHT, MIDNIGHT + timedelta(seconds=11))
        assert events == {"eating": [], "cleaning_self": [], "barking": []}

    def test_an_event_moments_ago_is_kept(self):
        just_after = MIDNIGHT + timedelta(seconds=5510, minutes=1)
        assert len(parse_behavior_trends(self.PAYLOAD, MIDNIGHT, just_after)["cleaning_self"]) == 2
