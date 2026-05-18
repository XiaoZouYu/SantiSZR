import type { BackendStateResponse, HealthResponse, TaskRecord, TaskKind } from "@/types"

function defaultApiBase() {
  if (typeof window !== "undefined" && window.location?.hostname) {
    return `${window.location.protocol}//${window.location.hostname}:7860`
  }
  return "http://127.0.0.1:7860"
}

const DEFAULT_API_BASE = defaultApiBase()

export function normalizeApiBase(base: string) {
  const trimmed = base.trim()
  return trimmed.replace(/\/+$/, "") || DEFAULT_API_BASE
}

export const API_BASE = normalizeApiBase(
  typeof import.meta !== "undefined" && import.meta.env?.VITE_API_BASE ? String(import.meta.env.VITE_API_BASE) : DEFAULT_API_BASE,
)

export class ApiError extends Error {
  status: number
  payload: unknown

  constructor(message: string, status: number, payload: unknown) {
    super(message)
    this.name = "ApiError"
    this.status = status
    this.payload = payload
  }
}

function joinUrl(baseUrl: string, path: string) {
  return new URL(path.startsWith("/") ? path : `/${path}`, baseUrl).toString()
}

async function parseResponse<T>(response: Response): Promise<T> {
  const contentType = response.headers.get("content-type") ?? ""
  const payload =
    contentType.includes("application/json")
      ? await response.json().catch(() => null)
      : await response.text().catch(() => "")

  if (!response.ok) {
    const message =
      typeof payload === "string"
        ? payload || response.statusText
        : (payload as Record<string, unknown> | null)?.message?.toString() || response.statusText
    throw new ApiError(message, response.status, payload)
  }

  return payload as T
}

export class ApiClient {
  baseUrl = API_BASE

  setBaseUrl(baseUrl: string) {
    this.baseUrl = normalizeApiBase(baseUrl)
  }

  private async request<T>(path: string, init: RequestInit = {}) {
    const headers = new Headers(init.headers)
    const hasBody = init.body !== undefined && !(init.body instanceof FormData)
    if (hasBody && !headers.has("Content-Type")) {
      headers.set("Content-Type", "application/json")
    }
    headers.set("Accept", "application/json")

    const response = await fetch(joinUrl(this.baseUrl, path), {
      ...init,
      headers,
    })
    return parseResponse<T>(response)
  }

  health() {
    return this.request<HealthResponse>("/api/health")
  }

  state() {
    return this.request<BackendStateResponse>("/api/state")
  }

  recentWorkspaces() {
    return this.request<string[] | { items?: string[]; recent_workspaces?: string[] }>("/api/workspaces/recent")
  }

  selectWorkspace(workspacePath: string) {
    return this.request<BackendStateResponse>("/api/workspaces/select", {
      method: "POST",
      body: JSON.stringify({ path: workspacePath }),
    })
  }

  assets() {
    return this.request<unknown>("/api/assets")
  }

  deleteAsset(path: string) {
    const query = new URLSearchParams({ path })
    return this.request<unknown>(`/api/assets?${query.toString()}`, {
      method: "DELETE",
    })
  }

  uploadAsset(file: File, kind: string, workspace?: string) {
    const formData = new FormData()
    formData.append("file", file)
    formData.append("asset_type", kind)
    formData.append("kind", kind)
    if (workspace) formData.append("workspace", workspace)
    return this.request<unknown>("/api/assets/upload", {
      method: "POST",
      body: formData,
    })
  }

  referenceAudioTranscript(referenceAudioPath: string, workspace?: string) {
    return this.request<unknown>("/api/reference-audio/transcript", {
      method: "POST",
      body: JSON.stringify({
        reference_audio_path: referenceAudioPath,
        workspace,
      }),
    })
  }

  createTask(kind: TaskKind, payload: Record<string, unknown>) {
    return this.request<unknown>(`/api/tasks/${encodeURIComponent(kind)}`, {
      method: "POST",
      body: JSON.stringify({ payload }),
    })
  }

  preparePublishMaterials(payload: Record<string, unknown>) {
    return this.request<unknown>("/api/publish/prepare", {
      method: "POST",
      body: JSON.stringify(payload),
    })
  }

  listTasks() {
    return this.request<unknown>("/api/tasks")
  }

  getTask(taskId: string) {
    return this.request<TaskRecord>(`/api/tasks/${encodeURIComponent(taskId)}`)
  }

  cancelTask(taskId: string) {
    return this.request<unknown>(`/api/tasks/${encodeURIComponent(taskId)}/cancel`, {
      method: "POST",
    })
  }

  fileUrl(path: string) {
    return joinUrl(this.baseUrl, `/api/files?path=${encodeURIComponent(path)}`)
  }

  readTextFile(path: string) {
    return this.request<string>(`/api/files?path=${encodeURIComponent(path)}`)
  }

  writeTextFile(path: string, content: string) {
    return this.request<unknown>("/api/files", {
      method: "PUT",
      body: JSON.stringify({ path, content }),
    })
  }

  saveLlmSettings(payload: { api_key?: string | null; api_base: string; model: string }) {
    return this.request<unknown>("/api/settings/llm", {
      method: "PUT",
      body: JSON.stringify(payload),
    })
  }

  testLlmSettings(payload: { api_key?: string | null; api_base?: string; model?: string }) {
    return this.request<unknown>("/api/settings/llm/test", {
      method: "POST",
      body: JSON.stringify(payload),
    })
  }

  eventsUrl() {
    return joinUrl(this.baseUrl, "/api/events")
  }
}

export const api = new ApiClient()
