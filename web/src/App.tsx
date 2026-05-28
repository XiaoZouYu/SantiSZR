import { useEffect, useMemo, useState } from "react"
import {
  Activity,
  FolderOpen,
  LayoutDashboard,
  PenLine,
  RefreshCw,
  Settings2,
  Sparkles,
  Captions,
  MicVocal,
  PictureInPicture2,
  Video,
  Send,
  Waypoints,
} from "lucide-react"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { Progress } from "@/components/ui/progress"
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip"
import { Separator } from "@/components/ui/separator"
import { WorkspaceSection } from "@/components/dashboard/WorkspaceSection"
import { CopywritingSection } from "@/components/dashboard/CopywritingSection"
import { AudioSection } from "@/components/dashboard/AudioSection"
import { SubtitleSection } from "@/components/dashboard/SubtitleSection"
import { AvatarSection } from "@/components/dashboard/AvatarSection"
import { PictureInPictureSection } from "@/components/dashboard/PictureInPictureSection"
import { PublishSection } from "@/components/dashboard/PublishSection"
import { TasksSection } from "@/components/dashboard/TasksSection"
import { SettingsSection } from "@/components/dashboard/SettingsSection"
import { PanelShell } from "@/components/dashboard/common"
import { useDashboard } from "@/app/useDashboard"
import { cn, pathBasename } from "@/lib/utils"
import type { AssetRecord, TaskRecord } from "@/types"

const NAV_ITEMS = [
  { id: "workspace", label: "工作空间", icon: FolderOpen },
  { id: "copywriting", label: "文案 / 改写", icon: PenLine },
  { id: "audio", label: "音频", icon: MicVocal },
  { id: "avatar", label: "数字人", icon: Video },
  { id: "pip", label: "画中画", icon: PictureInPicture2 },
  { id: "subtitle", label: "字幕", icon: Captions },
  { id: "publish", label: "发布", icon: Send },
  { id: "tasks", label: "任务中心", icon: Activity },
  { id: "settings", label: "设置 / 诊断", icon: Settings2 },
] as const

type NavItemId = (typeof NAV_ITEMS)[number]["id"]

function navIdFromHash(hash: string): NavItemId | null {
  const id = hash.replace(/^#/, "")
  return NAV_ITEMS.some((item) => item.id === id) ? (id as NavItemId) : null
}

const WORKFLOW_STEPS = [
  { id: "copywriting", label: "文案" },
  { id: "audio", label: "音频" },
  { id: "avatar", label: "数字人" },
  { id: "pip", label: "画中画" },
  { id: "subtitle", label: "字幕" },
  { id: "publish", label: "发布" },
] as const

type WorkflowStepId = (typeof WORKFLOW_STEPS)[number]["id"]

const AVATAR_QUALITY_PRESETS: Record<string, { batchSize: number; maxReferenceEdge: number; qualityPreset: string }> = {
  speed: { batchSize: 4, maxReferenceEdge: 720, qualityPreset: "speed" },
  clear: { batchSize: 4, maxReferenceEdge: 1080, qualityPreset: "clear" },
  hd: { batchSize: 2, maxReferenceEdge: 0, qualityPreset: "hd" },
}

function workflowStepFromTask(kind?: string, stage?: string): WorkflowStepId {
  const text = `${kind ?? ""} ${stage ?? ""}`.toLowerCase()
  if (text.includes("publish") || text.includes("发布")) return "publish"
  if (text.includes("pip") || text.includes("picture") || text.includes("画中画")) return "pip"
  if (text.includes("avatar") || text.includes("数字人") || text.includes("render")) return "avatar"
  if (text.includes("subtitle") || text.includes("字幕")) return "subtitle"
  if (text.includes("tts") || text.includes("audio") || text.includes("音频")) return "audio"
  if (text.includes("rewrite") || text.includes("content") || text.includes("文案") || text.includes("改写")) return "copywriting"
  return "copywriting"
}

function WorkflowRail({
  currentStep,
  progress,
  taskLabel,
}: {
  currentStep: string
  progress: number
  taskLabel: string
}) {
  const activeIndex = Math.max(
    0,
    WORKFLOW_STEPS.findIndex((step) => step.id === currentStep),
  )
  return (
    <PanelShell
      eyebrow="工作流"
      title="文案 -> 音频 -> 数字人 -> 画中画 -> 字幕 -> 发布"
      description="只要按顺序往下走，后续模块会直接复用上一步的产物。"
    >
      <div className="grid gap-4">
        <div className="flex flex-wrap items-center gap-2">
          {WORKFLOW_STEPS.map((step, index) => {
            const state = index < activeIndex ? "done" : index === activeIndex ? "active" : "idle"
            return (
              <div
                key={step.id}
                className={cn(
                  "flex items-center gap-2 rounded-md border px-3 py-2 text-sm font-medium",
                  state === "done" && "border-success/30 bg-success/10 text-success",
                  state === "active" && "border-warning/40 bg-warning/10 text-warning-foreground",
                  state === "idle" && "border-border bg-card text-muted-foreground",
                )}
              >
                <span className="text-xs uppercase">{index + 1}</span>
                <span>{step.label}</span>
              </div>
            )
          })}
        </div>
        <div className="grid gap-2">
          <div className="flex items-center justify-between gap-3 text-xs text-muted-foreground">
            <span>{taskLabel || "等待任务"}</span>
            <span>{Math.round(progress * 100)}%</span>
          </div>
          <Progress value={progress} />
        </div>
      </div>
    </PanelShell>
  )
}

function StageChip({
  label,
  value,
  tone = "secondary",
}: {
  label: string
  value: string
  tone?: "secondary" | "success" | "warning" | "destructive"
}) {
  return (
    <div className="flex items-center gap-2 rounded-md border border-border bg-card px-3 py-2">
      <span className="text-xs font-semibold uppercase tracking-normal text-muted-foreground">{label}</span>
      <Badge variant={tone}>{value}</Badge>
    </div>
  )
}

function firstNonEmptyString(...values: unknown[]) {
  for (const value of values) {
    if (typeof value === "string" && value.trim()) return value
  }
  return ""
}

function recordFrom(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value) ? (value as Record<string, unknown>) : null
}

function taskVideoPath(task: TaskRecord | null) {
  if (!task) return ""
  for (const candidate of [task.result, task.payload]) {
    const record = recordFrom(candidate)
    if (!record) continue
    const avatar = recordFrom(record.avatar)
    const postprocess = recordFrom(record.postprocess)
    const path = firstNonEmptyString(
      avatar?.video_path,
      postprocess?.final_video_path,
      postprocess?.subtitle_video_path,
      record.video_path,
      record.final_video_path,
      record.subtitle_video_path,
      record.path,
    )
    if (path) return path
  }
  return ""
}

function taskPostprocessRecord(task: TaskRecord | null) {
  if (!task || task.task_kind !== "postprocess") return null
  for (const candidate of [task.result, task.payload]) {
    const record = recordFrom(candidate)
    if (!record) continue
    return recordFrom(record.postprocess) ?? record
  }
  return null
}

function taskPostprocessSteps(task: TaskRecord | null) {
  const record = taskPostprocessRecord(task)
  return Array.isArray(record?.steps_applied) ? record.steps_applied.map((item) => String(item)) : []
}

function taskPostprocessPath(task: TaskRecord | null, ...keys: string[]) {
  const record = taskPostprocessRecord(task)
  if (!record) return ""
  return firstNonEmptyString(...keys.map((key) => record[key]))
}

function taskRewriteProvider(task: TaskRecord | null) {
  if (!task) return ""
  for (const candidate of [task.result, task.payload]) {
    const record = recordFrom(candidate)
    if (!record) continue
    const rewrite = recordFrom(record.rewrite)
    const provider = firstNonEmptyString(rewrite?.provider, record.provider)
    if (provider) return provider
  }
  return ""
}

function isAvatarVideoAsset(asset: AssetRecord) {
  return (
    asset.category === "avatar_video" ||
    asset.kind === "avatar_video" ||
    asset.source === "avatar" ||
    /[\\/]avatar[\\/]/i.test(asset.path)
  )
}

function assetModifiedAt(asset: AssetRecord) {
  return asset.modified_at ?? asset.updated_at ?? asset.created_at ?? ""
}

function compactRunId() {
  return new Date().toISOString().replace(/\D/g, "").slice(4, 14)
}

const PUBLISH_PLATFORM_ORDER = ["douyin", "xiaohongshu", "wechat_channels"] as const

function parsePublishTags(value: string) {
  const seen = new Set<string>()
  const tags: string[] = []
  for (const item of value.split(/[\s,，、#]+/)) {
    const tag = item.trim()
    if (!tag || seen.has(tag)) continue
    seen.add(tag)
    tags.push(tag)
  }
  return tags
}

function parseSubtitleKeywords(value: string) {
  const seen = new Set<string>()
  const keywords: string[] = []
  for (const item of value.split(/[\s,，,、]+/)) {
    const keyword = item.trim()
    if (!keyword || seen.has(keyword)) continue
    seen.add(keyword)
    keywords.push(keyword)
    if (keywords.length >= 12) break
  }
  return keywords
}

function App() {
  const dashboard = useDashboard()
  const [activeNav, setActiveNav] = useState<NavItemId>(() =>
    typeof window === "undefined" ? "workspace" : navIdFromHash(window.location.hash) ?? "workspace",
  )

  useEffect(() => {
    const syncActiveNavFromHash = () => {
      const nextNav = navIdFromHash(window.location.hash)
      if (nextNav) setActiveNav(nextNav)
    }
    syncActiveNavFromHash()
    window.addEventListener("hashchange", syncActiveNavFromHash)
    return () => window.removeEventListener("hashchange", syncActiveNavFromHash)
  }, [])

  const currentStep = workflowStepFromTask(dashboard.currentTask?.task_kind, dashboard.currentTask?.stage)
  const llmStatus = dashboard.health?.llm
  const llmConfigured = Boolean(llmStatus?.configured)
  const currentTone = dashboard.statusTone(dashboard.currentTask?.status)
  const currentTaskLabel = dashboard.currentTask ? `${dashboard.currentTask.task_kind} · ${dashboard.currentTask.stage || "pending"}` : "无任务"
  const workflowProgress = dashboard.currentTask?.progress ?? 0
  const audioTextSource = dashboard.copy.rewriteText || dashboard.copy.extractedText || dashboard.copy.sourceText || dashboard.copy.sourceInput
  const selectedWorkspace = (dashboard.workspace.current || dashboard.workspace.draft).trim()
  const sourceInput = dashboard.copy.sourceInput.trim()
  const rewriteSource = (dashboard.copy.sourceText || dashboard.copy.extractedText || dashboard.copy.sourceInput).trim()
  const selectedAudioAsset = dashboard.assets.audio.find((asset) => asset.path === dashboard.audio.selectedAudioPath)
  const selectedAudioLooksReference =
    selectedAudioAsset?.category === "reference_audio" ||
    selectedAudioAsset?.kind === "reference_audio" ||
    selectedAudioAsset?.source === "reference/audio" ||
    /[\\/]reference[\\/]audio[\\/]/i.test(dashboard.audio.selectedAudioPath)
  const selectedGeneratedAudioPath =
    dashboard.audio.selectedAudioPath && !selectedAudioLooksReference ? dashboard.audio.selectedAudioPath : ""
  const subtitleAudioPath = dashboard.subtitle.audioPath || selectedGeneratedAudioPath || dashboard.audio.generatedAudioPath
  const subtitleReferenceText = dashboard.subtitle.referenceText || dashboard.copy.rewriteText || dashboard.copy.sourceText
  const avatarAudioPath =
    dashboard.avatar.audioPath ||
    selectedGeneratedAudioPath ||
    dashboard.audio.generatedAudioPath ||
    dashboard.subtitle.audioPath
  const latestAvatarTask = useMemo(
    () => dashboard.taskHistory.find((task) => task.task_kind === "avatar") ?? null,
    [dashboard.taskHistory],
  )
  const latestAvatarTaskVideoPath = taskVideoPath(latestAvatarTask)
  const latestAvatarAssetPath = useMemo(() => {
    const avatarAssets = dashboard.assets.video.filter(isAvatarVideoAsset)
    if (!avatarAssets.length) return ""
    return [...avatarAssets].sort((a, b) => assetModifiedAt(b).localeCompare(assetModifiedAt(a)))[0]?.path ?? ""
  }, [dashboard.assets.video])
  const latestPictureInPictureTask = useMemo(
    () =>
      dashboard.taskHistory.find((task) => {
        const steps = taskPostprocessSteps(task)
        return steps.includes("pip") || Boolean(taskPostprocessPath(task, "pip_video_path"))
      }) ?? null,
    [dashboard.taskHistory],
  )
  const latestSubtitlePostprocessTask = useMemo(
    () =>
      dashboard.taskHistory.find((task) => {
        const steps = taskPostprocessSteps(task)
        return steps.includes("subtitle") || Boolean(taskPostprocessPath(task, "subtitle_video_path"))
      }) ?? null,
    [dashboard.taskHistory],
  )
  const avatarGeneratedVideoPath =
    dashboard.avatar.resultVideoPath ||
    latestAvatarTaskVideoPath ||
    latestAvatarAssetPath ||
    dashboard.avatar.baseVideoPath
  const rawPictureInPictureResultVideoPath =
    dashboard.pictureInPicture.resultVideoPath ||
    taskPostprocessPath(latestPictureInPictureTask, "pip_video_path")
  const pictureInPictureResultVideoPath = dashboard.pictureInPicture.enabled ? rawPictureInPictureResultVideoPath : ""
  const rawSubtitleResultVideoPath =
    dashboard.subtitle.resultVideoPath ||
    taskPostprocessPath(latestSubtitlePostprocessTask, "subtitle_video_path", "final_video_path")
  const subtitleResultVideoPath = dashboard.subtitle.burnIn ? rawSubtitleResultVideoPath : ""
  const subtitleTargetVideoPath =
    dashboard.pictureInPicture.enabled
      ? pictureInPictureResultVideoPath
      : avatarGeneratedVideoPath
  const publishVideoPath =
    subtitleResultVideoPath ||
    pictureInPictureResultVideoPath ||
    avatarGeneratedVideoPath
  const pictureInPictureSourceAssets = useMemo(
    () => dashboard.assets.pip,
    [dashboard.assets.pip],
  )
  const publishTitle = dashboard.publish.title || dashboard.copy.title
  const publishDescription = dashboard.publish.description || dashboard.copy.rewriteText || dashboard.copy.sourceText
  const publishTagsText = dashboard.publish.tags || dashboard.copy.tags
  const publishTags = parsePublishTags(publishTagsText)
  const publishAiSourceText =
    audioTextSource ||
    dashboard.subtitle.referenceText ||
    dashboard.copy.rewriteText ||
    dashboard.copy.extractedText ||
    dashboard.copy.sourceText ||
    dashboard.copy.sourceInput
  const subtitleStylePayload = useMemo(
    () => ({
      ...dashboard.subtitle.style,
      highlight_keywords: parseSubtitleKeywords(dashboard.subtitle.style.highlight_keywords),
    }),
    [dashboard.subtitle.style],
  )
  const selectedPublishPlatforms = PUBLISH_PLATFORM_ORDER.filter((platform) => dashboard.publish.platforms[platform])
  const sourceInputIsCurrentDirectory = sourceInput === "." || sourceInput === "./" || sourceInput === ".\\"
  const extractDisabledReason = !selectedWorkspace
    ? "请先选择工作空间，再提取原文。"
    : !sourceInput
      ? "请先填写分享文案、URL 或本地素材路径，再提取原文。"
      : sourceInputIsCurrentDirectory
        ? "请输入具体文案、URL 或文件路径，不能只使用当前目录符号。"
        : ""
  const rewriteDisabledReason = !selectedWorkspace
    ? "请先选择工作空间，再改写文案。"
    : !llmConfigured
      ? "请先到设置里保存并测试大模型配置，再改写文案。"
    : !rewriteSource
      ? "请先填写原文或完成提取，再改写文案。"
      : ""
  const subtitleDisabledReason = !selectedWorkspace
    ? "请先选择工作空间，再生成字幕。"
    : !dashboard.subtitle.burnIn
      ? "字幕未启用。"
    : !subtitleAudioPath.trim()
      ? "请先生成或选择音频，再生成字幕。"
      : ""
  const subtitleApplyDisabledReason = !selectedWorkspace
    ? "请先选择工作空间，再加载字幕到视频。"
    : !dashboard.subtitle.burnIn
      ? "字幕未启用。"
    : !dashboard.subtitle.generatedSrtPath.trim()
      ? "请先生成字幕，再加载到视频。"
      : dashboard.pictureInPicture.enabled && !pictureInPictureResultVideoPath.trim()
        ? "画中画已开启，请先生成画中画视频，再加载字幕。"
        : !subtitleTargetVideoPath.trim()
          ? "请先生成数字人视频，再加载字幕。"
          : ""
  const avatarDisabledReason = !selectedWorkspace
    ? "请先选择工作空间，再生成数字人。"
    : !avatarAudioPath.trim()
      ? "请先生成或选择音频，再生成数字人。"
      : !dashboard.avatar.referenceVideoPath.trim()
        ? "请先上传或选择参考视频。"
        : ""
  const pictureInPictureDisabledReason = !selectedWorkspace
    ? "请先选择工作空间，再生成画中画。"
    : !dashboard.pictureInPicture.enabled
      ? "打开画中画开关后再生成。"
      : !avatarGeneratedVideoPath.trim()
        ? "请先生成数字人视频，再添加画中画。"
        : !dashboard.pictureInPicture.sourcePath.trim()
          ? "请先上传或选择画中画素材。"
          : !dashboard.pictureInPicture.fullDuration && dashboard.pictureInPicture.endSec <= dashboard.pictureInPicture.startSec
            ? "结束秒数需要大于开始秒数。"
            : ""
  const publishDisabledReason = !selectedWorkspace
    ? "请先选择工作空间，再发布。"
    : !publishVideoPath.trim()
      ? "请先生成可发布的视频。"
      : !publishTitle.trim()
        ? "请先填写发布标题。"
        : publishTags.length === 0
          ? "请先填写至少一个发布标签。"
          : selectedPublishPlatforms.length === 0
            ? "请至少选择一个发布平台。"
            : ""

  const openSection = (id: NavItemId) => {
    setActiveNav(id)
    window.history.replaceState(null, "", `#${id}`)
    document.getElementById(id)?.scrollIntoView({ behavior: "smooth", block: "start" })
  }

  const handleExtractCopy = () => {
    if (extractDisabledReason) return Promise.resolve(null)
    return dashboard.submitTask("content", {
      source: {
        source_type: dashboard.copy.sourceType,
        raw_input: sourceInput,
      },
      workspace: selectedWorkspace,
      download_video: false,
      extract_audio: false,
      stream_transcription: true,
    })
  }

  const handleRewriteCopy = () => {
    if (rewriteDisabledReason) return Promise.resolve(null)
    return dashboard.submitTask("rewrite", {
      text: rewriteSource,
      mode: dashboard.copy.rewriteMode,
      prompt: dashboard.copy.rewritePrompt.trim() || null,
      model: dashboard.copy.rewriteModel,
      temperature: dashboard.copy.temperature,
      workspace: selectedWorkspace,
    })
  }

  const handleGeneratePictureInPicture = () => {
    if (pictureInPictureDisabledReason) return Promise.resolve(null)
    const outputBase = pathBasename(avatarGeneratedVideoPath).replace(/\.[^.]+$/, "") || "picture-in-picture"
    dashboard.pictureInPictureActions.setStatusNote(`正在生成画中画视频：${pathBasename(dashboard.pictureInPicture.sourcePath)}`)
    return dashboard.submitTask("postprocess", {
      video_path: avatarGeneratedVideoPath,
      picture_in_picture: {
        enabled: true,
        source_path: dashboard.pictureInPicture.sourcePath,
        start_sec: dashboard.pictureInPicture.fullDuration ? 0 : dashboard.pictureInPicture.startSec,
        end_sec: dashboard.pictureInPicture.fullDuration ? null : dashboard.pictureInPicture.endSec,
        template: dashboard.pictureInPicture.template,
        position: dashboard.pictureInPicture.position,
        scale: dashboard.pictureInPicture.scale,
        border_width: dashboard.pictureInPicture.borderWidth,
        border_color: dashboard.pictureInPicture.borderColor,
        shadow: dashboard.pictureInPicture.shadow,
        opacity: dashboard.pictureInPicture.opacity,
        animation: dashboard.pictureInPicture.animation,
        fade_duration: dashboard.pictureInPicture.fadeDuration,
        loop: dashboard.pictureInPicture.loop,
        mute: true,
      },
      burn_subtitles: false,
      workspace: selectedWorkspace,
      output_name: `${outputBase}_${compactRunId()}`,
    }).catch((error) => {
      dashboard.pictureInPictureActions.setStatusNote(error instanceof Error ? error.message : "画中画生成失败。")
      throw error
    })
  }

  const handlePublish = () => {
    if (publishDisabledReason) return Promise.resolve(null)
    dashboard.publishActions.setStatusNote("正在本机打开平台发布页，并尝试填充视频、封面和文案。")
    return dashboard.submitTask("publish_materials", {
      platforms: selectedPublishPlatforms,
      video_path: publishVideoPath,
      title: publishTitle.trim(),
      description: publishDescription.trim() || null,
      tags: publishTags,
      cover_path: dashboard.publish.coverPath.trim() || null,
      workspace: selectedWorkspace,
      continue_on_error: true,
      browser_assist: true,
    })
  }

  const handlePreparePublishMaterials = () => {
    if (!selectedWorkspace || !publishVideoPath.trim()) return Promise.resolve(null)
    return dashboard.preparePublishMaterials({
      workspace: selectedWorkspace,
      video_path: publishVideoPath,
      source_text: publishAiSourceText,
      title: "",
      description: "",
      tags: [],
      cover_title: dashboard.publish.coverTitle,
      cover_highlight: dashboard.publish.coverHighlight,
      cover_timestamp_sec: dashboard.publish.coverTimestampSec,
      generate_with_ai: true,
      generate_cover: true,
    })
  }

  const handleRenderPublishCover = () => {
    if (!selectedWorkspace || !publishVideoPath.trim()) return Promise.resolve(null)
    return dashboard.preparePublishMaterials({
      ui_mode: "cover",
      workspace: selectedWorkspace,
      video_path: publishVideoPath,
      source_text: publishAiSourceText,
      title: publishTitle,
      description: publishDescription,
      tags: publishTags,
      cover_title: dashboard.publish.coverTitle,
      cover_highlight: dashboard.publish.coverHighlight,
      cover_timestamp_sec: dashboard.publish.coverTimestampSec,
      generate_with_ai: false,
      generate_cover: true,
    })
  }

  const titleTags = useMemo(
    () => [
      dashboard.workspace.current || dashboard.workspace.draft || "未选择工作空间",
      dashboard.currentTask ? dashboard.currentTask.status || "running" : "idle",
    ],
    [dashboard.currentTask, dashboard.workspace.current, dashboard.workspace.draft],
  )

  const latestAudioTask = useMemo(
    () => dashboard.taskHistory.find((task) => task.task_kind === "tts") ?? null,
    [dashboard.taskHistory],
  )
  const latestRewriteProvider = taskRewriteProvider(
    dashboard.taskHistory.find((task) => task.task_kind === "rewrite" || task.task_kind === "rewrite-text") ?? null,
  )
  return (
    <TooltipProvider delayDuration={120}>
      <div className="min-h-screen bg-background text-foreground workbench-grid">
        <div className="mx-auto flex min-h-screen w-full max-w-[1680px]">
          <aside className="sticky top-0 hidden h-screen w-72 flex-col border-r border-border bg-[#171717] text-sidebar-foreground lg:flex">
            <div className="border-b border-sidebar-border px-5 py-5">
              <div className="flex items-center gap-3">
                <div className="flex h-11 w-11 items-center justify-center rounded-md bg-accent text-accent-foreground">
                  <LayoutDashboard className="h-5 w-5" />
                </div>
                <div className="min-w-0">
                  <p className="text-sm font-semibold tracking-tight">SantiSZR</p>
                  <p className="truncate text-xs text-sidebar-foreground/70">本地短视频生产工作台</p>
                </div>
              </div>
            </div>

            <div className="grid gap-4 px-4 py-4">
              <div className="grid gap-2 rounded-md border border-sidebar-border bg-white/5 p-3">
                <div className="flex items-center justify-between gap-3">
                  <span className="text-xs uppercase tracking-normal text-sidebar-foreground/70">工作空间</span>
                  <Badge variant={dashboard.workspace.current ? "success" : "warning"}>{dashboard.workspace.current ? "已选" : "未选"}</Badge>
                </div>
                <p className="truncate-path text-sm font-medium">
                  {dashboard.workspace.current || dashboard.workspace.draft || "等待选择"}
                </p>
                <p className="truncate-path text-xs text-sidebar-foreground/70">
                  {dashboard.workspace.current ? pathBasename(dashboard.workspace.current) : "需要先指定一个本地目录"}
                </p>
              </div>

              <div className="grid gap-2 rounded-md border border-sidebar-border bg-white/5 p-3">
                <div className="flex items-center justify-between gap-3">
                  <span className="text-xs uppercase tracking-normal text-sidebar-foreground/70">任务状态</span>
                  <Badge variant={currentTone === "success" ? "success" : currentTone === "warning" ? "warning" : currentTone === "error" ? "destructive" : "secondary"}>
                    {dashboard.currentTask?.status || "idle"}
                  </Badge>
                </div>
                <p className="truncate text-sm font-medium">{currentTaskLabel}</p>
                <div className="text-xs text-sidebar-foreground/70">{dashboard.connection.message || "事件流待连接"}</div>
              </div>
            </div>

            <Separator className="bg-sidebar-border" />

            <nav className="grid gap-1 px-3 py-3">
              {NAV_ITEMS.map((item) => {
                const Icon = item.icon
                const active = activeNav === item.id
                return (
                  <Button
                    key={item.id}
                    variant={active ? "secondary" : "ghost"}
                    className={cn(
                      "h-10 justify-start gap-3 border border-transparent bg-transparent text-left text-sidebar-foreground hover:bg-white/8 hover:text-white",
                      active && "border-sidebar-border bg-white/8 text-white",
                    )}
                    onClick={() => openSection(item.id)}
                  >
                    <Icon className="h-4 w-4" />
                    <span className="truncate">{item.label}</span>
                  </Button>
                )
              })}
            </nav>

            <div className="mt-auto border-t border-sidebar-border px-4 py-4 text-xs text-sidebar-foreground/70">
              <div className="grid gap-1">
                <div>API Base</div>
                <div className="truncate-path text-sidebar-foreground">{dashboard.apiBase}</div>
                <div className="mt-2 flex flex-wrap gap-2">
                  {titleTags.map((tag) => (
                    <Badge key={tag} variant="secondary">
                      {tag}
                    </Badge>
                  ))}
                </div>
              </div>
            </div>
          </aside>

          <div className="min-w-0 flex-1">
            <header className="sticky top-0 z-30 border-b border-border bg-card/88 backdrop-blur">
              <div className="flex items-center gap-3 px-4 py-3 lg:px-6">
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <Badge variant={dashboard.workspace.current ? "success" : "warning"}>
                      {dashboard.workspace.current ? "工作空间已选" : "尚未选择工作空间"}
                    </Badge>
                    <Badge variant={currentTone === "success" ? "success" : currentTone === "warning" ? "warning" : currentTone === "error" ? "destructive" : "secondary"}>
                      {dashboard.currentTask?.status || "idle"}
                    </Badge>
                    <Badge variant={dashboard.connection.live ? "success" : "destructive"}>
                      {dashboard.connection.live ? "后端在线" : "后端离线"}
                    </Badge>
                  </div>
                  <div className="mt-1 flex min-w-0 flex-wrap items-center gap-3">
                    <h1 className="truncate text-lg font-semibold tracking-tight sm:text-xl">本地短视频生产工作台</h1>
                    <div className="text-sm text-muted-foreground">
                      {dashboard.workspace.current || dashboard.workspace.draft || "先选一个工作空间开始"}
                    </div>
                  </div>
                </div>

                <div className="hidden items-center gap-2 lg:flex">
                  <Tooltip>
                    <TooltipTrigger asChild>
                      <Button size="icon" variant="quiet" aria-label="刷新全部" onClick={() => void dashboard.refreshAll()}>
                        <RefreshCw className="h-4 w-4" />
                      </Button>
                    </TooltipTrigger>
                    <TooltipContent>刷新全部</TooltipContent>
                  </Tooltip>
                  <Tooltip>
                    <TooltipTrigger asChild>
                      <Button size="icon" variant="quiet" aria-label="打开设置" onClick={() => openSection("settings")}>
                        <Settings2 className="h-4 w-4" />
                      </Button>
                    </TooltipTrigger>
                    <TooltipContent>跳转设置</TooltipContent>
                  </Tooltip>
                </div>
              </div>
              <div className="border-t border-border px-4 py-3 lg:hidden">
                <div className="flex gap-2 overflow-x-auto pb-1">
                  {NAV_ITEMS.map((item) => {
                    const Icon = item.icon
                    const active = activeNav === item.id
                    return (
                      <Button
                        key={item.id}
                        variant={active ? "secondary" : "quiet"}
                        size="sm"
                        className="shrink-0"
                        onClick={() => openSection(item.id)}
                      >
                        <Icon className="h-4 w-4" />
                        {item.label}
                      </Button>
                    )
                  })}
                </div>
              </div>
            </header>

            <main className="space-y-0">
              <WorkflowRail currentStep={currentStep} progress={workflowProgress} taskLabel={currentTaskLabel} />

              <WorkspaceSection
                current={dashboard.workspace.current}
                draft={dashboard.workspace.draft}
                recent={dashboard.workspace.recent}
                isSaving={dashboard.workspace.isSaving}
                message={dashboard.workspace.message}
                onDraftChange={dashboard.setWorkspaceDraft}
                onSelectWorkspace={dashboard.selectWorkspace}
                onRefresh={dashboard.refreshAll}
              />

              <CopywritingSection
                workspace={dashboard.workspace.current || dashboard.workspace.draft}
                sourceType={dashboard.copy.sourceType}
                sourceInput={dashboard.copy.sourceInput}
                downloadVideo={dashboard.copy.downloadVideo}
                extractAudio={dashboard.copy.extractAudio}
                streamTranscription={dashboard.copy.streamTranscription}
                sourceText={dashboard.copy.sourceText}
                extractedText={dashboard.copy.extractedText}
                rewriteMode={dashboard.copy.rewriteMode}
                rewritePrompt={dashboard.copy.rewritePrompt}
                rewriteModel={dashboard.copy.rewriteModel}
                temperature={dashboard.copy.temperature}
                rewriteText={dashboard.copy.rewriteText}
                title={dashboard.copy.title}
                tags={dashboard.copy.tags}
                onSourceTypeChange={dashboard.copyActions.setSourceType}
                onSourceInputChange={dashboard.copyActions.setSourceInput}
                onDownloadVideoChange={dashboard.copyActions.setDownloadVideo}
                onExtractAudioChange={dashboard.copyActions.setExtractAudio}
                onStreamTranscriptionChange={dashboard.copyActions.setStreamTranscription}
                onSourceTextChange={dashboard.copyActions.setSourceText}
                onExtractedTextChange={dashboard.copyActions.setExtractedText}
                onRewriteModeChange={dashboard.copyActions.setRewriteMode}
                onRewritePromptChange={dashboard.copyActions.setRewritePrompt}
                onRewriteModelChange={dashboard.copyActions.setRewriteModel}
                onTemperatureChange={dashboard.copyActions.setTemperature}
                onRewriteTextChange={dashboard.copyActions.setRewriteText}
                onTitleChange={dashboard.copyActions.setTitle}
                onTagsChange={dashboard.copyActions.setTags}
                onExtract={handleExtractCopy}
                onRewrite={handleRewriteCopy}
                busyExtract={dashboard.isTaskBusy("content")}
                busyRewrite={dashboard.isTaskBusy("rewrite")}
                extractDisabled={Boolean(extractDisabledReason)}
                rewriteDisabled={Boolean(rewriteDisabledReason)}
                extractDisabledReason={extractDisabledReason}
                rewriteDisabledReason={rewriteDisabledReason}
                llmStatus={llmStatus}
                lastRewriteProvider={latestRewriteProvider}
              />

              <AudioSection
                workspace={dashboard.workspace.current || dashboard.workspace.draft}
                referenceAudioPath={dashboard.audio.referenceAudioPath}
                referenceAudioName={dashboard.audio.referenceAudioName}
                promptText={dashboard.audio.promptText}
                speed={dashboard.audio.speed}
                ultimateClone={dashboard.audio.ultimateClone}
                outputName={dashboard.audio.outputName}
                selectedAudioPath={dashboard.audio.selectedAudioPath}
                playingAudioPath={dashboard.audio.playingAudioPath}
                generatedAudioPath={dashboard.audio.generatedAudioPath}
                latestTask={latestAudioTask}
                assets={dashboard.assets.audio}
                referenceAssets={dashboard.assets.audio.filter((asset) => asset.category === "reference_audio" || asset.kind === "reference_audio" || asset.source === "reference/audio")}
                busyGenerate={dashboard.isTaskBusy("tts")}
                onReferenceAudioPathChange={dashboard.audioActions.setReferenceAudioPath}
                onReferenceAudioNameChange={dashboard.audioActions.setReferenceAudioName}
                onPromptTextChange={dashboard.audioActions.setPromptText}
                onSpeedChange={dashboard.audioActions.setSpeed}
                onUltimateCloneChange={dashboard.audioActions.setUltimateClone}
                onOutputNameChange={dashboard.audioActions.setOutputName}
                onSelectedAudioPathChange={dashboard.audioActions.setSelectedAudioPath}
                onPlayingAudioPathChange={dashboard.audioActions.setPlayingAudioPath}
                onGeneratedAudioPathChange={dashboard.audioActions.setGeneratedAudioPath}
                onUpload={(file) => dashboard.uploadAsset("audio", file)}
                onDeleteAsset={dashboard.deleteAsset}
                onFetchReferenceTranscript={dashboard.fetchReferenceTranscript}
                onGenerate={() =>
                  dashboard.submitTask("tts", {
                    text: audioTextSource,
                    voice: "reference-clone",
                    reference_audio_path: dashboard.audio.referenceAudioPath,
                    ultimate_clone: dashboard.audio.ultimateClone,
                    prompt_text: dashboard.audio.ultimateClone ? dashboard.audio.promptText.trim() : null,
                    speed: dashboard.audio.speed,
                    speaker: null,
                    sample_rate: 22050,
                    workspace: dashboard.workspace.current || dashboard.workspace.draft,
                    output_name: dashboard.audio.outputName,
                  })
                }
                fileUrl={dashboard.fileUrl}
                textSource={audioTextSource}
              />

              <AvatarSection
                workspace={dashboard.workspace.current || dashboard.workspace.draft}
                audioPath={avatarAudioPath}
                referenceVideoPath={dashboard.avatar.referenceVideoPath}
                referenceVideoName={dashboard.avatar.referenceVideoName}
                referenceVideoAssets={dashboard.assets.video.filter((asset) => asset.category === "reference_video" || asset.kind === "reference_video" || asset.source === "reference/video")}
                qualityPreset={dashboard.avatar.qualityPreset}
                beautifyTeeth={dashboard.avatar.beautifyTeeth}
                resultVideoPath={avatarGeneratedVideoPath}
                errorLog={dashboard.avatar.errorLog}
                busyGenerate={dashboard.isTaskBusy("avatar")}
                latestTask={latestAvatarTask}
                onAudioPathChange={dashboard.avatarActions.setAudioPath}
                onReferenceVideoPathChange={dashboard.avatarActions.setReferenceVideoPath}
                onReferenceVideoNameChange={dashboard.avatarActions.setReferenceVideoName}
                onQualityPresetChange={dashboard.avatarActions.setQualityPreset}
                onBeautifyTeethChange={dashboard.avatarActions.setBeautifyTeeth}
                onResultVideoPathChange={dashboard.avatarActions.setResultVideoPath}
                onErrorLogChange={dashboard.avatarActions.setErrorLog}
                onUpload={(file) => dashboard.uploadAsset("video", file)}
                onGenerate={() => {
                  if (avatarDisabledReason) return Promise.resolve(null)
                  const qualityPreset =
                    AVATAR_QUALITY_PRESETS[dashboard.avatar.qualityPreset] ?? AVATAR_QUALITY_PRESETS.clear
                  return dashboard.submitTask("avatar", {
                    audio_path: avatarAudioPath,
                    model_id: "uploaded-avatar",
                    engine: dashboard.avatar.engine,
                    workspace: dashboard.workspace.current || dashboard.workspace.draft,
                    subtitle_path: null,
                    subtitle_style: subtitleStylePayload,
                    reference_video_path: dashboard.avatar.referenceVideoPath,
                    background_video_path: null,
                    batch_size: qualityPreset.batchSize,
                    sync_offset: 0,
                    scale_h: 1.6,
                    scale_w: 3.6,
                    compress_inference: false,
                    beautify_teeth: dashboard.avatar.beautifyTeeth,
                    add_ai_watermark: false,
                    quality_preset: qualityPreset.qualityPreset,
                    max_reference_edge: qualityPreset.maxReferenceEdge,
                  })
                }}
                fileUrl={dashboard.fileUrl}
                generateDisabledReason={avatarDisabledReason}
              />

              <PictureInPictureSection
                workspace={dashboard.workspace.current || dashboard.workspace.draft}
                baseVideoPath={avatarGeneratedVideoPath}
                enabled={dashboard.pictureInPicture.enabled}
                sourcePath={dashboard.pictureInPicture.sourcePath}
                sourceName={dashboard.pictureInPicture.sourceName}
                sourceAssets={pictureInPictureSourceAssets}
                fullDuration={dashboard.pictureInPicture.fullDuration}
                startSec={dashboard.pictureInPicture.startSec}
                endSec={dashboard.pictureInPicture.endSec}
                template={dashboard.pictureInPicture.template}
                position={dashboard.pictureInPicture.position}
                scale={dashboard.pictureInPicture.scale}
                borderWidth={dashboard.pictureInPicture.borderWidth}
                borderColor={dashboard.pictureInPicture.borderColor}
                shadow={dashboard.pictureInPicture.shadow}
                opacity={dashboard.pictureInPicture.opacity}
                animation={dashboard.pictureInPicture.animation}
                fadeDuration={dashboard.pictureInPicture.fadeDuration}
                loop={dashboard.pictureInPicture.loop}
                resultVideoPath={pictureInPictureResultVideoPath}
                statusNote={dashboard.pictureInPicture.statusNote}
                busy={dashboard.isTaskBusy("postprocess")}
                disabledReason={pictureInPictureDisabledReason}
                onEnabledChange={dashboard.pictureInPictureActions.setEnabled}
                onSourcePathChange={dashboard.pictureInPictureActions.setSourcePath}
                onSourceNameChange={dashboard.pictureInPictureActions.setSourceName}
                onFullDurationChange={dashboard.pictureInPictureActions.setFullDuration}
                onStartSecChange={dashboard.pictureInPictureActions.setStartSec}
                onEndSecChange={dashboard.pictureInPictureActions.setEndSec}
                onTemplateChange={dashboard.pictureInPictureActions.setTemplate}
                onPositionChange={dashboard.pictureInPictureActions.setPosition}
                onScaleChange={dashboard.pictureInPictureActions.setScale}
                onBorderWidthChange={dashboard.pictureInPictureActions.setBorderWidth}
                onBorderColorChange={dashboard.pictureInPictureActions.setBorderColor}
                onShadowChange={dashboard.pictureInPictureActions.setShadow}
                onOpacityChange={dashboard.pictureInPictureActions.setOpacity}
                onAnimationChange={dashboard.pictureInPictureActions.setAnimation}
                onFadeDurationChange={dashboard.pictureInPictureActions.setFadeDuration}
                onLoopChange={dashboard.pictureInPictureActions.setLoop}
                onUpload={(kind, file) => dashboard.uploadAsset(kind === "video" ? "pip_video" : "pip_image", file)}
                onGenerate={handleGeneratePictureInPicture}
                fileUrl={dashboard.fileUrl}
              />

              <SubtitleSection
                workspace={dashboard.workspace.current || dashboard.workspace.draft}
                audioPath={subtitleAudioPath}
                videoPath={subtitleTargetVideoPath}
                enabled={dashboard.subtitle.burnIn}
                referenceText={subtitleReferenceText}
                correctWithAI={dashboard.subtitle.correctWithAI && llmConfigured}
                outputName={dashboard.subtitle.outputName}
                style={dashboard.subtitle.style}
                srtText={dashboard.subtitle.srtText}
                generatedSrtPath={dashboard.subtitle.generatedSrtPath}
                generatedAssPath={dashboard.subtitle.generatedAssPath}
                resultVideoPath={subtitleResultVideoPath}
                onAudioPathChange={dashboard.subtitleActions.setAudioPath}
                onEnabledChange={dashboard.subtitleActions.setBurnIn}
                onReferenceTextChange={dashboard.subtitleActions.setReferenceText}
                onCorrectWithAIChange={dashboard.subtitleActions.setCorrectWithAI}
                onOutputNameChange={dashboard.subtitleActions.setOutputName}
                onStyleChange={dashboard.subtitleActions.setStyle}
                onSrtTextChange={dashboard.subtitleActions.setSrtText}
                onGenerate={() => {
                  if (subtitleDisabledReason) return Promise.resolve(null)
                  dashboard.subtitleActions.clearResultVideo()
                  return dashboard.submitTask("subtitle", {
                    audio_path: subtitleAudioPath,
                    video_path: null,
                    reference_text: subtitleReferenceText,
                    style: subtitleStylePayload,
                    burn_in: false,
                    workspace: dashboard.workspace.current || dashboard.workspace.draft,
                    output_name: dashboard.subtitle.outputName,
                    correct_with_ai: dashboard.subtitle.correctWithAI && llmConfigured,
                    max_chars_per_line: 20,
                  })
                }}
                onApplyToVideo={async () => {
                  if (subtitleApplyDisabledReason) return Promise.resolve(null)
                  if (dashboard.subtitle.srtText.trim()) {
                    await dashboard.writeTextFile(dashboard.subtitle.generatedSrtPath, dashboard.subtitle.srtText)
                  }
                  const subtitleOutputBase = dashboard.subtitle.outputName.trim() || "subtitle"
                  return dashboard.submitTask("postprocess", {
                    video_path: subtitleTargetVideoPath,
                    subtitle_path: dashboard.subtitle.generatedSrtPath,
                    subtitle_style: subtitleStylePayload,
                    burn_subtitles: true,
                    workspace: dashboard.workspace.current || dashboard.workspace.draft,
                    output_name: `${subtitleOutputBase}-${compactRunId()}`,
                  })
                }}
                busyGenerate={dashboard.isTaskBusy("subtitle")}
                busyApply={dashboard.isTaskBusy("postprocess")}
                generateDisabledReason={subtitleDisabledReason}
                applyDisabledReason={subtitleApplyDisabledReason}
                llmStatus={llmStatus}
                fileUrl={dashboard.fileUrl}
              />

              <PublishSection
                workspace={dashboard.workspace.current || dashboard.workspace.draft}
                coverPath={dashboard.publish.coverPath}
                coverTitle={dashboard.publish.coverTitle}
                coverHighlight={dashboard.publish.coverHighlight}
                coverTimestampSec={dashboard.publish.coverTimestampSec}
                publishTextPath={dashboard.publish.publishTextPath}
                title={publishTitle}
                description={publishDescription}
                tags={publishTagsText}
                platforms={dashboard.publish.platforms}
                statusNote={dashboard.publish.statusNote}
                videoPath={publishVideoPath}
                fileUrl={dashboard.fileUrl}
                busy={dashboard.isTaskBusy("publish_materials")}
                disabledReason={publishDisabledReason}
                selectedPlatformCount={selectedPublishPlatforms.length}
                onCoverPathChange={dashboard.publishActions.setCoverPath}
                onCoverTitleChange={dashboard.publishActions.setCoverTitle}
                onCoverHighlightChange={dashboard.publishActions.setCoverHighlight}
                onCoverTimestampSecChange={dashboard.publishActions.setCoverTimestampSec}
                onTitleChange={dashboard.publishActions.setTitle}
                onDescriptionChange={dashboard.publishActions.setDescription}
                onTagsChange={dashboard.publishActions.setTags}
                onPlatformChange={dashboard.publishActions.setPlatform}
                onStatusNoteChange={dashboard.publishActions.setStatusNote}
                onUpload={(file) => dashboard.uploadAsset("image", file)}
                onPrepareMaterials={handlePreparePublishMaterials}
                onRenderCover={handleRenderPublishCover}
                onPublish={handlePublish}
              />

              <TasksSection
                currentTask={dashboard.currentTask}
                tasks={dashboard.taskHistory}
                logsByTask={dashboard.taskState.logsByTask}
                onCancelTask={dashboard.cancelTask}
                onRefresh={dashboard.refreshTasks}
                compactDateTime={dashboard.compactDateTime}
                statusTone={dashboard.statusTone}
              />

              <SettingsSection
                health={dashboard.health}
                stateSnapshot={dashboard.stateSnapshot}
                diagnostics={dashboard.diagnostics}
                connection={dashboard.connection}
                settings={dashboard.settings}
                onApiBaseChange={dashboard.settingsActions.setApiBase}
                onApiKeyChange={dashboard.settingsActions.setApiKey}
                onLlmApiBaseChange={dashboard.settingsActions.setLlmApiBase}
                onRewriteModelChange={dashboard.settingsActions.setRewriteModel}
                onSaveLlmSettings={dashboard.saveLlmSettings}
                onTestLlmSettings={dashboard.testLlmSettings}
                onRefreshHealth={dashboard.refreshHealth}
                onRefreshState={dashboard.refreshWorkspace}
                loading={dashboard.loading}
              />
            </main>
          </div>
        </div>
      </div>
    </TooltipProvider>
  )
}

export default App
