"use client";

import React, { useMemo, useState } from "react";
import { BedDouble, ChevronDown, ChevronUp } from "lucide-react";
import type {
  BedState,
  FastTrackMatch,
  Patient3D,
} from "@/types/hospital";

const ESI_COLORS: Record<number, string> = {
  1: "#ef4444",
  2: "#f97316",
  3: "#f59e0b",
  4: "#22c55e",
  5: "#0ea5e9",
};

interface BedRow {
  key: string;
  bedId: string | null;
  patientId?: string;
  mrn?: string;
  esiLevel?: number;
  predictedLosHours?: number | null;
  status: "OCCUPIED" | "FREE" | "EVS_CLEANING" | "RESERVED" | "IN_QUEUE";
}

interface BedAssignmentsPanelProps {
  beds: BedState[];
  patients: Patient3D[];
  fastTrackMatches?: FastTrackMatch[];
  /** Fires when a row's bed should be focused in the 3D scene. */
  onFocusBed?: (bedId: string) => void;
}

export default function BedAssignmentsPanel({
  beds,
  patients,
  fastTrackMatches = [],
  onFocusBed,
}: BedAssignmentsPanelProps) {
  const [expanded, setExpanded] = useState(false);

  const { rows, queued } = useMemo(() => {
    const matchByBed = new Map<string, FastTrackMatch>();
    for (const m of fastTrackMatches) {
      if (m.matched_bed_id) matchByBed.set(m.matched_bed_id, m);
    }

    const rows: BedRow[] = beds.map((b) => {
      const match = matchByBed.get(b.id);
      if (b.isOccupied || b.patientId) {
        return {
          key: b.id,
          bedId: b.id,
          patientId: b.patientId,
          mrn: match?.mrn,
          esiLevel: match?.esi_level,
          predictedLosHours: match?.predicted_los_hours ?? null,
          status: "OCCUPIED" as const,
        };
      }
      if (b.isBeingCleaned) {
        return { key: b.id, bedId: b.id, status: "EVS_CLEANING" as const };
      }
      if (match) {
        return {
          key: b.id,
          bedId: b.id,
          mrn: match.mrn,
          esiLevel: match.esi_level,
          predictedLosHours: match.predicted_los_hours ?? null,
          status: "RESERVED" as const,
        };
      }
      return { key: b.id, bedId: b.id, status: "FREE" as const };
    });

    // Sort: occupied → reserved/cleaning → free
    const order: Record<BedRow["status"], number> = {
      OCCUPIED: 0,
      RESERVED: 1,
      EVS_CLEANING: 2,
      FREE: 3,
      IN_QUEUE: 4,
    };
    rows.sort((a, b) => a.bedId!.localeCompare(b.bedId!));
    rows.sort((a, b) => order[a.status] - order[b.status]);

    // Patients without any bed yet
    const queued = patients
      .filter((p) => !p.bedId && p.status !== "DISCHARGED")
      .map((p) => ({ id: p.id, status: p.status }));

    return { rows, queued };
  }, [beds, patients, fastTrackMatches]);

  const occupiedCount = beds.filter((b) => b.isOccupied).length;
  const statusMeta: Record<
    BedRow["status"],
    { label: string; color: string; bg?: string }
  > = {
    OCCUPIED: { label: "OCCUPIED", color: "#22c55e", bg: "rgba(34,197,94,0.1)" },
    RESERVED: { label: "RESERVED", color: "#38bdf8", bg: "rgba(56,189,248,0.1)" },
    EVS_CLEANING: { label: "EVS CLEAN", color: "#f59e0b", bg: "rgba(245,158,11,0.12)" },
    FREE: { label: "FREE", color: "#64748b" },
    IN_QUEUE: { label: "IN QUEUE", color: "#60a5fa", bg: "rgba(96,165,250,0.1)" },
  };

  if (!expanded) {
    /* ── Collapsed pill ── */
    return (
      <button
        onClick={() => setExpanded(true)}
        className="glass-panel"
        style={{
          position: "absolute",
          left: 16,
          bottom: 72,
          zIndex: 10,
          padding: "8px 14px",
          display: "flex",
          alignItems: "center",
          gap: 8,
          color: "var(--card-foreground)",
          cursor: "pointer",
          transition: "all .25s ease",
        }}
        title="Show bed assignments"
      >
        <BedDouble size={14} style={{ color: "var(--primary)" }} />
        <span style={{ fontSize: 11, fontWeight: 600 }}>
          {occupiedCount}/{beds.length} beds
        </span>
        {queued.length > 0 && (
          <span style={{ fontSize: 10, color: "#f59e0b" }}>· {queued.length} queued</span>
        )}
        <ChevronUp size={13} style={{ color: "var(--muted-foreground)" }} />
      </button>
    );
  }

  /* ── Expanded table ── */
  return (
    <div
      className="glass-panel"
      style={{
        position: "absolute",
        left: 16,
        bottom: 72,
        zIndex: 15,
        width: 480,
        maxWidth: "calc(100vw - 32px)",
        maxHeight: "48vh",
        overflowY: "auto",
        padding: 14,
        color: "var(--card-foreground)",
        display: "flex",
        flexDirection: "column",
        gap: 8,
        transition: "all .25s ease",
      }}
    >
      {/* Header */}
      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <BedDouble size={14} style={{ color: "var(--primary)" }} />
        <span style={{ fontSize: 12, fontWeight: 600, letterSpacing: "0.03em" }}>
          Bed Assignments
        </span>
        <span className="ui-badge ui-badge-status-idle" style={{ fontSize: 9 }}>
          {occupiedCount}/{beds.length} occupied
        </span>
        <div style={{ flex: 1 }} />
        <button
          onClick={() => setExpanded(false)}
          className="sidebar-btn-ghost"
          title="Collapse"
          style={{ padding: "4px 6px" }}
        >
          <ChevronDown size={13} />
        </button>
      </div>

      {/* Table */}
      <table style={{ width: "100%", fontSize: 10, borderCollapse: "collapse" }}>
        <thead>
          <tr style={{ color: "var(--muted-foreground)" }}>
            <th style={{ textAlign: "left", paddingBottom: 4 }}>BED</th>
            <th style={{ textAlign: "left", paddingBottom: 4 }}>STATUS</th>
            <th style={{ textAlign: "left", paddingBottom: 4 }}>PATIENT</th>
            <th style={{ textAlign: "left", paddingBottom: 4 }}>MRN</th>
            <th style={{ textAlign: "center", paddingBottom: 4 }}>ESI</th>
            <th style={{ textAlign: "right", paddingBottom: 4 }}>LOS</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => {
            const sm = statusMeta[r.status];
            return (
              <tr
                key={r.key}
                onClick={() => r.bedId && onFocusBed?.(r.bedId)}
                style={{
                  borderTop: "1px solid var(--border)",
                  background: sm.bg,
                  cursor: r.bedId ? "pointer" : "default",
                }}
                title={r.bedId ? `Click to focus ${r.bedId} in 3D view` : undefined}
              >
                <td style={{ padding: "4px 0", fontWeight: 700 }}>{r.bedId}</td>
                <td>
                  <span style={{ fontSize: 8, fontWeight: 800, color: sm.color }}>
                    {sm.label}
                  </span>
                </td>
                <td>{r.patientId ?? "—"}</td>
                <td>{r.mrn ?? "—"}</td>
                <td style={{ textAlign: "center" }}>
                  {r.esiLevel ? (
                    <span
                      style={{
                        fontWeight: 800,
                        color: "#fff",
                        background: ESI_COLORS[r.esiLevel],
                        borderRadius: 3,
                        padding: "0 4px",
                      }}
                    >
                      {r.esiLevel}
                    </span>
                  ) : (
                    "—"
                  )}
                </td>
                <td style={{ textAlign: "right", fontFamily: "var(--font-mono), monospace" }}>
                  {r.predictedLosHours != null ? `~${r.predictedLosHours}h` : "—"}
                </td>
              </tr>
            );
          })}

          {/* Waiting queue */}
          {queued.length > 0 && (
            <>
              <tr>
                <td colSpan={6} style={{ paddingTop: 8, paddingBottom: 2 }}>
                  <span style={{ fontSize: 8, color: "#f59e0b", textTransform: "uppercase", letterSpacing: "0.05em" }}>
                    ▼ Waiting in queue ({queued.length})
                  </span>
                </td>
              </tr>
              {queued.map((q) => (
                <tr key={q.id} style={{ borderTop: "1px solid var(--border)", background: statusMeta.IN_QUEUE.bg }}>
                  <td style={{ padding: "4px 0" }}>—</td>
                  <td>
                    <span style={{ fontSize: 8, fontWeight: 800, color: statusMeta.IN_QUEUE.color }}>
                      {statusMeta[q.status as BedRow["status"]]?.label ?? "WAITING"}
                    </span>
                  </td>
                  <td>{q.id}</td>
                  <td>—</td>
                  <td style={{ textAlign: "center" }}>—</td>
                  <td style={{ textAlign: "right" }}>—</td>
                </tr>
              ))}
            </>
          )}
        </tbody>
      </table>
    </div>
  );
}
