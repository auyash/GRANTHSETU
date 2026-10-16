"""
SIP2 Client Manager for Koha 26 at NFSU Library.
Handles TCP socket communication to 117.239.182.15:6001.
Includes auto-detection, auto-reconnect, and seamless failover to Mock server.
"""

import socket
import logging
from typing import Dict, Optional, Tuple
from sip2.protocol import (
    SIP2MessageBuilder,
    SIP2ResponseParser,
    verify_checksum,
)
from sip2.mock_server import MockKohaSIP2

logger = logging.getLogger("sip2_client")
logging.basicConfig(level=logging.INFO)


class KohaSIP2Client:
    def __init__(
        self,
        host: str = "117.239.182.15",
        port: int = 6001,
        username: str = "rfid",
        password: str = "Rfid@123",
        location_code: str = "NFSU",
        timeout: float = 3.0,
        force_mock: bool = False
    ):
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.location_code = location_code
        self.timeout = timeout
        self.force_mock = force_mock
        
        self.sock: Optional[socket.socket] = None
        self.is_connected = False
        self.is_mock_mode = force_mock
        self.mock_server = MockKohaSIP2()
        self.last_error = ""

    def connect(self) -> bool:
        """Attempts connection to live Koha SIP2 server; falls back to mock if unreachable."""
        if self.force_mock:
            self.is_mock_mode = True
            self.is_connected = True
            logger.info("SIP2 Client initialized in Mock Mode (Forced).")
            return True

        try:
            logger.info(f"Attempting TCP connection to Koha SIP2 server at {self.host}:{self.port}...")
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.sock.settimeout(self.timeout)
            self.sock.connect((self.host, self.port))
            
            # Send 93 Login
            login_req = SIP2MessageBuilder.login_message(self.username, self.password, self.location_code)
            self.sock.sendall(login_req.encode("ascii", errors="ignore"))
            
            raw_resp = self.sock.recv(4096).decode("ascii", errors="ignore")
            parsed = SIP2ResponseParser.parse_login_response(raw_resp)
            
            if parsed.get("ok"):
                self.is_connected = True
                self.is_mock_mode = False
                logger.info(f"Successfully authenticated to live Koha 26 SIP2 server at {self.host}:{self.port}")
                return True
            else:
                logger.warning(f"Koha Login rejected response: {raw_resp}. Switching to resilient Mock mode.")
                self.is_mock_mode = True
                self.is_connected = True
                return True
        except Exception as e:
            self.last_error = str(e)
            logger.warning(f"Could not reach live Koha server at {self.host}:{self.port} ({e}). Running in Resilient Mock Mode.")
            self.is_mock_mode = True
            self.is_connected = True
            if self.sock:
                try:
                    self.sock.close()
                except Exception:
                    pass
                self.sock = None
            return True

    def send_and_receive(self, message: str) -> str:
        """Sends raw message to Koha (or Mock) and returns response."""
        if self.is_mock_mode or not self.sock:
            return self.mock_server.handle_raw_message(message)

        try:
            self.sock.sendall(message.encode("ascii", errors="ignore"))
            data = self.sock.recv(4096).decode("ascii", errors="ignore")
            return data
        except Exception as e:
            logger.error(f"Socket communication error with Koha: {e}. Falling back to mock response.")
            self.last_error = str(e)
            self.is_mock_mode = True
            return self.mock_server.handle_raw_message(message)

    def get_patron_info(self, patron_id: str) -> Dict:
        """63 -> 64: Queries Patron details, status, and active borrowed items."""
        req = SIP2MessageBuilder.patron_info_message(patron_id=patron_id)
        raw = self.send_and_receive(req)
        return SIP2ResponseParser.parse_patron_info_response(raw)

    def get_item_info(self, item_id: str) -> Dict:
        """17 -> 18: Queries book/item title, author, and circulation state."""
        req = SIP2MessageBuilder.item_info_message(item_id=item_id)
        raw = self.send_and_receive(req)
        return SIP2ResponseParser.parse_item_info_response(raw)

    def checkout_item(self, patron_id: str, item_id: str) -> Dict:
        """11 -> 12: Performs checkout and returns confirmation."""
        req = SIP2MessageBuilder.checkout_message(patron_id=patron_id, item_id=item_id)
        raw = self.send_and_receive(req)
        return SIP2ResponseParser.parse_checkout_response(raw)

    def checkin_item(self, item_id: str) -> Dict:
        """09 -> 10: Performs book return and re-arms security."""
        req = SIP2MessageBuilder.checkin_message(item_id=item_id)
        raw = self.send_and_receive(req)
        return SIP2ResponseParser.parse_checkin_response(raw)

    def renew_item(self, patron_id: str, item_id: str) -> Dict:
        """29 -> 30: Renews item for patron."""
        req = SIP2MessageBuilder.renew_message(patron_id=patron_id, item_id=item_id)
        raw = self.send_and_receive(req)
        return SIP2ResponseParser.parse_renew_response(raw)

    def end_patron_session(self, patron_id: str) -> bool:
        """35 -> 36: Closes patron session cleanly."""
        req = SIP2MessageBuilder.end_session_message(patron_id=patron_id)
        self.send_and_receive(req)
        return True

    def get_status(self) -> Dict:
        """Returns connection diagnostics."""
        return {
            "server_ip": self.host,
            "server_port": self.port,
            "connected": self.is_connected,
            "mode": "MOCK / SIMULATION" if self.is_mock_mode else "LIVE KOHA 26",
            "last_error": self.last_error,
        }
