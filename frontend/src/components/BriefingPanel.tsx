import React from "react";
import { X, Sparkles, AlertTriangle, ShieldCheck, RefreshCw } from "lucide-react";

export interface BriefingInput {
  hospital_id: string;
  unit_id: string;
  peak_predicted_occupancy: number | null;
  total_beds: number;
  anomaly_detected: boolean;
  anomaly_explanation: string | null;
  findings: string[];
  recommendations: string[];
  policy_decision: string | null;
}

export interface BriefingOutput {
  briefing: string;
  riskLevel: "LOW" | "MEDIUM" | "HIGH" | "CRITICAL";
  requiresAttention: boolean;
  requestId: string;
  timestamp: string;
}

export interface BriefingResponse {
  status: "SUCCESS" | "ERROR";
  request_id?: string;
  hospital_id?: string;
  unit_id?: string;
  aava_input?: BriefingInput;
  briefing?: BriefingOutput;
  error?: string;
}

interface BriefingPanelProps {
  wardLabel: string;
  loading: boolean;
  data: BriefingResponse | null;
  onClose: () => void;
  onRefresh: () => void;
}

const RISK_META: Record<string, { color: string; bg: string }> = {
  LOW: { color: "#22c55e", bg: "rgba(34,197,94,0.14)" },
  MEDIUM: { color: "#f59e0b", bg: "rgba(245,158,11,0.14)" },
  HIGH: { color: "#f97316", bg: "rgba(249,115,22,0.14)" },
  CRITICAL: { color: "#ef4444", bg: "rgba(239,68,68,0.16)" },
};

export default function BriefingPanel({
  wardLabel,
  loading,
  data,
  onClose,
  onRefresh,
}: BriefingPanelProps) {
  const briefing = data?.briefing;
  const input = data?.aava_input;
  const risk = briefing ? RISK_META[briefing.riskLevel] ?? RISK_META.MEDIUM : RISK_META.MEDIUM;

  return (
    <div
      style={{
        position: "absolute",
        top: 16,
        right: 16,
        zIndex: 40,
        width: 380,
        maxHeight: "calc(100vh - 32px)",
        overflowY: "auto",
        background: "var(--card, #0e1621)",
        border: "1px solid rgba(148,163,184,0.25)",
        borderRadius: 12,
        boxShadow: "0 10px 40px rgba(0,0,0,0.45)",
        color: "var(--card-foreground, #e2e8f0)",
        padding: 16,
      }}
    >
      {/* Header */}
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 10 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <Sparkles size={16} style={{ color: "#a78bfa" }} />
          <span style={{ fontSize: 13, fontWeight: 600 }}>AI Capacity Briefing</span>
          <span
            className="ui-badge"
            style={{ fontSize: 9, background: "rgba(167,139,250,0.15)", color: "#a78bfa" }}
          >
            AAVA
          </span>
        </div>
        <div style={{ display: "flex", gap: 6 }}>
          <button
            onClick={onRefresh}
            disabled={loading}
            title="Regenerate"
            className="sidebar-btn-ghost"
            style={{ padding: "3px 6px" }}
          >
            <RefreshCw size={13} className={loading ? "spin" : undefined} />
          </button>
          <button onClick={onClose} title="Close" className="sidebar-btn-ghost" style={{ padding: "3px 6px" }}>
            <X size={13} />
          </button>
        </div>
      </div>

      <div style={{ fontSize: 10, color: "var(--muted-foreground, #94a3b8)", marginBottom: 12 }}>
        Ward: <strong>{wardLabel}</strong>
      </div>

      {/* Loading */}
      {loading && (
        <div style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 12, color: "#38bdf8", padding: "16px 0" }}>
          <RefreshCw size={14} className="spin" /> Running pipeline &amp; querying AAVA…
        </div>
      )}

      {/* Error */}
      {!loading && data?.status === "ERROR" && (
        <div
          style={{
            display: "flex",
            gap: 8,
            fontSize: 12,
            color: "#fca5a5",
            background: "rgba(239,68,68,0.1)",
            border: "1px solid rgba(239,68,68,0.3)",
            borderRadius: 8,
            padding: 10,
          }}
        >
          <AlertTriangle size={14} style={{ flexShrink: 0, marginTop: 1 }} />
          <span>{data.error || "Briefing generation failed."}</span>
        </div>
      )}

      {/* Success */}
      {!loading && briefing && (
        <>
          <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 12 }}>
            <span
              style={{
                fontSize: 11,
                fontWeight: 700,
                letterSpacing: "0.05em",
                color: risk.color,
                background: risk.bg,
                border: `1px solid ${risk.color}55`,
                borderRadius: 6,
                padding: "3px 10px",
              }}
            >
              {briefing.riskLevel} RISK
            </span>
            {briefing.requiresAttention ? (
              <span style={{ display: "flex", alignItems: "center", gap: 4, fontSize: 11, color: "#f97316" }}>
                <AlertTriangle size={13} /> Attention required
              </span>
            ) : (
              <span style={{ display: "flex", alignItems: "center", gap: 4, fontSize: 11, color: "#22c55e" }}>
                <ShieldCheck size={13} /> Nominal
              </span>
            )}
          </div>

          <p style={{ fontSize: 12.5, lineHeight: 1.55, margin: "0 0 14px" }}>{briefing.briefing}</p>

          {/* Input snapshot */}
          {input && (
            <div
              style={{
                fontSize: 11,
                background: "rgba(148,163,184,0.06)",
                border: "1px solid rgba(148,163,184,0.15)",
                borderRadius: 8,
                padding: 10,
              }}
            >
              <div style={{ fontSize: 9, textTransform: "uppercase", letterSpacing: "0.05em", color: "var(--muted-foreground, #94a3b8)", marginBottom: 6 }}>
                Signals sent to AAVA
              </div>
              <Row
                label="Peak occupancy"
                value={
                  input.peak_predicted_occupancy != null
                    ? `${(input.peak_predicted_occupancy * 100).toFixed(1)}%`
                    : "—"
                }
              />
              <Row label="Total beds" value={String(input.total_beds)} />
              <Row label="Anomaly" value={input.anomaly_detected ? "Detected" : "None"} />
              <Row label="Policy decision" value={input.policy_decision ?? "—"} />
              {input.findings.length > 0 && (
                <div style={{ marginTop: 6 }}>
                  <div style={{ fontSize: 9, color: "var(--muted-foreground, #94a3b8)", marginBottom: 3 }}>Findings</div>
                  <ul style={{ margin: 0, paddingLeft: 16 }}>
                    {input.findings.map((f, i) => (
                      <li key={i} style={{ marginBottom: 2 }}>{f}</li>
                    ))}
                  </ul>
                </div>
              )}
            </div>
          )}

          <div style={{ fontSize: 9, color: "var(--muted-foreground, #94a3b8)", marginTop: 10 }}>
            AAVA request {briefing.requestId}
          </div>
        </>
      )}
    </div>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div style={{ display: "flex", justifyContent: "space-between", padding: "2px 0" }}>
      <span style={{ color: "var(--muted-foreground, #94a3b8)" }}>{label}</span>
      <span style={{ fontWeight: 600 }}>{value}</span>
    </div>
  );
}
