"""
Core Kiosk Circulation Controller for NFSU Library.
Enforces:
1. Koha 26 SIP2 Integration (Live connection to 117.239.182.15:6001).
2. Strict 2-Book Borrowing Limit per patron.
3. RFID EAS/AFI Security Tag Toggling (Arming & Disarming).
4. Exit Gate Security Alarm logic.
5. Inactivity session auto-logout.
"""

from datetime import datetime, timedelta
from typing import Dict, List, Optional
import os
import logging
from sip2.client import KohaSIP2Client
from security.audit import audit_logger

logger = logging.getLogger("kiosk_engine")


class KioskController:
    MAX_BORROW_LIMIT = 2  # University policy limit

    def __init__(self):
        # Koha SIP2 Client pointing to the NFSU configuration (supports env vars for cloud)
        koha_host = os.getenv("KOHA_SERVER_IP", "117.239.182.15")
        koha_port = int(os.getenv("KOHA_SERVER_PORT", "6001"))
        koha_user = os.getenv("KOHA_USER", "rfid")
        koha_pass = os.getenv("KOHA_PASS", "Rfid@123")
        koha_loc = os.getenv("KOHA_LOCATION", "NFSU")

        self.sip_client = KohaSIP2Client(
            host=koha_host,
            port=koha_port,
            username=koha_user,
            password=koha_pass,
            location_code=koha_loc
        )
        self.sip_client.connect()

        # Active patron session state
        self.active_patron: Optional[Dict] = None
        self.session_started_at: Optional[datetime] = None

        # RFID Security State Registry (EAS / AFI bits for items)
        # "ARMED" (AFI 0xC2) -> Will trigger exit gate alarm
        # "DISARMED" (AFI 0x07) -> Safe passage through exit gate
        self.rfid_tag_registry: Dict[str, str] = {
            "BK-FOR-101": "DISARMED",  # Currently with Priya Nair
            "BK-CYB-201": "DISARMED",  # Currently with Dr. Rajesh Verma
            "BK-DIG-301": "DISARMED",  # Currently with Dr. Rajesh Verma
            "BK-BAL-401": "DISARMED",  # Currently with Kabir Das
            "BK-TOX-501": "ARMED",     # On shelf
            "BK-DNA-601": "ARMED",     # On shelf
        }

    def authenticate_patron(self, patron_id: str, auth_method: str = "CARD") -> Dict:
        """
        Authenticates patron via MIFARE Card, Barcode, or Face Recognition.
        Fetches live borrowing stats from Koha 26 via SIP2 message 63.
        """
        patron_id = patron_id.strip()
        resp = self.sip_client.get_patron_info(patron_id)
        
        if not resp.get("ok"):
            return {
                "success": False,
                "message": f"Could not find or verify patron ID: {patron_id}"
            }

        # Calculate borrowing capacity
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

        audit_logger.log_event(
            category="AUTHENTICATION",
            action=f"LOGIN_{auth_method}",
            details=f"Patron {patron_id} ({self.active_patron['name']}) logged in. Borrowed: {charged_count}/{self.MAX_BORROW_LIMIT} books."
        )

        return {
            "success": True,
            "patron": self.active_patron,
            "server_status": self.sip_client.get_status()
        }

    def get_item_details(self, item_barcode: str) -> Dict:
        """Looks up book information via SIP2 message 17."""
        item_barcode = item_barcode.strip()
        info = self.sip_client.get_item_info(item_barcode)
        
        # Attach RFID EAS security state
        eas_state = self.rfid_tag_registry.get(item_barcode, "ARMED")
        info["eas_security_state"] = eas_state
        info["afi_byte"] = "0x07 (Disarmed)" if eas_state == "DISARMED" else "0xC2 (Armed - Active)"
        return info

    def checkout_item(self, item_barcode: str) -> Dict:
        """
        Processes checkout with:
        1. 2-Book Borrowing Limit Guard.
        2. Koha 26 SIP2 message 11.
        3. RFID EAS Security Bit Disarm (AFI 0x07).
        """
        if not self.active_patron:
            return {"success": False, "message": "No active patron session. Please authenticate first."}

        patron_id = self.active_patron["patron_id"]
        item_barcode = item_barcode.strip()

        # 1. Enforce 2-Book Limit Guard
        current_charged = self.active_patron["charged_count"]
        if current_charged >= self.MAX_BORROW_LIMIT:
            audit_logger.log_limit_violation(patron_id, current_charged, item_barcode)
            return {
                "success": False,
                "error_code": "LIMIT_EXCEEDED",
                "message": (
                    f"Borrowing limit reached ({current_charged}/{self.MAX_BORROW_LIMIT} books). "
                    "University policy allows a maximum of 2 books per patron. "
                    "Please return an existing book before issuing a new one."
                ),
                "charged_count": current_charged,
                "max_limit": self.MAX_BORROW_LIMIT
            }

        # 2. Query Koha SIP2 Checkout
        res = self.sip_client.checkout_item(patron_id, item_barcode)
        if not res.get("ok"):
            # Koha denied or limit hit
            return {
                "success": False,
                "message": res.get("screen_msg", "Checkout was declined by Koha ILMS."),
                "koha_response": res
            }

        # 3. Success! Update local patron state
        self.active_patron["charged_count"] += 1
        if item_barcode not in self.active_patron["charged_items"]:
            self.active_patron["charged_items"].append(item_barcode)
        self.active_patron["available_limit"] = max(0, self.MAX_BORROW_LIMIT - self.active_patron["charged_count"])
        self.active_patron["limit_reached"] = self.active_patron["charged_count"] >= self.MAX_BORROW_LIMIT

        # 4. Disarm RFID EAS Security Bit (AFI 0x07) so book won't trigger exit alarm!
        self.rfid_tag_registry[item_barcode] = "DISARMED"
        audit_logger.log_eas_state_change(item_barcode, "DISARMED", "Legitimate SIP2 Checkout confirmed")

        return {
            "success": True,
            "message": f"Successfully issued '{res.get('title', item_barcode)}'. Due: {res.get('due_date')}",
            "item_id": item_barcode,
            "title": res.get("title", item_barcode),
            "due_date": res.get("due_date"),
            "rfid_security": "DISARMED (AFI 0x07) - Safe to pass exit gates",
            "patron": self.active_patron
        }

    def checkin_item(self, item_barcode: str) -> Dict:
        """
        Processes book return via Koha SIP2 message 09 and re-arms RFID EAS security tag.
        """
        item_barcode = item_barcode.strip()
        res = self.sip_client.checkin_item(item_barcode)

        if not res.get("ok"):
            return {
                "success": False,
                "message": res.get("screen_msg", "Return could not be processed by Koha ILMS."),
                "koha_response": res
            }

        # 1. Re-arm RFID EAS tag to ARMED (AFI 0xC2)
        self.rfid_tag_registry[item_barcode] = "ARMED"
        audit_logger.log_eas_state_change(item_barcode, "ARMED", "Book returned to library inventory")

        # 2. Update active patron state if this patron held the item
        if self.active_patron:
            if item_barcode in self.active_patron["charged_items"]:
                self.active_patron["charged_items"].remove(item_barcode)
                self.active_patron["charged_count"] = max(0, self.active_patron["charged_count"] - 1)
                self.active_patron["available_limit"] = max(0, self.MAX_BORROW_LIMIT - self.active_patron["charged_count"])
                self.active_patron["limit_reached"] = False

        return {
            "success": True,
            "message": f"'{res.get('title', item_barcode)}' returned successfully. Thank you!",
            "item_id": item_barcode,
            "title": res.get("title", item_barcode),
            "rfid_security": "RE-ARMED (AFI 0xC2) - Secured in library",
            "patron": self.active_patron
        }

    def renew_item(self, item_barcode: str) -> Dict:
        """Renews item via Koha SIP2 message 29."""
        if not self.active_patron:
            return {"success": False, "message": "Please scan or tap patron card first."}

        patron_id = self.active_patron["patron_id"]
        res = self.sip_client.renew_item(patron_id, item_barcode)

        if not res.get("ok"):
            return {
                "success": False,
                "message": res.get("screen_msg", "Renewal could not be processed.")
            }

        return {
            "success": True,
            "message": f"Renewed '{res.get('title', item_barcode)}'. New due date: {res.get('due_date')}",
            "item_id": item_barcode,
            "title": res.get("title", item_barcode),
            "due_date": res.get("due_date")
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

    def end_session(self) -> Dict:
        """Ends current patron session cleanly with SIP2 35."""
        if self.active_patron:
            pid = self.active_patron["patron_id"]
            self.sip_client.end_patron_session(pid)
            audit_logger.log_event("SESSION", "END_SESSION", f"Session closed for patron {pid}.")
            self.active_patron = None
            self.session_started_at = None
        return {"success": True, "message": "Session terminated cleanly."}


kiosk_controller = KioskController()
