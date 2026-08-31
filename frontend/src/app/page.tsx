"use client";

import React, { useEffect, useState, useCallback, useRef, useMemo } from "react";
import dynamic from "next/dynamic";
import { Film, RotateCcw, Zap, Shield, TrendingUp } from "lucide-react";
import ThemeToggle, { type Theme } from "@/components/ui/ThemeToggle";
import ApprovalModal from "@/components/ui/ApprovalModal";
import StatusPanel from "@/components/ui/StatusPanel";
import ERAdmissionsPanel from "@/components/ERAdmissionsPanel";
import BedAssignmentsPanel from "@/components/BedAssignmentsPanel";
import {
  BedState,
  Patient3D,
  Staff3D,
  Simulation3DEvent,
  WSMessage,
  WaitingPatientDTO,
  FastTrackMatch,
  StaffNotification,
  AgentStage,
  AgentDispatch,
} from "@/types/hospital";
import type { HorizonType } from "@/types/forecast";
import type { ChartDatum } from "@/components/ForecastTimelineChart";
import { PatientFlowResponse } from "@/types/forecast";

const HospitalFloor = dynamic(() => import("@/components/3d/HospitalFloor"), {
  ssr: false,
  loading: () => (
    <div style={{ width: "100%", height: "100%", display: "flex", alignItems: "center", justifyContent: "center", background: "var(--3d-bg, #0b1118)", color: "var(--muted-foreground, #64748b)", fontSize: "14px", fontFamily: "var(--font-sans), sans-serif" }}>
      Loading 3D Hospital Floor...
    </div>
  ),
});

const ForecastTimelineChart = dynamic(
  () => import("@/components/ForecastTimelineChart"),
  { ssr: false }
);

const WS_URL = process.env.NEXT_PUBLIC_WS_URL || "ws://localhost:8000/ws/simulation";
const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

/* ═══════════════════════════════════════════════════════
   Layout: 10 beds in 2 rows of 5
   ═══════════════════════════════════════════════════════ */
const BED_POSITIONS = [
  // Row A (top): z = -5
  { id: "BED-1", x: -10, z: -5 },
  { id: "BED-2", x: -5, z: -5 },
  { id: "BED-3", x: 0, z: -5 },
  { id: "BED-4", x: 5, z: -5 },
  { id: "BED-5", x: 10, z: -5 },
  // Row B (bottom): z = 0
  { id: "BED-6", x: -10, z: 0 },
  { id: "BED-7", x: -5, z: 0 },
  { id: "BED-8", x: 0, z: 0 },
  { id: "BED-9", x: 5, z: 0 },
  { id: "BED-10", x: 10, z: 0 },
];

/* ═══════════════════════════════════════════════════════
   Initial state
   ═══════════════════════════════════════════════════════ */
function generateInitialBeds(): BedState[] {
  return BED_POSITIONS.map((b, i) => ({
    id: b.id,
    position: { x: b.x, y: 0, z: b.z },
    isOccupied: i < 5, // 5 beds occupied initially
    patientId: i < 5 ? `PAT-${String(i + 1).padStart(3, "0")}` : undefined,
  }));
}

const INITIAL_PATIENTS: Patient3D[] = [
  // 5 patients in beds
  { id: "PAT-001", position: { x: -10, y: 0, z: -5 }, targetPosition: { x: -10, y: 0, z: -5 }, status: "BED_ASSIGNED", bedId: "BED-1" },
  { id: "PAT-002", position: { x: -5, y: 0, z: -5 }, targetPosition: { x: -5, y: 0, z: -5 }, status: "BED_ASSIGNED", bedId: "BED-2" },
  { id: "PAT-003", position: { x: 0, y: 0, z: -5 }, targetPosition: { x: 0, y: 0, z: -5 }, status: "BED_ASSIGNED", bedId: "BED-3" },
  { id: "PAT-004", position: { x: 5, y: 0, z: -5 }, targetPosition: { x: 5, y: 0, z: -5 }, status: "BED_ASSIGNED", bedId: "BED-4" },
  { id: "PAT-005", position: { x: -10, y: 0, z: 0 }, targetPosition: { x: -10, y: 0, z: 0 }, status: "BED_ASSIGNED", bedId: "BED-6" },
  // 3 patients queuing at admission gate
  { id: "PAT-006", position: { x: -12, y: 0, z: 8 }, targetPosition: { x: -12, y: 0, z: 8 }, status: "ARRIVED" },
  { id: "PAT-007", position: { x: -12, y: 0, z: 6.5 }, targetPosition: { x: -12, y: 0, z: 6.5 }, status: "ARRIVED" },
  { id: "PAT-008", position: { x: -12, y: 0, z: 5 }, targetPosition: { x: -12, y: 0, z: 5 }, status: "ARRIVED" },
];

const INITIAL_STAFF: Staff3D[] = [
  { id: "NURSE-01", position: { x: -4, y: 0, z: -2.5 }, targetPosition: { x: -4, y: 0, z: -2.5 }, status: "WALKING", role: "nurse" },
  { id: "NURSE-02", position: { x: 4, y: 0, z: -2.5 }, targetPosition: { x: 4, y: 0, z: -2.5 }, status: "WALKING", role: "nurse" },
  { id: "DR-01", position: { x: 0, y: 0, z: 3.5 }, targetPosition: { x: 0, y: 0, z: 3.5 }, status: "IDLE", role: "doctor" },
];

/* ═══════════════════════════════════════════════════════
   Error boundary
   ═══════════════════════════════════════════════════════ */
class Scene3DErrorBoundary extends React.Component<
  { children: React.ReactNode; fallback: React.ReactNode },
  { hasError: boolean; error: string }
> {
  constructor(props: { children: React.ReactNode; fallback: React.ReactNode }) {
    super(props);
    this.state = { hasError: false, error: "" };
  }
  static getDerivedStateFromError(error: Error) { return { hasError: true, error: error.message }; }
  componentDidCatch(error: Error) { console.error("[3D Scene Error]", error); }
  render() { return this.state.hasError ? this.props.fallback : this.props.children; }
}

function Scene3DFallback({ error }: { error?: string }) {
  return (
    <div style={{ width: "100%", height: "100%", display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", background: "var(--3d-bg, #0b1118)", color: "var(--muted-foreground, #94a3b8)", fontFamily: "var(--font-sans), sans-serif", fontSize: "14px", padding: 20, textAlign: "center" }}>
      <div style={{ fontSize: 48, marginBottom: 16 }}>🏥</div>
      <p style={{ marginBottom: 8, color: "#e2e8f0" }}>3D Hospital Floor</p>
      <p style={{ fontSize: 12, color: "var(--muted-foreground)", maxWidth: 300 }}>{error || "WebGL not available."}</p>
    </div>
  );
}

/* ═══════════════════════════════════════════════════════
   Dashboard
   ═══════════════════════════════════════════════════════ */
export default function Dashboard() {
  const [beds, setBeds] = useState<BedState[]>(generateInitialBeds);
  const [patients, setPatients] = useState<Patient3D[]>(INITIAL_PATIENTS);
  const [staff, setStaff] = useState<Staff3D[]>(INITIAL_STAFF);
  const [isConnected, setIsConnected] = useState(false);
  const [lastEvent, setLastEvent] = useState<string | null>(null);
  const [theme, setTheme] = useState<Theme>("dark");
  const [sceneError, setSceneError] = useState<string | null>(null);
  const [approvalRequest, setApprovalRequest] = useState<{
    workflow_id: string;
    recommendations: Array<{ title: string; description: string; priority: string; rationale: string }>;
  } | null>(null);
  const [showForecast, setShowForecast] = useState(false);
  const [playbackStatus, setPlaybackStatus] = useState<{
    active: boolean;
    mode: "STEP" | "TIMELINE" | null;
    horizonType: string | null;
    stepIndex: number | null;
    totalSteps: number | null;
    occupiedBeds: number | null;
  }>({ active: false, mode: null, horizonType: null, stepIndex: null, totalSteps: null, occupiedBeds: null });
  const [playbackSpeed, setPlaybackSpeed] = useState(1);
  const [showERAdmissions, setShowERAdmissions] = useState(false);
  const [fastTrackResult, setFastTrackResult] = useState<{
    matches: FastTrackMatch[];
    notifications: StaffNotification[];
    total_boarders: number;
    admitted?: number;
    run_id?: string;
  } | null>(null);
  const [fastTrackProcessing, setFastTrackProcessing] = useState(false);
  const [agentStages, setAgentStages] = useState<AgentStage[]>([]);
  const [agentDispatches, setAgentDispatches] = useState<AgentDispatch[]>([]);
  const [dataRegime, setDataRegime] = useState("outbreak_surge");
  const [regimeChanging, setRegimeChanging] = useState(false);
  const [focusBedRequest, setFocusBedRequest] = useState<{ bedId: string; token: number } | null>(null);
  const [staffAlerts, setStaffAlerts] = useState<StaffNotification[]>([]);
  const [staffAlertsToken, setStaffAlertsToken] = useState(0);
  const [selectedWard, setSelectedWard] = useState("ICU-EAST");
  const [patientFlow, setPatientFlow] = useState<PatientFlowResponse | null>(null);

  const handleFocusBed = useCallback((bedId: string) => {
    setFocusBedRequest({ bedId, token: Date.now() });
  }, []);

  /* ─── Ward-aware patient flow (24h anticipated admissions/discharges) ─── */
  useEffect(() => {
    let cancelled = false;
    fetch(`${API_URL}/api/forecast/patient-flow?unit_id=${selectedWard}&days=7`)
      .then((r) => r.json())
      .then((j: PatientFlowResponse) => { if (!cancelled) setPatientFlow(j); })
      .catch(() => {});
    return () => { cancelled = true; };
  }, [selectedWard]);

  const wsRef = useRef<WebSocket | null>(null);

  /* ─── 3D event handler (backend-driven simulation) ─── */
  const handle3DEvent = useCallback((evt: Simulation3DEvent) => {
    const target = evt.target_position
      ? { x: evt.target_position.x, y: evt.target_position.y, z: evt.target_position.z }
      : undefined;

    switch (evt.event_type) {
      case "PATIENT_ARRIVED":
        if (!evt.patient_id) break;
        setPatients((prev) => [
          ...prev.filter((p) => p.id !== evt.patient_id),
          {
            id: evt.patient_id!,
            position: { x: evt.position.x, y: evt.position.y, z: evt.position.z },
            targetPosition: target,
            status: "ARRIVED" as const,
          },
        ]);
        break;

      case "PATIENT_ESCORTED":
        if (!evt.patient_id) break;
        setPatients((prev) =>
          prev.map((p) =>
            p.id === evt.patient_id
              ? { ...p, targetPosition: target, status: "ESCORTED" as const, bedId: evt.bed_id }
              : p
          )
        );
        if (evt.staff_id) {
          setStaff((prev) =>
            prev.map((s) =>
              s.id === evt.staff_id
                ? { ...s, targetPosition: target ? { x: target.x, y: 0, z: target.z - 1.5 } : undefined, status: "DISPATCHED" as const }
                : s
            )
          );
        }
        break;

      case "BED_ASSIGNED":
        if (evt.bed_id && evt.patient_id) {
          setBeds((prev) =>
            prev.map((b) => (b.id === evt.bed_id ? { ...b, isOccupied: true, patientId: evt.patient_id } : b))
          );
          setPatients((prev) =>
            prev.map((p) => (p.id === evt.patient_id ? { ...p, status: "BED_ASSIGNED" as const, bedId: evt.bed_id } : p))
          );
          if (evt.staff_id) {
            setStaff((prev) =>
              prev.map((s) =>
                s.id === evt.staff_id
                  ? { ...s, targetPosition: { x: 0, y: 0, z: 3 }, status: "WALKING" as const }
                  : s
              )
            );
          }
        }
        break;

      case "STAFF_DISPATCHED":
        if (evt.staff_id) {
          setStaff((prev) =>
            prev.map((s) =>
              s.id === evt.staff_id
                ? { ...s, position: { x: evt.position.x, y: 0, z: evt.position.z }, targetPosition: target, status: "DISPATCHED" as const }
                : s
            )
          );
        }
        break;

      case "PATIENT_WALKING_OUT":
        // Free the bed immediately; patient walks to the discharge gate
        if (evt.bed_id) {
          setBeds((prev) => prev.map((b) => (b.id === evt.bed_id ? { ...b, isOccupied: false, patientId: undefined } : b)));
        }
        if (evt.patient_id) {
          setPatients((prev) =>
            prev.map((p) =>
              p.id === evt.patient_id
                ? { ...p, bedId: undefined, targetPosition: target ?? { x: 13, y: 0, z: 8 }, status: "DISCHARGED" as const }
                : p
            )
          );
        }
        break;

      case "EVS_CLEANING_STARTED":
        if (evt.bed_id) {
          setBeds((prev) => prev.map((b) => (b.id === evt.bed_id ? { ...b, isBeingCleaned: true } : b)));
        }
        break;

      case "EVS_CLEANING_COMPLETE":
        if (evt.bed_id) {
          setBeds((prev) => prev.map((b) => (b.id === evt.bed_id ? { ...b, isBeingCleaned: false } : b)));
        }
        break;

      case "PATIENT_DISCHARGED":
        if (evt.bed_id) {
          setBeds((prev) => prev.map((b) => (b.id === evt.bed_id ? { ...b, isOccupied: false, patientId: undefined } : b)));
        }
        setPatients((prev) => prev.filter((p) => p.id !== evt.patient_id));
        break;
    }
  }, []);

  /* ─── WebSocket ─── */
  useEffect(() => {
    try {
      const canvas = document.createElement("canvas");
      const gl = canvas.getContext("webgl2") || canvas.getContext("webgl");
      if (!gl) setSceneError("WebGL is not supported.");
    } catch { setSceneError("Failed to check WebGL."); }

    const ws = new WebSocket(WS_URL);
    ws.onopen = () => {
      setIsConnected(true);
      // Bring the floor to life automatically once connected
      setTimeout(() => {
        try {
          ws.send(JSON.stringify({ action: "PLAY_FORECAST_TIMELINE", horizon_type: "24H" }));
        } catch { /* ignore */ }
      }, 800);
    };
    ws.onmessage = (event) => {
      const data: WSMessage = JSON.parse(event.data);
      switch (data.type) {
        case "SIMULATION_3D_EVENT":
          if (data.event) {
            handle3DEvent(data.event);
            if (data.event.patient_id) setLastEvent(`${data.event.event_type}: ${data.event.patient_id}`);
          }
          if (data.playback_status) {
            setPlaybackStatus((prev) => ({
              ...prev,
              active: true,
              mode: "TIMELINE",
              stepIndex: data.playback_status!.step_index,
              occupiedBeds: data.playback_status!.occupied_beds,
            }));
          }
          break;
        case "SIM_PLAYBACK_STARTED":
          setPlaybackStatus({
            active: true,
            mode: data.mode ?? "STEP",
            horizonType: data.horizon_type ?? null,
            stepIndex: data.step_index ?? null,
            totalSteps: data.total_steps ?? null,
            occupiedBeds: null,
          });
          setLastEvent(`Playback started (${data.mode}: ${data.horizon_type})`);
          break;
        case "SIM_PLAYBACK_STATUS":
          setPlaybackStatus((prev) => ({
            ...prev,
            active: true,
            stepIndex: data.step_index ?? prev.stepIndex,
            occupiedBeds: data.occupied_beds ?? prev.occupiedBeds,
          }));
          break;
        case "SIM_PLAYBACK_COMPLETE":
          setPlaybackStatus({ active: false, mode: null, horizonType: null, stepIndex: null, totalSteps: null, occupiedBeds: data.occupied_beds ?? null });
          setLastEvent(`Playback complete — ${data.occupied_beds} beds occupied`);
          break;
        case "SIM_PLAYBACK_ERROR":
          setPlaybackStatus({ active: false, mode: null, horizonType: null, stepIndex: null, totalSteps: null, occupiedBeds: null });
          setLastEvent(`Playback error: ${data.message}`);
          break;
        case "AGENT_STAGE":
          setAgentStages((prev) => {
            const next = prev.filter((s) => s.stage !== data.stage);
            return [
              ...next,
              {
                run_id: data.run_id ?? "",
                stage: (data.stage ?? "TRIAGE_MATCHING") as AgentStage["stage"],
                status: data.status ?? "RUNNING",
                detail: data.detail ?? "",
              },
            ];
          });
          break;
        case "AGENT_DISPATCH":
          setAgentDispatches((prev) => [
            ...prev,
            {
              run_id: data.run_id ?? "",
              channel: data.channel ?? "",
              recipient_role: data.recipient_role ?? "",
              priority: data.priority ?? "",
              message_title: data.message_title,
              status: data.status ?? "UNKNOWN",
            },
          ]);
          setLastEvent(`Alert ${data.status}: ${data.channel} → ${data.recipient_role}`);
          break;
        case "FAST_TRACK_RESULT":
          setAgentStages((prev) => [
            ...prev.filter((s) => s.stage !== "CHANNEL_DISPATCH"),
            {
              run_id: data.run_id ?? "",
              stage: "CHANNEL_DISPATCH" as AgentStage["stage"],
              status: "DONE" as const,
              detail: `${(data.notifications ?? []).length} alerts dispatched`,
            },
          ]);
          setFastTrackResult({
            matches: data.matches ?? [],
            notifications: data.notifications ?? [],
            total_boarders: data.total_boarders ?? 0,
            run_id: data.run_id,
          });
          setLastEvent(`Fast-track triage complete: ${data.total_boarders} boarders`);
          break;
        case "FAST_TRACK_ANIMATION_COMPLETE":
          setFastTrackProcessing(false);
          setFastTrackResult((prev) => (prev ? { ...prev, admitted: data.admitted } : prev));
          setLastEvent(`Fast-track admissions done — ${data.occupied_beds} beds occupied`);
          break;
        case "FAST_TRACK_ERROR":
          setFastTrackProcessing(false);
          setLastEvent(`Fast-track error: ${data.message}`);
          break;
        case "MOCK_REGIME_CHANGED":
          setLastEvent(`Data regime → ${data.scenario} (${data.records_ingested} snapshots ingested)`);
          break;
        case "REQUIRE_HUMAN_APPROVAL":
          if (data.workflow_id && data.recommendations) setApprovalRequest({ workflow_id: data.workflow_id, recommendations: data.recommendations });
          break;
        case "APPROVAL_ACKNOWLEDGED": setApprovalRequest(null); setLastEvent(`Approved: ${data.workflow_id}`); break;
        case "WORKFLOW_STARTED": setLastEvent(`Workflow: ${data.workflow_id}`); break;
        case "AGENT_RESULT_READY": setLastEvent(`Result: ${data.workflow_id}`); break;
        case "RECOMMENDATION_EXECUTED":
          if (Array.isArray(data.notifications) && data.notifications.length) {
            setStaffAlerts(data.notifications);
            setStaffAlertsToken(Date.now());
            setLastEvent(`Executed: ${data.notifications.length} staff alert(s) dispatched`);
          }
          break;
      }
    };
    ws.onclose = () => setIsConnected(false);
    ws.onerror = () => setIsConnected(false);
    wsRef.current = ws;
    return () => ws.close();
  }, [handle3DEvent]);

  const sendWsAction = useCallback((payload: Record<string, unknown>) => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify(payload));
      return true;
    }
    setLastEvent("Error: not connected");
    return false;
  }, []);

  /* ─── Chart-driven simulation callbacks ─── */
  /* ─── Chart-driven simulation callbacks ─── */
  const handleStepSelect = useCallback(
    (_datum: ChartDatum, horizonType: HorizonType) => {
      sendWsAction({ action: "PLAY_FORECAST_STEP", horizon_type: horizonType, speed: playbackSpeed });
    },
    [sendWsAction, playbackSpeed]
  );

  const handlePlayTimeline = useCallback(
    (horizonType: HorizonType, speed: number) => {
      sendWsAction({ action: "PLAY_FORECAST_TIMELINE", horizon_type: horizonType, speed });
    },
    [sendWsAction]
  );

  const handleStopPlayback = useCallback(() => {
    sendWsAction({ action: "STOP_PLAYBACK" });
  }, [sendWsAction]);

  const handleSubmitBoarders = useCallback(
    (boarders: WaitingPatientDTO[]) => {
      if (!boarders.length) return;
      setFastTrackProcessing(true);
      setFastTrackResult(null);
      setAgentStages([]);
      setAgentDispatches([]);
      sendWsAction({ action: "RUN_FAST_TRACK_ADMISSION", boarders });
    },
    [sendWsAction]
  );

  const handleSimulateSurge = useCallback(
    (count: number) => {
      setFastTrackProcessing(true);
      setFastTrackResult(null);
      setAgentStages([]);
      setAgentDispatches([]);
      sendWsAction({ action: "SIMULATE_ER_SURGE", count });
    },
    [sendWsAction]
  );

  /* ─── Data regime switcher ─── */
  const DATA_REGIMES = [
    { key: "outbreak_surge", label: "Outbreak Surge (legacy)" },
    { key: "balanced", label: "Balanced (50-70%)" },
    { key: "high_capacity", label: "High Capacity Crisis (85-100%)" },
    { key: "volatile", label: "Volatile Mini-Waves" },
    { key: "recovery", label: "Recovery Ramp-Down" },
  ];

  const handleRegimeChange = useCallback(
    async (scenario: string) => {
      if (regimeChanging || playbackStatus.active) return;
      setDataRegime(scenario);
      setRegimeChanging(true);
      setLastEvent(`Switching data regime → ${scenario}…`);
      try {
        const res = await fetch(`${API_URL}/api/mock/regenerate`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ scenario, days: 30, trigger_forecast: true }),
        });
        const json = await res.json();
        if (json.status === "SUCCESS") {
          setLastEvent(`Regime → ${scenario} · ${json.records_ingested} snapshots · forecast refreshing`);
        } else {
          setLastEvent(`Regime change failed: ${json.message ?? "unknown error"}`);
        }
      } catch {
        setLastEvent("Error: regime switch failed to reach API");
      } finally {
        // Give the forecast workflow a moment, then notify
        setTimeout(() => {
          setRegimeChanging(false);
          setLastEvent(`Data regime is now ${scenario} — open Forecast Timeline and hit Refresh`);
        }, 6000);
      }
    },
    [regimeChanging, playbackStatus.active]
  );

  const triggerSimulationOnly = () => {
    // Legacy entry point retained: replays the full 24H timeline on the floor
    handlePlayTimeline("24H", playbackSpeed);
  };

  const resetFloor = () => {
    setBeds(generateInitialBeds());
    setPatients(INITIAL_PATIENTS);
    setStaff(INITIAL_STAFF);
    setLastEvent(null);
  };

  const handleDecision = (workflowId: string, approved: boolean) => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify({ action: "SUBMIT_HUMAN_APPROVAL", workflow_id: workflowId, approved }));
    }
    setApprovalRequest(null);
  };

  const triggerCapacityCheck = async () => {
    try {
      await fetch(`${API_URL}/api/trigger-capacity-check`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ request_id: `REQ-${Date.now()}`, hospital_id: "HOSPITAL-MAIN-01", unit_id: selectedWard, objective: `Predict 24h bed occupancy and patient flow for ${selectedWard}.` }),
      });
    } catch { setLastEvent("Error: Failed to connect"); }
  };

  // Deduplicate patients by ID (safety net for race conditions)
  const uniquePatients = useMemo(() => {
    const seen = new Set<string>();
    return patients.filter((p) => { if (seen.has(p.id)) return false; seen.add(p.id); return true; });
  }, [patients]);

  const occupiedCount = beds.filter((b) => b.isOccupied).length;
  const queuedCount = uniquePatients.filter((p) => p.status === "ARRIVED" || (p.status === "ESCORTED" && !p.bedId)).length;

  return (
    <main style={{ position: "relative", width: "100vw", height: "100vh", overflow: "hidden" }}>
      {/* Control panel */}
      <div className="glass-panel fade-up" style={{ position: "absolute", top: 16, left: 16, zIndex: 10, padding: 20, width: 288, color: "var(--card-foreground)" }}>
        <div style={{ marginBottom: 16 }}>
          <h1 style={{ fontFamily: "var(--font-display), sans-serif", fontSize: 28, fontWeight: 400, letterSpacing: "0.04em", textTransform: "uppercase" as const, lineHeight: 1.16 }}>
            Hospital Floor
          </h1>
          <p style={{ fontSize: 12, color: "var(--muted-foreground)", marginTop: 4 }}>Temporal + LangGraph + Gemini + Claw3D</p>
        </div>

        <div style={{ marginBottom: 12, display: "flex", gap: 6, flexWrap: "wrap" }}>
          {isConnected ? (
            <span className="ui-badge ui-badge-status-connected" style={{ fontSize: 10, gap: 6 }}>
              <div className="ui-dot ui-dot-connected status-ping" /> Connected
            </span>
          ) : (
            <span className="ui-badge ui-badge-status-disconnected" style={{ fontSize: 10, gap: 6 }}>
              <div className="ui-dot ui-dot-disconnected" /> Disconnected
            </span>
          )}
          {playbackStatus.active ? (
            <span className="ui-badge ui-badge-status-running" style={{ fontSize: 10 }}>
              ▶ SIMULATING{playbackStatus.stepIndex !== null ? ` STEP ${playbackStatus.stepIndex}` : ""}
              {playbackStatus.occupiedBeds !== null ? ` · ${playbackStatus.occupiedBeds} beds` : ""}
            </span>
          ) : (
            <span className="ui-badge ui-badge-status-idle" style={{ fontSize: 10 }}>○ FLOOR IDLE</span>
          )}
        </div>

        {/* Ward selector */}
        <div style={{ marginBottom: 14 }}>
          <p style={{ fontSize: 10, color: "var(--muted-foreground)", textTransform: "uppercase", letterSpacing: "0.06em", marginBottom: 6 }}>Ward</p>
          <div style={{ display: "flex", gap: 4 }}>
            {[
              { id: "ICU-EAST", label: "ICU" },
              { id: "GENERAL-MALE", label: "Male" },
              { id: "GENERAL-FEMALE", label: "Female" },
              { id: "STEP-DOWN", label: "Step-Down" },
            ].map((w) => (
              <button
                key={w.id}
                onClick={() => setSelectedWard(w.id)}
                className="sidebar-btn-ghost"
                style={{
                  flex: 1,
                  fontSize: 11,
                  padding: "5px 2px",
                  justifyContent: "center",
                  ...(selectedWard === w.id
                    ? { background: "var(--primary)", color: "var(--primary-foreground)", borderColor: "var(--primary)" }
                    : {}),
                }}
              >
                {w.label}
              </button>
            ))}
          </div>
        </div>

        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          <button onClick={triggerCapacityCheck} className="sidebar-btn-primary w-full" disabled={!isConnected} style={{ gap: 8 }}>
            <Zap size={15} /> Run Capacity Check
          </button>
          <button onClick={triggerSimulationOnly} className="sidebar-btn-ghost w-full" disabled={!isConnected || playbackStatus.active} style={{ gap: 8 }}>
            <Film size={15} /> Replay 24H Timeline
          </button>
          <button onClick={() => setShowERAdmissions((v) => !v)} className="sidebar-btn-ghost w-full" disabled={!isConnected} style={{ gap: 8, color: "#f59e0b" }}>
            <Zap size={15} /> {showERAdmissions ? "Hide ER Admissions" : "ER Fast-Track Admissions"}
          </button>
          <button onClick={() => setShowForecast((v) => !v)} className="sidebar-btn-ghost w-full" style={{ gap: 8 }}>
            <TrendingUp size={15} /> {showForecast ? "Hide Forecast" : "Forecast Timeline"}
          </button>
          <div style={{ display: "flex", gap: 8 }}>
            <button onClick={resetFloor} className="sidebar-btn-ghost" style={{ flex: 1, gap: 6, fontSize: 12 }} disabled={playbackStatus.active}>
              ↺ Reset Floor
            </button>
          </div>
        </div>

        {/* Data regime switcher */}
        <div style={{ marginTop: 12 }}>
          <span style={{ fontSize: 9, color: "var(--muted-foreground)", textTransform: "uppercase", letterSpacing: "0.05em" }}>
            Data Regime
          </span>
          <select
            value={dataRegime}
            onChange={(e) => handleRegimeChange(e.target.value)}
            disabled={regimeChanging || playbackStatus.active}
            style={{
              width: "100%",
              marginTop: 4,
              background: "rgba(148,163,184,0.08)",
              border: "1px solid rgba(148,163,184,0.25)",
              borderRadius: 6,
              color: "var(--card-foreground)",
              fontSize: 11,
              padding: "6px 8px",
              cursor: "pointer",
            }}
          >
            {DATA_REGIMES.map((r) => (
              <option key={r.key} value={r.key} style={{ background: "#0b1118" }}>
                {r.label}
              </option>
            ))}
          </select>
          {regimeChanging && (
            <div style={{ fontSize: 9, color: "#38bdf8", marginTop: 3 }}>⟳ regenerating data + forecast…</div>
          )}
        </div>

        <div style={{ marginTop: 10, display: "flex", alignItems: "center", gap: 8 }}>
          <ThemeToggle theme={theme} onThemeChange={setTheme} />
        </div>

        <div style={{ marginTop: 10, display: "flex", flexWrap: "wrap", gap: 6 }}>
          {["Temporal", "LangGraph", "Gemini", "3D"].map((tag) => (
            <span key={tag} className="ui-badge ui-badge-status-idle" style={{ fontSize: 9 }}>{tag}</span>
          ))}
        </div>
      </div>

      <StatusPanel
        beds={beds}
        patients={uniquePatients}
        isConnected={isConnected}
        lastEvent={lastEvent}
        wardLabel={selectedWard}
        next24h={patientFlow?.next_24h ?? null}
      />

      <BedAssignmentsPanel
        beds={beds}
        patients={uniquePatients}
        fastTrackMatches={fastTrackResult?.matches ?? []}
        onFocusBed={handleFocusBed}
      />

      {/* 3D Canvas */}
      <div style={{ position: "absolute", inset: 0, zIndex: 0, background: "var(--3d-bg, #0b1118)" }}>
        {sceneError ? <Scene3DFallback error={sceneError} /> : (
          <Scene3DErrorBoundary fallback={<Scene3DFallback />}>
            <HospitalFloor
              beds={beds}
              patients={uniquePatients}
              staff={staff}
              theme={theme}
              playbackInfo={playbackStatus}
              lastEvent={lastEvent}
              fastTrackMatches={fastTrackResult?.matches ?? []}
              focusBedId={focusBedRequest}
              staffAlerts={staffAlerts}
              staffAlertsToken={staffAlertsToken}
            />
          </Scene3DErrorBoundary>
        )}
      </div>

      {/* Bottom bar */}
      <div className="glass-panel fade-up-delay" style={{ position: "absolute", bottom: 16, left: 16, zIndex: 10, padding: "12px 20px", display: "flex", alignItems: "center", gap: 16 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <Shield size={13} style={{ color: "var(--primary)" }} />
          <span style={{ fontSize: 12, color: "var(--muted-foreground)" }}>Hospital AI Agent Twin</span>
        </div>
        <div style={{ width: 1, height: 16, background: "var(--border)" }} />
        <span style={{ fontSize: 12, color: occupiedCount >= 8 ? "#ef4444" : "#64748b", fontFamily: "var(--font-mono), monospace" }}>
          {occupiedCount}/{beds.length} beds
        </span>
        <div style={{ width: 1, height: 16, background: "var(--border)" }} />
        <span style={{ fontSize: 12, color: queuedCount > 2 ? "#f59e0b" : "#64748b", fontFamily: "var(--font-mono), monospace" }}>
          {queuedCount} in queue
        </span>
      </div>

      {showERAdmissions && (
        <ERAdmissionsPanel
          onClose={() => setShowERAdmissions(false)}
          onSubmitBoarders={handleSubmitBoarders}
          onSimulateSurge={handleSimulateSurge}
          result={fastTrackResult}
          processing={fastTrackProcessing}
          connected={isConnected && !playbackStatus.active}
          stages={agentStages}
          dispatches={agentDispatches}
        />
      )}

      {showForecast && (
        <ForecastTimelineChart
          onClose={() => setShowForecast(false)}
          onAnimateStep={handleStepSelect}
          onPlayTimeline={handlePlayTimeline}
          onStopPlayback={handleStopPlayback}
          playingStep={playbackStatus.active ? playbackStatus.stepIndex : null}
          playbackTotalSteps={playbackStatus.totalSteps}
          playbackDisabled={playbackStatus.active}
          speed={playbackSpeed}
          onSpeedChange={setPlaybackSpeed}
          unitId={selectedWard}
        />
      )}

      {approvalRequest && (
        <ApprovalModal workflowId={approvalRequest.workflow_id} recommendations={approvalRequest.recommendations} onDecision={handleDecision} />
      )}
    </main>
  );
}
