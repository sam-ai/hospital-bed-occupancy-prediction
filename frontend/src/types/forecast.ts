/** A single forecast point returned by /api/forecast/multi-horizon. */
export interface ForecastPointDoc {
  hospital_id: string;
  unit_id: string;
  horizon_type: "24H" | "7D" | "6M";
  time_step_index: number;
  timestamp: string;
  predicted_occupancy: number;
  predicted_occupied_beds: number;
  peak_occupancy: number;
  lower_bound: number;
  upper_bound: number;
  has_anomaly: boolean;
  anomaly_severity: string;
  anomaly_type: string | null;
  anomaly_explanation: string | null;
  /** Explainability: occupancy % contributed by each driver. */
  drivers?: Record<string, number> | null;
  /** Actual occupancy at this hour (present on past-date views). */
  actual_occupancy?: number;
}

/** Response envelope for /api/forecast/multi-horizon. */
export interface MultiHorizonForecastResponse {
  forecast_date: string;
  horizon_type: "24H" | "7D" | "6M";
  is_past?: boolean;
  total_points: number;
  error?: string;
  points: ForecastPointDoc[];
}

/** Distinct forecast dates available for back-dated exploration. */
export interface HistoryDatesResponse {
  dates: string[];
  error?: string;
}

export type HorizonType = "24H" | "7D" | "6M";

/* ═══════════════════════════════════════════════════════
   Strategy loop: what-if scenarios & accuracy
   ═══════════════════════════════════════════════════════ */
export interface ScenarioParams {
  bed_delta: number;
  elective_deferral_pct: number;
  er_surge_pct: number;
}

export interface ScenarioPoint {
  timestamp: string;
  predicted_occupancy: number;
  lower_bound: number;
  upper_bound: number;
}

export interface ScenarioSummary {
  peak_delta: number;
  mean_delta: number;
  mean_occupancy: number;
  peak_occupancy: number;
  total_beds_after_change: number;
  beds_freed_avg: number;
  max_hourly_shift: number;
  or_window_delta: number;
}

export interface ScenarioResponse {
  hospital_id: string;
  unit_id: string;
  params: ScenarioParams;
  summary: ScenarioSummary;
  baseline: ScenarioPoint[];
  scenario: ScenarioPoint[];
  error?: string;
}

export interface AccuracyRecord {
  day: string;
  horizon_type: string;
  mae: number;
  rmse?: number;
  mape?: number | null;
  bias: number;
  points_evaluated: number;
  evaluated_at: string;
}

export interface AccuracyResponse {
  horizon_type: string;
  aggregate: {
    days_evaluated: number;
    mae_avg: number;
    bias_avg: number;
    grade: "good" | "fair" | "poor";
  } | null;
  records: AccuracyRecord[];
  error?: string;
}

export interface BacktestResponse {
  hospital_id: string;
  unit_id: string;
  aggregate: {
    days_evaluated: number;
    mae_avg: number;
    mae_max: number;
    bias_avg: number;
  } | null;
  per_day: Array<{ day: string; mae: number; bias: number; points_evaluated: number }>;
  /** Model comparison (TimesFM vs baselines) sorted by MAE. */
  models?: Array<{ model: string; mae: number; rmse: number; bias: number }>;
}

/* ── Patient flow (admissions/discharges) forecast ── */
export interface PatientFlowDay {
  day: string;
  predicted_admissions: number;
  predicted_discharges: number;
  net_flow: number;
}

export interface PatientFlowResponse {
  hospital_id: string;
  unit_id: string;
  model: string;
  forecast: PatientFlowDay[];
  recent_history: Array<{ day: string; admissions: number; discharges: number }>;
}
