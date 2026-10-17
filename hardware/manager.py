"""
GRANTHSETU — Central Hardware Manager
=============================================================================
Orchestrates hardware peripherals for the self-service kiosk:
- Barcode Scanner (Handheld laser / 2D imager)
- Contactless Smart Card Reader (NFC / MIFARE PC/SC)
- High-Frequency RFID Antenna Pad (ISO 15693 / ISO 28560)
- Exit Gate Pedestal Sensor (EAS alarm detection)

Provides:
- Unified status monitoring and health checks
- Environment-controlled physical vs simulation mode selection
- Graceful recovery and disconnect handling
- Clear separation between physical device states and software simulation
=============================================================================
"""

import os
import logging
from typing import Dict, Any, Optional

from hardware.base import DeviceStatus, DeviceType
from hardware.barcode_scanner import BarcodeScannerAdapter
from hardware.card_reader import SmartCardReaderAdapter
from hardware.rfid_antenna import RFIDAntennaAdapter

logger = logging.getLogger("hardware_manager")


class HardwareManager:
    """Singleton manager coordinating all kiosk hardware peripheral adapters."""

    def __init__(self, force_simulated: bool = False):
        # Determine global hardware mode: "SIMULATED" (default) or "PHYSICAL"
        raw_hw_mode = "SIMULATED" if force_simulated else os.getenv("HARDWARE_MODE", "SIMULATED").strip().upper()
        self.is_physical_mode = (raw_hw_mode == "PHYSICAL")

        # Configure individual device modes
        scanner_mode = "SIMULATED" if force_simulated else os.getenv("BARCODE_SCANNER_MODE", "USB_HID" if self.is_physical_mode else "SIMULATED").strip().upper()
        scanner_port = None if force_simulated else os.getenv("BARCODE_SCANNER_PORT")

        card_mode = "SIMULATED" if force_simulated else os.getenv("SMART_CARD_MODE", "PCSC" if self.is_physical_mode else "SIMULATED").strip().upper()
        card_port = None if force_simulated else os.getenv("SMART_CARD_PORT")

        rfid_mode = "SIMULATED" if force_simulated else os.getenv("RFID_READER_MODE", "FEIG_USB" if self.is_physical_mode else "SIMULATED").strip().upper()
        rfid_port = None if force_simulated else os.getenv("RFID_READER_PORT")

        # Instantiate adapters
        self.scanner = BarcodeScannerAdapter(mode=scanner_mode, port=scanner_port)
        self.card_reader = SmartCardReaderAdapter(mode=card_mode, serial_port=card_port)
        self.rfid_antenna = RFIDAntennaAdapter(mode=rfid_mode, port=rfid_port)

        # Initialize connections
        self.initialize_all()

    @property
    def is_simulated(self) -> bool:
        return not self.is_physical_mode

    @property
    def barcode_scanner(self) -> BarcodeScannerAdapter:
        return self.scanner

    def initialize_all(self) -> Dict[str, bool]:
        """Initializes and connects all configured hardware adapters."""
        results = {
            "barcode_scanner": self.scanner.connect(),
            "smart_card_reader": self.card_reader.connect(),
            "rfid_antenna": self.rfid_antenna.connect(),
        }
        logger.info(
            f"Hardware Manager initialized (Mode: {'PHYSICAL' if self.is_physical_mode else 'SIMULATED'}). "
            f"Results: {results}"
        )
        return results

    def shutdown_all(self) -> None:
        """Safely releases all hardware handles and ports."""
        logger.info("Shutting down kiosk hardware peripherals...")
        self.scanner.disconnect()
        self.card_reader.disconnect()
        self.rfid_antenna.disconnect()

    def get_hardware_status(self) -> Dict[str, Any]:
        """
        Returns comprehensive diagnostic status for all hardware devices.
        Strictly distinguishes physical connections from simulated components.
        """
        devices = {
            "barcode_scanner": self.scanner.get_diagnostics(),
            "smart_card_reader": self.card_reader.get_diagnostics(),
            "rfid_antenna": self.rfid_antenna.get_diagnostics(),
        }

        all_ok = all(d["connected"] for d in devices.values())
        any_physical = any(not d["is_simulated"] for d in devices.values())
        device_list = list(devices.values())

        return {
            "status": "OPERATIONAL" if all_ok else "DEGRADED",
            "all_devices_ready": all_ok,
            "hardware_mode": "PHYSICAL" if any_physical else "SIMULATED",
            "is_simulated": not any_physical,
            "device_count": len(device_list),
            "devices": device_list,
            "device_map": devices,
            "disclaimer": (
                "Hardware devices are connected in PHYSICAL mode."
                if any_physical
                else "All hardware peripherals are operating in verified SOFTWARE SIMULATION mode. "
                     "Physical RFID/EAS tag writing requires approved vendor hardware."
            ),
        }


# Global hardware manager instance
hardware_manager = HardwareManager()
