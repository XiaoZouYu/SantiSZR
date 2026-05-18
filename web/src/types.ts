export type TaskStatus = "pending" | "running" | "succeeded" | "failed" | "cancelled" | string

export type TaskKind =
  | "content"
  | "rewrite"
  | "rewrite-text"
  | "tts"
  | "subtitle"
  | "avatar"
  | "workflow"
  | "postprocess"
  | "publish_materials"
  | string

export interface ErrorInfo {
  code?: string
  message?: string
  detail?: Record<string, unknown>
}

export interface WorkerEventPayload {
  event?: string
  status?: string
  task_id?: string
  id?: string
  taskId?: string
  task_kind?: TaskKind
  kind?: TaskKind
  taskKind?: TaskKind
  stage?: string
  progress?: number
  message?: string
  payload?: Record<string, unknown>
  error?: ErrorInfo | null
}

export interface TaskRecord {
  task_id: string
  task_kind: TaskKind
  status?: TaskStatus
  stage?: string
  progress?: number
  message?: string
  created_at?: string
  updated_at?: string
  logs?: string[]
  payload?: Record<string, unknown>
  result?: unknown
  error?: ErrorInfo | null
}

export interface AssetRecord {
  id?: string
  asset_id?: string
  kind?: string
  category?: string
  name?: string
  path: string
  url?: string
  created_at?: string
  updated_at?: string
  modified_at?: string
  duration_sec?: number | null
  mime_type?: string
  size_bytes?: number | null
  source?: string
  linked_text_path?: string | null
  linked_text_ref?: string | null
  text_preview?: string | null
  meta?: Record<string, unknown>
}

export interface DiagnosticItem {
  ok: boolean
  label: string
  detail?: string
  path?: string
}

export interface DiagnosticHealthItem {
  name?: string
  label?: string
  status?: string
  message?: string
  detail?: string
  path?: string
  executable?: string
  model_path?: string
  modelPath?: string
  ok?: boolean
  ready?: boolean
  available?: boolean
  [key: string]: unknown
}

export interface HealthResponse {
  ok?: boolean
  status?: string
  message?: string
  app?: string
  version?: string
  workspace?: string
  runtime_ok?: boolean
  llm?: {
    configured?: boolean
    provider?: string
    model?: string
    api_base?: string
    key_preview?: string
    message?: string
  }
  diagnostics?: DiagnosticHealthItem[] | Record<string, unknown>
  ffmpeg?: unknown
  voxcpm?: unknown
  tuilionnx?: unknown
  whisper?: unknown
  gpu?: unknown
  [key: string]: unknown
}

export interface BackendStateResponse {
  workspace?: string
  current_workspace?: string
  last_workspace?: string
  recent_workspaces?: string[]
  source_input?: string
  source_text?: string
  extracted_text?: string
  rewritten_text?: string
  tts_audio_path?: string
  subtitle_text?: string
  subtitle_srt?: string
  subtitle_path?: string
  avatar_video_path?: string
  cover_path?: string
  title?: string
  description?: string
  tags?: string[]
  tasks?: TaskRecord[]
  current_task?: TaskRecord | null
  [key: string]: unknown
}

export type AssetBuckets = {
  audio: AssetRecord[]
  video: AssetRecord[]
  image: AssetRecord[]
  subtitle: AssetRecord[]
  other: AssetRecord[]
}

export interface DashboardTaskState {
  tasks: TaskRecord[]
  currentTaskId: string
  logsByTask: Record<string, string[]>
}
