"""
GRANTHSETU — Koha SIP2 Connection Test Tool
=============================================================================
Safely verifies TCP connectivity and performs a SIP2 93/94 Login Handshake
against an authorized Koha ILMS test server.

STRICT SAFETY RULES:
- Never prints or leaks passwords, secrets, or raw login packets.
- Attempts connection ONLY when KOHA_MODE=live or when explicitly forced.
- Validates all configuration parameters before attempting socket connection.
- Measures round-trip handshake latency.
=============================================================================
"""

import os
import sys
import argparse
import socket
from pathlib import Path
from typing import Dict, Optional, List

from sip2.client import KohaSIP2Client


def load_env_file(filepath: Optional[str] = None) -> Dict[str, str]:
    """Safely loads key-value pairs from .env without external dependencies."""
    loaded = {}
    if not filepath:
        candidate = Path(".env")
        if candidate.is_file():
            filepath = str(candidate)
        else:
            return loaded

    try:
        with open(filepath, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    k, v = line.split("=", 1)
                    k = k.strip()
                    v = v.strip().strip('"').strip("'")
                    loaded[k] = v
                    os.environ.setdefault(k, v)
    except Exception as e:
        print(f"Warning: Could not read env file '{filepath}': {e}", file=sys.stderr)
    return loaded


def run_connection_test(
    env_file: Optional[str] = ".env",
    host: Optional[str] = None,
    port: Optional[int] = None,
    username: Optional[str] = None,
    password: Optional[str] = None,
    location_code: Optional[str] = None,
    terminal_password: Optional[str] = None,
    encoding: Optional[str] = None,
    check_sequence: Optional[bool] = None,
    timeout: float = 4.0,
    force_live: bool = False,
    print_output: bool = True,
) -> Dict:
    """
    Executes a controlled SIP2 login test without exposing credentials.
    Returns a dictionary of diagnostic results.
    """
    # 1. Load environment if present
    load_env_file(env_file)

    # 2. Determine mode
    raw_mode = os.getenv("KOHA_MODE", "MOCK").strip().upper()
    is_live = (raw_mode == "LIVE") or force_live

    if not is_live:
        msg = (
            "Connection test skipped: System is configured in MOCK mode (KOHA_MODE=mock). "
            "No live Koha network connection was attempted. "
            "To test a real or staging Koha server, set KOHA_MODE=live in .env or pass --force-live."
        )
        if print_output:
            print("\n" + "=" * 68)
            print("  GRANTHSETU -- Koha SIP2 Connection Test")
            print("=" * 68)
            print("  Mode:               MOCK (Offline Simulation Mode)")
            print("  Status:             SKIPPED - No live network connection attempted.")
            print("-" * 68)
            print("  Notice:")
            print("    The kiosk is currently running safely in MOCK mode.")
            print("    To test connection against an authorized Koha SIP2 test server:")
            print("      1. Set KOHA_MODE=live in your .env file")
            print("      2. Configure KOHA_SERVER_IP, KOHA_SERVER_PORT, KOHA_USER, KOHA_PASS")
            print("      3. Run: python test_koha_connection.py")
            print("=" * 68 + "\n")
        return {
            "success": False,
            "mode": "MOCK",
            "tested": False,
            "message": msg,
        }

    # 3. Resolve configuration parameters
    resolved_host = host or os.getenv("KOHA_SERVER_IP")
    resolved_port_raw = port if port is not None else os.getenv("KOHA_SERVER_PORT")
    resolved_user = username or os.getenv("KOHA_USER") or os.getenv("SIP2_USERNAME")
    resolved_pass = password or os.getenv("KOHA_PASS") or os.getenv("SIP2_PASSWORD")
    resolved_loc = location_code or os.getenv("KOHA_LOCATION", "NFSU")
    resolved_term_pwd = (
        terminal_password
        if terminal_password is not None
        else os.getenv("KOHA_TERMINAL_PASSWORD", "").strip()
    )
    resolved_enc = encoding or os.getenv("SIP2_ENCODING", "utf-8").strip()

    if check_sequence is None:
        seq_env = os.getenv("SIP2_CHECK_SEQUENCE", "false").strip().lower()
        resolved_check_seq = seq_env in ("true", "1", "yes")
    else:
        resolved_check_seq = check_sequence

    # 4. Validate presence of required parameters
    missing: List[str] = []
    if not resolved_host:
        missing.append("KOHA_SERVER_IP")
    if not resolved_port_raw:
        missing.append("KOHA_SERVER_PORT")
    if not resolved_user:
        missing.append("KOHA_USER")
    if not resolved_pass:
        missing.append("KOHA_PASS")

    if missing:
        err_msg = (
            f"Configuration Error: Missing required environment variables: {', '.join(missing)}. "
            "Please configure them in your .env file or environment."
        )
        if print_output:
            print("\n" + "=" * 68)
            print("  GRANTHSETU -- Koha SIP2 Connection Test: CONFIGURATION ERROR")
            print("=" * 68)
            print(f"  Missing:            {', '.join(missing)}")
            print("  Result:             FAILED (Pre-flight validation)")
            print("=" * 68 + "\n")
        return {
            "success": False,
            "mode": "LIVE",
            "tested": False,
            "missing_fields": missing,
            "message": err_msg,
            "error": err_msg,
        }

    # 5. Validate port number
    try:
        resolved_port = int(resolved_port_raw)
        if not (1 <= resolved_port <= 65535):
            raise ValueError(f"Port out of range (1-65535): {resolved_port}")
    except (ValueError, TypeError) as e:
        err_msg = f"Configuration Error: KOHA_SERVER_PORT must be an integer (1-65535), got '{resolved_port_raw}'."
        if print_output:
            print("\n" + "=" * 68)
            print("  GRANTHSETU -- Koha SIP2 Connection Test: INVALID PORT")
            print("=" * 68)
            print(f"  Port:               {resolved_port_raw}")
            print(f"  Error:              {err_msg}")
            print("=" * 68 + "\n")
        return {
            "success": False,
            "mode": "LIVE",
            "tested": False,
            "message": err_msg,
            "error": err_msg,
        }

    # 6. Initialize SIP2 Client and attempt handshake
    if print_output:
        print("\n" + "=" * 68)
        print("  GRANTHSETU -- Koha SIP2 Connection Test (LIVE)")
        print("=" * 68)
        print(f"  Target Server:      {resolved_host}:{resolved_port}")
        print(f"  Location Code:      {resolved_loc}")
        print(f"  SIP2 Username:      {resolved_user}")
        print("  SIP2 Password:      [PROTECTED - NOT DISPLAYED]")
        term_status = "[PROTECTED - CONFIGURED]" if resolved_term_pwd else "[NOT CONFIGURED / OPTIONAL]"
        print(f"  Terminal Password:  {term_status}")
        print(f"  Encoding:           {resolved_enc}")
        print(f"  Check Sequence:     {resolved_check_seq}")
        print(f"  Socket Timeout:     {timeout}s")
        print("-" * 68)
        print("  Connecting and sending SIP2 93 Login Handshake...")

    client = KohaSIP2Client(
        host=resolved_host,
        port=resolved_port,
        username=resolved_user,
        password=resolved_pass,
        location_code=resolved_loc,
        terminal_password=resolved_term_pwd,
        timeout=timeout,
        mode="LIVE",
        encoding=resolved_enc,
        check_sequence=resolved_check_seq,
    )

    result = client.test_connection()

    if print_output:
        latency = result.get("latency_ms", "N/A")
        if result.get("success"):
            print("  Status:             SUCCESS (HTTP 200 / SIP2 94 OK)")
            print(f"  Round-Trip Latency: {latency} ms")
            print("  Result:             Koha ILMS successfully authenticated terminal credentials.")
        else:
            print("  Status:             FAILED")
            print(f"  Round-Trip Latency: {latency} ms")
            print(f"  Error:              {result.get('error_detail') or result.get('message')}")
        print("=" * 68 + "\n")

    return result


def main():
    parser = argparse.ArgumentParser(
        description="GRANTHSETU Koha SIP2 Connection Test Tool"
    )
    parser.add_argument(
        "--env-file",
        default=".env",
        help="Path to .env configuration file (default: .env)",
    )
    parser.add_argument("--host", help="Koha SIP2 server IP or hostname")
    parser.add_argument("--port", type=int, help="Koha SIP2 server port")
    parser.add_argument("--user", help="Koha SIP2 username")
    parser.add_argument("--password", help="Koha SIP2 password")
    parser.add_argument("--location", help="Koha library location code (e.g. NFSU)")
    parser.add_argument("--terminal-password", help="Koha terminal password (if configured in SIPconfig.xml)")
    parser.add_argument("--encoding", help="SIP2 character encoding (e.g. utf-8)")
    parser.add_argument(
        "--check-sequence",
        action="store_true",
        default=None,
        help="Enforce strict sequence number validation",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=4.0,
        help="TCP socket timeout in seconds (default: 4.0)",
    )
    parser.add_argument(
        "--force-live",
        action="store_true",
        help="Force live connection test even if KOHA_MODE=mock",
    )

    args = parser.parse_args()

    result = run_connection_test(
        env_file=args.env_file,
        host=args.host,
        port=args.port,
        username=args.user,
        password=args.password,
        location_code=args.location,
        terminal_password=args.terminal_password,
        encoding=args.encoding,
        check_sequence=args.check_sequence,
        timeout=args.timeout,
        force_live=args.force_live,
        print_output=True,
    )

    # Exit code: 0 if success or skipped in mock mode; 1 if test failed or config invalid
    if result.get("success"):
        sys.exit(0)
    elif result.get("tested") is False and result.get("mode") == "MOCK":
        sys.exit(0)
    else:
        sys.exit(1)


if __name__ == "__main__":
    main()
