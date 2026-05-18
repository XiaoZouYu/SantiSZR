import { useEffect, useMemo, useRef, useState } from "react"
import { Check, MicVocal, Pause, Play, Trash2, Upload } from "lucide-react"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Slider } from "@/components/ui/slider"
import { Switch } from "@/components/ui/switch"
import { Textarea } from "@/components/ui/textarea"
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip"
import { assetPathFromUploadResponse, pathBasename } from "@/lib/utils"
import type { AssetRecord, TaskRecord } from "@/types"
import { PanelShell, TokenBadge } from "./common"

type Props = {
  workspace: string
  referenceAudioPath: string
  referenceAudioName: string
  promptText: string
  speed: number
  ultimateClone: boolean
  outputName: string
  selectedAudioPath: string
  playingAudioPath: string
  generatedAudioPath: string
  latestTask: TaskRecord | null
  assets: AssetRecord[]
  referenceAssets: AssetRecord[]
  busyGenerate: boolean
  onReferenceAudioPathChange: (value: string) => void
  onReferenceAudioNameChange: (value: string) => void
  onPromptTextChange: (value: string) => void
  onSpeedChange: (value: number) => void
  onUltimateCloneChange: (value: boolean) => void
  onOutputNameChange: (value: string) => void
  onSelectedAudioPathChange: (value: string) => void
  onPlayingAudioPathChange: (value: string) => void
  onGeneratedAudioPathChange: (value: string) => void
  onUpload: (file: File) => Promise<unknown>
  onGenerate: () => Promise<unknown>
  onDeleteAsset: (path: string) => Promise<unknown>
  onFetchReferenceTranscript: (path: string) => Promise<string>
  fileUrl: (path: string) => string
  textSource: string
}

function assetSortKey(asset: AssetRecord) {
  return asset.modified_at ?? asset.updated_at ?? asset.created_at ?? ""
}

function formatClockTime(seconds?: number | null) {
  const totalSeconds =
    typeof seconds === "number" && Number.isFinite(seconds) && seconds > 0 ? Math.round(seconds) : 0
  const hours = Math.floor(totalSeconds / 3600)
  const minutes = Math.floor((totalSeconds % 3600) / 60)
  const remainingSeconds = totalSeconds % 60
  const paddedSeconds = remainingSeconds.toString().padStart(2, "0")
  if (hours > 0) return `${hours}:${minutes.toString().padStart(2, "0")}:${paddedSeconds}`
  return `${minutes}:${paddedSeconds}`
}

function formatDuration(duration?: number | null) {
  if (typeof duration !== "number" || !Number.isFinite(duration) || duration <= 0) return "读取中"
  return formatClockTime(duration)
}

function clampTime(value: number, duration?: number | null) {
  const safeValue = Number.isFinite(value) ? value : 0
  const safeDuration = typeof duration === "number" && Number.isFinite(duration) && duration > 0 ? duration : 0
  if (!safeDuration) return Math.max(0, safeValue)
  return Math.min(safeDuration, Math.max(0, safeValue))
}

export function AudioSection(props: Props) {
  const uploadRef = useRef<HTMLInputElement | null>(null)
  const audioRef = useRef<HTMLAudioElement | null>(null)
  const playbackPathRef = useRef("")
  const metadataProbesRef = useRef<Record<string, HTMLAudioElement>>({})
  const timeByPathRef = useRef<Record<string, number>>({})
  const [uploading, setUploading] = useState(false)
  const [volumeByPath, setVolumeByPath] = useState<Record<string, number>>({})
  const [durationByPath, setDurationByPath] = useState<Record<string, number>>({})
  const [timeByPath, setTimeByPath] = useState<Record<string, number>>({})
  const [transcriptLoading, setTranscriptLoading] = useState(false)
  const [transcriptMessage, setTranscriptMessage] = useState("")
  const transcriptRequestRef = useRef(0)

  const generateDisabledReason = !props.textSource.trim()
    ? "请先准备待生成文案。"
    : !props.referenceAudioPath.trim()
      ? "请先选择参考音频。"
      : props.ultimateClone && transcriptLoading
        ? "正在识别参考音频文案，请稍等。"
        : props.ultimateClone && !props.promptText.trim()
          ? "极致克隆会先自动识别参考音频文案。"
        : ""

  const latestTaskStatus = `${props.latestTask?.status ?? ""}`.toLowerCase()
  const latestTaskPayload = props.latestTask?.payload ? (props.latestTask.payload as Record<string, unknown>) : null
  const latestTaskResult =
    props.latestTask?.result && typeof props.latestTask.result === "object" && !Array.isArray(props.latestTask.result)
      ? (props.latestTask.result as Record<string, unknown>)
      : null
  const latestTaskPath =
    latestTaskStatus === "succeeded"
      ? (typeof latestTaskPayload?.audio_path === "string" && latestTaskPayload.audio_path.trim()
          ? latestTaskPayload.audio_path
          : typeof latestTaskPayload?.path === "string" && latestTaskPayload.path.trim()
            ? latestTaskPayload.path
            : typeof latestTaskResult?.audio_path === "string" && latestTaskResult.audio_path.trim()
              ? latestTaskResult.audio_path
              : typeof latestTaskResult?.path === "string" && latestTaskResult.path.trim()
                ? latestTaskResult.path
                : props.generatedAudioPath)
      : !props.latestTask && props.generatedAudioPath
        ? props.generatedAudioPath
        : ""
  const latestTaskTone =
    latestTaskStatus === "succeeded"
      ? "success"
      : latestTaskStatus === "failed" || latestTaskStatus === "cancelled"
        ? "destructive"
        : latestTaskStatus === "running" || latestTaskStatus === "pending" || latestTaskStatus === "queued"
          ? "warning"
          : props.generatedAudioPath
            ? "success"
            : "idle"
  const latestTaskLabel =
    latestTaskStatus === "succeeded"
      ? "生成成功"
      : latestTaskStatus === "failed"
        ? "生成失败"
        : latestTaskStatus === "cancelled"
          ? "已取消"
          : latestTaskStatus === "running" || latestTaskStatus === "pending" || latestTaskStatus === "queued"
            ? "生成中"
            : props.generatedAudioPath
              ? "已有输出"
              : "等待生成"
  const latestTaskMessage =
    props.latestTask?.error?.message ||
    props.latestTask?.message ||
    (latestTaskStatus === "succeeded"
      ? "音频已生成完成。"
      : latestTaskStatus === "failed"
        ? "音频生成失败。"
        : latestTaskStatus === "cancelled"
          ? "任务已取消。"
          : latestTaskStatus === "running" || latestTaskStatus === "pending" || latestTaskStatus === "queued"
            ? "正在生成音频，请稍等。"
            : props.generatedAudioPath
              ? "当前工作空间里已有一份可用音频。"
              : "点击生成后，这里会显示结果和输出位置。")
  const audioList = useMemo(
    () =>
      [...props.assets]
        .filter((asset) => asset.source !== "media-library" && asset.category !== "reference_audio" && asset.kind !== "reference_audio")
        .sort((a, b) => assetSortKey(a).localeCompare(assetSortKey(b)) || (a.path ?? "").localeCompare(b.path ?? "")),
    [props.assets],
  )
  const referenceAudioList = useMemo(
    () =>
      [...props.referenceAssets].sort(
        (a, b) => assetSortKey(a).localeCompare(assetSortKey(b)) || (a.path ?? "").localeCompare(b.path ?? ""),
      ),
    [props.referenceAssets],
  )
  const visibleTaskPath =
    latestTaskPath || (latestTaskStatus === "succeeded" && audioList.length > 0 ? audioList[audioList.length - 1].path : "")
  const fileUrl = props.fileUrl

  const volumeFor = (path: string) => volumeByPath[path] ?? 80

  const fetchReferenceTranscript = async (path = props.referenceAudioPath) => {
    const referencePath = path.trim()
    const requestId = transcriptRequestRef.current + 1
    transcriptRequestRef.current = requestId
    if (!referencePath) {
      setTranscriptMessage("请先选择参考音频。")
      return
    }
    setTranscriptLoading(true)
    setTranscriptMessage("正在识别参考音频文案...")
    try {
      const transcript = await props.onFetchReferenceTranscript(referencePath)
      if (transcriptRequestRef.current !== requestId) return
      setTranscriptMessage(transcript ? "已自动识别，可直接修改。" : "没有识别到文案，请手动填写。")
    } catch (error) {
      if (transcriptRequestRef.current !== requestId) return
      setTranscriptMessage(error instanceof Error ? error.message : "参考音频文案识别失败，请手动填写。")
    } finally {
      if (transcriptRequestRef.current === requestId) {
        setTranscriptLoading(false)
      }
    }
  }

  const setAssetVolume = (path: string, value: number) => {
    const nextVolume = Math.min(100, Math.max(0, Math.round(value)))
    setVolumeByPath((prev) => (prev[path] === nextVolume ? prev : { ...prev, [path]: nextVolume }))
    if (props.playingAudioPath === path && audioRef.current) {
      audioRef.current.volume = nextVolume / 100
    }
  }

  const setAssetTime = (path: string, value: number, duration?: number | null) => {
    const nextTime = clampTime(value, duration)
    timeByPathRef.current = { ...timeByPathRef.current, [path]: nextTime }
    setTimeByPath((prev) =>
      Math.abs((prev[path] ?? 0) - nextTime) < 0.1 ? prev : { ...prev, [path]: nextTime },
    )
  }

  const seekAsset = (asset: AssetRecord, value: number) => {
    const audio = audioRef.current
    const path = asset.path
    const duration = asset.duration_sec || durationByPath[path]
    const nextTime = clampTime(value, duration)
    setAssetTime(path, nextTime, duration)
    props.onSelectedAudioPathChange(path)
    if (!audio) return

    if (playbackPathRef.current !== path) {
      playbackPathRef.current = path
      audio.src = asset.url || props.fileUrl(path)
      audio.volume = volumeFor(path) / 100
    }

    const applySeek = () => {
      const audioDuration = Number.isFinite(audio.duration) && audio.duration > 0 ? audio.duration : duration
      audio.currentTime = clampTime(nextTime, audioDuration)
    }

    if (audio.readyState >= 1) {
      applySeek()
    } else {
      audio.addEventListener("loadedmetadata", applySeek, { once: true })
      audio.load()
    }
  }

  useEffect(() => {
    timeByPathRef.current = timeByPath
  }, [timeByPath])

  useEffect(() => {
    if (!props.ultimateClone || !props.referenceAudioPath.trim()) return
    void fetchReferenceTranscript(props.referenceAudioPath)
  }, [props.ultimateClone, props.referenceAudioPath])

  useEffect(() => {
    let cancelled = false
    const pendingAssets = audioList.filter((asset) => !asset.duration_sec && !durationByPath[asset.path])
    const probes: Array<{ path: string; probe: HTMLAudioElement }> = []

    for (const asset of pendingAssets) {
      const probe = new Audio()
      probes.push({ path: asset.path, probe })
      metadataProbesRef.current[asset.path]?.removeAttribute("src")
      metadataProbesRef.current[asset.path]?.load()
      metadataProbesRef.current[asset.path] = probe

      const releaseProbe = () => {
        if (metadataProbesRef.current[asset.path] === probe) {
          delete metadataProbesRef.current[asset.path]
        }
        probe.removeAttribute("src")
        probe.load()
      }
      probe.preload = "metadata"
      probe.onloadedmetadata = () => {
        if (!cancelled && Number.isFinite(probe.duration) && probe.duration > 0) {
          setDurationByPath((prev) =>
            Math.round(prev[asset.path] ?? 0) === Math.round(probe.duration)
              ? prev
              : { ...prev, [asset.path]: probe.duration },
          )
        }
        releaseProbe()
      }
      probe.onerror = releaseProbe
      probe.src = asset.url || fileUrl(asset.path)
    }

    return () => {
      cancelled = true
      for (const { path, probe } of probes) {
        probe.onloadedmetadata = null
        probe.onerror = null
        if (metadataProbesRef.current[path] === probe) {
          delete metadataProbesRef.current[path]
        }
        probe.removeAttribute("src")
        probe.load()
      }
    }
  }, [audioList, durationByPath, fileUrl])

  useEffect(() => {
    const audio = audioRef.current
    if (!audio) return
    const handleEnded = () => {
      const path = playbackPathRef.current
      if (path) setAssetTime(path, 0, durationByPath[path])
      props.onPlayingAudioPathChange("")
    }
    const handlePause = () => {
      if (audio.ended) return
      if (props.playingAudioPath) {
        props.onPlayingAudioPathChange("")
      }
    }
    const handleTimeUpdate = () => {
      const path = playbackPathRef.current
      if (!path) return
      if (Number.isFinite(audio.duration) && audio.duration > 0) {
        setDurationByPath((prev) =>
          Math.round(prev[path] ?? 0) === Math.round(audio.duration) ? prev : { ...prev, [path]: audio.duration },
        )
      }
      setAssetTime(path, audio.currentTime, audio.duration)
    }
    audio.addEventListener("ended", handleEnded)
    audio.addEventListener("pause", handlePause)
    audio.addEventListener("timeupdate", handleTimeUpdate)
    return () => {
      audio.removeEventListener("ended", handleEnded)
      audio.removeEventListener("pause", handlePause)
      audio.removeEventListener("timeupdate", handleTimeUpdate)
    }
  }, [durationByPath, props.onPlayingAudioPathChange, props.playingAudioPath])

  const togglePlay = async (asset: AssetRecord) => {
    const audio = audioRef.current
    if (!audio) return
    const path = asset.path
    if (props.playingAudioPath === path) {
      audio.pause()
      props.onPlayingAudioPathChange("")
      return
    }
    props.onSelectedAudioPathChange(path)
    playbackPathRef.current = path
    audio.src = asset.url || props.fileUrl(path)
    audio.volume = volumeFor(path) / 100
    const duration = asset.duration_sec || durationByPath[path]
    const requestedStart = timeByPathRef.current[path] ?? 0
    const startTime = duration && requestedStart >= duration - 0.2 ? 0 : requestedStart
    const applyStartTime = () => {
      const audioDuration = Number.isFinite(audio.duration) && audio.duration > 0 ? audio.duration : duration
      audio.currentTime = clampTime(startTime, audioDuration)
    }
    if (startTime > 0) {
      if (audio.readyState >= 1) {
        applyStartTime()
      } else {
        audio.addEventListener("loadedmetadata", applyStartTime, { once: true })
      }
    }
    try {
      await audio.play()
      props.onPlayingAudioPathChange(path)
    } catch {
      props.onPlayingAudioPathChange("")
    }
  }

  const handleDelete = async (asset: AssetRecord) => {
    const audio = audioRef.current
    const probe = metadataProbesRef.current[asset.path]
    if (probe) {
      probe.onloadedmetadata = null
      probe.onerror = null
      probe.removeAttribute("src")
      probe.load()
      delete metadataProbesRef.current[asset.path]
    }
    if (props.playingAudioPath === asset.path || playbackPathRef.current === asset.path) {
      audio?.pause()
      audio?.removeAttribute("src")
      audio?.load()
      playbackPathRef.current = ""
      props.onPlayingAudioPathChange("")
    }
    await props.onDeleteAsset(asset.path)
    setVolumeByPath((prev) => {
      const next = { ...prev }
      delete next[asset.path]
      return next
    })
    setDurationByPath((prev) => {
      const next = { ...prev }
      delete next[asset.path]
      return next
    })
    setTimeByPath((prev) => {
      const next = { ...prev }
      delete next[asset.path]
      timeByPathRef.current = next
      return next
    })
  }

  const handleUpload = async (file?: File | null) => {
    if (!file) return
    setUploading(true)
    try {
      const response = await props.onUpload(file)
      const uploadedPath = assetPathFromUploadResponse(response, ["audio_path"]) || file.name
      props.onReferenceAudioPathChange(uploadedPath)
      props.onSelectedAudioPathChange(uploadedPath)
      props.onReferenceAudioNameChange(file.name)
    } finally {
      setUploading(false)
    }
  }

  return (
    <PanelShell
      id="audio"
      eyebrow="音频模块"
      title="参考音频、极致克隆与生成列表"
      description="上传参考音频后生成新音频。生成列表只显示当前工作空间里的内容，播放只改变选中状态，不会打乱列表顺序。"
    >
      <div className="grid gap-4">
        <div className="grid gap-4 rounded-none border-0 bg-transparent p-0">
          <div className="grid gap-3 xl:grid-cols-[minmax(0,1fr)_auto_auto] xl:items-end">
            <div className="grid gap-2">
              <Label>参考音频</Label>
              <Select
                value={props.referenceAudioPath || undefined}
                onValueChange={(value) => {
                  const asset = referenceAudioList.find((item) => item.path === value)
                  props.onReferenceAudioPathChange(value)
                  props.onReferenceAudioNameChange(asset?.name || pathBasename(value))
                  props.onSelectedAudioPathChange(value)
                }}
              >
                <SelectTrigger>
                  <SelectValue placeholder={referenceAudioList.length ? "选择参考音频" : "先上传参考音频"} />
                </SelectTrigger>
                <SelectContent>
                  {referenceAudioList.map((asset) => (
                    <SelectItem key={asset.id || asset.path} value={asset.path}>
                      {asset.name || pathBasename(asset.path)}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <input
              ref={uploadRef}
              type="file"
              accept="audio/*"
              className="hidden"
              onChange={(event) => {
                void handleUpload(event.target.files?.[0])
                event.currentTarget.value = ""
              }}
            />
            <Button variant="quiet" onClick={() => uploadRef.current?.click()} loading={uploading}>
              <Upload className="h-4 w-4" />
              上传参考音频
            </Button>
            <Button
              onClick={() => void props.onGenerate()}
              loading={props.busyGenerate}
              disabled={Boolean(generateDisabledReason)}
              title={generateDisabledReason || "生成音频"}
            >
              <MicVocal className="h-4 w-4" />
              生成音频
            </Button>
          </div>

          <div className="grid gap-3 rounded-md border border-border bg-background/55 p-4">
            <div className="flex flex-wrap items-center gap-2">
              <TokenBadge tone={props.referenceAudioPath ? "success" : "warning"}>
                {props.referenceAudioPath ? "参考音频已选" : "等待选择参考音频"}
              </TokenBadge>
              <TokenBadge tone={latestTaskTone}>{latestTaskLabel}</TokenBadge>
              {visibleTaskPath ? <Badge variant="secondary">输出: {pathBasename(visibleTaskPath)}</Badge> : null}
            </div>
            <div className="grid gap-1 text-sm text-muted-foreground">
              <p>{latestTaskMessage}</p>
              {visibleTaskPath ? <p className="truncate-path text-xs">位置: {visibleTaskPath}</p> : null}
            </div>
            <div className="grid gap-3">
              <div className="grid gap-2">
                <Label>输出名称</Label>
                <Input value={props.outputName} onChange={(event) => props.onOutputNameChange(event.target.value)} placeholder="narration" />
              </div>
            </div>
            <div className="grid gap-3 sm:grid-cols-2 sm:items-stretch">
              <div className="flex min-h-[58px] items-center justify-between gap-3 rounded-md border border-border bg-card px-3 py-2">
                <div>
                  <p className="text-sm font-medium">极致克隆</p>
                  <p className="text-xs text-muted-foreground">开启后使用参考音频文案做精准匹配。</p>
                </div>
                <Switch
                  checked={props.ultimateClone}
                  onCheckedChange={props.onUltimateCloneChange}
                />
              </div>
              <div className="flex min-h-[58px] items-center gap-3 rounded-md border border-border bg-card px-3 py-2">
                <Label className="shrink-0">速度</Label>
                <Slider
                  className="min-w-0 flex-1"
                  min={0.5}
                  max={2}
                  step={0.05}
                  value={[props.speed]}
                  onValueChange={(value) => props.onSpeedChange(value[0] ?? props.speed)}
                />
                <Badge variant="secondary" className="w-16 justify-center tabular-nums">
                  {props.speed.toFixed(2)}x
                </Badge>
              </div>
            </div>
            {props.ultimateClone ? (
              <div className="grid gap-2 rounded-md border border-warning/30 bg-warning/10 p-3">
                <Label>参考音频文案（精准匹配）</Label>
                <Textarea
                  value={props.promptText}
                  onChange={(event) => props.onPromptTextChange(event.target.value)}
                  placeholder={transcriptLoading ? "正在识别参考音频文案..." : "识别后会自动填入，你也可以手动修改"}
                  className="min-h-[92px] bg-background/80"
                />
                <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-muted-foreground">
                  <span>{transcriptMessage || "开启后会自动识别参考音频文案，内容需要和参考音频对应。"}</span>
                  <Button
                    type="button"
                    variant="ghost"
                    size="sm"
                    className="h-7 px-2 text-xs"
                    loading={transcriptLoading}
                    disabled={!props.referenceAudioPath.trim()}
                    onClick={() => void fetchReferenceTranscript()}
                  >
                    重新识别
                  </Button>
                </div>
              </div>
            ) : null}
            {generateDisabledReason ? (
              <div className="rounded-md border border-warning/30 bg-warning/10 px-3 py-2 text-xs leading-5 text-warning-foreground">
                {generateDisabledReason}
              </div>
            ) : null}
          </div>

          <div className="grid gap-2">
            <Label>待生成文本</Label>
            <Textarea value={props.textSource} readOnly className="min-h-[120px] font-mono text-xs leading-6" />
          </div>
        </div>

        <div className="grid gap-3 rounded-md border border-border bg-background/60 p-4">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="flex items-center gap-3">
              <p className="control-label">生成音频列表</p>
              <Badge variant="secondary">{audioList.length}</Badge>
            </div>
          </div>
          <TooltipProvider delayDuration={120}>
            <div className="grid gap-2">
              {audioList.length > 0 ? (
                audioList.map((asset) => {
                  const active = props.selectedAudioPath === asset.path
                  const playing = props.playingAudioPath === asset.path
                  const reference = props.referenceAudioPath === asset.path
                  const generated = props.generatedAudioPath === asset.path
                  const timestamp = assetSortKey(asset)
                  const itemVolume = volumeFor(asset.path)
                  const itemDuration = asset.duration_sec || durationByPath[asset.path]
                  const itemTime = timeByPath[asset.path] ?? 0
                  const audioSource = asset.url || props.fileUrl(asset.path)

                  return (
                    <div
                      key={asset.id || asset.path}
                      className={`relative rounded-md border p-3 transition-colors hover:bg-secondary/40 ${
                        active ? "border-primary/50 bg-primary/5 shadow-sm" : "border-border bg-card"
                      }`}
                      onClick={() => props.onSelectedAudioPathChange(asset.path)}
                    >
                      {active ? (
                        <span className="absolute right-3 top-3 inline-flex h-5 w-5 items-center justify-center rounded-full bg-primary text-primary-foreground shadow-sm">
                          <Check className="h-3.5 w-3.5" />
                        </span>
                      ) : null}
                      <div className="grid gap-3 pr-8 xl:grid-cols-[minmax(0,1fr)_minmax(520px,0.95fr)_auto] xl:items-center">
                        <div className="flex min-w-0 items-center gap-3">
                          <Tooltip>
                            <TooltipTrigger asChild>
                              <Button
                                size="icon"
                                variant="quiet"
                                aria-label={`${playing ? "暂停播放" : "播放"} ${asset.name || pathBasename(asset.path)}`}
                                title={`${playing ? "暂停播放" : "播放"} ${asset.name || pathBasename(asset.path)}`}
                                onClick={(event) => {
                                  event.stopPropagation()
                                  void togglePlay(asset)
                                }}
                              >
                                {playing ? <Pause className="h-4 w-4" /> : <Play className="h-4 w-4" />}
                              </Button>
                            </TooltipTrigger>
                            <TooltipContent>{playing ? "暂停播放" : "播放音频"}</TooltipContent>
                          </Tooltip>
                          <div className="min-w-0">
                            <div className="flex min-w-0 flex-wrap items-center gap-2">
                              <span className="max-w-full truncate font-medium">{asset.name || pathBasename(asset.path)}</span>
                              {generated ? <Badge variant="secondary">生成</Badge> : null}
                              {reference ? <Badge variant="outline">参考</Badge> : null}
                            </div>
                            <div className="mt-1 flex min-w-0 items-center gap-2 text-xs text-muted-foreground">
                              <span className="min-w-0 truncate-path">{asset.path}</span>
                              <span className="shrink-0">·</span>
                              <span className="shrink-0">{formatDuration(itemDuration)}</span>
                              <span className="shrink-0">·</span>
                              <span className="shrink-0">{timestamp ? new Date(timestamp).toLocaleString("zh-CN") : "—"}</span>
                            </div>
                          </div>
                        </div>
                        <div
                          className="grid min-w-0 grid-cols-[32px_40px_minmax(120px,1fr)_42px_32px_minmax(120px,1fr)_40px] items-center gap-2 text-xs text-muted-foreground"
                          onClick={(event) => event.stopPropagation()}
                        >
                          <span className="font-medium text-foreground">进度</span>
                          <span className="text-right tabular-nums">{formatClockTime(itemTime)}</span>
                          <Slider
                            className="min-w-0"
                            min={0}
                            max={itemDuration && itemDuration > 0 ? itemDuration : 1}
                            step={0.1}
                            value={[clampTime(itemTime, itemDuration || 1)]}
                            disabled={!itemDuration}
                            onValueChange={(value) => setAssetTime(asset.path, value[0] ?? itemTime, itemDuration)}
                            onValueCommit={(value) => seekAsset(asset, value[0] ?? itemTime)}
                          />
                          <span className="tabular-nums">{itemDuration ? formatClockTime(itemDuration) : "--:--"}</span>
                          <span className="font-medium text-foreground">音量</span>
                          <Slider
                            className="min-w-0"
                            min={0}
                            max={100}
                            step={1}
                            value={[itemVolume]}
                            onValueChange={(value) => setAssetVolume(asset.path, value[0] ?? itemVolume)}
                          />
                          <span className="w-10 text-right tabular-nums">{itemVolume}%</span>
                        </div>
                        <div className="flex items-center justify-end" onClick={(event) => event.stopPropagation()}>
                          <Tooltip>
                            <TooltipTrigger asChild>
                              <Button
                                size="icon"
                                variant="ghost"
                                className="text-destructive hover:bg-destructive/10 hover:text-destructive"
                                aria-label={`删除 ${asset.name || pathBasename(asset.path)}`}
                                title={`删除 ${asset.name || pathBasename(asset.path)}`}
                                onClick={() => void handleDelete(asset)}
                              >
                                <Trash2 className="h-4 w-4" />
                              </Button>
                            </TooltipTrigger>
                            <TooltipContent>删除音频</TooltipContent>
                          </Tooltip>
                        </div>
                      </div>
                    </div>
                  )
                })
              ) : (
                <div className="rounded-md border border-dashed border-border bg-card px-3 py-10 text-center text-sm text-muted-foreground">
                  当前工作空间还没有生成音频或上传素材。
                </div>
              )}
            </div>
          </TooltipProvider>

          <audio ref={audioRef} className="hidden" />
          <div className="rounded-md border border-border bg-card px-3 py-2 text-sm text-muted-foreground">
            当前播放：{props.playingAudioPath ? pathBasename(props.playingAudioPath) : "未播放"}，当前选中：
            {props.selectedAudioPath ? pathBasename(props.selectedAudioPath) : "未选择"}
          </div>
        </div>
      </div>
    </PanelShell>
  )
}
