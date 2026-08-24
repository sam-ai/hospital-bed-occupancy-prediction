"""Tests for the post-approval execution activity (staff alert dispatch)."""

from app.models import (
    AgentResult,
    DataQuality,
    HospitalContext,
    Recommendation,
)
from app.temporal.activities import build_staff_alerts


def _make_result(recommendations: list[Recommendation]) -> AgentResult:
    return AgentResult(
        request_id="REQ-TEST-01",
        hospital_id="HOSPITAL-MAIN-01",
        unit_id="ICU-EAST",
        objective="Predict 24h bed occupancy.",
        data_quality=DataQuality(status="usable", quality_score=0.99),
        hospital_context=HospitalContext(
            hospital_id="HOSPITAL-MAIN-01",
            unit_id="ICU-EAST",
            total_beds=50,
            occupied_beds=43,
            admissions_24h=12,
            discharges_24h=5,
            staff_on_duty=6,
            average_los_hours=42.0,
            timestamp="2026-08-24T00:00:00+00:00",
        ),
        recommendations=recommendations,
    )


def _make_rec(priority: str) -> Recommendation:
    return Recommendation(
        recommendation_id=f"REC-{priority.upper()}",
        title="Capacity Surge Escalation",
        description=f"Activate surge capacity ({priority}).",
        priority=priority,
        rationale="Anomaly detected in forecast trajectory.",
        expected_effect="Free up critical bed capacity.",
        requires_human_approval=True,
        confidence=0.91,
    )


class TestBuildStaffAlerts:
    def test_empty_recommendations(self):
        result = _make_result([])
        assert build_staff_alerts(result) == []

    def test_high_priority_routes_to_bed_coordinator(self):
        alerts = build_staff_alerts(_make_result([_make_rec("high")]))
        assert len(alerts) == 1
        alert = alerts[0]
        assert alert.channel == "CLAW3D_UI_WEBSOCKET"
        assert alert.recipient_role == "BED_COORDINATOR"
        assert alert.priority == "HIGH"
        assert alert.message_title == "Capacity Surge Escalation"

    def test_critical_priority_routes_to_nursing_supervisor(self):
        alerts = build_staff_alerts(_make_result([_make_rec("critical")]))
        assert alerts[0].recipient_role == "NURSING_SUPERVISOR"
        assert alerts[0].priority == "CRITICAL"

    def test_one_alert_per_recommendation_with_payload(self):
        recs = [_make_rec("high"), _make_rec("critical"), _make_rec("low")]
        alerts = build_staff_alerts(_make_result(recs))
        assert len(alerts) == 3
        assert [a.priority for a in alerts] == ["HIGH", "CRITICAL", "LOW"]
        payload = alerts[0].payload
        assert payload["action"] == "SURGE_ESCALATION"
        assert payload["unit_id"] == "ICU-EAST"
        assert payload["recommendation_id"] == "REC-HIGH"
