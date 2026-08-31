"""End-to-end test of the full hospital agent LangGraph pipeline."""
import asyncio

from app.agents.hospital_graph import hospital_agent_graph


async def test_standalone_agent():
    print("Running standalone LangGraph full pipeline test...")

    initial_state = {
        "request_id": "TEST-LOCAL-01",
        "hospital_id": "HOSPITAL-MAIN-01",
        "unit_id": "ICU-EAST",
        "objective": "Predict 24h bed occupancy and evaluate capacity surge risk.",
    }

    result = await hospital_agent_graph.ainvoke(initial_state)
    agent_res = result["agent_result"]

    # Verify all fields populated
    assert agent_res.request_id == "TEST-LOCAL-01"
    assert agent_res.hospital_id == "HOSPITAL-MAIN-01"
    assert agent_res.unit_id == "ICU-EAST"
    # Occupancy is sourced from the 10-bed ward mock data (ICU-EAST total_beds=10)
    assert 0 <= agent_res.hospital_context.occupied_beds <= agent_res.hospital_context.total_beds
    assert agent_res.data_quality.status == "usable"
    assert len(agent_res.external_signals) == 2
    assert agent_res.forecast is not None
    assert len(agent_res.forecast.points) == 24
    assert agent_res.anomaly is not None
    
    assert agent_res.anomaly.detected is True
    assert agent_res.anomaly.severity == "critical"
    assert len(agent_res.recommendations) == 1
    assert agent_res.policy_decision is not None
    assert agent_res.policy_decision.decision == "HUMAN_APPROVAL"
    assert len(agent_res.findings) == 2
    assert agent_res.confidence == 0.90

    # Print summary
    print("\n" + "=" * 60)
    print("AGENT RESULT SUMMARY")
    print("=" * 60)
    print(f"Request ID     : {agent_res.request_id}")
    print(f"Hospital/Unit  : {agent_res.hospital_id} / {agent_res.unit_id}")
    print(f"Data Quality   : {agent_res.data_quality.status} (score={agent_res.data_quality.quality_score})")
    print(f"Signals        : {len(agent_res.external_signals)} external signals")
    print(f"Findings       : {len(agent_res.findings)} findings")
    for f in agent_res.findings:
        print(f"  - {f}")
    print(f"Forecast       : {agent_res.forecast.model_name} ({agent_res.forecast.horizon_hours}h horizon)")
    max_occ = max(p.predicted_occupancy for p in agent_res.forecast.points)
    print(f"Max Occupancy  : {max_occ:.2%}")
    print(f"Anomaly        : {agent_res.anomaly.anomaly_type} [{agent_res.anomaly.severity}]")
    print(f"Recommendation : {agent_res.recommendations[0].title} (priority={agent_res.recommendations[0].priority})")
    print(f"Policy Decision: {agent_res.policy_decision.decision}")
    print(f"Summary        : {agent_res.recommendation_summary[:80]}...")
    print(f"Confidence     : {agent_res.confidence}")
    print("=" * 60)
    print("[SUCCESS] Full LangGraph pipeline verified!")


if __name__ == "__main__":
    asyncio.run(test_standalone_agent())
