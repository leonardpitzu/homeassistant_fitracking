"""Async client for Fi's GraphQL API.

Replaces pytryfi, which initialised a process-wide Sentry client pointed at a
third-party DSN, swallowed every failure into it, and never re-authenticated
once Fi expired the session.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta

from aiohttp import ClientError, ClientResponseError, ClientSession, ClientTimeout

from . import queries
from .exceptions import FiAuthError, FiConnectionError
from .models import Base, FiData, Pet

LOGGER = logging.getLogger(__name__)

API_BASE = "https://api.tryfi.com"
LOGIN_URL = f"{API_BASE}/auth/login"
GRAPHQL_URL = f"{API_BASE}/graphql"
TIMEOUT = ClientTimeout(total=30)


class FiClient:
    """Talks to Fi. Owns its own cookie jar via the session handed in."""

    def __init__(self, session: ClientSession, email: str, password: str) -> None:
        self._session = session
        self._email = email
        self._password = password
        self._user_id: str | None = None
        self._login_lock = asyncio.Lock()

    @property
    def user_id(self) -> str | None:
        return self._user_id

    async def async_login(self) -> str:
        """Authenticate and keep the session cookie. Returns the Fi user id."""
        async with self._login_lock:
            try:
                async with self._session.post(
                    LOGIN_URL,
                    data={"email": self._email, "password": self._password},
                    timeout=TIMEOUT,
                ) as response:
                    payload = await response.json(content_type=None)
                    if response.status in (400, 401, 403):
                        raise FiAuthError(_login_error(payload) or "Fi rejected the credentials")
                    response.raise_for_status()
            except ClientResponseError as err:
                raise FiConnectionError(f"Fi login failed: HTTP {err.status}") from err
            except (ClientError, TimeoutError) as err:
                raise FiConnectionError(f"Could not reach Fi: {err}") from err

            if error := _login_error(payload):
                raise FiAuthError(error)
            user_id = payload.get("userId")
            if not user_id:
                raise FiAuthError("Fi login returned no user id")
            self._user_id = user_id
            return user_id

    async def _async_graphql(self, document: str, variables: dict | None = None, *, retry_auth: bool = True) -> dict:
        """POST a document. Re-authenticates once if Fi expired the session."""
        body: dict = {"query": document}
        if variables is not None:
            body["variables"] = variables
        try:
            async with self._session.post(GRAPHQL_URL, json=body, timeout=TIMEOUT) as response:
                if response.status in (401, 403):
                    if not retry_auth:
                        raise FiAuthError("Fi rejected the session")
                    LOGGER.debug("Fi session expired, re-authenticating")
                    await self.async_login()
                    return await self._async_graphql(document, variables, retry_auth=False)
                # Apollo reports validation errors as HTTP 400 with a usable body.
                if response.status >= 500:
                    raise FiConnectionError(f"Fi returned HTTP {response.status}")
                payload = await response.json(content_type=None)
        except (ClientError, TimeoutError) as err:
            raise FiConnectionError(f"Could not reach Fi: {err}") from err

        if not isinstance(payload, dict):
            raise FiConnectionError("Fi returned a malformed response")
        # Errors can accompany partial data, so they are logged, not raised,
        # unless nothing usable came back at all.
        if errors := payload.get("errors"):
            messages = "; ".join(str(item.get("message", item))[:200] for item in errors)
            if payload.get("data") is None:
                raise FiConnectionError(f"Fi query failed: {messages}")
            LOGGER.debug("Fi returned partial data: %s", messages)
        return payload.get("data") or {}

    async def async_get_data(self, midnight: datetime) -> FiData:
        """Fetch every pet and base. One request for the household, one per pet."""
        data = await self._async_graphql(queries.HOUSEHOLDS)
        pets: list[Pet] = []
        bases: list[Base] = []
        for entry in (data.get("currentUser") or {}).get("userHouseholds") or []:
            household = entry.get("household") or {}
            for raw in household.get("pets") or []:
                if (pet := Pet.parse_profile(raw)) is not None:
                    pets.append(pet)
            for raw in household.get("bases") or []:
                if (base := Base.parse(raw)) is not None:
                    bases.append(base)

        nights = {
            "lastNight": _fi_date(midnight - timedelta(days=1)),
            "priorNight": _fi_date(midnight - timedelta(days=2)),
        }
        results = await asyncio.gather(
            *(self._async_pet_detail(pet, midnight, nights) for pet in pets),
            return_exceptions=True,
        )
        for pet, result in zip(pets, results, strict=True):
            if isinstance(result, Exception):
                # One unreachable pet must not blank the rest of the household.
                LOGGER.warning("Could not refresh pet %s: %s", pet.name, result)
        return FiData(pets=pets, bases=bases)

    async def _async_pet_detail(self, pet: Pet, midnight: datetime, nights: dict[str, str]) -> None:
        data = await self._async_graphql(queries.PET_DETAIL, {"petId": pet.pet_id} | nights)
        pet.apply_detail(data.get("pet"), data.get("getPetHealthTrendsForPet"), midnight)

    async def async_set_led(self, module_id: str, enabled: bool) -> None:
        await self._async_graphql(
            queries.SET_DEVICE_OPS,
            {"input": {"moduleId": module_id, "ledEnabled": enabled}},
        )

    async def async_set_led_color(self, module_id: str, color_code: int) -> None:
        await self._async_graphql(queries.SET_LED_COLOR, {"moduleId": module_id, "ledColorCode": int(color_code)})

    async def async_set_lost_mode(self, module_id: str, lost: bool) -> None:
        mode = queries.PET_MODE_LOST if lost else queries.PET_MODE_NORMAL
        await self._async_graphql(queries.SET_DEVICE_OPS, {"input": {"moduleId": module_id, "mode": mode}})


def _fi_date(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _login_error(payload: object) -> str | None:
    if not isinstance(payload, dict):
        return None
    error = payload.get("error")
    if isinstance(error, dict):
        return str(error.get("message") or error)
    return str(error) if error else None
