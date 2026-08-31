# End-to-End User Journey

> Step-by-step walkthrough of every user interaction in the Hospital AI Agent
> Twin dashboard. Each row maps a UI action to its backend trigger and expected
> visual result.

---

## Architecture at a glance

```
Browser (:3000)                FastAPI Gateway (:8000)         Temporal (:7233)
     │                               │                               │
     │── WS /ws/simulation ──────────│                               │
     │── GET /api/forecast/* ────────│── ES queries (:9200) ─────────│
     │── POST /api/trigger-capacity ─│── start_workflow ─────────────│
     │── POST /api/mock/regenerate ──│── ES bulk + forecast trigger ─│
     │── WS SUBMIT_HUMAN_APPROVAL ──│── handle.signal() ────────────│
```

---

## Journey summary table

| # | User step | UI trigger | API / WS | Backend | UI effect |
|---|---|---|---|---|---|
| 1 | **Load page** | `GET /` | `WS /ws/simulation` (`page.tsx:296`) + `GET /patient-flow?unit_id=ICU-EAST` (`page.tsx:170`) | `gateway.py:611` `manager.connect`; `timeline_router.py:322` | `isConnected` dot, `StatusPanel` 5/10 + next24h, static floor |
| 2 | **Select ward** | Tabs `ICU/Male/Female/Step-Down` → `setSelectedWard` (`page.tsx:591-619`) | `GET /patient-flow?unit_id={ward}&days=7` + chart re-fetches with `unitId` | `get_patient_flow_forecast:322`; chart `ForecastTimelineChart:252,313` (now ward-aware) | `StatusPanel` badge + objective label update |
| 3 | **View forecasts** | `Forecast Timeline` btn (`:632`) → mount `ForecastTimelineChart` | `GET /multi-horizon?horizon_type=24H&unit_id={ward}` (`Chart:262`), `GET /history-dates`, `GET /accuracy`, `GET /patient-flow` | `timeline_router.py:29,141,249` | Chart lines/bands, MAE badge, 7-day flow bars |
| 4 | **Scrub step** | Slider/dot/←→ (`:980,475,422`) → `scrubTo` → `onAnimateStep(datum,horizon)` | `WS PLAY_FORECAST_STEP {horizon_type, speed}` (`page.tsx:443`) | `gateway.py:162` fetches `FORECAST_INDEX` for `{horizon, unit}` → `SIM_PLAYBACK_STARTED/STATUS/COMPLETE` + `SIMULATION_3D_EVENT`s | Floor animates queue→bed, beds dirty→clean, gauge/ticker, pulsing dot |
| 5 | **Play timeline** | `Play` (`:802`) → `onPlayTimeline` | `WS PLAY_FORECAST_TIMELINE {horizon_type, speed}` (`:450`) | `gateway.py:531` `simulate_forecast_playback` per batch | Progress bar 0-100% + per-step `SIM_PLAYBACK_STATUS` |
| 6 | **Stop** | `Stop` (`:795`) → `onStopPlayback` | `WS STOP_PLAYBACK` (`:456`) | `gateway.py:650-661` `task.cancel()` | `SIM_PLAYBACK_COMPLETE{aborted:true}` → idle |
| 7 | **Capacity check** | `Run Capacity Check` (`:622`) → `triggerCapacityCheck` | `POST /trigger-capacity-check {unit_id:selectedWard}` (`:541`) | `gateway.py:675` `start_workflow(HospitalCapacityWorkflow.run)` → `WORKFLOW_STARTED` → `_monitor_and_stream` poll `workflow.status` → `REQUIRE_HUMAN_APPROVAL` | Button busy; after result `AGENT_RESULT_READY` + floor `SIMULATION_3D_EVENT`s; if `HUMAN_APPROVAL` → `ApprovalModal` |
| 8 | **Approve / Reject** | `ApprovalModal` Authorize/Reject (`page.tsx:534`) | `WS SUBMIT_HUMAN_APPROVAL {workflow_id, approved}` | `gateway.py:618-640` `handle.signal(approve_recommendation)` | `APPROVAL_ACKNOWLEDGED` → modal dismiss; `AGENT_RESULT_READY` + `RECOMMENDATION_EXECUTED` → `StaffAlertOverlay` (3D) |
| 9 | **See results** | Automatic | — | `_monitor_and_stream:746-819` `handle.result()` → `AGENT_RESULT_READY`, `RECOMMENDATION_EXECUTED` (only if `EXECUTED`), then `generate_simulation_stream` | `lastEvent` + results drawer (currently minimal — gap) |
| 10 | **ER Fast-track (manual queue)** | `ER Fast-Track` → form add (ESI/NEWS2) → `Run Fast-Track` (`:459`) | `WS RUN_FAST_TRACK_ADMISSION {boarders:[...]}` | `gateway.py:233-401` `fast_track_agent.astream` → `AGENT_STAGE`/`AGENT_DISPATCH` → `FAST_TRACK_RESULT` → animation | Stepper + terminal log + `BedAssignmentsPanel` RESERVED badges + ghost beams |
| 11 | **Simulate surge** | `Simulate Surge ×4` | `WS SIMULATE_ER_SURGE {count:4}` (`page.tsx:??`) | `gateway.py:666` `generate_surge_boarders(count)` → same as above | Same as #10 |
| 12 | **Focus bed** | Click `BedAssignmentsPanel` row → `onFocusBed` (`:163`) | local | `HospitalFloor CameraRig` | Camera lerp + 🎯 FOCUSED HUD, ESC resets |
| 13 | **Regime switch (admin)** | `Data Regime` select (`:491`) | `POST /mock/regenerate {scenario, days:30, trigger_forecast:true}` | `gateway.py:414` delete `ICU-EAST` → `async_bulk` → `DailyForecastWorkflow` → `MOCK_REGIME_CHANGED` | Toast + "open Forecast Timeline and hit Refresh" |
| 14 | **What-if / Backtest** | Chart sliders → `Run` / `Backtest` | `POST /scenario {bed_delta, deferral, surge}` + `POST /backtest {days:14}` | `timeline_router.py:194,222` | Scenario orange line + summary chips; model comparison table |
| 15 | **Reset** | `↺ Reset Floor` (`:527`) | local `setBeds(generateInitialBeds)` | — | Beds 5 occ / 3 queued (but server `sim_engine` not reset) |

---

## Detailed journey steps

### 1. Load page

| What happens | Where |
|---|---|
| `Dashboard` mounts, `generateInitialBeds()` creates 10 beds (5 occupied, 3 queued) | `page.tsx:65-72,74-91` |
| WebSocket connects to `ws://localhost:8000/ws/simulation` | `page.tsx:296` |
| Auto-plays 24H timeline on connect (800ms delay) | `page.tsx:300-304` |
| `GET /api/forecast/patient-flow?unit_id=ICU-EAST&days=7` fetches patient flow | `page.tsx:170` |
| `StatusPanel` shows occupancy bar, ward badge, Next-24H card | `StatusPanel.tsx:26-195` |
| Green dot "Connected" in status bar | `page.tsx:572-574` |

**Expected:** 3D floor visible, 5/10 beds occupied (red), 3 patients queued at admission gate, "Connected" badge.

---

### 2. Select ward

| What happens | Where |
|---|---|
| Click ICU / Male / Female / Step-Down tab | `page.tsx:591-619` |
| `setSelectedWard(w.id)` updates state | `page.tsx:603` |
| `GET /api/forecast/patient-flow?unit_id={ward}&days=7` re-fetches | `page.tsx:170` |
| `StatusPanel` badge updates to selected ward label | `page.tsx:689` |
| `ForecastTimelineChart` (if open) re-fetches with `unitId` prop | `Chart:206,252,313` |

**Expected:** StatusPanel badge changes, Next-24H card shows ward-specific admissions/discharges with trend arrows.

---

### 3. View forecast chart

| What happens | Where |
|---|---|
| Click "Forecast Timeline" button | `page.tsx:631-633` |
| `ForecastTimelineChart` mounts, fetches: | `Chart:262-284` |
| — `GET /api/forecast/multi-horizon?horizon_type=24H` | |
| — `GET /api/forecast/history-dates?horizon_type=24H` | |
| — `GET /api/forecast/accuracy?horizon_type=24H&unit_id={ward}` | `Chart:314` |
| — `GET /api/forecast/patient-flow?days=7&unit_id={ward}` | `Chart:254` |
| Chart renders line + confidence band + anomaly dots | `Chart:880-976` |
| Patient-flow card shows 7-day admissions vs discharges bars | `Chart:1002-1067` |

**Expected:** 24H hourly forecast line, accuracy badge (green/amber/red), 7-day flow bars with ER/elective/transfer breakdown in tooltips.

---

### 4. Scrub to forecast step

| What happens | Where |
|---|---|
| Drag slider / click dot / press arrow keys | `Chart:980-987,475,422` |
| `scrubTo(step, animate)` → `onAnimateStep(datum, horizon)` | `Chart:441-449` |
| `handleStepSelect` sends `PLAY_FORECAST_STEP` WS message | `page.tsx:441-446` |
| Gateway `_run_playback_step` fetches forecast from ES | `gateway.py:162-230` |
| `sim_engine.sync_registry(prev_occ)` transitions beds | `gateway.py:206` |
| `SIMULATION_3D_EVENT` events stream to frontend | `gateway.py:154-159` |
| `SIM_PLAYBACK_COMPLETE` with final bed count | `gateway.py:222-228` |

**Note:** Currently `step_index` is not sent from the frontend (`page.tsx:443`) — the backend defaults to step 1. Known issue.

**Expected:** 3D floor animates queue→bed transitions, beds change state, bottom bar updates bed count.

---

### 5. Play full timeline

| What happens | Where |
|---|---|
| Click "Play" button in chart | `Chart:800-808` |
| `onPlayTimeline(horizon, speed)` sends `PLAY_FORECAST_TIMELINE` | `page.tsx:448-453` |
| Gateway `_run_playback_timeline` iterates all forecast points | `gateway.py:531-594` |
| `SIM_PLAYBACK_STATUS` events per step with `step_index` + `occupied_beds` | `gateway.py:574-579` |
| Progress bar updates in chart | `Chart:841-858` |
| `SIM_PLAYBACK_COMPLETE` on finish | `gateway.py:588-594` |

**Expected:** Animated time-lapse across all 24 steps, progress bar 0→100%, pulsing dot on active step.

---

### 6. Stop playback

| What happens | Where |
|---|---|
| Click "Stop" button | `Chart:795-798` |
| `onStopPlayback()` sends `STOP_PLAYBACK` | `page.tsx:455-457` |
| Gateway cancels `_active_playback_task` | `gateway.py:650-655` |

**Expected:** Animation halts, `SIM_PLAYBACK_COMPLETE{aborted:true}` received, floor returns to idle.

---

### 7. Run Capacity Check

| What happens | Where |
|---|---|
| Click "Run Capacity Check" | `page.tsx:621-624` |
| `POST /api/trigger-capacity-check` with `{hospital_id, unit_id: selectedWard}` | `page.tsx:541-549` |
| Gateway starts `HospitalCapacityWorkflow` in Temporal | `gateway.py:689-694` |
| `WORKFLOW_STARTED` broadcast | `gateway.py:697-701` |
| `_monitor_and_stream` polls `workflow.status` query | `gateway.py:746-770` |
| LangGraph agent pipeline runs (wrangling→monitoring→forecast→anomaly→recommendation) | `activities.py` → `hospital_graph.py` |
| Policy engine decides: ALLOW or HUMAN_APPROVAL | `policy/engine.py` |
| If HUMAN_APPROVAL: `REQUIRE_HUMAN_APPROVAL` broadcast | `gateway.py:761-766` |
| If ALLOW: `AGENT_RESULT_READY` + simulation events | `gateway.py:774-813` |

**Expected:** "Workflow started" toast, then either ApprovalModal appears or results stream directly.

---

### 8. Approve / Reject recommendation

| What happens | Where |
|---|---|
| `ApprovalModal` shows recommendations with Authorize/Reject buttons | `page.tsx:764-766` |
| Click "Authorize Action" | `page.tsx:534-539` |
| WS `SUBMIT_HUMAN_APPROVAL {workflow_id, approved: true}` | `page.tsx:536` |
| Gateway signals `HospitalCapacityWorkflow.approve_recommendation` | `gateway.py:624-630` |
| `APPROVAL_ACKNOWLEDGED` sent back | `gateway.py:631-635` |
| Workflow resumes: `execute_approved_recommendation` activity runs | `workflows.py:83-87` |
| `RECOMMENDATION_EXECUTED` with staff alert notifications | `gateway.py:788-801` |
| `StaffAlertOverlay` renders in 3D scene | `HospitalFloor.tsx` |

**Expected:** Modal dismisses, staff alert overlay appears in 3D scene with role/channel/priority badges.

---

### 9. See results

| What happens | Where |
|---|---|
| `AGENT_RESULT_READY` updates `lastEvent` | `page.tsx:414` |
| `RECOMMENDATION_EXECUTED` populates `staffAlerts` | `page.tsx:415-421` |
| 3D simulation events stream (bed transitions) | `gateway.py:803-813` |

**Expected:** Status bar shows "Result: {workflow_id}", staff alerts visible, floor animates to final state.

---

### 10. ER Fast-Track Admissions

| What happens | Where |
|---|---|
| Click "ER Fast-Track Admissions" | `page.tsx:628-630` |
| `ERAdmissionsPanel` opens with form to add boarders | `page.tsx:736-747` |
| Add patients (ESI level, NEWS2 score) → click "Run Fast-Track" | `ERAdmissionsPanel` |
| WS `RUN_FAST_TRACK_ADMISSION {boarders:[...]}` | `page.tsx:459-469` |
| Gateway runs `fast_track_agent.astream` (LangGraph) | `gateway.py:294-319` |
| `AGENT_STAGE` events: TRIAGE_MATCHING → ROLE_NOTIFICATIONS → CHANNEL_DISPATCH | `gateway.py:286-342` |
| `FAST_TRACK_RESULT` with matches + notifications | `gateway.py:362-370` |
| Floor animates: discharge dirty beds → EVS clean → admit patients | `gateway.py:372-401` |
| `FAST_TRACK_ANIMATION_COMPLETE` | `gateway.py:395-401` |

**Expected:** Stepper shows pipeline progress, terminal log streams, bed assignments panel shows RESERVED badges, ghost beams animate to beds.

---

### 11. Simulate ER Surge

| What happens | Where |
|---|---|
| Click "Simulate Surge x4" | `page.tsx:471-480` |
| WS `SIMULATE_ER_SURGE {count:4}` | `page.tsx:477` |
| Gateway generates synthetic boarders via `generate_surge_boarders(4)` | `gateway.py:666-669` |
| Same flow as ER Fast-Track (#10) | `gateway.py:663-669` |

**Expected:** 4 synthetic patients appear at admission gate, fast-track pipeline runs.

---

### 12. Focus a bed

| What happens | Where |
|---|---|
| Click a row in `BedAssignmentsPanel` | `page.tsx:163-165` |
| `setFocusBedRequest({bedId, token})` | `page.tsx:164` |
| `HospitalFloor` CameraRig receives `focusBedId` | `HospitalFloor.tsx` |
| Camera lerps to focused bed, "FOCUSED" HUD appears | `HospitalFloor.tsx` |
| ESC key resets camera | `HospitalFloor.tsx` |

**Expected:** Smooth camera zoom to selected bed, target indicator, ESC returns to default view.

---

### 13. Switch data regime (global)

| What happens | Where |
|---|---|
| Select from "Data Regime" dropdown | `page.tsx:642-671` |
| `handleRegimeChange(scenario)` | `page.tsx:491-520` |
| `POST /api/mock/regenerate {scenario, days:30, trigger_forecast:true}` | `page.tsx:498-503` |
| Gateway `regenerate_mock_regime`: | `gateway.py:414-509` |
| — Generates new 30-day data for all 4 wards | `gateway.py:425-427` |
| — Purges old snapshots, bulk-ingests new | `gateway.py:449-478` |
| — Triggers `DailyForecastWorkflow` | `gateway.py:481-495` |
| `MOCK_REGIME_CHANGED` broadcast | `page.tsx:406-408` |

**Available regimes:** `balanced` (50-70%), `high_capacity` (85-100%), `volatile` (mini-waves), `recovery` (ramp-down), `outbreak_surge` (legacy).

**Expected:** Toast "Regime → {scenario} · N snapshots · forecast refreshing". After ~6s: "open Forecast Timeline and hit Refresh".

---

### 14. What-if scenario

| What happens | Where |
|---|---|
| Open Forecast Timeline → "What-If Scenario" | `Chart:660-672` |
| Adjust sliders: BEDS Δ, DEFER ELECTIVES, ER SURGE | `Chart:707-722` |
| Click "Run" | `Chart:720-722` |
| `POST /api/forecast/scenario` with slider values | `Chart:327-343` |
| `run_what_if_scenario` runs TimesFM on modified features | `strategy_service.py` |
| Scenario overlay line (orange dashed) appears on chart | `Chart:951-962` |
| Summary chips: Peak Δ, Avg Δ, OR window Δ, Beds freed | `Chart:726-748` |

**Expected:** Orange dashed scenario line vs blue baseline, delta badges.

---

### 14b. Backtest

| What happens | Where |
|---|---|
| Click "Backtest" button | `Chart:673-681` |
| `POST /api/forecast/backtest {days:14}` | `Chart:345-362` |
| `run_backtest` walks history, scores predictions vs actuals | `strategy_service.py` |
| Accuracy records persisted to `hospital-forecast-accuracy` | `timeline_router.py:234-241` |
| Model comparison table renders | `Chart:1069-1100` |
| Accuracy badge refreshes | `Chart:314-318` |

**Expected:** Table showing TimesFM vs baseline models (MAE, RMSE, Bias), updated trust badge.

---

### 15. Reset floor

| What happens | Where |
|---|---|
| Click "Reset Floor" | `page.tsx:635-637` |
| `generateInitialBeds()` resets to 5 occupied / 3 queued | `page.tsx:527-532` |
| Patients + staff reset to initial positions | `page.tsx:529-531` |

**Note:** Client-side only — server `sim_engine` is not reset. Replaying a forecast will re-sync.

**Expected:** Floor returns to initial state (5/10 beds, 3 queued).

---

## Quick reference: UI controls

| Control | Location | Action |
|---|---|---|
| Ward tabs (ICU/Male/Female/Step-Down) | Left panel, top | Switches active ward for all dashboards |
| "Run Capacity Check" | Left panel | Triggers Temporal workflow → HITL |
| "Replay 24H Timeline" | Left panel | Replays full 24-step animation |
| "ER Fast-Track Admissions" | Left panel | Opens ER boarder panel |
| "Forecast Timeline" | Left panel | Opens forecast chart + flow card |
| "Reset Floor" | Left panel | Returns floor to initial state |
| Data Regime dropdown | Left panel | Switches mock data scenario |
| Forecast chart scrubber | Chart bottom | Drag to explore forecast steps |
| ← → arrows / keyboard | Chart / global | Navigate forecast steps |
| Play / Stop | Chart transport | Timeline playback control |
| Speed (0.5x / 1x / 2x) | Chart transport | Playback speed |
| Backtest | Chart strategy bar | Score historical accuracy |
| What-If Scenario | Chart strategy bar | Run scenario overlay |
| Bed row click | BedAssignmentsPanel | Focus camera on bed |
| ESC | Global | Exit focused bed / expanded chart |
| Authorize / Reject | ApprovalModal | Human-in-the-loop decision |
