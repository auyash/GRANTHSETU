"""
Unit Tests for 3M SIP2 Protocol Builders, Parsers, Checksums, and Field Extraction.
"""

import pytest
from sip2.protocol import (
    compute_checksum,
    verify_checksum,
    format_message,
    parse_fields,
    SIP2MessageBuilder,
    SIP2ResponseParser,
)


class TestSIP2ChecksumAndFormatting:
    def test_checksum_computation_and_verification(self):
        msg = "9300CNrfid|CORfid@123|CPNFSU|"
        formatted = format_message(msg, seq_num=1, use_checksum=True)
        assert formatted.endswith("\r")
        assert "AY1" in formatted
        assert "AZ" in formatted
        
        # Verify checksum passes on untampered message
        unframed = formatted.strip("\r\n")
        assert verify_checksum(unframed) is True

    def test_checksum_fails_on_tampered_payload(self):
        msg = "9300CNrfid|CORfid@123|CPNFSU|"
        formatted = format_message(msg, seq_num=1, use_checksum=True).strip("\r\n")
        
        # Tamper payload
        tampered = formatted.replace("rfid", "attacker")
        assert verify_checksum(tampered) is False

    def test_verify_checksum_without_az(self):
        # When protocol operates without checksum, verify_checksum returns True
        raw = "941AONFSU|"
        assert verify_checksum(raw) is True

    def test_verify_checksum_truncated_az(self):
        # Truncated checksum (< 4 chars) should fail
        raw = "941AONFSU|AZ12"
        assert verify_checksum(raw) is False


class TestSIP2FieldParsing:
    def test_standard_pipe_delimited_fields(self):
        raw = "AONFSU|AA1234|AB5678|AJTest Book|"
        fields = parse_fields(raw)
        assert fields["AO"] == "NFSU"
        assert fields["AA"] == "1234"
        assert fields["AB"] == "5678"
        assert fields["AJ"] == "Test Book"

    def test_non_delimited_ay_and_az(self):
        # In 3M SIP2, AY and AZ often appear at end without delimiter (e.g. |AY1AZF4B2)
        raw = "AONFSU|AA1234|AY1AZF4B2"
        fields = parse_fields(raw)
        assert fields["AO"] == "NFSU"
        assert fields["AA"] == "1234"
        assert fields["AY"] == "1"
        assert fields["AZ"] == "F4B2"

    def test_duplicate_fields_aggregated(self):
        # Multiple charged items (AS) or screen lines (AF)
        raw = "AONFSU|ASBook1|ASBook2|ASBook3|AFLine 1|AFLine 2|"
        fields = parse_fields(raw)
        assert fields["AO"] == "NFSU"
        assert fields["AS"] == "Book1;;Book2;;Book3"
        assert fields["AF"] == "Line 1;;Line 2"


class TestSIP2MessageBuilders:
    def test_login_message(self):
        msg = SIP2MessageBuilder.login_message("testuser", "testpass", "NFSU", seq_num=0)
        assert msg.startswith("9300CNtestuser|COtestpass|CPNFSU|AY0AZ")
        assert msg.endswith("\r")

    def test_patron_info_message(self):
        msg = SIP2MessageBuilder.patron_info_message("NFSU2024001", institution_id="NFSU", seq_num=2)
        assert msg.startswith("63001")
        assert "AONFSU" in msg
        assert "AANFSU2024001" in msg
        assert "AY2AZ" in msg

    def test_checkout_message(self):
        msg = SIP2MessageBuilder.checkout_message("NFSU2024001", "BK-FOR-101", seq_num=3)
        assert msg.startswith("11YN")
        assert "AONFSU" in msg
        assert "AANFSU2024001" in msg
        assert "ABBK-FOR-101" in msg
        assert "AY3AZ" in msg

    def test_checkin_message(self):
        msg = SIP2MessageBuilder.checkin_message("BK-FOR-101", seq_num=4)
        assert msg.startswith("09N")
        assert "ABBK-FOR-101" in msg
        assert "AY4AZ" in msg

    def test_renew_message(self):
        msg = SIP2MessageBuilder.renew_message("NFSU2024001", "BK-FOR-101", title="Forensic Science", seq_num=5)
        assert msg.startswith("29YY")
        assert "ABBK-FOR-101" in msg
        assert "AJForensic Science" in msg
        assert "AY5AZ" in msg

    def test_end_session_message(self):
        msg = SIP2MessageBuilder.end_session_message("NFSU2024001", seq_num=6)
        assert msg.startswith("35")
        assert "AANFSU2024001" in msg
        assert "AY6AZ" in msg


class TestSIP2ResponseParsers:
    def test_parse_login_response_ok(self):
        raw = format_message("941AONFSU|", seq_num=1, use_checksum=True)
        res = SIP2ResponseParser.parse_login_response(raw)
        assert res["ok"] is True
        assert res["fields"]["AO"] == "NFSU"

    def test_parse_login_response_rejected(self):
        raw = format_message("940AONFSU|", seq_num=1, use_checksum=True)
        res = SIP2ResponseParser.parse_login_response(raw)
        assert res["ok"] is False

    def test_parse_login_response_checksum_failure(self):
        # Tampered checksum
        raw = "941AONFSU|AY1AZ0000\r"
        res = SIP2ResponseParser.parse_login_response(raw)
        assert res["ok"] is False
        assert res["error_code"] == "CHECKSUM_FAILED"

    def test_parse_patron_info_response_ok(self):
        # 61 chars fixed prefix + variable fields
        fixed = "64              00120261016    120000000000000001000000000000"
        variable = "AONFSU|AANFSU2024001|AEPriya Nair|BLY|BK1|ASBK-FOR-101|"
        msg = format_message(fixed + variable, seq_num=2, use_checksum=True)
        res = SIP2ResponseParser.parse_patron_info_response(msg)
        assert res["ok"] is True
        assert res["patron_id"] == "NFSU2024001"
        assert res["patron_name"] == "Priya Nair"
        assert res["valid_patron"] is True
        assert res["charged_count"] == 1
        assert res["charged_items"] == ["BK-FOR-101"]

    def test_parse_patron_info_response_too_short(self):
        raw = "64TooShort\r"
        res = SIP2ResponseParser.parse_patron_info_response(raw)
        assert res["ok"] is False
        assert res["error_code"] == "MALFORMED_RESPONSE"

    def test_parse_checkout_response_ok(self):
        # 24 chars fixed prefix (ok=1, renewal_ok=N, magnetic=Y, desensitize=Y, 18-char date)
        fixed = "121NYY20261016    120000"
        variable = "AONFSU|AANFSU2024001|ABBK-TOX-501|AJToxicology|AH20261030    235900|"
        msg = format_message(fixed + variable, seq_num=3, use_checksum=True)
        res = SIP2ResponseParser.parse_checkout_response(msg)
        assert res["ok"] is True
        assert res["desensitize"] is True
        assert res["item_id"] == "BK-TOX-501"
        assert res["title"] == "Toxicology"

    def test_parse_checkout_response_denied(self):
        fixed = "120NNN20261016    120000"
        variable = "AONFSU|AANFSU2024001|ABBK-TOX-501|AFItem on hold for another patron|"
        msg = format_message(fixed + variable, seq_num=3, use_checksum=True)
        res = SIP2ResponseParser.parse_checkout_response(msg)
        assert res["ok"] is False
        assert res["desensitize"] is False
        assert "Item on hold" in res["screen_msg"]

    def test_parse_checkin_response_ok(self):
        fixed = "101YYN20261016    120000"
        variable = "AONFSU|ABBK-TOX-501|AJToxicology|AFReturned|"
        msg = format_message(fixed + variable, seq_num=4, use_checksum=True)
        res = SIP2ResponseParser.parse_checkin_response(msg)
        assert res["ok"] is True
        assert res["resensitize"] is True
        assert res["item_id"] == "BK-TOX-501"

    def test_parse_renew_response_ok(self):
        fixed = "301YYY20261016    120000"
        variable = "AONFSU|AANFSU2024001|ABBK-TOX-501|AH20261114    235900|"
        msg = format_message(fixed + variable, seq_num=5, use_checksum=True)
        res = SIP2ResponseParser.parse_renew_response(msg)
        assert res["ok"] is True
        assert res["renewal_ok"] is True
        assert res["magnetic_media"] is True
        assert res["desensitize"] is True
        assert res["item_id"] == "BK-TOX-501"

    def test_parse_end_session_response_ok(self):
        fixed = "36Y20261016    120000"
        variable = "AONFSU|AANFSU2024001|"
        msg = format_message(fixed + variable, seq_num=6, use_checksum=True)
        res = SIP2ResponseParser.parse_end_session_response(msg)
        assert res["ok"] is True
        assert res["patron_id"] == "NFSU2024001"
