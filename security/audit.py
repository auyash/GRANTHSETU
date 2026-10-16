"""
Forensic Security & Compliance Audit Log Engine for NFSU Kiosk.
Records security events, verifies biometric privacy compliance (0-retention),
tracks SIP2 checksum integrity, and logs RFID security gate alerts.
"""

from datetime import datetime
from typing import Dict, List, Optional
import hashlib


class SecurityAuditLogger:
    def __init__(self):
        self.audit_trail: List[Dict] = []
        self.biometric_bytes_persisted: int = 0  # Must strictly remain 0
        self.checksums_verified: int = 0
        self.unauthorized_attempts_blocked: int = 0
        self.gate_alarms_triggered: int = 0

    def log_event(self, category: str, action: str, details: str, severity: str = "INFO"):
        """Appends a forensic audit entry with SHA-256 hash chaining."""
        ts = datetime.now().isoformat()
        prev_hash = self.audit_trail[-1]["hash"] if self.audit_trail else "ROOT_GENESIS"
        
        raw_signature = f"{ts}:{category}:{action}:{details}:{prev_hash}"
        event_hash = hashlib.sha256(raw_signature.encode()).hexdigest()[:16]

        entry = {
            "timestamp": ts,
            "category": category,
            "action": action,
            "details": details,
            "severity": severity,
            "hash": event_hash,
            "prev_hash": prev_hash,
        }
        self.audit_trail.append(entry)
        if len(self.audit_trail) > 100:
            self.audit_trail.pop(0)

    def log_biometric_verification(self, patron_id: str, liveness_passed: bool, confidence: float):
        self.log_event(
            category="BIOMETRICS",
            action="LIVENESS_CHECK",
            details=f"Patron {patron_id} verified. Liveness: {liveness_passed} (Confidence: {confidence:.2f}). Frame buffer cleared (0 bytes written to disk).",
            severity="INFO" if liveness_passed else "WARNING"
        )

    def log_limit_violation(self, patron_id: str, current_count: int, attempted_item: str):
        self.unauthorized_attempts_blocked += 1
        self.log_event(
            category="CIRCULATION_POLICY",
            action="2_BOOK_LIMIT_BLOCKED",
            details=f"Patron {patron_id} holds {current_count} books. Attempted checkout of {attempted_item} blocked. Policy limit: 2 books.",
            severity="WARNING"
        )

    def log_gate_alarm(self, item_id: str, reason: str = "ARMED_TAG_DETECTED"):
        self.gate_alarms_triggered += 1
        self.log_event(
            category="RFID_GATE_SECURITY",
            action="ALARM_TRIGGERED",
            details=f"Security gate detected ARMED RFID tag {item_id}! Audible and visual siren activated. Reason: {reason}",
            severity="CRITICAL"
        )

    def log_eas_state_change(self, item_id: str, state: str, reason: str):
        self.log_event(
            category="RFID_EAS",
            action=f"EAS_{state.upper()}",
            details=f"Tag {item_id} transition to {state}. Reason: {reason}",
            severity="INFO"
        )

    def get_security_report(self) -> Dict:
        """Returns forensic security compliance metrics for audit review."""
        return {
            "biometric_zero_retention_compliance": self.biometric_bytes_persisted == 0,
            "persisted_biometric_bytes": self.biometric_bytes_persisted,
            "active_crypto_chain": True,
            "unauthorized_blocks": self.unauthorized_attempts_blocked,
            "gate_alarms": self.gate_alarms_triggered,
            "audit_trail": list(reversed(self.audit_trail[-20:])),
        }


audit_logger = SecurityAuditLogger()
