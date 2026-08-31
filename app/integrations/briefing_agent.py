"""Daily Hospital Capacity Briefing Agent integration.

Wraps the AAVA-hosted "Daily Hospital Capacity Briefing Agent" (agentId
configured via AAVA_BRIEFING_AGENT_ID). Converts a pipeline `AgentResult`
into the agent's expected input shape and parses its structured response
into a `CapacityBriefing` model.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from app.config import AAVA_BRIEFING_AGENT_ID
from app.integrations.aava_client import AAVAClient
from app.models import AgentResult

# Local folder where every briefing request/response pair is saved as JSON.
AAVA_OUTPUT_DIR = Path(__file__).parent.parent.parent / "aava_output"


class CapacityBriefing(BaseModel):
    """Structured output of the Daily Hospital Capacity Briefing Agent."""

    briefing: str
    riskLevel: Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"]
    requiresAttention: bool
    requestId: str
    timestamp: str


def build_briefing_input(result: AgentResult) -> dict:
    """Build the AAVA agent input payload from a pipeline AgentResult."""
    peak_occupancy = None
    if result.forecast and result.forecast.points:
        peak_occupancy = max(p.predicted_occupancy for p in result.forecast.points)

    return {
        "hospital_id": result.hospital_id,
        "unit_id": result.unit_id,
        "peak_predicted_occupancy": peak_occupancy,
        "total_beds": result.hospital_context.total_beds,
        "anomaly_detected": bool(result.anomaly and result.anomaly.detected),
        "anomaly_explanation": result.anomaly.explanation if result.anomaly else None,
        "findings": result.findings,
        "recommendations": [r.description for r in result.recommendations],
        "policy_decision": result.policy_decision.decision if result.policy_decision else None,
    }


def save_briefing_output(
    result: AgentResult, payload: dict, briefing: CapacityBriefing
) -> Path:
    """Save the request payload and parsed briefing response as a JSON file.

    Files are written to ./aava_output/<hospital_id>_<unit_id>_<timestamp>.json
    """
    AAVA_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    filename = f"{result.hospital_id}_{result.unit_id}_{timestamp}.json".replace(" ", "_")
    file_path = AAVA_OUTPUT_DIR / filename

    record = {
        "request_id": result.request_id,
        "hospital_id": result.hospital_id,
        "unit_id": result.unit_id,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "input": payload,
        "output": briefing.model_dump(),
    }

    file_path.write_text(json.dumps(record, indent=2))
    return file_path


async def generate_capacity_briefing(
    result: AgentResult, client: AAVAClient | None = None
) -> CapacityBriefing:
    """Call the AAVA briefing agent, save the result locally, and return it."""
    client = client or AAVAClient()
    payload = build_briefing_input(result)
    output = await client.execute_agent(AAVA_BRIEFING_AGENT_ID, payload)
    briefing = CapacityBriefing.model_validate(output)
    save_briefing_output(result, payload, briefing)
    return briefing
