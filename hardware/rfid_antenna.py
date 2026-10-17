"""
GRANTHSETU — HF RFID Antenna & Exit Gate Pedestal Hardware Adapter
=============================================================================
Supports:
1. ISO 15693 / ISO 18000-3 Mode 1 High-Frequency (13.56 MHz) RFID Technology:
   Standard in academic and national libraries worldwide.
2. ISO 28560 Library RFID Data Model & AFI/EAS Security:
   - AFI 0xC2 (ARMED / In-Library): Default inventory state. Exit gate alarm triggers!
   - AFI 0x07 (DISARMED / Issued): Legitimate checkout. Safe exit allowed.
3. Multi-Item Anti-Collision:
   Allows multiple books stacked on the kiosk pad to be inventoried in one pass.
4. Separation of Physical Driver from Software Simulation:
   Physical RFID tag writes are performed only when verified vendor hardware is connected.
   In simulation mode, state changes are stored in software persistence only.
=============================================================================
"""

import os
import json
import logging
from typing import Dict, Any, List, Optional
from hardware.base import BaseHardwareAdapter, DeviceType, DeviceStatus

logger = logging.getLogger("hardware_rfid")

# Persistence file for software simulation resilience
SIMULATED_RFID_FILE = os.path.join(os.path.dirname(__file__), "..", "data", "simulated_rfid_tags.json")


class RFIDAntennaAdapter(BaseHardwareAdapter):
    """Modular adapter for HF RFID reader pads and exit gate sensors."""

    # ISO 28560 Security Byte Constants
    AFI_ARMED = 0xC2       # Decimal 194 - Default on shelf
    AFI_DISARMED = 0x07    # Decimal 7 - Checked out to patron

    def __init__(
        self,
        mode: str = "SIMULATED",  # "SIMULATED" | "FEIG_USB" | "NORDIC_ID" | "SERIAL"
        port: Optional[str] = None,
    ):
        is_sim = (mode.upper() == "SIMULATED")
        super().__init__(
            name="ISO 15693 HF Library RFID Reader (Pad & Exit Gate)",
            device_type=DeviceType.RFID_ANTENNA,
            is_simulated=is_sim,
        )
        self.mode = mode.upper()
        self.port = port
        self._tag_registry: Dict[str, str] = {}
        self._load_simulated_tags()

        self._device_info = {
            "frequency": "13.56 MHz (HF)",
            "standards": ["ISO 15693", "ISO 18000-3 Mode 1", "ISO 28560-2 (Data Model)"],
            "afi_codes": {
                "ARMED": f"0x{self.AFI_ARMED:02X} (EAS Active)",
                "DISARMED": f"0x{self.AFI_DISARMED:02X} (EAS Inactive)",
            },
            "anti_collision_capable": True,
        }

    def _load_simulated_tags(self):
        """Loads simulated tag states from disk for software demo persistence."""
        defaults = {
            "BK-FOR-101": "DISARMED",
            "BK-CYB-201": "DISARMED",
            "BK-DIG-301": "DISARMED",
            "BK-BAL-401": "DISARMED",
            "BK-TOX-501": "ARMED",
            "BK-DNA-601": "ARMED",
        }
        if os.path.exists(SIMULATED_RFID_FILE):
            try:
                with open(SIMULATED_RFID_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, dict):
                        defaults.update(data)
            except Exception as e:
                logger.warning(f"Could not load simulated RFID tags: {e}")
        self._tag_registry = defaults

    def _persist_simulated_tags(self):
        """Saves simulated tag states to disk."""
        if self.is_simulated:
            try:
                os.makedirs(os.path.dirname(SIMULATED_RFID_FILE), exist_ok=True)
                with open(SIMULATED_RFID_FILE, "w", encoding="utf-8") as f:
                    json.dump(self._tag_registry, f, indent=2)
            except Exception as e:
                logger.warning(f"Could not persist simulated RFID tags: {e}")

    def connect(self) -> bool:
        """Initializes connection to physical RFID antenna or simulation."""
        self._last_error = None
        if self.is_simulated:
            self._status = DeviceStatus.SIMULATED
            logger.info("HF RFID Antenna initialized in SIMULATED mode.")
            return True

        # In physical mode, check for vendor driver / library
        if self.mode in ("FEIG_USB", "NORDIC_ID", "SERIAL"):
            self._status = DeviceStatus.DISCONNECTED
            self._last_error = (
                f"Physical {self.mode} driver not present in environment. "
                "Connect verified ISO 15693 hardware and install vendor SDK (e.g. Feig FEDM / OBID)."
            )
            logger.warning(self._last_error)
            return False

        self._status = DeviceStatus.ERROR
        self._last_error = f"Unsupported RFID antenna mode: {self.mode}"
        return False

    def disconnect(self) -> None:
        if not self.is_simulated:
            self._status = DeviceStatus.DISCONNECTED

    def inventory_pad(self) -> List[Dict[str, Any]]:
        """
        Executes ISO 15693 inventory command to identify all RFID tags currently on pad.
        Returns list of tags with barcode, UID, and current AFI state.
        """
        if not self.is_connected:
            raise RuntimeError(f"RFID reader is not connected ({self._last_error or 'Offline'}).")

        # In simulation mode, return registered tags
        return [
            {
                "item_id": barcode,
                "afi_state": state,
                "afi_byte": hex(self.AFI_ARMED if state == "ARMED" else self.AFI_DISARMED),
                "is_physical": False,
            }
            for barcode, state in self._tag_registry.items()
        ]

    def read_tag_security(self, item_barcode: str) -> str:
        """Reads AFI / EAS security state for an item: 'ARMED' or 'DISARMED'."""
        if not self.is_connected:
            raise RuntimeError(f"RFID reader is not connected ({self._last_error or 'Offline'}).")
        return self._tag_registry.get(item_barcode.strip(), "ARMED")

    def write_afi_security(self, item_barcode: str, target_state: str) -> bool:
        """
        Writes ISO 28560 AFI byte to RFID tag chip.
        - 'DISARMED' -> Sets AFI 0x07 (Allows patron to pass exit gate safely)
        - 'ARMED'    -> Sets AFI 0xC2 (Secures item in library; alarm sounds at gate)

        IMPORTANT: If physical hardware is not connected, this updates software
        simulation state only and NEVER falsely claims physical chip disarming.
        """
        if not self.is_connected:
            raise RuntimeError(f"RFID reader is not connected ({self._last_error or 'Offline'}).")

        target_state = target_state.upper().strip()
        if target_state not in ("ARMED", "DISARMED"):
            raise ValueError(f"Invalid AFI target state: '{target_state}'. Must be ARMED or DISARMED.")

        item_barcode = item_barcode.strip()
        self._tag_registry[item_barcode] = target_state
        self._persist_simulated_tags()
        logger.info(
            f"RFID Tag {item_barcode} set to {target_state} (AFI 0x{self.AFI_DISARMED if target_state == 'DISARMED' else self.AFI_ARMED:02X}) "
            f"[{'SIMULATION' if self.is_simulated else 'PHYSICAL'}]."
        )
        return True

    def evaluate_gate_passage(self, item_barcode: str) -> Dict[str, Any]:
        """
        Simulates an RFID tag passing through library exit security pedestals.
        If tag AFI is ARMED (0xC2) -> Alarm triggered!
        If tag AFI is DISARMED (0x07) -> Safe passage!
        """
        item_barcode = item_barcode.strip()
        state = self.read_tag_security(item_barcode)

        if state == "ARMED":
            return {
                "alarm_triggered": True,
                "status": "ALARM_RINGING",
                "message": f"🚨 SECURITY ALARM TRIGGERED! Item {item_barcode} is ARMED (AFI 0xC2). Unauthorized removal detected!",
                "item_id": item_barcode,
                "eas_state": "ARMED",
                "audio_alert": True,
                "is_physical": not self.is_simulated,
            }
        else:
            return {
                "alarm_triggered": False,
                "status": "SAFE_PASSAGE",
                "message": f"✅ SAFE PASSAGE: Item {item_barcode} is DISARMED (AFI 0x07). Legitimate checkout verified.",
                "item_id": item_barcode,
                "eas_state": "DISARMED",
                "audio_alert": False,
                "is_physical": not self.is_simulated,
            }
