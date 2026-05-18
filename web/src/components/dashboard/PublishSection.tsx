import { useEffect, useMemo, useRef, useState } from "react"
import { AlertCircle, CheckCircle2, ImageIcon, Send, Upload, Video, Wand2 } from "lucide-react"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { ScrollArea } from "@/components/ui/scroll-area"
import { Textarea } from "@/components/ui/textarea"
import { PanelShell, TokenBadge } from "./common"
import { assetPathFromUploadResponse, pathBasename } from "@/lib/utils"
import type { AssetRecord } from "@/types"

type Props = {
  workspace: string
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
  assetImages: AssetRecord[]
  videoPath: string
  fileUrl: (path: string) => string
  busy: boolean
  disabledReason: string
  selectedPlatformCount: number
  onCoverPathChange: (value: string) => void
  onCoverTitleChange: (value: string) => void
  onCoverHighlightChange: (value: string) => void
  onCoverTimestampSecChange: (value: number) => void
  onTitleChange: (value: string) => void
  onDescriptionChange: (value: string) => void
  onTagsChange: (value: string) => void
  onPlatformChange: (key: string, value: boolean) => void
  onStatusNoteChange: (value: string) => void
  onUpload: (file: File) => Promise<unknown>
  onPrepareMaterials: () => Promise<unknown> | void
  onRenderCover: () => Promise<unknown> | void
  onPublish: () => Promise<unknown> | void
}

const PLATFORMS = [
  ["douyin", "抖音"],
  ["xiaohongshu", "小红书"],
  ["wechat_channels", "视频号"],
] as const

function formatTagInput(value: string) {
  const seen = new Set<string>()
  const tags: string[] = []
  for (const item of value.split(/[\s,，、#]+/)) {
    const tag = item.trim()
    if (!tag || seen.has(tag)) continue
    seen.add(tag)
    tags.push(`#${tag}`)
    if (tags.length >= 10) break
  }
  return tags.join(" ")
}

export function PublishSection(props: Props) {
  const uploadRef = useRef<HTMLInputElement | null>(null)
  const renderCoverRef = useRef(props.onRenderCover)
  const firstCoverRenderRef = useRef(true)
  const lastCoverRenderKeyRef = useRef("")
  const [uploading, setUploading] = useState(false)
  const [preparing, setPreparing] = useState(false)
  const previewPath = props.coverPath || props.assetImages[0]?.path || ""
  const previewVersion = encodeURIComponent(
    `${previewPath}:${props.videoPath}:${props.coverTimestampSec}:${props.coverTitle}:${props.coverHighlight}`,
  )
  const previewUrl = previewPath ? `${props.fileUrl(previewPath)}&v=${previewVersion}` : ""
  const hasVideo = Boolean(props.videoPath.trim())
  const hasTitle = Boolean(props.title.trim())
  const hasTags = Boolean(props.tags.trim())
  const ready = !props.disabledReason

  const platformSummary = useMemo(
    () =>
      PLATFORMS.filter(([key]) => props.platforms[key])
        .map(([, label]) => label)
        .join("、") || "未选择平台",
    [props.platforms],
  )

  const handleUpload = async (file?: File | null) => {
    if (!file) return
    setUploading(true)
    try {
      const response = await props.onUpload(file)
      const path = assetPathFromUploadResponse(response, ["cover_path", "image_path"]) || file.name
      props.onCoverPathChange(path)
      props.onStatusNoteChange("封面素材已更新。")
    } finally {
      setUploading(false)
    }
  }

  const handlePrepareMaterials = async () => {
    if (!hasVideo) return
    setPreparing(true)
    try {
      await props.onPrepareMaterials()
    } finally {
      setPreparing(false)
    }
  }

  useEffect(() => {
    renderCoverRef.current = props.onRenderCover
  }, [props.onRenderCover])

  useEffect(() => {
    if (!hasVideo) return
    const renderKey = JSON.stringify([
      props.videoPath,
      props.coverTimestampSec,
      props.coverTitle.trim(),
      props.coverHighlight.trim(),
    ])
    if (firstCoverRenderRef.current) {
      firstCoverRenderRef.current = false
      lastCoverRenderKeyRef.current = renderKey
      return
    }
    if (lastCoverRenderKeyRef.current === renderKey) return
    const timer = window.setTimeout(() => {
      lastCoverRenderKeyRef.current = renderKey
      void Promise.resolve(renderCoverRef.current()).catch(() => undefined)
    }, 700)
    return () => window.clearTimeout(timer)
  }, [hasVideo, props.videoPath, props.coverTimestampSec, props.coverTitle, props.coverHighlight])

  return (
    <PanelShell
      id="publish"
      eyebrow="发布"
      title="发布素材与平台提交"
      description="先用 AI 根据文案生成发布数据，再确认封面和平台，最后提交发布任务。"
      actions={
        <>
          <Button variant="amber" size="sm" disabled={!hasVideo} loading={preparing} onClick={() => void handlePrepareMaterials()} title={hasVideo ? "AI 生成发布文案和封面文案，不修改抽帧时间" : "请先生成可发布视频"}>
            <Wand2 className="h-4 w-4" />
            AI生成
          </Button>
          <Button variant="quiet" size="sm" onClick={() => uploadRef.current?.click()} loading={uploading}>
            <Upload className="h-4 w-4" />
            上传封面
          </Button>
          <Button size="sm" variant="amber" disabled={!ready} loading={props.busy} title={props.disabledReason || "在本机浏览器打开选中平台并尝试填充发布资料"} onClick={() => void props.onPublish()}>
            <Send className="h-4 w-4" />
            打开发布页
          </Button>
        </>
      }
    >
      <div className="grid gap-4 xl:grid-cols-[minmax(340px,0.94fr)_minmax(0,1.06fr)]">
        <div className="grid content-start gap-3">
          <div className="grid gap-3 rounded-md border border-border bg-background/55 p-3">
            <div className="flex items-center justify-between gap-3">
              <Label>成片视频</Label>
              <TokenBadge tone={hasVideo ? "success" : "warning"}>{hasVideo ? "已就绪" : "待生成"}</TokenBadge>
            </div>
            <div className="flex items-center gap-3 rounded-md border border-border bg-card px-3 py-3">
              <div className="flex h-10 w-10 items-center justify-center rounded-md border border-border bg-background text-primary">
                <Video className="h-5 w-5" />
              </div>
              <div className="min-w-0 flex-1">
                <p className="truncate text-sm font-semibold">{hasVideo ? pathBasename(props.videoPath) : "暂无可发布视频"}</p>
                <p className="truncate text-xs text-muted-foreground">{hasVideo ? props.videoPath : "请先完成数字人视频或字幕后处理。"}</p>
              </div>
            </div>
          </div>

          <div className="grid gap-3 rounded-md border border-border bg-background/55 p-3">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <Label>封面</Label>
              <div className="flex flex-wrap items-center gap-2">
                <Badge variant="secondary">默认视频抽帧</Badge>
                {previewPath ? <Badge variant="secondary">{pathBasename(previewPath)}</Badge> : <Badge variant="outline">未生成</Badge>}
              </div>
            </div>
            <div className="grid gap-3">
              <div className="mx-auto w-full max-w-[420px] overflow-hidden rounded-md border border-border bg-black shadow-sm">
                {previewPath ? (
                  <img src={previewUrl} alt="封面预览" className="aspect-[9/16] w-full object-contain" />
                ) : (
                  <div className="flex aspect-[9/16] items-center justify-center text-sm text-muted-foreground">
                    <div className="grid justify-items-center gap-2 px-4 text-center">
                      <ImageIcon className="h-6 w-6" />
                      <span>点击 AI生成 后自动抽帧并叠加文案</span>
                    </div>
                  </div>
                )}
              </div>
            </div>
          </div>

          <input
            ref={uploadRef}
            type="file"
            accept="image/*"
            className="hidden"
            onChange={(event) => {
              void handleUpload(event.target.files?.[0])
              event.currentTarget.value = ""
            }}
          />
        </div>

        <div className="grid content-start gap-4">
          <div className="grid gap-3 rounded-md border border-border bg-background/55 p-4">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <Label>封面参数</Label>
              <Badge variant="secondary">修改后自动更新预览</Badge>
            </div>
            <div className="grid gap-3 sm:grid-cols-3">
              <div className="grid gap-2">
                <Label>抽帧时间（秒）</Label>
                <Input
                  type="number"
                  min={0}
                  step={0.1}
                  value={props.coverTimestampSec}
                  onChange={(event) => props.onCoverTimestampSecChange(Number(event.target.value) || 0)}
                />
              </div>
              <div className="grid gap-2">
                <Label>封面标题</Label>
                <Input value={props.coverTitle} onChange={(event) => props.onCoverTitleChange(event.target.value)} placeholder="AI 自动生成，可修改" />
              </div>
              <div className="grid gap-2">
                <Label>高亮词</Label>
                <Input value={props.coverHighlight} onChange={(event) => props.onCoverHighlightChange(event.target.value)} placeholder="最多 4 个字" />
              </div>
            </div>
          </div>

          <div className="grid gap-3 rounded-md border border-border bg-background/55 p-4">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div className="min-w-0">
                <Label>发布文案</Label>
                <p className="mt-1 text-xs text-muted-foreground">根据文案内容一次生成标题、标签、封面标题、高亮词和固定发布文件。</p>
              </div>
            </div>
            <div className="grid gap-3 sm:grid-cols-2">
              <div className="grid gap-2">
                <Label>标题</Label>
                <Input value={props.title} onChange={(event) => props.onTitleChange(event.target.value)} placeholder="发布标题" />
              </div>
              <div className="grid gap-2">
                <Label>标签</Label>
                <Input
                  value={props.tags}
                  onChange={(event) => props.onTagsChange(event.target.value)}
                  onBlur={(event) => props.onTagsChange(formatTagInput(event.currentTarget.value))}
                  placeholder="#短视频 #教程"
                />
              </div>
            </div>
            <div className="grid gap-2">
              <Label>描述</Label>
              <Textarea
                value={props.description}
                onChange={(event) => props.onDescriptionChange(event.target.value)}
                placeholder="可选。AI 会优先使用上游文案内容，平台发布时优先使用标题和标签。"
                className="min-h-[96px]"
              />
            </div>
          </div>

          <div className="grid gap-3 rounded-md border border-border bg-background/55 p-4">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <Label>发布平台</Label>
              <Badge variant={props.selectedPlatformCount > 0 ? "secondary" : "warning"}>{platformSummary}</Badge>
            </div>
            <div className="grid gap-2">
              {PLATFORMS.map(([key, label]) => {
                const checked = Boolean(props.platforms[key])
                return (
                  <div key={key} className="flex items-center justify-between gap-3 rounded-md border border-border bg-card px-3 py-2">
                    <label className="flex min-w-0 items-center gap-3 text-sm">
                      <Checkbox checked={checked} onCheckedChange={(value) => props.onPlatformChange(key, Boolean(value))} />
                      <span className="font-medium">{label}</span>
                    </label>
                    <Badge variant={checked ? "success" : "outline"}>{checked ? "已选择" : "未选择"}</Badge>
                  </div>
                )
              })}
            </div>
          </div>

          <div className="grid gap-2">
            <div className="flex flex-wrap items-center justify-between gap-2 rounded-md border border-border bg-background/55 px-4 py-3">
              <Label>发布状态</Label>
              <div className="flex flex-wrap items-center gap-2">
                <Badge variant={hasTitle ? "success" : "warning"}>标题</Badge>
                <Badge variant={hasTags ? "success" : "warning"}>标签</Badge>
                <Badge variant={ready ? "success" : "warning"}>{ready ? "可以提交" : "等待补齐"}</Badge>
                {props.publishTextPath ? <Badge variant="secondary">output.txt</Badge> : null}
              </div>
            </div>
            <ScrollArea className="h-[132px] rounded-md border border-border bg-background/55">
              <div className="grid gap-2 p-3 text-sm text-muted-foreground">
                {props.disabledReason ? (
                  <div className="flex items-start gap-2 rounded-md border border-warning/30 bg-warning/10 px-3 py-2 text-warning-foreground">
                    <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
                    <span>{props.disabledReason}</span>
                  </div>
                ) : null}
                <div className="flex items-start gap-2 rounded-md border border-border bg-card px-3 py-2">
                  <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-success" />
                  <span>{props.statusNote || "等待发布任务。"}</span>
                </div>
              </div>
            </ScrollArea>
          </div>
        </div>
      </div>
    </PanelShell>
  )
}
