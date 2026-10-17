"""
Unit and Integration Tests for Transaction Safety, Failure Semantics, and Policy Enforcement.
Verifies:
- FAILED_BEFORE_SEND on policy limit violations or disconnected states.
- UNKNOWN_AFTER_SEND on socket timeouts or dropped connections after transmission.
- Safe state preservation: UNKNOWN transactions NEVER disarm RFID tags or increment patron counts.
- Authoritative Koha confirmation requirements.
- 2-Book borrowing limit boundary conditions (0, 1, 2, attempt on 3rd).
"""

import pytest
from datetime import datetime, timedelta
from unittest.mock import MagicMock
from kiosk_engine import KioskController
from sip2.client import (
    KohaSIP2Client,
    SIP2ConnectionError,
    SIP2PreSendError,
    SIP2SendError,
    SIP2TransmissionError,
    SIP2ReceiveError,
    SIP2ProtocolError
)


class TestBorrowingLimitGuard:
    def test_two_book_limit_enforced_before_send(self):
        controller = KioskController()
        # Simulate patron with 2 books already borrowed
        controller.active_patron = {
            "patron_id": "NFSU2024003",
            "name": "Dr. Rajesh Verma",
            "charged_count": 2,
            "charged_items": ["BK-CYB-201", "BK-DIG-301"],
            "available_limit": 0,
            "limit_reached": True
        }

        controller.rfid_tag_registry["BK-TOX-501"] = "ARMED"

        # Attempt to issue a 3rd book
        res = controller.checkout_item("BK-TOX-501")
        assert res["success"] is False
        assert res["error_code"] == "FAILED_BEFORE_SEND"
        assert "Borrowing limit reached" in res["message"]
        assert controller.active_patron["charged_count"] == 2
        # RFID tag of the unissued item must remain ARMED
        assert controller.rfid_tag_registry["BK-TOX-501"] == "ARMED"

    def test_patron_with_one_book_can_issue_second(self):
        controller = KioskController()
        controller.active_patron = {
            "patron_id": "NFSU2024002",
            "name": "Priya Nair",
            "charged_count": 1,
            "charged_items": ["BK-FOR-101"],
            "available_limit": 1,
            "limit_reached": False
        }

        # Issue 2nd book
        res = controller.checkout_item("BK-TOX-501")
        assert res["success"] is True
        assert controller.active_patron["charged_count"] == 2
        assert controller.active_patron["limit_reached"] is True
        assert "BK-TOX-501" in controller.active_patron["charged_items"]
        assert controller.rfid_tag_registry["BK-TOX-501"] == "DISARMED"


class TestSafeFailureSemantics:
    def test_unknown_after_send_preserves_armed_security(self):
        """Simulates network interruption after checkout message is transmitted."""
        controller = KioskController()
        controller.active_patron = {
            "patron_id": "NFSU2024001",
            "name": "Aarav Sharma",
            "charged_count": 0,
            "charged_items": [],
            "available_limit": 2,
            "limit_reached": False
        }
        controller.rfid_tag_registry["BK-DNA-601"] = "ARMED"

        # Mock SIP2 client to raise SIP2ReceiveError (sent, but timeout/drop on recv)
        controller.sip_client.checkout_item = MagicMock(
            side_effect=SIP2ReceiveError("Socket timed out waiting for response from Koha")
        )

        res = controller.checkout_item("BK-DNA-601")
        
        # 1. Must fail with UNKNOWN_AFTER_SEND
        assert res["success"] is False
        assert res["error_code"] == "UNKNOWN_AFTER_SEND"
        assert "Transaction outcome is UNKNOWN" in res["message"]
        
        # 2. CRITICAL SAFETY: Tag must remain ARMED
        assert controller.rfid_tag_registry["BK-DNA-601"] == "ARMED"
        
        # 3. CRITICAL SAFETY: Patron count must NOT be incremented
        assert controller.active_patron["charged_count"] == 0
        assert "BK-DNA-601" not in controller.active_patron["charged_items"]

    def test_checksum_failure_after_send_preserves_armed_security(self):
        """Simulates response packet corruption detected by checksum validation."""
        controller = KioskController()
        controller.active_patron = {
            "patron_id": "NFSU2024001",
            "name": "Aarav Sharma",
            "charged_count": 0,
            "charged_items": [],
            "available_limit": 2,
            "limit_reached": False
        }
        controller.rfid_tag_registry["BK-DNA-601"] = "ARMED"

        # Mock SIP2 client to raise SIP2ProtocolError (checksum failed)
        controller.sip_client.checkout_item = MagicMock(
            side_effect=SIP2ProtocolError("SIP2 AZ checksum validation failed")
        )

        res = controller.checkout_item("BK-DNA-601")
        assert res["success"] is False
        assert res["error_code"] == "UNKNOWN_AFTER_SEND"
        assert controller.rfid_tag_registry["BK-DNA-601"] == "ARMED"
        assert controller.active_patron["charged_count"] == 0

    def test_koha_denied_preserves_armed_security(self):
        """Simulates authoritative Koha refusal (e.g. item on reserve)."""
        controller = KioskController()
        controller.active_patron = {
            "patron_id": "NFSU2024001",
            "name": "Aarav Sharma",
            "charged_count": 0,
            "charged_items": []
        }
        controller.rfid_tag_registry["BK-DNA-601"] = "ARMED"

        # Mock Koha response with ok=0 (Denied)
        controller.sip_client.checkout_item = MagicMock(return_value={
            "ok": False,
            "desensitize": False,
            "screen_msg": "Book is on hold for another patron"
        })

        res = controller.checkout_item("BK-DNA-601")
        assert res["success"] is False
        assert res["error_code"] == "DENIED"
        assert controller.rfid_tag_registry["BK-DNA-601"] == "ARMED"
        assert controller.active_patron["charged_count"] == 0


class TestRFIDExitGateIntegration:
    def test_armed_item_triggers_security_alarm(self):
        controller = KioskController()
        controller.rfid_tag_registry["BK-DNA-601"] = "ARMED"

        gate_res = controller.test_exit_gate("BK-DNA-601")
        assert gate_res["alarm_triggered"] is True
        assert gate_res["status"] == "ALARM_RINGING"
        assert gate_res["eas_state"] == "ARMED"

    def test_disarmed_item_passes_freely(self):
        controller = KioskController()
        controller.rfid_tag_registry["BK-FOR-101"] = "DISARMED"

        gate_res = controller.test_exit_gate("BK-FOR-101")
        assert gate_res["alarm_triggered"] is False
        assert gate_res["status"] == "SAFE_PASSAGE"
        assert gate_res["eas_state"] == "DISARMED"


class TestSendFailureSafety:
    def test_pre_send_failure_disconnected_socket(self):
        """When client is disconnected, checkout fails safely before send."""
        controller = KioskController()
        controller.active_patron = {
            "patron_id": "NFSU2024001",
            "name": "Aarav Sharma",
            "charged_count": 0,
            "charged_items": [],
            "available_limit": 2,
            "limit_reached": False
        }
        controller.rfid_tag_registry["BK-DNA-601"] = "ARMED"
        # Simulate disconnected live client
        controller.sip_client.is_mock_mode = False
        controller.sip_client.is_connected = False
        controller.sip_client.sock = None

        res = controller.checkout_item("BK-DNA-601")
        assert res["success"] is False
        assert res["error_code"] == "FAILED_BEFORE_SEND"
        assert "Request was not transmitted" in res["message"] or "aborted before transmission" in res["message"]
        assert controller.rfid_tag_registry["BK-DNA-601"] == "ARMED"
        assert controller.active_patron["charged_count"] == 0

    def test_sendall_socket_exception_classified_as_unknown_after_send(self):
        """
        When socket.sendall() raises an exception, bytes may have reached Koha.
        The system must classify this as UNKNOWN_AFTER_SEND, never assume zero bytes arrived,
        never disarm the RFID tag, and never increment the patron count.
        """
        controller = KioskController()
        controller.active_patron = {
            "patron_id": "NFSU2024001",
            "name": "Aarav Sharma",
            "charged_count": 0,
            "charged_items": [],
            "available_limit": 2,
            "limit_reached": False
        }
        controller.rfid_tag_registry["BK-DNA-601"] = "ARMED"

        # Mock socket whose sendall() raises BrokenPipeError (simulating partial or interrupted send)
        mock_sock = MagicMock()
        mock_sock.sendall.side_effect = BrokenPipeError("Broken pipe during sendall transmission")
        
        controller.sip_client.is_mock_mode = False
        controller.sip_client.is_connected = True
        controller.sip_client.sock = mock_sock

        res = controller.checkout_item("BK-DNA-601")
        
        # 1. Must be UNKNOWN_AFTER_SEND, NOT FAILED_BEFORE_SEND
        assert res["success"] is False
        assert res["error_code"] == "UNKNOWN_AFTER_SEND"
        assert "outcome is UNKNOWN" in res["message"]
        assert "reconcile your circulation record in Koha" in res["message"]
        
        # 2. Tag must remain ARMED
        assert controller.rfid_tag_registry["BK-DNA-601"] == "ARMED"
        
        # 3. Patron count must NOT be incremented
        assert controller.active_patron["charged_count"] == 0
        assert "BK-DNA-601" not in controller.active_patron["charged_items"]

    def test_sendall_exception_on_checkin_classified_as_unknown_after_send(self):
        """When socket.sendall() fails during checkin, outcome is UNKNOWN_AFTER_SEND."""
        controller = KioskController()
        mock_sock = MagicMock()
        mock_sock.sendall.side_effect = ConnectionResetError("Connection reset by peer during send")
        controller.sip_client.is_mock_mode = False
        controller.sip_client.is_connected = True
        controller.sip_client.sock = mock_sock

        res = controller.checkin_item("BK-FOR-101")
        assert res["success"] is False
        assert res["error_code"] == "UNKNOWN_AFTER_SEND"
        assert "reconcile the transaction in Koha" in res["message"]

    def test_sendall_exception_on_renew_classified_as_unknown_after_send(self):
        """When socket.sendall() fails during renew, outcome is UNKNOWN_AFTER_SEND."""
        controller = KioskController()
        controller.active_patron = {
            "patron_id": "NFSU2024001",
            "name": "Aarav Sharma",
            "charged_count": 1,
            "charged_items": ["BK-FOR-101"]
        }
        mock_sock = MagicMock()
        mock_sock.sendall.side_effect = TimeoutError("Network timeout during socket send")
        controller.sip_client.is_mock_mode = False
        controller.sip_client.is_connected = True
        controller.sip_client.sock = mock_sock

        res = controller.renew_item("BK-FOR-101")
        assert res["success"] is False
        assert res["error_code"] == "UNKNOWN_AFTER_SEND"
        assert "reconcile the renewal in Koha" in res["message"]


class TestSequenceNumberValidation:
    def test_sequence_validation_disabled_by_default_accepts_missing_ay(self):
        """Default check_sequence=False accepts responses even when AY tag is missing."""
        client = KohaSIP2Client(mode="LIVE", host="127.0.0.1", port=6001, username="u", password="p", check_sequence=False)
        mock_sock = MagicMock()
        # Response has checksum AZ but no sequence AY
        mock_sock.recv.side_effect = [b"121NYY20261016    120000AONFSU|AA123|AB456|AZD1B3\r"]
        client.is_connected = True
        client.sock = mock_sock

        raw = client.send_and_receive("11YN...AY2AZ...\r", expected_seq=2)
        assert "121NYY" in raw

    def test_sequence_validation_disabled_accepts_different_ay(self):
        """Default check_sequence=False accepts responses when AY differs from request."""
        client = KohaSIP2Client(mode="LIVE", host="127.0.0.1", port=6001, username="u", password="p", check_sequence=False)
        mock_sock = MagicMock()
        # Request had seq 1, response has AY8
        mock_sock.recv.side_effect = [b"121NYY20261016    120000AONFSU|AA123|AB456|AY8AZC308\r"]
        client.is_connected = True
        client.sock = mock_sock

        raw = client.send_and_receive("11YN...AY1AZ...\r", expected_seq=1)
        assert "AY8" in raw

    def test_sequence_validation_enabled_accepts_matching_ay(self):
        """When check_sequence=True, matching sequence is accepted."""
        client = KohaSIP2Client(mode="LIVE", host="127.0.0.1", port=6001, username="u", password="p", check_sequence=True)
        mock_sock = MagicMock()
        # Response matches expected_seq=3
        mock_sock.recv.side_effect = [b"121NYY20261016    120000AONFSU|AA123|AB456|AY3AZB348\r"]
        client.is_connected = True
        client.sock = mock_sock

        raw = client.send_and_receive("11YN...AY3AZ...\r", expected_seq=3)
        assert "AY3" in raw

    def test_sequence_validation_enabled_rejects_mismatched_ay(self):
        """When check_sequence=True, mismatched sequence raises SIP2ProtocolError."""
        client = KohaSIP2Client(mode="LIVE", host="127.0.0.1", port=6001, username="u", password="p", check_sequence=True)
        mock_sock = MagicMock()
        # Response has AY7 instead of expected 3
        mock_sock.recv.side_effect = [b"121NYY20261016    120000AONFSU|AA123|AB456|AY7AZA348\r"]
        client.is_connected = True
        client.sock = mock_sock

        with pytest.raises(SIP2ProtocolError) as exc_info:
            client.send_and_receive("11YN...AY3AZ...\r", expected_seq=3)
        assert "sequence number mismatch" in str(exc_info.value)

    def test_sequence_validation_enabled_rejects_missing_ay(self):
        """When check_sequence=True, missing AY in response raises SIP2ProtocolError."""
        client = KohaSIP2Client(mode="LIVE", host="127.0.0.1", port=6001, username="u", password="p", check_sequence=True)
        mock_sock = MagicMock()
        # Response omitted AY entirely
        mock_sock.recv.side_effect = [b"121NYY20261016    120000AONFSU|AA123|AB456|\r"]
        client.is_connected = True
        client.sock = mock_sock

        with pytest.raises(SIP2ProtocolError) as exc_info:
            client.send_and_receive("11YN...AY3AZ...\r", expected_seq=3)
        assert "omitted AY tag" in str(exc_info.value)


class TestEncodingSafety:
    def test_ascii_encoding_accepts_ascii_payload(self):
        """Client configured with encoding='ascii' successfully encodes and sends ASCII payload."""
        client = KohaSIP2Client(mode="LIVE", host="127.0.0.1", port=6001, username="u", password="p", encoding="ascii")
        mock_sock = MagicMock()
        mock_sock.recv.side_effect = [b"121NYY20261016    120000AONFSU|AA123|AB456|\r"]
        client.is_connected = True
        client.sock = mock_sock

        msg = "11YN20261016    120000                  AONFSU|AA123|AB456|\r"
        raw = client.send_and_receive(msg)
        assert "121NYY" in raw
        mock_sock.sendall.assert_called_once_with(msg.encode("ascii"))

    def test_ascii_encoding_rejects_non_ascii_at_presend(self):
        """Client configured with encoding='ascii' fails before send if non-ASCII text is passed."""
        client = KohaSIP2Client(mode="LIVE", host="127.0.0.1", port=6001, username="u", password="p", encoding="ascii")
        mock_sock = MagicMock()
        client.is_connected = True
        client.sock = mock_sock

        # Message with Devanagari Hindi characters
        msg = "11YN20261016    120000                  AONFSU|AA123|AJन्यायालयिक|\r"
        with pytest.raises(SIP2PreSendError) as exc_info:
            client.send_and_receive(msg)
        assert "Failed to encode SIP2 message" in str(exc_info.value)
        # Socket sendall must NEVER have been called!
        mock_sock.sendall.assert_not_called()

    def test_utf8_encoding_handles_unicode_text(self):
        """Client configured with encoding='utf-8' correctly sends and decodes Unicode text."""
        client = KohaSIP2Client(mode="LIVE", host="127.0.0.1", port=6001, username="u", password="p", encoding="utf-8")
        mock_sock = MagicMock()
        resp_text = "121NYY20261016    120000AONFSU|AA123|AJन्यायालयिक विज्ञान|\r"
        mock_sock.recv.side_effect = [resp_text.encode("utf-8")]
        client.is_connected = True
        client.sock = mock_sock

        msg = "11YN20261016    120000                  AONFSU|AA123|AB456|\r"
        raw = client.send_and_receive(msg)
        assert "न्यायालयिक विज्ञान" in raw
        mock_sock.sendall.assert_called_once_with(msg.encode("utf-8"))

    def test_invalid_byte_stream_raises_protocol_error_without_silent_loss(self):
        """Corrupted byte sequence raises SIP2ProtocolError instead of silently losing data."""
        client = KohaSIP2Client(mode="LIVE", host="127.0.0.1", port=6001, username="u", password="p", encoding="utf-8")
        mock_sock = MagicMock()
        # Invalid UTF-8 bytes followed by \r
        mock_sock.recv.side_effect = [b"\xff\xfe\x80\r"]
        client.is_connected = True
        client.sock = mock_sock

        with pytest.raises(SIP2ProtocolError) as exc_info:
            client.send_and_receive("11...\r")
        assert "Failed to decode SIP2 response" in str(exc_info.value)
        assert "Corrupted or incompatible byte stream" in str(exc_info.value)


class TestSessionLifecycleAndInactivityTimeout:
    def test_active_session_retains_patron_within_timeout(self):
        controller = KioskController()
        controller.active_patron = {
            "patron_id": "NFSU2024001",
            "name": "Aarav Sharma",
            "charged_count": 0,
            "charged_items": [],
            "available_limit": 2,
            "limit_reached": False
        }
        # Activity 10s ago (within 60s limit)
        controller.last_activity_at = datetime.now() - timedelta(seconds=10)
        assert controller.check_session_expiry() is False
        assert controller.active_patron is not None
        assert controller.active_patron["patron_id"] == "NFSU2024001"

    def test_session_expiry_after_inactivity_timeout(self):
        controller = KioskController(session_timeout_seconds=60)
        controller.active_patron = {
            "patron_id": "NFSU2024001",
            "name": "Aarav Sharma",
            "charged_count": 0,
            "charged_items": [],
            "available_limit": 2,
            "limit_reached": False
        }
        # Deterministic simulation: time elapsed exceeds 60s timeout
        controller.last_activity_at = datetime.now() - timedelta(seconds=65)

        # Must detect expiry and clear active patron
        assert controller.check_session_expiry() is True
        assert controller.active_patron is None
        assert controller.session_started_at is None
        assert controller.last_activity_at is None

    def test_checkout_attempt_after_session_expiry_fails_safely(self):
        controller = KioskController(session_timeout_seconds=60)
        controller.active_patron = {
            "patron_id": "NFSU2024001",
            "name": "Aarav Sharma",
            "charged_count": 0,
            "charged_items": [],
            "available_limit": 2,
            "limit_reached": False
        }
        controller.rfid_tag_registry["BK-TOX-501"] = "ARMED"

        # Simulate expired session
        controller.last_activity_at = datetime.now() - timedelta(seconds=70)

        res = controller.checkout_item("BK-TOX-501")
        assert res["success"] is False
        assert res["error_code"] == "FAILED_BEFORE_SEND"
        assert "expired" in res["message"].lower() or "no active patron session" in res["message"].lower()
        # Security tag of requested book must remain ARMED
        assert controller.rfid_tag_registry["BK-TOX-501"] == "ARMED"
        assert controller.active_patron is None

    def test_renew_attempt_after_session_expiry_fails_safely(self):
        controller = KioskController(session_timeout_seconds=60)
        controller.active_patron = {
            "patron_id": "NFSU2024001",
            "name": "Aarav Sharma",
            "charged_count": 1,
            "charged_items": ["BK-FOR-101"],
            "available_limit": 1,
            "limit_reached": False
        }
        controller.last_activity_at = datetime.now() - timedelta(seconds=80)

        res = controller.renew_item("BK-FOR-101")
        assert res["success"] is False
        assert res["error_code"] == "FAILED_BEFORE_SEND"
        assert controller.active_patron is None

    def test_manual_logout_ends_session_and_prevents_circulation(self):
        controller = KioskController()
        controller.active_patron = {
            "patron_id": "NFSU2024001",
            "name": "Aarav Sharma",
            "charged_count": 0,
            "charged_items": [],
            "available_limit": 2,
            "limit_reached": False
        }
        controller.rfid_tag_registry["BK-TOX-501"] = "ARMED"

        logout_res = controller.end_session(reason="MANUAL_LOGOUT")
        assert logout_res["success"] is True
        assert controller.active_patron is None

        # Immediate checkout attempt must be blocked
        checkout_res = controller.checkout_item("BK-TOX-501")
        assert checkout_res["success"] is False
        assert checkout_res["error_code"] == "FAILED_BEFORE_SEND"
        assert controller.rfid_tag_registry["BK-TOX-501"] == "ARMED"

    def test_circulation_activity_refreshes_timeout(self):
        controller = KioskController(session_timeout_seconds=60)
        controller.active_patron = {
            "patron_id": "NFSU2024001",
            "name": "Aarav Sharma",
            "charged_count": 0,
            "charged_items": [],
            "available_limit": 2,
            "limit_reached": False
        }
        controller.rfid_tag_registry["BK-TOX-501"] = "ARMED"

        # Set activity to 40 seconds ago (valid, but approaching 60s limit)
        stale_time = datetime.now() - timedelta(seconds=40)
        controller.last_activity_at = stale_time

        # Checkout book
        res = controller.checkout_item("BK-TOX-501")
        assert res["success"] is True

        # Activity timestamp must refresh to current time
        assert controller.last_activity_at > stale_time
        # Session must remain active
        assert controller.check_session_expiry() is False
        assert controller.active_patron is not None

    def test_session_expiry_check_when_no_active_patron_returns_false(self):
        controller = KioskController()
        controller.active_patron = None
        assert controller.check_session_expiry() is False

    def test_end_session_is_idempotent(self):
        controller = KioskController()
        controller.active_patron = {
            "patron_id": "NFSU2024001",
            "name": "Aarav Sharma",
            "charged_count": 0,
            "charged_items": []
        }
        res1 = controller.end_session()
        assert res1["success"] is True
        assert controller.active_patron is None

        # Second call when already logged out must also succeed safely
        res2 = controller.end_session()
        assert res2["success"] is True
        assert controller.active_patron is None

    def test_checkin_with_active_patron_decrements_charged_items(self):
        controller = KioskController()
        controller.active_patron = {
            "patron_id": "NFSU2024002",
            "name": "Priya Nair",
            "charged_count": 1,
            "charged_items": ["BK-FOR-101"],
            "available_limit": 1,
            "limit_reached": False
        }
        controller.rfid_tag_registry["BK-FOR-101"] = "DISARMED"

        res = controller.checkin_item("BK-FOR-101")
        assert res["success"] is True
        assert controller.rfid_tag_registry["BK-FOR-101"] == "ARMED"
        assert controller.active_patron["charged_count"] == 0
        assert "BK-FOR-101" not in controller.active_patron["charged_items"]
        assert controller.active_patron["available_limit"] == 2
        assert controller.active_patron["limit_reached"] is False
