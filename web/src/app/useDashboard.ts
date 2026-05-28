import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { api, API_BASE, ApiError, normalizeApiBase } from "@/lib/api"
import { clamp, compactDateTime, pathBasename, uniqueStrings } from "@/lib/utils"
import type {
  AssetBuckets,
  AssetRecord,
  BackendStateResponse,
  DashboardTaskState,
  DiagnosticItem,
  ErrorInfo,
  HealthResponse,
  TaskKind,
  TaskRecord,
  TaskStatus,
  WorkerEventPayload,
} from "@/types"

type WorkspaceSlice = {
  current: string
  draft: string
  recent: string[]
  isSaving: boolean
  message: string
}

type CopySlice = {
  sourceType: string
  sourceInput: string
  downloadVideo: boolean
  extractAudio: boolean
  streamTranscription: boolean
  sourceText: string
  extractedText: string
  rewriteMode: string
  rewritePrompt: string
  rewriteModel: string
  temperature: number
  rewriteText: string
  title: string
  tags: string
}

type AudioSlice = {
  referenceAudioPath: string
  referenceAudioName: string
  voice: string
  promptText: string
  speed: number
  ultimateClone: boolean
  outputName: string
  selectedAudioPath: string
  playingAudioPath: string
  generatedAudioPath: string
}

type SubtitleStyleSlice = {
  font_name: string
  font_size: number
  color: string
  outline_color: string
  bottom_margin: number
  template: string
  highlight_keywords: string
  highlight_color: string
}

type SubtitleSlice = {
  audioPath: string
  referenceText: string
  referenceTextSourceAudioPath: string
  burnIn: boolean
  correctWithAI: boolean
  outputName: string
  style: SubtitleStyleSlice
  srtText: string
  generatedSrtPath: string
  generatedAssPath: string
  resultVideoPath: string
}

type AvatarSlice = {
  audioPath: string
  referenceVideoPath: string
  referenceVideoName: string
  baseVideoPath: string
  engine: string
  qualityPreset: string
  beautifyTeeth: boolean
  resultVideoPath: string
  errorLog: string[]
}

type PictureInPictureSlice = {
  enabled: boolean
  sourcePath: string
  sourceName: string
  fullDuration: boolean
  startSec: number
  endSec: number
  template: string
  position: string
  scale: number
  borderWidth: number
  borderColor: string
  shadow: boolean
  opacity: number
  animation: string
  fadeDuration: number
  loop: boolean
  resultVideoPath: string
  statusNote: string
}

type PublishSlice = {
  coverPath: string
  coverTitle: string
  coverHighlight: string
  coverTimestampSec: number
  publishTextPath: string
  title: string
  description: string
  tags: string
  platforms: Record<string, boolean>
  statusNote: string
}

type SettingsSlice = {
  apiKey: string
  apiBase: string
  llmApiBase: string
  rewriteModel: string
  voice: string
}

type ConnectionSlice = {
  live: boolean
  message: string
  lastError: string
  lastSynced: string
}

const DEFAULT_SUBTITLE_STYLE: SubtitleStyleSlice = {
  font_name: "Microsoft YaHei",
  font_size: 32,
  color: "#FFFFFF",
  outline_color: "#000000",
  bottom_margin: 72,
  template: "short_video",
  highlight_keywords: "",
  highlight_color: "#FF3B30",
}

const DEFAULT_PLATFORMS = {
  douyin: true,
  xiaohongshu: false,
  wechat_channels: false,
}

const API_BASE_STORAGE_KEY = "santiszr.apiBase"
const SETTINGS_STORAGE_KEY = "santiszr.settings"

function readStoredApiBase() {
  if (typeof window === "undefined") return API_BASE
  try {
    const stored = window.localStorage.getItem(API_BASE_STORAGE_KEY)
    return stored ? normalizeApiBase(stored) : API_BASE
  } catch {
    return API_BASE
  }
}

function readStoredSettings() {
  if (typeof window === "undefined") return {}
  try {
    const stored = window.localStorage.getItem(SETTINGS_STORAGE_KEY)
    if (!stored) return {}
    const parsed = JSON.parse(stored)
    return parsed && typeof parsed === "object" && !Array.isArray(parsed) ? parsed as Partial<SettingsSlice> : {}
  } catch {
    return {}
  }
}

function createEmptyBuckets(): AssetBuckets {
  return {
    audio: [],
    video: [],
    image: [],
    pip: [],
    subtitle: [],
    other: [],
  }
}

function asRecord(value: unknown): Record<string, unknown> | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null
  return value as Record<string, unknown>
}

function asString(value: unknown, fallback = "") {
  return typeof value === "string" ? value : fallback
}

function asBool(value: unknown, fallback = false) {
  return typeof value === "boolean" ? value : fallback
}

function asNumber(value: unknown, fallback = 0) {
  const number = typeof value === "number" ? value : Number(value)
  return Number.isFinite(number) ? number : fallback
}

function firstString(...values: unknown[]) {
  for (const value of values) {
    if (typeof value === "string" && value.trim()) return value
  }
  return ""
}

function formatPublishTags(value: unknown) {
  const rawItems = Array.isArray(value)
    ? value.map((item) => String(item))
    : typeof value === "string"
      ? value.split(/[\s,，、#]+/)
      : []
  const seen = new Set<string>()
  const tags: string[] = []
  for (const item of rawItems) {
    const tag = item.trim().replace(/^#+/, "")
    if (!tag || seen.has(tag)) continue
    seen.add(tag)
    tags.push(`#${tag}`)
    if (tags.length >= 10) break
  }
  return tags.join(" ")
}

function createClientId(prefix = "client") {
  const randomUUID = globalThis.crypto?.randomUUID
  if (typeof randomUUID === "function") {
    return randomUUID.call(globalThis.crypto)
  }
  return `${prefix}-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`
}

function normalizeTaskStatus(value: unknown, fallback = "pending") {
  const normalized = firstString(value, fallback).toLowerCase()
  if (normalized === "created" || normalized === "queued") return "queued"
  if (normalized === "started" || normalized === "progress" || normalized === "log") return "running"
  return normalized || fallback
}

function isAudioPath(path: string) {
  return /\.(mp3|wav|aac|m4a|flac|ogg|opus|pcm|amr)$/i.test(path)
}

function isReferenceAudioAsset(asset: AssetRecord | undefined, path = "") {
  return (
    asset?.category === "reference_audio" ||
    asset?.kind === "reference_audio" ||
    asset?.source === "reference/audio" ||
    /[\\/]reference[\\/]audio[\\/]/i.test(path)
  )
}

function audioOutputNameFromPath(path: string, fallback = "narration") {
  const name = pathBasename(path)
  const stem = name.replace(/\.[^.]+$/, "").trim()
  return stem || fallback
}

function isVideoPath(path: string) {
  return /\.(mp4|mov|mkv|webm|avi|m4v|ts)$/i.test(path)
}

function isImagePath(path: string) {
  return /\.(png|jpe?g|webp|avif|gif|bmp|tiff?)$/i.test(path)
}

function isSubtitlePath(path: string) {
  return /\.(srt|vtt|ass|ssa)$/i.test(path)
}

function detectAssetBucket(asset: AssetRecord) {
  const kind = `${asset.kind ?? ""}`.toLowerCase()
  const category = `${asset.category ?? ""}`.toLowerCase()
  const source = `${asset.source ?? ""}`.toLowerCase()
  const path = `${asset.path ?? asset.url ?? ""}`.toLowerCase()
  if (
    kind.includes("pip") ||
    category.includes("pip") ||
    source.startsWith("pip/") ||
    /[\\/]pip[\\/]/i.test(path)
  ) return "pip"
  if (kind.includes("audio") || isAudioPath(path)) return "audio"
  if (kind.includes("video") || isVideoPath(path)) return "video"
  if (kind.includes("image") || isImagePath(path)) return "image"
  if (kind.includes("subtitle") || isSubtitlePath(path)) return "subtitle"
  return "other"
}

function normalizeAsset(record: Record<string, unknown>, fallbackKind?: string): AssetRecord | null {
  const path = firstString(record.path, record.file_path, record.full_path, record.absolute_path, record.uri, record.url)
  const url = firstString(record.url, record.download_url)
  if (!path && !url) return null
  return {
    id: asString(record.id ?? record.asset_id ?? record.key, path || url),
    asset_id: asString(record.asset_id ?? record.id ?? record.key, path || url),
    kind: asString(record.kind ?? record.type ?? record.category ?? fallbackKind, fallbackKind ?? "other"),
    category: asString(record.category ?? record.kind ?? record.type, ""),
    name: asString(record.name ?? record.label, pathBasename(path || url)),
    path: path || url,
    url,
    created_at: asString(record.created_at ?? record.createdAt),
    updated_at: asString(record.updated_at ?? record.updatedAt),
    modified_at: asString(record.modified_at ?? record.modifiedAt),
    duration_sec: record.duration_sec !== undefined ? asNumber(record.duration_sec, 0) : null,
    mime_type: asString(record.mime_type ?? record.mimeType),
    size_bytes: record.size_bytes !== undefined ? asNumber(record.size_bytes, 0) : null,
    source: asString(record.source, ""),
    linked_text_path: asString(record.linked_text_path ?? record.linkedTextPath),
    linked_text_ref: asString(record.linked_text_ref ?? record.linkedTextRef),
    text_preview: asString(record.text_preview ?? record.textPreview),
    meta: record.meta ? asRecord(record.meta) ?? undefined : undefined,
  }
}

function flattenAssets(raw: unknown, fallbackKind?: string): AssetRecord[] {
  if (!raw) return []
  if (Array.isArray(raw)) {
    return raw.flatMap((item) => {
      const record = asRecord(item)
      return record ? [normalizeAsset(record, fallbackKind)].filter(Boolean) as AssetRecord[] : []
    })
  }
  const record = asRecord(raw)
  if (!record) return []
  const directAsset = asRecord(record.asset)
  if (directAsset) {
    const normalized = normalizeAsset(directAsset, fallbackKind)
    return normalized ? [normalized] : []
  }
  const items = record.items ?? record.data ?? record.assets ?? record.files ?? record.results
  if (Array.isArray(items)) {
    return flattenAssets(items, fallbackKind)
  }
  const buckets: AssetRecord[] = []
  for (const [key, value] of Object.entries(record)) {
    if (["items", "data", "assets", "files", "results"].includes(key)) continue
    if (Array.isArray(value)) {
      buckets.push(...flattenAssets(value, key))
    }
  }
  const fallback = normalizeAsset(record, fallbackKind)
  if (fallback) buckets.push(fallback)
  return buckets
}

function normalizeAssetBuckets(raw: unknown): AssetBuckets {
  const buckets = createEmptyBuckets()
  const flat = flattenAssets(raw).filter((asset) => asset.source !== "media-library")
  for (const asset of flat) {
    buckets[detectAssetBucket(asset)].push(asset)
  }
  for (const key of Object.keys(buckets) as Array<keyof AssetBuckets>) {
    buckets[key].sort((a, b) => {
      const byDate = (a.modified_at ?? a.updated_at ?? a.created_at ?? "").localeCompare(
        b.modified_at ?? b.updated_at ?? b.created_at ?? "",
      )
      if (byDate !== 0) return byDate
      return (a.path ?? "").localeCompare(b.path ?? "")
    })
  }
  return buckets
}

function normalizeRecentWorkspaces(raw: unknown): string[] {
  if (Array.isArray(raw)) {
    return uniqueStrings(raw.map((item) => (typeof item === "string" ? item : "")))
  }
  const record = asRecord(raw)
  if (!record) return []
  return uniqueStrings(
    [
      ...(Array.isArray(record.items) ? record.items : []),
      ...(Array.isArray(record.recent_workspaces) ? record.recent_workspaces : []),
      ...(Array.isArray(record.workspaces) ? record.workspaces : []),
    ].map((item) => (typeof item === "string" ? item : "")),
  )
}

function normalizeTasks(raw: unknown): TaskRecord[] {
  let items: unknown[] = []
  if (Array.isArray(raw)) {
    items = raw
  } else {
    const record = asRecord(raw)
    if (!record) return []
    const candidates = [record.items, record.tasks, record.data, record.results]
    for (const candidate of candidates) {
      if (Array.isArray(candidate)) {
        items = candidate
        break
      }
    }
    if (items.length === 0 && record.task_id) {
      items = [record]
    }
  }

  return items
    .map((item) => asRecord(item))
    .filter((item): item is Record<string, unknown> => Boolean(item))
    .map(normalizeTaskRecord)
    .filter(Boolean) as TaskRecord[]
}

function normalizeTaskRecord(raw: Record<string, unknown>): TaskRecord {
  const errorRecord = asRecord(raw.error)
  const status = normalizeTaskStatus(firstString(raw.status, raw.event, raw.state), "pending")
  const taskId = firstString(raw.task_id, raw.id, raw.taskId) || createClientId("task")
  return {
    task_id: taskId,
    task_kind: firstString(raw.task_kind, raw.kind, raw.taskKind, "content"),
    status,
    stage: firstString(raw.stage, raw.step),
    progress: asNumber(raw.progress, 0),
    message: firstString(raw.message, raw.title),
    created_at: firstString(raw.created_at, raw.createdAt),
    updated_at: firstString(raw.updated_at, raw.updatedAt),
    logs: Array.isArray(raw.logs) ? raw.logs.map((item) => (typeof item === "string" ? item : JSON.stringify(item))) : [],
    payload: asRecord(raw.payload) ?? undefined,
    result: raw.result ?? raw.payload ?? undefined,
    error: errorRecord
      ? {
          code: asString(errorRecord.code),
          message: asString(errorRecord.message),
          detail: asRecord(errorRecord.detail) ?? undefined,
        }
      : null,
  }
}

function normalizeHealth(raw: unknown): HealthResponse {
  const record = asRecord(raw)
  if (!record) {
    return {}
  }
  const ok = asBool(record.ok, asBool(record.healthy, asBool(record.runtime_ok, false)))
  return {
    ...record,
    ok,
    status: asString(record.status, ok ? "ok" : ""),
  }
}

function normalizeBackendState(raw: unknown): BackendStateResponse {
  const record = asRecord(raw)
  return record ? (record as BackendStateResponse) : {}
}

function normalizeReferenceTranscript(raw: unknown) {
  const record = asRecord(raw)
  return {
    transcript: firstString(record?.transcript, record?.text),
    cacheHit: asBool(record?.cache_hit, false),
  }
}

function unwrapTaskResponse(raw: unknown) {
  if (!raw || typeof raw !== "object") return { task: null, payload: null }
  const record = raw as Record<string, unknown>
  const task = asRecord(record.task) ?? (record.task_id || record.id || record.kind || record.task_kind ? record : null)
  const payload = asRecord(record.result) ?? asRecord(record.payload) ?? asRecord(record.data) ?? asRecord(record.artifacts)
  return { task, payload }
}

function normalizeWorkerEvent(raw: unknown) {
  const record = asRecord(raw)
  if (!record) return null
  const event = normalizeTaskStatus(firstString(record.event, record.status, record.state), "running")
  const taskId = firstString(record.task_id, record.id, record.taskId)
  const taskKind = firstString(record.task_kind, record.kind, record.taskKind, "content")
  if (!taskId) return null
  const errorRecord = asRecord(record.error)
  return {
    event,
    task_id: taskId,
    task_kind: taskKind,
    stage: firstString(record.stage, record.step),
    progress: clamp(asNumber(record.progress, event === "succeeded" ? 1 : 0)),
    message: firstString(record.message, record.detail, record.title),
    payload: asRecord(record.payload) ?? asRecord(record.data) ?? asRecord(record.result) ?? undefined,
    error: errorRecord
      ? {
          code: asString(errorRecord.code),
          message: asString(errorRecord.message),
          detail: asRecord(errorRecord.detail) ?? undefined,
        }
      : null,
  }
}

function mergeLists(existing: string[], incoming: string[]) {
  return uniqueStrings([...incoming, ...existing])
}

function statusTone(status: TaskStatus | undefined) {
  const normalized = `${status ?? ""}`.toLowerCase()
  if (normalized === "running" || normalized === "pending" || normalized === "queued" || normalized === "created") return "warning"
  if (normalized === "succeeded") return "success"
  if (normalized === "failed" || normalized === "cancelled") return "error"
  return "idle"
}

function isActiveTaskStatus(status: TaskStatus | undefined) {
  const normalized = `${status ?? ""}`.toLowerCase()
  return normalized === "running" || normalized === "pending" || normalized === "queued" || normalized === "created"
}

function compactError(error?: ErrorInfo | null) {
  return error?.message || error?.code || ""
}

function statusIsHealthy(value: unknown) {
  const status = firstString(value).toLowerCase()
  return status === "ok" || status === "ready" || status === "available"
}

function looksLikePath(value: string) {
  return /^[A-Za-z]:[\\/]/.test(value) || /^\\\\/.test(value) || /^\//.test(value)
}

function diagnosticItemFromRecord(record: Record<string, unknown>, fallbackLabel = "诊断项"): DiagnosticItem {
  const label = firstString(record.name, record.label, record.title, fallbackLabel)
  const status = firstString(record.status, record.state)
  const ok = statusIsHealthy(status) || asBool(record.ok, asBool(record.ready, asBool(record.available, false)))
  const detail = firstString(record.message, record.detail, record.status, ok ? "可用" : "异常")
  const explicitPath = firstString(record.path, record.executable, record.model_path, record.modelPath)
  return {
    label,
    ok,
    detail,
    path: explicitPath || (looksLikePath(detail) ? detail : ""),
  }
}

function diagnosticItemsFromHealth(health: HealthResponse | null): DiagnosticItem[] {
  if (!health) return []

  if (Array.isArray(health.diagnostics) && health.diagnostics.length > 0) {
    return health.diagnostics
      .map((item) => asRecord(item))
      .filter((item): item is Record<string, unknown> => Boolean(item))
      .map((item) => diagnosticItemFromRecord(item))
  }

  const sources: Array<[string, unknown]> = [
    ["FFmpeg", health.ffmpeg],
    ["VoxCPM", health.voxcpm],
    ["TuiliONNX", health.tuilionnx],
    ["Whisper", health.whisper],
    ["GPU", health.gpu],
  ]

  return sources.map(([label, raw]) => {
    if (raw && typeof raw === "object" && !Array.isArray(raw)) {
      return diagnosticItemFromRecord(raw as Record<string, unknown>, label)
    }
    if (typeof raw === "boolean") {
      return { label, ok: raw, detail: raw ? "可用" : "不可用" }
    }
    if (typeof raw === "string") {
      const lower = raw.toLowerCase()
      return { label, ok: lower.includes("ok") || lower.includes("ready") || lower.includes("available"), detail: raw }
    }
    return { label, ok: false, detail: "未返回" }
  })
}

function updateTaskCollection(state: DashboardTaskState, patch: Partial<TaskRecord> & { task_id: string; task_kind?: string }) {
  const normalizedPatch = normalizeTaskRecord({
    task_id: patch.task_id,
    task_kind: patch.task_kind ?? "content",
    status: patch.status ?? "running",
    stage: patch.stage ?? "",
    progress: patch.progress ?? 0,
    message: patch.message ?? "",
    logs: patch.logs ?? [],
    error: patch.error ?? null,
    created_at: patch.created_at ?? "",
    updated_at: patch.updated_at ?? "",
    payload: patch.payload ?? {},
    result: patch.result ?? {},
  })

  const existingIndex = state.tasks.findIndex((task) => task.task_id === normalizedPatch.task_id)
  const tasks = [...state.tasks]
  if (existingIndex >= 0) {
    tasks[existingIndex] = {
      ...tasks[existingIndex],
      ...normalizedPatch,
      logs: uniqueStrings([...(tasks[existingIndex].logs ?? []), ...(normalizedPatch.logs ?? [])]),
    }
  } else {
    tasks.unshift(normalizedPatch)
  }

  tasks.sort((a, b) => (b.updated_at ?? b.created_at ?? "").localeCompare(a.updated_at ?? a.created_at ?? ""))
  return {
    tasks,
    currentTaskId: tasks.find((task) => isActiveTaskStatus(task.status))?.task_id ?? "",
    logsByTask: {
      ...state.logsByTask,
      [normalizedPatch.task_id]: uniqueStrings([...(state.logsByTask[normalizedPatch.task_id] ?? []), ...(normalizedPatch.logs ?? [])]),
    },
  }
}

function publishPlatformLabel(value: unknown) {
  const key = firstString(value)
  if (key === "douyin") return "抖音"
  if (key === "xiaohongshu") return "小红书"
  if (key === "wechat_channels") return "视频号"
  return key || "未知平台"
}

function publishErrorMessage(error: unknown) {
  const record = asRecord(error)
  const code = firstString(record?.code)
  const message = firstString(record?.message)
  if (code === "publish_not_configured") return "未配置自动发布器"
  if (code === "publish_script_missing") return "发布脚本不存在"
  if (code === "publish_browser_missing_dependency") return "缺少本机浏览器自动化依赖"
  if (code === "publish_browser_assist_failed") return message || "本机半自动发布失败"
  return message || code || "失败"
}

function formatPublishStatus(record: Record<string, unknown>) {
  const results = Array.isArray(record.results) ? record.results.map((item) => asRecord(item)).filter(Boolean) : []
  if (results.length > 0) {
    const parts = results.map((item) => {
      const label = publishPlatformLabel(item?.platform)
      const success = asBool(item?.success, false)
      const status = firstString(item?.status)
      if (success && status === "browser_opened") return `${label} 已打开发布页`
      if (success) return `${label} 成功`
      return `${label} ${publishErrorMessage(item?.error)}`
    })
    return `发布结果：${parts.join("；")}`
  }

  const errorMessage = publishErrorMessage(record.error)
  if (asRecord(record.error)) return `发布失败：${errorMessage}`

  const summary = firstString(record.summary)
  if (summary) return `发布结果：${summary}`

  return asBool(record.success, false) ? "发布任务已完成。" : "发布任务未完成。"
}

function latestPublishTaskPayload(tasks: TaskRecord[]) {
  const task = tasks.find((item) => item.task_kind === "publish_materials" && !isActiveTaskStatus(item.status))
  if (!task) return null
  const result = asRecord(task.result)
  if (result) return result
  if (task.error || task.message) {
    return {
      success: false,
      error: task.error ?? { code: "publish_failed", message: task.message || "发布失败" },
    }
  }
  return null
}

function latestCompletedTaskPayload(tasks: TaskRecord[], kind: string) {
  const task = tasks.find((item) => item.task_kind === kind && !isActiveTaskStatus(item.status))
  if (!task) return null
  const result = asRecord(task.result)
  if (result) return result
  const payload = asRecord(task.payload)
  return payload
}

function applyTaskPayload(
  kind: TaskKind | string,
  payload: unknown,
  setters: {
    setCopy: React.Dispatch<React.SetStateAction<CopySlice>>
    setAudio: React.Dispatch<React.SetStateAction<AudioSlice>>
    setSubtitle: React.Dispatch<React.SetStateAction<SubtitleSlice>>
    setAvatar: React.Dispatch<React.SetStateAction<AvatarSlice>>
    setPictureInPicture: React.Dispatch<React.SetStateAction<PictureInPictureSlice>>
    setPublish: React.Dispatch<React.SetStateAction<PublishSlice>>
  },
) {
  const wrappedRecord = asRecord(payload)
  const wrappedKindRecord = wrappedRecord ? asRecord(wrappedRecord[kind]) : null
  const record = wrappedKindRecord ?? wrappedRecord
  if (!record) return

  if (kind === "content") {
    const extracted = asRecord(record.extracted_copy)
    setters.setCopy((prev) => ({
      ...prev,
      sourceText: firstString(record.cleaned_text, record.raw_text, extracted?.cleaned_text, extracted?.raw_text, prev.sourceText),
      extractedText: firstString(extracted?.cleaned_text, extracted?.raw_text, record.cleaned_text, record.raw_text, prev.extractedText),
      title: firstString(record.title, extracted?.title, prev.title),
    }))
    setters.setPublish((prev) => ({
      ...prev,
      title: firstString(record.title, extracted?.title, prev.title),
    }))
  }

  if (kind === "rewrite" || kind === "rewrite-text") {
    const rewrittenText = firstString(record.rewritten_text, record.text, record.content)
    const title = firstString(record.title)
    const tags = Array.isArray(record.tags) ? record.tags.filter((item) => typeof item === "string").join(", ") : ""
    setters.setCopy((prev) => ({
      ...prev,
      rewriteText: rewrittenText || prev.rewriteText,
      title: title || prev.title,
      tags: tags || prev.tags,
    }))
    setters.setSubtitle((prev) => ({
      ...prev,
      referenceText: rewrittenText || prev.referenceText,
    }))
    setters.setPublish((prev) => ({
      ...prev,
      title: title || prev.title,
      tags: tags || prev.tags,
    }))
  }

  if (kind === "tts") {
    setters.setAudio((prev) => ({
      ...prev,
      generatedAudioPath: firstString(record.audio_path, prev.generatedAudioPath),
      selectedAudioPath: firstString(record.audio_path, prev.selectedAudioPath),
      referenceAudioPath: firstString(record.reference_audio_path, prev.referenceAudioPath),
    }))
    setters.setSubtitle((prev) => ({
      ...prev,
      audioPath: firstString(record.audio_path, prev.audioPath),
    }))
    setters.setAvatar((prev) => ({
      ...prev,
      audioPath: firstString(record.audio_path, prev.audioPath),
    }))
  }

  if (kind === "subtitle") {
    setters.setSubtitle((prev) => ({
      ...prev,
      srtText: firstString(record.subtitle_text, prev.srtText),
      generatedSrtPath: firstString(record.srt_path, prev.generatedSrtPath),
      generatedAssPath: firstString(record.ass_path, prev.generatedAssPath),
      resultVideoPath: firstString(record.burned_video_path),
    }))
  }

  if (kind === "avatar") {
    const videoPath = firstString(record.video_path)
    setters.setAvatar((prev) => ({
      ...prev,
      baseVideoPath: videoPath || prev.baseVideoPath,
      resultVideoPath: videoPath || prev.resultVideoPath,
      errorLog: uniqueStrings([...prev.errorLog, ...(Array.isArray(record.notes) ? record.notes.filter((item) => typeof item === "string") : [])]),
    }))
  }

  if (kind === "postprocess") {
    const steps = Array.isArray(record.steps_applied) ? record.steps_applied.map((item) => String(item)) : []
    if (steps.includes("pip") || record.pip_video_path) {
      setters.setPictureInPicture((prev) => ({
        ...prev,
        enabled: true,
        resultVideoPath: firstString(record.pip_video_path, record.final_video_path, prev.resultVideoPath),
        sourcePath: firstString(record.pip_source_path, prev.sourcePath),
        statusNote: firstString(record.final_video_path)
          ? `画中画视频已生成：${firstString(record.final_video_path)}`
          : "画中画后处理已完成。",
      }))
    }
    if (steps.includes("subtitle") || record.subtitle_video_path) {
      setters.setSubtitle((prev) => ({
        ...prev,
        resultVideoPath: firstString(record.subtitle_video_path, record.final_video_path, prev.resultVideoPath),
      }))
    }
    setters.setPublish((prev) => ({
      ...prev,
      coverPath: firstString(record.cover_image_path, prev.coverPath),
    }))
  }

  if (kind === "publish_materials") {
    setters.setPublish((prev) => ({
      ...prev,
      statusNote: formatPublishStatus(record),
    }))
  }

  if (kind === "workflow") {
    const artifacts = asRecord(record.artifacts)
    if (!artifacts) return
    if (artifacts.content) applyTaskPayload("content", artifacts.content, setters)
    if (artifacts.rewrite) applyTaskPayload("rewrite", artifacts.rewrite, setters)
    if (artifacts.tts) applyTaskPayload("tts", artifacts.tts, setters)
    if (artifacts.avatar) applyTaskPayload("avatar", artifacts.avatar, setters)
    if (artifacts.subtitle) applyTaskPayload("subtitle", artifacts.subtitle, setters)
    if (artifacts.postprocess) {
      const postprocess = asRecord(artifacts.postprocess)
      if (postprocess) {
        const steps = Array.isArray(postprocess.steps_applied) ? postprocess.steps_applied.map((item) => String(item)) : []
        setters.setPictureInPicture((prev) => ({
          ...prev,
          enabled: true,
          resultVideoPath: firstString(postprocess.pip_video_path, prev.resultVideoPath),
          sourcePath: firstString(postprocess.pip_source_path, prev.sourcePath),
        }))
        if (steps.includes("subtitle") || postprocess.subtitle_video_path) {
          setters.setSubtitle((prev) => ({
            ...prev,
            resultVideoPath: firstString(postprocess.subtitle_video_path, postprocess.final_video_path, prev.resultVideoPath),
          }))
        }
        setters.setPublish((prev) => ({
          ...prev,
          coverPath: firstString(postprocess.cover_image_path, prev.coverPath),
        }))
      }
    }
    if (artifacts.publish) {
      const publish = asRecord(artifacts.publish)
      if (publish) {
        setters.setPublish((prev) => ({
          ...prev,
          statusNote: formatPublishStatus(publish),
        }))
      }
    }
  }
}

export function useDashboard() {
  const [workspace, setWorkspace] = useState<WorkspaceSlice>({
    current: "",
    draft: "",
    recent: [],
    isSaving: false,
    message: "",
  })
  const [copy, setCopy] = useState<CopySlice>({
    sourceType: "douyin_share_text",
    sourceInput: "",
    downloadVideo: false,
    extractAudio: false,
    streamTranscription: true,
    sourceText: "",
    extractedText: "",
    rewriteMode: "custom",
    rewritePrompt: "",
    rewriteModel: readStoredSettings().rewriteModel || "deepseek-chat",
    temperature: 0.7,
    rewriteText: "",
    title: "",
    tags: "",
  })
  const [audio, setAudio] = useState<AudioSlice>({
    referenceAudioPath: "",
    referenceAudioName: "",
    voice: "default",
    promptText: "",
    speed: 1,
    ultimateClone: false,
    outputName: "narration",
    selectedAudioPath: "",
    playingAudioPath: "",
    generatedAudioPath: "",
  })
  const [subtitle, setSubtitle] = useState<SubtitleSlice>({
    audioPath: "",
    referenceText: "",
    referenceTextSourceAudioPath: "",
    burnIn: true,
    correctWithAI: false,
    outputName: "narration",
    style: DEFAULT_SUBTITLE_STYLE,
    srtText: "",
    generatedSrtPath: "",
    generatedAssPath: "",
    resultVideoPath: "",
  })
  const [avatar, setAvatar] = useState<AvatarSlice>({
    audioPath: "",
    referenceVideoPath: "",
    referenceVideoName: "",
    baseVideoPath: "",
    engine: "tuilionnx",
    qualityPreset: "clear",
    beautifyTeeth: false,
    resultVideoPath: "",
    errorLog: [],
  })
  const [pictureInPicture, setPictureInPicture] = useState<PictureInPictureSlice>({
    enabled: false,
    sourcePath: "",
    sourceName: "",
    fullDuration: true,
    startSec: 0,
    endSec: 8,
    template: "corner",
    position: "top_right",
    scale: 0.18,
    borderWidth: 0,
    borderColor: "#FFFFFF",
    shadow: false,
    opacity: 1,
    animation: "none",
    fadeDuration: 0.35,
    loop: true,
    resultVideoPath: "",
    statusNote: "未启用画中画。",
  })
  const [publish, setPublish] = useState<PublishSlice>({
    coverPath: "",
    coverTitle: "",
    coverHighlight: "",
    coverTimestampSec: 0,
    publishTextPath: "",
    title: "",
    description: "",
    tags: "",
    platforms: DEFAULT_PLATFORMS,
    statusNote: "等待发布任务。",
  })
  const [settings, setSettings] = useState<SettingsSlice>({
    apiKey: "",
    apiBase: readStoredApiBase(),
    llmApiBase: readStoredSettings().llmApiBase || "https://api.deepseek.com/v1",
    rewriteModel: readStoredSettings().rewriteModel || "deepseek-chat",
    voice: readStoredSettings().voice || "default",
  })
  const [assets, setAssets] = useState<AssetBuckets>(createEmptyBuckets())
  const [health, setHealth] = useState<HealthResponse | null>(null)
  const [stateSnapshot, setStateSnapshot] = useState<BackendStateResponse | null>(null)
  const [connection, setConnection] = useState<ConnectionSlice>({
    live: false,
    message: "",
    lastError: "",
    lastSynced: "",
  })
  const [taskState, setTaskState] = useState<DashboardTaskState>({
    tasks: [],
    currentTaskId: "",
    logsByTask: {},
  })
  const [isBusyByKind, setIsBusyByKind] = useState<Record<string, boolean>>({})
  const [loading, setLoading] = useState({
    health: false,
    state: false,
    tasks: false,
    assets: false,
  })
  const eventSourceRef = useRef<EventSource | null>(null)

  useEffect(() => {
    api.setBaseUrl(settings.apiBase)
    try {
      window.localStorage.setItem(API_BASE_STORAGE_KEY, settings.apiBase)
    } catch {
      // Ignore storage failures in private or restricted contexts.
    }
  }, [settings.apiBase])

  useEffect(() => {
    try {
      window.localStorage.setItem(
        SETTINGS_STORAGE_KEY,
        JSON.stringify({
          rewriteModel: settings.rewriteModel,
          llmApiBase: settings.llmApiBase,
          voice: settings.voice,
        }),
      )
    } catch {
      // Ignore storage failures in private or restricted contexts.
    }
  }, [settings.llmApiBase, settings.rewriteModel, settings.voice])

  const syncConnection = useCallback((patch: Partial<ConnectionSlice>) => {
    setConnection((prev) => ({ ...prev, ...patch }))
  }, [])

  const updateTaskState = useCallback((patch: Partial<TaskRecord> & { task_id: string; task_kind?: string }) => {
    setTaskState((prev) => updateTaskCollection(prev, patch))
  }, [])

  const ingestStateSnapshot = useCallback((snapshot: BackendStateResponse) => {
    setStateSnapshot(snapshot)
    const artifacts = asRecord(snapshot.artifacts)
    const currentWorkspace = firstString(snapshot.workspace, snapshot.current_workspace, snapshot.last_workspace, snapshot.path, workspace.current)
    const recent = normalizeRecentWorkspaces(snapshot.recent_workspaces)

    if (currentWorkspace) {
      setWorkspace((prev) => ({
        ...prev,
        current: currentWorkspace,
        draft: currentWorkspace,
        recent: mergeLists(prev.recent, recent),
        message: "",
        isSaving: false,
      }))
    } else if (recent.length > 0) {
      setWorkspace((prev) => ({
        ...prev,
        recent: mergeLists(prev.recent, recent),
      }))
    }

    setCopy((prev) => ({
      ...prev,
      sourceInput: firstString(snapshot.source_input, prev.sourceInput),
      sourceText: firstString(snapshot.source_text, snapshot.extracted_text, prev.sourceText),
      extractedText: firstString(snapshot.extracted_text, prev.extractedText),
      rewriteText: firstString(snapshot.rewritten_text, prev.rewriteText),
      title: firstString(snapshot.title, prev.title),
      tags: snapshot.tags ? snapshot.tags.filter((item): item is string => typeof item === "string").join(", ") || prev.tags : prev.tags,
    }))
    const ttsPath = firstString(snapshot.tts_audio_path, snapshot.subtitle_path, prevAudioFromState(snapshot))
    if (ttsPath) {
      setAudio((prev) => ({
        ...prev,
        selectedAudioPath: ttsPath,
        generatedAudioPath: ttsPath,
      }))
      setSubtitle((prev) => ({
        ...prev,
        audioPath: ttsPath,
      }))
      setAvatar((prev) => ({
        ...prev,
        audioPath: ttsPath,
      }))
    }
    setSubtitle((prev) => ({
      ...prev,
      srtText: firstString(snapshot.subtitle_text, prev.srtText),
      generatedSrtPath: firstString(snapshot.subtitle_path, artifacts?.subtitle, prev.generatedSrtPath),
      generatedAssPath: prev.generatedAssPath,
      resultVideoPath: firstString(snapshot.burned_video_path, snapshot.subtitle_video_path, prev.resultVideoPath),
    }))
    setAvatar((prev) => ({
      ...prev,
      baseVideoPath: firstString(snapshot.avatar_video_path, artifacts?.avatar_video, prev.baseVideoPath),
      resultVideoPath: firstString(snapshot.avatar_video_path, artifacts?.avatar_video, prev.resultVideoPath),
    }))
    setPublish((prev) => ({
      ...prev,
      coverPath: firstString(snapshot.cover_path, prev.coverPath),
      title: firstString(snapshot.title, prev.title),
      description: firstString(snapshot.description, prev.description),
      tags: formatPublishTags(snapshot.tags) || prev.tags,
    }))
    syncConnection({
      lastSynced: new Date().toISOString(),
      message: "状态已同步",
      lastError: "",
    })
  }, [syncConnection, workspace.current])

  function prevAudioFromState(snapshot: BackendStateResponse) {
    return firstString(snapshot.tts_audio_path, snapshot.subtitle_path)
  }

  const refreshHealth = useCallback(async () => {
    setLoading((prev) => ({ ...prev, health: true }))
    try {
      const result = await api.health()
      const normalized = normalizeHealth(result)
      setHealth(normalized)
      setSettings((prev) => ({
        ...prev,
        llmApiBase: firstString(normalized.llm?.api_base, prev.llmApiBase),
        rewriteModel: firstString(normalized.llm?.model, prev.rewriteModel),
      }))
      setCopy((prev) => ({
        ...prev,
        rewriteModel: firstString(normalized.llm?.model, prev.rewriteModel),
      }))
      syncConnection({ live: true, lastError: "", message: result?.message ? String(result.message) : "后端在线" })
    } catch (error) {
      setHealth(null)
      syncConnection({
        live: false,
        lastError: error instanceof Error ? error.message : "健康检查失败",
        message: "后端未连接",
      })
    } finally {
      setLoading((prev) => ({ ...prev, health: false }))
    }
  }, [syncConnection])

  const refreshState = useCallback(async () => {
    setLoading((prev) => ({ ...prev, state: true }))
    try {
      const result = await api.state()
      ingestStateSnapshot(normalizeBackendState(result))
    } catch (error) {
      syncConnection({ lastError: error instanceof Error ? error.message : "状态同步失败" })
    } finally {
      setLoading((prev) => ({ ...prev, state: false }))
    }
  }, [ingestStateSnapshot, syncConnection])

  const refreshRecentWorkspaces = useCallback(async () => {
    try {
      const result = await api.recentWorkspaces()
      const recent = Array.isArray(result)
        ? normalizeRecentWorkspaces(result)
        : normalizeRecentWorkspaces((result as Record<string, unknown>)?.recent_workspaces ?? (result as Record<string, unknown>)?.items)
      setWorkspace((prev) => ({
        ...prev,
        recent: mergeLists(prev.recent, recent),
      }))
    } catch (error) {
      syncConnection({ lastError: error instanceof Error ? error.message : "最近工作空间获取失败" })
    }
  }, [syncConnection])

  const refreshAssets = useCallback(async () => {
    setLoading((prev) => ({ ...prev, assets: true }))
    try {
      const result = await api.assets()
      setAssets(normalizeAssetBuckets(result))
    } catch (error) {
      syncConnection({ lastError: error instanceof Error ? error.message : "素材列表获取失败" })
    } finally {
      setLoading((prev) => ({ ...prev, assets: false }))
    }
  }, [syncConnection])

  const refreshTasks = useCallback(async () => {
    setLoading((prev) => ({ ...prev, tasks: true }))
    try {
      const result = await api.listTasks()
      const tasks = normalizeTasks(result)
      const logsByTask: Record<string, string[]> = {}
      for (const task of tasks) {
        logsByTask[task.task_id] = uniqueStrings(task.logs ?? [])
      }
      const publishPayload = latestPublishTaskPayload(tasks)
      if (publishPayload) {
        applyTaskPayload("publish_materials", publishPayload, {
          setCopy,
          setAudio,
          setSubtitle,
          setAvatar,
          setPictureInPicture,
          setPublish,
        })
      }
      const postprocessPayload = latestCompletedTaskPayload(tasks, "postprocess")
      if (postprocessPayload) {
        applyTaskPayload("postprocess", postprocessPayload, {
          setCopy,
          setAudio,
          setSubtitle,
          setAvatar,
          setPictureInPicture,
          setPublish,
        })
      }
      setTaskState((prev) => ({
        tasks,
        currentTaskId:
          tasks.find((task) => isActiveTaskStatus(task.status))?.task_id ??
          (tasks.some((task) => task.task_id === prev.currentTaskId && isActiveTaskStatus(task.status)) ? prev.currentTaskId : ""),
        logsByTask: {
          ...prev.logsByTask,
          ...logsByTask,
        },
      }))
    } catch (error) {
      syncConnection({ lastError: error instanceof Error ? error.message : "任务列表获取失败" })
    } finally {
      setLoading((prev) => ({ ...prev, tasks: false }))
    }
  }, [syncConnection])

  const refreshAll = useCallback(async () => {
    await Promise.allSettled([refreshHealth(), refreshState(), refreshRecentWorkspaces(), refreshAssets(), refreshTasks()])
  }, [refreshAssets, refreshHealth, refreshRecentWorkspaces, refreshState, refreshTasks])

  const setWorkspaceDraft = useCallback((value: string) => {
    setWorkspace((prev) => ({ ...prev, draft: value }))
  }, [])

  const selectWorkspace = useCallback(
    async (workspacePath: string) => {
      const normalized = workspacePath.trim()
      if (!normalized) {
        setWorkspace((prev) => ({ ...prev, message: "请输入工作空间路径。" }))
        return
      }
      setWorkspace((prev) => ({ ...prev, isSaving: true, message: "" }))
      try {
        const result = await api.selectWorkspace(normalized)
        const snapshot = normalizeBackendState(result)
        setWorkspace((prev) => ({
          ...prev,
          current: firstString(snapshot.workspace, snapshot.current_workspace, snapshot.path, normalized),
          draft: firstString(snapshot.workspace, snapshot.current_workspace, snapshot.path, normalized),
          recent: mergeLists(prev.recent, normalizeRecentWorkspaces(snapshot.recent_workspaces).length > 0 ? normalizeRecentWorkspaces(snapshot.recent_workspaces) : [normalized]),
          isSaving: false,
          message: "工作空间已切换。",
        }))
        ingestStateSnapshot(snapshot)
        await Promise.allSettled([refreshRecentWorkspaces(), refreshAssets(), refreshTasks()])
      } catch (error) {
        setWorkspace((prev) => ({
          ...prev,
          isSaving: false,
          message: error instanceof Error ? error.message : "工作空间切换失败。",
        }))
      }
    },
    [ingestStateSnapshot, refreshAssets, refreshRecentWorkspaces, refreshTasks],
  )

  const setBusyKind = useCallback((kind: TaskKind | string, value: boolean) => {
    setIsBusyByKind((prev) => ({ ...prev, [kind]: value }))
  }, [])

  const ingestTaskResponse = useCallback(
    (kind: TaskKind | string, raw: unknown) => {
      const { task, payload } = unwrapTaskResponse(raw)
      if (task) {
        const normalizedTask = normalizeTaskRecord({
          task_id: firstString(task.task_id, task.id),
          task_kind: firstString(task.task_kind, task.kind, kind),
          status: firstString(task.status, "running"),
          stage: firstString(task.stage),
          progress: asNumber(task.progress, 0),
          message: firstString(task.message),
          created_at: firstString(task.created_at, task.createdAt),
          updated_at: firstString(task.updated_at, task.updatedAt),
          logs: Array.isArray(task.logs) ? task.logs : [],
          payload: asRecord(task.payload) ?? undefined,
          result: asRecord(task.result) ?? undefined,
          error: asRecord(task.error as never)
            ? {
                code: asString((task.error as Record<string, unknown>).code),
                message: asString((task.error as Record<string, unknown>).message),
                detail: asRecord((task.error as Record<string, unknown>).detail) ?? undefined,
              }
            : null,
        })
        setTaskState((prev) => {
          const merged = updateTaskCollection(prev, normalizedTask)
          return {
            ...merged,
            currentTaskId: isActiveTaskStatus(normalizedTask.status) ? normalizedTask.task_id : merged.currentTaskId,
          }
        })
        if (!isActiveTaskStatus(normalizedTask.status)) {
          setIsBusyByKind((prev) => ({ ...prev, [kind]: false }))
        }
      }
      if (payload) {
        applyTaskPayload(kind, payload, {
          setCopy,
          setAudio,
          setSubtitle,
          setAvatar,
          setPictureInPicture,
          setPublish,
        })
      }
    },
    [],
  )

  const submitTask = useCallback(
    async (kind: TaskKind, payload: Record<string, unknown>) => {
      setBusyKind(kind, true)
      try {
        const response = await api.createTask(kind, payload)
        ingestTaskResponse(kind, response)
        await Promise.allSettled([refreshTasks(), refreshState(), refreshAssets()])
        return response
      } catch (error) {
        setBusyKind(kind, false)
        const message = error instanceof ApiError ? error.message : error instanceof Error ? error.message : "任务提交失败"
        syncConnection({ lastError: message, message })
        throw error
      }
    },
    [ingestTaskResponse, refreshAssets, refreshState, refreshTasks, setBusyKind, syncConnection],
  )

  const cancelTask = useCallback(async (taskId: string) => {
    try {
      await api.cancelTask(taskId)
      setTaskState((prev) => ({
        ...prev,
        tasks: prev.tasks.map((task) =>
          task.task_id === taskId ? { ...task, status: "cancelled", message: "已取消" } : task,
        ),
      }))
    } catch (error) {
      syncConnection({ lastError: error instanceof Error ? error.message : "取消任务失败" })
    }
  }, [syncConnection])

  const uploadAsset = useCallback(
    async (kind: string, file: File) => {
      try {
        const response = await api.uploadAsset(file, kind, workspace.current || workspace.draft)
        const assetsResponse = normalizeAssetBuckets(response)
        const normalizedKind = kind.trim().toLowerCase()
        const shouldMergeIntoList = normalizedKind !== "audio" && normalizedKind !== "video"
        if (shouldMergeIntoList && (
          assetsResponse.audio.length ||
          assetsResponse.video.length ||
          assetsResponse.image.length ||
          assetsResponse.pip.length ||
          assetsResponse.subtitle.length
        )) {
          setAssets((prev) => ({
            audio: mergeAssetLists(prev.audio, assetsResponse.audio),
            video: mergeAssetLists(prev.video, assetsResponse.video),
            image: mergeAssetLists(prev.image, assetsResponse.image),
            pip: mergeAssetLists(prev.pip, assetsResponse.pip),
            subtitle: mergeAssetLists(prev.subtitle, assetsResponse.subtitle),
            other: mergeAssetLists(prev.other, assetsResponse.other),
          }))
        } else if (shouldMergeIntoList) {
          const fallbackAsset = {
            id: createClientId("asset"),
            kind,
            name: file.name,
            path: file.name,
            created_at: new Date().toISOString(),
            meta: { size: file.size, type: file.type },
          } satisfies AssetRecord
          setAssets((prev) => {
            const bucket = detectAssetBucket(fallbackAsset)
            return {
              ...prev,
              [bucket]: mergeAssetLists(prev[bucket], [fallbackAsset]),
            }
          })
        }
        await Promise.allSettled([refreshAssets()])
        return response
      } catch (error) {
        syncConnection({ lastError: error instanceof Error ? error.message : "素材上传失败" })
        throw error
      }
    },
    [refreshAssets, syncConnection, workspace.current, workspace.draft],
  )

  const preparePublishMaterials = useCallback(
    async (payload: Record<string, unknown>) => {
      const coverOnly = payload.ui_mode === "cover"
      setPublish((prev) => ({ ...prev, statusNote: coverOnly ? "正在更新封面预览。" : "正在准备发布文案和封面。" }))
      try {
        const response = await api.preparePublishMaterials(payload)
        const record = asRecord(response)
        const tags = formatPublishTags(record?.tags)
        const publishTextPath = firstString(record?.publish_text_path)
        const coverPath = firstString(record?.cover_path)
        setPublish((prev) => ({
          ...prev,
          title: coverOnly ? prev.title : firstString(record?.title, prev.title),
          description: coverOnly ? prev.description : firstString(record?.description, prev.description),
          tags: coverOnly ? prev.tags : tags || prev.tags,
          coverPath: coverPath || prev.coverPath,
          coverTitle: firstString(record?.cover_title, prev.coverTitle),
          coverHighlight: firstString(record?.cover_highlight, prev.coverHighlight),
          publishTextPath: publishTextPath || prev.publishTextPath,
          statusNote: coverOnly
            ? coverPath
              ? `封面预览已更新：${coverPath}`
              : "封面预览已更新。"
            : publishTextPath
            ? `发布素材已准备：${publishTextPath}${coverPath ? `；封面：${coverPath}` : ""}`
            : "发布素材已准备。",
        }))
        await refreshAssets()
        return response
      } catch (error) {
        const message = error instanceof ApiError ? error.message : error instanceof Error ? error.message : "发布素材准备失败"
        setPublish((prev) => ({ ...prev, statusNote: message }))
        syncConnection({ lastError: message, message })
        throw error
      }
    },
    [refreshAssets, syncConnection],
  )

  const saveLlmSettings = useCallback(async () => {
    try {
      const response = await api.saveLlmSettings({
        api_key: settings.apiKey.trim() ? settings.apiKey.trim() : null,
        api_base: settings.llmApiBase,
        model: settings.rewriteModel,
      })
      setSettings((prev) => ({ ...prev, apiKey: "" }))
      await refreshHealth()
      syncConnection({ message: "大模型配置已保存", lastError: "" })
      return response
    } catch (error) {
      const message = error instanceof Error ? error.message : "大模型配置保存失败"
      syncConnection({ lastError: message, message })
      throw error
    }
  }, [refreshHealth, settings.apiKey, settings.llmApiBase, settings.rewriteModel, syncConnection])

  const testLlmSettings = useCallback(async () => {
    try {
      const response = await api.testLlmSettings({
        api_key: settings.apiKey.trim() || undefined,
        api_base: settings.llmApiBase,
        model: settings.rewriteModel,
      })
      const record = asRecord(response)
      const ok = asBool(record?.ok, false)
      const message = firstString(record?.message, ok ? "大模型连接成功" : "大模型连接失败")
      syncConnection({ message, lastError: ok ? "" : message })
      return response
    } catch (error) {
      const message = error instanceof Error ? error.message : "大模型连接测试失败"
      syncConnection({ lastError: message, message })
      throw error
    }
  }, [settings.apiKey, settings.llmApiBase, settings.rewriteModel, syncConnection])

  const deleteAsset = useCallback(
    async (path: string) => {
      try {
        await api.deleteAsset(path)
        setAudio((prev) => {
          const next = { ...prev }
          if (prev.referenceAudioPath === path) {
            next.referenceAudioPath = ""
            next.referenceAudioName = ""
          }
          if (prev.selectedAudioPath === path) next.selectedAudioPath = ""
          if (prev.playingAudioPath === path) next.playingAudioPath = ""
          if (prev.generatedAudioPath === path) next.generatedAudioPath = ""
          return next
        })
        setSubtitle((prev) => ({
          ...prev,
          ...(prev.audioPath === path ? { audioPath: "", referenceText: "", referenceTextSourceAudioPath: "" } : {}),
          ...(prev.resultVideoPath === path ? { resultVideoPath: "" } : {}),
        }))
        setAvatar((prev) => (prev.audioPath === path ? { ...prev, audioPath: "" } : prev))
        setPictureInPicture((prev) => {
          if (prev.sourcePath === path) return { ...prev, sourcePath: "", sourceName: "", resultVideoPath: "" }
          if (prev.resultVideoPath === path) return { ...prev, resultVideoPath: "" }
          return prev
        })
        await Promise.allSettled([refreshAssets(), refreshState()])
        return true
      } catch (error) {
        syncConnection({ lastError: error instanceof Error ? error.message : "素材删除失败" })
        throw error
      }
    },
    [refreshAssets, refreshState, syncConnection],
  )

  const fetchReferenceTranscript = useCallback(
    async (referenceAudioPath: string) => {
      const normalizedPath = referenceAudioPath.trim()
      if (!normalizedPath) return ""
      try {
        const response = await api.referenceAudioTranscript(normalizedPath, workspace.current || workspace.draft)
        const result = normalizeReferenceTranscript(response)
        if (result.transcript) {
          setAudio((prev) => ({
            ...prev,
            promptText: result.transcript,
          }))
        }
        syncConnection({
          message: result.cacheHit ? "已读取参考音频识别缓存" : "参考音频文案已识别",
          lastError: "",
        })
        return result.transcript
      } catch (error) {
        const message = error instanceof Error ? error.message : "参考音频文案识别失败"
        syncConnection({ lastError: message, message })
        throw error
      }
    },
    [syncConnection, workspace.current, workspace.draft],
  )

  function mergeAssetLists(existing: AssetRecord[], incoming: AssetRecord[]) {
    const merged = [...existing]
    for (const item of incoming) {
      if (!merged.some((candidate) => candidate.path === item.path)) {
        merged.push(item)
      }
    }
    merged.sort((a, b) => {
      const byDate = (a.modified_at ?? a.updated_at ?? a.created_at ?? "").localeCompare(
        b.modified_at ?? b.updated_at ?? b.created_at ?? "",
      )
      return byDate || (a.path ?? "").localeCompare(b.path ?? "")
    })
    return merged
  }

  const isTaskBusy = useCallback(
    (kind: TaskKind | string) => {
      if (isBusyByKind[kind]) return true
      return taskState.tasks.some((task) => task.task_kind === kind && isActiveTaskStatus(task.status))
    },
    [isBusyByKind, taskState.tasks],
  )

  const currentTask = useMemo(() => {
    const selectedTask = taskState.tasks.find((task) => task.task_id === taskState.currentTaskId)
    if (selectedTask && isActiveTaskStatus(selectedTask.status)) return selectedTask
    return taskState.tasks.find((task) => isActiveTaskStatus(task.status)) ?? null
  }, [taskState.currentTaskId, taskState.tasks])

  const taskHistory = useMemo(
    () =>
      [...taskState.tasks].sort(
        (a, b) => (b.updated_at ?? b.created_at ?? "").localeCompare(a.updated_at ?? a.created_at ?? ""),
      ),
    [taskState.tasks],
  )

  const diagnostics = useMemo(() => diagnosticItemsFromHealth(health), [health])

  const syncSubtitleFromAudioPath = useCallback(
    (audioPath: string) => {
      const normalizedPath = audioPath.trim()
      if (!normalizedPath) return

      const asset = assets.audio.find((item) => item.path === normalizedPath)
      if (isReferenceAudioAsset(asset, normalizedPath)) return

      const previewText = (asset?.text_preview ?? "").trim()
      const linkedTextPath = (asset?.linked_text_path ?? "").trim()
      const outputName = audioOutputNameFromPath(normalizedPath)

      setSubtitle((prev) => {
        const audioChanged = prev.audioPath !== normalizedPath
        const shouldUsePreview =
          Boolean(previewText) &&
          (audioChanged || !prev.referenceText.trim() || prev.referenceTextSourceAudioPath === normalizedPath)

        return {
          ...prev,
          audioPath: normalizedPath,
          outputName: audioChanged || !prev.outputName.trim() ? outputName || prev.outputName : prev.outputName,
          referenceText: shouldUsePreview ? previewText : audioChanged && !previewText ? "" : prev.referenceText,
          referenceTextSourceAudioPath: shouldUsePreview
            ? normalizedPath
            : audioChanged && !previewText
              ? ""
              : prev.referenceTextSourceAudioPath,
        }
      })

      if (!linkedTextPath) return

      void api
        .readTextFile(linkedTextPath)
        .then((text) => {
          const fullText = text.trim()
          if (!fullText) return
          setSubtitle((prev) => {
            if (prev.audioPath !== normalizedPath) return prev
            const shouldUseText =
              !prev.referenceText.trim() || prev.referenceTextSourceAudioPath === normalizedPath
            if (!shouldUseText) return prev
            return {
              ...prev,
              referenceText: fullText,
              referenceTextSourceAudioPath: normalizedPath,
            }
          })
        })
        .catch((error) => {
          syncConnection({
            lastError: error instanceof Error ? error.message : "音频同名文案读取失败",
          })
        })
    },
    [assets.audio, syncConnection],
  )

  useEffect(() => {
    const selectedPath = audio.selectedAudioPath.trim()
    if (!selectedPath) return
    const asset = assets.audio.find((item) => item.path === selectedPath)
    if (!asset || isReferenceAudioAsset(asset, selectedPath)) return
    syncSubtitleFromAudioPath(selectedPath)
  }, [assets.audio, audio.selectedAudioPath, syncSubtitleFromAudioPath])

  useEffect(() => {
    void refreshAll()
  }, [refreshAll, settings.apiBase])

  useEffect(() => {
    const source = new EventSource(api.eventsUrl())
    eventSourceRef.current = source

    source.onopen = () => {
      syncConnection({ live: true, message: "事件流已连接", lastError: "" })
    }

    source.onmessage = (event) => {
      const text = event.data?.toString?.() ?? ""
      if (!text) return
      try {
        const payload = normalizeWorkerEvent(JSON.parse(text) as WorkerEventPayload)
        if (!payload) return
        setTaskState((prev) => {
          const next = updateTaskCollection(prev, {
            task_id: payload.task_id,
            task_kind: payload.task_kind,
            status: payload.event,
            stage: payload.stage,
            progress: clamp(payload.progress ?? 0),
            message: payload.message,
            logs: payload.message ? [payload.message] : [],
            error: payload.error ?? null,
            updated_at: new Date().toISOString(),
          })
          return {
            ...next,
            currentTaskId: isActiveTaskStatus(payload.event) ? payload.task_id : next.currentTaskId,
          }
        })
        if (payload.payload) {
          applyTaskPayload(payload.task_kind, payload.payload, {
            setCopy,
            setAudio,
            setSubtitle,
            setAvatar,
            setPictureInPicture,
            setPublish,
          })
        }
        if (payload.event === "succeeded" || payload.event === "failed" || payload.event === "cancelled") {
          setIsBusyByKind((prev) => ({ ...prev, [payload.task_kind]: false }))
          if ((payload.task_kind === "tts" || payload.task_kind === "subtitle" || payload.task_kind === "postprocess") && payload.event === "succeeded") {
            void refreshAssets()
          }
        }
      } catch {
        syncConnection({ lastError: "事件流解析失败" })
      }
    }

    source.onerror = () => {
      syncConnection({ live: false, message: "事件流已断开" })
    }

    return () => {
      source.close()
      if (eventSourceRef.current === source) {
        eventSourceRef.current = null
      }
    }
  }, [refreshAssets, settings.apiBase, syncConnection, updateTaskState])

  const copyActions = {
    setSourceType: (value: string) => setCopy((prev) => ({ ...prev, sourceType: value })),
    setSourceInput: (value: string) => setCopy((prev) => ({ ...prev, sourceInput: value })),
    setDownloadVideo: (value: boolean) => setCopy((prev) => ({ ...prev, downloadVideo: value })),
    setExtractAudio: (value: boolean) => setCopy((prev) => ({ ...prev, extractAudio: value })),
    setStreamTranscription: (value: boolean) => setCopy((prev) => ({ ...prev, streamTranscription: value })),
    setSourceText: (value: string) => setCopy((prev) => ({ ...prev, sourceText: value })),
    setExtractedText: (value: string) => setCopy((prev) => ({ ...prev, extractedText: value })),
    setRewriteMode: (value: string) => setCopy((prev) => ({ ...prev, rewriteMode: value })),
    setRewritePrompt: (value: string) => setCopy((prev) => ({ ...prev, rewritePrompt: value })),
    setRewriteModel: (value: string) => {
      setCopy((prev) => ({ ...prev, rewriteModel: value }))
      setSettings((prev) => ({ ...prev, rewriteModel: value }))
    },
    setTemperature: (value: number) => setCopy((prev) => ({ ...prev, temperature: value })),
    setRewriteText: (value: string) => setCopy((prev) => ({ ...prev, rewriteText: value })),
    setTitle: (value: string) => {
      setCopy((prev) => ({ ...prev, title: value }))
      setPublish((prev) => ({ ...prev, title: value }))
    },
    setTags: (value: string) => {
      setCopy((prev) => ({ ...prev, tags: value }))
      setPublish((prev) => ({ ...prev, tags: value }))
    },
  }

  const audioActions = {
    setReferenceAudioPath: (value: string) => setAudio((prev) => ({ ...prev, referenceAudioPath: value, selectedAudioPath: value })),
    setReferenceAudioName: (value: string) => setAudio((prev) => ({ ...prev, referenceAudioName: value })),
    setVoice: (value: string) => {
      setAudio((prev) => ({ ...prev, voice: value }))
      setSettings((prev) => ({ ...prev, voice: value }))
    },
    setPromptText: (value: string) => setAudio((prev) => ({ ...prev, promptText: value })),
    setSpeed: (value: number) => setAudio((prev) => ({ ...prev, speed: value })),
    setUltimateClone: (value: boolean) => setAudio((prev) => ({ ...prev, ultimateClone: value })),
    setOutputName: (value: string) => setAudio((prev) => ({ ...prev, outputName: value })),
    setSelectedAudioPath: (value: string) => {
      setAudio((prev) => ({ ...prev, selectedAudioPath: value }))
      syncSubtitleFromAudioPath(value)
    },
    setPlayingAudioPath: (value: string) => setAudio((prev) => ({ ...prev, playingAudioPath: value })),
    setGeneratedAudioPath: (value: string) => setAudio((prev) => ({ ...prev, generatedAudioPath: value })),
  }

  const subtitleActions = {
    setAudioPath: (value: string) => setSubtitle((prev) => ({ ...prev, audioPath: value })),
    setReferenceText: (value: string) =>
      setSubtitle((prev) => ({ ...prev, referenceText: value, referenceTextSourceAudioPath: "" })),
    setBurnIn: (value: boolean) => setSubtitle((prev) => ({ ...prev, burnIn: value })),
    setCorrectWithAI: (value: boolean) => setSubtitle((prev) => ({ ...prev, correctWithAI: value })),
    setOutputName: (value: string) => setSubtitle((prev) => ({ ...prev, outputName: value })),
    setStyle: (key: keyof SubtitleStyleSlice, value: string | number) =>
      setSubtitle((prev) => ({
        ...prev,
        style: { ...prev.style, [key]: value },
        generatedAssPath: "",
        resultVideoPath: "",
      })),
    setSrtText: (value: string) =>
      setSubtitle((prev) => ({
        ...prev,
        srtText: value,
        generatedAssPath: "",
        resultVideoPath: "",
      })),
    clearResultVideo: () => setSubtitle((prev) => ({ ...prev, resultVideoPath: "" })),
  }

  const avatarActions = {
    setAudioPath: (value: string) => setAvatar((prev) => ({ ...prev, audioPath: value })),
    setReferenceVideoPath: (value: string) => setAvatar((prev) => ({ ...prev, referenceVideoPath: value })),
    setReferenceVideoName: (value: string) => setAvatar((prev) => ({ ...prev, referenceVideoName: value })),
    setEngine: (value: string) => setAvatar((prev) => ({ ...prev, engine: value })),
    setQualityPreset: (value: string) => setAvatar((prev) => ({ ...prev, qualityPreset: value })),
    setBeautifyTeeth: (value: boolean) => setAvatar((prev) => ({ ...prev, beautifyTeeth: value })),
    setResultVideoPath: (value: string) => setAvatar((prev) => ({ ...prev, resultVideoPath: value })),
    setErrorLog: (value: string[]) => setAvatar((prev) => ({ ...prev, errorLog: value })),
  }

  const pictureInPictureActions = {
    setEnabled: (value: boolean) =>
      setPictureInPicture((prev) => ({
        ...prev,
        enabled: value,
        statusNote: value ? "已启用画中画，选择素材后可生成。" : "未启用画中画。",
      })),
    setSourcePath: (value: string) =>
      setPictureInPicture((prev) => ({
        ...prev,
        sourcePath: value,
        resultVideoPath: value === prev.sourcePath ? prev.resultVideoPath : "",
        statusNote: value === prev.sourcePath ? prev.statusNote : "画中画素材已修改，请重新生成后查看新结果。",
      })),
    setSourceName: (value: string) => setPictureInPicture((prev) => ({ ...prev, sourceName: value })),
    setFullDuration: (value: boolean) =>
      setPictureInPicture((prev) => ({
        ...prev,
        fullDuration: value,
        resultVideoPath: value === prev.fullDuration ? prev.resultVideoPath : "",
        statusNote: value === prev.fullDuration ? prev.statusNote : "画中画显示时间已修改，请重新生成后查看新结果。",
      })),
    setStartSec: (value: number) =>
      setPictureInPicture((prev) => ({
        ...prev,
        startSec: value,
        resultVideoPath: value === prev.startSec ? prev.resultVideoPath : "",
        statusNote: value === prev.startSec ? prev.statusNote : "画中画显示时间已修改，请重新生成后查看新结果。",
      })),
    setEndSec: (value: number) =>
      setPictureInPicture((prev) => ({
        ...prev,
        endSec: value,
        resultVideoPath: value === prev.endSec ? prev.resultVideoPath : "",
        statusNote: value === prev.endSec ? prev.statusNote : "画中画显示时间已修改，请重新生成后查看新结果。",
      })),
    setTemplate: (value: string) =>
      setPictureInPicture((prev) => ({
        ...prev,
        template: value,
        resultVideoPath: value === prev.template ? prev.resultVideoPath : "",
        statusNote: value === prev.template ? prev.statusNote : "画中画模板已修改，请重新生成后查看新结果。",
      })),
    setPosition: (value: string) =>
      setPictureInPicture((prev) => ({
        ...prev,
        position: value,
        resultVideoPath: value === prev.position ? prev.resultVideoPath : "",
        statusNote: value === prev.position ? prev.statusNote : "画中画位置已修改，请重新生成后查看新结果。",
      })),
    setScale: (value: number) =>
      setPictureInPicture((prev) => ({
        ...prev,
        scale: value,
        resultVideoPath: value === prev.scale ? prev.resultVideoPath : "",
        statusNote: value === prev.scale ? prev.statusNote : "画中画显示大小已修改，请重新生成后查看新结果。",
      })),
    setBorderWidth: (value: number) =>
      setPictureInPicture((prev) => ({
        ...prev,
        borderWidth: value,
        resultVideoPath: value === prev.borderWidth ? prev.resultVideoPath : "",
        statusNote: value === prev.borderWidth ? prev.statusNote : "画中画边框已修改，请重新生成后查看新结果。",
      })),
    setBorderColor: (value: string) =>
      setPictureInPicture((prev) => ({
        ...prev,
        borderColor: value,
        resultVideoPath: value === prev.borderColor ? prev.resultVideoPath : "",
        statusNote: value === prev.borderColor ? prev.statusNote : "画中画边框颜色已修改，请重新生成后查看新结果。",
      })),
    setShadow: (value: boolean) =>
      setPictureInPicture((prev) => ({
        ...prev,
        shadow: value,
        resultVideoPath: value === prev.shadow ? prev.resultVideoPath : "",
        statusNote: value === prev.shadow ? prev.statusNote : "画中画阴影已修改，请重新生成后查看新结果。",
      })),
    setOpacity: (value: number) =>
      setPictureInPicture((prev) => ({
        ...prev,
        opacity: value,
        resultVideoPath: value === prev.opacity ? prev.resultVideoPath : "",
        statusNote: value === prev.opacity ? prev.statusNote : "画中画透明度已修改，请重新生成后查看新结果。",
      })),
    setAnimation: (value: string) =>
      setPictureInPicture((prev) => ({
        ...prev,
        animation: value,
        resultVideoPath: value === prev.animation ? prev.resultVideoPath : "",
        statusNote: value === prev.animation ? prev.statusNote : "画中画动画已修改，请重新生成后查看新结果。",
      })),
    setFadeDuration: (value: number) =>
      setPictureInPicture((prev) => ({
        ...prev,
        fadeDuration: value,
        resultVideoPath: value === prev.fadeDuration ? prev.resultVideoPath : "",
        statusNote: value === prev.fadeDuration ? prev.statusNote : "画中画动画时长已修改，请重新生成后查看新结果。",
      })),
    setLoop: (value: boolean) =>
      setPictureInPicture((prev) => ({
        ...prev,
        loop: value,
        resultVideoPath: value === prev.loop ? prev.resultVideoPath : "",
        statusNote: value === prev.loop ? prev.statusNote : "画中画循环设置已修改，请重新生成后查看新结果。",
      })),
    setResultVideoPath: (value: string) => setPictureInPicture((prev) => ({ ...prev, resultVideoPath: value })),
    setStatusNote: (value: string) => setPictureInPicture((prev) => ({ ...prev, statusNote: value })),
  }

  const publishActions = {
    setCoverPath: (value: string) => setPublish((prev) => ({ ...prev, coverPath: value })),
    setCoverTitle: (value: string) => setPublish((prev) => ({ ...prev, coverTitle: value })),
    setCoverHighlight: (value: string) => setPublish((prev) => ({ ...prev, coverHighlight: value })),
    setCoverTimestampSec: (value: number) => setPublish((prev) => ({ ...prev, coverTimestampSec: value })),
    setTitle: (value: string) => setPublish((prev) => ({ ...prev, title: value })),
    setDescription: (value: string) => setPublish((prev) => ({ ...prev, description: value })),
    setTags: (value: string) => setPublish((prev) => ({ ...prev, tags: value })),
    setPlatform: (key: string, value: boolean) => setPublish((prev) => ({ ...prev, platforms: { ...prev.platforms, [key]: value } })),
    setStatusNote: (value: string) => setPublish((prev) => ({ ...prev, statusNote: value })),
  }

  const settingsActions = {
    setApiKey: (value: string) => setSettings((prev) => ({ ...prev, apiKey: value })),
    setApiBase: (value: string) => {
      const next = normalizeApiBase(value)
      api.setBaseUrl(next)
      setSettings((prev) => ({ ...prev, apiBase: next }))
    },
    setLlmApiBase: (value: string) => setSettings((prev) => ({ ...prev, llmApiBase: value.trim() })),
    setRewriteModel: (value: string) => {
      setSettings((prev) => ({ ...prev, rewriteModel: value }))
      setCopy((prev) => ({ ...prev, rewriteModel: value }))
    },
    setVoice: (value: string) => {
      setSettings((prev) => ({ ...prev, voice: value }))
      setAudio((prev) => ({ ...prev, voice: value }))
    },
  }

  return {
    apiBase: settings.apiBase,
    workspace,
    setWorkspaceDraft,
    selectWorkspace,
    refreshWorkspace: refreshAll,
    refreshAll,
    copy,
    copyActions,
    audio,
    audioActions,
    subtitle,
    subtitleActions,
    avatar,
    avatarActions,
    pictureInPicture,
    pictureInPictureActions,
    publish,
    publishActions,
    settings,
    settingsActions,
    saveLlmSettings,
    testLlmSettings,
    assets,
    diagnostics,
    health,
    stateSnapshot,
    connection,
    loading,
    taskState,
    currentTask,
    taskHistory,
    isTaskBusy,
    submitTask,
    cancelTask,
    preparePublishMaterials,
    uploadAsset,
    deleteAsset,
    fetchReferenceTranscript,
    fileUrl: api.fileUrl.bind(api),
    writeTextFile: api.writeTextFile.bind(api),
    refreshAssets,
    refreshTasks,
    refreshHealth,
    statusTone,
    compactDateTime,
    compactError,
  }
}
