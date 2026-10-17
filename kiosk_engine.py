"""
Core Kiosk Circulation Controller for NFSU Library.
Enforces:
1. Strict Operation Mode Separation: LIVE Koha mode vs explicit MOCK mode.
2. Safe Failure Semantics:
   - Differentiates FAILED_BEFORE_SEND, UNKNOWN_AFTER_SEND, and DENIED.
   - UNKNOWN transactions NEVER update confirmed counts or disarm RFID tags.
   - Never automatically retries ambiguous transactions.
3. Strict 2-Book Borrowing Limit per patron.
4. RFID EAS/AFI Security Tag Toggling (Arming & Disarming).
5. Exit Gate Security Alarm logic.
6. Inactivity session auto-logout.
"""

from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
import os
import json
import logging
from sip2.client import (
    KohaSIP2Client,
    SIP2ConnectionError,
    SIP2PreSendError,
    SIP2SendError,
    SIP2TransmissionError,
    SIP2ReceiveError,
    SIP2ProtocolError
)
from security.audit import audit_logger

logger = logging.getLogger("kiosk_engine")

SIMULATED_RFID_STATE_FILE = os.path.join(os.path.dirname(__file__), "data", "simulated_rfid_state.json")


def load_local_env():
    """Lightweight .env loader using standard library only."""
    env_path = os.path.join(os.path.dirname(__file__), ".env")
    if os.path.exists(env_path):
        try:
            with open(env_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, v = line.split("=", 1)
                        os.environ.setdefault(k.strip(), v.strip())
        except Exception as e:
            logger.warning(f"Could not load .env file: {e}")

load_local_env()


class KioskController:
    MAX_BORROW_LIMIT = 2  # University policy limit
    DEFAULT_SESSION_TIMEOUT_SECONDS = 60.0  # Server-side inactivity limit (client timer is 30s)

    def __init__(self, session_timeout_seconds: Optional[float] = None):
        raw_mode = os.getenv("KOHA_MODE", "MOCK").strip().upper()
        self.mode = "LIVE" if raw_mode == "LIVE" else "MOCK"

        sip2_enc = os.getenv("SIP2_ENCODING", "utf-8").strip()
        sip2_seq_raw = os.getenv("SIP2_CHECK_SEQUENCE", "false").strip().lower()
        sip2_check_seq = sip2_seq_raw in ("true", "1", "yes")

        timeout_raw = os.getenv("SESSION_TIMEOUT_SECONDS", "").strip()
        if session_timeout_seconds is not None:
            self.session_timeout_seconds = float(session_timeout_seconds)
        elif timeout_raw:
            try:
                self.session_timeout_seconds = float(timeout_raw)
            except ValueError:
                self.session_timeout_seconds = self.DEFAULT_SESSION_TIMEOUT_SECONDS
        else:
            self.session_timeout_seconds = self.DEFAULT_SESSION_TIMEOUT_SECONDS

        sip2_timeout_raw = os.getenv("SIP2_SOCKET_TIMEOUT", "4.0").strip()
        try:
            sip2_timeout = float(sip2_timeout_raw)
        except ValueError:
            sip2_timeout = 4.0

        koha_term_pwd = os.getenv("KOHA_TERMINAL_PASSWORD", "").strip()

        if self.mode == "MOCK":
            logger.info("Initializing Kiosk Controller in explicit MOCK mode.")
            self.sip_client = KohaSIP2Client(
                mode="MOCK",
                encoding=sip2_enc,
                check_sequence=sip2_check_seq,
                terminal_password=koha_term_pwd,
                timeout=sip2_timeout,
            )
        else:
            koha_host = os.getenv("KOHA_SERVER_IP")
            koha_port_str = os.getenv("KOHA_SERVER_PORT")
            koha_user = os.getenv("KOHA_USER") or os.getenv("SIP2_USERNAME")
            koha_pass = os.getenv("KOHA_PASS") or os.getenv("SIP2_PASSWORD")
            koha_loc = os.getenv("KOHA_LOCATION", "NFSU")

            missing_vars = []
            if not koha_host: missing_vars.append("KOHA_SERVER_IP")
            if not koha_port_str: missing_vars.append("KOHA_SERVER_PORT")
            if not koha_user: missing_vars.append("KOHA_USER (or SIP2_USERNAME)")
            if not koha_pass: missing_vars.append("KOHA_PASS (or SIP2_PASSWORD)")

            if missing_vars:
                err_msg = (
                    f"Configuration Error: LIVE mode is active, but required environment variables are missing: "
                    f"{', '.join(missing_vars)}. Configure them in .env or set KOHA_MODE=mock for simulation."
                )
                logger.error(err_msg)
                raise ValueError(err_msg)

            try:
                koha_port = int(koha_port_str)
            except ValueError:
                raise ValueError(f"KOHA_SERVER_PORT must be an integer, got: {koha_port_str}")

            self.sip_client = KohaSIP2Client(
                host=koha_host,
                port=koha_port,
                username=koha_user,
                password=koha_pass,
                location_code=koha_loc,
                terminal_password=koha_term_pwd,
                timeout=sip2_timeout,
                encoding=sip2_enc,
                check_sequence=sip2_check_seq,
                mode="LIVE"
            )

        # Initial connection: connect immediately in MOCK mode.
        # In LIVE mode, do NOT connect automatically at startup to ensure safe, explicit integration.
        if self.mode == "MOCK":
            self.sip_client.connect()
        else:
            logger.info(
                "Kiosk Controller initialized in LIVE mode. "
                "Automatic startup connection skipped for safety. "
                "Use test_live_connection() or test_koha_connection.py to establish Koha session."
            )

        # Active patron session state
        self._active_patron: Optional[Dict] = None
        self.session_started_at: Optional[datetime] = None
        self.last_activity_at: Optional[datetime] = None

        # Pre-checkout transaction tray & session history for digital slips
        self.session_tray: List[Dict[str, Any]] = []
        self.session_history: List[Dict[str, Any]] = []

        # RFID Security State Registry (EAS / AFI bits for items)
        # "ARMED" (AFI 0xC2) -> Will trigger exit gate alarm
        # "DISARMED" (AFI 0x07) -> Safe passage through exit gate
        # Note: This persistence applies solely to software simulation resilience.
        # In physical hardware deployment, EAS/AFI tag bytes are written to physical RFID chips directly.
        self.rfid_tag_registry: Dict[str, str] = self._load_simulated_rfid_registry()

    @property
    def active_patron(self) -> Optional[Dict]:
        self.check_session_expiry()
        return self._active_patron

    @active_patron.setter
    def active_patron(self, val: Optional[Dict]):
        self._active_patron = val
        if val is not None:
            now = datetime.now()
            if self.session_started_at is None:
                self.session_started_at = now
            self.last_activity_at = now
        else:
            self.session_started_at = None
            self.last_activity_at = None

    def check_session_expiry(self) -> bool:
        """
        Verifies if active patron session has exceeded the server-side inactivity timeout.
        If expired, safely ends the session, sends SIP2 35 termination, and clears state.
        Returns True if a session was expired and cleared, False otherwise.
        """
        if not self._active_patron or not self.last_activity_at:
            return False

        elapsed = (datetime.now() - self.last_activity_at).total_seconds()
        if elapsed > self.session_timeout_seconds:
            pid = self._active_patron.get("patron_id", "UNKNOWN")
            logger.info(
                f"Active patron session for {pid} expired after {elapsed:.1f}s of server-side inactivity "
                f"(timeout limit: {self.session_timeout_seconds}s)."
            )
            self.end_session(reason="INACTIVITY_TIMEOUT")
            return True
        return False

    def _load_simulated_rfid_registry(self) -> Dict[str, str]:
        """Loads persisted simulated RFID tag security states from local storage."""
        default_registry = {
            "BK-FOR-101": "DISARMED",
            "BK-CYB-201": "DISARMED",
            "BK-DIG-301": "DISARMED",
            "BK-BAL-401": "DISARMED",
            "BK-TOX-501": "ARMED",
            "BK-DNA-601": "ARMED",
        }
        if os.path.exists(SIMULATED_RFID_STATE_FILE):
            try:
                with open(SIMULATED_RFID_STATE_FILE, "r", encoding="utf-8") as f:
                    saved = json.load(f)
                    if isinstance(saved, dict):
                        default_registry.update(saved)
            except Exception as e:
                logger.warning(f"Could not load simulated RFID state file: {e}")
        return default_registry

    def _save_simulated_rfid_registry(self):
        """Persists the simulated RFID registry for software simulation resilience."""
        try:
            os.makedirs(os.path.dirname(SIMULATED_RFID_STATE_FILE), exist_ok=True)
            with open(SIMULATED_RFID_STATE_FILE, "w", encoding="utf-8") as f:
                json.dump(self.rfid_tag_registry, f, indent=2)
        except Exception as e:
            logger.warning(f"Could not persist simulated RFID state: {e}")

    def authenticate_patron(self, patron_id: str, auth_method: str = "CARD") -> Dict:
        """
        Authenticates patron via MIFARE Card, Barcode, or Face Recognition.
        Fetches live borrowing stats from Koha 26 via SIP2 message 63.
        Distinguishes FAILED_BEFORE_SEND, UNKNOWN_AFTER_SEND, and DENIED.
        """
        patron_id = patron_id.strip()
        if not patron_id:
            return {
                "success": False,
                "error_code": "FAILED_BEFORE_SEND",
                "message": "Patron ID cannot be empty."
            }

        try:
            resp = self.sip_client.get_patron_info(patron_id)
        except (SIP2PreSendError, SIP2SendError) as err:
            return {
                "success": False,
                "error_code": "FAILED_BEFORE_SEND",
                "message": f"Koha server connection failed before sending: {str(err)}. Patron verification aborted."
            }
        except SIP2TransmissionError as err:
            return {
                "success": False,
                "error_code": "UNKNOWN_AFTER_SEND",
                "message": (
                    f"Patron verification outcome is UNKNOWN due to network error ({str(err)}). "
                    "Please ask library staff to verify your circulation record in Koha."
                )
            }

        if not resp.get("ok") or not resp.get("valid_patron", True):
            return {
                "success": False,
                "error_code": "DENIED",
                "message": f"Patron identification not recognized or card is blocked: {patron_id}"
            }

        charged_count = resp.get("charged_count", len(resp.get("charged_items", [])))
        available_limit = max(0, self.MAX_BORROW_LIMIT - charged_count)
        limit_reached = charged_count >= self.MAX_BORROW_LIMIT

        self.active_patron = {
            "patron_id": patron_id,
            "name": resp.get("patron_name", "NFSU Patron"),
            "charged_count": charged_count,
            "charged_items": resp.get("charged_items", []),
            "available_limit": available_limit,
            "limit_reached": limit_reached,
            "auth_method": auth_method,
            "auth_timestamp": datetime.now().isoformat(),
        }
        self.session_started_at = datetime.now()
        self.session_tray.clear()
        self.session_history.clear()

        audit_logger.log_event(
            category="AUTHENTICATION",
            action=f"LOGIN_{auth_method}",
            details=f"Patron {patron_id} ({self.active_patron['name']}) logged in [{self.mode}]. Borrowed: {charged_count}/{self.MAX_BORROW_LIMIT} books."
        )

        return {
            "success": True,
            "patron": self.active_patron,
            "server_status": self.sip_client.get_status()
        }

    def get_item_details(self, item_barcode: str) -> Dict:
        """Looks up book information via SIP2 message 17."""
        item_barcode = item_barcode.strip()
        try:
            info = self.sip_client.get_item_info(item_barcode)
        except SIP2ConnectionError as err:
            return {
                "ok": False,
                "title": "Item Lookup Unavailable",
                "message": f"Koha server communication error: {str(err)}"
            }

        eas_state = self.rfid_tag_registry.get(item_barcode, "ARMED")
        info["eas_security_state"] = eas_state
        info["afi_byte"] = "0x07 (Disarmed)" if eas_state == "DISARMED" else "0xC2 (Armed - Active)"
        return info

    def checkout_item(self, item_barcode: str) -> Dict:
        """
        Processes checkout with rigorous safety semantics:
        - 2-Book Borrowing Limit Guard (FAILED_BEFORE_SEND)
        - Network transmission failure before Koha receives (FAILED_BEFORE_SEND)
        - Network drop / timeout after transmission (UNKNOWN_AFTER_SEND) -> Tag remains ARMED!
        - Koha rejection (DENIED) -> Tag remains ARMED!
        - Authoritative Koha 12 confirmation -> Tag is DISARMED and state updated.
        """
        if not self.active_patron:
            return {
                "success": False,
                "error_code": "FAILED_BEFORE_SEND",
                "message": "No active patron session. Please authenticate first."
            }

        patron_id = self.active_patron["patron_id"]
        item_barcode = item_barcode.strip()
        if not item_barcode:
            return {
                "success": False,
                "error_code": "FAILED_BEFORE_SEND",
                "message": "Item barcode cannot be empty."
            }

        # 1. Enforce 2-Book Limit Guard locally (FAILED_BEFORE_SEND)
        current_charged = self.active_patron["charged_count"]
        if current_charged >= self.MAX_BORROW_LIMIT:
            audit_logger.log_limit_violation(patron_id, current_charged, item_barcode)
            return {
                "success": False,
                "error_code": "FAILED_BEFORE_SEND",
                "message": (
                    f"Borrowing limit reached ({current_charged}/{self.MAX_BORROW_LIMIT} books). "
                    "University policy allows a maximum of 2 books per patron. "
                    "Please return an existing book before issuing a new one."
                ),
                "charged_count": current_charged,
                "max_limit": self.MAX_BORROW_LIMIT
            }

        # 2. Query Koha SIP2 Checkout with specific error handling
        try:
            res = self.sip_client.checkout_item(patron_id, item_barcode)
        except (SIP2PreSendError, SIP2SendError) as err:
            # FAILED_BEFORE_SEND: Request was definitively NOT transmitted
            return {
                "success": False,
                "error_code": "FAILED_BEFORE_SEND",
                "message": f"Checkout aborted before transmission: {str(err)}. Item was NOT issued.",
                "rfid_security": "REMAINS ARMED (AFI 0xC2)"
            }
        except SIP2TransmissionError as err:
            # UNKNOWN_AFTER_SEND: Transmission was attempted (socket.sendall failed or response interrupted/malformed).
            # CRITICAL SAFETY: Never auto-retry, tag remains ARMED, counts unchanged!
            return {
                "success": False,
                "error_code": "UNKNOWN_AFTER_SEND",
                "message": (
                    f"Transaction outcome is UNKNOWN ({str(err)}). "
                    "A network failure occurred during or after transmission to Koha ILMS. "
                    "Some or all bytes may have reached the server. "
                    "For security, this book has NOT been issued and remains ARMED. "
                    "Please ask library staff to reconcile your circulation record in Koha before retrying."
                ),
                "rfid_security": "REMAINS ARMED (AFI 0xC2)",
                "reconciliation_instructions": [
                    "1. DO NOT retry checkout without library staff verification.",
                    "2. Book RFID security tag remains ARMED to prevent unauthorized removal.",
                    "3. Library staff must inspect Koha Staff Client circulation history for this patron/item.",
                    "4. If transaction succeeded on Koha, perform manual RFID tag update at staff station.",
                    "5. If transaction was not recorded, retry once network connection is verified."
                ]
            }

        # 3. Check authoritative Koha confirmation
        if not res.get("ok"):
            # DENIED: Koha explicitly declined the checkout
            return {
                "success": False,
                "error_code": "DENIED",
                "message": res.get("screen_msg", "Checkout was declined by Koha ILMS."),
                "rfid_security": "REMAINS ARMED (AFI 0xC2)",
                "koha_response": res
            }

        # 4. Authoritative Success Confirmed: Update patron state and disarm RFID tag
        self.active_patron["charged_count"] += 1
        if item_barcode not in self.active_patron["charged_items"]:
            self.active_patron["charged_items"].append(item_barcode)
        self.active_patron["available_limit"] = max(0, self.MAX_BORROW_LIMIT - self.active_patron["charged_count"])
        self.active_patron["limit_reached"] = self.active_patron["charged_count"] >= self.MAX_BORROW_LIMIT

        self.rfid_tag_registry[item_barcode] = "DISARMED"
        self._save_simulated_rfid_registry()
        audit_logger.log_eas_state_change(item_barcode, "DISARMED", f"SIP2 Checkout confirmed [{self.mode}]")
        self.last_activity_at = datetime.now()

        # Record in session history for digital slip
        self.session_history.append({
            "action": "ISSUE",
            "barcode": item_barcode,
            "title": res.get("title", item_barcode),
            "due_date": res.get("due_date", "Standard Loan"),
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "mode": self.mode,
        })

        return {
            "success": True,
            "message": f"Successfully issued '{res.get('title', item_barcode)}'. Due: {res.get('due_date')}",
            "item_id": item_barcode,
            "title": res.get("title", item_barcode),
            "due_date": res.get("due_date"),
            "rfid_security": "DISARMED (AFI 0x07) - Safe to pass exit gates",
            "patron": self.active_patron,
            "mode": self.mode
        }

    def checkin_item(self, item_barcode: str) -> Dict:
        """
        Processes book return with safe failure semantics:
        - Fails safely if Koha does not confirm.
        - Re-arms RFID tag only upon authoritative Koha 10 response.
        """
        item_barcode = item_barcode.strip()
        if not item_barcode:
            return {
                "success": False,
                "error_code": "FAILED_BEFORE_SEND",
                "message": "Item barcode cannot be empty."
            }

        try:
            res = self.sip_client.checkin_item(item_barcode)
        except (SIP2PreSendError, SIP2SendError) as err:
            return {
                "success": False,
                "error_code": "FAILED_BEFORE_SEND",
                "message": f"Return aborted before transmission: {str(err)}."
            }
        except SIP2TransmissionError as err:
            return {
                "success": False,
                "error_code": "UNKNOWN_AFTER_SEND",
                "message": (
                    f"Return outcome is UNKNOWN ({str(err)}). "
                    "A network failure occurred during or after transmission to Koha ILMS. "
                    "For security, item inventory and patron counts have NOT been updated locally. "
                    "Please ask library staff to reconcile the transaction in Koha before retrying."
                ),
                "reconciliation_instructions": [
                    "1. DO NOT return book to shelves without staff verification.",
                    "2. Item inventory status has NOT been confirmed by Koha.",
                    "3. Library staff must verify return in Koha Staff Client.",
                    "4. If return succeeded on Koha, ensure tag is ARMED before shelving.",
                    "5. If return failed, retry once connection is verified."
                ]
            }

        if not res.get("ok"):
            return {
                "success": False,
                "error_code": "DENIED",
                "message": res.get("screen_msg", "Return could not be confirmed by Koha ILMS."),
                "koha_response": res
            }

        # Re-arm RFID EAS tag to ARMED (AFI 0xC2)
        self.rfid_tag_registry[item_barcode] = "ARMED"
        self._save_simulated_rfid_registry()
        audit_logger.log_eas_state_change(item_barcode, "ARMED", f"Book returned to library inventory [{self.mode}]")

        if self.active_patron:
            if item_barcode in self.active_patron["charged_items"]:
                self.active_patron["charged_items"].remove(item_barcode)
                self.active_patron["charged_count"] = max(0, self.active_patron["charged_count"] - 1)
                self.active_patron["available_limit"] = max(0, self.MAX_BORROW_LIMIT - self.active_patron["charged_count"])
                self.active_patron["limit_reached"] = False
            self.last_activity_at = datetime.now()

        # Record in session history
        self.session_history.append({
            "action": "RETURN",
            "barcode": item_barcode,
            "title": res.get("title", item_barcode),
            "due_date": "Returned to Library",
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "mode": self.mode,
        })

        return {
            "success": True,
            "message": f"'{res.get('title', item_barcode)}' returned successfully.",
            "item_id": item_barcode,
            "title": res.get("title", item_barcode),
            "rfid_security": "RE-ARMED (AFI 0xC2) - Secured in library",
            "patron": self.active_patron,
            "mode": self.mode
        }

    def renew_item(self, item_barcode: str) -> Dict:
        """Renews item via Koha SIP2 message 29 with safe failure semantics."""
        if not self.active_patron:
            return {
                "success": False,
                "error_code": "FAILED_BEFORE_SEND",
                "message": "Please authenticate patron card first."
            }

        patron_id = self.active_patron["patron_id"]
        item_barcode = item_barcode.strip()
        if not item_barcode:
            return {
                "success": False,
                "error_code": "FAILED_BEFORE_SEND",
                "message": "Item barcode cannot be empty."
            }

        try:
            res = self.sip_client.renew_item(patron_id, item_barcode)
        except (SIP2PreSendError, SIP2SendError) as err:
            return {
                "success": False,
                "error_code": "FAILED_BEFORE_SEND",
                "message": f"Renewal aborted before transmission: {str(err)}."
            }
        except SIP2TransmissionError as err:
            return {
                "success": False,
                "error_code": "UNKNOWN_AFTER_SEND",
                "message": (
                    f"Renewal outcome is UNKNOWN ({str(err)}). "
                    "A network failure occurred during or after transmission to Koha ILMS. "
                    "Please ask library staff to reconcile the renewal in Koha before retrying."
                ),
                "reconciliation_instructions": [
                    "1. DO NOT assume due date was extended.",
                    "2. Library staff must verify renewal in Koha Staff Client.",
                    "3. If renewal was rejected (limit reached or item on reserve), book must be returned."
                ]
            }

        if not res.get("ok"):
            return {
                "success": False,
                "error_code": "DENIED",
                "message": res.get("screen_msg", "Renewal could not be processed by Koha.")
            }

        self.last_activity_at = datetime.now()

        # Record in session history
        self.session_history.append({
            "action": "RENEW",
            "barcode": item_barcode,
            "title": res.get("title", item_barcode),
            "due_date": res.get("due_date", "Extended"),
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "mode": self.mode,
        })
        return {
            "success": True,
            "message": f"Renewed '{res.get('title', item_barcode)}'. New due date: {res.get('due_date')}",
            "item_id": item_barcode,
            "title": res.get("title", item_barcode),
            "due_date": res.get("due_date"),
            "mode": self.mode
        }

    def test_exit_gate(self, item_barcode: str) -> Dict:
        """
        Simulates passing through library exit RFID pedestals.
        If tag is ARMED -> Triggers Gate Security Siren!
        If tag is DISARMED -> Safe passage (No alarm).
        """
        item_barcode = item_barcode.strip()
        eas_state = self.rfid_tag_registry.get(item_barcode, "ARMED")

        if eas_state == "ARMED":
            audit_logger.log_gate_alarm(item_barcode, "Unissued / Armed Item passing through security gate")
            return {
                "alarm_triggered": True,
                "status": "ALARM_RINGING",
                "message": f"🚨 SECURITY ALARM TRIGGERED! Item {item_barcode} is ARMED (AFI 0xC2). Unauthorized removal detected!",
                "item_id": item_barcode,
                "eas_state": "ARMED",
                "audio_alert": True
            }
        else:
            return {
                "alarm_triggered": False,
                "status": "SAFE_PASSAGE",
                "message": f"✅ SAFE PASSAGE: Item {item_barcode} is DISARMED (AFI 0x07). Legitimate checkout verified.",
                "item_id": item_barcode,
                "eas_state": "DISARMED",
                "audio_alert": False
            }

    def end_session(self, reason: str = "MANUAL_LOGOUT") -> Dict:
        """Ends current patron session cleanly with SIP2 35."""
        if self._active_patron:
            pid = self._active_patron.get("patron_id", "")
            try:
                if pid:
                    self.sip_client.end_patron_session(pid)
            except Exception as e:
                logger.warning(f"Could not send SIP2 35 end session message for {pid}: {e}")
            audit_logger.log_event("SESSION", "END_SESSION", f"Session closed for patron {pid} [{self.mode}]. Reason: {reason}.")
            self._active_patron = None
            self.session_started_at = None
            self.last_activity_at = None
            self.session_tray.clear()
            self.session_history.clear()
        return {"success": True, "message": "Session terminated cleanly."}

    def test_live_connection(self) -> Dict:
        """
        Explicitly triggers a SIP2 connection and login test to Koha.
        In MOCK mode, returns status without attempting live network transmission.
        In LIVE mode, performs 93/94 handshake, updates client connection state, and measures latency.
        Zero credentials or secrets are ever leaked.
        """
        if self.mode == "MOCK":
            return {
                "success": False,
                "mode": "MOCK",
                "tested": False,
                "message": (
                    "Kiosk is currently configured in MOCK mode (offline simulation). "
                    "No live network connection was attempted. "
                    "To test a real or test Koha ILMS server, configure KOHA_MODE=live in .env."
                ),
                "koha_status": self.sip_client.get_status()
            }
        return self.sip_client.test_connection()

    def add_to_tray(self, barcode: str) -> Dict[str, Any]:
        """
        Adds an item to the active session pre-checkout tray.
        Enforces 2-book university limit before allowing addition.
        """
        if not self.active_patron:
            return {
                "success": False,
                "error_code": "FAILED_BEFORE_SEND",
                "message": "Please authenticate patron card first."
            }

        barcode = barcode.strip()
        if not barcode:
            return {
                "success": False,
                "error_code": "FAILED_BEFORE_SEND",
                "message": "Item barcode cannot be empty."
            }

        # Check duplicate in tray
        if any(item["barcode"] == barcode for item in self.session_tray):
            return {
                "success": False,
                "error_code": "DUPLICATE_TRAY_ITEM",
                "message": f"Item {barcode} is already in the transaction tray.",
                "tray": list(self.session_tray),
                "tray_count": len(self.session_tray)
            }

        # Check 2-book limit: current charged + items currently in tray >= MAX_BORROW_LIMIT
        projected_count = self.active_patron["charged_count"] + len(self.session_tray) + 1
        if projected_count > self.MAX_BORROW_LIMIT:
            audit_logger.log_limit_violation(
                self.active_patron["patron_id"],
                self.active_patron["charged_count"],
                barcode
            )
            return {
                "success": False,
                "error_code": "FAILED_BEFORE_SEND",
                "message": (
                    f"Borrowing limit reached ({self.active_patron['charged_count']}/2 books borrowed, "
                    f"{len(self.session_tray)} in tray). University policy permits maximum {self.MAX_BORROW_LIMIT} books."
                ),
                "tray": list(self.session_tray),
                "tray_count": len(self.session_tray)
            }

        # Look up item details via SIP2 17
        item_details = self.get_item_details(barcode)
        title = item_details.get("title", barcode)

        tray_item = {
            "barcode": barcode,
            "title": title,
            "due_date": "Calculated at Checkout",
            "added_at": datetime.now().isoformat(),
        }
        self.session_tray.append(tray_item)
        self.last_activity_at = datetime.now()

        available_slots = max(0, self.MAX_BORROW_LIMIT - (self.active_patron["charged_count"] + len(self.session_tray)))
        return {
            "success": True,
            "message": f"Added '{title}' to transaction tray.",
            "item": tray_item,
            "tray": list(self.session_tray),
            "tray_count": len(self.session_tray),
            "available_slots": available_slots,
            "limit_reached": available_slots == 0
        }

    def remove_from_tray(self, barcode: str) -> Dict[str, Any]:
        """Removes an item from the pre-checkout tray."""
        barcode = barcode.strip()
        initial_len = len(self.session_tray)
        self.session_tray = [item for item in self.session_tray if item["barcode"] != barcode]
        removed = len(self.session_tray) < initial_len
        self.last_activity_at = datetime.now()
        available_slots = max(0, self.MAX_BORROW_LIMIT - (self.active_patron["charged_count"] + len(self.session_tray))) if self.active_patron else 2
        return {
            "success": removed,
            "message": f"Item {barcode} removed from tray." if removed else f"Item {barcode} not found in tray.",
            "tray": list(self.session_tray),
            "tray_count": len(self.session_tray),
            "available_slots": available_slots
        }

    def clear_tray(self) -> Dict[str, Any]:
        """Clears all items from the transaction tray."""
        self.session_tray.clear()
        self.last_activity_at = datetime.now()
        return {"success": True, "message": "Transaction tray cleared.", "tray_count": 0}

    def get_tray(self) -> Dict[str, Any]:
        """Returns items in the active pre-checkout tray."""
        self.check_session_expiry()
        available = max(0, self.MAX_BORROW_LIMIT - (self.active_patron["charged_count"] + len(self.session_tray))) if self.active_patron else 2
        return {
            "tray": list(self.session_tray),
            "tray_count": len(self.session_tray),
            "available_slots": available,
            "active_patron": self.active_patron,
        }

    def checkout_tray(self) -> Dict[str, Any]:
        """Processes checkout for all items currently in tray sequentially."""
        if not self.active_patron:
            return {"success": False, "error_code": "FAILED_BEFORE_SEND", "message": "Please authenticate patron card first."}
        if not self.session_tray:
            return {"success": False, "error_code": "EMPTY_TRAY", "message": "Transaction tray is empty. Scan an item first."}

        results = []
        all_success = True
        items_to_remove = []

        for item in list(self.session_tray):
            res = self.checkout_item(item["barcode"])
            results.append(res)
            if res.get("success"):
                items_to_remove.append(item["barcode"])
            else:
                all_success = False
                break  # Halt on first failure for safety

        self.session_tray = [i for i in self.session_tray if i["barcode"] not in items_to_remove]
        self.last_activity_at = datetime.now()

        return {
            "success": all_success,
            "processed_count": len(items_to_remove),
            "remaining_tray_count": len(self.session_tray),
            "results": results,
            "patron": self.active_patron,
        }

    def generate_receipt(self, circulation_mode: str = "ISSUE") -> Dict[str, Any]:
        """Builds structured digital receipt data for current session."""
        if not self.active_patron:
            return {"success": False, "message": "No active session for receipt."}
        from services.receipt_generator import ReceiptGenerator
        receipt_data = ReceiptGenerator.build_receipt_data(
            patron=self.active_patron,
            items=self.session_history,
            circulation_mode=circulation_mode,
            koha_mode=self.mode
        )
        return {
            "success": True,
            "receipt": receipt_data
        }

    def generate_receipt_pdf(self, circulation_mode: str = "ISSUE") -> bytes:
        """Renders in-memory PDF receipt bytes for current session."""
        from services.receipt_generator import ReceiptGenerator
        receipt_data = ReceiptGenerator.build_receipt_data(
            patron=self.active_patron or {"patron_id": "GUEST", "name": "Patron", "charged_count": 0},
            items=self.session_history,
            circulation_mode=circulation_mode,
            koha_mode=self.mode
        )
        return ReceiptGenerator.generate_pdf_bytes(receipt_data)


kiosk_controller = KioskController()
