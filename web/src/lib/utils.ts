import { clsx, type ClassValue } from "clsx"
import { twMerge } from "tailwind-merge"

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}

export function clamp(value: number, min = 0, max = 1) {
  return Math.max(min, Math.min(max, value))
}

export function formatPercent(value: number | undefined) {
  return `${Math.round(clamp(Number(value) || 0) * 100)}%`
}

export function pathBasename(path: string | undefined | null) {
  if (!path) return ""
  const normalized = path.replace(/\\/g, "/")
  return normalized.split("/").filter(Boolean).pop() ?? path
}

export function compactDateTime(value: string | undefined | null) {
  if (!value) return "待记录"
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date)
}

export function uniqueStrings(values: Array<string | undefined | null>) {
  const seen = new Set<string>()
  const result: string[] = []
  for (const item of values) {
    const value = item?.trim()
    if (!value || seen.has(value)) continue
    seen.add(value)
    result.push(value)
  }
  return result
}

function stringFromRecord(record: Record<string, unknown> | null | undefined, keys: string[]) {
  if (!record) return ""
  for (const key of keys) {
    const value = record[key]
    if (typeof value === "string" && value.trim()) return value
  }
  return ""
}

export function assetPathFromUploadResponse(response: unknown, pathKeys: string[] = []) {
  if (!response || typeof response !== "object" || Array.isArray(response)) return ""
  const record = response as Record<string, unknown>
  const asset = record.asset && typeof record.asset === "object" && !Array.isArray(record.asset)
    ? (record.asset as Record<string, unknown>)
    : null
  const keys = ["path", "file_path", "full_path", "absolute_path", "url", "download_url", ...pathKeys]
  return stringFromRecord(asset, keys) || stringFromRecord(record, keys)
}
