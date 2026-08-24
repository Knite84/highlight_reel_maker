export interface Project {
  id: number
  name: string
  slug: string
  media_path: string
  video_count: number
  photo_count: number
  scanned_at: string | null
  created_at: string
  files_total?: number
}

export interface MediaFile {
  id: number
  rel_path: string
  kind: 'video' | 'photo'
  size_bytes: number
  mtime: number
  duration_sec: number | null
  error: string | null
  analyzed_at?: string | null
  scene_count?: number
}

export interface SceneResult {
  scene_id: number
  rel_path: string
  kind: 'video' | 'photo'
  start_sec: number
  end_sec: number
  thumb_rel: string | null
  score: number
  tags: { tag: string; score: number }[]
}

export interface TagCount {
  tag: string
  count: number
}

export interface PlannedClip {
  rel_path: string
  start_sec: number
  end_sec: number
  transition_in: string
  transition_duration_sec?: number
  ken_burns?: { direction: string; intensity: number } | null
  reason?: string
}

export interface EditPlanData {
  schema_version: string
  prompt: string
  target_duration_sec: number
  seed: number
  clips: PlannedClip[]
  title?: { text: string; subtitle?: string | null } | null
  notes?: string | null
}

export interface ReelPlan {
  id: number
  prompt: string
  target_duration_sec: number
  status: 'planned' | 'rendering' | 'rendered' | 'failed'
  model_id: string | null
  render_path: string | null
  error: string | null
  created_at: string
}

export interface ReelPlanDetail extends ReelPlan {
  plan: EditPlanData
}

export interface Job {
  id: number
  project_id: number | null
  kind: string
  status: 'queued' | 'running' | 'done' | 'failed' | 'cancelled'
  progress: number
  done: number
  total: number | null
  message: string | null
  error: string | null
  created_at: string
  started_at: string | null
  finished_at: string | null
}

export interface SystemStatus {
  tools: {
    ffmpeg: string | null
    ffprobe: string | null
    exiftool: string | null
  }
  gpu: { ok: boolean; name: string | null; vram_gb: number | null }
  nvenc: { ok: boolean; encoders: string[] }
  unsloth: { ok: boolean; url: string; models: string[]; error: string | null }
  planner_model_id: string
  checked_at: number
}

export interface AppConfig {
  data_root: string
  projects_root: string
  unsloth_base_url: string
  unsloth_api_key_set: boolean
  planner_model_id: string
}
