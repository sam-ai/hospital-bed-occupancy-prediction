"""Clinical triage data schemas for Fast-Track Admission & Alert Engine.

Captures patient acuity (ESI/NEWS2), bed requirements, allocation matches,
and structured notification payloads for multi-channel dispatch.
"""

from typing import Any, Literal

from pydantic import BaseModel


class WaitingPatient(BaseModel):
    """An ER boarder waiting for an inpatient bed assignment."""

    patient_id: str
    mrn: str  # Medical Record Number
    esi_level: Literal[1, 2, 3, 4, 5]  # Emergency Severity Index (1=Resuscitation, 5=Non-urgent)
    news2_score: int  # National Early Warning Score 2 (0-20, higher=sicker)
    wait_time_minutes: int
    required_bed_type: Literal["ICU", "MED_SURG", "STEP_DOWN", "TELEMETRY", "ISOLATION"]
    chief_complaint: str
    isolation_required: bool = False


class BedAllocationMatch(BaseModel):
    """Result of matching a waiting patient to an available or pending bed."""

    patient_id: str
    mrn: str
    esi_level: int
    priority_score: float
    matched_bed_id: str | None = None
    allocation_status: Literal[
        "READY_TO_ASSIGN",
        "AWAITING_EVS_CLEANING",
        "NEEDS_EXPEDITED_DISCHARGE",
    ]
    action_item: str


class StaffAlertNotification(BaseModel):
    """Structured notification payload for multi-channel dispatch."""

    recipient_role: Literal[
        "BED_COORDINATOR",
        "PHYSICIAN",
        "EVS_HOUSEKEEPING",
        "NURSING_SUPERVISOR",
    ]
    channel: Literal["SLACK", "TWILIO_SMS", "EHR_INBASKET", "CLAW3D_UI_WEBSOCKET"]
    priority: Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"]
    message_title: str
    message_body: str
    payload: dict[str, Any]
