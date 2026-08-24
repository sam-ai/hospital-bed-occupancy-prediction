from temporalio import activity

from app.agents.hospital_graph import hospital_agent_graph
from app.models import AgentResult, HospitalRequest


@activity.defn
async def run_agent(request: HospitalRequest) -> AgentResult:
    """Temporal activity that executes the full LangGraph hospital agent pipeline."""
    activity.logger.info(
        "Executing Agent Activity for Request: %s", request.request_id
    )

    initial_state = {
        "request_id": request.request_id,
        "hospital_id": request.hospital_id,
        "unit_id": request.unit_id,
        "objective": request.objective,
    }

    result = await hospital_agent_graph.ainvoke(initial_state)
    return result["agent_result"]
