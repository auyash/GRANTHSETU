"""
GRANTHSETU — Hardware Abstraction Layer (HAL) Base Interfaces
=============================================================================
Defines standard lifecycle, status reporting, and diagnostic contracts for:
- Barcode Scanners (USB HID / Serial RS-232)
- Smart Card Readers (PC/SC NFC / MIFARE)
- RFID Book Identification Antenna Pads (ISO 15693 / ISO 18000-3)
- Library RFID Exit Gates (EAS Pedestals)

Strict Principle:
Simulated and physical device states are strictly distinguished.
A failed hardware operation cannot be mistaken for a successful library transaction.
=============================================================================
"""

import enum
import logging
from abc import ABC, abstractmethod
from typing import Dict, Any, Optional

logger = logging.getLogger("hardware_hal")


class DeviceType(str, enum.Enum):
    BARCODE_SCANNER = "BARCODE_SCANNER"
    SMART_CARD_READER = "SMART_CARD_READER"
    RFID_ANTENNA = "RFID_ANTENNA"
    EXIT_GATE = "EXIT_GATE"


class DeviceStatus(str, enum.Enum):
    CONNECTED = "CONNECTED"           # Physical hardware connected and responsive
    DISCONNECTED = "DISCONNECTED"     # Physical hardware offline or unplugged
    SIMULATED = "SIMULATED"           # High-fidelity software mock/simulation
    ERROR = "ERROR"                   # Hardware malfunction or driver exception


class BaseHardwareAdapter(ABC):
    """Abstract base class for all physical and simulated kiosk hardware peripherals."""

    def __init__(self, name: str, device_type: DeviceType, is_simulated: bool = True):
        self.name = name
        self.device_type = device_type
        self.is_simulated = is_simulated
        self._status: DeviceStatus = DeviceStatus.SIMULATED if is_simulated else DeviceStatus.DISCONNECTED
        self._last_error: Optional[str] = None
        self._device_info: Dict[str, Any] = {}

    @property
    def status(self) -> DeviceStatus:
        return self._status

    @property
    def is_connected(self) -> bool:
        return self._status in (DeviceStatus.CONNECTED, DeviceStatus.SIMULATED)

    @property
    def last_error(self) -> Optional[str]:
        return self._last_error

    @abstractmethod
    def connect(self) -> bool:
        """Establishes communication with the hardware device or initializes simulation."""
        pass

    @abstractmethod
    def disconnect(self) -> None:
        """Safely closes device handles, serial ports, or USB endpoints."""
        pass

    def get_diagnostics(self) -> Dict[str, Any]:
        """Returns safe diagnostic information without leaking system secrets."""
        return {
            "name": self.name,
            "device_type": self.device_type.value,
            "adapter_class": self.__class__.__name__,
            "status": self._status.value,
            "is_simulated": self.is_simulated,
            "connected": self.is_connected,
            "last_error": self._last_error,
            "details": self._device_info,
        }
