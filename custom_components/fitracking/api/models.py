"""Typed models parsed from Fi's GraphQL payloads.

Absent data is None, never a substituted zero. pytryfi defaulted sleep and nap
to 0 before parsing and swallowed the failure, which made "Fi sent nothing"
indistinguishable from "the pet slept nothing" -- the bug that hid Fi's daily
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
PHASE_IDLE = "idle"
PHASE_OFFLINE = "offline"
PHASE_NO_DATA = "no_data"
PHASES = (
    PHASE_NIGHT_SLEEP,
    PHASE_DAY_SLEEP,
    PHASE_ACTIVE,
    PHASE_IDLE,
    PHASE_OFFLINE,
    PHASE_NO_DATA,
)

# Fi reports the night in progress with sleepEnd at the collar's latest upload,
# so a night is over only once its end has stood still. Shorter than this is a
# stir: Fi's own render pad is ~12 minutes, so this sits just above that.
NIGHT_SETTLED_AFTER = timedelta(minutes=15)

# An end also stands still while the collar cannot upload, and then Fi keeps
# drawing rest up to the present. A pet that really got up shows on the rest
# bar as at least this much time after the end that is not rest.
UP_AFTER_NIGHT_SECONDS = 60

# Fi files a whole night under the week or month it began in, so the night
# across a period's start counts nowhere in the new period. These are the
# aliases of that night, asked for by the evening before each period began.
BOUNDARY_NIGHTS = {"WEEKLY": "weekEve", "MONTHLY": "monthEve"}

# Behaviour events are stamped by the collar, so one from the future is not a
# clock drift of seconds but a chart from another day.
BEHAVIOR_CLOCK_LEEWAY = timedelta(minutes=5)


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


def _is_concrete(raw: dict | None) -> bool:
    return bool(raw) and raw.get("__typename") == "ConcreteOvernightRestSummary"


def _tonight_start(raw: dict | None, midnight: datetime) -> datetime | None:
    """When Fi says tonight's night began, or None while it has not started one.

    Fi keys a night by the evening it began. Asked for by today's date, the
    summary is Unavailable until a rest reaching past 20:00 has run for about
    an hour; then it is concrete, with the start backdated to when the pet lay
    down but never before 20:00. A start outside today is not tonight's.
    """
    if not _is_concrete(raw):
        return None
    start = _as_datetime(raw.get("sleepStart"))
    if start is None or not midnight <= start < midnight + timedelta(days=1):
        return None
    return start


def _boundary_sleep(raw: dict | None, period_start: datetime, now: datetime) -> int:
    """Seconds of the night across a period's start that fell inside the period.

    Counted from the period's start to the night's end, both Fi's own, and
    never more than Fi's total for that night. A night that does not straddle
    the start -- the wrong one, or one that began inside the period and is
    already in Fi's total -- adds nothing.
    """
    if not _is_concrete(raw):
        return 0
    begin = _as_datetime(raw.get("sleepStart"))
    end = _as_datetime(raw.get("sleepEnd"))
    if begin is None or end is None or not begin < period_start < end:
        return 0
    inside = int((min(end, now) - period_start).total_seconds())
    total = _as_int(raw.get("sleepSeconds"))
    return max(0, inside if total is None else min(inside, total))


def _is_settled(raw: dict | None, now: datetime) -> bool:
    """Whether a summary's end has stood still long enough to be over.

    An unfinished night comes back either as UnavailableOvernightRestSummary or
    as a concrete summary whose sleepEnd follows the collar's latest upload,
    from midnight until the pet gets up. Only an end that has stopped moving
    can mark the night as over; for the newest night, `_got_up_after` also
    asks whether the collar could have moved it.
    """
    if not _is_concrete(raw):
        return False
    end = _as_datetime(raw.get("sleepEnd"))
    return end is None or now - end >= NIGHT_SETTLED_AFTER


def _got_up_after(raw: dict, rest_raw: dict | None, midnight: datetime, now: datetime) -> bool:
    """Whether today's rest bar shows the pet up after the night's end.

    Fi's end stops moving both when the pet gets up and when the collar cannot
    upload -- out of range, switched off, flat, or back but not yet synced. In
    the second case Fi keeps drawing rest up to the present, so only a stretch
    after the end that is not rest -- awake, active, or the collar taken off --
    shows the night is over. The padding Fi adds to the last rest can delay it
    by a few minutes, never past the 15 the end has to stand still anyway.

    Without a rest bar there is nothing to ask, and the end standing still is
    taken at its word, as it always was.
    """
    end = _as_datetime(raw.get("sleepEnd"))
    intervals = _intervals(rest_raw)
    if end is None or not intervals:
        return True
    spans: list[tuple[str | None, float, float]] = []
    for item in intervals:
        offset = _as_float(item.get("offset"))
        length = _as_float(item.get("length"))
        if offset is not None and length is not None and length > 0:
            spans.append((item.get("intervalType"), offset, offset + length))
    # Fi's bar runs to the present, padded past it now and then.
    horizon = min(max((stop for _, _, stop in spans), default=0.0), (now - midnight).total_seconds())
    # The bar is today's. An end before midnight -- the collar went quiet in
    # the evening -- leaves nothing on it to read as the pet being up.
    cursor = max(0.0, (end - midnight).total_seconds())
    for start, stop in sorted((start, stop) for kind, start, stop in spans if kind == "EVENT"):
        if stop <= cursor:
            continue
        if min(start, horizon) - cursor >= UP_AFTER_NIGHT_SECONDS:
            return True
        cursor = max(cursor, stop)
        if cursor >= horizon:
            return False
    return horizon - cursor >= UP_AFTER_NIGHT_SECONDS


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


def _paint_night(grid: list[str], window: tuple[int, int] | None) -> None:
    if window is None:
        return
    for minute in range(*window):
        if grid[minute] == PHASE_DAY_SLEEP:
            grid[minute] = PHASE_NIGHT_SLEEP


def build_day_phases(
    rest_raw: dict | None,
    activity_raw: dict | None,
    night: tuple[int, int] | None,
    evening: tuple[int, int] | None,
    now_min: int,
) -> tuple[DaySegment, ...]:
    """Give every minute of the local day exactly one phase.

    `night` is the one that began yesterday evening, `evening` the one Fi has
    started tonight; rest inside either is night sleep, any other rest a nap.

    Rest is painted over activity where the two overlap: Fi stretches any event
    shorter than its minimum render width to that width, and those stubs
    straddle genuine rest. For the same reason a phase's total here is a
    placement, not a duration -- Fi's own totals stay on the stat sensors.

    Minutes Fi has not reported on yet stay `no_data`, which is what lets the
    phases add up to a full 1440 while the day is still running. That includes
    every minute from `now_min` on: the same stretching reaches up to ~12
    minutes into the future, and Fi alternates between padded and unpadded
    answers within seconds.
    """
    rest = _intervals(rest_raw)
    activity = _intervals(activity_raw)
    if not rest and not activity:
        return ()
    spans = [span for item in rest + activity if (span := _span(item)) is not None]
    reported = min(max((end for _, end in spans), default=0), max(0, now_min))
    # Fi moves the activity bar on to a new minute some seconds before the rest
    # bar, so for one poll a rest still running ends a minute short and that
    # minute reads as a stir. It waits for the rest bar; a pet that really got
    # up shows one minute later.
    rest_end = max(
        (span[1] for item in rest if item.get("intervalType") == "EVENT" and (span := _span(item)) is not None),
        default=None,
    )
    if rest_end is not None and reported == rest_end + 1:
        reported = rest_end

    grid = [PHASE_NO_DATA] * DAY_MINUTES
    grid[:reported] = [PHASE_IDLE] * reported
    _paint(grid, activity, "EVENT", PHASE_ACTIVE)
    _paint(grid, rest, "EVENT", PHASE_DAY_SLEEP)
    _paint_night(grid, night)
    _paint_night(grid, evening)
    # Nothing is knowable while the collar is off, so this goes on last.
    _paint(grid, rest, "DEVICE_OFF", PHASE_OFFLINE)
    _paint(grid, activity, "DEVICE_OFF", PHASE_OFFLINE)
    # Painting follows Fi's spans, padding and all; what lies past the last
    # report has not happened yet.
    grid[reported:] = [PHASE_NO_DATA] * (DAY_MINUTES - reported)
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
    resting_since: datetime | None = None
    last_night_sleep_s: int | None = None
    last_night_start: datetime | None = None
    last_night_end: datetime | None = None
    night_in_progress: bool = False
    tonight_start: datetime | None = None
    period_starts: dict[str, datetime] = field(default_factory=dict)
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
        pet = cls(pet_id=pet_id, name="")
        pet.apply_profile(raw)
        return pet

    def apply_profile(self, raw: dict) -> None:
        """Refresh the household-level fields, leaving the detail ones alone."""
        photos = ((raw.get("photos") or {}).get("first") or {}).get("image") or {}
        self.name = raw.get("name") or "Unknown"
        self.breed = (raw.get("breed") or {}).get("name")
        self.photo_url = photos.get("fullSize")
        self.device = Device.parse(raw.get("device"))

    def apply_detail(self, data: dict | None, midnight: datetime, now: datetime) -> None:
        """Merge one PET_DETAIL response into this pet, as of `now`."""
        data = data or {}
        pet_raw = data.get("pet") or {}
        self._apply_location(pet_raw.get("ongoingActivity"))
        self._apply_overnight(
            pet_raw.get("lastNight"), pet_raw.get("priorNight"), data.get("restTimeline"), midnight, now
        )
        stats: dict[str, Stats] = {}
        for period in PERIODS:
            prefix = period.lower()
            activity_raw = pet_raw.get(f"{prefix}Activity")
            steps, goal, distance, active_s = _parse_activity(activity_raw)
            sleep_s, nap_s = _parse_rest(pet_raw.get(f"{prefix}Rest"))
            # Fi's own start of the period; the week is whatever Fi says it is.
            if (start := _as_datetime((activity_raw or {}).get("start"))) is not None:
                self.period_starts[period] = start
            if sleep_s is not None and start is not None and period in BOUNDARY_NIGHTS:
                sleep_s += self._boundary_sleep(pet_raw.get(BOUNDARY_NIGHTS[period]), start, stats, midnight, now)
            stats[period] = Stats(
                steps=steps,
                goal=goal,
                distance_m=distance,
                sleep_s=sleep_s,
                nap_s=nap_s,
                active_s=active_s,
            )
        self.stats = stats
        self.tonight_start = _tonight_start(pet_raw.get("tonight"), midnight)
        self.behavior_events = parse_behavior_trends(data.get("getPetHealthTrendsForPet"), midnight, now)
        self.day_segments = build_day_phases(
            data.get("restTimeline"),
            data.get("activityTimeline"),
            self._night_window(midnight),
            self._evening_window(midnight),
            int((now - midnight).total_seconds() // 60),
        )

    def _boundary_sleep(
        self, raw: dict | None, period_start: datetime, stats: dict[str, Stats], midnight: datetime, now: datetime
    ) -> int:
        """What the night across a period's start adds to that period's sleep.

        On the period's first morning Fi may still answer Unavailable for the
        night in progress. Until it has an end, Fi's daily sleep is that night
        clipped at midnight, which is the period's start.
        """
        if _is_concrete(raw) or not self.night_in_progress or period_start != midnight:
            return _boundary_sleep(raw, period_start, now)
        daily = stats.get("DAILY")
        return (daily.sleep_s if daily else None) or 0

    def _evening_window(self, midnight: datetime) -> tuple[int, int] | None:
        """Tonight's night as [start, end of day), once Fi has started it.

        The bar stops at the collar's last report, so the open end cannot claim
        anything that has not happened; past midnight the night is tomorrow's.
        """
        if self.tonight_start is None:
            return None
        return (int((self.tonight_start - midnight).total_seconds() // 60), DAY_MINUTES)

    def _night_window(self, midnight: datetime) -> tuple[int, int] | None:
        """Last night as [start, end) minutes of today, or None if it missed today.

        The evening that began the night is always yesterday's bar; only what
        fell after local midnight is today's.

        While the night is still running the pet is *in* it, so every minute of
        rest since midnight belongs to it -- the timeline stops at the present
        anyway, so the open end cannot over-claim. Once the night settles,
        sleepEnd bounds it exactly.
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

    def _apply_overnight(
        self,
        last_night: dict | None,
        prior_night: dict | None,
        rest_raw: dict | None,
        midnight: datetime,
        now: datetime,
    ) -> None:
        """Take the newest settled night, and note when none of them has ended.

        Between local midnight and the moment the pet wakes, the newest
        candidate is the night still in progress and the one before it is the
        night just finished. Fi says so with UnavailableOvernightRestSummary or
        with a concrete summary whose end is still moving; absent is neither.
        """
        unavailable = (last_night or {}).get("__typename") == "UnavailableOvernightRestSummary"
        last_ended = _is_settled(last_night, now) and _got_up_after(last_night, rest_raw, midnight, now)
        self.night_in_progress = unavailable or (_is_concrete(last_night) and not last_ended)
        # The night before last ended the morning before; today's rest bar says
        # nothing about it.
        for raw, ended in ((last_night, last_ended), (prior_night, _is_settled(prior_night, now))):
            if ended:
                self.last_night_sleep_s = _as_int(raw.get("sleepSeconds"))
                self.last_night_start = _as_datetime(raw.get("sleepStart"))
                self.last_night_end = _as_datetime(raw.get("sleepEnd"))
                return
        self.last_night_sleep_s = None
        self.last_night_start = None
        self.last_night_end = None


def parse_behavior_trends(raw: dict | None, midnight: datetime, now: datetime) -> dict[str, list[datetime]]:
    """Return {behaviour key: event times} for today in local time, oldest first.

    Offsets are seconds since local midnight. An EVENT's `length` is a fixed
    render width (always 700s) and events can overlap, so it is not a duration
    and is deliberately not requested.

    For a few minutes after midnight Fi still serves yesterday's chart, which
    these offsets would date to later today. A chart with an event after `now`
    is therefore not today's, and today has no events yet.
    """
    events: dict[str, list[datetime]] = {}
    for trend in (raw or {}).get("behaviorTrends") or []:
        key = str(trend.get("id") or "").split(":")[0]
        if not key:
            continue
        intervals = (trend.get("chart") or {}).get("intervals") or []
        events[key] = sorted(
            midnight + timedelta(seconds=interval["offset"])
            for interval in intervals
            if interval.get("intervalType") == "EVENT" and interval.get("offset") is not None
        )
    latest = max((times[-1] for times in events.values() if times), default=None)
    if latest is not None and latest > now + BEHAVIOR_CLOCK_LEEWAY:
        return {key: [] for key in events}
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
