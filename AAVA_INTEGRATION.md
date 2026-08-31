# AAVA Daily Capacity Briefing Agent — Integration Notes

This document explains the new AAVA agent integration added to this project:
what it is, why it was created, how it works end-to-end, and how to test or
change it.

## 1. What this is

We created a new agent on the AAVA platform called **"Daily Hospital
Capacity Briefing Agent"**. It's a new capability, not a replacement for any
existing LLM logic in this codebase.

**What it does:** takes the technical output of a hospital capacity run
(forecast numbers, detected anomalies, recommendations, policy decision) and
turns it into a short, plain-English briefing for hospital administrators,
plus a risk level and an "needs attention" flag.

- Agent ID: `56091` (configurable via `AAVA_BRIEFING_AGENT_ID`)
- AAVA endpoint base: `https://int-ai.aava.ai`

## 2. Why this spot in the pipeline

The pipeline (`app/agents/hospital_graph.py`) already produces a rich
`AgentResult` object at the end of every run — forecast, anomaly, findings,
recommendations, and policy decision. Administrators shouldn't have to read
raw JSON to understand "is this bad, and do I need to act today?" — that's
exactly what the briefing agent answers.

## 3. How the AAVA API works (background)

AAVA agent calls are two-step and asynchronous:

1. **POST** `/agents/execute/agent-executions` — submit a job.
   - Body is `multipart/form-data` with fields `agentId`, `userInputs`
     (a JSON string wrapping your actual input under the key `"{{content}}"`),
     and `executionId` (a UUID you generate).
   - Returns an `agentExecutionId`.
2. **GET** `/agents/execute/history/execution?execution_id=<id>` — poll this
   until `status` is `SUCCESS` (or `FAILED`). The `output` field is itself a
   JSON string (needs a second `json.loads`) containing the agent's actual
   structured answer.

Auth: Bearer token, sent as `Authorization: Bearer <token>`.

One gotcha we hit and fixed: `aiohttp`'s `FormData` only switches to
`multipart/form-data` automatically when a field looks like a file. With
plain text fields it silently sends `application/x-www-form-urlencoded`,
which AAVA rejects with `415 Unsupported Media Type`. Fixed by forcing
`FormData(default_to_multipart=True)`.

## 4. New files added

| File | Purpose |
|---|---|
| `app/integrations/aava_client.py` | Generic reusable AAVA client: submit a job, poll until done, return parsed output. Not specific to any one agent. |
| `app/integrations/briefing_agent.py` | Specific to the briefing agent: builds the input JSON from an `AgentResult`, calls the client, parses the response into a `CapacityBriefing` model, and saves the input/output pair to a local JSON file. |
| `tests/test_briefing_agent.py` | Standalone script that builds a sample `AgentResult` (without running the full ML pipeline) and calls the real AAVA agent, to verify the integration works. |

## 5. Files modified

| File | Change |
|---|---|
| `app/config.py`, `.env`, `.env.example` | Added `AAVA_BASE_URL`, `AAVA_API_KEY`, `AAVA_BRIEFING_AGENT_ID`, `AAVA_POLL_INTERVAL_SECONDS`, `AAVA_POLL_TIMEOUT_SECONDS`. |
| `app/temporal/activities.py` | Added a new Temporal activity `generate_daily_briefing(result)` that calls the briefing agent. |
| `app/temporal/worker.py` | Registered `generate_daily_briefing` so the Temporal worker can run it. |
| `app/temporal/workflows.py` | `HospitalCapacityWorkflow` now calls `generate_daily_briefing` automatically right after the main agent pipeline finishes, every run. If the AAVA call fails, the error is logged but does not fail the rest of the workflow. |
| `.gitignore` | Added `aava_output/` (generated data, not source). |

## 6. Where the output goes

Every time the briefing agent runs (whether via the automatic workflow step
or via the test script), the result is saved locally as a JSON file:

```
aava_output/<hospital_id>_<unit_id>_<timestamp>.json
```

Example content:

```json
{
  "request_id": "TEST-BRIEFING-01",
  "hospital_id": "HOSPITAL-MAIN-01",
  "unit_id": "ICU-3",
  "generated_at": "2026-08-26T16:25:50.229625+00:00",
  "input": {
    "hospital_id": "HOSPITAL-MAIN-01",
    "unit_id": "ICU-3",
    "peak_predicted_occupancy": 1.05,
    "total_beds": 40,
    "anomaly_detected": true,
    "anomaly_explanation": "Occupancy trending 15% above seasonal baseline",
    "findings": ["External alert: flu_index severity is high (increasing)."],
    "recommendations": ["Activate surge capacity management for ICU-3..."],
    "policy_decision": "HUMAN_APPROVAL"
  },
  "output": {
    "briefing": "ICU-3 at HOSPITAL-MAIN-01 is forecast to exceed full capacity...",
    "riskLevel": "CRITICAL",
    "requiresAttention": true,
    "requestId": "req_7f3a2c91-d4e8-4b10-a6f5-830c1e2d9047",
    "timestamp": "2025-01-30T08:00:00Z"
  }
}
```

This was chosen (for now) instead of Slack/email so results are easy to
inspect locally. Swapping in a real notification channel later just means
adding a call at the end of `generate_capacity_briefing()`.

## 7. How the pieces fit together (flow)

```
HospitalCapacityWorkflow (Temporal workflow)
  └─> run_agent activity
        └─> hospital_agent_graph (LangGraph pipeline)
              produces AgentResult (forecast, anomaly, recommendations, policy)
  └─> generate_daily_briefing activity   [NEW]
        └─> generate_capacity_briefing(result)
              ├─> build_briefing_input(result)      # AgentResult -> plain dict
              ├─> AAVAClient.execute_agent(...)      # POST + poll GET
              ├─> CapacityBriefing.model_validate()  # parse structured output
              └─> save_briefing_output(...)          # write JSON to aava_output/
  └─> (existing) human approval flow continues as before
```

## 8. Configuration

Add these to `.env` (already done in this project):

```
AAVA_BASE_URL=https://int-ai.aava.ai
AAVA_API_KEY=<your bearer token>
AAVA_BRIEFING_AGENT_ID=56091
AAVA_POLL_INTERVAL_SECONDS=2
AAVA_POLL_TIMEOUT_SECONDS=60
```

## 9. How to test it

**Fastest way (no Temporal needed):**

```powershell
uv run python tests/test_briefing_agent.py
```

This builds a sample `AgentResult`, calls the real AAVA agent, prints the
briefing, and saves a JSON file to `aava_output/`.

**Full end-to-end (via Temporal):**

1. Make sure a Temporal server is running and the worker is started
   (`uv run temporal-worker` or `python -m app.temporal.worker`).
2. Run the client script that starts `HospitalCapacityWorkflow`
   (`uv run temporal-client` or `python -m app.temporal.client`).
3. The workflow will call `run_agent`, then automatically call
   `generate_daily_briefing`, then continue with the human-approval flow.
   Check `aava_output/` for the new JSON file.

## 10. Known limitations / things not yet done

- The briefing is not sent anywhere outside the local file (no Slack/email
  yet) — this was an explicit choice for now, easy to add later.
- If the AAVA call fails or times out, the workflow logs it and moves on;
  there's no retry policy configured yet on that specific activity call.
- The full end-to-end run through the actual Temporal worker/server has not
  been executed in this environment (only the direct function-call path via
  the test script has been verified against the real AAVA API).
