"""Test recommendation subgraph (fallback mode without LLM API key)."""
import asyncio

from app.agents.recommendation import recommendation_graph
from app.models import AnomalyResult, HospitalContext


async def test_recommendation():
    # Test with a detected anomaly (no LLM key = fallback path)
    ctx = HospitalContext(
        hospital_id="HOSPITAL-MAIN-01",
        unit_id="ICU-EAST",
        total_beds=50,
        occupied_beds=43,
        admissions_24h=12,
        discharges_24h=5,
        staff_on_duty=6,
        average_los_hours=42.0,
        timestamp="2024-01-01T00:00:00Z",
    )
    anomaly = AnomalyResult(
        detected=True,
        anomaly_type="capacity_exhaustion_risk",
        severity="critical",
        score=0.97,
        explanation="Upper-bound occupancy reaches 97%.",
        affected_metric="occupancy",
    )

    state = {
        "hospital_id": "HOSPITAL-MAIN-01",
        "unit_id": "ICU-EAST",
        "hospital_context": ctx,
        "anomaly": anomaly,
    }

    result = await recommendation_graph.ainvoke(state)

    # Verify recommendations generated
    recs = result["recommendations"]
    assert len(recs) == 1
    assert recs[0].title == "Capacity Surge Escalation"
    assert recs[0].priority == "critical"
    assert recs[0].requires_human_approval is True
    assert "ICU-EAST" in recs[0].description
    print(f"Recommendation: {recs[0].title} (priority={recs[0].priority})")
    print(f"  Description: {recs[0].description}")

    # Verify policy decision
    policy = result["policy_decision"]
    assert policy.decision == "HUMAN_APPROVAL"
    print(f"Policy: {policy.decision} - {policy.reason}")

    # Test with no anomaly
    no_anomaly = AnomalyResult(
        detected=False, severity="none", score=0.6, explanation="Normal"
    )
    state2 = {
        "hospital_id": "H1",
        "unit_id": "U1",
        "hospital_context": ctx,
        "anomaly": no_anomaly,
    }
    result2 = await recommendation_graph.ainvoke(state2)
    assert len(result2["recommendations"]) == 0
    assert result2["policy_decision"].decision == "ALLOW"
    print(f"No anomaly: {result2['policy_decision'].decision}")

    print("[SUCCESS] Recommendation subgraph verified!")


if __name__ == "__main__":
    asyncio.run(test_recommendation())
