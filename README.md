# Hospital Bed Capacity & AI Agentic Platform

A production-ready **Agentic AI platform** for hospital bed occupancy forecasting and capacity management. Combines **Temporal** for durable workflow orchestration, **LangGraph** for multi-agent reasoning, **Google Gemini** for natural-language synthesis, **Statistical ML** for deterministic time-series forecasting, and a **Deterministic Policy Engine** for human-in-the-loop safety.

---

## Architecture

```
                               ┌─────────────────────────────────────────┐
                               │           TEMPORAL WORKFLOW              │
                               │      (HospitalCapacityWorkflow)         │
                               └────────────────────┬────────────────────┘
                                                    │
                                                    ▼
                               ┌─────────────────────────────────────────┐
                               │            TEMPORAL ACTIVITY             │
                               │               (run_agent)               │
                               └────────────────────┬────────────────────┘
                                                    │
                                                    ▼
┌────────────────────────────────────────────────────────────────────────────────┐
│                           LANGGRAPH AGENT PIPELINE                              │
│                                                                                │
│   Wrangling ──► Monitoring ──► Forecast ──► Anomaly ──► Recommendation ──► Out │
│   (MCP Data)    (Signals)      (ML Model)   (Threshold)  (Gemini LLM)          │
│                                                              │                 │
│                                                              ▼                 │
│                                                      Policy Engine             │
│                                                    (ALLOW / APPROVE)           │
└────────────────────────────────────────────────────────────────────────────────┘
                                                    │
                                                    ▼
                               ┌───────────────────────────────┐
                               │   HUMAN APPROVAL GATEWAY      │
                               │   (Temporal Signal / Wait)    │
                               └───────────────────────────────┘
```

## Key Principles

1. **LLMs do NOT calculate predictions** — Numerical forecasting is performed by the `StatisticalOccupancyModel` (deterministic ML).
2. **Policy decisions are deterministic** — The `HospitalPolicyEngine` blocks or requires human approval for high-risk actions.
3. **Temporal manages durability** — Pauses workflows safely for human approval without losing state.
4. **LangGraph manages multi-agent context** — Specialist subgraphs handle data wrangling, monitoring, and synthesis.

---

## Project Structure

```
hospital-ai/
├── pyproject.toml          # Dependencies and project metadata
├── .env.example            # Environment variable template
├── README.md
├── app/
│   ├── config.py           # Configuration (env vars)
│   ├── models.py           # Pydantic data models
│   ├── data/
│   │   └── mock_mcp.py     # Mock MCP clients (hospital + signals)
│   ├── forecasting/
│   │   ├── features.py     # Feature engineering
│   │   ├── model.py        # Statistical occupancy model
│   │   ├── service.py      # Forecast service
│   │   └── controller.py   # Controller with validation
│   ├── anomaly/
│   │   └── service.py      # Threshold-based anomaly detection
│   ├── policy/
│   │   └── engine.py       # Deterministic safety policy
│   ├── agents/
│   │   ├── state.py        # LangGraph shared state
│   │   ├── wrangling.py    # Data wrangling subgraph
│   │   ├── monitoring.py   # External signal monitoring subgraph
│   │   ├── recommendation.py  # Gemini-powered recommendations
│   │   └── hospital_graph.py  # Main agent graph (full pipeline)
│   └── temporal/
│       ├── activities.py   # Temporal activity (wraps agent)
│       ├── workflows.py    # Workflow with human approval signal
│       ├── worker.py       # Temporal worker process
│       └── client.py       # Workflow client (trigger + approve)
└── tests/
    ├── test_agent.py       # Full pipeline test
    ├── test_subgraphs.py   # Subgraph unit tests
    └── test_recommendation.py  # Recommendation + policy test
```

---

## Quick Start

### Prerequisites

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) package manager
- [Temporal CLI](https://docs.temporal.io/cli) (for full workflow execution)

### 1. Install Dependencies

```bash
cd D:\hackathon_asc
uv sync
```

### 2. Configure Environment

```bash
copy .env.example .env
# Edit .env and add your GOOGLE_API_KEY (from Google AI Studio)
```

> **Note:** The platform works without a Gemini API key — it falls back to deterministic recommendations.

### 3. Run Standalone Agent Test (No Temporal needed)

```bash
uv run python tests/test_agent.py
```

Expected output:
```
Running standalone LangGraph full pipeline test...
============================================================
AGENT RESULT SUMMARY
============================================================
Request ID     : TEST-LOCAL-01
Hospital/Unit  : HOSPITAL-MAIN-01 / ICU-EAST
Data Quality   : usable (score=0.99)
Forecast       : occupancy_drift_v1 (24h horizon)
Max Occupancy  : 98.72%
Anomaly        : capacity_exhaustion_risk [critical]
Recommendation : Capacity Surge Escalation (priority=critical)
Policy Decision: HUMAN_APPROVAL
============================================================
[SUCCESS] Full LangGraph pipeline verified!
```

### 4. Install Temporal CLI (Windows)

Download from:
```
https://temporal.download/cli/archive/latest?platform=windows&arch=amd64
```

Extract the archive and add `temporal.exe` to your PATH.

### 5. Start Temporal Dev Server

```bash
temporal server start-dev
```

This starts:
- Temporal Server on `localhost:7233`
- Temporal Web UI on `http://localhost:8233`

### 6. Run the Temporal Worker

Open a new terminal:
```bash
cd D:\hackathon_asc
uv run python -m app.temporal.worker
```

### 7. Trigger the Workflow

Open another terminal:
```bash
cd D:\hackathon_asc
uv run python -m app.temporal.client
```

The client will:
1. Start the `HospitalCapacityWorkflow`
2. Execute the full LangGraph agent pipeline
3. Wait 3 seconds, then send a human approval signal
4. Print the final formatted result

---

## Technology Stack

| Component | Technology | Purpose |
|-----------|-----------|---------|
| Workflow Orchestration | Temporal SDK 1.31 | Durable execution, human-in-the-loop signals |
| Multi-Agent Engine | LangGraph 1.2 | StateGraph pipeline, subgraph composition |
| LLM Synthesis | Google Gemini 2.0 Flash | Natural-language recommendation generation |
| LLM Framework | LangChain (langchain-google-genai) | Unified LLM interface |
| Forecasting | Statistical ML (Python) | Deterministic occupancy drift model |
| Safety | Deterministic Policy Engine | ALLOW / HUMAN_APPROVAL / BLOCK decisions |
| Data Models | Pydantic 2.13 | Type-safe validation and serialization |
| Package Manager | uv | Fast dependency resolution |

---

## How It Works

### Pipeline Flow

1. **Data Wrangling** — Fetches hospital bed/staff state via MCP client
2. **Monitoring** — Retrieves external epidemiological signals (viral index, weather)
3. **Forecasting** — Builds features → runs statistical occupancy model → 24h forecast
4. **Anomaly Detection** — Threshold check: ≥95% upper bound = critical, ≥85% predicted = high
5. **Recommendation** — If anomaly detected, generates operational recommendation (Gemini LLM or fallback)
6. **Policy Gate** — Deterministic check: critical/high recommendations require human approval
7. **Human Approval** — Temporal pauses workflow, waits for signal (up to 24h)
8. **Result** — Final `AgentResult` with all data, recommendations, and approval status

### Human-in-the-Loop

When the policy engine requires `HUMAN_APPROVAL`, the Temporal workflow:
- Pauses without consuming resources
- Waits for a signal (e.g., from a dashboard, Slack bot, or CLI)
- Resumes with the decision (approved/rejected)
- Times out after 24 hours if no response

---

## Development

### Run All Tests

```bash
uv run python tests/test_agent.py
uv run python tests/test_subgraphs.py
uv run python tests/test_recommendation.py
```

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `TEMPORAL_HOST` | `localhost:7233` | Temporal server address |
| `TEMPORAL_NAMESPACE` | `default` | Temporal namespace |
| `TEMPORAL_TASK_QUEUE` | `hospital-ai-queue` | Task queue name |
| `GOOGLE_API_KEY` | (empty) | Google AI Studio API key |
| `GOOGLE_MODEL` | `gemini-2.0-flash` | Gemini model ID |
| `USE_LLM` | `true` | Enable/disable LLM (falls back to deterministic) |

---

---

## Docker Compose (Full Infrastructure)

The project includes a complete Docker Compose setup that spins up the entire platform:

| Container | Image | Port | Purpose |
|-----------|-------|------|---------|
| `hospital-postgres` | postgres:15-alpine | 5432 | Temporal persistence |
| `hospital-elasticsearch` | elasticsearch:7.17.20 | 9200 | Temporal visibility |
| `hospital-temporal-server` | temporalio/auto-setup:1.24.2 | 7233 | Temporal server |
| `hospital-temporal-ui` | temporalio/ui:2.28.0 | 8080 | Temporal Web Dashboard |
| `hospital-fastapi-gateway` | Custom (Python) | 8000 | FastAPI WebSocket Gateway |
| `hospital-temporal-worker` | Custom (Python) | — | Temporal Worker (agent) |
| `hospital-claw3d-frontend` | Custom (Next.js) | 3000 | 3D Hospital Floor UI |

### Quick Start with Docker

```bash
# 1. Set your Gemini API key in .env
#    Edit .env and replace "your-google-api-key-here" with your actual key

# 2. Spin up all 7 services
docker compose up --build -d

# 3. Check services are running
docker compose ps

# 4. Open in browser:
#    - 3D Hospital UI:     http://localhost:3000
#    - FastAPI Swagger:    http://localhost:8000/docs
#    - Temporal Dashboard: http://localhost:8080
```

---

## Local Startup Guide — 6 Backend Services (Docker) + UI (Local Node)

Run everything in Docker **except** the Next.js UI, which runs locally via Node for hot-reload during development.

### Prerequisites

- Docker Desktop (or Engine + Compose v2) running
- A `.env` file at project root with at least `GOOGLE_API_KEY=<your-key>`
  *(optional: `OPENAI_API_KEY`, `USE_LLM=true` — without keys the platform runs on deterministic fallbacks)*
- Node.js 18+ (for the UI)
- Free ports: 5432, 9200, 7233, 8080, 8000, 3000

### Step-by-step

```bash
# 1. Infrastructure first — wait until healthy before continuing
docker compose up -d postgresql elasticsearch temporal
docker compose ps          # hospital-postgres & hospital-elasticsearch should show "(healthy)"

# 2. Temporal debug UI (optional but recommended)
docker compose up -d temporal-ui        # http://localhost:8080

# 3. Backend services
#    First build can take several minutes (torch CPU wheel is cached per-layer).
docker compose up -d --build fastapi-gateway temporal-worker

# 4. Verify everything
docker compose ps                        # 6 containers Up
curl http://localhost:8000/api/health    # {"status": "healthy", ...}
curl http://localhost:9200/_cluster/health | grep status   # "yellow"/"green" is fine

# 5. Seed demo data + generate forecasts
uv run python scripts/generate_mock_data_10_beds.py --scenario outbreak_surge
uv run python scripts/ingest_to_elasticsearch.py

# Trigger today's forecast via the Temporal schedule
# (first run downloads the TimesFM model ~200MB into the hf-cache volume)
uv run python -c "
import asyncio
from temporalio.client import Client
async def main():
    c = await Client.connect('localhost:7233', namespace='default')
    await c.get_schedule_handle('daily-forecast-9am').trigger()
    print('triggered')
asyncio.run(main())
"

# 6. Run the UI locally (NOT in docker)
cd frontend
npm install
npm run dev              # http://localhost:3000
```

### What you should see

| Check | Expected |
|---|---|
| `docker compose ps` | 6 containers Up, postgres/ES healthy |
| Worker logs (`docker compose logs -f temporal-worker`) | `[✓] Created schedule …` ×4, then `Listening for tasks...` |
| `GET /api/forecast/multi-horizon` | forecast points ~1–2 min after step 5's trigger |
| http://localhost:3000 | 3D floor with all sidebar panels |

### Registered cron schedules (auto-created by the worker)

| Schedule | Cron | Produces |
|----------|------|----------|
| `daily-forecast-9am` | `0 9 * * *` | 24H hourly occupancy forecast |
| `weekly-forecast-mon-8am` | `0 8 * * 1` | 7-day daily forecast |
| `monthly-forecast-1st-8am` | `0 8 1 * *` | 6-month trend forecast |
| `nightly-accuracy-2350` | `50 23 * * *` | Forecast-vs-actual accuracy scoring |

### Common gotchas

- **First forecast is slow** — TimesFM model download (~200MB); cached afterwards in the shared `hf-cache` volume (gateway + worker)
- **"No Workers polling"** in the Temporal UI → worker crashed on startup; check `docker compose logs temporal-worker`
- **Elasticsearch won't start** (Linux hosts) — raise `vm.max_map_count`: `sudo sysctl -w vm.max_map_count=262144`, then retry
- Port conflicts → stop any local Postgres/Elasticsearch instances first

### Stop & clean up

```bash
docker compose stop claw3d-frontend   # keep the app UI out of the way if it was started
docker compose down                   # stop all containers
docker compose down -v                # stop + remove volumes (full reset incl. ES data)
```

### Docker Compose Lifecycle

1. Open `http://localhost:3000` in your browser
2. Click **"Run Full Capacity Check"**
3. Open `http://localhost:8080` to see the workflow running in Temporal
4. Watch the **Human Approval Modal** appear in the 3D UI
5. Click **"Authorize Action"** — workflow completes, patients animate to beds

### Stop & Clean Up

```bash
docker compose down           # Stop all containers
docker compose down -v        # Stop + remove volumes (reset database)
```

---

## 3D Visualization (Claw3D-based Hospital Floor)

The project includes a **3D hospital floor visualization** adapted from [Claw3D](https://github.com/iamlukethedev/Claw3D) (MIT licensed) using React Three Fiber + Three.js + Drei.

### Architecture

```
┌─────────────────────────────────────────────────────────────┐
│            PYTHON BACKEND (Temporal + LangGraph + ML)        │
│                                                             │
│  Temporal Workflow → LangGraph Agent → ML Forecast           │
│       │                                    │                │
│       │ Human Approval                     ▼                │
│       │ Signal                   Simulation Engine           │
│       ▼                          (3D events from forecast)  │
│  FastAPI WebSocket Gateway ◄────────────────┘               │
└─────────────────┬───────────────────────────────────────────┘
                  │ WebSocket (JSON events)
                  ▼
┌─────────────────────────────────────────────────────────────┐
│          NEXT.JS FRONTEND (Claw3D adaptation)               │
│                                                             │
│  ┌──────────────┐  ┌──────────────┐  ┌─────────────────┐   │
│  │ WebSocket    │  │ 3D Hospital  │  │ Human Approval  │   │
│  │ State Sync   │→ │ Floor (R3F)  │  │ Modal (Policy)  │   │
│  └──────────────┘  └──────────────┘  └─────────────────┘   │
└─────────────────────────────────────────────────────────────┘
```

### Running the Full Stack

**Terminal 1:** Temporal Dev Server
```bash
temporal server start-dev
```

**Terminal 2:** Temporal Worker
```bash
cd D:\hackathon_asc
uv run python -m app.temporal.worker
```

**Terminal 3:** FastAPI Gateway (bridges backend ↔ frontend)
```bash
cd D:\hackathon_asc
uv run uvicorn app.api.gateway:app --reload --port 8000
```

**Terminal 4:** Next.js 3D Frontend
```bash
cd D:\hackathon_asc\frontend
npm run dev
```

Then open **http://localhost:3000** to see the 3D hospital floor.

### What You'll See

1. **3D Hospital Floor** — 10 beds (6 occupied = red, 4 empty = green), nurse station, admission/discharge gates
2. **Click "Run Full Capacity Check"** — Triggers Temporal workflow → LangGraph agent → ML forecast
3. **Human Approval Modal** — Pops up when policy engine flags critical recommendations
4. **Click "Authorize Action"** — Sends approval signal back to Temporal via WebSocket
5. **Patient Simulation** — 3D characters animate walking to assigned beds based on forecast

### Demo Mode (No Temporal Required)

If you don't have Temporal installed yet, you can still demo the 3D visualization:

```bash
# Terminal 1: FastAPI Gateway
uv run uvicorn app.api.gateway:app --reload --port 8000

# Terminal 2: Frontend
cd frontend && npm run dev
```

Then click **"Simulate Patient Flow (Demo)"** in the UI — this runs the simulation engine directly without needing a Temporal server.


[*] Generating 30 days (720 hours) of hospital time-series mock data...
    Hospital: HOSPITAL-MAIN-01 | Unit: FLOOR-1 | Total Capacity: 10 Beds
    Floor Staffing: 3 Staff (2 Nurses + 1 Doctor)
    Occupancy Target: 5 to 10 simulated patients
    Features: Diurnal cycles, weekly patterns, outbreak surge on Day 15

[OK] Generated 720 hourly records
    Output: /Users/sambit/Documents/hackathon_asc/data/hospital_30day_mock_data_10_beds.json

    --- 10-Bed Floor Summary Statistics ---
    Total Capacity   : 10 Beds
    Occupancy Range  : 5 - 10 patients
    Mean Occupancy   : 6.2 beds
    Staffing On Duty : Min 2 (Nurses) | Max 3 (2 Nurses + 1 Doctor)
    Max ER Boarders  : 3 waiting patients
    Outbreak Peak    : Day 15+ (intensity up to 0.95)

    --- First Snapshot (Hour 0 - Baseline) ---
    Timestamp: 2026-07-22T23:35:12.514944+00:00
    Occupied : 6/10 beds
    Staffing : 2 (2 Nurses + 1 Doctor)
    ER Wait  : 1 (Boarders: 0)

    --- Last Snapshot (Hour 719 - Outbreak Surge) ---
    Timestamp: 2026-08-21T22:35:12.514944+00:00
    Occupied : 6/10 beds
    Staffing : 2 (2 Nurses + 1 Doctor)
    ER Wait  : 3 (Boarders: 2)

---

## License

MIT
