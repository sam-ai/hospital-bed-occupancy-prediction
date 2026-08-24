"""Test wrangling and monitoring subgraphs."""
import asyncio

from app.agents.monitoring import monitoring_graph
from app.agents.wrangling import wrangling_graph


async def test_subgraphs():
    # Test wrangling subgraph
    state = {"hospital_id": "HOSPITAL-MAIN-01", "unit_id": "ICU-EAST"}
    result = await wrangling_graph.ainvoke(state)
    assert result["hospital_context"].total_beds == 50
    assert 35 <= result["hospital_context"].occupied_beds <= 50
    assert result["data_quality"].status == "usable"
    assert result["data_quality"].quality_score == 0.99
    print(f"Wrangling: {result['hospital_context'].occupied_beds}/{result['hospital_context'].total_beds} beds")
    print(f"  Quality: {result['data_quality'].status} (score={result['data_quality'].quality_score})")

    # Test monitoring subgraph
    state = {"hospital_id": "HOSPITAL-MAIN-01", "unit_id": "ICU-EAST"}
    result = await monitoring_graph.ainvoke(state)
    assert len(result["external_signals"]) == 2
    assert len(result["findings"]) == 2
    print(f"Monitoring: {len(result['external_signals'])} signals, {len(result['findings'])} findings")
    for f in result["findings"]:
        print(f"  - {f}")

    print("[SUCCESS] Both subgraphs verified!")


if __name__ == "__main__":
    asyncio.run(test_subgraphs())
