"""
NFSU Self-Service Library Kiosk — Main Application Server
=============================================================================
FastAPI Backend delivering:
- Koha 26 SIP2 Integration via 3M SIP2 Protocol
- Strict 2-Book Borrowing Limit Guard
- RFID Tag Security & Exit Gate Alarm Logic
- Biometric Face Authentication with Liveness & Zero-Retention
- Hardware Abstraction Layer (HAL) for Peripherals
- Active Transaction Tray Management
- Digital Thermal PDF Receipt Generation
- Forensic Security & Compliance Dashboard
=============================================================================
"""

import os
from datetime import datetime
from pathlib import Path
from typing import Optional, List
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from kiosk_engine import kiosk_controller
from security.biometrics import face_engine, BiometricConsentError
from security.audit import audit_logger
from hardware.manager import hardware_manager

app = FastAPI(title="NFSU Library Self-Check Kiosk", version="2.0")

# Enable CORS for local touch terminal interfaces
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Setup Paths
BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
IMAGES_DIR = STATIC_DIR / "images"
IMAGES_DIR.mkdir(parents=True, exist_ok=True)


# Pydantic Request Models with Input Sanitization
class PatronAuthRequest(BaseModel):
    patron_id: str = Field(..., min_length=1, max_length=64)
    method: str = "CARD"  # "CARD" | "BARCODE" | "MANUAL"


class FaceAuthRequest(BaseModel):
    image_base64: Optional[str] = None
    simulate_liveness: bool = True
    mock_choice: Optional[str] = "FACE_01"
    user_consent: bool = True


class CirculationRequest(BaseModel):
    barcode: str = Field(..., min_length=1, max_length=64)


class GateTestRequest(BaseModel):
    barcode: str = Field(..., min_length=1, max_length=64)


class TrayItemRequest(BaseModel):
    barcode: str = Field(..., min_length=1, max_length=64)


class ReceiptRequest(BaseModel):
    circulation_mode: str = "ISSUE"


# ---------------------------------------------------------------------------
# Health & Status Endpoints
# ---------------------------------------------------------------------------

@app.get("/api/health")
def get_health():
    """Liveness probe for monitoring, container orchestrators, and deployment."""
    return {
        "status": "ok",
        "service": "GRANTHSETU",
        "version": "2.0",
        "koha_mode": kiosk_controller.mode,
        "is_connected": kiosk_controller.sip_client.is_connected,
        "timestamp": datetime.now().isoformat(),
    }


@app.get("/api/kiosk/status")
def get_kiosk_status():
    """Returns current system health, Koha connection, active session, and tray count."""
    kiosk_controller.check_session_expiry()
    return {
        "active_patron": kiosk_controller.active_patron,
        "koha_status": kiosk_controller.sip_client.get_status(),
        "max_borrow_limit": kiosk_controller.MAX_BORROW_LIMIT,
        "rfid_tags": kiosk_controller.rfid_tag_registry,
        "tray_count": len(kiosk_controller.session_tray),
    }


@app.post("/api/koha/test-connection")
def test_koha_connection():
    """
    Explicitly tests connection and SIP2 login handshake to Koha.
    Only attempts live network transmission when KOHA_MODE=live.
    Returns diagnostics without exposing passwords or sensitive patron data.
    """
    res = kiosk_controller.test_live_connection()
    status_code = 200 if (res.get("success") or res.get("tested") is False) else 503
    return JSONResponse(status_code=status_code, content=res)


@app.get("/api/hardware/status")
def get_hardware_status():
    """Returns status and diagnostic details for all connected or simulated hardware devices."""
    return hardware_manager.get_hardware_status()


@app.get("/api/biometrics/status")
def get_biometrics_status():
    """Returns compliance status of forensic zero-retention biometric engine."""
    return face_engine.get_biometric_status()


# ---------------------------------------------------------------------------
# Authentication Endpoints
# ---------------------------------------------------------------------------

@app.post("/api/auth/patron")
def authenticate_patron(req: PatronAuthRequest):
    """Authenticates patron via MIFARE Card or Barcode and queries Koha SIP2."""
    res = kiosk_controller.authenticate_patron(req.patron_id, req.method)
    if not res.get("success"):
        raise HTTPException(status_code=400, detail=res.get("message"))
    return res


@app.post("/api/auth/face")
def authenticate_face(req: FaceAuthRequest):
    """
    Performs AI Facial Authentication with Liveness detection.
    Enforces user consent and strict volatile RAM zero-retention (0 bytes on disk).
    """
    try:
        bio_res = face_engine.verify_face_stream(
            base64_image=req.image_base64,
            simulate_liveness=req.simulate_liveness,
            mock_choice=req.mock_choice,
            user_consent=req.user_consent,
        )
    except BiometricConsentError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Biometric processing failed: {e}")

    if not bio_res.get("success"):
        raise HTTPException(status_code=401, detail="Liveness verification failed or face not recognized.")

    # Authenticate matched patron in circulation engine
    auth_res = kiosk_controller.authenticate_patron(bio_res["patron_id"], auth_method="FACE_BIOMETRIC")
    return {
        "biometrics": bio_res,
        "patron_session": auth_res
    }


# ---------------------------------------------------------------------------
# Item Catalog & Transaction Tray Endpoints
# ---------------------------------------------------------------------------

@app.get("/api/items/lookup")
def lookup_item(barcode: str):
    """Queries book details from Koha SIP2 (message 17)."""
    return kiosk_controller.get_item_details(barcode)


@app.get("/api/tray")
def get_transaction_tray():
    """Returns pre-checkout tray contents for current patron session."""
    return kiosk_controller.get_tray()


@app.post("/api/tray/add")
def add_to_tray(req: TrayItemRequest):
    """Adds a validated book to the pre-checkout tray, enforcing the 2-book limit."""
    res = kiosk_controller.add_to_tray(req.barcode)
    if not res.get("success"):
        return JSONResponse(status_code=400, content=res)
    return res


@app.post("/api/tray/remove")
def remove_from_tray(req: TrayItemRequest):
    """Removes a book from the pre-checkout tray."""
    res = kiosk_controller.remove_from_tray(req.barcode)
    if not res.get("success"):
        return JSONResponse(status_code=404, content=res)
    return res


@app.post("/api/tray/clear")
def clear_tray():
    """Clears all items from the pre-checkout tray."""
    return kiosk_controller.clear_tray()


@app.post("/api/tray/checkout")
def checkout_tray():
    """Processes sequential checkout for all books in the transaction tray."""
    res = kiosk_controller.checkout_tray()
    if not res.get("success"):
        return JSONResponse(status_code=400, content=res)
    return res


# ---------------------------------------------------------------------------
# Circulation Operations
# ---------------------------------------------------------------------------

@app.post("/api/circulation/checkout")
def checkout_item(req: CirculationRequest):
    """Issues book via Koha SIP2 (11), enforcing the 2-book limit and disarming EAS."""
    res = kiosk_controller.checkout_item(req.barcode)
    if not res.get("success"):
        return JSONResponse(status_code=400, content=res)
    return res


@app.post("/api/circulation/checkin")
def checkin_item(req: CirculationRequest):
    """Returns book via Koha SIP2 (09) and re-arms EAS tag."""
    res = kiosk_controller.checkin_item(req.barcode)
    if not res.get("success"):
        return JSONResponse(status_code=400, content=res)
    return res


@app.post("/api/circulation/renew")
def renew_item(req: CirculationRequest):
    """Renews item via Koha SIP2 (29)."""
    res = kiosk_controller.renew_item(req.barcode)
    if not res.get("success"):
        return JSONResponse(status_code=400, content=res)
    return res


@app.post("/api/circulation/receipt")
def get_circulation_receipt(req: ReceiptRequest):
    """Returns structured JSON receipt with cryptographic verification fingerprint."""
    res = kiosk_controller.generate_receipt(circulation_mode=req.circulation_mode)
    if not res.get("success"):
        return JSONResponse(status_code=400, content=res)
    return res


@app.get("/api/circulation/receipt/pdf")
def get_circulation_receipt_pdf(mode: str = "ISSUE"):
    """Streams in-memory official PDF receipt document."""
    pdf_bytes = kiosk_controller.generate_receipt_pdf(circulation_mode=mode)
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": "inline; filename=nfsu_library_receipt.pdf"},
    )


# ---------------------------------------------------------------------------
# Security, Exit Gate & Session Endpoints
# ---------------------------------------------------------------------------

@app.post("/api/gate/test")
def test_gate_sensor(req: GateTestRequest):
    """Simulates item passing through RFID exit gate to trigger or suppress alarm."""
    return kiosk_controller.test_exit_gate(req.barcode)


@app.post("/api/session/end")
def end_session():
    """Logs out patron, sends SIP2 message 35, and wipes session memory."""
    return kiosk_controller.end_session()


@app.get("/api/security/audit")
def get_security_audit():
    """Returns cryptographic audit trail and zero-retention compliance metrics."""
    return audit_logger.get_security_report()


# ---------------------------------------------------------------------------
# Static Frontend Mount & Root Index
# ---------------------------------------------------------------------------

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/")
def serve_index():
    return FileResponse(STATIC_DIR / "index.html")
