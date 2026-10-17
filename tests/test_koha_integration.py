"""
Unit and Integration Tests for Koha 26 SIP2 Integration and Connection Testing.
Covers:
- Configuration validation (missing IP, port, user, pass; invalid port format).
- Mode gating (MOCK mode as default; MOCK mode skipping live socket; LIVE mode enforcement).
- Safety at startup (no automatic connection attempt in LIVE mode).
- Controlled connection testing (93/94 login handshake via mocked sockets).
- Error handling: connection refused, timeout, host resolution error, encoding error, login rejection (940).
- Sequence number validation on login (AY matching vs mismatch).
- Zero secret leakage: asserts password is never present in stdout, stderr, logs, or return payloads.
- REST API endpoint: POST /api/koha/test-connection (MOCK skip vs LIVE response).
"""

import os
import sys
import socket
import logging
from unittest.mock import MagicMock, patch
import pytest

from sip2.client import KohaSIP2Client
from sip2.protocol import format_message
from kiosk_engine import KioskController, kiosk_controller
from test_koha_connection import run_connection_test
from main import app
from tests.test_api_routes import TestClient


class TestKohaConfigurationValidation:
    def test_mock_mode_default_when_unset(self, monkeypatch):
        """When KOHA_MODE is unset or empty, KioskController defaults to MOCK mode."""
        monkeypatch.delenv("KOHA_MODE", raising=False)
        controller = KioskController()
        assert controller.mode == "MOCK"
        assert controller.sip_client.is_mock_mode is True
        assert controller.sip_client.is_connected is True

    def test_mock_mode_explicit(self, monkeypatch):
        """When KOHA_MODE=mock, controller starts in MOCK mode."""
        monkeypatch.setenv("KOHA_MODE", "mock")
        controller = KioskController()
        assert controller.mode == "MOCK"
        assert controller.sip_client.is_mock_mode is True

    def test_live_mode_missing_config_raises_value_error(self, monkeypatch):
        """When KOHA_MODE=live but required env vars are missing, raises ValueError."""
        monkeypatch.setenv("KOHA_MODE", "live")
        monkeypatch.delenv("KOHA_SERVER_IP", raising=False)
        monkeypatch.delenv("KOHA_SERVER_PORT", raising=False)
        monkeypatch.delenv("KOHA_USER", raising=False)
        monkeypatch.delenv("KOHA_PASS", raising=False)
        monkeypatch.delenv("SIP2_USERNAME", raising=False)
        monkeypatch.delenv("SIP2_PASSWORD", raising=False)

        with pytest.raises(ValueError) as excinfo:
            KioskController()
        err = str(excinfo.value)
        assert "LIVE mode is active, but required environment variables are missing" in err
        assert "KOHA_SERVER_IP" in err
        assert "KOHA_SERVER_PORT" in err

    def test_live_mode_invalid_port_raises_value_error(self, monkeypatch):
        """When KOHA_SERVER_PORT is non-numeric in LIVE mode, raises ValueError."""
        monkeypatch.setenv("KOHA_MODE", "live")
        monkeypatch.setenv("KOHA_SERVER_IP", "127.0.0.1")
        monkeypatch.setenv("KOHA_SERVER_PORT", "not_a_number")
        monkeypatch.setenv("KOHA_USER", "test_user")
        monkeypatch.setenv("KOHA_PASS", "test_pass")

        with pytest.raises(ValueError) as excinfo:
            KioskController()
        assert "KOHA_SERVER_PORT must be an integer" in str(excinfo.value)

    def test_connection_test_missing_config_reports_error(self, monkeypatch):
        """run_connection_test() returns pre-flight error when required vars are missing."""
        monkeypatch.delenv("KOHA_SERVER_IP", raising=False)
        monkeypatch.delenv("KOHA_SERVER_PORT", raising=False)
        monkeypatch.delenv("KOHA_USER", raising=False)
        monkeypatch.delenv("KOHA_PASS", raising=False)
        monkeypatch.delenv("SIP2_USERNAME", raising=False)
        monkeypatch.delenv("SIP2_PASSWORD", raising=False)

        res = run_connection_test(force_live=True, env_file=None, print_output=False)
        assert res["success"] is False
        assert res["tested"] is False
        assert "KOHA_SERVER_IP" in res["missing_fields"]
        assert "KOHA_USER" in res["missing_fields"]

    def test_connection_test_invalid_port_reports_error(self, monkeypatch):
        """run_connection_test() handles non-numeric or out-of-range port cleanly."""
        res = run_connection_test(
            force_live=True,
            host="127.0.0.1",
            port=999999,  # Out of range
            username="u",
            password="p",
            env_file=None,
            print_output=False
        )
        assert res["success"] is False
        assert res["tested"] is False
        assert "KOHA_SERVER_PORT must be an integer" in res["message"]


class TestModeGatingAndStartupSafety:
    def test_mock_mode_skips_live_connection_test(self, monkeypatch):
        """In MOCK mode, run_connection_test() does not perform any live socket calls."""
        monkeypatch.setenv("KOHA_MODE", "mock")
        res = run_connection_test(env_file=None, print_output=False)
        assert res["success"] is False
        assert res["mode"] == "MOCK"
        assert res["tested"] is False
        assert "Connection test skipped" in res["message"]

    def test_live_mode_no_auto_connect_at_startup(self, monkeypatch):
        """In LIVE mode, KioskController does NOT connect automatically at initialization."""
        monkeypatch.setenv("KOHA_MODE", "live")
        monkeypatch.setenv("KOHA_SERVER_IP", "127.0.0.1")
        monkeypatch.setenv("KOHA_SERVER_PORT", "6001")
        monkeypatch.setenv("KOHA_USER", "test_user")
        monkeypatch.setenv("KOHA_PASS", "test_pass")

        # Socket.connect should NOT be called during KioskController.__init__
        with patch("sip2.client.socket.socket") as mock_sock_cls:
            controller = KioskController()
            assert controller.mode == "LIVE"
            assert controller.sip_client.is_connected is False
            # Verify socket constructor was not called for live connection
            mock_sock_cls.return_value.connect.assert_not_called()


class TestKohaConnectionHandshake:
    def test_successful_93_94_login_handshake(self):
        """When Koha returns valid 941 response, connection test succeeds and measures latency."""
        mock_sock = MagicMock()
        # Valid 941 response with sequence AY0 and valid checksum
        resp_bytes = format_message("94120261016    1200002.00AONFSU|AAtest_user|", seq_num=0).encode("utf-8")
        mock_sock.recv.side_effect = [resp_bytes]

        with patch("sip2.client.socket.socket", return_value=mock_sock):
            res = run_connection_test(
                force_live=True,
                host="127.0.0.1",
                port=6001,
                username="test_user",
                password="secret_password_123",
                location_code="NFSU",
                env_file=None,
                print_output=False
            )

        assert res["success"] is True
        assert res["mode"] == "LIVE"
        assert res["connected"] is True
        assert res["server_ip"] == "127.0.0.1"
        assert res["server_port"] == 6001
        assert res["username"] == "test_user"
        assert res["latency_ms"] >= 0.0
        assert "Successfully connected and authenticated" in res["message"]
        # Ensure password is NEVER returned in the result dictionary
        assert "password" not in res
        assert "secret_password_123" not in str(res)

    def test_login_rejected_by_koha_940(self):
        """When Koha returns 940 response, connection test fails gracefully without secrets."""
        mock_sock = MagicMock()
        # 940 Login Rejected response with sequence AY0 and valid checksum
        resp_bytes = format_message("94020261016    1200002.00AONFSU|AAtest_user|", seq_num=0).encode("utf-8")
        mock_sock.recv.side_effect = [resp_bytes]

        with patch("sip2.client.socket.socket", return_value=mock_sock):
            res = run_connection_test(
                force_live=True,
                host="127.0.0.1",
                port=6001,
                username="test_user",
                password="invalid_password",
                location_code="NFSU",
                env_file=None,
                print_output=False
            )

        assert res["success"] is False
        assert res["mode"] == "LIVE"
        assert res["connected"] is False
        assert "Login rejected by Koha ILMS" in res["message"]
        assert "invalid_password" not in str(res)

    def test_connection_refused_error_handling(self):
        """When Koha daemon is offline / port blocked, ConnectionRefusedError is handled safely."""
        with patch("sip2.client.socket.socket") as mock_sock_cls:
            mock_sock = MagicMock()
            mock_sock.connect.side_effect = ConnectionRefusedError("Connection refused")
            mock_sock_cls.return_value = mock_sock

            res = run_connection_test(
                force_live=True,
                host="127.0.0.1",
                port=6001,
                username="test_user",
                password="some_password",
                env_file=None,
                print_output=False
            )

        assert res["success"] is False
        assert res["connected"] is False
        assert "Connection refused by Koha server" in res["message"]
        assert "some_password" not in str(res)

    def test_connection_timeout_error_handling(self):
        """When socket times out connecting to Koha, TimeoutError is handled safely."""
        with patch("sip2.client.socket.socket") as mock_sock_cls:
            mock_sock = MagicMock()
            mock_sock.connect.side_effect = socket.timeout("Timed out")
            mock_sock_cls.return_value = mock_sock

            res = run_connection_test(
                force_live=True,
                host="192.168.1.99",
                port=6001,
                username="test_user",
                password="some_password",
                timeout=2.0,
                env_file=None,
                print_output=False
            )

        assert res["success"] is False
        assert res["connected"] is False
        assert "Connection timed out" in res["message"]
        assert "some_password" not in str(res)

    def test_host_resolution_error_handling(self):
        """When hostname cannot be resolved, socket.gaierror is handled cleanly."""
        with patch("sip2.client.socket.socket") as mock_sock_cls:
            mock_sock = MagicMock()
            mock_sock.connect.side_effect = socket.gaierror("getaddrinfo failed")
            mock_sock_cls.return_value = mock_sock

            res = run_connection_test(
                force_live=True,
                host="non_existent_koha_host.invalid",
                port=6001,
                username="test_user",
                password="some_password",
                env_file=None,
                print_output=False
            )

        assert res["success"] is False
        assert res["connected"] is False
        assert "Could not resolve host address" in res["message"]
        assert "some_password" not in str(res)

    def test_login_sequence_checking_validation(self):
        """When check_sequence=True, sequence number mismatches in 94 response are caught."""
        mock_sock = MagicMock()
        # Request sent seq 0, but response returns AY5 with valid checksum
        resp_bytes = format_message("94120261016    1200002.00AONFSU|AAtest_user|", seq_num=5).encode("utf-8")
        mock_sock.recv.side_effect = [resp_bytes]

        with patch("sip2.client.socket.socket", return_value=mock_sock):
            res = run_connection_test(
                force_live=True,
                host="127.0.0.1",
                port=6001,
                username="test_user",
                password="some_password",
                check_sequence=True,
                env_file=None,
                print_output=False
            )

        assert res["success"] is False
        assert "Login sequence mismatch" in res["message"]

    def test_login_encoding_error_handling(self):
        """When encoding is ascii but credentials have non-ascii chars, pre-send fails cleanly."""
        client = KohaSIP2Client(
            mode="LIVE",
            host="127.0.0.1",
            port=6001,
            username="patron_ümlaut",
            password="pwd",
            encoding="ascii"
        )
        with patch("sip2.client.socket.socket"):
            success = client.connect()
            assert success is False
            assert "Login encoding error with 'ascii'" in client.last_error


class TestZeroSecretLeakage:
    def test_password_never_printed_in_stdout_or_logs(self, capsys, monkeypatch):
        """Verifies that password string never appears in stdout, stderr, or log messages."""
        secret_token = "SUPER_SECRET_NFSU_PASS_xyz99!"
        mock_sock = MagicMock()
        mock_sock.connect.side_effect = ConnectionRefusedError("Connection refused")

        with patch("sip2.client.socket.socket", return_value=mock_sock):
            run_connection_test(
                force_live=True,
                host="127.0.0.1",
                port=6001,
                username="rfid_kiosk_admin",
                password=secret_token,
                env_file=None,
                print_output=True
            )

        captured = capsys.readouterr()
        # Assert secret token is ABSENT from stdout and stderr
        assert secret_token not in captured.out
        assert secret_token not in captured.err
        # Assert username is displayed, but password is masked
        assert "rfid_kiosk_admin" in captured.out
        assert "[PROTECTED - NOT DISPLAYED]" in captured.out


class TestKohaConnectionApiRoute:
    def test_api_test_connection_in_mock_mode(self, monkeypatch):
        """In MOCK mode, POST /api/koha/test-connection returns HTTP 200 with tested=False."""
        monkeypatch.setenv("KOHA_MODE", "mock")
        # Ensure controller is in MOCK mode
        kiosk_controller.mode = "MOCK"
        kiosk_controller.sip_client = KohaSIP2Client(mode="MOCK")
        kiosk_controller.sip_client.connect()

        client = TestClient(app)
        resp = client.request("POST", "/api/koha/test-connection")
        assert resp.status_code == 200
        data = resp.json()
        assert data["mode"] == "MOCK"
        assert data["tested"] is False
        assert "Kiosk is currently configured in MOCK mode" in data["message"]

    def test_api_test_connection_in_live_mode_success(self, monkeypatch):
        """In LIVE mode, POST /api/koha/test-connection returns HTTP 200 on handshake success."""
        monkeypatch.setattr(kiosk_controller, "test_live_connection", lambda: {
            "success": True,
            "mode": "LIVE",
            "server_ip": "127.0.0.1",
            "server_port": 6001,
            "username": "test_kiosk",
            "location_code": "NFSU",
            "encoding": "utf-8",
            "check_sequence": False,
            "latency_ms": 15.2,
            "message": "Successfully connected and authenticated to Koha SIP2 server at 127.0.0.1:6001.",
            "connected": True,
        })

        client = TestClient(app)
        resp = client.request("POST", "/api/koha/test-connection")
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["mode"] == "LIVE"
        assert data["connected"] is True
        assert data["server_ip"] == "127.0.0.1"

    def test_api_test_connection_in_live_mode_failure(self, monkeypatch):
        """In LIVE mode, POST /api/koha/test-connection returns HTTP 503 on connection failure."""
        monkeypatch.setattr(kiosk_controller, "test_live_connection", lambda: {
            "success": False,
            "mode": "LIVE",
            "server_ip": "127.0.0.1",
            "server_port": 6001,
            "username": "test_kiosk",
            "location_code": "NFSU",
            "encoding": "utf-8",
            "check_sequence": False,
            "latency_ms": 2.5,
            "message": "Koha SIP2 connection test failed: Connection refused by Koha server at 127.0.0.1:6001.",
            "error_detail": "Connection refused",
            "connected": False,
        })

        client = TestClient(app)
        resp = client.request("POST", "/api/koha/test-connection")
        assert resp.status_code == 503
        data = resp.json()
        assert data["success"] is False
        assert data["mode"] == "LIVE"
        assert data["connected"] is False
        assert "Connection refused" in data["message"]

        # Restore MOCK mode after test
        kiosk_controller.mode = "MOCK"
        kiosk_controller.sip_client = KohaSIP2Client(mode="MOCK")
        kiosk_controller.sip_client.connect()


class TestTerminalPasswordAndTimeoutConfiguration:
    def test_client_respects_terminal_password_and_timeout_env(self, monkeypatch):
        """KohaSIP2Client correctly picks up KOHA_TERMINAL_PASSWORD and SIP2_SOCKET_TIMEOUT."""
        monkeypatch.setenv("KOHA_TERMINAL_PASSWORD", "SecretTerm123")
        monkeypatch.setenv("SIP2_SOCKET_TIMEOUT", "7.5")

        client = KohaSIP2Client(mode="MOCK")
        assert client.terminal_password == "SecretTerm123"
        assert client.timeout == 7.5

        status = client.get_status()
        assert status["has_terminal_password"] is True
        assert status["timeout_seconds"] == 7.5
        # Ensure password itself is not in status
        assert "SecretTerm123" not in str(status)

    def test_client_circulation_calls_pass_terminal_pwd_to_protocol(self):
        """Circulation methods pass terminal_pwd to protocol builders."""
        client = KohaSIP2Client(mode="MOCK", terminal_password="TERM_PWD_999")
        assert client.terminal_password == "TERM_PWD_999"

        with patch.object(client, "send_and_receive", return_value="121NNN20261016    120000AONFSU|AA123|AB456|") as mock_sr:
            client.checkout_item("123", "456")
            call_arg = mock_sr.call_args[0][0]
            assert "ACTERM_PWD_999" in call_arg

        with patch.object(client, "send_and_receive", return_value="101YNN20261016    120000APNFSU|AONFSU|AB456|") as mock_sr:
            client.checkin_item("456")
            call_arg = mock_sr.call_args[0][0]
            assert "ACTERM_PWD_999" in call_arg

        with patch.object(client, "send_and_receive", return_value="301NNN20261016    120000AONFSU|AA123|AB456|") as mock_sr:
            client.renew_item("123", "456")
            call_arg = mock_sr.call_args[0][0]
            assert "ACTERM_PWD_999" in call_arg

    def test_run_connection_test_cli_terminal_password_protected(self, capsys):
        """run_connection_test masks terminal password and displays protected status."""
        result = run_connection_test(
            force_live=True,
            host="127.0.0.1",
            port=6001,
            username="rfid_test",
            password="test_password_xyz",
            terminal_password="super_secret_term",
            print_output=True,
        )
        captured = capsys.readouterr().out
        assert "super_secret_term" not in captured
        assert "[PROTECTED - CONFIGURED]" in captured

