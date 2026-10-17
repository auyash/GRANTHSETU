"""
Automated unit tests for GRANTHSETU Transaction Tray & Digital PDF Receipts.
Verifies:
1. Session pre-checkout tray management (add, remove, clear, batch checkout).
2. Strict 2-book limit pre-send check on tray addition:
   (charged_count + len(tray) + 1) > 2 immediately blocked with audit logging.
3. PDF receipt generation via ReportLab (valid %PDF binary, masked patron ID, SHA-256 verification hash).
4. Ambiguous UNKNOWN_AFTER_SEND reconciliation instruction generation.
"""

import pytest
import io
import hashlib
from kiosk_engine import KioskController
from services.receipt_generator import ReceiptGenerator


@pytest.fixture
def test_controller():
    controller = KioskController()
    controller.mode = "MOCK"
    controller.rfid_tag_registry = {
        "BK-FOR-101": "ARMED",
        "BK-CYB-201": "ARMED",
        "BK-TOX-501": "ARMED",
        "BK-DNA-601": "ARMED",
    }
    controller.session_tray = []
    controller.session_history = []
    return controller


class TestTransactionTrayOperations:
    def test_add_to_tray_requires_authenticated_patron(self, test_controller):
        test_controller.active_patron = None
        res = test_controller.add_to_tray("BK-FOR-101")
        assert res["success"] is False
        assert res["error_code"] == "FAILED_BEFORE_SEND"
        assert "authenticate" in res["message"].lower()

    def test_add_to_tray_empty_barcode_rejected(self, test_controller):
        test_controller.authenticate_patron("NFSU2024001")
        res = test_controller.add_to_tray("   ")
        assert res["success"] is False
        assert res["error_code"] == "FAILED_BEFORE_SEND"

    def test_add_to_tray_success_and_slot_tracking(self, test_controller):
        # NFSU2024001 starts with 0 charged books
        test_controller.authenticate_patron("NFSU2024001")
        res = test_controller.add_to_tray("BK-FOR-101")
        assert res["success"] is True
        assert res["tray_count"] == 1
        assert res["available_slots"] == 1
        assert res["limit_reached"] is False
        assert res["item"]["barcode"] == "BK-FOR-101"

    def test_add_duplicate_item_to_tray_rejected(self, test_controller):
        test_controller.authenticate_patron("NFSU2024001")
        test_controller.add_to_tray("BK-FOR-101")
        dup_res = test_controller.add_to_tray("BK-FOR-101")
        assert dup_res["success"] is False
        assert dup_res["error_code"] == "DUPLICATE_TRAY_ITEM"
        assert dup_res["tray_count"] == 1

    def test_tray_limit_guard_patron_with_zero_books(self, test_controller):
        # 0 books charged -> can add at most 2 books to tray
        test_controller.authenticate_patron("NFSU2024001")
        res1 = test_controller.add_to_tray("BK-FOR-101")
        assert res1["success"] is True
        res2 = test_controller.add_to_tray("BK-CYB-201")
        assert res2["success"] is True
        assert res2["available_slots"] == 0

        # Attempting 3rd book must be blocked before send
        res3 = test_controller.add_to_tray("BK-TOX-501")
        assert res3["success"] is False
        assert res3["error_code"] == "FAILED_BEFORE_SEND"
        assert "Borrowing limit reached" in res3["message"]
        assert len(test_controller.session_tray) == 2

    def test_tray_limit_guard_patron_with_one_book_charged(self, test_controller):
        # NFSU2024002 has 1 book already charged
        test_controller.authenticate_patron("NFSU2024002")
        assert test_controller.active_patron["charged_count"] == 1

        # Can add 1 book to tray
        res1 = test_controller.add_to_tray("BK-FOR-101")
        assert res1["success"] is True
        assert res1["available_slots"] == 0

        # Second book in tray violates 2-book limit: (1 charged + 1 tray + 1 = 3 > 2)
        res2 = test_controller.add_to_tray("BK-CYB-201")
        assert res2["success"] is False
        assert res2["error_code"] == "FAILED_BEFORE_SEND"
        assert len(test_controller.session_tray) == 1

    def test_tray_limit_guard_patron_with_two_books_charged(self, test_controller):
        # NFSU2024003 has 2 books charged (limit reached)
        test_controller.authenticate_patron("NFSU2024003")
        assert test_controller.active_patron["charged_count"] == 2

        # Adding even 1 book to tray is blocked immediately
        res = test_controller.add_to_tray("BK-FOR-101")
        assert res["success"] is False
        assert res["error_code"] == "FAILED_BEFORE_SEND"
        assert len(test_controller.session_tray) == 0

    def test_remove_from_tray(self, test_controller):
        test_controller.authenticate_patron("NFSU2024001")
        test_controller.add_to_tray("BK-FOR-101")
        test_controller.add_to_tray("BK-CYB-201")
        assert len(test_controller.session_tray) == 2

        rem_res = test_controller.remove_from_tray("BK-FOR-101")
        assert rem_res["success"] is True
        assert rem_res["tray_count"] == 1
        assert rem_res["available_slots"] == 1
        assert test_controller.session_tray[0]["barcode"] == "BK-CYB-201"

    def test_clear_tray(self, test_controller):
        test_controller.authenticate_patron("NFSU2024001")
        test_controller.add_to_tray("BK-FOR-101")
        assert len(test_controller.session_tray) == 1

        clear_res = test_controller.clear_tray()
        assert clear_res["success"] is True
        assert clear_res["tray_count"] == 0
        assert len(test_controller.session_tray) == 0

    def test_checkout_tray_executes_batch(self, test_controller):
        test_controller.authenticate_patron("NFSU2024001")
        test_controller.add_to_tray("BK-TOX-501")
        test_controller.add_to_tray("BK-DNA-601")

        checkout_res = test_controller.checkout_tray()
        assert checkout_res["success"] is True
        assert checkout_res["processed_count"] == 2
        assert checkout_res["remaining_tray_count"] == 0
        assert len(test_controller.session_tray) == 0

        # Verify RFID security tags disarmed
        assert test_controller.rfid_tag_registry["BK-TOX-501"] == "DISARMED"
        assert test_controller.rfid_tag_registry["BK-DNA-601"] == "DISARMED"

        # Verify patron charged count updated
        assert test_controller.active_patron["charged_count"] == 2


class TestReceiptGenerationAndIntegrity:
    def test_receipt_data_builder_structure(self):
        patron = {"patron_id": "NFSU2024001", "name": "Aarav Sharma", "charged_count": 1}
        items = [
            {
                "action": "ISSUE",
                "barcode": "BK-FOR-101",
                "title": "Forensic Science in Criminal Investigation",
                "due_date": "2026-11-01",
                "timestamp": "2026-10-16 12:00:00",
            }
        ]
        data = ReceiptGenerator.build_receipt_data(
            patron=patron,
            items=items,
            circulation_mode="ISSUE",
            koha_mode="MOCK"
        )
        assert "National Forensic Sciences University" in data["institution"]
        assert data["patron_name"] == "Aarav Sharma"
        # Masked ID: "NFSU****001"
        assert "NFSU****001" == data["patron_id_masked"]
        assert len(data["items"]) == 1
        assert "sha256_fingerprint" in data
        assert len(data["sha256_fingerprint"]) == 64  # valid SHA-256 hex string

    def test_generate_pdf_bytes_magic_number(self):
        patron = {"patron_id": "NFSU2024001", "name": "Aarav Sharma", "charged_count": 1}
        items = [
            {
                "action": "ISSUE",
                "barcode": "BK-FOR-101",
                "title": "Forensic Science in Criminal Investigation",
                "due_date": "2026-11-01",
                "timestamp": "2026-10-16 12:00:00",
            }
        ]
        receipt_data = ReceiptGenerator.build_receipt_data(
            patron=patron,
            items=items,
            circulation_mode="ISSUE",
            koha_mode="MOCK"
        )
        pdf_bytes = ReceiptGenerator.generate_pdf_bytes(receipt_data)
        assert isinstance(pdf_bytes, bytes)
        assert len(pdf_bytes) > 500
        # Check standard PDF magic header '%PDF-'
        assert pdf_bytes.startswith(b"%PDF-")

    def test_reconciliation_instructions_on_unknown_after_send(self, test_controller):
        # Force UNKNOWN_AFTER_SEND
        from sip2.client import SIP2TransmissionError
        def mock_error(*args, **kwargs):
            raise SIP2TransmissionError("Simulated interrupted connection")

        test_controller.authenticate_patron("NFSU2024001")
        test_controller.sip_client.checkout_item = mock_error

        res = test_controller.checkout_item("BK-FOR-101")
        assert res["success"] is False
        assert res["error_code"] == "UNKNOWN_AFTER_SEND"
        assert "reconciliation_instructions" in res
        assert len(res["reconciliation_instructions"]) >= 3
        # Security tag remains ARMED
        assert "ARMED" in res["rfid_security"]
        assert test_controller.rfid_tag_registry["BK-FOR-101"] == "ARMED"
