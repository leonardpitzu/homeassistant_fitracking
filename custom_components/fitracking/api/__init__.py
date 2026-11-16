"""Self-contained client for Fi's API."""

from .client import FiClient
from .exceptions import FiAuthError, FiConnectionError, FiError
from .models import DAY_MINUTES, PERIODS, PHASE_NO_DATA, PHASES, Base, Device, FiData, LedColor, Pet, Stats

__all__ = [
    "DAY_MINUTES",
    "PERIODS",
    "PHASES",
    "PHASE_NO_DATA",
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
