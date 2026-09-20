"""Typed models parsed from Fi's GraphQL payloads.

Absent data is None, never a substituted zero. pytryfi defaulted sleep and nap
to 0 before parsing and swallowed the failure, which made "Fi sent nothing"
indistinguishable from "the dog slept nothing" -- the bug that hid Fi's daily
rest behaviour for as long as it did.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from .queries import ACTIVITY_ONGOING_WALK, PET_MODE_LOST

PERIODS = ("DAILY", "WEEKLY", "MONTHLY")


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


def _parse_activity(raw: dict | None) -> tuple[int | None, int | None, float | None]:
    if not raw:
        return None, None, None
    return (
        _as_int(raw.get("totalSteps")),
        _as_int(raw.get("stepGoal")),
        _as_float(raw.get("totalDistance")),
    )


def _parse_rest(raw: dict | None) -> tuple[int | None, int | None]:
    """Return (sleep, nap) seconds, or (None, None) when Fi reported no summary."""
    summaries = (raw or {}).get("restSummaries") or []
    if not summaries:
        return None, None
    amounts = (summaries[0].get("data") or {}).get("sleepAmounts")
    if not amounts:
        return None, None
    by_type = {item.get("type"): _as_int(item.get("duration")) for item in amounts}
    return by_type.get("SLEEP"), by_type.get("NAP")


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

    def apply_detail(self, pet_raw: dict | None, trends_raw: dict | None, midnight: datetime) -> None:
        """Merge one PET_DETAIL response into this pet."""
        pet_raw = pet_raw or {}
        self._apply_location(pet_raw.get("ongoingActivity"))
        stats: dict[str, Stats] = {}
        for period in PERIODS:
            prefix = period.lower()
            steps, goal, distance = _parse_activity(pet_raw.get(f"{prefix}Activity"))
            sleep_s, nap_s = _parse_rest(pet_raw.get(f"{prefix}Rest"))
            stats[period] = Stats(steps=steps, goal=goal, distance_m=distance, sleep_s=sleep_s, nap_s=nap_s)
        self.stats = stats
        self._apply_overnight(pet_raw.get("overnightRestSummary"))
        self.behavior_events = parse_behavior_trends(trends_raw, midnight)

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

    def _apply_overnight(self, raw: dict | None) -> None:
        # Fi returns UnavailableOvernightRestSummary until the night is settled.
        if not raw or raw.get("__typename") != "ConcreteOvernightRestSummary":
            self.last_night_sleep_s = None
            self.last_night_start = None
            self.last_night_end = None
            return
        self.last_night_sleep_s = _as_int(raw.get("sleepSeconds"))
        self.last_night_start = _as_datetime(raw.get("sleepStart"))
        self.last_night_end = _as_datetime(raw.get("sleepEnd"))


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
