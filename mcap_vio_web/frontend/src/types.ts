export interface Device {
  name: string
  valid_sequence_count: number
}

export interface Sequence {
  name: string
  complete: boolean
  duration_sec: number | null
  platform_name: string | null
  info_warning: string | null
  sensor_mcap_counts: Record<string, number>
}

export interface ConfigOption {
  id: string
  label: string
}

export interface TaskGroupCreated {
  group_id: string
  task_ids: string[]
}

export type TaskStatus =
  | 'queued'
  | 'extracting'
  | 'vio'
  | 'succeeded'
  | 'failed'
  | 'interrupted'

export interface Task {
  id: string
  group_id: string
  sequence: string
  status: TaskStatus
  created_at: string
  started_at: string | null
  finished_at: string | null
  exit_code: number | null
  runner_status: string | null
  error_summary: string | null
  device: string
  config_rel: string
  cam_workers: number
  keep_images: number
}

export interface Trajectory {
  timestamp_start: number
  timestamp_end: number
  duration_sec: number
  point_count: number
  points: [number, number, number][]
  bbox: { min: [number, number, number]; max: [number, number, number] }
  path_length_m: number
  displacement_m: number
}

export interface Artifact {
  path: string
  name: string
  size: number
}

export interface RuntimeStatus {
  max_concurrency: number
  running: number
  queued: number
  accepting_tasks: boolean
}
