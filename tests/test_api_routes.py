"""
Automated Route Tests for FastAPI REST Endpoints in GRANTHSETU.
Covers:
- GET /api/kiosk/status
- POST /api/auth/patron (Success, Denial, 422 Validation Error)
- POST /api/auth/face (Liveness Success, 401 Liveness Failure)
- GET /api/items/lookup (Success, 422 Missing Parameter)
- POST /api/circulation/checkout (Unauthenticated Denial, Limit Exceeded Denial, Success & EAS Disarm, 422 Validation)
- POST /api/circulation/checkin (Success & EAS Arm, 400 Empty Barcode, 422 Validation)
- POST /api/circulation/renew (Unauthenticated Denial, Authenticated Success)
- POST /api/gate/test (Armed Alarm, Disarmed Safe Passage, 422 Validation)
- POST /api/session/end (Clean Logout)
- GET /api/security/audit (Audit Trail & Zero-Retention Metrics)
- GET / (Index UI HTML)
"""

import asyncio
import json
import urllib.parse
from datetime import datetime, timedelta
import pytest
from main import app
from kiosk_engine import kiosk_controller

# Build or import TestClient
try:
    from starlette.testclient import TestClient
except (ImportError, ModuleNotFoundError, RuntimeError):
    class ASGIResponse:
        def __init__(self, status_code: int, headers: dict, body: bytes):
            self.status_code = status_code
            self.headers = headers
            self.body = body

        def json(self):
            return json.loads(self.body.decode("utf-8"))

        @property
        def text(self):
            return self.body.decode("utf-8")

    class TestClient:
        __test__ = False
        def __init__(self, asgi_app):
            self.app = asgi_app

        def request(self, method: str, path: str, headers: dict = None, json_data: dict = None, params: dict = None):
            if params:
                query_string = urllib.parse.urlencode(params).encode("utf-8")
            elif "?" in path:
                path, q = path.split("?", 1)
                query_string = q.encode("utf-8")
            else:
                query_string = b""

            body_bytes = b""
            req_headers = []
            if headers:
                for k, v in headers.items():
                    req_headers.append((k.lower().encode("latin-1"), v.encode("latin-1")))

            if json_data is not None:
                body_bytes = json.dumps(json_data).encode("utf-8")
                req_headers.append((b"content-type", b"application/json"))
                req_headers.append((b"content-length", str(len(body_bytes)).encode("latin-1")))

            scope = {
                "type": "http",
                "asgi": {"version": "3.0"},
                "http_version": "1.1",
                "method": method.upper(),
                "scheme": "http",
                "path": path,
                "raw_path": path.encode("ascii"),
                "query_string": query_string,
                "headers": req_headers,
                "client": ("127.0.0.1", 12345),
                "server": ("testserver", 80),
            }

            response_status = 200
            response_headers = []
            response_body = bytearray()
            req_body_sent = False
            response_complete = asyncio.Event()

            async def receive():
                nonlocal req_body_sent
                if not req_body_sent:
                    req_body_sent = True
                    return {"type": "http.request", "body": body_bytes, "more_body": False}
                await response_complete.wait()
                return {"type": "http.disconnect"}

            async def send(message):
                nonlocal response_status, response_headers, response_body
                if message["type"] == "http.response.start":
                    response_status = message["status"]
                    response_headers = message.get("headers", [])
                elif message["type"] == "http.response.body":
                    response_body.extend(message.get("body", b""))
                    if not message.get("more_body", False):
                        response_complete.set()

            async def run():
                await self.app(scope, receive, send)

            asyncio.run(run())
            norm_headers = {}
            for hk, hv in response_headers:
                k_str = hk.decode("latin-1").lower() if isinstance(hk, bytes) else str(hk).lower()
                v_str = hv.decode("latin-1") if isinstance(hv, bytes) else str(hv)
                norm_headers[k_str] = v_str
            return ASGIResponse(response_status, norm_headers, bytes(response_body))

        def get(self, path: str, params: dict = None, headers: dict = None):
            return self.request("GET", path, headers=headers, params=params)

        def post(self, path: str, json: dict = None, headers: dict = None, params: dict = None):
            return self.request("POST", path, headers=headers, json_data=json, params=params)


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture(autouse=True)
def reset_kiosk_state():
    """Ensure test isolation by resetting kiosk session and tag registry states."""
    import os
    state_file = "data/simulated_rfid_state.json"
    default_registry = {
        "BK-FOR-101": "DISARMED",
        "BK-CYB-201": "DISARMED",
        "BK-DIG-301": "DISARMED",
        "BK-BAL-401": "DISARMED",
        "BK-TOX-501": "ARMED",
        "BK-DNA-601": "ARMED",
    }
    kiosk_controller.active_patron = None
    kiosk_controller.session_started_at = None
    kiosk_controller.last_activity_at = None
    kiosk_controller.rfid_tag_registry = dict(default_registry)
    if os.path.exists(state_file):
        try:
            os.remove(state_file)
        except OSError:
            pass
    yield
    kiosk_controller.active_patron = None
    kiosk_controller.session_started_at = None
    kiosk_controller.last_activity_at = None
    kiosk_controller.rfid_tag_registry = dict(default_registry)
    if os.path.exists(state_file):
        try:
            os.remove(state_file)
        except OSError:
            pass


class TestStatusRoutes:
    def test_get_kiosk_status(self, client):
        res = client.get("/api/kiosk/status")
        assert res.status_code == 200
        data = res.json()
        assert "active_patron" in data
        assert "koha_status" in data
        assert data["max_borrow_limit"] == 2
        assert "rfid_tags" in data

    def test_get_root_ui(self, client):
        res = client.get("/")
        assert res.status_code == 200
        assert "GRANTHSETU" in res.text or "<!DOCTYPE html>" in res.text or "<html" in res.text

    def test_kiosk_status_expires_inactive_session(self, client):
        # 1. Authenticate patron
        client.post("/api/auth/patron", json={"patron_id": "NFSU2024001", "method": "CARD"})
        assert kiosk_controller.active_patron is not None

        # 2. Fast forward inactivity past timeout
        kiosk_controller.last_activity_at = datetime.now() - timedelta(seconds=120)

        # 3. Querying status must detect expiry and return null active_patron
        res = client.get("/api/kiosk/status")
        assert res.status_code == 200
        data = res.json()
        assert data["active_patron"] is None

        # 4. Subsequent checkout attempt must be rejected
        checkout_res = client.post("/api/circulation/checkout", json={"barcode": "BK-TOX-501"})
        assert checkout_res.status_code == 400
        assert checkout_res.json()["error_code"] == "FAILED_BEFORE_SEND"
        assert kiosk_controller.rfid_tag_registry["BK-TOX-501"] == "ARMED"


class TestPatronAuthRoutes:
    def test_auth_patron_success(self, client):
        res = client.post("/api/auth/patron", json={"patron_id": "NFSU2024001", "method": "CARD"})
        assert res.status_code == 200
        data = res.json()
        assert data["success"] is True
        assert data["patron"]["patron_id"] == "NFSU2024001"
        assert kiosk_controller.active_patron is not None
        assert kiosk_controller.active_patron["patron_id"] == "NFSU2024001"

    def test_auth_patron_empty_id_denial(self, client):
        res = client.post("/api/auth/patron", json={"patron_id": "   ", "method": "CARD"})
        assert res.status_code == 400
        data = res.json()
        assert "detail" in data
        assert kiosk_controller.active_patron is None

    def test_auth_patron_blocked_card_denial(self, client):
        res = client.post("/api/auth/patron", json={"patron_id": "BLOCKED_PATRON_01", "method": "CARD"})
        assert res.status_code == 400
        data = res.json()
        assert "detail" in data
        assert kiosk_controller.active_patron is None

    def test_auth_patron_validation_error(self, client):
        # Missing required field patron_id
        res = client.post("/api/auth/patron", json={"method": "CARD"})
        assert res.status_code == 422
        data = res.json()
        assert "detail" in data


class TestFaceAuthRoutes:
    def test_face_auth_success(self, client):
        res = client.post("/api/auth/face", json={"simulate_liveness": True, "mock_choice": "FACE_01"})
        assert res.status_code == 200
        data = res.json()
        assert data["biometrics"]["success"] is True
        assert data["biometrics"]["patron_id"] == "NFSU2024001"
        assert kiosk_controller.active_patron is not None

    def test_face_auth_liveness_failure(self, client):
        # With simulate_liveness=False and no valid base64 image, liveness check must fail with 401
        res = client.post("/api/auth/face", json={"simulate_liveness": False, "image_base64": None})
        assert res.status_code == 401
        data = res.json()
        assert "Liveness verification failed" in data["detail"]


class TestItemLookupRoute:
    def test_lookup_item_success(self, client):
        res = client.get("/api/items/lookup", params={"barcode": "BK-TOX-501"})
        assert res.status_code == 200
        data = res.json()
        assert data["item_id"] == "BK-TOX-501"
        assert "Clarke's Analytical Forensic Toxicology" in data["title"]
        assert "afi_byte" in data

    def test_lookup_item_missing_barcode_param(self, client):
        res = client.get("/api/items/lookup")
        assert res.status_code == 422


class TestCirculationRoutes:
    def test_checkout_unauthenticated_fails(self, client):
        kiosk_controller.active_patron = None
        res = client.post("/api/circulation/checkout", json={"barcode": "BK-TOX-501"})
        assert res.status_code == 400
        data = res.json()
        assert data["success"] is False
        assert data["error_code"] == "FAILED_BEFORE_SEND"
        assert "No active patron session" in data["message"]

    def test_checkout_success_disarms_rfid(self, client):
        # Authenticate patron with 0 books
        client.post("/api/auth/patron", json={"patron_id": "NFSU2024001", "method": "CARD"})
        kiosk_controller.rfid_tag_registry["BK-TOX-501"] = "ARMED"

        res = client.post("/api/circulation/checkout", json={"barcode": "BK-TOX-501"})
        assert res.status_code == 200
        data = res.json()
        assert data["success"] is True
        assert "DISARMED" in data["rfid_security"]
        assert kiosk_controller.rfid_tag_registry["BK-TOX-501"] == "DISARMED"
        assert kiosk_controller.active_patron["charged_count"] == 1

    def test_checkout_borrowing_limit_reached_denial(self, client):
        # Setup patron who already has 2 books
        kiosk_controller.active_patron = {
            "patron_id": "NFSU2024003",
            "name": "Dr. Rajesh Verma",
            "charged_count": 2,
            "charged_items": ["BK-CYB-201", "BK-DIG-301"],
            "available_limit": 0,
            "limit_reached": True
        }
        kiosk_controller.rfid_tag_registry["BK-DNA-601"] = "ARMED"

        # Attempt to issue a 3rd book
        res = client.post("/api/circulation/checkout", json={"barcode": "BK-DNA-601"})
        assert res.status_code == 400
        data = res.json()
        assert data["success"] is False
        assert data["error_code"] == "FAILED_BEFORE_SEND"
        assert "Borrowing limit reached" in data["message"]
        # Must preserve ARMED state on denial
        assert kiosk_controller.rfid_tag_registry["BK-DNA-601"] == "ARMED"

    def test_checkout_validation_error(self, client):
        res = client.post("/api/circulation/checkout", json={})
        assert res.status_code == 422

    def test_checkin_success_arms_rfid(self, client):
        kiosk_controller.rfid_tag_registry["BK-FOR-101"] = "DISARMED"
        res = client.post("/api/circulation/checkin", json={"barcode": "BK-FOR-101"})
        assert res.status_code == 200
        data = res.json()
        assert data["success"] is True
        assert "ARMED" in data["rfid_security"]
        assert kiosk_controller.rfid_tag_registry["BK-FOR-101"] == "ARMED"

    def test_checkin_empty_barcode_denial(self, client):
        res = client.post("/api/circulation/checkin", json={"barcode": "   "})
        assert res.status_code == 400
        data = res.json()
        assert data["success"] is False
        assert data["error_code"] == "FAILED_BEFORE_SEND"

    def test_checkin_validation_error(self, client):
        res = client.post("/api/circulation/checkin", json={})
        assert res.status_code == 422

    def test_renew_unauthenticated_fails(self, client):
        kiosk_controller.active_patron = None
        res = client.post("/api/circulation/renew", json={"barcode": "BK-FOR-101"})
        assert res.status_code == 400
        data = res.json()
        assert data["success"] is False
        assert "authenticate" in data["message"].lower() or "patron" in data["message"].lower()

    def test_renew_success(self, client):
        client.post("/api/auth/patron", json={"patron_id": "NFSU2024002", "method": "CARD"})
        res = client.post("/api/circulation/renew", json={"barcode": "BK-FOR-101"})
        assert res.status_code == 200
        data = res.json()
        assert data["success"] is True
        assert data["title"] is not None


class TestGateAndSecurityRoutes:
    def test_gate_alarm_on_armed_item(self, client):
        kiosk_controller.rfid_tag_registry["BK-TOX-501"] = "ARMED"
        res = client.post("/api/gate/test", json={"barcode": "BK-TOX-501"})
        assert res.status_code == 200
        data = res.json()
        assert data["alarm_triggered"] is True
        assert data["status"] == "ALARM_RINGING"
        assert data["audio_alert"] is True

    def test_gate_safe_passage_on_disarmed_item(self, client):
        kiosk_controller.rfid_tag_registry["BK-FOR-101"] = "DISARMED"
        res = client.post("/api/gate/test", json={"barcode": "BK-FOR-101"})
        assert res.status_code == 200
        data = res.json()
        assert data["alarm_triggered"] is False
        assert data["status"] == "SAFE_PASSAGE"
        assert data["audio_alert"] is False

    def test_gate_validation_error(self, client):
        res = client.post("/api/gate/test", json={})
        assert res.status_code == 422

    def test_end_session_route(self, client):
        client.post("/api/auth/patron", json={"patron_id": "NFSU2024001", "method": "CARD"})
        assert kiosk_controller.active_patron is not None
        res = client.post("/api/session/end")
        assert res.status_code == 200
        data = res.json()
        assert data["success"] is True
        assert kiosk_controller.active_patron is None

    def test_security_audit_report(self, client):
        res = client.get("/api/security/audit")
        assert res.status_code == 200
        data = res.json()
        assert "audit_trail" in data
        assert data["biometric_zero_retention_compliance"] is True
        assert data["persisted_biometric_bytes"] == 0
