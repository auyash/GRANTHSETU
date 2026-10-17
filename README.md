# GRANTHSETU (ग्रन्थसेतु)
### AI-Powered Self-Service Library Kiosk for NFSU Central Library
**Integrated with Koha 26 ILMS via 3M SIP2 Protocol**

[![Python 3.11+](https://img.shields.io/badge/Python-3.11+-blue.svg)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-Framework-009688.svg)](https://fastapi.tiangolo.com/)
[![Koha 26 ILMS](https://img.shields.io/badge/Koha%20ILMS-Version%2026-green.svg)](https://koha-community.org/)
[![SIP2 Protocol](https://img.shields.io/badge/Protocol-3M%20SIP2-orange.svg)](https://en.wikipedia.org/wiki/Standard_Interchange_Protocol)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

---

## 🏛️ Background & Purpose

Traditional library circulation processes at the **National Forensic Sciences University (NFSU)** require manual staff assistance for book issuance, returns, and renewals, leading to circulation bottlenecks and waiting lines.

**GRANTHSETU** is an institutional self-service library kiosk designed to automate circulation while safeguarding university materials. Powered by **Koha 26**, **MIFARE smart cards**, **barcode scanners**, and **RFID security tags**, GRANTHSETU enforces strict circulation limits, anti-theft gate security, and zero-retention biometric verification compliant with forensic data protection standards.

---

## 🌟 Key Features

### 1. Authentic NFSU Kiosk UI
- Styled to match NFSU's institutional visual identity:
  - Official **NFSU Campus Backdrop** & **Emblem**.
  - 3 Patron Authentication Modes: **Face Recognition**, **MIFARE Smart Card**, **Patron Barcode**.
  - 3 Primary Circulation Cards: **ISSUE**, **RETURN**, **RENEW**.
  - Dynamic **Books Borrowed Status Badge** with real-time slot counter.

### 2. Koha 26 SIP2 Integration
- Native TCP socket communication with Koha 26 SIP2 server:
  - Configurable server parameters loaded securely via environment variables (see `.env.example`).
  - Zero hardcoded credentials or server secrets committed to version control.
- Fully compliant with 3M SIP2 standard messages:
  - `93 / 94`: Login handshake & heartbeat
  - `63 / 64`: Patron verification, status & charged items lookup
  - `17 / 18`: Book information & catalog retrieval
  - `11 / 12`: Item Checkout (Issue)
  - `09 / 10`: Item Checkin (Return)
  - `29 / 30`: Item Renewal
  - `35 / 36`: End patron session
- **Zero-Downtime Failover**: Includes an internal high-fidelity Mock SIP2 Server so the kiosk functions seamlessly during offline demonstrations or when outside the campus network.

### 3. Strict 2-Book Borrowing Limit Guard
- University circulation rules allow a **maximum of 2 books** per patron.
- Dynamically evaluated from Koha SIP2 `64` message field `BK`:
  - `0 / 2`: 2 Slots Available (Green)
  - `1 / 2`: 1 Slot Available (Amber)
  - `2 / 2`: **Limit Reached (Red Alert)**
- If a patron attempts to issue a 3rd book, the transaction is immediately blocked, a warning banner is shown, and a 1-click shortcut directs the user to the **Return** workflow.

### 4. RFID EAS Security & Exit Gate Alarm
- **EAS / AFI Security Bits (ISO 28560)**:
  - `0xC2 (Armed)`: Default state for library inventory. If passed through exit pedestals, triggers the security alarm!
  - `0x07 (Disarmed)`: Applied **only after Koha confirms successful checkout** (`SIP2 12 OK`). Safe to leave library.
  - Automatically re-arms to `0xC2` upon book return (`SIP2 10 OK`).
- **Interactive Exit Gate Simulator**:
  - Test safe passage with legitimately checked-out items.
  - Test unauthorized/unissued items triggering the **🚨 Loud Acoustic Siren & Flashing Red Strobe Alarm**!

### 5. Forensic Privacy & Security Demonstration
- **Zero-Retention Biometrics**: Live webcam frames are analyzed in ephemeral RAM for liveness and anti-spoofing; raw frames are immediately purged from memory (**0 bytes stored on disk**).
- **Packet Integrity**: All SIP2 communication is validated using standard CRC-16 `AZ` checksums.
- **Inactivity Session Auto-Purge**: 30-second idle countdown automatically ends patron sessions and wipes session memory.
- **Forensic Audit Dashboard**: Real-time cryptographic SHA-256 chained audit logs demonstrating policy enforcement and biometric compliance to judges and evaluators.

---

## 🚀 Quick Start (Local Run)

### Prerequisites
- Python 3.9+ installed

### Environment Configuration
Copy `.env.example` to `.env` to configure your environment:
```bash
copy .env.example .env
```
Key configuration parameters:
- `KOHA_MODE`: `mock` for standalone offline simulation (default), `live` for Koha socket connection.
- `KOHA_SERVER_IP`, `KOHA_SERVER_PORT`: Koha SIP2 server network address.
- `KOHA_USER`, `KOHA_PASS`: Koha SIP2 credentials (also accepts `SIP2_USERNAME`, `SIP2_PASSWORD`).
- `KOHA_LOCATION`: Koha library location code (default: `NFSU`).
- `SIP2_ENCODING`: Character stream encoding (default: `utf-8`).
- `SIP2_CHECK_SEQUENCE`: Strict AY sequence validation (`false` by default).
- `SESSION_TIMEOUT_SECONDS`: Server-side inactivity timeout (default: `60`).
- `HOST`, `PORT`: Web server binding (default: `127.0.0.1:8050`).

### Koha Test-Server Pre-Flight Verification
Before starting the kiosk in `live` mode, test the connection safely without risking circulation state or exposing credentials:
```bash
python test_koha_connection.py
```
For detailed instructions on configuring Koha's `SIPconfig.xml`, verifying character encoding, and enabling sequence checking, refer to the [Koha Administration Integration Guide](docs/KOHA_ADMIN_INTEGRATION_GUIDE.md).

### 1-Click Launch (Windows)
Double-click `start_kiosk.bat` or run in terminal:
```bash
pip install -r requirements.txt
uvicorn main:app --host 127.0.0.1 --port 8050 --reload
```
Open your browser at: **`http://localhost:8050`** (Press `F11` for Fullscreen Touch Kiosk Mode).

---

## 🌐 24/7 Cloud Deployment (Runs Even When Laptop is Closed)

To ensure evaluators or kiosk terminals can access the system 24/7 without keeping your personal laptop awake:

### Option A: Deploy to Render.com (Free Tier)
1. Commit and push your code to your GitHub repo (`https://github.com/auyash/GRANTHSETU`).
2. Go to [Render.com](https://render.com) and sign in.
3. Click **New +** $\rightarrow$ **Blueprint** (or **Web Service**).
4. Select your `GRANTHSETU` repository. Render will automatically read [`render.yaml`](render.yaml) and deploy the app.
5. Your kiosk will be live at `https://granthsetu.onrender.com` 24/7!

### Option B: Docker Container
```bash
docker build -t granthsetu-kiosk .
docker run -p 8000:8000 granthsetu-kiosk
```

---

## 🧪 Demonstration Guide for Evaluators

| Step | Action | Expected Result |
| :--- | :--- | :--- |
| **1. Patron Login** | Click **"Tap Card: Priya (1/2 Books)"** or open **Face Recognition** | Authenticates, shows patron metadata and `Borrowed: 1 / 2 Books (1 Slot Available)`. |
| **2. Issue Book** | Click **"BK-TOX-501 (Toxicology)"** | Koha SIP2 11 issues item $\rightarrow$ RFID tag disarmed (`AFI 0x07`) $\rightarrow$ Counter becomes `2 / 2 (Limit Reached)`. |
| **3. Test Limit Guard** | Attempt to issue another book (e.g. `BK-DNA-601`) | **BLOCKED!** Red alert banner displays: *"Borrowing limit reached (2/2 books). Please return a book first."* |
| **4. Test Exit Gate (Safe)** | Click **"Test Exit Gate"** $\rightarrow$ **"Test Checked-Out Book (Safe)"** | RFID Pedestals glow green: `✅ SAFE PASSAGE: Item is DISARMED (AFI 0x07)`. |
| **5. Test Exit Gate (Alarm!)** | Click **"Test Stolen / Unissued Book (Alarm 🚨)"** | **🚨 LOUD SIREN SOUNDS!** Pedestals flash red strobe: `SECURITY BREACH: Unauthorized book detected!`. |
| **6. Return Book** | Click **"Go to Returns"** and process `BK-TOX-501` | Koha SIP2 09 checks in item $\rightarrow$ Tag re-arms to `ARMED (0xC2)` $\rightarrow$ Patron counter drops back to `1/2`. |
| **7. Security Audit** | Click **"Security Audit"** in top bar | Demonstrates **0 Bytes** Biometric Disk Persistence, CRC-16 Checksum, and SHA-256 Audit Chain. |

---

## 📂 Repository Structure

```text
GRANTHSETU/
├── Dockerfile                  # Containerization for 24/7 cloud hosting
├── render.yaml                 # Render.com cloud deployment config
├── requirements.txt            # Python dependencies (FastAPI, ReportLab, Pillow)
├── start_kiosk.bat             # 1-click Windows launch script (port 8050)
├── test_koha_connection.py     # Safe non-destructive Koha SIP2 connection tester
├── main.py                     # FastAPI backend server with CORS & input sanitization
├── kiosk_engine.py             # 2-book limit, tray management & RFID circulation controller
├── README.md                   # Project documentation
├── docs/
│   └── KOHA_ADMIN_INTEGRATION_GUIDE.md  # Library admin Koha setup & staging protocol
├── hardware/                   # Modular Hardware Abstraction Layer (HAL)
│   ├── base.py                 # Abstract hardware adapters and device status contracts
│   ├── barcode_scanner.py      # USB HID, Serial RS-232, and high-fidelity simulation
│   ├── card_reader.py          # PC/SC USB contactless smart card reader & APDU commands
│   ├── rfid_antenna.py         # ISO 15693 / ISO 28560 HF RFID antenna pad & exit gate
│   └── manager.py              # Central peripheral coordinator & status diagnostics
├── services/
│   └── receipt_generator.py    # ReportLab digital PDF circulation slip compiler
├── security/
│   ├── audit.py                # SHA-256 tamper-evident security audit logger
│   └── biometrics.py           # Ephemeral face liveness & zero-retention engine
├── sip2/
│   ├── protocol.py             # 3M SIP2 packet builder, parser & CRC-16 checksum
│   ├── client.py               # Configurable TCP socket client for Koha SIP2
│   └── mock_server.py          # Resilient offline failover SIP2 server
├── static/
│   ├── index.html              # Touchscreen kiosk frontend with tray & HAL diagnostics
│   ├── css/
│   │   └── styles.css          # NFSU-branded glassmorphic UI stylesheet
│   ├── js/
│   │   └── app.js              # Tray manager, Web Audio synthesizer, gate tester & timer
│   └── images/
│       ├── nfsu_campus.jpg     # Official NFSU campus building backdrop
│       └── nfsu_logo.png       # Official NFSU emblem and banner
└── tests/
    ├── test_api_routes.py         # FastAPI REST route integration tests
    ├── test_hardware_adapters.py  # HAL peripheral adapters & simulation tests
    ├── test_biometrics.py         # Consent enforcement & 0-byte disk retention tests
    ├── test_receipts_and_tray.py  # Tray limits, batch issue & PDF generator tests
    ├── test_new_api_routes.py     # Health, HAL, and digital receipt endpoint tests
    ├── test_koha_integration.py   # Koha connection, handshake & safety tests
    ├── test_sip2_protocol.py      # SIP2 message building, parsing & checksums
    ├── test_stream_framing.py     # TCP framing & chunk reassembly tests
    └── test_transaction_safety.py # 2-book limit, inactivity timeout & RFID tests
```

---

## ⚖️ License
Developed for the National Forensic Sciences University (NFSU). Distributed under the MIT License.
