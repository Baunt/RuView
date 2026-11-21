export type Block = 'seated' | 'supine' | 'motion' | 'empty' | 'unmarked';
export type Connection = {url: string; source_address: string | null; node_id: number; token_set: boolean};
export type Calibration = {status?: string; active?: boolean; frame_count?: number; min_frames?: number; elapsed_s?: number; min_duration_s?: number};
export type Observation = {
  received_at: number; status: string; hr_bpm: number | null; br_rpm: number | null;
  quality_score?: number; reasons: string[]; abstention_reason?: string;
  node?: {node_id: number; rssi_dbm: number; csi_status: string};
  responses?: {health?: {source?: string; tick?: number; processing?: {state?: string; last_update_age_ms?: number; udp?: {datagrams?: number; dropped_by_allowlist?: number}}}; calibration?: Calibration; nodes?: {error?: string}; vitals?: {authority?: string; error?: string}};
};
export type Station = {connection: Connection; latest: Observation | null; active_session_id: string | null; station_version: string};
export type Session = {id: string; name: string; status: string; started_at: number; ended_at: number | null; initial_block: Block; alignment: {offset_s: number; uncertainty_s: number | null}; raw_capture?: {raw_frames: number; missing_sequences: number; utc_overlap_s: number; clock_sync: string}; recovery_note?: string};
export type Point = {timestamp: number; reference_hr: number | null; ruview_hr: number | null; eligible: boolean; block: Block; reason: string};
export type Report = {status: string; counts: Record<string, number>; coverage: number | null; total_coverage: number | null; mae_bpm: number | null; p95_abs_bpm: number | null; bias_bpm: number | null; limitations: string[]; timeline: Point[]; markers: {timestamp: number; kind: string; note: string}[]; reference_quality: Record<string, number | null> | null};
