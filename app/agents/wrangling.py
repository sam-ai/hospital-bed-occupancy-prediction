from langgraph.graph import END, START, StateGraph

from app.agents.state import HospitalAgentState
from app.data.mock_mcp import HospitalMCPClient
from app.models import DataQuality

hospital_mcp = HospitalMCPClient()


async def fetch_hospital_data(state: HospitalAgentState) -> dict:
    """Fetch current hospital context via MCP client."""
    ctx = await hospital_mcp.get_hospital_context(
        state["hospital_id"], state["unit_id"]
    )
    return {"hospital_context": ctx}


async def validate_hospital_data(state: HospitalAgentState) -> dict:
    """Validate fetched hospital data for quality and consistency."""
    ctx = state["hospital_context"]
    issues: list[str] = []

    if ctx.total_beds <= 0:
        issues.append("Invalid total beds")
    if ctx.occupied_beds > ctx.total_beds:
        issues.append("Occupied exceeds total")
    if ctx.staff_on_duty <= 0:
        issues.append("No staff on duty")

    quality = DataQuality(
        status="usable" if not issues else "invalid",
        quality_score=0.99 if not issues else 0.0,
        issues=issues,
    )
    return {"data_quality": quality}


def build_wrangling_graph():
    """Build the data wrangling subgraph: fetch → validate."""
    builder = StateGraph(HospitalAgentState)
    builder.add_node("fetch_hospital_data", fetch_hospital_data)
    builder.add_node("validate_hospital_data", validate_hospital_data)
    builder.add_edge(START, "fetch_hospital_data")
    builder.add_edge("fetch_hospital_data", "validate_hospital_data")
    builder.add_edge("validate_hospital_data", END)
    return builder.compile()


wrangling_graph = build_wrangling_graph()
