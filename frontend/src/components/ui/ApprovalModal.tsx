"use client";

import React from "react";
import { AlertTriangle, Check, X } from "lucide-react";

interface Recommendation {
  title: string;
  description: string;
  priority: string;
  rationale: string;
}

interface ApprovalModalProps {
  workflowId: string;
  recommendations: Recommendation[];
  onDecision: (workflowId: string, approved: boolean) => void;
}

/** Human-in-the-Loop approval overlay for policy-gated recommendations. */
export default function ApprovalModal({
  workflowId,
  recommendations,
  onDecision,
}: ApprovalModalProps) {
  return (
    <div
      className="fixed inset-0 flex items-center justify-center z-50 p-4"
      style={{
        background: "rgba(6, 12, 18, 0.85)",
        backdropFilter: "blur(8px)",
      }}
    >
      <div className="glass-panel fade-up p-0 max-w-lg w-full text-[color:var(--foreground)] overflow-hidden">
        {/* Header bar */}
        <div
          className="px-6 py-4 flex items-center gap-3"
          style={{
            background: "var(--danger-soft-bg)",
            borderBottom: "1px solid var(--danger-soft-border)",
          }}
        >
          <AlertTriangle
            size={20}
            style={{ color: "var(--destructive)" }}
          />
          <div>
            <h2
              className="text-base font-semibold"
              style={{
                fontFamily: "var(--font-display)",
                letterSpacing: "0.04em",
                textTransform: "uppercase",
                color: "var(--danger-soft-fg)",
              }}
            >
              Policy Approval Required
            </h2>
            <p
              className="type-meta mt-0.5"
              style={{ color: "var(--danger-soft-fg)", opacity: 0.8 }}
            >
              High-priority recommendations awaiting authorization
            </p>
          </div>
        </div>

        {/* Body */}
        <div className="px-6 py-5">
          <p
            className="type-body mb-4"
            style={{ color: "var(--muted-foreground)" }}
          >
            The AI Capacity Agent generated operational recommendations that
            require human authorization before execution:
          </p>

          {/* Recommendations List */}
          <div className="space-y-3 mb-5 max-h-60 overflow-y-auto">
            {recommendations.map((rec, idx) => (
              <div
                key={idx}
                className="sidebar-card p-4"
              >
                <div className="flex items-center gap-2 mb-2">
                  <span
                    className={`ui-badge text-[10px] ${
                      rec.priority === "critical"
                        ? "ui-badge-status-error"
                        : rec.priority === "high"
                        ? "ui-badge-status-approval"
                        : "ui-badge-status-idle"
                    }`}
                  >
                    {rec.priority.toUpperCase()}
                  </span>
                  <h3 className="type-section-title text-sm">{rec.title}</h3>
                </div>
                <p
                  className="type-body text-sm"
                  style={{ color: "var(--muted-foreground)" }}
                >
                  {rec.description}
                </p>
                {rec.rationale && (
                  <p
                    className="type-meta mt-2 italic"
                    style={{
                      color: "var(--muted-foreground)",
                      opacity: 0.7,
                    }}
                  >
                    Rationale: {rec.rationale}
                  </p>
                )}
              </div>
            ))}
          </div>

          {/* Workflow ID */}
          <p
            className="type-meta mb-5"
            style={{
              fontFamily: "var(--font-mono)",
              color: "var(--muted-foreground)",
              opacity: 0.6,
            }}
          >
            Workflow: {workflowId}
          </p>

          {/* Action Buttons */}
          <div className="flex justify-end gap-3">
            <button
              onClick={() => onDecision(workflowId, false)}
              className="sidebar-btn-ghost gap-2"
            >
              <X size={14} />
              Reject
            </button>
            <button
              onClick={() => onDecision(workflowId, true)}
              className="sidebar-btn-primary gap-2"
              style={{
                background: "var(--danger-soft-bg)",
                border: "1px solid var(--danger-soft-border)",
                color: "var(--danger-soft-fg)",
              }}
            >
              <Check size={14} />
              Authorize Action
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
