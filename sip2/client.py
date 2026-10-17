"""
SIP2 Client Manager for Koha 26 at NFSU Library.
Strictly separates LIVE Koha TCP communication from MOCK simulation.
Features:
- Stream framing accumulator supporting fragmented packets and multiple messages in a single read.
- Granular error distinction: SIP2SendError vs SIP2ReceiveError vs SIP2ProtocolError.
- Sequence number tracking (0-9).
- Checksum enforcement on incoming responses.
- Safe connection lifecycle management without credentials leakage.
"""

import os
import socket
import logging
from typing import Dict, Optional, Tuple
from sip2.protocol import (
    SIP2MessageBuilder,
    SIP2ResponseParser,
    parse_fields,
    verify_checksum,
)
from sip2.mock_server import MockKohaSIP2

logger = logging.getLogger("sip2_client")
logging.basicConfig(level=logging.INFO)


class SIP2ConnectionError(Exception):
    """Base class for SIP2 communication failures."""
    pass


class SIP2PreSendError(SIP2ConnectionError):
    """Failure occurred before any network transmission was attempted (definitively FAILED_BEFORE_SEND)."""
    pass


class SIP2SendError(SIP2PreSendError):
    """Pre-send failure where request was definitively NOT transmitted."""
    pass


class SIP2TransmissionError(SIP2ConnectionError):
    """
    Failure occurred during transmission (e.g. socket.sendall exception).
    Some or all bytes may have reached the server, so outcome is UNKNOWN_AFTER_SEND.
    """
    pass


class SIP2ReceiveError(SIP2TransmissionError):
    """Failure occurred after transmission while waiting for response (UNKNOWN_AFTER_SEND)."""
    pass


class SIP2ProtocolError(SIP2TransmissionError):
    """Response was received but failed checksum, was malformed, or failed sequence validation (UNKNOWN_AFTER_SEND)."""
    pass


class KohaSIP2Client:
    def __init__(
        self,
        host: Optional[str] = None,
        port: Optional[int] = None,
        username: Optional[str] = None,
        password: Optional[str] = None,
        location_code: str = "NFSU",
        terminal_password: Optional[str] = None,
        timeout: Optional[float] = None,
        mode: str = "LIVE",
        encoding: Optional[str] = None,
        check_sequence: Optional[bool] = None,
    ):
        self.mode = mode.upper().strip()
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.location_code = location_code
        self.terminal_password = (
            terminal_password
            if terminal_password is not None
            else os.getenv("KOHA_TERMINAL_PASSWORD", "").strip()
        )

        if timeout is None:
            timeout_env = os.getenv("SIP2_SOCKET_TIMEOUT", "4.0").strip()
            try:
                self.timeout = float(timeout_env)
            except ValueError:
                self.timeout = 4.0
        else:
            self.timeout = float(timeout)

        # Explicit character encoding (default: utf-8). Must match Koha SIPconfig.xml encoding.
        if encoding is None:
            self.encoding = os.getenv("SIP2_ENCODING", "utf-8").strip()
        else:
            self.encoding = encoding.strip()

        # Sequence checking configuration (default: False, matching standard Koha sequence=no default)
        if check_sequence is None:
            seq_env = os.getenv("SIP2_CHECK_SEQUENCE", "false").strip().lower()
            self.check_sequence = seq_env in ("true", "1", "yes")
        else:
            self.check_sequence = check_sequence
        
        self.sock: Optional[socket.socket] = None
        self.is_connected = False
        self.is_mock_mode = (self.mode == "MOCK")
        self.mock_server = MockKohaSIP2() if self.is_mock_mode else None
        self.last_error = ""
        self._rx_bytes = b""
        self._seq_num = 0

        # Validate configuration for LIVE mode
        if self.mode == "LIVE":
            missing = []
            if not self.host: missing.append("KOHA_SERVER_IP")
            if not self.port: missing.append("KOHA_SERVER_PORT")
            if not self.username: missing.append("KOHA_USER")
            if not self.password: missing.append("KOHA_PASS")
            if missing:
                raise ValueError(
                    f"LIVE mode requires environment variables: {', '.join(missing)}. "
                    "Provide valid credentials or set KOHA_MODE=mock for offline simulation."
                )

    @property
    def _rx_buffer(self) -> str:
        """Backward-compatible string view of the receive byte buffer."""
        return self._rx_bytes.decode(self.encoding, errors="replace")

    @_rx_buffer.setter
    def _rx_buffer(self, val):
        if isinstance(val, bytes):
            self._rx_bytes = val
        elif isinstance(val, str):
            self._rx_bytes = val.encode(self.encoding, errors="replace")
        else:
            self._rx_bytes = b""

    def _next_seq(self) -> int:
        """Returns next sequence number in range 0..9."""
        curr = self._seq_num
        self._seq_num = (self._seq_num + 1) % 10
        return curr

    def connect(self) -> bool:
        """
        Connects to Koha SIP2 server in LIVE mode, or initializes MOCK mode.
        NEVER silently switches to mock mode if live connection fails.
        """
        if self.is_mock_mode:
            self.is_connected = True
            logger.info("SIP2 Client running in explicit MOCK / SIMULATION Mode.")
            return True

        if self.sock:
            self._close_socket()
        self._rx_bytes = b""
        try:
            logger.info(f"Attempting TCP connection to Live Koha SIP2 server at {self.host}:{self.port}...")
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.sock.settimeout(self.timeout)
            self.sock.connect((self.host, self.port))
            
            # Send 93 Login Handshake with sequence number
            seq = self._next_seq()
            login_req = SIP2MessageBuilder.login_message(
                self.username, self.password, self.location_code, seq_num=seq
            )
            try:
                encoded_login = login_req.encode(self.encoding)
            except UnicodeEncodeError as e:
                self.is_connected = False
                self.last_error = f"Login encoding error with '{self.encoding}': {e}"
                logger.error(self.last_error)
                self._close_socket()
                return False

            self.sock.sendall(encoded_login)
            
            # Read complete framed response
            raw_resp = self._read_framed_response()
            parsed = SIP2ResponseParser.parse_login_response(raw_resp)
            
            if parsed.get("ok"):
                # If sequence checking is enabled, verify login sequence number
                if self.check_sequence:
                    fields = parsed.get("fields", {})
                    resp_ay = fields.get("AY")
                    if resp_ay is None or resp_ay != str(seq):
                        self.is_connected = False
                        self.last_error = f"Login sequence mismatch: expected AY{seq}, got AY{resp_ay}"
                        logger.error(self.last_error)
                        self._close_socket()
                        return False

                self.is_connected = True
                self.last_error = ""
                logger.info(f"Successfully authenticated to Live Koha 26 SIP2 server at {self.host}:{self.port} as user '{self.username}'.")
                return True
            else:
                self.is_connected = False
                err_detail = parsed.get("error") or "Login rejected by Koha ILMS (Invalid credentials or unauthorized terminal)."
                self.last_error = err_detail
                logger.error(f"Live Koha SIP2 authentication failed: {err_detail}")
                self._close_socket()
                return False

        except (socket.timeout, TimeoutError):
            self.is_connected = False
            self.last_error = f"Connection timed out ({self.timeout}s) reaching Koha server at {self.host}:{self.port}"
            logger.error(self.last_error)
            self._close_socket()
            return False
        except ConnectionRefusedError:
            self.is_connected = False
            self.last_error = f"Connection refused by Koha server at {self.host}:{self.port}. Verify that Koha SIP2 service is running."
            logger.error(self.last_error)
            self._close_socket()
            return False
        except socket.gaierror:
            self.is_connected = False
            self.last_error = f"Could not resolve host address '{self.host}'"
            logger.error(self.last_error)
            self._close_socket()
            return False
        except Exception as e:
            self.is_connected = False
            self.last_error = f"Koha server connection failed: {type(e).__name__}"
            logger.error(f"Could not connect to live Koha SIP2 server at {self.host}:{self.port} ({type(e).__name__}). LIVE mode will NOT fall back to mock.")
            self._close_socket()
            return False

    def test_connection(self) -> Dict:
        """
        Explicitly tests connection and SIP2 93/94 login handshake to Koha.
        Measures round-trip latency.
        Returns detailed diagnostics without exposing passwords or sensitive credentials.
        """
        if self.is_mock_mode:
            return {
                "success": True,
                "mode": "MOCK",
                "server_ip": "127.0.0.1 (Local Mock)",
                "server_port": 6001,
                "username": self.username or "mock_user",
                "location_code": self.location_code,
                "encoding": self.encoding,
                "check_sequence": self.check_sequence,
                "latency_ms": 0.5,
                "message": "SIP2 client is running in explicit MOCK / SIMULATION mode (Local in-memory).",
                "connected": True,
            }

        import time
        start_t = time.perf_counter()
        connected = self.connect()
        latency_ms = round((time.perf_counter() - start_t) * 1000, 2)

        if connected:
            return {
                "success": True,
                "mode": "LIVE",
                "server_ip": self.host,
                "server_port": self.port,
                "username": self.username,
                "location_code": self.location_code,
                "encoding": self.encoding,
                "check_sequence": self.check_sequence,
                "latency_ms": latency_ms,
                "message": f"Successfully connected and authenticated to Koha SIP2 server at {self.host}:{self.port}.",
                "connected": True,
            }
        else:
            return {
                "success": False,
                "mode": "LIVE",
                "server_ip": self.host,
                "server_port": self.port,
                "username": self.username,
                "location_code": self.location_code,
                "encoding": self.encoding,
                "check_sequence": self.check_sequence,
                "latency_ms": latency_ms,
                "message": f"Koha SIP2 connection test failed: {self.last_error}",
                "error_detail": self.last_error,
                "connected": False,
            }

    def _close_socket(self):
        """Safely closes socket and resets stream buffer."""
        if self.sock:
            try:
                self.sock.close()
            except Exception:
                pass
            self.sock = None
        self._rx_bytes = b""

    def _read_framed_response(self) -> str:
        """
        Reads stream from socket until terminating carriage return character b'\\r'.
        Accurately handles fragmented TCP chunks in byte buffer and decodes without silent loss.
        """
        if not self.sock:
            raise SIP2ReceiveError("Socket is not open.")

        while b"\r" not in self._rx_bytes:
            try:
                chunk = self.sock.recv(4096)
                if not chunk:
                    raise SIP2ReceiveError("Connection closed unexpectedly by Koha server while waiting for response.")
                self._rx_bytes += chunk
            except socket.timeout:
                raise SIP2ReceiveError(f"Socket timed out ({self.timeout}s) waiting for SIP2 response from Koha.")
            except Exception as e:
                if isinstance(e, SIP2ReceiveError):
                    raise
                raise SIP2ReceiveError(f"Socket read error: {type(e).__name__} - {e}")

        # Split at the first carriage return
        msg_bytes, self._rx_bytes = self._rx_bytes.split(b"\r", 1)

        # Strict decoding without errors='ignore' to prevent hiding corrupted messages
        try:
            msg = msg_bytes.decode(self.encoding)
        except UnicodeDecodeError as e:
            raise SIP2ProtocolError(
                f"Failed to decode SIP2 response using configured encoding '{self.encoding}': {e}. "
                "Corrupted or incompatible byte stream received from Koha."
            )
        return msg.strip("\n")

    def send_and_receive(self, message: str, expected_seq: Optional[int] = None) -> str:
        """
        Sends raw message to Koha (LIVE) or Mock server.
        In LIVE mode:
        - Socket errors or encoding failures before transmission raise SIP2PreSendError (FAILED_BEFORE_SEND).
        - Socket errors during transmission (socket.sendall) raise SIP2TransmissionError (UNKNOWN_AFTER_SEND)
          because socket.sendall() is not atomic and partial bytes may have reached Koha.
        - Socket errors or timeouts while waiting for response raise SIP2ReceiveError (UNKNOWN_AFTER_SEND).
        - Malformed responses, checksum failures, or sequence mismatches raise SIP2ProtocolError (UNKNOWN_AFTER_SEND).
        - NEVER returns mock success in LIVE mode!
        """
        if self.is_mock_mode:
            raw = self.mock_server.handle_raw_message(message)
            if self.check_sequence and expected_seq is not None:
                fields = parse_fields(raw)
                resp_ay = fields.get("AY")
                if resp_ay is None:
                    raise SIP2ProtocolError(
                        f"SIP2 sequence validation enabled, but mock response omitted AY tag (expected AY{expected_seq})."
                    )
                if resp_ay != str(expected_seq):
                    raise SIP2ProtocolError(
                        f"SIP2 sequence number mismatch: expected AY{expected_seq}, but mock returned AY{resp_ay}."
                    )
            return raw

        if not self.is_connected or not self.sock:
            raise SIP2PreSendError(
                f"Koha server is offline or not connected ({self.last_error or 'Disconnected'}). "
                "Request was not transmitted."
            )

        # 1. Encode message before transmission attempt
        try:
            encoded_bytes = message.encode(self.encoding)
        except UnicodeEncodeError as e:
            raise SIP2PreSendError(
                f"Failed to encode SIP2 message with configured encoding '{self.encoding}': {e}. "
                "Request was not transmitted."
            )

        # 2. Transmit message
        # IMPORTANT: socket.sendall() exception does NOT prove zero bytes reached the server.
        # Partial transmission may have occurred, so any exception during send is UNKNOWN_AFTER_SEND.
        try:
            self.sock.sendall(encoded_bytes)
        except Exception as e:
            self.is_connected = False
            self.last_error = f"Socket send failure: {type(e).__name__} - {e}"
            self._close_socket()
            raise SIP2TransmissionError(
                f"Network failure during message transmission ({type(e).__name__}: {e}). "
                "Some or all bytes may have reached Koha ILMS. Transaction outcome is UNKNOWN."
            )

        # 3. Receive response (Message was sent, so failures now become UNKNOWN_AFTER_SEND)
        try:
            data = self._read_framed_response()
            if not data:
                raise SIP2ReceiveError("Received empty response from Koha SIP2 server.")
        except (SIP2ReceiveError, SIP2ProtocolError) as e:
            self.is_connected = False
            self.last_error = str(e)
            self._close_socket()
            raise
        except Exception as e:
            self.is_connected = False
            self.last_error = f"Socket receive error: {type(e).__name__}"
            self._close_socket()
            raise SIP2ReceiveError(f"Failed to receive response from Koha after transmission: {type(e).__name__}")

        # 4. Validate sequence number (if enabled)
        if self.check_sequence and expected_seq is not None:
            fields = parse_fields(data)
            resp_ay = fields.get("AY")
            if resp_ay is None:
                raise SIP2ProtocolError(
                    f"SIP2 sequence validation enabled, but Koha response omitted AY tag (expected AY{expected_seq})."
                )
            if resp_ay != str(expected_seq):
                raise SIP2ProtocolError(
                    f"SIP2 sequence number mismatch: expected AY{expected_seq}, but Koha returned AY{resp_ay}."
                )

        return data

    def get_patron_info(self, patron_id: str) -> Dict:
        """63 -> 64: Queries Patron details, status, and active borrowed items."""
        seq = self._next_seq()
        req = SIP2MessageBuilder.patron_info_message(
            patron_id=patron_id,
            institution_id=self.location_code,
            terminal_pwd=self.terminal_password,
            seq_num=seq,
        )
        raw = self.send_and_receive(req, expected_seq=seq)
        parsed = SIP2ResponseParser.parse_patron_info_response(raw)
        if not parsed.get("ok") and "error" in parsed:
            raise SIP2ProtocolError(f"Invalid 64 Patron Info response: {parsed['error']}")
        return parsed

    def get_item_info(self, item_id: str) -> Dict:
        """17 -> 18: Queries book/item title, author, and circulation state."""
        seq = self._next_seq()
        req = SIP2MessageBuilder.item_info_message(
            item_id=item_id,
            institution_id=self.location_code,
            terminal_pwd=self.terminal_password,
            seq_num=seq,
        )
        raw = self.send_and_receive(req, expected_seq=seq)
        parsed = SIP2ResponseParser.parse_item_info_response(raw)
        if not parsed.get("ok") and "error" in parsed:
            raise SIP2ProtocolError(f"Invalid 18 Item Info response: {parsed['error']}")
        return parsed

    def checkout_item(self, patron_id: str, item_id: str) -> Dict:
        """11 -> 12: Performs checkout and returns confirmation."""
        seq = self._next_seq()
        req = SIP2MessageBuilder.checkout_message(
            patron_id=patron_id,
            item_id=item_id,
            institution_id=self.location_code,
            terminal_pwd=self.terminal_password,
            seq_num=seq,
        )
        raw = self.send_and_receive(req, expected_seq=seq)
        parsed = SIP2ResponseParser.parse_checkout_response(raw)
        if not parsed.get("ok") and parsed.get("error_code") in ("MALFORMED_RESPONSE", "CHECKSUM_FAILED", "UNEXPECTED_MESSAGE_CODE"):
            raise SIP2ProtocolError(f"Invalid 12 Checkout response: {parsed.get('error')}")
        return parsed

    def checkin_item(self, item_id: str) -> Dict:
        """09 -> 10: Performs book return and re-arms security."""
        seq = self._next_seq()
        req = SIP2MessageBuilder.checkin_message(
            item_id=item_id,
            institution_id=self.location_code,
            terminal_pwd=self.terminal_password,
            location=self.location_code,
            seq_num=seq,
        )
        raw = self.send_and_receive(req, expected_seq=seq)
        parsed = SIP2ResponseParser.parse_checkin_response(raw)
        if not parsed.get("ok") and parsed.get("error_code") in ("MALFORMED_RESPONSE", "CHECKSUM_FAILED", "UNEXPECTED_MESSAGE_CODE"):
            raise SIP2ProtocolError(f"Invalid 10 Checkin response: {parsed.get('error')}")
        return parsed

    def renew_item(self, patron_id: str, item_id: str) -> Dict:
        """29 -> 30: Renews item for patron."""
        seq = self._next_seq()
        req = SIP2MessageBuilder.renew_message(
            patron_id=patron_id,
            item_id=item_id,
            institution_id=self.location_code,
            terminal_pwd=self.terminal_password,
            seq_num=seq,
        )
        raw = self.send_and_receive(req, expected_seq=seq)
        parsed = SIP2ResponseParser.parse_renew_response(raw)
        if not parsed.get("ok") and parsed.get("error_code") in ("MALFORMED_RESPONSE", "CHECKSUM_FAILED", "UNEXPECTED_MESSAGE_CODE"):
            raise SIP2ProtocolError(f"Invalid 30 Renew response: {parsed.get('error')}")
        return parsed

    def end_patron_session(self, patron_id: str) -> bool:
        """35 -> 36: Closes patron session cleanly."""
        try:
            seq = self._next_seq()
            req = SIP2MessageBuilder.end_session_message(
                patron_id=patron_id,
                institution_id=self.location_code,
                seq_num=seq,
            )
            self.send_and_receive(req, expected_seq=seq)
            return True
        except Exception:
            return False

    def get_status(self) -> Dict:
        """Returns connection diagnostics without leaking sensitive credentials."""
        return {
            "server_ip": self.host if self.mode == "LIVE" else "127.0.0.1 (Local Mock)",
            "server_port": self.port if self.mode == "LIVE" else 6001,
            "connected": self.is_connected,
            "mode": self.mode,
            "is_mock": self.is_mock_mode,
            "location_code": self.location_code,
            "has_terminal_password": bool(self.terminal_password),
            "timeout_seconds": self.timeout,
            "encoding": self.encoding,
            "check_sequence": self.check_sequence,
            "last_error": self.last_error,
        }
