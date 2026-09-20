"""Typed models parsed from Fi's GraphQL payloads.

Absent data is None, never a substituted zero. pytryfi defaulted sleep and nap
to 0 before parsing and swallowed the failure, which made "Fi sent nothing"
indistinguishable from "the dog slept nothing" -- the bug that hid Fi's daily
rest behaviour for as long as it did.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from itertools import groupby

from .queries import ACTIVITY_ONGOING_WALK, PET_MODE_LOST

PERIODS = ("DAILY", "WEEKLY", "MONTHLY")

DAY_MINUTES = 24 * 60

# One of these is assigned to every minute of the local day, so the phases
# always add up to 1440 no matter what Fi did or did not report.
PHASE_NIGHT_SLEEP = "night_sleep"
PHASE_DAY_SLEEP = "day_sleep"
PHASE_ACTIVE = "active"
PHASE_AWAKE = "awake"
PHASE_OFFLINE = "offline"
PHASE_NO_DATA = "no_data"
PHASES = (
    PHASE_NIGHT_SLEEP,
    PHASE_DAY_SLEEP,
    PHASE_ACTIVE,
    PHASE_AWAKE,
    PHASE_OFFLINE,
    PHASE_NO_DATA,
)

# A day runs night, day, next night. Fi settles a night only the morning after,
# so the evening one has to be recognised while it is happening: rest that is
# still running at the collar's last report and started this late is the night
# beginning, not another nap. Brief stirs inside it stay inside it -- Fi's own
# render pad is ~12 minutes, so the tolerance sits just above that.
NIGHT_EVENING_START = 18 * 60
NIGHT_GAP_TOLERANCE = 15


def _as_int(value: object) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _as_float(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_datetime(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _is_settled(raw: dict | None) -> bool:
    """Fi answers UnavailableOvernightRestSummary until a night has ended."""
    return bool(raw) and raw.get("__typename") == "ConcreteOvernightRestSummary"


@dataclass(frozen=True, slots=True)
class LedColor:
    code: int
    hex_code: str
    name: str | None = None

    @classmethod
    def parse(cls, raw: dict) -> LedColor | None:
        code = _as_int(raw.get("ledColorCode"))
        hex_code = raw.get("hexCode")
        if code is None or not hex_code:
            return None
        return cls(code=code, hex_code=hex_code, name=raw.get("name"))


@dataclass(frozen=True, slots=True)
class Device:
    device_id: str
    module_id: str | None = None
    build_id: str | None = None
    battery_percent: int | None = None
    is_charging: bool = False
    led_enabled: bool = False
    led_off_at: datetime | None = None
    led_color_hex: str | None = None
    available_led_colors: tuple[LedColor, ...] = ()
    connection_state_type: str | None = None
    mode: str | None = None
    has_active_subscription: bool | None = None

    @property
    def is_lost(self) -> bool:
        return self.mode == PET_MODE_LOST

    def led_on(self, now: datetime) -> bool:
        """Fi leaves ledEnabled true after auto-extinguishing the collar LED."""
        if not self.led_enabled:
            return False
        return self.led_off_at is None or now < self.led_off_at

    @classmethod
    def parse(cls, raw: dict | None) -> Device | None:
        if not raw or not raw.get("id"):
            return None
        info = raw.get("info") or {}
        ops = raw.get("operationParams") or {}
        colors = tuple(
            color
            for color in (LedColor.parse(item) for item in raw.get("availableLedColors") or [])
            if color is not None
        )
        return cls(
            device_id=raw["id"],
            module_id=raw.get("moduleId"),
            build_id=info.get("buildId"),
            battery_percent=_as_int(info.get("batteryPercent")),
            # V2 collars dropped isCharging; its absence is "not charging".
            is_charging=bool(info.get("isCharging")),
            led_enabled=bool(ops.get("ledEnabled")),
            led_off_at=_as_datetime(ops.get("ledOffAt")),
            led_color_hex=(raw.get("ledColor") or {}).get("hexCode"),
            available_led_colors=colors,
            connection_state_type=(raw.get("lastConnectionState") or {}).get("__typename"),
            mode=ops.get("mode"),
            has_active_subscription=raw.get("hasActiveSubscription"),
        )


@dataclass(frozen=True, slots=True)
class Stats:
    """One period's activity and rest totals. Any field may legitimately be None."""

    steps: int | None = None
    goal: int | None = None
    distance_m: float | None = None
    sleep_s: int | None = None
    nap_s: int | None = None
    active_s: int | None = None


def _parse_activity(raw: dict | None) -> tuple[int | None, int | None, float | None, int | None]:
    if not raw:
        return None, None, None, None
    return (
        _as_int(raw.get("totalSteps")),
        _as_int(raw.get("stepGoal")),
        _as_float(raw.get("totalDistance")),
        _as_int(raw.get("totalActiveTimeSeconds")),
    )


def _parse_rest(raw: dict | None) -> tuple[int | None, int | None]:
    """Return (sleep, nap) seconds for the period, or (None, None) when absent.

    restFeed clips a session at the period boundary, so a night that began
    yesterday contributes only the part that fell inside today -- the split the
    Fi app shows. A zero here is therefore a real zero, not a bucketing artefact.
    """
    summary = (raw or {}).get("restSummary")
    if not summary:
        return None, None
    return _as_int(summary.get("sleepSecondsTotal")), _as_int(summary.get("napSecondsTotal"))


@dataclass(frozen=True, slots=True)
class DaySegment:
    """A run of consecutive minutes of the local day spent in one phase."""

    phase: str
    start_min: int
    minutes: int


def _intervals(raw: dict | None) -> list[dict]:
    return ((raw or {}).get("segmentedTimeline") or {}).get("intervals") or []


def _span(interval: dict) -> tuple[int, int] | None:
    """One interval as [start, end) minutes of the local day, or None if empty."""
    offset = _as_float(interval.get("offset"))
    length = _as_float(interval.get("length"))
    if offset is None or length is None:
        return None
    start = max(0, int(offset // 60))
    end = min(DAY_MINUTES, int((offset + length) // 60))
    return (start, end) if end > start else None


def _paint(grid: list[str], intervals: list[dict], interval_type: str, phase: str) -> None:
    for interval in intervals:
        if interval.get("intervalType") != interval_type:
            continue
        if (span := _span(interval)) is not None:
            grid[span[0] : span[1]] = [phase] * (span[1] - span[0])


def _collapse(grid: list[str]) -> tuple[DaySegment, ...]:
    segments: list[DaySegment] = []
    start = 0
    for phase, run in groupby(grid):
        minutes = sum(1 for _ in run)
        segments.append(DaySegment(phase=phase, start_min=start, minutes=minutes))
        start += minutes
    return tuple(segments)


def _evening_night_start(grid: list[str], reported: int) -> int | None:
    """Where tonight's rest began, walking back from the collar's last report.

    Rest still running at the end of the day is the next night starting; a nap
    is over by then. Returns None when the dog is up, or when the run reaches
    back past the evening and so belongs to the day instead.
    """
    start: int | None = None
    gap = 0
    for minute in range(reported - 1, NIGHT_EVENING_START - 1, -1):
        if grid[minute] == PHASE_DAY_SLEEP:
            start, gap = minute, 0
            continue
        gap += 1
        if gap > NIGHT_GAP_TOLERANCE:
            break
    return start


def _paint_night(grid: list[str], window: tuple[int, int] | None) -> None:
    if window is None:
        return
    for minute in range(*window):
        if grid[minute] == PHASE_DAY_SLEEP:
            grid[minute] = PHASE_NIGHT_SLEEP


def build_day_phases(
    rest_raw: dict | None, activity_raw: dict | None, night: tuple[int, int] | None
) -> tuple[DaySegment, ...]:
    """Give every minute of the local day exactly one phase.

    Rest is painted over activity where the two overlap: Fi stretches any event
    shorter than its minimum render width to that width, and those stubs
    straddle genuine rest. For the same reason a phase's total here is a
    placement, not a duration -- Fi's own totals stay on the stat sensors.

    Minutes Fi has not reported on yet stay `no_data`, which is what lets the
    phases add up to a full 1440 while the day is still running.
    """
    rest = _intervals(rest_raw)
    activity = _intervals(activity_raw)
    if not rest and not activity:
        return ()
    spans = [span for item in rest + activity if (span := _span(item)) is not None]
    reported = max((end for _, end in spans), default=0)

    grid = [PHASE_NO_DATA] * DAY_MINUTES
    grid[:reported] = [PHASE_AWAKE] * reported
    _paint(grid, activity, "EVENT", PHASE_ACTIVE)
    _paint(grid, rest, "EVENT", PHASE_DAY_SLEEP)
    _paint_night(grid, night)
    if (evening := _evening_night_start(grid, reported)) is not None:
        _paint_night(grid, (evening, reported))
    # Nothing is knowable while the collar is off, so this goes on last.
    _paint(grid, rest, "DEVICE_OFF", PHASE_OFFLINE)
    _paint(grid, activity, "DEVICE_OFF", PHASE_OFFLINE)
    return _collapse(grid)


@dataclass(frozen=True, slots=True)
class Base:
    base_id: str
    name: str
    online: bool

    @classmethod
    def parse(cls, raw: dict) -> Base | None:
        base_id = raw.get("baseId")
        if not base_id:
            return None
        return cls(base_id=base_id, name=raw.get("name") or base_id, online=bool(raw.get("online")))


@dataclass(slots=True)
class Pet:
    pet_id: str
    name: str
    breed: str | None = None
    photo_url: str | None = None
    device: Device | None = None
    activity_type: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    place_name: str | None = None
    place_address: str | None = None
    area_name: str | None = None
    resting_since: datetime | None = None
    last_night_sleep_s: int | None = None
    last_night_start: datetime | None = None
    last_night_end: datetime | None = None
    night_in_progress: bool = False
    day_segments: tuple[DaySegment, ...] = ()
    stats: dict[str, Stats] = field(default_factory=dict)
    behavior_events: dict[str, list[datetime]] = field(default_factory=dict)

    @property
    def is_lost(self) -> bool:
        return self.device is not None and self.device.is_lost

    @classmethod
    def parse_profile(cls, raw: dict) -> Pet | None:
        pet_id = raw.get("id")
        if not pet_id:
            return None
        photos = ((raw.get("photos") or {}).get("first") or {}).get("image") or {}
        return cls(
            pet_id=pet_id,
            name=raw.get("name") or "Unknown",
            breed=(raw.get("breed") or {}).get("name"),
            photo_url=photos.get("fullSize"),
            device=Device.parse(raw.get("device")),
        )

    def apply_detail(self, data: dict | None, midnight: datetime) -> None:
        """Merge one PET_DETAIL response into this pet."""
        data = data or {}
        pet_raw = data.get("pet") or {}
        self._apply_location(pet_raw.get("ongoingActivity"))
        stats: dict[str, Stats] = {}
        for period in PERIODS:
            prefix = period.lower()
            steps, goal, distance, active_s = _parse_activity(pet_raw.get(f"{prefix}Activity"))
            sleep_s, nap_s = _parse_rest(pet_raw.get(f"{prefix}Rest"))
            stats[period] = Stats(
                steps=steps,
                goal=goal,
                distance_m=distance,
                sleep_s=sleep_s,
                nap_s=nap_s,
                active_s=active_s,
            )
        self.stats = stats
        self._apply_overnight(pet_raw.get("lastNight"), pet_raw.get("priorNight"))
        self.behavior_events = parse_behavior_trends(data.get("getPetHealthTrendsForPet"), midnight)
        self.day_segments = build_day_phases(
            data.get("restTimeline"),
            data.get("activityTimeline"),
            self._night_window(midnight),
        )

    def _night_window(self, midnight: datetime) -> tuple[int, int] | None:
        """Last night as [start, end) minutes of today, or None if it missed today.

        The evening that began the night is always yesterday's bar; only what
        fell after local midnight is today's.

        While Fi still answers Unavailable the dog is *in* that night, so every
        minute of rest since midnight belongs to it -- the timeline stops at the
        collar's last report anyway, so the open end cannot over-claim. Once the
        night settles, sleepEnd bounds it exactly.
        """
        if self.night_in_progress:
            return (0, DAY_MINUTES)
        if self.last_night_start is None or self.last_night_end is None:
            return None
        start = max(0, int((self.last_night_start - midnight).total_seconds() // 60))
        end = min(DAY_MINUTES, int((self.last_night_end - midnight).total_seconds() // 60))
        return (start, end) if end > start else None

    def _apply_location(self, raw: dict | None) -> None:
        if not raw:
            return
        self.activity_type = raw.get("__typename")
        self.area_name = raw.get("areaName")
        self.resting_since = _as_datetime(raw.get("start")) if self.activity_type == "OngoingRest" else None
        if self.activity_type == ACTIVITY_ONGOING_WALK:
            positions = raw.get("positions") or []
            position = (positions[-1].get("position") or {}) if positions else {}
        else:
            position = raw.get("position") or {}
        latitude = _as_float(position.get("latitude"))
        longitude = _as_float(position.get("longitude"))
        if latitude is not None and longitude is not None:
            self.latitude = latitude
            self.longitude = longitude
        place = raw.get("place") or {}
        self.place_name = place.get("name")
        self.place_address = place.get("address")

    def _apply_overnight(self, last_night: dict | None, prior_night: dict | None) -> None:
        """Take the newest settled night, and note when none of them has ended.

        Fi answers UnavailableOvernightRestSummary for a night that has not
        ended yet, so between local midnight and the moment the dog wakes the
        newest candidate is still in progress and the one before it is the
        night just finished.
        """
        self.night_in_progress = (last_night or {}).get("__typename") == "UnavailableOvernightRestSummary"
        for raw in (last_night, prior_night):
            if _is_settled(raw):
                self.last_night_sleep_s = _as_int(raw.get("sleepSeconds"))
                self.last_night_start = _as_datetime(raw.get("sleepStart"))
                self.last_night_end = _as_datetime(raw.get("sleepEnd"))
                return
        self.last_night_sleep_s = None
        self.last_night_start = None
        self.last_night_end = None


def parse_behavior_trends(raw: dict | None, midnight: datetime) -> dict[str, list[datetime]]:
    """Return {behaviour key: event times} for today in local time.

    Offsets are seconds since local midnight. An EVENT's `length` is a fixed
    render width (always 700s) and events can overlap, so it is not a duration
    and is deliberately not requested.
    """
    events: dict[str, list[datetime]] = {}
    for trend in (raw or {}).get("behaviorTrends") or []:
        key = str(trend.get("id") or "").split(":")[0]
        if not key:
            continue
        intervals = (trend.get("chart") or {}).get("intervals") or []
        events[key] = [
            midnight + timedelta(seconds=interval["offset"])
            for interval in intervals
            if interval.get("intervalType") == "EVENT" and interval.get("offset") is not None
        ]
    return events


@dataclass(slots=True)
class FiData:
    """Everything one refresh produced."""

    pets: list[Pet] = field(default_factory=list)
    bases: list[Base] = field(default_factory=list)

    def get_pet(self, pet_id: str) -> Pet | None:
        return next((pet for pet in self.pets if pet.pet_id == pet_id), None)

    def get_base(self, base_id: str) -> Base | None:
        return next((base for base in self.bases if base.base_id == base_id), None)
