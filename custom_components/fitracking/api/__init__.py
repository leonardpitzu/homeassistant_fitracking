"""Self-contained client for Fi's API."""

from .client import FiClient
from .exceptions import FiAuthError, FiConnectionError, FiError
from .models import Base, Device, FiData, LedColor, Pet, Stats

__all__ = [
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
