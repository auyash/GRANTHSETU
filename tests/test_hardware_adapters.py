"""
Automated unit tests for GRANTHSETU Hardware Abstraction Layer (HAL).
Verifies:
1. BarcodeScannerAdapter (USB HID, Serial fallback, simulation, regex format validation).
2. SmartCardReaderAdapter (PC/SC APDU UID commands, contactless simulation, UID format).
3. RFIDAntennaAdapter (ISO 15693 AFI 0xC2 ARMED / 0x07 DISARMED read/write, tag inventory).
4. HardwareManager (Device registry, health diagnostics, graceful physical/simulation separation).
"""

import pytest
import os
from hardware.base import DeviceStatus, DeviceType
from hardware.barcode_scanner import BarcodeScannerAdapter
from hardware.card_reader import SmartCardReaderAdapter
from hardware.rfid_antenna import RFIDAntennaAdapter
from hardware.manager import HardwareManager


class TestBarcodeScannerAdapter:
    def test_barcode_adapter_default_initialization(self):
        adapter = BarcodeScannerAdapter(mode="SIMULATED")
        adapter.connect()
        assert adapter.device_type == DeviceType.BARCODE_SCANNER
        assert adapter.is_simulated is True
        assert adapter.status == DeviceStatus.SIMULATED

    def test_barcode_validation_valid_formats(self):
        adapter = BarcodeScannerAdapter(mode="SIMULATED")
        assert adapter.validate_barcode("BK-FOR-101") == "BK-FOR-101"
        assert adapter.validate_barcode("9780123456789") == "9780123456789"
        assert adapter.validate_barcode("NFSU2024001") == "NFSU2024001"
        assert adapter.validate_barcode("ITEM_42") == "ITEM_42"

    def test_barcode_validation_invalid_formats(self):
        adapter = BarcodeScannerAdapter(mode="SIMULATED")
        assert adapter.validate_barcode("") is None
        assert adapter.validate_barcode("   ") is None
        assert adapter.validate_barcode("BK FOR 101") is None  # spaces disallowed
        assert adapter.validate_barcode("BK$FOR#101") is None  # special chars disallowed
        assert adapter.validate_barcode("A" * 65) is None     # exceeds 32 chars

    def test_barcode_diagnostics_report(self):
        adapter = BarcodeScannerAdapter(mode="SIMULATED")
        adapter.connect()
        diag = adapter.get_diagnostics()
        assert diag["device_type"] == DeviceType.BARCODE_SCANNER.value
        assert diag["is_simulated"] is True
        assert diag["connected"] is True
        assert "interface" in diag["details"]

    def test_physical_connection_fallback_when_serial_absent(self):
        # On dev machine without physical COM port, serial connect fails safely
        adapter = BarcodeScannerAdapter(mode="SERIAL", port="COM99")
        ok = adapter.connect()
        assert ok is False
        assert adapter.status in (DeviceStatus.DISCONNECTED, DeviceStatus.ERROR)
        assert adapter.is_simulated is False


class TestSmartCardReaderAdapter:
    def test_card_reader_default_initialization(self):
        adapter = SmartCardReaderAdapter(mode="SIMULATED")
        adapter.connect()
        assert adapter.device_type == DeviceType.SMART_CARD_READER
        assert adapter.is_simulated is True
        assert adapter.status == DeviceStatus.SIMULATED

    def test_apdu_uid_command_definition(self):
        # Standard ISO/IEC 14443 Type A / MIFARE PC/SC GET DATA command for UID
        assert SmartCardReaderAdapter.APDU_GET_UID == [0xFF, 0xCA, 0x00, 0x00, 0x00]

    def test_simulate_card_read_and_resolution(self):
        adapter = SmartCardReaderAdapter(mode="SIMULATED")
        adapter.connect()
        uid = adapter.read_card_uid()
        assert uid == "CARD_E3A59102"
        patron_id = adapter.resolve_patron_id(uid)
        assert patron_id == "NFSU2024001"

    def test_card_reader_diagnostics_report(self):
        adapter = SmartCardReaderAdapter(mode="SIMULATED")
        adapter.connect()
        diag = adapter.get_diagnostics()
        assert diag["device_type"] == DeviceType.SMART_CARD_READER.value
        assert diag["is_simulated"] is True
        assert "standards" in diag["details"]


class TestRFIDAntennaAdapter:
    def test_rfid_antenna_default_initialization(self):
        adapter = RFIDAntennaAdapter(mode="SIMULATED")
        adapter.connect()
        assert adapter.device_type == DeviceType.RFID_ANTENNA
        assert adapter.is_simulated is True
        assert adapter.status == DeviceStatus.SIMULATED

    def test_iso_afi_constants(self):
        # ISO 28560 / ISO 15693 AFI Security byte definitions
        assert RFIDAntennaAdapter.AFI_ARMED == 0xC2
        assert RFIDAntennaAdapter.AFI_DISARMED == 0x07

    def test_simulate_read_and_write_afi(self):
        adapter = RFIDAntennaAdapter(mode="SIMULATED")
        adapter.connect()
        barcode = "BK-TEST-999"

        # Write disarmed (0x07)
        ok = adapter.write_afi_security(barcode, "DISARMED")
        assert ok is True
        assert adapter.read_tag_security(barcode) == "DISARMED"

        # Write armed (0xC2)
        ok = adapter.write_afi_security(barcode, "ARMED")
        assert ok is True
        assert adapter.read_tag_security(barcode) == "ARMED"

    def test_inventory_pad_returns_tags(self):
        adapter = RFIDAntennaAdapter(mode="SIMULATED")
        adapter.connect()
        tags = adapter.inventory_pad()
        assert isinstance(tags, list)
        assert len(tags) > 0
        first = tags[0]
        assert "item_id" in first
        assert "afi_state" in first
        assert "afi_byte" in first

    def test_evaluate_gate_passage(self):
        adapter = RFIDAntennaAdapter(mode="SIMULATED")
        adapter.connect()
        adapter.write_afi_security("BK-SAFE-01", "DISARMED")
        adapter.write_afi_security("BK-THEFT-01", "ARMED")

        safe_res = adapter.evaluate_gate_passage("BK-SAFE-01")
        assert safe_res["alarm_triggered"] is False
        assert safe_res["status"] == "SAFE_PASSAGE"

        theft_res = adapter.evaluate_gate_passage("BK-THEFT-01")
        assert theft_res["alarm_triggered"] is True
        assert theft_res["status"] == "ALARM_RINGING"


class TestHardwareManager:
    def test_hardware_manager_initialization(self):
        manager = HardwareManager(force_simulated=True)
        assert manager.is_simulated is True
        status = manager.get_hardware_status()
        assert status["status"] == "OPERATIONAL"
        assert status["device_count"] == 3
        assert len(status["devices"]) == 3

    def test_hardware_manager_individual_adapters_accessible(self):
        manager = HardwareManager(force_simulated=True)
        assert manager.barcode_scanner is not None
        assert manager.card_reader is not None
        assert manager.rfid_antenna is not None

    def test_hardware_status_serialization(self):
        manager = HardwareManager(force_simulated=True)
        status = manager.get_hardware_status()
        assert "devices" in status
        device_types = [d["device_type"] for d in status["devices"]]
        assert DeviceType.BARCODE_SCANNER.value in device_types
        assert DeviceType.SMART_CARD_READER.value in device_types
        assert DeviceType.RFID_ANTENNA.value in device_types
