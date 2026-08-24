from langgraph.graph import END, START, StateGraph

from app.agents.state import HospitalAgentState
from app.llm import get_llm
from app.models import Recommendation
from app.policy.engine import HospitalPolicyEngine

policy_engine = HospitalPolicyEngine()
_llm = get_llm()


async def generate_recommendations(state: HospitalAgentState) -> dict:
    """Generate operational recommendations based on anomaly detection.

    Uses Gemini LLM if available; falls back to deterministic template otherwise.
    """
    anomaly = state["anomaly"]
    context = state["hospital_context"]
    recommendations: list[Recommendation] = []

    if anomaly.detected:
        # Try LLM-powered recommendation
        description = None
        if _llm:
            try:
                prompt = (
                    f"System: You are an AI Hospital Operations Planning Agent.\n"
                    f"Context: Hospital {context.hospital_id}, Unit {context.unit_id}. "
                    f"Total Beds: {context.total_beds}, Occupied: {context.occupied_beds}.\n"
                    f"Anomaly Detected: {anomaly.anomaly_type} - {anomaly.explanation} "
                    f"(Severity: {anomaly.severity}).\n"
                    f"Task: Provide 1 clear, actionable operational recommendation "
                    f"to handle bed capacity. Keep response under 30 words."
                )
                response = await _llm.ainvoke(prompt)
                description = str(response.content).strip()
            except Exception:
                description = None

        # Fallback to deterministic recommendation
        if not description:
            description = (
                f"Activate surge capacity management for {context.unit_id} "
                f"due to {anomaly.explanation}"
            )

        recommendations.append(
            Recommendation(
                recommendation_id="REC-CAPACITY-001",
                title="Capacity Surge Escalation",
                description=description,
                priority="critical" if anomaly.severity == "critical" else "high",
                rationale=anomaly.explanation,
                expected_effect="Free up critical bed capacity and balance unit load.",
                requires_human_approval=True,
                confidence=0.91,
            )
        )

    return {"recommendations": recommendations}


async def evaluate_policy(state: HospitalAgentState) -> dict:
    """Run recommendations through the deterministic policy engine."""
    decision = policy_engine.evaluate(state.get("recommendations", []))
    return {"policy_decision": decision}


def build_recommendation_graph():
    """Build the recommendation subgraph: generate → policy evaluation."""
    builder = StateGraph(HospitalAgentState)
    builder.add_node("generate_recommendations", generate_recommendations)
    builder.add_node("evaluate_policy", evaluate_policy)
    builder.add_edge(START, "generate_recommendations")
    builder.add_edge("generate_recommendations", "evaluate_policy")
    builder.add_edge("evaluate_policy", END)
    return builder.compile()


recommendation_graph = build_recommendation_graph()
