"""
3M Standard Interchange Protocol (SIP2) Protocol Implementation
Specially tailored for Koha 26 ILMS Integration at NFSU Library.
Supports standard message types:
  93/94 - Login / Login Response
  63/64 - Patron Information / Response
  17/18 - Item Information / Response
  11/12 - Checkout / Checkout Response
  09/10 - Checkin / Checkin Response
  29/30 - Renew / Renew Response
  35/36 - End Patron Session / Response
Includes 3M CRC Checksum calculation, sequence numbering, and variable field parsing.
"""

from datetime import datetime
from typing import Dict, List, Optional, Tuple


def get_sip_timestamp() -> str:
    """Returns SIP2 timestamp: YYYYMMDDZZZZHHMMSS (18 chars)"""
    return datetime.now().strftime("%Y%m%d    %H%M%S")


def compute_checksum(message_without_checksum: str) -> str:
    """
    Computes 3M SIP2 4-character hex checksum.
    The checksum is calculated such that adding the checksum to the sum of
    characters modulo 65536 equals 0.
    """
    total = sum(ord(c) for c in message_without_checksum)
    chk = (-total) & 0xFFFF
    return f"{chk:04X}"


def verify_checksum(message: str) -> bool:
    """Verifies if message ends with valid AZ<checksum>."""
    if "AZ" not in message:
        return True  # No checksum present in message
    idx = message.rfind("AZ")
    content = message[: idx + 2]
    expected = message[idx + 2 : idx + 6]
    computed = compute_checksum(content)
    return expected.upper() == computed.upper()


def format_message(msg_body: str, seq_num: Optional[int] = None, use_checksum: bool = True) -> str:
    """Appends sequence number AY and checksum AZ if requested."""
    result = msg_body
    if seq_num is not None:
        result += f"AY{seq_num}"
    if use_checksum:
        result += "AZ"
        chk = compute_checksum(result)
        result += chk
    return result + "\r"


def parse_fields(raw_fields_str: str) -> Dict[str, str]:
    """Parses SIP2 delimited fields (e.g., AO...|AA...|AB...)."""
    fields: Dict[str, str] = {}
    tokens = raw_fields_str.split("|")
    for token in tokens:
        token = token.strip("\r\n")
        if len(token) >= 2:
            tag = token[:2]
            val = token[2:]
            # If multiple tokens with same tag exist (e.g. AS for multiple charged items), store list or comma-separated
            if tag in fields:
                fields[tag] += f";;{val}"
            else:
                fields[tag] = val
    return fields


class SIP2MessageBuilder:
    @staticmethod
    def login_message(username: str, password: str, location_code: str = "NFSU") -> str:
        """93: Login Request"""
        body = f"9300CN{username}|CO{password}|CP{location_code}|"
        return format_message(body)

    @staticmethod
    def patron_info_message(
        patron_id: str,
        institution_id: str = "NFSU",
        patron_pwd: str = "",
        summary: str = "          "
    ) -> str:
        """63: Patron Information Request"""
        ts = get_sip_timestamp()
        body = f"63001{ts}{summary}AO{institution_id}|AA{patron_id}|AD{patron_pwd}|"
        return format_message(body)

    @staticmethod
    def item_info_message(
        item_id: str,
        institution_id: str = "NFSU",
        terminal_pwd: str = ""
    ) -> str:
        """17: Item Information Request"""
        ts = get_sip_timestamp()
        body = f"17{ts}AO{institution_id}|AB{item_id}|AC{terminal_pwd}|"
        return format_message(body)

    @staticmethod
    def checkout_message(
        patron_id: str,
        item_id: str,
        institution_id: str = "NFSU",
        terminal_pwd: str = "",
        no_block: str = "N",
        sc_renewal_policy: str = "Y"
    ) -> str:
        """11: Checkout Request"""
        ts = get_sip_timestamp()
        nb_due = "                  "  # 18 spaces
        body = (
            f"11{sc_renewal_policy}{no_block}{ts}{nb_due}"
            f"AO{institution_id}|AA{patron_id}|AB{item_id}|AC{terminal_pwd}|"
        )
        return format_message(body)

    @staticmethod
    def checkin_message(
        item_id: str,
        institution_id: str = "NFSU",
        terminal_pwd: str = "",
        location: str = "NFSU",
        no_block: str = "N"
    ) -> str:
        """09: Checkin Request"""
        ts = get_sip_timestamp()
        body = (
            f"09{no_block}{ts}{ts}"
            f"AP{location}|AO{institution_id}|AB{item_id}|AC{terminal_pwd}|"
        )
        return format_message(body)

    @staticmethod
    def renew_message(
        patron_id: str,
        item_id: str,
        institution_id: str = "NFSU",
        terminal_pwd: str = "",
        title: str = ""
    ) -> str:
        """29: Renew Request"""
        ts = get_sip_timestamp()
        nb_due = "                  "
        body = (
            f"29YY{ts}{nb_due}"
            f"AO{institution_id}|AA{patron_id}|AB{item_id}|AC{terminal_pwd}|AJ{title}|"
        )
        return format_message(body)

    @staticmethod
    def end_session_message(patron_id: str, institution_id: str = "NFSU") -> str:
        """35: End Patron Session Request"""
        ts = get_sip_timestamp()
        body = f"35{ts}AO{institution_id}|AA{patron_id}|"
        return format_message(body)


class SIP2ResponseParser:
    @staticmethod
    def parse_login_response(raw: str) -> Dict:
        """94: Login Response"""
        raw = raw.strip("\r\n")
        ok = raw[2:3] == "1" if len(raw) >= 3 else False
        fields = parse_fields(raw[3:])
        return {"ok": ok, "raw": raw, "fields": fields}

    @staticmethod
    def parse_patron_info_response(raw: str) -> Dict:
        """64: Patron Information Response"""
        raw = raw.strip("\r\n")
        if len(raw) < 61:
            return {"error": "Malformed 64 message", "raw": raw}
        
        patron_status = raw[2:16]
        language = raw[16:19]
        tx_date = raw[19:37]
        hold_count = int(raw[37:41]) if raw[37:41].strip().isdigit() else 0
        overdue_count = int(raw[41:45]) if raw[41:45].strip().isdigit() else 0
        charged_count = int(raw[45:49]) if raw[45:49].strip().isdigit() else 0
        
        fields = parse_fields(raw[61:])
        
        # Check if BK field provides precise charged count
        if "BK" in fields and fields["BK"].isdigit():
            charged_count = int(fields["BK"])
            
        charged_items = []
        if "AS" in fields:
            charged_items = [item.strip() for item in fields["AS"].split(";;") if item.strip()]

        valid_patron = fields.get("BL", "Y").upper() == "Y"
        patron_name = fields.get("AE", "NFSU Patron")
        patron_barcode = fields.get("AA", "")

        return {
            "ok": True,
            "patron_status": patron_status,
            "transaction_date": tx_date,
            "hold_count": hold_count,
            "overdue_count": overdue_count,
            "charged_count": charged_count,
            "charged_items": charged_items,
            "valid_patron": valid_patron,
            "patron_name": patron_name,
            "patron_id": patron_barcode,
            "fields": fields,
            "raw": raw
        }

    @staticmethod
    def parse_item_info_response(raw: str) -> Dict:
        """18: Item Information Response"""
        raw = raw.strip("\r\n")
        if len(raw) < 32:
            return {"error": "Malformed 18 message", "raw": raw}
            
        circ_status = raw[2:4]
        security_marker = raw[4:6]
        fee_type = raw[6:8]
        tx_date = raw[8:26]
        fields = parse_fields(raw[26:])
        
        return {
            "ok": True,
            "circulation_status": circ_status,
            "security_marker": security_marker,
            "item_id": fields.get("AB", ""),
            "title": fields.get("AJ", "Unknown Title"),
            "author": fields.get("CF", fields.get("AJ", "Unknown")),
            "owner": fields.get("AQ", "NFSU Central Library"),
            "current_location": fields.get("AP", "NFSU"),
            "fields": fields,
            "raw": raw
        }

    @staticmethod
    def parse_checkout_response(raw: str) -> Dict:
        """12: Checkout Response"""
        raw = raw.strip("\r\n")
        if len(raw) < 24:
            return {"error": "Malformed 12 message", "raw": raw, "ok": False}
            
        ok = raw[2:3] == "1"
        renewal_ok = raw[3:4] == "Y"
        magnetic_media = raw[4:5] == "Y"
        desensitize = raw[5:6] == "Y"  # If Y, RFID EAS tag should be DISARMED
        tx_date = raw[6:24]
        fields = parse_fields(raw[24:])
        
        return {
            "ok": ok,
            "renewal_ok": renewal_ok,
            "desensitize": desensitize,
            "transaction_date": tx_date,
            "patron_id": fields.get("AA", ""),
            "item_id": fields.get("AB", ""),
            "title": fields.get("AJ", "Unknown Title"),
            "due_date": fields.get("AH", ""),
            "screen_msg": fields.get("AF", "Checkout processed"),
            "fields": fields,
            "raw": raw
        }

    @staticmethod
    def parse_checkin_response(raw: str) -> Dict:
        """10: Checkin Response"""
        raw = raw.strip("\r\n")
        if len(raw) < 24:
            return {"error": "Malformed 10 message", "raw": raw, "ok": False}
            
        ok = raw[2:3] == "1"
        resensitize = raw[3:4] == "Y"  # If Y, RFID EAS tag should be RE-ARMED
        magnetic_media = raw[4:5] == "Y"
        alert = raw[5:6] == "Y"
        tx_date = raw[6:24]
        fields = parse_fields(raw[24:])
        
        return {
            "ok": ok,
            "resensitize": resensitize,
            "alert": alert,
            "transaction_date": tx_date,
            "item_id": fields.get("AB", ""),
            "title": fields.get("AJ", "Unknown Title"),
            "permanent_loc": fields.get("AQ", "NFSU Central Library"),
            "screen_msg": fields.get("AF", "Return processed successfully"),
            "fields": fields,
            "raw": raw
        }

    @staticmethod
    def parse_renew_response(raw: str) -> Dict:
        """30: Renew Response"""
        raw = raw.strip("\r\n")
        if len(raw) < 24:
            return {"error": "Malformed 30 message", "raw": raw, "ok": False}
            
        ok = raw[2:3] == "1"
        renewal_ok = raw[3:4] == "Y"
        tx_date = raw[6:24]
        fields = parse_fields(raw[24:])
        
        return {
            "ok": ok,
            "renewal_ok": renewal_ok,
            "transaction_date": tx_date,
            "patron_id": fields.get("AA", ""),
            "item_id": fields.get("AB", ""),
            "title": fields.get("AJ", "Unknown Title"),
            "due_date": fields.get("AH", ""),
            "screen_msg": fields.get("AF", "Renewal processed"),
            "fields": fields,
            "raw": raw
        }
