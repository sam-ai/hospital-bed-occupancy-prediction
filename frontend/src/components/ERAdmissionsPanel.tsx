"use client";

import React, { useEffect, useMemo, useState } from "react";
import { Plus, Trash2, Zap, UserPlus, Radio, X, Check, Maximize2, Minimize2 } from "lucide-react";
import type {
  AgentDispatch,
  AgentStage,
  FastTrackMatch,
  FastTrackRunSummary,
  StaffNotification,
  WaitingPatientDTO,
} from "@/types/hospital";

const BED_TYPES = ["ICU", "MED_SURG", "TELEMETRY", "STEP_DOWN", "ISOLATION"] as const;

const ESI_COLORS: Record<number, string> = {
  1: "#ef4444",
  2: "#f97316",
  3: "#f59e0b",
  4: "#22c55e",
  5: "#0ea5e9",
};

const STATUS_META: Record<string, { label: string; color: string; bg: string }> = {
  READY_TO_ASSIGN: { label: "READY", color: "#22c55e", bg: "rgba(34,197,94,0.12)" },
  AWAITING_EVS_CLEANING: { label: "EVS CLEAN", color: "#f59e0b", bg: "rgba(245,158,11,0.12)" },
  NEEDS_EXPEDITED_DISCHARGE: { label: "DISCHARGE FIRST", color: "#f97316", bg: "rgba(249,115,22,0.12)" },
};

const CHANNEL_ICONS: Record<string, string> = {
  SLACK: "#💬",
  TWILIO_SMS: "📱",
  EHR_INBASKET: "📋",
  CLAW3D_UI_WEBSOCKET: "🖥️",
};

interface ERAdmissionsPanelProps {
  onClose: () => void;
  /** Sends the fast-track request over the WebSocket. */
  onSubmitBoarders: (boarders: WaitingPatientDTO[]) => void;
  /** Requests a simulated ER surge of `count` boarders. */
  onSimulateSurge: (count: number) => void;
  /** Latest fast-track result from the backend (null while processing). */
  result: {
    matches: FastTrackMatch[];
    notifications: StaffNotification[];
    total_boarders: number;
    admitted?: number;
    run_id?: string;
  } | null;
  processing: boolean;
  connected: boolean;
  /** Live agent pipeline stages (TRIAGE_MATCHING / ROLE_NOTIFICATIONS / CHANNEL_DISPATCH). */
  stages: AgentStage[];
  /** Live dispatch receipts streamed per channel. */
  dispatches: AgentDispatch[];
}

const STAGE_ORDER: { key: AgentStage["stage"]; label: string }[] = [
  { key: "TRIAGE_MATCHING", label: "Triage Matching" },
  { key: "ROLE_NOTIFICATIONS", label: "Staff Alerts" },
  { key: "CHANNEL_DISPATCH", label: "Channel Dispatch" },
];

function timeNow(): string {
  return new Date().toLocaleTimeString([], { hour12: false });
}

const emptyForm = (): WaitingPatientDTO => ({
  patient_id: `ER-${Math.floor(1000 + Math.random() * 9000)}`,
  mrn: `MRN${Math.floor(100000 + Math.random() * 900000)}`,
  esi_level: 3,
  news2_score: 5,
  wait_time_minutes: 30,
  required_bed_type: "MED_SURG",
  chief_complaint: "",
  isolation_required: false,
});

export default function ERAdmissionsPanel({
  onClose,
  onSubmitBoarders,
  onSimulateSurge,
  result,
  processing,
  connected,
  stages = [],
  dispatches = [],
}: ERAdmissionsPanelProps) {
  const [queue, setQueue] = useState<WaitingPatientDTO[]>([]);
  const [form, setForm] = useState<WaitingPatientDTO>(emptyForm());
  const [history, setHistory] = useState<FastTrackRunSummary[]>([]);
  const [expanded, setExpanded] = useState(false);

  // Record completed runs into session history
  useEffect(() => {
    if (result?.run_id && !processing) {
      setHistory((h) =>
        [
          {
            time: timeNow(),
            boarders: result.total_boarders,
            admitted: result.admitted,
            channels: new Set(result.notifications.map((n) => n.channel)).size,
          },
          ...h.filter((r) => r.time !== timeNow() || r.boarders !== result.total_boarders),
        ].slice(0, 5)
      );
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [result?.run_id]);

  const maxScore = useMemo(
    () => (result ? Math.max(...result.matches.map((m) => m.priority_score), 1) : 1),
    [result]
  );

  const addBoarder = () => {
    if (!connected) return;
    const entry = { ...form, chief_complaint: form.chief_complaint || "Unspecified complaint" };
    setQueue((q) => [...q, entry]);
    setForm(emptyForm());
  };

  return (
    <div
      className="glass-panel fade-up"
      style={{
        position: "absolute",
        right: 16,
        top: 16,
        zIndex: 20,
        width: expanded ? 680 : 420,
        maxHeight: "calc(100vh - 32px)",
        overflowY: "auto",
        padding: 16,
        color: "#f0f6ff",
        display: "flex",
        flexDirection: "column",
        gap: 10,
        transition: "all .25s ease",
      }}
    >
      {/* Header */}
      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <Zap size={15} style={{ color: "#f59e0b" }} />
        <span style={{ fontSize: 13, fontWeight: 600, letterSpacing: "0.03em" }}>
          Fast-Track ER Admissions
        </span>
        <div style={{ flex: 1 }} />
        <button
          onClick={() => setExpanded((v) => !v)}
          className="sidebar-btn-ghost"
          title={expanded ? "Collapse" : "Expand"}
          style={{ padding: "4px 6px" }}
        >
          {expanded ? <Minimize2 size={13} /> : <Maximize2 size={13} />}
        </button>
        <button onClick={onClose} className="sidebar-btn-ghost" title="Close" style={{ padding: "4px 6px" }}>
          <X size={13} />
        </button>
      </div>

      {/* Quick-add form */}
      <div style={{ display: "flex", flexDirection: "column", gap: 8, padding: 10, borderRadius: 8, background: "rgba(148,163,184,0.06)" }}>
        {/* ESI selector */}
        <div style={{ display: "flex", gap: 4, alignItems: "center" }}>
          <span style={{ fontSize: 9, color: "var(--muted-foreground)", width: 44 }}>ESI</span>
          {[1, 2, 3, 4, 5].map((lvl) => (
            <button
              key={lvl}
              onClick={() => setForm((f) => ({ ...f, esi_level: lvl as WaitingPatientDTO["esi_level"] }))}
              style={{
                flex: 1,
                padding: "5px 0",
                fontSize: 11,
                fontWeight: form.esi_level === lvl ? 800 : 400,
                background: form.esi_level === lvl ? ESI_COLORS[lvl] : "rgba(148,163,184,0.08)",
                color: form.esi_level === lvl ? "#fff" : "#94a3b8",
                border: "none",
                borderRadius: 6,
                cursor: "pointer",
              }}
              title={`ESI ${lvl}`}
            >
              {lvl}
            </button>
          ))}
        </div>

        {/* NEWS2 slider */}
        <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
          <span style={{ fontSize: 9, color: "var(--muted-foreground)", width: 44 }}>NEWS2</span>
          <input
            type="range"
            min={0}
            max={20}
            value={form.news2_score}
            onChange={(e) => setForm((f) => ({ ...f, news2_score: Number(e.target.value) }))}
            style={{ flex: 1, accentColor: "#38bdf8" }}
          />
          <span style={{ fontSize: 11, fontFamily: "var(--font-mono), monospace", minWidth: 20, textAlign: "right" }}>
            {form.news2_score}
          </span>
        </div>

        {/* Wait time + bed type */}
        <div style={{ display: "flex", gap: 8 }}>
          <div style={{ display: "flex", gap: 6, alignItems: "center", flex: 1 }}>
            <span style={{ fontSize: 9, color: "var(--muted-foreground)", width: 44 }}>WAIT</span>
            <input
              type="number"
              min={0}
              value={form.wait_time_minutes}
              onChange={(e) => setForm((f) => ({ ...f, wait_time_minutes: Number(e.target.value) }))}
              style={{
                width: 60,
                background: "rgba(148,163,184,0.08)",
                border: "1px solid rgba(148,163,184,0.2)",
                borderRadius: 6,
                color: "var(--card-foreground)",
                fontSize: 11,
                padding: "4px 6px",
              }}
            />
            <span style={{ fontSize: 9, color: "var(--muted-foreground)" }}>min</span>
          </div>
          <select
            value={form.required_bed_type}
            onChange={(e) => setForm((f) => ({ ...f, required_bed_type: e.target.value as WaitingPatientDTO["required_bed_type"] }))}
            style={{
              flex: 1,
              background: "rgba(148,163,184,0.08)",
              border: "1px solid rgba(148,163,184,0.2)",
              borderRadius: 6,
              color: "var(--card-foreground)",
              fontSize: 11,
              padding: "4px 6px",
            }}
          >
            {BED_TYPES.map((t) => (
              <option key={t} value={t} style={{ background: "#0b1118" }}>{t}</option>
            ))}
          </select>
        </div>

        {/* Complaint + isolation */}
        <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
          <input
            type="text"
            placeholder="Chief complaint…"
            value={form.chief_complaint}
            onChange={(e) => setForm((f) => ({ ...f, chief_complaint: e.target.value }))}
            onKeyDown={(e) => e.key === "Enter" && addBoarder()}
            style={{
              flex: 1,
              background: "rgba(148,163,184,0.08)",
              border: "1px solid rgba(148,163,184,0.2)",
              borderRadius: 6,
              color: "var(--card-foreground)",
              fontSize: 11,
              padding: "5px 8px",
            }}
          />
          <button
            onClick={() => setForm((f) => ({ ...f, isolation_required: !f.isolation_required }))}
            style={{
              fontSize: 9,
              padding: "5px 8px",
              borderRadius: 6,
              border: `1px solid ${form.isolation_required ? "#f59e0b" : "rgba(148,163,184,0.2)"}`,
              background: form.isolation_required ? "rgba(245,158,11,0.15)" : "transparent",
              color: form.isolation_required ? "#f59e0b" : "#64748b",
              cursor: "pointer",
            }}
            title="Isolation required"
          >
            ☣ ISO
          </button>
          <button onClick={addBoarder} disabled={!connected} className="sidebar-btn-primary" style={{ padding: "5px 8px" }} title="Add boarder to queue">
            <Plus size={13} />
          </button>
        </div>
      </div>

      {/* Queue + actions */}
      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <span className="ui-badge ui-badge-status-idle" style={{ fontSize: 10 }}>
          {queue.length} in queue
        </span>
        <div style={{ flex: 1 }} />
        <button
          onClick={() => onSimulateSurge(4)}
          disabled={!connected || processing}
          className="sidebar-btn-ghost"
          style={{ fontSize: 10, gap: 5 }}
          title="Generate 4 realistic ER boarders"
        >
          <UserPlus size={12} /> Simulate Surge ×4
        </button>
        <button
          onClick={() => {
            onSubmitBoarders(queue);
            setQueue([]);
          }}
          disabled={!connected || processing || queue.length === 0}
          className="sidebar-btn-primary"
          style={{ fontSize: 11, gap: 6 }}
        >
          <Zap size={13} /> {processing ? "Triaging…" : "Run Fast-Track"}
        </button>
      </div>

      {/* Queued boarders */}
      {queue.length > 0 && (
        <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
          {queue.map((b, i) => (
            <div key={b.patient_id + i} style={{ display: "flex", alignItems: "center", gap: 8, padding: "4px 8px", borderRadius: 6, background: "rgba(148,163,184,0.06)" }}>
              <span style={{ ...{ fontSize: 10, fontWeight: 700, color: "#fff" }, background: ESI_COLORS[b.esi_level], borderRadius: 4, padding: "1px 6px" } as React.CSSProperties}>
                ESI {b.esi_level}
              </span>
              <span style={{ fontSize: 10, color: "var(--muted-foreground)", flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                {b.mrn} · N{b.news2_score} · {b.wait_time_minutes}m · {b.required_bed_type}{b.isolation_required ? " · ☣" : ""}
              </span>
              <button
                onClick={() => setQueue((q) => q.filter((_, j) => j !== i))}
                className="sidebar-btn-ghost"
                style={{ padding: "2px 4px" }}
                title="Remove"
              >
                <Trash2 size={11} />
              </button>
            </div>
          ))}
        </div>
      )}

      {/* Agent pipeline stepper */}
      {(processing || stages.length > 0) && (
        <div style={{ display: "flex", gap: 4, alignItems: "center" }}>
          {STAGE_ORDER.map((s, i) => {
            const st = stages.find((x) => x.stage === s.key);
            const done = st?.status === "DONE";
            const running = st?.status === "RUNNING";
            return (
              <React.Fragment key={s.key}>
                {i > 0 && <div style={{ flex: 1, height: 1, background: done || running ? "#38bdf8" : "rgba(148,163,184,0.2)" }} />}
                <div
                  title={st?.detail}
                  style={{
                    display: "flex",
                    alignItems: "center",
                    gap: 5,
                    padding: "5px 9px",
                    borderRadius: 6,
                    fontSize: 10,
                    border: `1px solid ${done ? "#22c55e55" : running ? "#38bdf8" : "rgba(148,163,184,0.15)"}`,
                    background: done ? "rgba(34,197,94,0.08)" : running ? "rgba(56,189,248,0.1)" : "transparent",
                    color: done ? "#22c55e" : running ? "#38bdf8" : "#475569",
                    fontWeight: running || done ? 700 : 400,
                  }}
                >
                  {done ? (
                    <Check size={12} />
                  ) : running ? (
                    <Radio size={12} className="status-ping" />
                  ) : (
                    <span style={{ opacity: 0.6 }}>{i + 1}</span>
                  )}
                  {s.label}
                </div>
              </React.Fragment>
            );
          })}
        </div>
      )}
      {stages.length > 0 && stages[stages.length - 1]?.detail && (
        <div style={{ fontSize: 10, color: "var(--muted-foreground)", marginTop: -6 }}>
          {processing ? "⚙ " : "✓ "}{stages[stages.length - 1].detail}
        </div>
      )}

      {/* Dispatch receipt log (terminal style) */}
      {dispatches.length > 0 && (
        <div
          style={{
            background: "rgba(0,0,0,0.45)",
            border: "1px solid rgba(34,197,94,0.25)",
            borderRadius: 8,
            padding: "8px 10px",
            fontFamily: "var(--font-mono), monospace",
            fontSize: 10,
            lineHeight: 1.8,
            color: "#86efac",
            maxHeight: 110,
            overflowY: "auto",
          }}
        >
          {dispatches.map((d, i) => (
            <div key={i}>
              [{timeNow()}] {d.status === "UNKNOWN" ? "…" : "✓"}{" "}
              <span style={{ color: d.priority === "CRITICAL" ? "#fca5a5" : "#7dd3fc" }}>{d.channel}</span>{" "}
              → {d.recipient_role}{" "}
              <span style={{ color: "var(--muted-foreground)" }}>[{d.priority}]</span>{" "}
              <span style={{ color: d.status === "UNKNOWN" ? "#f59e0b" : "#22c55e" }}>{d.status}</span>
            </div>
          ))}
        </div>
      )}

      {/* Results */}
      {result && !processing && (
        <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <span style={{ fontSize: 11, fontWeight: 700, color: "#22c55e" }}>
              ✓ Triaged {result.total_boarders} boarders
            </span>
            {result.admitted !== undefined && (
              <span style={{ fontSize: 10, color: "var(--muted-foreground)" }}>· {result.admitted} assigned · see floor animation</span>
            )}
          </div>

          {result.matches.map((m, i) => {
            const sm = STATUS_META[m.allocation_status];
            return (
              <div key={i} style={{ display: "flex", gap: 8, alignItems: "center", padding: "6px 8px", borderRadius: 6, background: sm.bg, border: `1px solid ${sm.color}33` }}>
                <span style={{ fontSize: 9, color: "var(--muted-foreground)" }}>#{i + 1}</span>
                <span style={{ ...{ fontSize: 10, fontWeight: 700, color: "#fff" }, background: ESI_COLORS[m.esi_level], borderRadius: 4, padding: "1px 5px" } as React.CSSProperties}>
                  {m.esi_level}
                </span>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                    <span style={{ fontSize: 10, color: "var(--card-foreground)" }}>
                      {m.mrn} → <strong>{m.matched_bed_id ?? "NO BED"}</strong> · score {m.priority_score}
                    </span>
                  </div>
                  {/* Priority score bar */}
                  <div style={{ height: 3, borderRadius: 2, background: "rgba(148,163,184,0.15)", marginTop: 3, overflow: "hidden" }}>
                    <div
                      style={{
                        height: "100%",
                        width: `${Math.round((m.priority_score / maxScore) * 100)}%`,
                        background: ESI_COLORS[m.esi_level],
                      }}
                    />
                  </div>
                  <div style={{ fontSize: 9, color: "var(--muted-foreground)", marginTop: 2 }}>
                    {m.action_item}
                    {m.predicted_los_hours != null && (
                      <span style={{ color: "#a78bfa" }}>
                        {" "}· predicted LOS ~{m.predicted_los_hours}h
                        {m.los_top_factors
                          ? ` (${Object.keys(m.los_top_factors).slice(0, 2).join(", ")})`
                          : ""}
                      </span>
                    )}
                  </div>
                </div>
                <span style={{ fontSize: 8, fontWeight: 800, color: sm.color, textTransform: "uppercase" }}>{sm.label}</span>
              </div>
            );
          })}

          {result.notifications.length > 0 && (
            <div style={{ display: "flex", flexDirection: "column", gap: 3, marginTop: 4 }}>
              <span style={{ fontSize: 9, color: "var(--muted-foreground)", textTransform: "uppercase", letterSpacing: "0.05em" }}>Staff alerts dispatched</span>
              {result.notifications.map((n, i) => (
                <div key={i} style={{ display: "flex", gap: 6, alignItems: "center", fontSize: 10, color: "var(--muted-foreground)" }}>
                  <span>{CHANNEL_ICONS[n.channel] ?? "📨"}</span>
                  <strong style={{ color: n.priority === "CRITICAL" ? "#ef4444" : "var(--card-foreground)" }}>{n.recipient_role}</strong>
                  <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", flex: 1 }}>{n.message_title}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* Run history strip */}
      {history.length > 0 && !processing && (
        <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
          {history.map((r, i) => (
            <span key={i} className="ui-badge ui-badge-status-idle" style={{ fontSize: 9 }}>
              {r.time} · {r.boarders} boarders{r.admitted !== undefined ? ` · ${r.admitted} assigned` : ""} · {r.channels} ch
            </span>
          ))}
        </div>
      )}
    </div>
  );
}
