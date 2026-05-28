import { useEffect, useRef, useState } from "react"
import { Smartphone, Upload, Video } from "lucide-react"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import { Label } from "@/components/ui/label"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { PanelShell, TokenBadge, VideoPreviewPanel } from "./common"
import { assetPathFromUploadResponse, cn, pathBasename } from "@/lib/utils"
import type { AssetRecord, TaskRecord } from "@/types"

type Props = {
  workspace: string
  audioPath: string
  referenceVideoPath: string
  referenceVideoName: string
  referenceVideoAssets: AssetRecord[]
  qualityPreset: string
  beautifyTeeth: boolean
  resultVideoPath: string
  errorLog: string[]
  busyGenerate: boolean
  latestTask: TaskRecord | null
  onAudioPathChange: (value: string) => void
  onReferenceVideoPathChange: (value: string) => void
  onReferenceVideoNameChange: (value: string) => void
  onQualityPresetChange: (value: string) => void
  onBeautifyTeethChange: (value: boolean) => void
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
  const [mediaError, setMediaError] = useState(false)

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
  const qualityLabel = props.qualityPreset === "speed" ? "速度" : props.qualityPreset === "hd" ? "高清" : "清晰"
  const taskMessage = typeof props.latestTask?.message === "string" ? props.latestTask.message.trim() : ""
  const genericTaskMessage = /^task completed\.?$/i.test(taskMessage) || /^completed\.?$/i.test(taskMessage)

  useEffect(() => {
    setMediaError(false)
  }, [previewPath])

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
    (latestTaskStatus === "succeeded"
      ? "数字人视频已生成完成，右侧预览已切换到生成结果。"
      : latestTaskStatus === "failed"
        ? taskMessage && !genericTaskMessage
          ? taskMessage
          : "数字人视频生成失败。"
        : props.busyGenerate || latestTaskStatus === "running" || latestTaskStatus === "pending" || latestTaskStatus === "queued"
          ? taskMessage && !genericTaskMessage
            ? taskMessage
            : "正在生成数字人视频，请稍等。"
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
                value={props.referenceVideoPath}
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

          <div className="overflow-hidden rounded-md border border-border bg-background/55">
            <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border bg-card/80 px-4 py-3">
              <div className="flex flex-wrap items-center gap-2">
                <TokenBadge tone={statusTone}>{statusLabel}</TokenBadge>
                {latestResultPath ? <Badge variant="secondary">结果: {pathBasename(latestResultPath)}</Badge> : null}
              </div>
              <Badge variant="outline">质量: {qualityLabel}</Badge>
            </div>

            <div className="grid gap-4 p-4">
              <div className="rounded-md bg-muted/35 px-3 py-2 text-sm leading-6 text-muted-foreground">
                <p>{statusMessage}</p>
                {latestResultPath ? <p className="truncate-path text-xs">位置: {latestResultPath}</p> : null}
              </div>

              <div className="grid gap-3 sm:grid-cols-[minmax(190px,0.8fr)_minmax(0,1fr)] sm:items-end">
                <label
                  className={cn(
                    "flex h-9 cursor-pointer items-center justify-between gap-3 rounded-md border px-3 text-sm transition-colors",
                    props.beautifyTeeth
                      ? "border-primary/40 bg-primary/5 text-foreground"
                      : "border-border bg-background/70 text-foreground hover:bg-muted/35",
                  )}
                >
                  <span className="flex min-w-0 items-center gap-2 font-medium">
                    <Checkbox
                      checked={props.beautifyTeeth}
                      onCheckedChange={(value) => props.onBeautifyTeethChange(Boolean(value))}
                    />
                    牙齿美化
                  </span>
                  <Badge variant={props.beautifyTeeth ? "default" : "secondary"}>
                    {props.beautifyTeeth ? "开启" : "关闭"}
                  </Badge>
                </label>
                <div className="grid gap-2">
                  <Label className="sr-only">质量预设</Label>
                  <Select value={props.qualityPreset} onValueChange={props.onQualityPresetChange}>
                    <SelectTrigger>
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="speed">速度</SelectItem>
                      <SelectItem value="clear">清晰</SelectItem>
                      <SelectItem value="hd">高清</SelectItem>
                    </SelectContent>
                  </Select>
                </div>
              </div>

              <div className="grid gap-2 border-t border-border pt-3 sm:grid-cols-3">
                <div className="min-w-0">
                  <div className="control-label">音频</div>
                  <div className="truncate-path mt-1 text-sm font-medium text-foreground">
                    {pathBasename(props.audioPath || "未选择")}
                  </div>
                </div>
                <div className="min-w-0">
                  <div className="control-label">参考视频</div>
                  <div className="truncate-path mt-1 text-sm font-medium text-foreground">
                    {pathBasename(props.referenceVideoPath || "未选择")}
                  </div>
                </div>
                <div className="min-w-0">
                  <div className="control-label">字幕</div>
                  <div className="truncate-path mt-1 text-sm font-medium text-foreground">后处理模块</div>
                </div>
              </div>

              {generateDisabledReason ? (
                <div className="rounded-md border border-warning/30 bg-warning/10 px-3 py-2 text-xs leading-5 text-warning-foreground">
                  {generateDisabledReason}
                </div>
              ) : null}
            </div>
          </div>
        </div>

        <div className="grid content-start gap-4">
          <VideoPreviewPanel
            title="数字人视频预览"
            subtitle={latestResultPath || props.referenceVideoPath || "等待生成数字人视频"}
            badges={
              <>
                <TokenBadge tone={statusTone}>{statusLabel}</TokenBadge>
                {latestResultPath ? <Badge variant="secondary">{pathBasename(latestResultPath)}</Badge> : null}
              </>
            }
            bodyClassName="bg-transparent"
          >
            <div className="phone-preview-wrap">
              <div className="phone-preview-device" aria-label="手机竖屏视频预览">
                <div className="phone-preview-speaker" />
                <div className="phone-preview-screen relative">
                  {previewPath ? (
                    <>
                      <video
                        key={previewPath}
                        controls
                        className="h-full w-full bg-black object-contain"
                        src={props.fileUrl(previewPath)}
                        onError={() => setMediaError(true)}
                      />
                      {mediaError ? (
                        <div className="absolute inset-x-3 bottom-3 rounded-md border border-warning/30 bg-black/80 px-3 py-2 text-xs leading-5 text-warning">
                          当前浏览器无法解码这个视频。请用 Chrome/Edge 打开页面，或重新导出为浏览器支持的 H.264/AAC。
                        </div>
                      ) : null}
                    </>
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
          </VideoPreviewPanel>
        </div>
      </div>
      <div className="mt-4 text-xs text-muted-foreground">
        当前工作空间：{props.workspace || "未选择"}。数字人生成完成后，字幕模块会把这条视频作为后处理目标。
      </div>
    </PanelShell>
  )
}
