from langgraph.graph import END, START, StateGraph

from app.agents.monitoring import monitoring_graph
from app.agents.recommendation import recommendation_graph
from app.agents.state import HospitalAgentState
from app.agents.wrangling import wrangling_graph
from app.anomaly.service import AnomalyService
from app.forecasting.controller import ForecastController
from app.models import AgentResult

forecast_controller = ForecastController()
anomaly_service = AnomalyService()


async def run_forecast_node(state: HospitalAgentState) -> dict:
    """Execute the statistical forecasting pipeline."""
    forecast = await forecast_controller.run_forecast(
        state["hospital_context"],
        state.get("external_signals", []),
    )
    return {"forecast": forecast}


async def detect_anomaly_node(state: HospitalAgentState) -> dict:
    """Run anomaly detection on the forecast results."""
    anomaly = await anomaly_service.detect(state["hospital_context"], state["forecast"])
    return {"anomaly": anomaly}


async def synthesize_agent_result(state: HospitalAgentState) -> dict:
    """Assemble the final AgentResult from all pipeline outputs."""
    recs = state.get("recommendations", [])
    summary = recs[0].description if recs else "Operational status within nominal parameters."

    agent_result = AgentResult(
        request_id=state["request_id"],
        hospital_id=state["hospital_id"],
        unit_id=state["unit_id"],
        objective=state["objective"],
        data_quality=state["data_quality"],
        hospital_context=state["hospital_context"],
        external_signals=state.get("external_signals", []),
        forecast=state.get("forecast"),
        anomaly=state.get("anomaly"),
        findings=state.get("findings", []),
        recommendations=recs,
        policy_decision=state.get("policy_decision"),
        recommendation_summary=summary,
        confidence=0.90,
    )
    return {"agent_result": agent_result}


def build_hospital_agent_graph():
    """Build the full hospital agent pipeline graph.

    Flow: wrangling → monitoring → forecast → anomaly → recommendation → synthesize
    """
    builder = StateGraph(HospitalAgentState)

    # Add all nodes
    builder.add_node("wrangling", wrangling_graph)
    builder.add_node("monitoring", monitoring_graph)
    builder.add_node("forecast", run_forecast_node)
    builder.add_node("anomaly", detect_anomaly_node)
    builder.add_node("recommendation", recommendation_graph)
    builder.add_node("synthesize", synthesize_agent_result)

    # Define the linear pipeline
    builder.add_edge(START, "wrangling")
    builder.add_edge("wrangling", "monitoring")
    builder.add_edge("monitoring", "forecast")
    builder.add_edge("forecast", "anomaly")
    builder.add_edge("anomaly", "recommendation")
    builder.add_edge("recommendation", "synthesize")
    builder.add_edge("synthesize", END)

    return builder.compile()


hospital_agent_graph = build_hospital_agent_graph()
