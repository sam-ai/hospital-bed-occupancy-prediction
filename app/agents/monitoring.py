from langgraph.graph import END, START, StateGraph

from app.agents.state import HospitalAgentState
from app.data.mock_mcp import PublicSignalMCPClient
from app.llm import get_llm

public_mcp = PublicSignalMCPClient()
_llm = get_llm()


async def fetch_signals(state: HospitalAgentState) -> dict:
    """Fetch external epidemiological and environmental signals."""
    signals = await public_mcp.get_signals(state["hospital_id"])
    return {"external_signals": signals}


async def analyze_signals(state: HospitalAgentState) -> dict:
    """Analyze external signals and produce human-readable findings.

    Uses LLM if available to synthesize complex multi-signal patterns into
    actionable insights. Falls back to deterministic severity filtering.
    """
    signals = state.get("external_signals", [])
    context = state.get("hospital_context")
    findings: list[str] = []

    # Collect non-low severity signals
    notable_signals = [s for s in signals if s.severity in ("medium", "high", "critical")]

    if not notable_signals:
        return {"findings": findings}

    # --- LLM-powered synthesis ---
    if _llm and context:
        try:
            signal_summary = "\n".join(
                f"- {s.signal_type}: severity={s.severity}, "
                f"direction={s.direction}, confidence={s.confidence}"
                for s in notable_signals
            )
            prompt = (
                f"System: You are an AI Hospital Operations Monitoring Agent.\n"
                f"Context: Hospital {context.hospital_id}, Unit {context.unit_id}. "
                f"Beds: {context.occupied_beds}/{context.total_beds} occupied, "
                f"Staff on duty: {context.staff_on_duty}.\n"
                f"External Signals Detected:\n{signal_summary}\n\n"
                f"Task: Summarize the operational impact of these signals in 2-3 "
                f"concise sentences. Focus on what actions the hospital should take. "
                f"Keep response under 60 words."
            )
            response = await _llm.ainvoke(prompt)
            llm_finding = str(response.content).strip()
            if llm_finding:
                findings.append(f"LLM Analysis: {llm_finding}")
        except Exception:
            pass  # Fall through to deterministic fallback

    # --- Deterministic fallback (always runs for completeness) ---
    for sig in notable_signals:
        findings.append(
            f"External alert: {sig.signal_type} severity is "
            f"{sig.severity} ({sig.direction})."
        )

    return {"findings": findings}


def build_monitoring_graph():
    """Build the monitoring subgraph: fetch signals → analyze."""
    builder = StateGraph(HospitalAgentState)
    builder.add_node("fetch_signals", fetch_signals)
    builder.add_node("analyze_signals", analyze_signals)
    builder.add_edge(START, "fetch_signals")
    builder.add_edge("fetch_signals", "analyze_signals")
    builder.add_edge("analyze_signals", END)
    return builder.compile()


monitoring_graph = build_monitoring_graph()
