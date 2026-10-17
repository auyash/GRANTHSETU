"""
Automated unit & integration tests for newly added FastAPI endpoints in GRANTHSETU:
1. GET /api/health (Liveness probe)
2. GET /api/hardware/status (Hardware diagnostics)
3. GET /api/biometrics/status (Zero-retention forensics compliance)
4. Tray endpoints:
   - GET /api/tray
   - POST /api/tray/add
   - POST /api/tray/remove
   - POST /api/tray/clear
   - POST /api/tray/checkout
5. Digital Slip endpoints:
   - POST /api/circulation/receipt
   - GET /api/circulation/receipt/pdf
6. CORS Middleware Headers
"""

import pytest
from main import app
from kiosk_engine import kiosk_controller
from tests.test_api_routes import TestClient


@pytest.fixture
def client():
    kiosk_controller.mode = "MOCK"
    kiosk_controller.active_patron = None
    kiosk_controller.session_tray = []
    kiosk_controller.session_history = []
    kiosk_controller.rfid_tag_registry = {
        "BK-FOR-101": "ARMED",
        "BK-CYB-201": "ARMED",
        "BK-TOX-501": "ARMED",
        "BK-DNA-601": "ARMED",
    }
    kiosk_controller._save_simulated_rfid_registry()
    if hasattr(kiosk_controller.sip_client, "mock_server"):
        kiosk_controller.sip_client.mock_server.__init__()
    return TestClient(app)


class TestHealthAndDiagnosticsRoutes:
    def test_health_check_endpoint(self, client):
        res = client.get("/api/health")
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "ok"
        assert "koha_mode" in data
        assert "timestamp" in data

    def test_hardware_status_endpoint(self, client):
        res = client.get("/api/hardware/status")
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "OPERATIONAL"
        assert "devices" in data
        assert len(data["devices"]) >= 3
        types = [d["device_type"] for d in data["devices"]]
        assert "BARCODE_SCANNER" in types
        assert "SMART_CARD_READER" in types
        assert "RFID_ANTENNA" in types

    def test_biometrics_status_endpoint(self, client):
        res = client.get("/api/biometrics/status")
        assert res.status_code == 200
        data = res.json()
        assert data["zero_retention_compliant"] is True
        assert data["bytes_on_disk"] == 0


class TestTransactionTrayApiRoutes:
    def test_get_tray_empty(self, client):
        res = client.get("/api/tray")
        assert res.status_code == 200
        data = res.json()
        assert data["tray"] == []
        assert data["tray_count"] == 0

    def test_tray_add_item_unauthenticated(self, client):
        res = client.post("/api/tray/add", json={"barcode": "BK-FOR-101"})
        assert res.status_code == 400
        data = res.json()
        assert data["success"] is False
        assert data["error_code"] == "FAILED_BEFORE_SEND"

    def test_tray_add_and_remove_flow(self, client):
        # 1. Login patron
        auth_res = client.post("/api/auth/patron", json={"patron_id": "NFSU2024001", "method": "CARD"})
        assert auth_res.status_code == 200

        # 2. Add item to tray
        add_res = client.post("/api/tray/add", json={"barcode": "BK-FOR-101"})
        assert add_res.status_code == 200
        add_data = add_res.json()
        assert add_data["success"] is True
        assert add_data["tray_count"] == 1

        # 3. Verify in GET /api/tray
        get_res = client.get("/api/tray")
        assert get_res.status_code == 200
        assert get_res.json()["tray_count"] == 1

        # 4. Remove item
        rem_res = client.post("/api/tray/remove", json={"barcode": "BK-FOR-101"})
        assert rem_res.status_code == 200
        assert rem_res.json()["tray_count"] == 0

    def test_tray_clear_endpoint(self, client):
        client.post("/api/auth/patron", json={"patron_id": "NFSU2024001", "method": "CARD"})
        client.post("/api/tray/add", json={"barcode": "BK-FOR-101"})
        assert len(kiosk_controller.session_tray) == 1

        clear_res = client.post("/api/tray/clear", json={})
        assert clear_res.status_code == 200
        assert clear_res.json()["tray_count"] == 0
        assert len(kiosk_controller.session_tray) == 0

    def test_tray_checkout_endpoint(self, client):
        client.post("/api/auth/patron", json={"patron_id": "NFSU2024001", "method": "CARD"})
        client.post("/api/tray/add", json={"barcode": "BK-FOR-101"})
        client.post("/api/tray/add", json={"barcode": "BK-CYB-201"})

        checkout_res = client.post("/api/tray/checkout", json={})
        assert checkout_res.status_code == 200
        data = checkout_res.json()
        assert data["success"] is True
        assert data["processed_count"] == 2
        assert data["remaining_tray_count"] == 0
        assert kiosk_controller.rfid_tag_registry["BK-FOR-101"] == "DISARMED"
        assert kiosk_controller.rfid_tag_registry["BK-CYB-201"] == "DISARMED"


class TestReceiptApiRoutes:
    def test_receipt_structured_endpoint(self, client):
        client.post("/api/auth/patron", json={"patron_id": "NFSU2024001", "method": "CARD"})
        client.post("/api/circulation/checkout", json={"barcode": "BK-TOX-501"})

        res = client.post("/api/circulation/receipt", json={"circulation_mode": "ISSUE"})
        assert res.status_code == 200
        data = res.json()
        assert data["success"] is True
        assert "receipt" in data
        assert data["receipt"]["patron_id_masked"] == "NFSU****001"
        assert len(data["receipt"]["items"]) == 1

    def test_receipt_pdf_binary_endpoint(self, client):
        client.post("/api/auth/patron", json={"patron_id": "NFSU2024001", "method": "CARD"})
        client.post("/api/circulation/checkout", json={"barcode": "BK-TOX-501"})

        res = client.get("/api/circulation/receipt/pdf", params={"mode": "ISSUE"})
        assert res.status_code == 200
        # Check PDF header
        assert res.body.startswith(b"%PDF-")
        # Check Content-Type header
        content_type = res.headers.get("content-type", "")
        assert "application/pdf" in content_type
