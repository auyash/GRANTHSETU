"""
Unit Tests for SIP2 TCP Stream Framing and Buffer Handling.
Verifies proper message delineation, fragmentation reassembly, and multi-message read handling.
"""

import pytest
from unittest.mock import MagicMock
from sip2.client import KohaSIP2Client, SIP2ReceiveError


class MockSocket:
    """Simulates a TCP socket stream with custom chunk deliveries."""
    def __init__(self, chunks):
        self.chunks = list(chunks)
        self.closed = False

    def recv(self, bufsize):
        if not self.chunks:
            return b""  # EOF
        return self.chunks.pop(0)

    def sendall(self, data):
        pass

    def close(self):
        self.closed = True


class TestStreamFraming:
    def test_single_complete_message(self):
        client = KohaSIP2Client(mode="MOCK")
        client.sock = MockSocket([b"941AONFSU|AY1AZD614\r"])
        
        msg = client._read_framed_response()
        assert msg == "941AONFSU|AY1AZD614"
        assert client._rx_buffer == ""

    def test_fragmented_chunks_reassembled(self):
        # A single message delivered in 3 separate small chunks
        client = KohaSIP2Client(mode="MOCK")
        client.sock = MockSocket([
            b"941AO",
            b"NFSU|AY1AZ",
            b"D614\r"
        ])
        
        msg = client._read_framed_response()
        assert msg == "941AONFSU|AY1AZD614"
        assert client._rx_buffer == ""

    def test_multiple_messages_in_single_read(self):
        # Two messages arrive together in one network packet
        client = KohaSIP2Client(mode="MOCK")
        client.sock = MockSocket([
            b"941AONFSU|AY1AZD614\r98YYYYNN60000320261016    120000\r"
        ])
        
        msg1 = client._read_framed_response()
        assert msg1 == "941AONFSU|AY1AZD614"
        # Remainder is buffered in _rx_buffer
        assert client._rx_buffer == "98YYYYNN60000320261016    120000\r"
        
        msg2 = client._read_framed_response()
        assert msg2 == "98YYYYNN60000320261016    120000"
        assert client._rx_buffer == ""

    def test_unexpected_eof_raises_receive_error(self):
        # Server closes connection before sending terminating \r
        client = KohaSIP2Client(mode="MOCK")
        client.sock = MockSocket([b"941Incomplete"])
        
        with pytest.raises(SIP2ReceiveError) as exc_info:
            client._read_framed_response()
        assert "closed unexpectedly" in str(exc_info.value)
