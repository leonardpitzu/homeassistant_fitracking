"""Self-contained client for Fi's API."""

from .client import FiClient
from .exceptions import FiAuthError, FiConnectionError, FiError
from .models import DAY_MINUTES, PHASES, Base, Device, FiData, LedColor, Pet, Stats

__all__ = [
    "DAY_MINUTES",
    "PHASES",
    "Base",
    "Device",
    "FiAuthError",
    "FiClient",
    "FiConnectionError",
    "FiData",
    "FiError",
    "LedColor",
    "Pet",
    "Stats",
]
