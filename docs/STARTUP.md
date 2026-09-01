# End-to-End Startup Runbook

> Cold-start the Hospital Bed Occupancy Platform from zero and reach a fully
> seeded, demo-ready state with all 4 wards populated and forecasts generated.

---

## 1. Prerequisites

| Requirement | Detail |
|---|---|
| Python | `>=3.11` (`pyproject.toml:5`) |
| Package manager | `uv` (lockfile: `uv.lock`) |
| Node.js | 18+ (`frontend/package.json` Next 18) |
| Docker | Compose v2 — 6 backend containers |
| Ports free | `5432`, `9200`, `7233`, `8080`, `8000`, `3000` |

### Environment variables (`.env` at project root)

| Variable | Required | Default | Source |
|---|---|---|---|
| `GOOGLE_API_KEY` | Recommended | `""` | Google AI Studio |
| `OPENAI_API_KEY` | Optional | `""` | Baseten / OpenAI |
| `USE_LLM` | Optional | `true` | Enable LLM synthesis |

Compose reads `ELASTICSEARCH_URL=http://elasticsearch:9200` internally
(`docker-compose.yml:105,132`). Local scripts fall back to `http://localhost:9200`
(`app/data/elasticsearch_client.py:15`).

> **Security note:** Rotate `OPENAI_API_KEY` before sharing the repo publicly.

---

## 2. Infrastructure (ordered start)

**Order matters.** Temporal depends on postgres + ES healthy checks
(`docker-compose.yml:71-75`). Gateway depends on Temporal (`:118`).

```bash
# Step 1 — Data stores (wait for healthy)
docker compose up -d postgresql elasticsearch
docker compose ps   # both should show (healthy) within ~30s

# Step 2 — Temporal server + UI
docker compose up -d temporal temporal-ui
# Verify: open http://localhost:8080 — Temporal UI loads

# Step 3 — Backend services (build first run takes ~3min, torch CPU cached)
docker compose up -d --build fastapi-gateway temporal-worker
# Verify: docker compose logs temporal-worker | tail
#   → "Listening for tasks..." + "[✓] Created schedule …" ×4

# Step 4 — Quick health check
curl http://localhost:8000/api/health
# → {"status":"healthy","service":"Hospital AI Agent Gateway","connected_clients":0}
curl http://localhost:9200/_cluster/health | grep status
# → "status" : "yellow" (single-node) or "green"
```

### Local dev alternative (no Docker for gateway/worker)

```bash
temporal server start-dev &                              # terminal 1
uv run python -m app.temporal.worker &                   # terminal 2
uv run uvicorn app.api.gateway:app --reload --port 8000  # terminal 3
cd frontend && npm run dev                               # terminal 4 → :3000
```

---

## 3. Generate initial mock data (all 4 wards)

`app/data/wards.py:46-112` defines 4 wards, each with 10 beds:

| Ward | Type | Occupancy band | ER pressure | Transfer-in | Transfer-out |
|---|---|---|---|---|---|
| `ICU-EAST` | ICU | 62-92% | 1.6x | 20% | 55% |
| `GENERAL-MALE` | MED_SURG | 58-90% | 1.1x | 40% | — |
| `GENERAL-FEMALE` | MED_SURG | 55-88% | 1.0x | 30% | — |
| `STEP-DOWN` | STEP_DOWN | 50-85% | 0.5x | 70% | — |

```bash
# Generate 30 days × 4 wards = 2,880 hourly records
uv run python scripts/generate_mock_data.py
# Output: data/hospital_30day_mock_data.json (~3.5MB)
# Console shows per-ward stats (occupancy range, mean, bed count)

# Single-ward variant (regime experiments only, writes to a different file):
uv run python scripts/generate_mock_data_10_beds.py --scenario balanced --days 30
# → data/hospital_30day_mock_data_10_beds.json (NOT auto-ingested)
```

---

## 4. Ingest into Elasticsearch

The `regenerate_mock_regime` handler (`app/api/gateway.py:414-509`) is the
cleanest path — it purges old docs, bulk-ingests new ones, and optionally
triggers forecast backfill in one call.

```bash
# Global ingest via API (recommended — handles purge + bulk + forecast trigger)
curl -s -X POST http://localhost:8000/api/mock/regenerate \
  -H 'Content-Type: application/json' \
  -d '{"scenario":"balanced","days":30,"trigger_forecast":true}' | jq

# Or use the CLI ingest script (no purge, no forecast trigger):
uv run python scripts/ingest_to_elasticsearch.py
```

**Document ID convention** (`scripts/ingest_to_elasticsearch.py:25-72`):
```
{hospital_id}_{census.unit_id}_{timestamp}
```

**Known limitation:** The regime switch now purges and regenerates
all 4 wards (`gateway.py` `regenerate_mock_regime`). Data is generated per-ward
with ward-specific bed counts and occupancy bands.

---

## 5. Forecast backfill (cold start)

On first boot the `hospital-forecast-timeline` index is empty — charts show
"No 24H forecast available". The scheduled workflows auto-trigger daily at
9 AM, but to populate immediately:

```bash
# Backfill 24H forecasts for all 4 wards via Temporal
python -c "
import asyncio
from temporalio.client import Client
from app.temporal.scheduled_workflow import DailyForecastWorkflow

async def main():
    client = await Client.connect('localhost:7233', namespace='default')
    for ward in ['ICU-EAST', 'GENERAL-MALE', 'GENERAL-FEMALE', 'STEP-DOWN']:
        handle = await client.start_workflow(
            DailyForecastWorkflow.run,
            args=['HOSPITAL-MAIN-01', ward],
            id=f'backfill-daily-{ward}',
            task_queue='hospital-ai-queue',
        )
        result = await handle.result()
        print(f'{ward}: {result}')

asyncio.run(main())
"
# First run downloads TimesFM model (~200MB) into shared hf-cache volume.
# Subsequent runs take ~15-30s per ward.
```

### Registered Temporal schedules (auto-created by worker)

| Schedule ID | Cron | Horizon | Ward |
|---|---|---|---|
| `daily-forecast-9am` | `0 9 * * *` | 24H (24 hourly points) | ICU-EAST |
| `weekly-forecast-mon-8am` | `0 8 * * 1` | 7D (7 daily points) | ICU-EAST |
| `monthly-forecast-1st-8am` | `0 8 1 * *` | 6M (6 monthly points) | ICU-EAST |
| `nightly-accuracy-2350` | `50 23 * * *` | Accuracy scoring | ICU-EAST |

> Schedules default to `ICU-EAST`. Other wards require manual backfill or
> per-ward schedule creation.

---

## 6. Elasticsearch index reference

| Index | Key fields | Doc ID pattern | Created by |
|---|---|---|---|
| `hospital-snapshots` | `hospital_id`, `census.unit_id/unit_type`, `er_arrivals`, `historical_occupancy_48h` | `{h}_{u}_{ts}` | Ingest script / `regenerate_mock_regime` |
| `hospital-features` | `past_target`, `past_covariates`, `future_covariates` | `FEAT_{date}_{h}_{u}` | Forecast activity |
| `hospital-forecast-timeline` | `unit_id`, `horizon_type`, `time_step_index`, `predicted_occupancy`, `has_anomaly` | `{date}_{h}_{u}_{horizon}_STEP{idx}` | Forecast activity |
| `hospital-forecast-accuracy` | `day`, `horizon_type`, `mae`, `bias` | `ACC_{day}_{horizon}_{h}_{u}` | Accuracy activity |
| `hospital-patient-flow` | `unit_id`, `predicted_admissions`, `er_direct`, `elective`, `icu_transfers`, `predicted_discharges`, `baseline_admissions_90d` | `FLOW_{date}_{h}_{u}` | `flow_service.py` |

---

## 7. Verification checklist

Run these after seeding data to confirm everything is ready:

```bash
# 1. All 4 ward indices exist in hospital-snapshots
curl -s 'http://localhost:9200/hospital-snapshots/_search' \
  -H 'Content-Type: application/json' \
  -d '{"size":0,"aggs":{"wards":{"terms":{"field":"census.unit_id","size":10}}}}' | jq .aggregations.wards.buckets

# 2. Ward summaries (live census from ES)
curl -s http://localhost:8000/api/forecast/wards | jq '.wards[] | "\(.unit_id): \(.live.occupied_beds)/\(.total_beds)"'

# 3. Patient flow (per ward)
curl -s 'http://localhost:8000/api/forecast/patient-flow?unit_id=ICU-EAST&days=2' | jq .next_24h
curl -s 'http://localhost:8000/api/forecast/patient-flow?unit_id=STEP-DOWN&days=2' | jq .next_24h

# 4. Multi-horizon forecast (per ward)
curl -s 'http://localhost:8000/api/forecast/multi-horizon?horizon_type=24H&unit_id=ICU-EAST' | jq .total_points
curl -s 'http://localhost:8000/api/forecast/multi-horizon?horizon_type=7D&unit_id=GENERAL-MALE' | jq .total_points

# 5. Accuracy grade
curl -s 'http://localhost:8000/api/forecast/accuracy?horizon_type=24H&unit_id=ICU-EAST&days=7' | jq .aggregate.grade

# 6. Temporal UI — http://localhost:8080
#    → hospital-ai-queue should show 2 pollers
#    → Schedules tab: 4 active schedules

# 7. Frontend — http://localhost:3000
#    → Ward tabs: ICU / Male / Female / Step-Down
#    → StatusPanel shows live census for selected ward
#    → "Forecast Timeline" button opens chart with data
```

---

## 8. Constraints & known limitations

- **3D is ICU-EAST only (Option A):** `app/api/gateway.py:97`
  `sim_engine = HospitalSimulationEngine(total_beds=10)` is a single
  shared singleton. The 3D floor renders 10 ICU beds for the selected ward.
  Non-ICU wards (`GENERAL-MALE`, `GENERAL-FEMALE`, `STEP-DOWN`) show
  forecast charts, patient-flow cards, and StatusPanel badges only — no 3D.

- **Regime switch is global:** `gateway.py` `regenerate_mock_regime` now
  purges and regenerates all 4 wards in one shot, using ward-specific bed
  counts and occupancy profiles from `app/data/wards.py`.

- **Schedules target all 4 wards:** `worker.py` `SCHEDULE_DEFINITIONS`
  expands to 16 schedules (4 wards × 4 cadence types) using
  `WARD_IDS = ["ICU-EAST", "GENERAL-MALE", "GENERAL-FEMALE", "STEP-DOWN"]`.
  Each schedule launches its workflow with `(hospital_id, unit_id)` args.

- **`generate_mock_data_10_beds.py` is single-ward:** It writes to a separate
  file (`*_10_beds.json`) that `ingest_to_elasticsearch.py:204` does not
  ingest by default. Use `generate_mock_data.py` for the standard workflow.

- **Elasticsearch 7.17.20 pinned:** The backend client (`pyproject.toml`)
  uses `elasticsearch==7.17.*` to match the Docker image. All index
  mappings use ES 7 `body=` parameters. Do NOT upgrade the client to 8.x.

- **ES URL split:** Compose sets `ELASTICSEARCH_URL=http://elasticsearch:9200`
  for backend services; local scripts use `http://localhost:9200`. Both resolve
  to the same cluster when running in Docker.

---

## 9. Full reset

```bash
# Nuclear option: wipe everything and start fresh
docker compose down -v                    # stop containers + delete volumes
docker compose up -d postgresql elasticsearch
docker compose up -d temporal temporal-ui
docker compose up -d --build fastapi-gateway temporal-worker
uv run python scripts/generate_mock_data.py
curl -X POST http://localhost:8000/api/mock/regenerate \
  -H 'Content-Type: application/json' \
  -d '{"scenario":"balanced","days":30,"trigger_forecast":true}'
# Then backfill all 4 wards (see §5)
```
