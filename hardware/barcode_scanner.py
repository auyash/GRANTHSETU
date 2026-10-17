"""
GRANTHSETU — Barcode Scanner Hardware Adapter
=============================================================================
Supports:
1. USB HID Keyboard Wedge Mode:
   Standard for library handheld and flush-mount counter scanners.
   Scans arrive as keyboard input streams ending with CR/LF.
2. Serial / Virtual COM Port Mode (RS-232 / USB-CDC):
   Direct port reading with configurable baud rate and parity.
   Uses pyserial if available; fails safely if driver/port is missing.
3. High-Fidelity Simulation Mode:
   Allows automated testing and evaluator demonstrations with known barcodes.
=============================================================================
"""

import re
import logging
from typing import Dict, Any, Optional
from hardware.base import BaseHardwareAdapter, DeviceType, DeviceStatus

logger = logging.getLogger("hardware_scanner")


class BarcodeScannerAdapter(BaseHardwareAdapter):
    """Modular adapter for barcode reading equipment."""

    BARCODE_PATTERN = re.compile(r"^[A-Za-z0-9\-_]{3,32}$")

    def __init__(
        self,
        mode: str = "SIMULATED",  # "SIMULATED" | "USB_HID" | "SERIAL"
        port: Optional[str] = None,
        baud_rate: int = 9600,
        timeout: float = 2.0,
    ):
        is_sim = (mode.upper() == "SIMULATED")
        super().__init__(
            name="Honeywell/Zebra Barcode Scanner",
            device_type=DeviceType.BARCODE_SCANNER,
            is_simulated=is_sim,
        )
        self.mode = mode.upper()
        self.port = port
        self.baud_rate = baud_rate
        self.timeout = timeout
        self._serial_handle = None

        self._device_info = {
            "interface": self.mode,
            "port": self.port or ("KEYBOARD_WEDGE" if self.mode == "USB_HID" else "VIRTUAL_LOOPBACK"),
            "supported_symbologies": ["Code 39", "Code 128", "EAN-13", "QR Code"],
        }

    def connect(self) -> bool:
        """Connects to the barcode scanner device according to configured mode."""
        self._last_error = None
        if self.is_simulated:
            self._status = DeviceStatus.SIMULATED
            logger.info("Barcode scanner initialized in SIMULATED mode.")
            return True

        if self.mode == "USB_HID":
            # USB HID keyboard wedge does not require COM port handles; it captures via standard input
            self._status = DeviceStatus.CONNECTED
            self._device_info["status_detail"] = "Listening for keyboard-wedge keystrokes"
            logger.info("Barcode scanner connected in USB HID Keyboard-Wedge mode.")
            return True

        if self.mode == "SERIAL":
            if not self.port:
                self._status = DeviceStatus.ERROR
                self._last_error = "Serial barcode scanner requires a designated COM port (e.g. COM1, /dev/ttyUSB0)."
                logger.error(self._last_error)
                return False

            try:
                import serial
                self._serial_handle = serial.Serial(
                    port=self.port,
                    baudrate=self.baud_rate,
                    timeout=self.timeout
                )
                self._status = DeviceStatus.CONNECTED
                self._device_info["status_detail"] = f"Open on {self.port} at {self.baud_rate} baud"
                logger.info(f"Barcode scanner opened on serial port {self.port}.")
                return True
            except ImportError:
                self._status = DeviceStatus.ERROR
                self._last_error = "pyserial library is not installed. Install with 'pip install pyserial' for serial barcode scanners."
                logger.warning(self._last_error)
                return False
            except Exception as e:
                self._status = DeviceStatus.DISCONNECTED
                self._last_error = f"Failed to open barcode scanner port {self.port}: {type(e).__name__} - {e}"
                logger.error(self._last_error)
                return False

        self._status = DeviceStatus.ERROR
        self._last_error = f"Unsupported scanner mode: {self.mode}"
        return False

    def disconnect(self) -> None:
        """Closes serial connection if open."""
        if self._serial_handle:
            try:
                self._serial_handle.close()
            except Exception:
                pass
            self._serial_handle = None
        if not self.is_simulated:
            self._status = DeviceStatus.DISCONNECTED

    def validate_barcode(self, raw_barcode: str) -> Optional[str]:
        """
        Cleans and validates a scanned barcode.
        Rejects empty or malformed strings that don't match standard library formats.
        """
        if not raw_barcode or not isinstance(raw_barcode, str):
            return None
        cleaned = raw_barcode.strip().strip("\r\n")
        if not self.BARCODE_PATTERN.match(cleaned):
            logger.warning(f"Barcode validation rejected invalid format: '{cleaned}'")
            return None
        return cleaned

    def read_barcode(self, timeout_override: Optional[float] = None) -> Optional[str]:
        """
        Reads a single barcode from serial port or simulation buffer.
        Returns validated barcode string, or None if timed out / invalid.
        """
        if not self.is_connected:
            raise RuntimeError(f"Barcode scanner is not connected ({self._last_error or 'Offline'}).")

        if self.is_simulated or self.mode == "USB_HID":
            # For USB HID or Simulation, inputs arrive through application endpoints or UI
            return None

        if self.mode == "SERIAL" and self._serial_handle:
            try:
                raw_bytes = self._serial_handle.readline()
                if raw_bytes:
                    decoded = raw_bytes.decode("ascii", errors="replace").strip()
                    return self.validate_barcode(decoded)
            except Exception as e:
                self._last_error = f"Serial read error: {e}"
                logger.error(self._last_error)
                return None
        return None
