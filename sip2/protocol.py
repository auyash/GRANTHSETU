"""
3M Standard Interchange Protocol (SIP2) Protocol Implementation
Specially tailored for Koha 26 ILMS Integration at NFSU Library.
Supports standard message pairs:
  93/94 - Login / Login Response
  63/64 - Patron Information / Response
  17/18 - Item Information / Response
  11/12 - Checkout / Checkout Response
  09/10 - Checkin / Checkin Response
  29/30 - Renew / Renew Response
  35/36 - End Patron Session / Response
Features:
- Robust field parsing with explicit AZ/AY separation and duplicate tag aggregation
- Strict 3M additive checksum verification
- Sequence number formatting
- Rigorous prefix validation and malformed packet rejection
"""

from datetime import datetime
from typing import Dict, List, Optional, Tuple


def get_sip_timestamp() -> str:
    """Returns SIP2 timestamp: YYYYMMDDZZZZHHMMSS (18 chars)."""
    return datetime.now().strftime("%Y%m%d    %H%M%S")


def compute_checksum(message_without_checksum: str) -> str:
    """
    Computes 3M SIP2 4-character uppercase hex checksum.
    The checksum is the 2's complement of the sum of all character ASCII values
    modulo 65536 up to and including 'AZ'.
    """
    total = sum(ord(c) for c in message_without_checksum)
    chk = (-total) & 0xFFFF
    return f"{chk:04X}"


def verify_checksum(message: str) -> bool:
    """
    Verifies if message ends with valid AZ<checksum>.
    Returns True if no checksum is present or if the checksum matches.
    Returns False if checksum is malformed or invalid.
    """
    if "AZ" not in message:
        return True  # Protocol allows messages without checksums if agreed
    idx = message.rfind("AZ")
    content = message[: idx + 2]
    expected = message[idx + 2 : idx + 6]
    if len(expected) < 4:
        return False
    computed = compute_checksum(content)
    return expected.upper() == computed.upper()


def format_message(msg_body: str, seq_num: Optional[int] = None, use_checksum: bool = True) -> str:
    """Appends optional sequence number AY and checksum AZ, terminated with carriage return \r."""
    result = msg_body
    if seq_num is not None:
        result += f"AY{seq_num}"
    if use_checksum:
        result += "AZ"
        chk = compute_checksum(result)
        result += chk
    return result + "\r"


def parse_fields(raw_fields_str: str) -> Dict[str, str]:
    """
    Robustly parses SIP2 delimited fields (e.g. AO...|AA...|AB...).
    Correctly extracts non-delimited trailing AY and AZ fields.
    Aggregates duplicate tags (such as multiple AS charged items or AF messages) with ';;'.
    """
    fields: Dict[str, str] = {}
    raw = raw_fields_str.strip("\r\n")

    # 1. Extract AZ Checksum if present
    if "AZ" in raw:
        az_idx = raw.rfind("AZ")
        fields["AZ"] = raw[az_idx + 2 : az_idx + 6]
        raw = raw[:az_idx]

    # 2. Extract AY Sequence Number if present
    if "AY" in raw:
        ay_idx = raw.rfind("AY")
        fields["AY"] = raw[ay_idx + 2 :].rstrip("|")
        raw = raw[:ay_idx]

    # 3. Parse variable pipe-delimited fields
    tokens = raw.split("|")
    for token in tokens:
        token = token.strip()
        if len(token) >= 2:
            tag = token[:2]
            val = token[2:]
            if tag in fields:
                fields[tag] += f";;{val}"
            else:
                fields[tag] = val
    return fields


class SIP2MessageBuilder:
    @staticmethod
    def login_message(username: str, password: str, location_code: str = "NFSU", seq_num: Optional[int] = None) -> str:
        """93: Login Request."""
        body = f"9300CN{username}|CO{password}|CP{location_code}|"
        return format_message(body, seq_num=seq_num)

    @staticmethod
    def patron_info_message(
        patron_id: str,
        institution_id: str = "NFSU",
        patron_pwd: str = "",
        terminal_pwd: str = "",
        summary: str = "          ",
        seq_num: Optional[int] = None
    ) -> str:
        """63: Patron Information Request."""
        ts = get_sip_timestamp()
        body = f"63001{ts}{summary}AO{institution_id}|AA{patron_id}|AD{patron_pwd}|"
        if terminal_pwd:
            body += f"AC{terminal_pwd}|"
        return format_message(body, seq_num=seq_num)

    @staticmethod
    def item_info_message(
        item_id: str,
        institution_id: str = "NFSU",
        terminal_pwd: str = "",
        seq_num: Optional[int] = None
    ) -> str:
        """17: Item Information Request."""
        ts = get_sip_timestamp()
        body = f"17{ts}AO{institution_id}|AB{item_id}|AC{terminal_pwd}|"
        return format_message(body, seq_num=seq_num)

    @staticmethod
    def checkout_message(
        patron_id: str,
        item_id: str,
        institution_id: str = "NFSU",
        terminal_pwd: str = "",
        no_block: str = "N",
        sc_renewal_policy: str = "Y",
        seq_num: Optional[int] = None
    ) -> str:
        """11: Checkout Request."""
        ts = get_sip_timestamp()
        nb_due = "                  "  # 18 spaces
        body = (
            f"11{sc_renewal_policy}{no_block}{ts}{nb_due}"
            f"AO{institution_id}|AA{patron_id}|AB{item_id}|AC{terminal_pwd}|"
        )
        return format_message(body, seq_num=seq_num)

    @staticmethod
    def checkin_message(
        item_id: str,
        institution_id: str = "NFSU",
        terminal_pwd: str = "",
        location: str = "NFSU",
        no_block: str = "N",
        seq_num: Optional[int] = None
    ) -> str:
        """09: Checkin Request."""
        ts = get_sip_timestamp()
        body = (
            f"09{no_block}{ts}{ts}"
            f"AP{location}|AO{institution_id}|AB{item_id}|AC{terminal_pwd}|"
        )
        return format_message(body, seq_num=seq_num)

    @staticmethod
    def renew_message(
        patron_id: str,
        item_id: str,
        institution_id: str = "NFSU",
        terminal_pwd: str = "",
        title: str = "",
        seq_num: Optional[int] = None
    ) -> str:
        """29: Renew Request."""
        ts = get_sip_timestamp()
        nb_due = "                  "
        body = (
            f"29YY{ts}{nb_due}"
            f"AO{institution_id}|AA{patron_id}|AB{item_id}|AC{terminal_pwd}|AJ{title}|"
        )
        return format_message(body, seq_num=seq_num)

    @staticmethod
    def end_session_message(patron_id: str, institution_id: str = "NFSU", seq_num: Optional[int] = None) -> str:
        """35: End Patron Session Request."""
        ts = get_sip_timestamp()
        body = f"35{ts}AO{institution_id}|AA{patron_id}|"
        return format_message(body, seq_num=seq_num)


class SIP2ResponseParser:
    @staticmethod
    def _validate_raw(raw: str, min_len: int, expected_code: str) -> Optional[Dict]:
        """Validates response length, header identifier, and checksum integrity."""
        raw_clean = raw.strip("\r\n")
        if len(raw_clean) < min_len:
            return {
                "ok": False,
                "error_code": "MALFORMED_RESPONSE",
                "error": f"Message too short (< {min_len} chars)",
                "raw": raw
            }
        if raw_clean[:2] != expected_code:
            return {
                "ok": False,
                "error_code": "UNEXPECTED_MESSAGE_CODE",
                "error": f"Expected message code '{expected_code}', got '{raw_clean[:2]}'",
                "raw": raw
            }
        if "AZ" in raw_clean and not verify_checksum(raw_clean):
            return {
                "ok": False,
                "error_code": "CHECKSUM_FAILED",
                "error": "SIP2 AZ checksum validation failed",
                "raw": raw
            }
        return None

    @staticmethod
    def parse_login_response(raw: str) -> Dict:
        """94: Login Response."""
        err = SIP2ResponseParser._validate_raw(raw, 3, "94")
        if err:
            return err
        raw_clean = raw.strip("\r\n")
        ok = raw_clean[2:3] == "1"
        fields = parse_fields(raw_clean[3:])
        return {"ok": ok, "raw": raw, "fields": fields}

    @staticmethod
    def parse_patron_info_response(raw: str) -> Dict:
        """64: Patron Information Response."""
        err = SIP2ResponseParser._validate_raw(raw, 61, "64")
        if err:
            return err
        raw_clean = raw.strip("\r\n")
        
        patron_status = raw_clean[2:16]
        language = raw_clean[16:19]
        tx_date = raw_clean[19:37]
        hold_count = int(raw_clean[37:41]) if raw_clean[37:41].strip().isdigit() else 0
        overdue_count = int(raw_clean[41:45]) if raw_clean[41:45].strip().isdigit() else 0
        charged_count = int(raw_clean[45:49]) if raw_clean[45:49].strip().isdigit() else 0
        
        fields = parse_fields(raw_clean[61:])
        
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
        """18: Item Information Response."""
        err = SIP2ResponseParser._validate_raw(raw, 26, "18")
        if err:
            return err
        raw_clean = raw.strip("\r\n")
            
        circ_status = raw_clean[2:4]
        security_marker = raw_clean[4:6]
        fee_type = raw_clean[6:8]
        tx_date = raw_clean[8:26]
        fields = parse_fields(raw_clean[26:])
        
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
        """12: Checkout Response."""
        err = SIP2ResponseParser._validate_raw(raw, 24, "12")
        if err:
            return err
        raw_clean = raw.strip("\r\n")
            
        ok = raw_clean[2:3] == "1"
        renewal_ok = raw_clean[3:4] == "Y"
        magnetic_media = raw_clean[4:5] == "Y"
        desensitize = raw_clean[5:6] == "Y"  # If Y, RFID EAS tag should be DISARMED
        tx_date = raw_clean[6:24]
        fields = parse_fields(raw_clean[24:])
        
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
        """10: Checkin Response."""
        err = SIP2ResponseParser._validate_raw(raw, 24, "10")
        if err:
            return err
        raw_clean = raw.strip("\r\n")
            
        ok = raw_clean[2:3] == "1"
        resensitize = raw_clean[3:4] == "Y"  # If Y, RFID EAS tag should be RE-ARMED
        magnetic_media = raw_clean[4:5] == "Y"
        alert = raw_clean[5:6] == "Y"
        tx_date = raw_clean[6:24]
        fields = parse_fields(raw_clean[24:])
        
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
        """30: Renew Response."""
        err = SIP2ResponseParser._validate_raw(raw, 24, "30")
        if err:
            return err
        raw_clean = raw.strip("\r\n")
            
        ok = raw_clean[2:3] == "1"
        renewal_ok = raw_clean[3:4] == "Y"
        magnetic_media = raw_clean[4:5] == "Y"
        desensitize = raw_clean[5:6] == "Y"
        tx_date = raw_clean[6:24]
        fields = parse_fields(raw_clean[24:])
        
        return {
            "ok": ok,
            "renewal_ok": renewal_ok,
            "magnetic_media": magnetic_media,
            "desensitize": desensitize,
            "transaction_date": tx_date,
            "patron_id": fields.get("AA", ""),
            "item_id": fields.get("AB", ""),
            "title": fields.get("AJ", "Unknown Title"),
            "due_date": fields.get("AH", ""),
            "screen_msg": fields.get("AF", "Renewal processed"),
            "fields": fields,
            "raw": raw
        }

    @staticmethod
    def parse_end_session_response(raw: str) -> Dict:
        """36: End Session Response."""
        err = SIP2ResponseParser._validate_raw(raw, 21, "36")
        if err:
            return err
        raw_clean = raw.strip("\r\n")
        end_session_ok = raw_clean[2:3].upper() == "Y"
        tx_date = raw_clean[3:21]
        fields = parse_fields(raw_clean[21:])
        return {
            "ok": end_session_ok,
            "transaction_date": tx_date,
            "patron_id": fields.get("AA", ""),
            "fields": fields,
            "raw": raw
        }
