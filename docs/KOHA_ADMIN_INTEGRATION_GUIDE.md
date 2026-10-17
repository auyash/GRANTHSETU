# GRANTHSETU -- Koha 26 SIP2 Integration Guide for Library Administrators

**National Forensic Sciences University (NFSU) Central Library**  
*Self-Check Service Kiosk (PS-02) Integration & Staging Protocol*

---

## 1. Executive Summary

This guide provides authorized NFSU library systems administrators and IT engineers with exact, safe procedures for integrating the **GRANTHSETU** self-service kiosk with a **Koha ILMS test or staging server** using the 3M Standard Interchange Protocol v2.00 (SIP2).

### Core Principles
1. **Never Connect Directly to Production Without Prior Staging Validation**: All integration, protocol verification, and circulation testing must first succeed against an authorized Koha staging/test server.
2. **Zero Credential Exposure**: Passwords and secrets are never committed to version control, logged in application logs, printed on terminal consoles, or sent in REST error payloads.
3. **Transaction Safety**: Checkout operations require positive, verified confirmation from Koha before any RFID security tag is disarmed. Ambiguous network states are classified as `UNKNOWN_AFTER_SEND`, preserving armed security to prevent theft.
4. **Explicit Live Gating**: The kiosk defaults to safe, offline simulation (`MOCK` mode). Live TCP socket communication is attempted only when `KOHA_MODE=live` is explicitly configured.

---

## 2. Canonical Environment Variables Reference

GRANTHSETU relies on standardized environment variables configured in a local `.env` file (copied from `.env.example`).

| Variable | Required | Default | Description |
| :--- | :--- | :--- | :--- |
| `KOHA_MODE` | No | `mock` | Operational mode: `mock` (in-memory offline simulation) or `live` (active TCP socket to Koha). |
| `KOHA_SERVER_IP` | **Yes (in live)** | *None* | IP address or fully qualified domain name (FQDN) of the Koha SIP2 test server. |
| `KOHA_SERVER_PORT` | **Yes (in live)** | `6001` | TCP port where Koha's SIP2 daemon is listening (typically 6001). |
| `KOHA_USER` | **Yes (in live)** | *None* | SIP2 terminal account username defined in Koha's `SIPconfig.xml`. Also accepts legacy `SIP2_USERNAME`. |
| `KOHA_PASS` | **Yes (in live)** | *None* | SIP2 terminal account password defined in Koha's `SIPconfig.xml`. Also accepts legacy `SIP2_PASSWORD`. |
| `KOHA_LOCATION` | No | `NFSU` | Library branch or institution code matching Koha's `<institution id="...">`. |
| `KOHA_TERMINAL_PASSWORD` | No | *Empty* | Terminal password (tag `AC`) if required by Koha's `<policy>` or SIPconfig.xml. |
| `SIP2_ENCODING` | No | `utf-8` | Character encoding for SIP2 stream (`utf-8` or `ascii`). Must match Koha's `<encoding>` tag. |
| `SIP2_CHECK_SEQUENCE` | No | `false` | Whether to strictly enforce `AY` sequence numbers on incoming responses. Match Koha's `<sequence_check>`. |
| `SIP2_SOCKET_TIMEOUT` | No | `4.0` | Socket read/write connection timeout in seconds. |
| `SESSION_TIMEOUT_SECONDS` | No | `60` | Server-side patron session inactivity expiry in seconds. Client UI idle timer is 30s. |
| `HOST` | No | `127.0.0.1` | Local web server bind address for kiosk terminal. |
| `PORT` | No | `8050` | Local web server port for kiosk terminal. |


---

## 3. Koha Server-Side Prerequisites (SIPconfig.xml Checklist)

Before attempting connection from GRANTHSETU, the library systems administrator should inspect Koha's SIP2 server configuration file (typically located at `/etc/koha/sites/<instance>/SIPconfig.xml`):

### 3.1. Terminal Login Account
Verify that a `<login>` block exists for the kiosk terminal:
```xml
<login id="nfsu_kiosk"
       password="YourSecureKioskPassword"
       institution="NFSU"
       encoding="utf-8" />
```
- Set `KOHA_USER` to match `id`.
- Set `KOHA_PASS` to match `password`.
- Set `KOHA_LOCATION` to match `institution`.

### 3.2. Service Listener & Port
Verify that the SIP2 daemon is bound and listening:
```xml
<service port="6001"
         transport="tcp"
         protocol="SIP/2.00"
         timeout="60" />
```
- Confirm `KOHA_SERVER_PORT=6001`.
- Ensure host firewall (e.g. `ufw` or `iptables`) allows inbound TCP traffic on port 6001 from the kiosk terminal's IP address.

### 3.3. Character Encoding Configuration
Check the encoding declaration:
- If `<encoding>utf-8</encoding>`, set `SIP2_ENCODING=utf-8` in `.env`.
- If `<encoding>ascii</encoding>` or `<encoding>iso-8859-1</encoding>`, set `SIP2_ENCODING=ascii` in `.env`.

### 3.4. Sequence Checking
Check the sequence number setting:
- If `<sequence_check>no</sequence_check>` (Koha default), keep `SIP2_CHECK_SEQUENCE=false`.
- If `<sequence_check>yes</sequence_check>`, set `SIP2_CHECK_SEQUENCE=true`.

### 3.5. Daemon Status Verification
On the Koha test server, verify that the SIP2 daemon is running:
```bash
sudo koha-sip --status <instance>
# If stopped, start the service:
sudo koha-sip --start <instance>
```

---

## 4. Pre-Flight Verification via test_koha_connection.py

GRANTHSETU includes a dedicated, non-destructive connection testing tool: `test_koha_connection.py`.

### 4.1. Step 1: Create Configuration File
Copy `.env.example` to `.env`:
```bash
copy .env.example .env
```
Open `.env` in a text editor and fill in the test server parameters:
```ini
KOHA_MODE=live
KOHA_SERVER_IP=192.168.1.50
KOHA_SERVER_PORT=6001
KOHA_USER=nfsu_kiosk
KOHA_PASS=YourSecureKioskPassword
KOHA_LOCATION=NFSU
SIP2_ENCODING=utf-8
SIP2_CHECK_SEQUENCE=false
```

### 4.2. Step 2: Execute Pre-Flight Handshake
Run the connection test tool:
```bash
python test_koha_connection.py
```

### 4.3. Diagnostic Interpretation

#### Case A: Successful Authentication
```text
====================================================================
  GRANTHSETU -- Koha SIP2 Connection Test (LIVE)
====================================================================
  Target Server:      192.168.1.50:6001
  Location Code:      NFSU
  SIP2 Username:      nfsu_kiosk
  SIP2 Password:      [PROTECTED - NOT DISPLAYED]
  Encoding:           utf-8
  Check Sequence:     False
  Socket Timeout:     4.0s
--------------------------------------------------------------------
  Connecting and sending SIP2 93 Login Handshake...
  Status:             SUCCESS (HTTP 200 / SIP2 94 OK)
  Round-Trip Latency: 42.18 ms
  Result:             Koha ILMS successfully authenticated terminal credentials.
====================================================================
```
*Meaning*: Network connectivity, TCP handshake, credentials, and SIP2 94 response format are all verified. You may proceed to staged circulation testing.

#### Case B: Connection Refused
```text
  Status:             FAILED
  Round-Trip Latency: 3.12 ms
  Error:              Connection refused by Koha server at 192.168.1.50:6001. Verify that Koha SIP2 service is running.
```
*Remedy*: Confirm that `koha-sip` daemon is running on Koha server and that firewall port 6001 is open.

#### Case C: Connection Timed Out
```text
  Status:             FAILED
  Round-Trip Latency: 4002.51 ms
  Error:              Connection timed out (4.0s) reaching Koha server at 192.168.1.50:6001
```
*Remedy*: Check network routing, VPN tunnel, or campus subnet access rules between kiosk terminal and Koha server.

#### Case D: Login Rejected (SIP2 940)
```text
  Status:             FAILED
  Round-Trip Latency: 55.40 ms
  Error:              Login rejected by Koha ILMS (Invalid credentials or unauthorized terminal).
```
*Remedy*: Confirm `KOHA_USER` and `KOHA_PASS` match the `<login>` definition in `SIPconfig.xml`. Check Koha logs at `/var/log/koha/<instance>/sip-output.log`.

#### Case E: Sequence Number Mismatch
```text
  Status:             FAILED
  Error:              Login sequence mismatch: expected AY0, got AY...
```
*Remedy*: Check if Koha has sequence checking disabled (`<sequence_check>no</sequence_check>`). Set `SIP2_CHECK_SEQUENCE=false` in `.env`.

---

## 5. Phased Testing Protocol for Staging Environment

Once pre-flight handshake succeeds, follow this 4-phase testing procedure:

```
[Phase 1: Handshake Test]
       |
       v  (python test_koha_connection.py passes)
[Phase 2: Read-Only Lookups]
       |  - Patron query (SIP2 63/64): Verify patron name & borrowed count
       |  - Book query (SIP2 17/18): Verify book title & catalog metadata
       v
[Phase 3: Controlled Circulation]
       |  - Issue item (SIP2 11/12): Verify count updates & RFID disarmed
       |  - Limit guard: Attempt 3rd book -> Must block before network send
       |  - Return item (SIP2 09/10): Verify count decrements & RFID re-armed
       v
[Phase 4: Exit Gate RFID Verification]
          - Safe passage on disarmed item
          - Alarm siren on unissued/armed item
```

### Phase 2: Read-Only Lookups
1. Start kiosk backend:
   ```bash
   python -m uvicorn main:app --host 127.0.0.1 --port 8050
   ```
2. Navigate to `http://127.0.0.1:8050`.
3. Enter a test patron ID (e.g. `NFSU2024001`).
4. Verify that Koha returns the correct patron name and current borrowed books count without errors.

### Phase 3: Controlled Circulation
1. Issue a designated test book (e.g. barcode `BK-TEST-001`).
2. Verify that Koha circulation logs show the item issued to the test patron.
3. Verify that the kiosk UI updates the patron count.
4. Verify the 2-book limit guard: If the patron already holds 2 books, attempting to issue another must be rejected instantly by the kiosk with `FAILED_BEFORE_SEND`.
5. Return the test book via the kiosk return interface and verify Koha's check-in confirmation.

### Phase 4: Exit Gate Verification
1. Pass the checked-out item barcode through the exit gate tester (`POST /api/gate/test`).
2. Confirm safe passage (`AFI 0x07 / DISARMED`).
3. Pass an unissued item barcode through the exit gate tester.
4. Confirm security alarm triggers (`AFI 0xC2 / ARMED`).

---

## 7. Operator Reconciliation Protocol for UNKNOWN_AFTER_SEND

When a network drop, timeout, or TCP reset occurs after bytes were transmitted over the network to Koha, GRANTHSETU assigns the outcome `UNKNOWN_AFTER_SEND`.

### 7.1. Safety Invariants Enforced Automatically
1. **Zero Auto-Retry**: The kiosk software is programmatically prohibited from auto-retrying ambiguous checkouts to avoid duplicate loans or patron lockout.
2. **Tag Remains ARMED**: The book's ISO 15693 EAS tag remains set to `ARMED (AFI 0xC2)` so that passing the exit pedestals will trigger the alarm.
3. **Counts Unchanged**: Patron charged items count is NOT incremented without confirmed server ACK.

### 7.2. Standard Operating Procedure for Library Staff
When an `UNKNOWN_AFTER_SEND` dialog appears on the kiosk terminal:
```
+--------------------------------------------------------------------------------+
|                           STAFF RECONCILIATION WORKFLOW                        |
+--------------------------------------------------------------------------------+
  [1] Ask patron to step to the Circulation Desk with the book.
  [2] DO NOT re-attempt checkout on the self-service kiosk immediately.
  [3] Open Koha Staff Client -> Search Patron ID (e.g., NFSU2024001).
  [4] Inspect Patron Checkouts:
      a) If Koha shows the book checked out:
         - The transaction reached Koha before the connection broke.
         - Place book on the Staff RFID Pad and disarm tag (AFI 0x07).
         - Hand book to patron.
      b) If Koha does NOT show the book checked out:
         - The transaction did not commit on the server.
         - Check out the item manually in the Koha Staff Client.
         - Disarm tag on staff RFID pad.
  [5] Document incident in the Library Operations Reconciliation Log.
+--------------------------------------------------------------------------------+
```

---

## 8. Transaction Tray & Digital PDF Circulation Slips

### 8.1. Batch Tray Operations
GRANTHSETU supports multi-item checkout tray staging:
- **Pre-Send 2-Book Guard**: Evaluates `(charged_count + len(tray) + 1) > 2` before adding to tray.
- **Atomic Batch Processing**: Sequentially issues each book in the tray through Koha SIP2. If any item fails, execution halts cleanly for operator intervention.

### 8.2. Official PDF Circulation Slips
The kiosk generates encrypted digital PDF receipts via `ReportLab`:
- **Masked Privacy**: Patron IDs are masked according to ISO/IEC 29100 (`NFSU****001`).
- **Cryptographic Fingerprint**: Every slip includes a 64-character SHA-256 tamper-evident fingerprint (`TX_ID:PATRON_ID:MODE:COUNT:TIMESTAMP:KOHA_MODE`).
- **Watermark Classification**: Offline demonstrations print `*** SIMULATED RECORD (OFFLINE DEMO) ***`, while production issues print `*** OFFICIAL KOHA 26 RECORD ***`.

---

## 9. Hardware Abstraction Layer (HAL) Configuration

GRANTHSETU separates hardware peripheral drivers from simulation fallbacks via the `hardware/` subsystem:

| Device | Interface | Physical Standard / Driver | Fallback Behavior |
| :--- | :--- | :--- | :--- |
| **Barcode Scanner** | USB HID / RS-232 | Standard Keyboard Wedge or `pyserial` VCP | High-fidelity regex-validated simulated scanner |
| **Smart Card Reader** | PC/SC USB | ISO 14443-A MIFARE Classic / DESFire (`pyscard` APDU `FF CA 00 00 00`) | Mapped institutional student test cards |
| **RFID Antenna Pad** | USB / RS-232 | ISO 15693 / ISO 18000-3 Mode 1 (13.56 MHz HF, ISO 28560 AFI byte) | In-memory & JSON state persistence |
| **Exit Security Gate** | Digital I/O | Dual RFID EAS Pedestals monitoring AFI 0xC2 / 0x07 | Simulated gate corridor with audio siren |

To force physical hardware detection in `.env`:
```ini
HARDWARE_MODE=physical
BARCODE_SCANNER_MODE=usb_hid
SMART_CARD_MODE=pcsc
RFID_READER_MODE=feig_usb
```

---

*Document Version: 2.1.0*  
*National Forensic Sciences University -- School of Cyber Security & Digital Forensics*

