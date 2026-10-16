"""
NFSU Self-Service Library Kiosk - Main Application Server
FastAPI Backend delivering:
- Koha 26 SIP2 Integration (117.239.182.15:6001)
- Strict 2-Book Borrowing Limit Guard
- RFID Tag Security & Exit Gate Alarm Logic
- Biometric Face Authentication with Liveness & Zero-Retention
- Forensic Security & Compliance Dashboard
"""

import os
import shutil
from pathlib import Path
from typing import Optional
from fastapi import FastAPI, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from kiosk_engine import kiosk_controller
from security.biometrics import face_engine
from security.audit import audit_logger

app = FastAPI(title="NFSU Library Self-Check Kiosk", version="2.0")

# Setup Paths
BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
IMAGES_DIR = STATIC_DIR / "images"
IMAGES_DIR.mkdir(parents=True, exist_ok=True)



# Pydantic Request Models
class PatronAuthRequest(BaseModel):
    patron_id: str
    method: str = "CARD"  # "CARD" | "BARCODE" | "MANUAL"

class FaceAuthRequest(BaseModel):
    image_base64: Optional[str] = None
    simulate_liveness: bool = True
    mock_choice: Optional[str] = "FACE_01"

class CirculationRequest(BaseModel):
    barcode: str

class GateTestRequest(BaseModel):
    barcode: str


# REST Endpoints
@app.get("/api/kiosk/status")
def get_kiosk_status():
    """Returns current system health, Koha connection, and active session."""
    return {
        "active_patron": kiosk_controller.active_patron,
        "koha_status": kiosk_controller.sip_client.get_status(),
        "max_borrow_limit": kiosk_controller.MAX_BORROW_LIMIT,
        "rfid_tags": kiosk_controller.rfid_tag_registry,
    }


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
    Zero image persistence (frame is wiped from RAM immediately).
    """
    bio_res = face_engine.verify_face_stream(
        base64_image=req.image_base64,
        simulate_liveness=req.simulate_liveness,
        mock_choice=req.mock_choice
    )
    if not bio_res.get("success"):
        raise HTTPException(status_code=401, detail="Liveness verification failed or face not recognized.")

    # Authenticate matched patron in circulation engine
    auth_res = kiosk_controller.authenticate_patron(bio_res["patron_id"], auth_method="FACE_BIOMETRIC")
    return {
        "biometrics": bio_res,
        "patron_session": auth_res
    }


@app.get("/api/items/lookup")
def lookup_item(barcode: str):
    """Queries book details from Koha SIP2 (message 17)."""
    return kiosk_controller.get_item_details(barcode)


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


@app.post("/api/gate/test")
def test_gate_sensor(req: GateTestRequest):
    """Simulates item passing through RFID exit gate to trigger or suppress alarm."""
    return kiosk_controller.test_exit_gate(req.barcode)


@app.post("/api/session/end")
def end_session():
    """Logs out patron and sends SIP2 message 35."""
    return kiosk_controller.end_session()


@app.get("/api/security/audit")
def get_security_audit():
    """Returns cryptographic audit trail and zero-retention compliance metrics."""
    return audit_logger.get_security_report()


# Mount Static UI
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/")
def serve_index():
    return FileResponse(STATIC_DIR / "index.html")
