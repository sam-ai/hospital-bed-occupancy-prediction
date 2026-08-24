/** 3D world coordinate on the hospital floor. */
export interface Position3D {
  x: number;
  y: number;
  z: number;
}

/** State of a single hospital bed. */
export interface BedState {
  id: string;
  position: Position3D;
  isOccupied: boolean;
  patientId?: string;
  /** Bed is undergoing EVS cleaning (yellow glow). */
  isBeingCleaned?: boolean;
}

/** A patient entity in the 3D world. */
export interface Patient3D {
  id: string;
  position: Position3D;
  targetPosition?: Position3D;
  status: "ARRIVED" | "BED_ASSIGNED" | "DISCHARGED" | "WALKING" | "ESCORTED";
  bedId?: string;
}

/** A staff member entity in the 3D world. */
export interface Staff3D {
  id: string;
  position: Position3D;
  targetPosition?: Position3D;
  status: "IDLE" | "DISPATCHED" | "TREATING" | "WALKING";
  role?: "nurse" | "doctor";
}

/** Simulation event received from the backend WebSocket. */
export interface Simulation3DEvent {
  event_id: string;
  event_type:
    | "PATIENT_ARRIVED"
    | "PATIENT_ESCORTED"
    | "PATIENT_WALKING_OUT"
    | "BED_ASSIGNED"
    | "STAFF_DISPATCHED"
    | "EVS_CLEANING_STARTED"
    | "EVS_CLEANING_COMPLETE"
    | "PATIENT_DISCHARGED";
  patient_id?: string;
  bed_id?: string;
  staff_id?: string;
  position: Position3D;
  target_position?: Position3D;
  timestamp: string;
}

/* ═══════════════════════════════════════════════════════
   Fast-Track Admission (triage) types
   ═══════════════════════════════════════════════════════ */
export interface WaitingPatientDTO {
  patient_id: string;
  mrn: string;
  esi_level: 1 | 2 | 3 | 4 | 5;
  news2_score: number;
  wait_time_minutes: number;
  required_bed_type: "ICU" | "MED_SURG" | "STEP_DOWN" | "TELEMETRY" | "ISOLATION";
  chief_complaint: string;
  isolation_required: boolean;
}

export interface FastTrackMatch {
  patient_id: string;
  mrn: string;
  esi_level: number;
  priority_score: number;
  matched_bed_id: string | null;
  allocation_status: "READY_TO_ASSIGN" | "AWAITING_EVS_CLEANING" | "NEEDS_EXPEDITED_DISCHARGE";
  action_item: string;
  predicted_los_hours?: number | null;
  los_top_factors?: Record<string, number> | null;
}

export interface StaffNotification {
  recipient_role: string;
  channel: string;
  priority: string;
  message_title: string;
  message_body: string;
}

/* ═══════════════════════════════════════════════════════
   Agent activity streaming types
   ═══════════════════════════════════════════════════════ */
export type AgentStageName = "TRIAGE_MATCHING" | "ROLE_NOTIFICATIONS" | "CHANNEL_DISPATCH";

export interface AgentStage {
  run_id: string;
  stage: AgentStageName;
  status: "RUNNING" | "DONE";
  detail: string;
}

export interface AgentDispatch {
  run_id: string;
  channel: string;
  recipient_role: string;
  priority: string;
  message_title?: string;
  status: string;
}

/** One completed fast-track run (session history). */
export interface FastTrackRunSummary {
  time: string;
  boarders: number;
  admitted?: number;
  channels: number;
}

/** Playback progress metadata attached to simulation events / messages. */
export interface PlaybackStatus {
  mode: "STEP" | "TIMELINE";
  horizon_type: "24H" | "7D" | "6M";
  step_index: number;
  occupied_beds: number;
}

/** WebSocket message envelope from the backend. */
export interface WSMessage {
  type: string;
  workflow_id?: string;
  event?: Simulation3DEvent;
  data?: Record<string, unknown>;
  recommendations?: Array<{
    title: string;
    description: string;
    priority: string;
    rationale: string;
  }>;
  message?: string;
  // Forecast playback metadata
  mode?: "STEP" | "TIMELINE";
  horizon_type?: "24H" | "7D" | "6M";
  step_index?: number;
  occupied_beds?: number;
  total_steps?: number;
  playback_status?: PlaybackStatus;
  // Fast-track admission results
  matches?: FastTrackMatch[];
  notifications?: StaffNotification[];
  dispatch_results?: Array<{ status: string; channel: string; recipient: string }>;
  total_boarders?: number;
  admitted?: number;
  run_id?: string;
  completed_at?: string;
  // Agent activity streaming
  stage?: AgentStageName;
  status?: "RUNNING" | "DONE";
  detail?: string;
  priority?: string;
  channel?: string;
  recipient_role?: string;
  message_title?: string;
  scenario?: string;
  records_ingested?: number;
}
