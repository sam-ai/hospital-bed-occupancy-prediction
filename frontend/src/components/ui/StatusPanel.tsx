"use client";

import React from "react";
import { BedState, Patient3D } from "@/types/hospital";
import { Activity, Wifi, WifiOff, Users } from "lucide-react";

interface StatusPanelProps {
  beds: BedState[];
  patients: Patient3D[];
  isConnected: boolean;
  lastEvent: string | null;
}

/** HUD overlay showing real-time hospital statistics. */
export default function StatusPanel({
  beds,
  patients,
  isConnected,
  lastEvent,
}: StatusPanelProps) {
  const totalBeds = beds.length;
  const occupiedBeds = beds.filter((b) => b.isOccupied).length;
  const occupancyRate = totalBeds > 0 ? (occupiedBeds / totalBeds) * 100 : 0;

  const getOccupancyBadge = () => {
    if (occupancyRate >= 90) return "ui-badge-status-error";
    if (occupancyRate >= 75) return "ui-badge-status-approval";
    return "ui-badge-status-running";
  };

  return (
    <div className="absolute top-4 right-4 z-10 ui-panel fade-up-delay p-5 w-72 text-[color:var(--foreground)]">
      {/* Header */}
      <div className="flex items-center justify-between mb-4">
        <h2 className="type-section-title">Floor Status</h2>
        <span className="ui-badge ui-badge-status-idle text-[10px]">
          ICU-EAST
        </span>
      </div>

      {/* Connection indicator */}
      <div className="flex items-center gap-2.5 mb-4">
        <div
          className={`ui-dot ${
            isConnected ? "ui-dot-connected status-ping" : "ui-dot-disconnected"
          }`}
        />
        <span className="type-meta text-[color:var(--muted-foreground)]">
          {isConnected ? "Connected to Agent" : "Disconnected"}
        </span>
        {isConnected ? (
          <Wifi size={13} className="ml-auto text-[color:var(--muted-foreground)]" />
        ) : (
          <WifiOff size={13} className="ml-auto text-[color:var(--muted-foreground)]" />
        )}
      </div>

      {/* Occupancy bar */}
      <div className="mb-4">
        <div className="flex justify-between items-center mb-1.5">
          <span className="type-meta text-[color:var(--muted-foreground)]">
            Bed Occupancy
          </span>
          <span className={`ui-badge ${getOccupancyBadge()} text-[10px]`}>
            {occupiedBeds}/{totalBeds} ({occupancyRate.toFixed(0)}%)
          </span>
        </div>
        <div
          className="w-full rounded-full h-1.5"
          style={{ background: "var(--surface-3)" }}
        >
          <div
            className="h-1.5 rounded-full transition-all duration-500"
            style={{
              width: `${occupancyRate}%`,
              background:
                occupancyRate >= 90
                  ? "var(--destructive)"
                  : occupancyRate >= 75
                  ? "var(--status-connecting-fg)"
                  : "var(--status-running-fg)",
            }}
          />
        </div>
      </div>

      {/* Patient count */}
      <div className="flex items-center justify-between mb-3">
        <span className="type-meta text-[color:var(--muted-foreground)]">
          Active Patients
        </span>
        <div className="flex items-center gap-1.5">
          <Users size={13} className="text-[color:var(--muted-foreground)]" />
          <span className="ui-badge ui-badge-status-connected text-[10px]">
            {patients.length}
          </span>
        </div>
      </div>

      {/* Occupancy grid mini */}
      <div className="flex gap-1 mb-4">
        {beds.map((bed) => (
          <div
            key={bed.id}
            className="flex-1 h-2 rounded-sm transition-colors duration-300"
            style={{
              background: bed.isOccupied
                ? "var(--destructive)"
                : "var(--status-running-bg)",
              border: `1px solid ${
                bed.isOccupied
                  ? "var(--danger-soft-border)"
                  : "var(--status-running-border)"
              }`,
            }}
            title={bed.id}
          />
        ))}
      </div>

      {/* Last event */}
      {lastEvent && (
        <div
          className="pt-3"
          style={{ borderTop: "1px solid var(--border)" }}
        >
          <p className="type-meta text-[color:var(--muted-foreground)] mb-1 flex items-center gap-1.5">
            <Activity size={11} />
            Last Event
          </p>
          <p
            className="type-meta truncate"
            style={{
              fontFamily: "var(--font-mono)",
              color: "var(--muted-foreground)",
            }}
          >
            {lastEvent}
          </p>
        </div>
      )}
    </div>
  );
}
