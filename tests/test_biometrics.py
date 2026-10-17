"""
Automated unit tests for GRANTHSETU Forensic Biometric Engine.
Verifies:
1. Strict Patron Consent Enforcement (BiometricConsentError on user_consent=False).
2. Zero Disk Retention Guarantee (0 bytes persisted to filesystem, in-memory RAM validation only).
3. Low Resolution & Malformed Frame Rejection.
4. Live Face Verification vs Explicit Demonstration/Simulation Mode separation.
5. Biometric subsystem compliance reporting.
"""

import pytest
import io
import base64
from PIL import Image
from security.biometrics import (
    FacialAuthEngine,
    BiometricConsentError,
    BiometricVerificationError,
    face_engine,
)


def _create_sample_base64_image(width: int = 320, height: int = 240, color: str = "red") -> str:
    """Helper creating an in-memory sample image for testing."""
    img = Image.new("RGB", (width, height), color=color)
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return base64.b64encode(buf.getvalue()).decode("utf-8")


class TestBiometricConsentEnforcement:
    def test_live_face_verification_without_consent_raises_error(self):
        engine = FacialAuthEngine()
        sample_b64 = _create_sample_base64_image()
        with pytest.raises(BiometricConsentError) as exc_info:
            engine.verify_live_face(sample_b64, user_consent=False)
        assert "Explicit patron consent is required" in str(exc_info.value)

    def test_mock_face_verification_without_consent_raises_error(self):
        engine = FacialAuthEngine()
        with pytest.raises(BiometricConsentError) as exc_info:
            engine.verify_mock_identity(mock_choice="FACE_01", user_consent=False)
        assert "Consent required" in str(exc_info.value)

    def test_verify_face_stream_without_consent_raises_error(self):
        engine = FacialAuthEngine()
        with pytest.raises(BiometricConsentError):
            engine.verify_face_stream(simulate_liveness=True, user_consent=False)


class TestZeroDiskRetentionAndRAMProcessing:
    def test_live_face_persisted_bytes_is_zero(self):
        engine = FacialAuthEngine()
        sample_b64 = _create_sample_base64_image(320, 240)
        res = engine.verify_live_face(sample_b64, user_consent=True)
        assert res["success"] is True
        assert res["zero_retention_verified"] is True
        assert res["persisted_bytes"] == 0
        assert res["is_simulated"] is False

    def test_mock_face_persisted_bytes_is_zero(self):
        engine = FacialAuthEngine()
        res = engine.verify_mock_identity(mock_choice="FACE_01", user_consent=True)
        assert res["success"] is True
        assert res["zero_retention_verified"] is True
        assert res["persisted_bytes"] == 0
        assert res["is_simulated"] is True

    def test_biometric_status_reports_zero_bytes_on_disk(self):
        status = face_engine.get_biometric_status()
        assert status["zero_retention_compliant"] is True
        assert status["bytes_on_disk"] == 0
        assert "Ephemeral Volatile RAM" in status["algorithm"]


class TestFrameInspectionAndResolutionConstraints:
    def test_low_resolution_frame_rejected(self):
        engine = FacialAuthEngine()
        # Tiny 30x30 image below MIN_RESOLUTION threshold (160x120)
        tiny_b64 = _create_sample_base64_image(30, 30)
        with pytest.raises(BiometricVerificationError) as exc_info:
            engine.verify_live_face(tiny_b64, user_consent=True)
        assert "Resolution too low" in str(exc_info.value)

    def test_malformed_base64_rejected(self):
        engine = FacialAuthEngine()
        with pytest.raises(BiometricVerificationError):
            engine.verify_live_face("NOT_A_VALID_BASE64_STRING!!!", user_consent=True)

    def test_live_stream_without_image_fails_when_simulation_disabled(self):
        engine = FacialAuthEngine()
        res = engine.verify_face_stream(base64_image=None, simulate_liveness=False, user_consent=True)
        assert res["success"] is False
        assert res["liveness_verified"] is False
        assert "No camera frame provided" in res["error"]
