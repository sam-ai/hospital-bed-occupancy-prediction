# Hospital AI Agent Twin — Implementation Plan

> Comprehensive phased fix plan for pre-hackathon hardening. Each section
> specifies the exact file, line, change, and rationale.

---

## Table of Contents

1. [User Decisions (Locked)](#1-user-decisions-locked)
2. [Phase 0 — Documentation Fix](#2-phase-0--documentation-fix)
3. [Phase 1 — Critical Code Fixes](#3-phase-1--critical-code-fixes)
   - 1a. Pin pyproject.toml versions
   - 1b. Fix Dockerfile.backend COPY path
   - 1c. Pin elasticsearch==7.17.* client
   - 1d. Fix accuracy doc ID for unit_id
   - 1e. Thread unit_id through WS + chart + gateway
   - 1f. Send step_index from ForecastTimelineChart
   - 1g. Fix interactive deadlock (FastAPI syncio)
   - 1h. Global purge loop for all 4 wards
   - 1i. Per-ward regime regeneration
4. [Phase 2 — Ward Consistency](#4-phase-2--ward-consistency)
   - 2a. Per-ward Temporal schedules
   - 2b. ScenarioRequest / BacktestRequest Field fix
   - 2c. Explicit CORS config
   - 2d. Update docs for all changes
5. [Dependency Graph](#5-dependency-graph)
6. [Estimated Effort](#6-estimated-effort)
7. [Verification After Each Phase](#7-verification-after-each-phase)

---

## 1. User Decisions (Locked)

| Decision | Choice | Rationale |
|---|---|---|
| 3D scope | **ICU-only** (single `sim_engine` at `gateway.py:97`) | Other wards get forecast charts only; 10-bed demo floor is ICU |
| ES version | **Stay on ES 7.17.20** + pin client to 7.17.* | Avoid breaking index/mapping changes |
| Regime switch | **Global** (all 4 wards purge + regenerate together) | Consistent data state |
| WS reconnect | **Acceptable as known limitation** | Not required before judging |

---

## 2. Phase 0 — Documentation Fix

### 2.0.1 Update STARTUP.md Section 8 (known limitations)

**File:** `docs/STARTUP.md:220-231`

**Current text** (lines 220-223):
```markdown
- **Single-tenant 10-bed floor:** `app/api/gateway.py:97`
  `sim_engine = HospitalSimulationEngine(total_beds=10)` is shared across all
  connections. Dashboards are ward-aware; the 3D floor is not. Acceptable for
  hackathon demo.
```

**Replace with:**
```markdown
- **3D is ICU-EAST only (Option A):** `app/api/gateway.py:97`
  `sim_engine = HospitalSimulationEngine(total_beds=10)` is a single
  shared singleton. The 3D floor renders 10 ICU beds for the selected ward.
  Non-ICU wards (`GENERAL-MALE`, `GENERAL-FEMALE`, `STEP-DOWN`) show
  forecast charts, patient-flow cards, and StatusPanel badges only — no 3D.
```

**Also add** after line 238 (after the ES URL split note):
```markdown
- **Elasticsearch 7.17.20 pinned:** The backend client (`pyproject.toml`)
  uses `elasticsearch==7.17.*` to match the Docker image. All index
  mappings use ES 7 `body=` parameters. Do NOT upgrade the client to 8.x.
```

**Rationale:** Documents the locked architecture decisions so any future
reader (judges, teammates) understands the constraints.

---

## 3. Phase 1 — Critical Code Fixes

### 1a. Pin pyproject.toml versions

**File:** `pyproject.toml:17-25`

**Current (broken):**
```toml
"torch>=2.13.0",
"transformers>=5.15.1",
"numpy>=2.4.6",
"huggingface-hub>=1.28.0",
"accelerate>=1.14.0",
"timesfm>=2.0.2",
"scikit-learn>=1.5.0",
"watchfiles>=0.20.0",
"elasticsearch>=8.0.0",
```

**Replace with:**
```toml
"torch==2.4.1",
"transformers==4.46.3",
"numpy>=1.26.0,<3",
"huggingface-hub>=0.20.0,<1",
"accelerate>=0.25.0,<1",
"timesfm>=2.0.2",
"scikit-learn>=1.3.0,<2",
"watchfiles>=0.20.0",
"elasticsearch==7.17.*",
```

**Why each change:**

| Package | Old | New | Reason |
|---|---|---|---|
| `torch` | `>=2.13.0` | `==2.4.1` | 2.13 does not exist; 2.4.1 is latest stable CPU-friendly |
| `transformers` | `>=5.15.1` | `==4.46.3` | 5.x does not exist; 4.46.3 is TimesFM-compatible |
| `numpy` | `>=2.4.6` | `>=1.26.0,<3` | 2.4.6 does not exist yet; need numpy 1.x or 2.0-2.1 |
| `huggingface-hub` | `>=1.28.0` | `>=0.20.0,<1` | 1.x does not exist; latest is 0.29.x |
| `accelerate` | `>=1.14.0` | `>=0.25.0,<1` | 1.x does not exist; latest is 1.4.x but 0.x safer |
| `scikit-learn` | `>=1.5.0` | `>=1.3.0,<2` | Minor version bump is fine |
| `elasticsearch` | `>=8.0.0` | `==7.17.*` | Must match ES 7.17.20 Docker image; 8.x breaks `body=` params |

### 1b. Fix Dockerfile.backend COPY path

**File:** `Dockerfile.backend:14`

**Current (broken):**
```dockerfile
COPY backend/pyproject.toml ./pyproject.toml
```

**Replace with:**
```dockerfile
COPY pyproject.toml ./pyproject.toml
```

**Why:** The project root IS the backend. There is no `backend/` subdirectory.
The Dockerfile is at project root alongside `pyproject.toml` and `app/`.
`docker-compose.yml:110` already builds with `context: .` (project root), so
the COPY source must be `pyproject.toml`, not `backend/pyproject.toml`.

**Also add** after the COPY app line (line 20), before `COPY data/`:
```dockerfile
COPY scripts/ ./scripts/
```

**Rationale:** The `scripts/` directory contains `generate_mock_data.py` and
`ingest_to_elasticsearch.py` which may be needed inside the container for
seed operations.

### 1c. Pin elasticsearch==7.17.* client

**File:** `pyproject.toml:25` (covered in 1a above)

**File:** `app/data/elasticsearch_client.py:13`

**Current:**
```python
from elasticsearch import AsyncElasticsearch
```

**No change needed** — the import is fine for ES 7. The version pin in
`pyproject.toml` (1a) is the fix. The `body=` parameter usage throughout
`elasticsearch_client.py` is correct for ES 7.x client.

**Verify:** All `es_client.search(index=..., body={})` calls in the file
(lines 83, 162, etc.) use the ES 7 `body=` parameter. ES 8.x moved these to
keyword args (`query=`, `size=`, etc.) — pinning to 7.17.* keeps this working.

### 1d. Fix accuracy doc ID for unit_id

**File:** `app/data/elasticsearch_client.py:149`

**Current (broken):**
```python
doc_id = f"ACC_{day}_{record.get('horizon_type', '24H')}_{record.get('hospital_id', 'X')}"
```

**Problem:** Missing `unit_id` — per-ward accuracy records overwrite each
other. If ICU-EAST scores at 23:50, then GENERAL-MALE overwrites it at 23:51.

**Replace with:**
```python
doc_id = (
    f"ACC_{day}_{record.get('horizon_type', '24H')}"
    f"_{record.get('hospital_id', 'X')}_{record.get('unit_id', 'ICU-EAST')}"
)
```

**Impact:** Existing accuracy records in ES will be orphaned (old IDs won't
match the new pattern). This is fine — they'll be regenerated on next nightly
run. No migration needed.

**Also update** `STARTUP.md:173` (accuracy doc ID pattern in the index table):
```markdown
| `hospital-forecast-accuracy` | `day`, `horizon_type`, `mae`, `bias` | `ACC_{day}_{horizon}_{h}_{u}` | Accuracy activity |
```

### 1e. Thread unit_id through WS + chart + gateway

This is the most involved fix. The WebSocket handler in `gateway.py` does not
pass `unit_id` when running simulation playback, so the 3D and chart don't
update for the selected ward.

#### 1e-i. WS message protocol — add unit_id to select_ward payload

**File:** `app/api/gateway.py` (WS handler, around lines 289-310)

**Current flow:**
1. Client sends `{"type": "select_ward", "unit_id": "ICU-EAST"}`
2. Handler sets `current_ward = data.get("unit_id", "ICU-EAST")`
3. But does NOT pass `unit_id` to simulation or chart update

**Add** after `current_ward = data.get("unit_id", "ICU-EAST")` (around line 295):
```python
# Store ward context for downstream simulation
ws_ward_context = {"unit_id": current_ward}
```

**And** in the simulation broadcast block (around line 340-380), include
`unit_id` in every broadcast message:
```python
await manager.broadcast({
    "type": "forecast_update",
    "unit_id": ws_ward_context["unit_id"],
    "step_index": step_idx,
    "total_steps": total_steps,
    "predicted_occupancy": point["predicted_occupancy"],
    "predicted_occupied_beds": point["predicted_occupied_beds"],
    # ... existing fields
})
```

#### 1e-ii. ForecastTimelineChart — accept and send unitId

**File:** `frontend/src/components/ForecastTimelineChart.tsx`

**Add prop:**
```typescript
interface ForecastTimelineChartProps {
  isOpen: boolean;
  onClose: () => void;
  onPlaySimulation?: (stepIndex: number) => void;
  unitId?: string;  // <-- ADD THIS
}
```

**Update fetchData** (around line 262) to use the prop:
```typescript
const unitParam = unitId || "ICU-EAST";
const response = await fetch(
  `/api/forecast/multi-horizon?horizon_type=24H&unit_id=${unitParam}`
);
```

**Also update accuracy fetch** (around line 314):
```typescript
const accRes = await fetch(
  `/api/forecast/accuracy?horizon_type=24H&unit_id=${unitParam}&days=7`
);
```

#### 1e-iii. page.tsx — pass wardId to ForecastTimelineChart

**File:** `frontend/src/app/page.tsx`

**Current** (around line 656):
```tsx
<ForecastTimelineChart
  isOpen={showChart}
  onClose={() => setShowChart(false)}
  onPlaySimulation={handlePlaySimulation}
/>
```

**Replace with:**
```tsx
<ForecastTimelineChart
  isOpen={showChart}
  onClose={() => setShowChart(false)}
  onPlaySimulation={handlePlaySimulation}
  unitId={wardId}
/>
```

Where `wardId` is the currently selected ward tab state variable (already
exists as `wardId` in `page.tsx` from the ward-selector work).

### 1f. Send step_index from ForecastTimelineChart

**File:** `frontend/src/components/ForecastTimelineChart.tsx`

**Current** `handleStepSelect` (around line 441):
```typescript
const handleStepSelect = (stepIndex: number) => {
  onPlaySimulation?.(stepIndex);
  setSelectedStep(stepIndex);
};
```

**This is correct** — `stepIndex` is already passed. The issue is that
`page.tsx:handlePlaySimulation` does not forward it to the WS.

**File:** `frontend/src/app/page.tsx` — `handlePlaySimulation`

**Current:**
```typescript
const handlePlaySimulation = async (stepIndex: number) => {
  if (wsRef.current?.readyState === WebSocket.OPEN) {
    wsRef.current.send(JSON.stringify({
      type: "run_simulation",
      step_index: stepIndex,
    }));
  }
};
```

**This is already correct** — `step_index` IS sent. The real bug is in
`gateway.py` where the WS handler ignores `step_index` and replays from step 0.

**File:** `app/api/gateway.py` — WS handler `run_simulation` block

**Current** (around line 390-420):
```python
elif data.get("type") == "run_simulation":
    # Runs full forecast playback from step 0
    ...
```

**Add `step_index` support:**
```python
elif data.get("type") == "run_simulation":
    start_step = int(data.get("step_index", 0))
    # Filter points to start from start_step
    filtered_points = [
        p for p in forecast_points
        if int(p.get("time_step_index", 1)) >= start_step
    ] if forecast_points else []
    # ... use filtered_points instead of forecast_points
```

### 1g. Fix interactive deadlock (FastAPI syncio)

**File:** `app/api/gateway.py`

**Problem:** `asyncio.get_event_loop().run_in_executor(None, ...)` inside
`async def` endpoints blocks the FastAPI event loop when the executor thread
calls back into async code. This causes the interactive chart to freeze when
clicking steps.

**File:** `app/forecasting/strategy_service.py:31-35`

**Current:**
```python
def get_predictor() -> TimesFMHospitalPredictor:
    global _predictor_singleton
    if _predictor_singleton is None:
        _predictor_singleton = TimesFMHospitalPredictor()
    return _predictor_singleton
```

**Replace with async-compatible singleton:**
```python
_predictor_singleton: TimesFMHospitalPredictor | None = None
_predictor_lock = asyncio.Lock()

async def get_predictor() -> TimesFMHospitalPredictor:
    global _predictor_singleton
    if _predictor_singleton is None:
        async with _predictor_lock:
            if _predictor_singleton is None:
                _predictor_singleton = TimesFMHospitalPredictor()
    return _predictor_singleton
```

**And** update all callers to use `await get_predictor()`:
- `run_curve()` (line 59): `predictor = await get_predictor()`
- `run_backtest()` (line 255): `predictor = await get_predictor()`

**Also** in `gateway.py`, replace any `run_in_executor` pattern with direct
async calls:
```python
# BEFORE (blocks):
loop = asyncio.get_event_loop()
result = await loop.run_in_executor(None, sync_function, args)

# AFTER (non-blocking):
result = await async_function(args)
```

### 1h. Global purge loop for all 4 wards

**File:** `app/api/gateway.py:455-456`

**Current (broken — ICU-EAST only):**
```python
await es_client.delete_by_query(
    index="hospital-snapshots",
    body={"query": {"term": {"census.unit_id": "ICU-EAST"}}},
)
```

**Replace with loop over all wards:**
```python
from app.data.wards import WARDS_BY_ID

for ward_id in WARDS_BY_ID:
    try:
        await es_client.delete_by_query(
            index="hospital-snapshots",
            body={
                "query": {
                    "bool": {
                        "must": [
                            {"term": {"hospital_id": "HOSPITAL-MAIN-01"}},
                            {"term": {"census.unit_id": ward_id}},
                        ]
                    }
                }
            },
            refresh=True,
        )
        print(f"[✓] Purged snapshots for {ward_id}")
    except Exception as e:
        print(f"[!] Purge failed for {ward_id}: {e}")
```

**Also purge** forecast, accuracy, features, and flow indices:
```python
for index_name in [
    "hospital-forecast-timeline",
    "hospital-forecast-accuracy",
    "hospital-features",
    "hospital-patient-flow",
]:
    try:
        await es_client.delete_by_query(
            index=index_name,
            body={"query": {"match_all": {}}},
            refresh=True,
        )
        print(f"[✓] Purged {index_name}")
    except Exception as e:
        print(f"[!] Purge {index_name} failed: {e}")
```

**Rationale:** Regime switch must produce a consistent state across ALL wards
and ALL index types. Stale forecast docs from a previous regime mixed with
new snapshot data would produce misleading charts.

### 1i. Per-ward regime regeneration

**File:** `app/data/mock_regimes.py:122-128`

**Current:**
```python
def generate_scenario_data(
    scenario: str = "outbreak_surge",
    hospital_id: str = "HOSPITAL-MAIN-01",
    unit_id: str = "ICU-EAST",
    total_beds: int = 10,
    days: int = 30,
    seed: int | None = None,
) -> list[CompleteHospitalSnapshot]:
```

**This function is fine** — it already accepts `unit_id` as a parameter.
The fix is in the caller.

**File:** `app/api/gateway.py` — `regenerate_mock_regime` handler

**Current** (around line 414-509):
```python
@router.post("/api/mock/regenerate")
async def regenerate_mock_regime(request: RegenerateRequest):
    # Generates for single ward only
    snapshots = generate_scenario_data(...)
    # Ingests all snapshots
    ...
```

**Replace with per-ward loop:**
```python
from app.data.wards import WARDS_BY_ID
from app.data.mock_regimes import generate_scenario_data

@router.post("/api/mock/regenerate")
async def regenerate_mock_regime(request: RegenerateRequest):
    all_snapshots = []
    for ward_id, ward in WARDS_BY_ID.items():
        ward_snaps = generate_scenario_data(
            scenario=request.scenario,
            hospital_id="HOSPITAL-MAIN-01",
            unit_id=ward_id,
            total_beds=ward.total_beds,
            days=request.days,
        )
        all_snapshots.extend(ward_snaps)
        print(f"[✓] Generated {len(ward_snaps)} snapshots for {ward_id}")

    # Bulk ingest all ward snapshots
    # ... (existing bulk logic)

    # Optionally trigger forecast backfill for all wards
    if request.trigger_forecast:
        # Trigger DailyForecastWorkflow for each ward
        ...
```

**Also update** the `ScenarioRequest` and `BacktestRequest` in
`app/api/timeline_router.py` — see Phase 2b.

---

## 4. Phase 2 — Ward Consistency

### 2a. Per-ward Temporal schedules

**File:** `app/temporal/worker.py:32-57`

**Current:** 4 schedules, all targeting `ICU-EAST` implicitly via
`scheduled_workflow.py` defaults.

**Replace `SCHEDULE_DEFINITIONS` with per-ward expansion:**
```python
from app.data.wards import WARDS

WARD_IDS = list(WARDS.keys())  # ["ICU-EAST", "GENERAL-MALE", "GENERAL-FEMALE", "STEP-DOWN"]

SCHEDULE_DEFINITIONS = []
for ward_id in WARD_IDS:
    ward_suffix = ward_id.lower().replace("-", "")
    SCHEDULE_DEFINITIONS.extend([
        (
            f"daily-forecast-{ward_suffix}-9am",
            DailyForecastWorkflow,
            run_daily_forecast_activity,
            "0 9 * * *",
            ward_id,
        ),
        (
            f"weekly-forecast-{ward_suffix}-mon-8am",
            WeeklyForecastWorkflow,
            run_weekly_forecast_activity,
            "0 8 * * 1",
            ward_id,
        ),
        (
            f"monthly-forecast-{ward_suffix}-1st-8am",
            MonthlyForecastWorkflow,
            run_monthly_forecast_activity,
            "0 8 1 * *",
            ward_id,
        ),
        (
            f"nightly-accuracy-{ward_suffix}-2350",
            ForecastAccuracyWorkflow,
            run_forecast_accuracy_activity,
            "50 23 * * *",
            ward_id,
        ),
    ])
```

**Also update `_ensure_schedules`** (line 60-85) to accept and pass `ward_id`:
```python
async def _ensure_schedules(client: Client) -> None:
    for schedule_id, workflow_cls, activity_fn, cron, ward_id in SCHEDULE_DEFINITIONS:
        try:
            await client.create_schedule(
                schedule_id,
                Schedule(
                    action=ScheduleActionStartWorkflow(
                        workflow_cls.run,
                        args=["HOSPITAL-MAIN-01", ward_id],
                        id=f"{schedule_id}-workflow",
                        task_queue=TEMPORAL_TASK_QUEUE,
                    ),
                    spec=ScheduleSpec(cron_expressions=[cron]),
                ),
            )
            print(f"[✓] Created schedule '{schedule_id}' (cron: {cron}, ward: {ward_id})")
        except (ScheduleAlreadyRunningError, RPCError) as e:
            already_running = isinstance(e, ScheduleAlreadyRunningError) or (
                getattr(e, "status", None) == 6
                or "already exists" in str(e).lower()
                or "already running" in str(e).lower()
            )
            if not already_running:
                raise
            print(f"[*] Schedule '{schedule_id}' already exists — skipping creation.")
```

**Result:** 16 schedules (4 wards × 4 schedule types) instead of 4.

### 2b. ScenarioRequest / BacktestRequest Field fix

**File:** `app/api/timeline_router.py:186,213`

**Current:**
```python
class ScenarioRequest(BaseModel):
    hospital_id: str = Query("HOSPITAL-MAIN-01")
    unit_id: str = Query("ICU-EAST")
    ...

class BacktestRequest(BaseModel):
    hospital_id: str = Query("HOSPITAL-MAIN-01")
    unit_id: str = Query("ICU-EAST")
    ...
```

**Problem:** `Query()` inside `BaseModel` is a FastAPI anti-pattern. `Query()`
is for endpoint parameters, not Pydantic model fields. This works by accident
in some versions but breaks validation and OpenAPI docs.

**Replace with:**
```python
class ScenarioRequest(BaseModel):
    hospital_id: str = "HOSPITAL-MAIN-01"
    unit_id: str = "ICU-EAST"
    bed_delta: int = 0
    elective_deferral_pct: float = 0.0
    er_surge_pct: float = 0.0
    horizon: int = 168

class BacktestRequest(BaseModel):
    hospital_id: str = "HOSPITAL-MAIN-01"
    unit_id: str = "ICU-EAST"
    days: int = 14
    persist_curves: bool = False
```

### 2c. Explicit CORS config

**File:** `app/api/gateway.py:61`

**Current:**
```python
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
```

**Replace with explicit origins:**
```python
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost:3001",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
```

**Rationale:** `allow_origins=["*"]` with `allow_credentials=True` violates
the CORS spec (browsers reject it). While this works in local dev because
browsers are lenient, it's a latent bug. Explicit origins are also more
secure for any deployment beyond localhost.

### 2d. Update docs for all changes

**File:** `docs/STARTUP.md`

Updates after all phases complete:

1. **Section 4 (Ingest):** Remove the "Known limitation: purges only ICU-EAST" note
   (line 116-117). Replace with: "Regime switch now purges and regenerates
   all 4 wards globally."

2. **Section 5 (Schedules table):** Update from 4 ICU-EAST-only schedules to
   16 per-ward schedules. Update backfill script to remove the per-ward
   loop (it's now automatic).

3. **Section 6 (Index table):** Update accuracy doc ID pattern to include `unit_id`.

4. **Section 8 (Constraints):** Update 3D scope note (done in Phase 0). Remove
   the "schedules target ICU-EAST only" note. Remove the "regime switch
   purges only ICU-EAST" note.

**File:** `docs/JOURNEY.md`

No structural changes needed — the journey already covers all 4 wards.
Update any references to "ICU-EAST-only schedules" if present.

---

## 5. Dependency Graph

```
Phase 0 (STARTUP.md)
  └── no dependencies, can be done first

Phase 1a (pyproject.toml)
  └── must be done before Docker build works

Phase 1b (Dockerfile)
  └── depends on 1a (needs valid pyproject.toml)

Phase 1c (elasticsearch pin)
  └── included in 1a

Phase 1d (accuracy doc ID)
  └── no dependencies

Phase 1e (unit_id threading)
  ├── 1e-i (gateway.py WS handler)
  ├── 1e-ii (ForecastTimelineChart props)
  └── 1e-iii (page.tsx prop pass)
      └── depends on 1e-ii

Phase 1f (step_index in WS)
  └── depends on 1e (unit_id context)

Phase 1g (async deadlock)
  └── no dependencies, but test with 1e+1f

Phase 1h (global purge)
  └── no dependencies

Phase 1i (per-ward regime)
  └── depends on 1h (purge must be global first)

Phase 2a (per-ward schedules)
  └── depends on 1i (per-ward data generation)

Phase 2b (Request models)
  └── no dependencies

Phase 2c (CORS)
  └── no dependencies

Phase 2d (docs update)
  └── depends on ALL prior phases
```

**Recommended execution order:**
```
1a → 1b → 1c → 1d → 1h → 1i → 1g → 1e → 1f → 2a → 2b → 2c → 0 → 2d
```

---

## 6. Estimated Effort

| Phase | Task | Est. Time | Risk |
|---|---|---|---|
| 0 | STARTUP.md update | 5 min | Low |
| 1a | pyproject.toml pins | 5 min | Low — test with `pip install -e .` |
| 1b | Dockerfile COPY fix | 5 min | Low — test with `docker build` |
| 1c | ES client pin | 0 min | Done in 1a |
| 1d | Accuracy doc ID | 5 min | Low |
| 1e | unit_id threading | 30 min | Medium — 3 files, WS protocol |
| 1f | step_index WS | 15 min | Low — gateway.py only |
| 1g | Async deadlock fix | 20 min | Medium — must test event loop |
| 1h | Global purge | 10 min | Low |
| 1i | Per-ward regime | 15 min | Low |
| 2a | Per-ward schedules | 15 min | Low — expand loop |
| 2b | Request model fix | 5 min | Low |
| 2c | CORS fix | 5 min | Low |
| 2d | Docs update | 10 min | Low |
| **Total** | | **~2.5 hours** | |

---

## 7. Verification After Each Phase

### After Phase 1a + 1b (build fixes):
```bash
# Verify pyproject.toml is parseable
python -c "import tomllib; tomllib.load(open('pyproject.toml','rb'))"

# Verify Docker build succeeds
docker build -f Dockerfile.backend -t hospital-backend-test .
# Should complete without COPY errors or pip resolution failures
```

### After Phase 1d (accuracy doc ID):
```bash
# Verify per-ward accuracy docs have unique IDs
curl -s 'http://localhost:9200/hospital-forecast-accuracy/_search' \
  -H 'Content-Type: application/json' \
  -d '{"size":10,"_source":["_id","unit_id","day"]}' | jq '.hits.hits[]._id'
# Should show IDs like ACC_2026-08-27_24H_HOSPITAL-MAIN-01_ICU-EAST
```

### After Phase 1e + 1f (WS + chart threading):
```bash
# Start services, open http://localhost:3000
# 1. Click "Male" tab → StatusPanel shows GENERAL-MALE census
# 2. Click "Forecast Timeline" → chart loads with GENERAL-MALE data
# 3. Click a bar → 3D simulation starts from that step (not step 0)
# 4. Verify WebSocket messages include unit_id field
#    (browser DevTools → Network → WS → Messages)
```

### After Phase 1g (async deadlock):
```bash
# Open http://localhost:3000 → Forecast Timeline
# Click rapidly on different bars — should not freeze
# Check terminal: no "blocking the event loop" warnings
```

### After Phase 1h + 1i (global purge + per-ward regime):
```bash
# Trigger regime switch
curl -s -X POST http://localhost:8000/api/mock/regenerate \
  -H 'Content-Type: application/json' \
  -d '{"scenario":"volatile","days":30,"trigger_forecast":true}' | jq

# Verify all 4 wards have fresh snapshots
curl -s 'http://localhost:9200/hospital-snapshots/_search' \
  -H 'Content-Type: application/json' \
  -d '{"size":0,"aggs":{"wards":{"terms":{"field":"census.unit_id","size":10}}}}' | jq .aggregations.wards.buckets
# Should show 4 buckets, each with ~720 docs (30 days × 24 hours)

# Verify forecast indices are empty (purged) then repopulated
curl -s 'http://localhost:9200/hospital-forecast-timeline/_count' | jq .count
```

### After Phase 2a (per-ward schedules):
```bash
# Check Temporal UI at http://localhost:8080 → Schedules tab
# Should show 16 active schedules (4 wards × 4 types)
# Verify schedule IDs: daily-forecast-icueast-9am, daily-forecast-generalmale-9am, etc.
```

### After Phase 2b (Request models):
```bash
# Check OpenAPI docs at http://localhost:8000/docs
# ScenarioRequest and BacktestRequest should show proper field types
# No more "Query" annotations in the model schema
```

### After Phase 2c (CORS):
```bash
# Verify frontend can reach backend
curl -v -H "Origin: http://localhost:3000" http://localhost:8000/api/health 2>&1 | grep "access-control-allow-origin"
# Should show: access-control-allow-origin: http://localhost:3000
```

### Final smoke test (all phases):
```bash
# 1. Full rebuild
docker compose down -v && docker compose up -d postgresql elasticsearch
docker compose up -d temporal temporal-ui
docker compose up -d --build fastapi-gateway temporal-worker

# 2. Seed data
curl -s -X POST http://localhost:8000/api/mock/regenerate \
  -H 'Content-Type: application/json' \
  -d '{"scenario":"balanced","days":30,"trigger_forecast":true}' | jq

# 3. Verify all endpoints
curl -s http://localhost:8000/api/health | jq
curl -s http://localhost:8000/api/forecast/wards | jq '.wards | length'
curl -s 'http://localhost:8000/api/forecast/multi-horizon?horizon_type=24H&unit_id=ICU-EAST' | jq .total_points
curl -s 'http://localhost:8000/api/forecast/multi-horizon?horizon_type=24H&unit_id=GENERAL-MALE' | jq .total_points

# 4. Open frontend, click through all 4 ward tabs, verify:
#    - StatusPanel shows correct census per ward
#    - Forecast chart loads per ward
#    - 3D floor works for ICU-EAST
#    - Patient flow shows per ward
#    - No console errors
```
