"""Errors raised by the Fi API client."""

from __future__ import annotations

from homeassistant.exceptions import HomeAssistantError


class FiError(HomeAssistantError):
    """Base error for every Fi API failure."""


class FiAuthError(FiError):
    """Credentials were rejected, or the session is no longer valid."""


class FiConnectionError(FiError):
    """Fi could not be reached, or answered with something unusable."""
