"""End-to-end test of the AAVA Daily Hospital Capacity Briefing Agent.

Builds a representative AgentResult fixture (avoiding a full pipeline run,
which would download the ~925MB TimesFM model), submits it to the
AAVA-hosted briefing agent, and validates the parsed response.

Requires AAVA_API_KEY (if the agent requires auth) and network access to
https://int-ai.aava.ai.
"""

import asyncio
from datetime import datetime, timezone

from app.integrations.briefing_agent import build_briefing_input, generate_capacity_briefing
from app.models import (
    AgentResult,
    AnomalyResult,
    DataQuality,
    ForecastPoint,
    ForecastResult,
    HospitalContext,
    PolicyDecision,
    Recommendation,
)


def build_sample_agent_result() -> AgentResult:
    """Build a representative AgentResult without running the full pipeline."""
    now = datetime.now(timezone.utc).isoformat()

    context = HospitalContext(
        hospital_id="HOSPITAL-MAIN-01",
        unit_id="ICU-3",
        total_beds=40,
        occupied_beds=38,
        admissions_24h=12,
        discharges_24h=5,
        staff_on_duty=8,
        average_los_hours=44.5,
        timestamp=now,
    )

    forecast = ForecastResult(
        model_name="google/timesfm-2.5-200m-transformers",
        model_version="2.5.0",
        horizon_hours=24,
        generated_at=now,
        points=[
            ForecastPoint(
                timestamp=now, predicted_occupancy=1.05, lower_bound=0.95, upper_bound=1.10
            )
        ],
        confidence=0.92,
    )

    anomaly = AnomalyResult(
        detected=True,
        anomaly_type="CAPACITY_SURGE",
        severity="critical",
        score=0.91,
        explanation="Occupancy trending 15% above seasonal baseline",
        affected_metric="occupied_beds",
    )

    recommendation = Recommendation(
        recommendation_id="REC-CAPACITY-001",
        title="Capacity Surge Escalation",
        description="Activate surge capacity management for ICU-3 due to occupancy exceeding baseline.",
        priority="critical",
        rationale=anomaly.explanation,
        expected_effect="Free up critical bed capacity and balance unit load.",
        requires_human_approval=True,
        confidence=0.91,
    )

    policy_decision = PolicyDecision(
        decision="HUMAN_APPROVAL",
        reason="Critical severity anomaly requires human sign-off before execution.",
        policy_id="POLICY-001",
        policy_version="1.0",
    )

    return AgentResult(
        request_id="TEST-BRIEFING-01",
        hospital_id="HOSPITAL-MAIN-01",
        unit_id="ICU-3",
        objective="Predict 24h bed occupancy and evaluate capacity surge risk.",
        data_quality=DataQuality(status="usable", quality_score=0.95),
        hospital_context=context,
        forecast=forecast,
        anomaly=anomaly,
        findings=["External alert: flu_index severity is high (increasing)."],
        recommendations=[recommendation],
        policy_decision=policy_decision,
        recommendation_summary=recommendation.description,
        confidence=0.90,
    )


async def test_briefing_agent():
    print("Building sample AgentResult fixture...")
    agent_res = build_sample_agent_result()

    print("\nBriefing agent input payload:")
    payload = build_briefing_input(agent_res)
    for k, v in payload.items():
        print(f"  {k}: {v}")

    print("\nCalling AAVA briefing agent (this polls until SUCCESS)...")
    briefing = await generate_capacity_briefing(agent_res)

    print("\n" + "=" * 60)
    print("DAILY CAPACITY BRIEFING")
    print("=" * 60)
    print(f"Risk Level        : {briefing.riskLevel}")
    print(f"Requires Attention: {briefing.requiresAttention}")
    print(f"Request ID        : {briefing.requestId}")
    print(f"Timestamp         : {briefing.timestamp}")
    print(f"Briefing          : {briefing.briefing}")
    print("=" * 60)

    assert briefing.briefing
    assert briefing.riskLevel in {"LOW", "MEDIUM", "HIGH", "CRITICAL"}
    assert isinstance(briefing.requiresAttention, bool)
    print("[SUCCESS] AAVA briefing agent verified end-to-end!")


if __name__ == "__main__":
    asyncio.run(test_briefing_agent())
