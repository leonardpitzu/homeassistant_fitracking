"""Tests for the Fi API client's resilience to Fi's own failures.

Both behaviours here were written after a live incident: Fi's gateway answered
502 three times overnight, which took every entity to `unavailable` and, on the
per-pet path, blanked every statistic to `unknown`.
"""

import json
from datetime import UTC, datetime
from unittest.mock import patch

import pytest

from custom_components.fitracking.api.client import FiClient
from custom_components.fitracking.api.exceptions import FiConnectionError
from custom_components.fitracking.api.models import FiData, Pet

MIDNIGHT = datetime(2026, 9, 21, tzinfo=UTC)

HOUSEHOLD = {
    "currentUser": {
        "userHouseholds": [
            {
                "household": {
                    "pets": [{"id": "p1", "name": "Rex", "breed": {"name": "Mixed"}}],
                    "bases": [{"baseId": "b1", "name": "Kitchen", "online": True}],
                }
            }
        ]
    }
}


class _Response:
    def __init__(self, status, payload):
        self.status = status
        self._payload = payload

    async def json(self, content_type=None):
        return self._payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _FullSession:
    """Like _Session, but keeps each request whole."""

    def __init__(self, script):
        self._script = list(script)
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append(json.dumps(kwargs.get("json", {})))
        status, payload = self._script.pop(0)
        return _Response(status, payload)


class _Session:
    """Answers each POST from a scripted queue, recording what was asked."""

    def __init__(self, script):
        self._script = list(script)
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append(json.dumps(kwargs.get("json", {}))[:40])
        status, payload = self._script.pop(0)
        return _Response(status, payload)


def _client(script):
    session = _Session(script)
    return FiClient(session, "user@example.com", "hunter2"), session


@pytest.fixture(autouse=True)
def _no_waiting():
    """The retry delays are real seconds; the test should not spend them."""
    with patch("custom_components.fitracking.api.client.asyncio.sleep"):
        yield


@pytest.mark.parametrize("status", [429, 500, 502, 503, 504])
async def test_transient_statuses_are_retried_until_one_succeeds(status):
    """Fi's 502s come in bursts of one; the next attempt usually works."""
    client, session = _client([(status, None), (200, {"data": {"pet": {}}})])
    result = await client._async_graphql("query { x }")
    assert result == {"pet": {}}
    assert len(session.calls) == 2


async def test_a_persistent_failure_still_raises():
    """Three attempts, then the caller hears about it."""
    client, session = _client([(502, None), (502, None), (502, None)])
    with pytest.raises(FiConnectionError):
        await client._async_graphql("query { x }")
    assert len(session.calls) == 3


async def test_a_failed_detail_keeps_the_previous_statistics():
    """The bug: a rebuilt pet had empty stats, so every sensor read `unknown`."""
    previous_pet = Pet.parse_profile({"id": "p1", "name": "Rex"})
    previous_pet.apply_detail(
        {"pet": {"dailyActivity": {"totalSteps": 1234, "stepGoal": 16000}}},
        MIDNIGHT,
        MIDNIGHT,
    )
    assert previous_pet.stats["DAILY"].steps == 1234

    # Household succeeds, then the pet detail fails all three attempts.
    client, _ = _client([(200, {"data": HOUSEHOLD})] + [(502, None)] * 3)
    data = await client.async_get_data(MIDNIGHT, FiData(pets=[previous_pet]))

    assert data.pets[0] is previous_pet
    assert data.pets[0].stats["DAILY"].steps == 1234


async def test_an_unknown_pet_starts_empty():
    """Nothing to carry forward means the statistics are genuinely absent."""
    client, _ = _client([(200, {"data": HOUSEHOLD})] + [(502, None)] * 3)
    data = await client.async_get_data(MIDNIGHT)
    assert data.pets[0].stats == {}


async def test_a_null_pet_beside_errors_keeps_the_previous_statistics():
    """Fi answers 200 with errors and a null pet; that is a failed refresh.

    It used to fall through as success, so the pet was re-parsed from an empty
    payload and every statistic read `unknown` -- with nothing above DEBUG to
    say why.
    """
    previous_pet = Pet.parse_profile({"id": "p1", "name": "Rex"})
    previous_pet.apply_detail({"pet": {"dailyActivity": {"totalSteps": 1234}}}, MIDNIGHT, MIDNIGHT)

    partial = (200, {"data": {"pet": None}, "errors": [{"message": "internal error"}]})
    client, _ = _client([(200, {"data": HOUSEHOLD}), partial])
    data = await client.async_get_data(MIDNIGHT, FiData(pets=[previous_pet]))

    assert data.pets[0].stats["DAILY"].steps == 1234


async def test_the_profile_is_refreshed_on_a_carried_pet():
    """Carrying the pet forward must not freeze its name or collar state."""
    previous_pet = Pet.parse_profile({"id": "p1", "name": "Old Name"})
    client, _ = _client([(200, {"data": HOUSEHOLD})] + [(502, None)] * 3)
    data = await client.async_get_data(MIDNIGHT, FiData(pets=[previous_pet]))
    assert data.pets[0].name == "Rex"
    assert data.pets[0].breed == "Mixed"


def _detail(week_start=None):
    activity = {"totalSteps": 1} | ({"start": week_start} if week_start else {})
    return 200, {"data": {"pet": {"weeklyActivity": activity}}}


def _eves(call):
    variables = json.loads(call)["variables"]
    return variables["weekEve"], variables["monthEve"]


async def test_boundary_nights_are_keyed_by_the_calendar_first():
    """MIDNIGHT is Monday 21 Sep: the week began today, the month on the 1st."""
    session = _FullSession([(200, {"data": HOUSEHOLD}), _detail()])
    await FiClient(session, "e", "p").async_get_data(MIDNIGHT)
    assert _eves(session.calls[1]) == ("2026-09-20T00:00:00.000Z", "2026-08-31T00:00:00.000Z")
    assert len(session.calls) == 2


async def test_fis_own_week_start_is_asked_again_when_it_differs():
    """A week Fi starts on Sunday needs Saturday's night, not Sunday's."""
    session = _FullSession([(200, {"data": HOUSEHOLD}), _detail("2026-09-20T00:00:00.000Z"), _detail()])
    data = await FiClient(session, "e", "p").async_get_data(MIDNIGHT)
    assert _eves(session.calls[2])[0] == "2026-09-19T00:00:00.000Z"
    assert len(session.calls) == 3

    # The next refresh starts from Fi's week and needs no second request.
    session._script.extend([(200, {"data": HOUSEHOLD}), _detail("2026-09-20T00:00:00.000Z")])
    await FiClient(session, "e", "p").async_get_data(MIDNIGHT, data)
    assert len(session.calls) == 5
