"use client";

import React, { useCallback, useEffect, useMemo, useState } from "react";
import {
  Area,
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import {
  ChevronLeft,
  ChevronRight,
  FlaskConical,
  Maximize2,
  Minimize2,
  Pause,
  Play,
  RefreshCw,
  ShieldCheck,
  TrendingUp,
  X,
} from "lucide-react";
import type {
  AccuracyResponse,
  BacktestResponse,
  ForecastPointDoc,
  HistoryDatesResponse,
  HorizonType,
  MultiHorizonForecastResponse,
  PatientFlowResponse,
  ScenarioResponse,
} from "@/types/forecast";

const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

const HORIZONS: { key: HorizonType; label: string; description: string }[] = [
  { key: "24H", label: "24H", description: "Hourly forecast — next 24 hours" },
  { key: "7D", label: "7D", description: "Daily forecast — next 7 days" },
  { key: "6M", label: "6M", description: "Monthly forecast — next 6 months" },
];

const SPEEDS = [0.5, 1, 2] as const;

const SEVERITY_COLORS: Record<string, string> = {
  low: "#22c55e",
  medium: "#f59e0b",
  high: "#f97316",
  critical: "#ef4444",
};

export interface ChartDatum {
  step: number;
  label: string;
  occupancy: number;
  actualOcc?: number;
  lower: number;
  upper: number;
  band: [number, number];
  peak: number;
  beds: number;
  anomaly: boolean;
  severity: string;
  explanation: string | null;
  drivers?: Record<string, number> | null;
}

interface ForecastTimelineChartProps {
  onClose: () => void;
  /** Animate the floor to a specific forecast step. */
  onAnimateStep?: (datum: ChartDatum, horizon: HorizonType) => void;
  /** Time-lapse the whole horizon. */
  onPlayTimeline?: (horizon: HorizonType, speed: number) => void;
  /** Abort the running playback. */
  onStopPlayback?: () => void;
  /** Currently playing step (drives highlight); null when idle. */
  playingStep?: number | null;
  /** Total steps in the active playback (for the progress bar). */
  playbackTotalSteps?: number | null;
  /** Disables interactions while a playback is running. */
  playbackDisabled?: boolean;
  /** Current playback speed multiplier. */
  speed?: number;
  /** Ward whose patient-flow is shown in the flow card. */
  unitId?: string;
  /** Called when the user changes playback speed. */
  onSpeedChange?: (speed: number) => void;
}

function formatLabel(timestamp: string, horizon: HorizonType): string {
  const d = new Date(timestamp);
  if (Number.isNaN(d.getTime())) return timestamp;
  if (horizon === "24H") {
    return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false });
  }
  if (horizon === "7D") {
    return d.toLocaleDateString([], { weekday: "short", day: "numeric", month: "short" });
  }
  return d.toLocaleDateString([], { month: "short", year: "2-digit" });
}

function toChartData(points: ForecastPointDoc[], horizon: HorizonType): ChartDatum[] {
  return points.map((p) => ({
    step: p.time_step_index,
    label: formatLabel(p.timestamp, horizon),
    occupancy: Math.round(p.predicted_occupancy * 1000) / 10,
    actualOcc: p.actual_occupancy != null ? Math.round(p.actual_occupancy * 1000) / 10 : undefined,
    lower: Math.round(p.lower_bound * 1000) / 10,
    upper: Math.round(p.upper_bound * 1000) / 10,
    band: [
      Math.round(p.lower_bound * 1000) / 10,
      Math.round(p.upper_bound * 1000) / 10,
    ],
    peak: Math.round((p.peak_occupancy ?? p.predicted_occupancy) * 1000) / 10,
    beds: Math.round(p.predicted_occupancy * 10),
    anomaly: p.has_anomaly,
    severity: p.anomaly_severity,
    explanation: p.anomaly_explanation,
    drivers: p.drivers ?? null,
  }));
}

/* ═══════════════════════════════════════════════════════
   Small presentational helpers
   ═══════════════════════════════════════════════════════ */
function StatChip({ label, value }: { label: string; value: string }) {
  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        padding: "4px 12px",
        borderRadius: 8,
        background: "rgba(148,163,184,0.08)",
        minWidth: 64,
      }}
    >
      <span style={{ fontSize: 9, color: "var(--muted-foreground)", textTransform: "uppercase", letterSpacing: "0.05em" }}>
        {label}
      </span>
      <span style={{ fontSize: 13, fontWeight: 700, color: "var(--card-foreground)", fontFamily: "var(--font-mono), monospace" }}>
        {value}
      </span>
    </div>
  );
}

function LegendToggle({
  label,
  color,
  checked,
  onChange,
}: {
  label: string;
  color: string;
  checked: boolean;
  onChange: (v: boolean) => void;
}) {
  return (
    <button
      onClick={() => onChange(!checked)}
      style={{
        display: "flex",
        alignItems: "center",
        gap: 5,
        fontSize: 9,
        color: checked ? "var(--muted-foreground)" : "var(--muted-foreground)",
        background: "none",
        border: "none",
        cursor: "pointer",
        padding: "2px 4px",
      }}
    >
      <span
        style={{
          width: 10,
          height: 10,
          borderRadius: 2,
          background: checked ? color : "transparent",
          border: `1px solid ${color}`,
          display: "inline-block",
        }}
      />
      {label}
    </button>
  );
}

/* ═══════════════════════════════════════════════════════
   Main component
   ═══════════════════════════════════════════════════════ */
export default function ForecastTimelineChart({
  onClose,
  onAnimateStep,
  onPlayTimeline,
  onStopPlayback,
  playingStep = null,
  playbackTotalSteps = null,
  playbackDisabled = false,
  speed = 1,
  onSpeedChange,
  unitId = "ICU-EAST",
}: ForecastTimelineChartProps) {
  const [horizon, setHorizon] = useState<HorizonType>("24H");
  const [data, setData] = useState<ChartDatum[]>([]);
  const [selectedStep, setSelectedStep] = useState<number | null>(null);
  const [meta, setMeta] = useState<{
    totalPoints: number;
    forecastDate: string;
    error?: string;
  } | null>(null);
  const [loading, setLoading] = useState(false);
  const [showBand, setShowBand] = useState(true);
  const [showPeak, setShowPeak] = useState(false);
  const [showAnomalies, setShowAnomalies] = useState(true);
  const [showStrategy, setShowStrategy] = useState(false);
  const [expanded, setExpanded] = useState(false);

  /* ── Back-date exploration state ── */
  const [availableDates, setAvailableDates] = useState<string[]>([]);
  const [viewDate, setViewDate] = useState<string | null>(null); // null = today/latest
  const [isPastView, setIsPastView] = useState(false);

  // ESC exits expanded mode
  useEffect(() => {
    if (!expanded) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setExpanded(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [expanded]);

  /* ── What-if scenario state ── */
  const [bedDelta, setBedDelta] = useState(0);
  const [deferral, setDeferral] = useState(0);
  const [surge, setSurge] = useState(0);
  const [scenario, setScenario] = useState<ScenarioResponse | null>(null);
  const [scenarioRunning, setScenarioRunning] = useState(false);

  /* ── Accuracy / backtest state ── */
  const [accuracy, setAccuracy] = useState<AccuracyResponse | null>(null);
  const [backtestRunning, setBacktestRunning] = useState(false);
  const [backtestResult, setBacktestResult] = useState<BacktestResponse | null>(null);
  const [patientFlow, setPatientFlow] = useState<PatientFlowResponse | null>(null);
  const [flowRefreshToken, setFlowRefreshToken] = useState(0);

  const fetchPatientFlow = useCallback(async () => {
    try {
      const res = await fetch(`${API_URL}/api/forecast/patient-flow?days=7&unit_id=${unitId}`);
      const j: PatientFlowResponse = await res.json();
      setPatientFlow(j);
    } catch {
      /* keep last known good flow data */
    }
  }, [unitId]);

  const fetchData = useCallback(async (h: HorizonType, date: string | null = null) => {
    setLoading(true);
    try {
      const url = date
        ? `${API_URL}/api/forecast/multi-horizon?horizon_type=${h}&date=${date}`
        : `${API_URL}/api/forecast/multi-horizon?horizon_type=${h}`;
      const res = await fetch(url);
      const json: MultiHorizonForecastResponse = await res.json();
      setMeta({
        totalPoints: json.total_points ?? 0,
        forecastDate: json.forecast_date ?? "",
        error: json.error,
      });
      setIsPastView(Boolean(json.is_past));
      setData(toChartData(json.points ?? [], h));
      setSelectedStep(null);
    } catch {
      setMeta({ totalPoints: 0, forecastDate: "", error: "Failed to reach API" });
      setData([]);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchData(horizon, viewDate);
  }, [horizon, viewDate, fetchData]);

  /* Available forecast dates for the back-date picker */
  useEffect(() => {
    fetch(`${API_URL}/api/forecast/history-dates?horizon_type=24H&limit=14`)
      .then((r) => r.json())
      .then((j: HistoryDatesResponse) => setAvailableDates(j.dates ?? []))
      .catch(() => {});
  }, [meta?.forecastDate]);

  const navigateDate = useCallback(
    (dir: -1 | 1) => {
      if (!availableDates.length) return;
      const current = viewDate ?? availableDates[0];
      const idx = availableDates.indexOf(current);
      const next = Math.min(
        availableDates.length - 1,
        Math.max(0, (idx === -1 ? 0 : idx) + dir)
      );
      setViewDate(availableDates[next]);
    },
    [availableDates, viewDate]
  );

  /* ── Strategy loop: accuracy badge + scenario run ── */
  useEffect(() => {
    fetch(`${API_URL}/api/forecast/accuracy?horizon_type=24H&unit_id=${unitId}&days=7`)
      .then((r) => r.json())
      .then((j: AccuracyResponse) => setAccuracy(j))
      .catch(() => {});
  }, [unitId]);

  useEffect(() => {
    fetchPatientFlow();
  }, [unitId, flowRefreshToken, fetchPatientFlow]);

  const runScenario = useCallback(async () => {
    setScenarioRunning(true);
    try {
      const res = await fetch(`${API_URL}/api/forecast/scenario`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          bed_delta: bedDelta,
          elective_deferral_pct: deferral,
          er_surge_pct: surge,
        }),
      });
      const json: ScenarioResponse = await res.json();
      if (!json.error) setScenario(json);
    } catch {
      /* keep previous scenario */
    } finally {
      setScenarioRunning(false);
    }
  }, [bedDelta, deferral, surge]);

  const runBacktest = useCallback(async () => {
    setBacktestRunning(true);
    try {
      const res = await fetch(`${API_URL}/api/forecast/backtest`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ days: 14 }),
      });
      const json: BacktestResponse = await res.json();
      setBacktestResult(json);
      const acc = await fetch(`${API_URL}/api/forecast/accuracy?horizon_type=24H&days=7`);
      setAccuracy(await acc.json());
    } catch {
      /* noop */
    } finally {
      setBacktestRunning(false);
    }
  }, []);

  const activeDescription = useMemo(
    () => HORIZONS.find((h) => h.key === horizon)?.description ?? "",
    [horizon]
  );

  const anomalyCount = data.filter((d) => d.anomaly).length;

  /* Past-date views are read-only: no live playback/animation */
  const interactive = !playbackDisabled && !isPastView;

  /* Per-day accuracy: predicted vs actual (past-date views only) */
  const dayMae = useMemo(() => {
    const pairs = data.filter((d) => d.actualOcc != null);
    if (pairs.length === 0) return null;
    const mae = pairs.reduce((acc, d) => acc + Math.abs(d.occupancy - d.actualOcc!), 0) / pairs.length;
    return { mae: Math.round(mae * 10) / 10, points: pairs.length };
  }, [data]);

  /* Merge scenario occupancy into chart rows by index */
  const chartData = useMemo(() => {
    if (!scenario) return data;
    return data.map((d, i) => ({
      ...d,
      scenarioOcc: scenario.scenario[i]
        ? Math.round(scenario.scenario[i].predicted_occupancy * 1000) / 10
        : undefined,
    }));
  }, [data, scenario]);

  const gradeColor =
    accuracy?.aggregate?.grade === "good"
      ? "#22c55e"
      : accuracy?.aggregate?.grade === "fair"
        ? "#f59e0b"
        : accuracy?.aggregate?.grade === "poor"
          ? "#ef4444"
          : "#64748b";

  const stats = useMemo(() => {
    if (!data.length) return null;
    const occ = data.map((d) => d.occupancy);
    return {
      min: Math.min(...occ),
      max: Math.max(...occ),
      avg: occ.reduce((a, b) => a + b, 0) / occ.length,
    };
  }, [data]);

  const selectedDatum = useMemo(
    () => data.find((d) => d.step === selectedStep) ?? null,
    [data, selectedStep]
  );

  /* ─── Navigation ─── */
  const navigate = useCallback(
    (delta: number) => {
      if (!data.length || !interactive) return;
      const currentIdx = selectedStep ? data.findIndex((d) => d.step === selectedStep) : -1;
      const nextIdx = Math.min(data.length - 1, Math.max(0, (currentIdx === -1 ? 0 : currentIdx) + delta));
      const datum = data[nextIdx];
      setSelectedStep(datum.step);
      onAnimateStep?.(datum, horizon);
    },
    [data, selectedStep, interactive, onAnimateStep, horizon]
  );

  useEffect(() => {
    if (!onAnimateStep) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.target instanceof HTMLInputElement) return;
      if (e.key === "ArrowLeft") navigate(-1);
      if (e.key === "ArrowRight") navigate(1);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [navigate, onAnimateStep]);

  const scrubTo = useCallback(
    (step: number, animate: boolean) => {
      const datum = data.find((d) => d.step === step);
      if (!datum) return;
      setSelectedStep(step);
      if (animate && interactive) onAnimateStep?.(datum, horizon);
    },
    [data, interactive, onAnimateStep, horizon]
  );

  const dotRenderer = useCallback(
    (props: { cx?: number; cy?: number; payload?: ChartDatum }) => {
      const { cx, cy, payload } = props;
      if (cx === undefined || cy === undefined || !payload) return null;
      const isSelected =
        (playingStep !== null && payload.step === playingStep) ||
        (playingStep === null && payload.step === selectedStep);
      const isPlaying = playingStep !== null && payload.step === playingStep;

      const circle = (
        <circle
          key={`dot-${payload.step}`}
          cx={cx}
          cy={cy}
          r={isPlaying ? 6 : isSelected ? 5 : 3}
          fill={
            payload.anomaly && showAnomalies
              ? SEVERITY_COLORS[payload.severity] ?? "#ef4444"
              : "#38bdf8"
          }
          stroke={isSelected ? "#fff" : "none"}
          strokeWidth={isSelected ? 1.5 : 0}
          fillOpacity={payload.anomaly || isSelected ? 1 : 0.75}
          style={{ cursor: onAnimateStep ? "pointer" : "default" }}
          onClick={() => interactive && onAnimateStep?.(payload, horizon)}
        />
      );
      if (isPlaying) {
        return (
          <g key={`pulse-${payload.step}`}>
            <circle cx={cx} cy={cy} r={10} fill="#38bdf8" fillOpacity={0.25}>
              <animate attributeName="r" values="7;13;7" dur="1s" repeatCount="indefinite" />
              <animate attributeName="fill-opacity" values="0.35;0.08;0.35" dur="1s" repeatCount="indefinite" />
            </circle>
            {circle}
          </g>
        );
      }
      return circle;
    },
    [playingStep, selectedStep, showAnomalies, interactive, onAnimateStep, horizon]
  );

  const progressPct =
    playingStep !== null && playbackTotalSteps
      ? Math.round((playingStep / playbackTotalSteps) * 100)
      : null;

  return (
    <div
      className="glass-panel fade-up"
      style={{
        position: "absolute",
        ...(expanded
          ? { top: 16, right: 16, bottom: 16, left: 16, width: "auto" }
          : { right: 16, bottom: 16, width: 820 }),
        zIndex: 20,
        maxWidth: "calc(100vw - 32px)",
        maxHeight: expanded ? "calc(100vh - 32px)" : undefined,
        padding: 16,
        color: "var(--card-foreground)",
        display: "flex",
        flexDirection: "column",
        gap: 10,
        overflowY: expanded ? "auto" : undefined,
        transition: "all .25s ease",
      }}
    >
      {/* Header */}
      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <TrendingUp size={15} style={{ color: "var(--primary)" }} />
        <span style={{ fontSize: 13, fontWeight: 600, letterSpacing: "0.03em" }}>
          Occupancy Forecast Timeline
        </span>
        <span style={{ fontSize: 10, color: "var(--muted-foreground)" }}>
          TimesFM 2.5 · {activeDescription}
        </span>
        <div style={{ flex: 1 }} />

        {/* Back-date navigator */}
        <button
          onClick={() => navigateDate(1)}
          disabled={!availableDates.length || (viewDate ?? "") >= (availableDates[availableDates.length - 1] ?? "")}
          className="sidebar-btn-ghost"
          title="Older forecast date"
          style={{ padding: "4px 6px" }}
        >
          <ChevronLeft size={13} />
        </button>
        <select
          value={viewDate ?? availableDates[0] ?? ""}
          onChange={(e) => setViewDate(e.target.value || null)}
          style={{
            fontSize: 10,
            padding: "4px 6px",
            background: "rgba(148,163,184,0.08)",
            border: isPastView ? "1px solid #9ca3af" : "1px solid rgba(148,163,184,0.25)",
            borderRadius: 6,
            color: "var(--card-foreground)",
            cursor: "pointer",
            maxWidth: 130,
          }}
          title="Forecast date"
        >
          {(availableDates.length ? availableDates : [meta?.forecastDate || ""]).map((d) => (
            <option key={d} value={d} style={{ background: "#0b1118" }}>{d}</option>
          ))}
        </select>
        <button
          onClick={() => navigateDate(-1)}
          disabled={viewDate === null}
          className="sidebar-btn-ghost"
          title="Newer forecast date"
          style={{ padding: "4px 6px" }}
        >
          <ChevronRight size={13} />
        </button>
        {viewDate && (
          <button
            onClick={() => setViewDate(null)}
            className="sidebar-btn-primary"
            style={{ fontSize: 9, padding: "4px 8px" }}
          >
            Today
          </button>
        )}
        {dayMae && (
          <span
            title={`Predicted vs actual across ${dayMae.points} evaluated hours`}
            style={{
              fontSize: 9,
              fontWeight: 700,
              color: dayMae.mae < 3 ? "#22c55e" : dayMae.mae < 6 ? "#f59e0b" : "#ef4444",
              border: `1px solid ${dayMae.mae < 3 ? "#22c55e55" : dayMae.mae < 6 ? "#f59e0b55" : "#ef444455"}`,
              borderRadius: 6,
              padding: "3px 7px",
              whiteSpace: "nowrap",
            }}
          >
            MAE {dayMae.mae}% this day
          </span>
        )}
        <button
          onClick={() => setExpanded((v) => !v)}
          className="sidebar-btn-ghost"
          title={expanded ? "Collapse (Esc)" : "Expand for better visualization"}
          style={{ padding: "4px 6px" }}
        >
          {expanded ? <Minimize2 size={13} /> : <Maximize2 size={13} />}
        </button>
        <button
          onClick={() => {
            fetchData(horizon);
            setFlowRefreshToken((t) => t + 1);
          }}
          className="sidebar-btn-ghost"
          title="Refresh"
          style={{ padding: "4px 6px" }}
        >
          <RefreshCw size={13} />
        </button>
        <button onClick={onClose} className="sidebar-btn-ghost" title="Close" style={{ padding: "4px 6px" }}>
          <X size={13} />
        </button>
      </div>

      {/* Stats strip */}
      {stats && (
        <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
          <StatChip label="Min" value={`${stats.min.toFixed(1)}%`} />
          <StatChip label="Avg" value={`${stats.avg.toFixed(1)}%`} />
          <StatChip label="Max" value={`${stats.max.toFixed(1)}%`} />
          <StatChip label="Pts" value={String(data.length)} />
          {anomalyCount > 0 && (
            <span className="ui-badge ui-badge-status-disconnected" style={{ fontSize: 9 }}>
              ⚠ {anomalyCount} anomalies
            </span>
          )}
          <div style={{ flex: 1 }} />
          {/* Legend toggles */}
          <LegendToggle label="Band" color="rgba(56,189,248,0.5)" checked={showBand} onChange={setShowBand} />
          <LegendToggle label="Peak" color="#a78bfa" checked={showPeak} onChange={setShowPeak} />
          <LegendToggle label="Anomaly" color="#ef4444" checked={showAnomalies} onChange={setShowAnomalies} />
        </div>
      )}

      {/* Drift alarm (guide §10: model drift mitigation) */}
      {accuracy?.aggregate?.grade === "poor" && (
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 8,
            padding: "6px 10px",
            borderRadius: 8,
            background: "rgba(239,68,68,0.1)",
            border: "1px solid rgba(239,68,68,0.4)",
            fontSize: 10,
            color: "#fca5a5",
          }}
        >
          ⚠ MODEL DRIFT DETECTED — 7-day MAE {accuracy.aggregate.mae_avg}% exceeds safe
          threshold (6%). Forecasts should be interpreted with caution; retraining or
          regime review recommended.
        </div>
      )}

      {/* Strategy loop bar */}
      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <button
          onClick={() => setShowStrategy((v) => !v)}
          className="sidebar-btn-ghost"
          style={{
            fontSize: 10,
            gap: 6,
            padding: "4px 10px",
            borderColor: showStrategy ? "var(--primary)" : undefined,
            color: showStrategy ? "var(--primary)" : undefined,
          }}
        >
          <FlaskConical size={12} /> What-If Scenario
        </button>
        <button
          onClick={runBacktest}
          disabled={backtestRunning}
          className="sidebar-btn-ghost"
          style={{ fontSize: 10, gap: 6 }}
          title="Re-score last 14 days of forecasts against actuals"
        >
          <ShieldCheck size={12} /> {backtestRunning ? "Backtesting…" : "Backtest"}
        </button>
        <div style={{ flex: 1 }} />
        {/* Accuracy trust badge */}
        {accuracy?.aggregate ? (
          <span
            title={`Bias ${accuracy.aggregate.bias_avg > 0 ? "+" : ""}${accuracy.aggregate.bias_avg}% · ${accuracy.aggregate.days_evaluated} days evaluated`}
            style={{
              fontSize: 10,
              fontWeight: 700,
              color: gradeColor,
              border: `1px solid ${gradeColor}55`,
              borderRadius: 6,
              padding: "3px 8px",
            }}
          >
            {accuracy.aggregate.grade === "good" ? "✓" : accuracy.aggregate.grade === "fair" ? "≈" : "⚠"}{" "}
            24H MAE {accuracy.aggregate.mae_avg}%
          </span>
        ) : (
          <span style={{ fontSize: 9, color: "var(--muted-foreground)" }}>no accuracy data yet</span>
        )}
      </div>

      {showStrategy && (
        <div style={{ display: "flex", flexDirection: "column", gap: 8, padding: 10, borderRadius: 8, background: "rgba(245,158,11,0.05)", border: "1px solid rgba(245,158,11,0.2)" }}>
          {/* Sliders */}
          <div style={{ display: "flex", gap: 14 }}>
            <label style={{ flex: 1, fontSize: 9, color: "var(--muted-foreground)" }}>
              BEDS Δ ({bedDelta > 0 ? "+" : ""}{bedDelta})
              <input type="range" min={-5} max={5} value={bedDelta} onChange={(e) => setBedDelta(Number(e.target.value))} style={{ width: "100%", accentColor: "#f59e0b" }} />
            </label>
            <label style={{ flex: 1, fontSize: 9, color: "var(--muted-foreground)" }}>
              DEFER ELECTIVES ({deferral}%)
              <input type="range" min={0} max={100} value={deferral} onChange={(e) => setDeferral(Number(e.target.value))} style={{ width: "100%", accentColor: "#f59e0b" }} />
            </label>
            <label style={{ flex: 1, fontSize: 9, color: "var(--muted-foreground)" }}>
              ER SURGE ({surge > 0 ? "+" : ""}{surge}%)
              <input type="range" min={-50} max={100} value={surge} onChange={(e) => setSurge(Number(e.target.value))} style={{ width: "100%", accentColor: "#f59e0b" }} />
            </label>
            <button onClick={runScenario} disabled={scenarioRunning} className="sidebar-btn-primary" style={{ fontSize: 11, alignSelf: "flex-end", whiteSpace: "nowrap" }}>
              {scenarioRunning ? "Running…" : "Run"}
            </button>
          </div>

          {/* Summary chips */}
          {scenario && (
            <div style={{ display: "flex", gap: 6, flexWrap: "wrap", fontSize: 10 }}>
              <span className="ui-badge ui-badge-status-idle">
                Peak {scenario.summary.peak_delta > 0 ? "+" : ""}{scenario.summary.peak_delta}%
              </span>
              <span className="ui-badge ui-badge-status-idle">
                Avg {scenario.summary.mean_delta > 0 ? "+" : ""}{scenario.summary.mean_delta}%
              </span>
              <span className="ui-badge ui-badge-status-idle">
                OR window {scenario.summary.or_window_delta > 0 ? "+" : ""}{scenario.summary.or_window_delta}%
              </span>
              <span className="ui-badge ui-badge-status-idle">
                Beds freed avg {scenario.summary.beds_freed_avg > 0 ? "+" : ""}{scenario.summary.beds_freed_avg}
              </span>
              {bedDelta !== 0 && (
                <span className="ui-badge ui-badge-status-idle">Capacity {scenario.summary.total_beds_after_change} beds</span>
              )}
              <button onClick={() => { setScenario(null); }} className="sidebar-btn-ghost" style={{ fontSize: 9, padding: "2px 6px" }}>
                clear
              </button>
            </div>
          )}
        </div>
      )}

      {/* Horizon tabs + transport controls */}
      <div style={{ display: "flex", gap: 6, alignItems: "center" }}>
        {HORIZONS.map((h) => (
          <button
            key={h.key}
            onClick={() => setHorizon(h.key)}
            disabled={interactive}
            className={`sidebar-btn-ghost ${horizon === h.key ? "forecast-tab-active" : ""}`}
            style={{
              fontSize: 11,
              padding: "5px 12px",
              borderColor: horizon === h.key ? "var(--primary)" : undefined,
              color: horizon === h.key ? "var(--primary)" : undefined,
              fontWeight: horizon === h.key ? 700 : 400,
              opacity: playbackDisabled && horizon !== h.key ? 0.4 : 1,
            }}
          >
            {h.label}
          </button>
        ))}

        <div style={{ width: 1, height: 18, background: "var(--border)", margin: "0 4px" }} />

        {/* Step navigation */}
        <button
          onClick={() => navigate(-1)}
          disabled={interactive || !data.length}
          className="sidebar-btn-ghost"
          title="Previous step (←)"
          style={{ padding: "5px 7px" }}
        >
          <ChevronLeft size={14} />
        </button>
        <button
          onClick={() => navigate(1)}
          disabled={interactive || !data.length}
          className="sidebar-btn-ghost"
          title="Next step (→)"
          style={{ padding: "5px 7px" }}
        >
          <ChevronRight size={14} />
        </button>

        {/* Play / Stop */}
        {interactive ? (
          <button onClick={() => onStopPlayback?.()} className="sidebar-btn-primary" title="Stop playback" style={{ fontSize: 11, padding: "5px 12px" }}>
            <Pause size={12} /> Stop
          </button>
        ) : (
          <button
            onClick={() => onPlayTimeline?.(horizon, speed)}
            disabled={!data.length}
            className="sidebar-btn-primary"
            title="Play full timeline on the 3D floor"
            style={{ fontSize: 11, padding: "5px 12px", opacity: data.length ? 1 : 0.5 }}
          >
            <Play size={12} /> Play
          </button>
        )}

        {/* Speed selector */}
        <div style={{ display: "flex", gap: 2, marginLeft: 2 }}>
          {SPEEDS.map((s) => (
            <button
              key={s}
              onClick={() => onSpeedChange?.(s)}
              className="sidebar-btn-ghost"
              style={{
                fontSize: 9,
                padding: "4px 6px",
                borderColor: speed === s ? "var(--primary)" : undefined,
                color: speed === s ? "var(--primary)" : "#64748b",
                fontWeight: speed === s ? 700 : 400,
              }}
            >
              {s}×
            </button>
          ))}
        </div>

        <div style={{ flex: 1 }} />
        {isPastView && (
          <span className="ui-badge ui-badge-status-idle" style={{ fontSize: 9 }}>
            🕐 PAST VIEW — playback off
          </span>
        )}
        <span style={{ fontSize: 10, color: "var(--muted-foreground)" }}>{loading ? "loading…" : ""}</span>
      </div>

      {/* Progress bar */}
      {progressPct !== null && (
        <div>
          <div style={{ display: "flex", justifyContent: "space-between", fontSize: 9, color: "var(--muted-foreground)", marginBottom: 2 }}>
            <span>PLAYING STEP {playingStep} / {playbackTotalSteps}</span>
            <span>{progressPct}%</span>
          </div>
          <div style={{ height: 4, borderRadius: 2, background: "rgba(148,163,184,0.15)", overflow: "hidden" }}>
            <div
              style={{
                height: "100%",
                width: `${progressPct}%`,
                background: "linear-gradient(90deg, #38bdf8, #a78bfa)",
                transition: "width 0.4s ease",
              }}
            />
          </div>
        </div>
      )}

      {/* Chart */}
      <div style={{ width: "100%", height: expanded ? "52vh" : 200, minHeight: 200 }}>
        {meta?.error || (!loading && data.length === 0) ? (
          <div
            style={{
              height: "100%",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              color: meta?.error ? "#ef4444" : "#64748b",
              fontSize: 12,
              textAlign: "center",
              padding: 12,
            }}
          >
            {meta?.error ||
              `No ${horizon} forecast available yet (${meta?.forecastDate}). Run the scheduled workflow or wait for the next cron trigger.`}
          </div>
        ) : (
          <>
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={chartData} margin={{ top: 8, right: 12, left: -18, bottom: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="rgba(148,163,184,0.15)" />
                <XAxis
                  dataKey="label"
                  tick={{ fontSize: 9, fill: "#64748b" }}
                  interval="preserveStartEnd"
                  minTickGap={24}
                />
                <YAxis tick={{ fontSize: 9, fill: "#64748b" }} domain={[0, 100]} unit="%" />
                <Tooltip
                  contentStyle={{
                    background: "#0b1118",
                    border: "1px solid rgba(148,163,184,0.3)",
                    borderRadius: 8,
                    fontSize: 11,
                    color: "var(--card-foreground)",
                  }}
                  formatter={(value) => `${Number(value)}%`}
                  labelFormatter={(label) => `Step: ${String(label)}`}
                />
                {showBand && (
                  <Area
                    type="monotone"
                    dataKey="band"
                    name="Confidence Band"
                    stroke="none"
                    fill="rgba(56,189,248,0.14)"
                    isAnimationActive={false}
                  />
                )}
                <Line
                  type="monotone"
                  dataKey="lower"
                  name="Lower Bound"
                  stroke="rgba(56,189,248,0.35)"
                  strokeWidth={1}
                  dot={false}
                  isAnimationActive={false}
                />
                <Line
                  type="monotone"
                  dataKey="upper"
                  name="Upper Bound"
                  stroke="rgba(56,189,248,0.35)"
                  strokeWidth={1}
                  dot={false}
                  isAnimationActive={false}
                />
                {showPeak && (
                  <Line
                    type="monotone"
                    dataKey="peak"
                    name="Peak Occupancy"
                    stroke="#a78bfa"
                    strokeWidth={1}
                    strokeDasharray="4 3"
                    dot={false}
                    isAnimationActive={false}
                  />
                )}
                <Line
                  type="monotone"
                  dataKey="occupancy"
                  name="Predicted Occupancy"
                  stroke="#38bdf8"
                  strokeWidth={2}
                  dot={dotRenderer}
                  isAnimationActive={false}
                  activeDot={{ r: 5, fill: "#38bdf8", stroke: "#fff" }}
                />
                {scenario && (
                  <Line
                    type="monotone"
                    dataKey="scenarioOcc"
                    name="Scenario"
                    stroke="#f59e0b"
                    strokeWidth={2}
                    strokeDasharray="6 3"
                    dot={false}
                    isAnimationActive={false}
                  />
                )}
                {isPastView && (
                  <Line
                    type="monotone"
                    dataKey="actualOcc"
                    name="Actual"
                    stroke="#9ca3af"
                    strokeWidth={2}
                    strokeDasharray="2 4"
                    dot={{ r: 2, fill: "#9ca3af" }}
                    isAnimationActive={false}
                  />
                )}
              </LineChart>
            </ResponsiveContainer>

            {/* Scrubber */}
            <div style={{ display: "flex", alignItems: "center", gap: 8, marginTop: -6 }}>
              <input
                type="range"
                min={data[0]?.step ?? 1}
                max={data[data.length - 1]?.step ?? 24}
                value={selectedStep ?? data[0]?.step ?? 1}
                onChange={(e) => scrubTo(Number(e.target.value), false)}
                onMouseUp={(e) => scrubTo(Number((e.target as HTMLInputElement).value), true)}
                onTouchEnd={(e) => scrubTo(Number((e.target as HTMLInputElement).value), true)}
                onKeyDown={(e) => e.stopPropagation()}
                disabled={interactive || !data.length}
                style={{ flex: 1, accentColor: "#38bdf8", cursor: "pointer" }}
              />
              <span style={{ fontSize: 10, color: "var(--muted-foreground)", minWidth: 110, textAlign: "right", fontFamily: "var(--font-mono), monospace" }}>
                {selectedDatum
                  ? `${selectedDatum.label} · ${Math.round(selectedDatum.beds)} beds`
                  : "drag to explore"}
              </span>
            </div>
          </>
        )}
      </div>

      {/* Patient-flow forecast card (admissions vs discharges) */}
      {patientFlow && patientFlow.forecast.length > 0 && (() => {
        const allZero = patientFlow.forecast.every(
          (f) => f.predicted_admissions === 0 && f.predicted_discharges === 0,
        );
        const hasHistory =
          Array.isArray(patientFlow.recent_history) &&
          patientFlow.recent_history.some((h) => h.admissions > 0 || h.discharges > 0);
        const useHistory = allZero && hasHistory;
        const bars = useHistory
          ? patientFlow.recent_history.slice(-7).map((h) => ({
              day: h.day,
              predicted_admissions: h.admissions,
              predicted_discharges: h.discharges,
              net_flow: h.admissions - h.discharges,
              er_direct: undefined as number | undefined,
              elective: undefined as number | undefined,
              icu_transfers: undefined as number | undefined,
            }))
          : patientFlow.forecast;
        return (
        <div style={{ padding: "8px 10px", borderRadius: 8, background: "rgba(148,163,184,0.06)" }}>
          <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 6 }}>
            <span style={{ fontSize: 9, color: "#94a3b8", textTransform: "uppercase", letterSpacing: "0.05em" }}>
              {useHistory ? "7-Day Patient Flow (actual history — forecast unavailable)" : "7-Day Patient Flow"}
            </span>
            {!useHistory && (
            <span style={{ fontSize: 8, color: "#475569" }}>
              {patientFlow.model === "mean_persistence_fallback" ? "(baseline model)" : "(TimesFM)"}
            </span>
            )}
            <span style={{ fontSize: 9, color: "#22c55e" }}>▼ discharges</span>
            <span style={{ fontSize: 9, color: "#38bdf8" }}>▲ admissions</span>
          </div>
          <div style={{ display: "flex", gap: 6 }}>
            {bars.map((f) => {
              const maxV = Math.max(
                ...bars.map((x) => Math.max(x.predicted_admissions, x.predicted_discharges)),
                1
              );
              return (
                <div key={f.day} style={{ flex: 1, textAlign: "center" }}>
                  <div style={{ display: "flex", flexDirection: "column", justifyContent: "flex-end", height: 44, gap: 2 }}>
                    <div
                      style={{ height: `${(f.predicted_admissions / maxV) * 100}%`, minHeight: 2, background: "#38bdf8", borderRadius: 2 }}
                      title={
                        f.er_direct != null
                          ? `Admissions ~${f.predicted_admissions} (ER ${f.er_direct}, elective ${f.elective ?? 0}, ICU-transfer ${f.icu_transfers ?? 0})`
                          : `Admissions ~${f.predicted_admissions}`
                      }
                    />
                    <div style={{ height: `${(f.predicted_discharges / maxV) * 100}%`, minHeight: 2, background: "#22c55e", borderRadius: 2 }} title={`Discharges ~${f.predicted_discharges}`} />
                  </div>
                  <div style={{ fontSize: 8, color: "#64748b", marginTop: 3 }}>
                    {new Date(f.day).toLocaleDateString([], { weekday: "short" })}
                  </div>
                  <div style={{ fontSize: 9, fontFamily: "var(--font-mono), monospace", color: f.net_flow > 0 ? "#fca5a5" : "#86efac" }}>
                    {f.net_flow > 0 ? "+" : ""}{f.net_flow}
                  </div>
                </div>
              );
            })}
          </div>
        </div>
        );
      })()}

      {/* Model comparison table (backtest) */}
      {backtestResult?.models && backtestResult.models.length > 0 && (
        <div style={{ padding: "8px 10px", borderRadius: 8, background: "rgba(167,139,250,0.06)", border: "1px solid rgba(167,139,250,0.25)" }}>
          <div style={{ fontSize: 9, color: "#a78bfa", textTransform: "uppercase", letterSpacing: "0.05em", marginBottom: 5 }}>
            Model Comparison — {backtestResult.aggregate?.days_evaluated ?? 0} days backtested
          </div>
          <table style={{ width: "100%", fontSize: 10, borderCollapse: "collapse", color: "#cbd5e1" }}>
            <thead>
              <tr style={{ color: "#64748b" }}>
                <th style={{ textAlign: "left", paddingBottom: 3 }}>Model</th>
                <th style={{ textAlign: "right" }}>MAE</th>
                <th style={{ textAlign: "right" }}>RMSE</th>
                <th style={{ textAlign: "right" }}>Bias</th>
              </tr>
            </thead>
            <tbody>
              {backtestResult.models.map((m) => (
                <tr key={m.model} style={{ borderTop: "1px solid rgba(148,163,184,0.12)" }}>
                  <td style={{ padding: "3px 0", fontWeight: m.model === "timesfm" ? 700 : 400 }}>
                    {m.model === "timesfm" ? "TimesFM 2.5 ⭐" : m.model.replace(/_/g, " ")}
                  </td>
                  <td style={{ textAlign: "right", fontFamily: "var(--font-mono), monospace" }}>{m.mae}%</td>
                  <td style={{ textAlign: "right", fontFamily: "var(--font-mono), monospace" }}>{m.rmse}%</td>
                  <td style={{ textAlign: "right", fontFamily: "var(--font-mono), monospace" }}>
                    {m.bias > 0 ? "+" : ""}{m.bias}%
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Selected-step detail card */}
      {selectedDatum && (
        <div
          style={{
            display: "flex",
            gap: 12,
            alignItems: "center",
            padding: "8px 12px",
            borderRadius: 8,
            background: "rgba(56,189,248,0.06)",
            border: "1px solid rgba(56,189,248,0.2)",
          }}
        >
          <div style={{ display: "flex", flexDirection: "column" }}>
            <span style={{ fontSize: 9, color: "var(--muted-foreground)", textTransform: "uppercase" }}>Step {selectedDatum.step}</span>
            <span style={{ fontSize: 13, fontWeight: 700, color: "#38bdf8", fontFamily: "var(--font-mono), monospace" }}>
              {selectedDatum.occupancy.toFixed(1)}%
            </span>
          </div>
          <div style={{ width: 1, height: 26, background: "var(--border)" }} />
          <div style={{ fontSize: 10, color: "var(--muted-foreground)", lineHeight: 1.5, flex: 1 }}>
            ~{selectedDatum.beds} beds on scene · CI [{selectedDatum.lower.toFixed(1)}%, {selectedDatum.upper.toFixed(1)}%]
            {selectedDatum.drivers && (
              <span style={{ color: "#94a3b8" }}>
                {" "}· drivers:{" "}
                {Object.entries(selectedDatum.drivers)
                  .filter(([k]) => k !== "base_model")
                  .map(([k, v]) => `${k} ${v > 0 ? "+" : ""}${v}%`)
                  .join(" · ")}
              </span>
            )}
            {selectedDatum.anomaly && (
              <span style={{ color: SEVERITY_COLORS[selectedDatum.severity] ?? "#ef4444", fontWeight: 700 }}>
                {" "}· ⚠ {selectedDatum.severity.toUpperCase()}: {selectedDatum.explanation ?? "anomaly detected"}
              </span>
            )}
          </div>
          {!interactive && (
            <button
              onClick={() => onAnimateStep?.(selectedDatum, horizon)}
              className="sidebar-btn-primary"
              style={{ fontSize: 11, padding: "6px 14px", whiteSpace: "nowrap" }}
            >
              <Play size={12} /> Animate
            </button>
          )}
        </div>
      )}
    </div>
  );
}
