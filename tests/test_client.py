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
                    "pets": [{"id": "p1", "name": "Scottie", "breed": {"name": "Corgi"}}],
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
    previous_pet = Pet.parse_profile({"id": "p1", "name": "Scottie"})
    previous_pet.apply_detail(
        {"pet": {"dailyActivity": {"totalSteps": 1234, "stepGoal": 16000}}},
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


async def test_the_profile_is_refreshed_on_a_carried_pet():
    """Carrying the pet forward must not freeze its name or collar state."""
    previous_pet = Pet.parse_profile({"id": "p1", "name": "Old Name"})
    client, _ = _client([(200, {"data": HOUSEHOLD})] + [(502, None)] * 3)
    data = await client.async_get_data(MIDNIGHT, FiData(pets=[previous_pet]))
    assert data.pets[0].name == "Scottie"
    assert data.pets[0].breed == "Corgi"
