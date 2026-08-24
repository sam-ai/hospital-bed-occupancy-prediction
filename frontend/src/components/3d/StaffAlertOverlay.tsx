"use client";

import React, { useEffect, useState } from "react";
import { Html } from "@react-three/drei";
import { StaffNotification } from "@/types/hospital";

interface StaffAlertOverlayProps {
  /** Latest dispatched staff alerts (empty = hidden). */
  alerts: StaffNotification[];
  /** Changing token triggers display of a new alert batch. */
  token?: number;
  /** Seconds before the overlay auto-dismisses. */
  durationSeconds?: number;
}

const PRIORITY_COLORS: Record<string, string> = {
  CRITICAL: "#ef4444",
  HIGH: "#f59e0b",
  MEDIUM: "#38bdf8",
  LOW: "#22c55e",
};

export default function StaffAlertOverlay({
  alerts,
  token = 0,
  durationSeconds = 15,
}: StaffAlertOverlayProps) {
  const [visible, setVisible] = useState(false);

  useEffect(() => {
    if (!alerts.length) return;
    setVisible(true);
    const timer = setTimeout(() => setVisible(false), durationSeconds * 1000);
    return () => clearTimeout(timer);
  }, [alerts, token, durationSeconds]);

  if (!visible || !alerts.length) return null;

  return (
    <Html transform position={[15.5, 4.6, -11]} scale={0.55} zIndexRange={[40, 0]} style={{ pointerEvents: "none" }}>
      <div
        style={{
          background: "rgba(11,17,24,0.85)",
          border: "1px solid rgba(56,189,248,0.35)",
          borderRadius: 8,
          padding: "8px 12px",
          color: "#f0f6ff",
          fontFamily: "var(--font-mono), monospace",
          userSelect: "none",
          width: 300,
        }}
      >
        <div style={{ fontSize: 11, color: "#38bdf8", fontWeight: 700, marginBottom: 4 }}>
          STAFF ALERTS DISPATCHED ({alerts.length})
        </div>
        {alerts.map((n, i) => (
          <div key={i} style={{ fontSize: 10.5, lineHeight: 1.7, marginBottom: 2 }}>
            <span style={{ color: PRIORITY_COLORS[n.priority] ?? "#38bdf8", fontWeight: n.priority === "CRITICAL" ? 800 : 400 }}>
              [{n.priority}]
            </span>{" "}
            {n.message_title} → <span style={{ color: "#94a3b8" }}>{n.recipient_role}</span>
            <div style={{ fontSize: 9.5, color: "#64748b", whiteSpace: "normal" }}>{n.message_body}</div>
          </div>
        ))}
      </div>
    </Html>
  );
}
