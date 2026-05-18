import { useRef, useState } from "react"
import { Smartphone, Upload, Video } from "lucide-react"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Textarea } from "@/components/ui/textarea"
import { PanelShell, TokenBadge } from "./common"
import { assetPathFromUploadResponse, pathBasename } from "@/lib/utils"
import type { AssetRecord, TaskRecord } from "@/types"

type Props = {
  workspace: string
  audioPath: string
  referenceVideoPath: string
  referenceVideoName: string
  referenceVideoAssets: AssetRecord[]
  engine: string
  resolution: string
  fps: number
  overlayText: string
  resultVideoPath: string
  errorLog: string[]
  copyTitle: string
  busyGenerate: boolean
  latestTask: TaskRecord | null
  onAudioPathChange: (value: string) => void
  onReferenceVideoPathChange: (value: string) => void
  onReferenceVideoNameChange: (value: string) => void
  onEngineChange: (value: string) => void
  onResolutionChange: (value: string) => void
  onFpsChange: (value: number) => void
  onOverlayTextChange: (value: string) => void
  onResultVideoPathChange: (value: string) => void
  onErrorLogChange: (value: string[]) => void
  onUpload: (file: File) => Promise<unknown>
  onGenerate: () => Promise<unknown>
  fileUrl: (path: string) => string
  generateDisabledReason?: string
}

export function AvatarSection(props: Props) {
  const uploadRef = useRef<HTMLInputElement | null>(null)
  const [uploading, setUploading] = useState(false)

  const handleUpload = async (file?: File | null) => {
    if (!file) return
    setUploading(true)
    try {
      const response = await props.onUpload(file)
      const path = assetPathFromUploadResponse(response, ["video_path"]) || file.name
      props.onReferenceVideoPathChange(path)
      props.onReferenceVideoNameChange(file.name)
    } finally {
      setUploading(false)
    }
  }

  const generateDisabledReason = props.generateDisabledReason || ""
  const latestTaskStatus = `${props.latestTask?.status ?? ""}`.toLowerCase()
  const latestTaskRecord =
    props.latestTask?.result && typeof props.latestTask.result === "object" && !Array.isArray(props.latestTask.result)
      ? (props.latestTask.result as Record<string, unknown>)
      : null
  const latestAvatarResult =
    latestTaskRecord?.avatar && typeof latestTaskRecord.avatar === "object" && !Array.isArray(latestTaskRecord.avatar)
      ? (latestTaskRecord.avatar as Record<string, unknown>)
      : latestTaskRecord
  const latestResultPath =
    props.resultVideoPath ||
    (latestTaskStatus === "succeeded" && typeof latestAvatarResult?.video_path === "string" ? latestAvatarResult.video_path : "")
  const previewPath = latestResultPath || props.referenceVideoPath
  const statusTone =
    latestTaskStatus === "succeeded"
      ? "success"
      : latestTaskStatus === "failed" || latestTaskStatus === "cancelled"
        ? "destructive"
        : props.busyGenerate || latestTaskStatus === "running" || latestTaskStatus === "pending" || latestTaskStatus === "queued"
          ? "warning"
          : props.resultVideoPath
            ? "success"
            : "idle"
  const statusLabel =
    latestTaskStatus === "succeeded"
      ? "生成成功"
      : latestTaskStatus === "failed"
        ? "生成失败"
        : latestTaskStatus === "cancelled"
          ? "已取消"
          : props.busyGenerate || latestTaskStatus === "running" || latestTaskStatus === "pending" || latestTaskStatus === "queued"
            ? "生成中"
            : props.resultVideoPath
              ? "已有输出"
              : "等待生成"
  const statusMessage =
    props.latestTask?.error?.message ||
    props.latestTask?.message ||
    (latestTaskStatus === "succeeded"
      ? "数字人视频已生成完成，右侧预览已切换到生成结果。"
      : latestTaskStatus === "failed"
        ? "数字人视频生成失败。"
        : props.busyGenerate || latestTaskStatus === "running" || latestTaskStatus === "pending" || latestTaskStatus === "queued"
          ? "正在生成数字人视频，请稍等。"
          : latestResultPath
            ? "右侧正在预览生成结果。"
            : "点击生成后，这里会显示结果和输出位置。")

  return (
    <PanelShell
      id="avatar"
      eyebrow="数字人模块"
      title="参考视频、音频驱动与结果预览"
      description="数字人只根据音频和参考视频生成无字幕视频；字幕在下方模块作为后处理加到视频上。"
    >
      <div className="grid items-start gap-4 xl:grid-cols-[minmax(0,0.95fr)_minmax(0,1.05fr)]">
        <div className="grid content-start gap-4">
          <div className="grid gap-3 sm:grid-cols-[minmax(0,1fr)_auto_auto] sm:items-end">
            <div className="grid gap-2">
              <Label>参考视频</Label>
              <Select
                value={props.referenceVideoPath || undefined}
                onValueChange={(value) => {
                  const asset = props.referenceVideoAssets.find((item) => item.path === value)
                  props.onReferenceVideoPathChange(value)
                  props.onReferenceVideoNameChange(asset?.name || pathBasename(value))
                }}
              >
                <SelectTrigger>
                  <SelectValue placeholder={props.referenceVideoAssets.length ? "选择参考视频" : "先上传参考视频"} />
                </SelectTrigger>
                <SelectContent>
                  {props.referenceVideoAssets.map((asset) => (
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
              accept="video/*"
              className="hidden"
              onChange={(event) => {
                void handleUpload(event.target.files?.[0])
                event.currentTarget.value = ""
              }}
            />
            <Button variant="quiet" onClick={() => uploadRef.current?.click()} loading={uploading}>
              <Upload className="h-4 w-4" />
              上传参考视频
            </Button>
            <Button
              onClick={() => void props.onGenerate()}
              loading={props.busyGenerate}
              disabled={Boolean(generateDisabledReason)}
              title={generateDisabledReason || "生成数字人视频"}
            >
              <Video className="h-4 w-4" />
              生成数字人视频
            </Button>
          </div>

          <div className="grid content-start gap-3 rounded-md border border-border bg-background/55 p-4">
            <div className="flex flex-wrap items-center gap-2">
              <TokenBadge tone={props.referenceVideoPath ? "success" : "warning"}>{props.referenceVideoPath ? "参考视频已选" : "等待选择视频"}</TokenBadge>
              <TokenBadge tone={statusTone}>{statusLabel}</TokenBadge>
              {latestResultPath ? <Badge variant="secondary">结果: {pathBasename(latestResultPath)}</Badge> : null}
            </div>
            <div className="grid gap-1 text-sm text-muted-foreground">
              <p>{statusMessage}</p>
              {latestResultPath ? <p className="truncate-path text-xs">位置: {latestResultPath}</p> : null}
            </div>
            <div className="grid gap-3 sm:grid-cols-2">
              <div className="grid gap-2">
                <Label>引擎</Label>
                <Select value={props.engine} onValueChange={props.onEngineChange}>
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="tuilionnx">TuiliONNX</SelectItem>
                  </SelectContent>
                </Select>
              </div>
              <div className="grid gap-2">
                <Label>分辨率</Label>
                <Select value={props.resolution} onValueChange={props.onResolutionChange}>
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="720p">720p</SelectItem>
                    <SelectItem value="1080p">1080p</SelectItem>
                    <SelectItem value="1440p">1440p</SelectItem>
                  </SelectContent>
                </Select>
              </div>
            </div>
            <div className="grid gap-3 sm:grid-cols-2">
              <div className="grid gap-2">
                <Label>帧率</Label>
                <Input type="number" min={15} max={60} value={props.fps} onChange={(event) => props.onFpsChange(Number(event.target.value))} />
              </div>
            </div>
            <div className="grid gap-2">
              <Label>视频叠加文字（可选）</Label>
              <Textarea
                value={props.overlayText}
                onChange={(event) => props.onOverlayTextChange(event.target.value)}
                placeholder={props.copyTitle || "留空则不在视频上叠加文字"}
                className="min-h-[94px]"
              />
              <p className="text-xs text-muted-foreground">只有这里手动填写内容时，才会把文字压到视频画面上。</p>
            </div>
            <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
              <Badge variant="outline">音频: {pathBasename(props.audioPath || "未选择")}</Badge>
              <Badge variant="outline">字幕: 后处理模块</Badge>
            </div>
            <div className="grid min-h-[76px] content-start gap-2">
              {generateDisabledReason ? (
                <div className="rounded-md border border-warning/30 bg-warning/10 px-3 py-2 text-xs leading-5 text-warning-foreground">
                  {generateDisabledReason}
                </div>
              ) : null}
            </div>
          </div>
        </div>

        <div className="grid content-start gap-4">
          <div className="grid gap-2">
            <div className="flex items-center justify-between">
              <Label>视频预览</Label>
              {latestResultPath ? <Badge variant="secondary">{pathBasename(latestResultPath)}</Badge> : null}
            </div>
            <div className="phone-preview-wrap">
              <div className="phone-preview-device" aria-label="手机竖屏视频预览">
                <div className="phone-preview-speaker" />
                <div className="phone-preview-screen">
                  {previewPath ? (
                    <video key={previewPath} controls className="h-full w-full bg-black object-contain" src={props.fileUrl(previewPath)} />
                  ) : (
                    <div className="flex h-full flex-col items-center justify-center gap-3 px-4 text-center text-sm text-zinc-400">
                      <Smartphone className="h-9 w-9 text-zinc-500" />
                      <span>暂无可预览视频</span>
                    </div>
                  )}
                </div>
                <div className="phone-preview-home" />
              </div>
            </div>
          </div>
        </div>
      </div>
      <div className="mt-4 text-xs text-muted-foreground">
        当前工作空间：{props.workspace || "未选择"}。数字人生成完成后，字幕模块会把这条视频作为后处理目标。
      </div>
    </PanelShell>
  )
}
