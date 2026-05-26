import { useRef, useState } from "react"
import { Check, Film, Image as ImageIcon, PictureInPicture2, Upload } from "lucide-react"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Slider } from "@/components/ui/slider"
import { Switch } from "@/components/ui/switch"
import { assetPathFromUploadResponse, cn, pathBasename } from "@/lib/utils"
import { PanelShell, TokenBadge, VideoPreviewFrame, VideoPreviewPanel } from "./common"
import type { AssetRecord } from "@/types"

type Props = {
  workspace: string
  baseVideoPath: string
  enabled: boolean
  sourcePath: string
  sourceName: string
  sourceAssets: AssetRecord[]
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
  busy: boolean
  disabledReason?: string
  onEnabledChange: (value: boolean) => void
  onSourcePathChange: (value: string) => void
  onSourceNameChange: (value: string) => void
  onFullDurationChange: (value: boolean) => void
  onStartSecChange: (value: number) => void
  onEndSecChange: (value: number) => void
  onTemplateChange: (value: string) => void
  onPositionChange: (value: string) => void
  onScaleChange: (value: number) => void
  onBorderWidthChange: (value: number) => void
  onBorderColorChange: (value: string) => void
  onShadowChange: (value: boolean) => void
  onOpacityChange: (value: number) => void
  onAnimationChange: (value: string) => void
  onFadeDurationChange: (value: number) => void
  onLoopChange: (value: boolean) => void
  onUpload: (kind: "image" | "video", file: File) => Promise<unknown>
  onGenerate: () => Promise<unknown>
  fileUrl: (path: string) => string
}

const POSITIONS = [
  { value: "top_left", label: "左上" },
  { value: "top_right", label: "右上" },
  { value: "bottom_left", label: "左下" },
  { value: "bottom_right", label: "右下" },
]

const PIP_TEMPLATES = [
  { value: "corner", label: "右上小窗", position: "top_right", scale: 0.18, borderWidth: 0, shadow: false, opacity: 1, animation: "none" },
  { value: "focus", label: "重点讲解", position: "top_right", scale: 0.26, borderWidth: 4, shadow: true, opacity: 1, animation: "fade" },
  { value: "reference", label: "左下资料", position: "bottom_left", scale: 0.22, borderWidth: 2, shadow: true, opacity: 0.96, animation: "fade" },
  { value: "clean", label: "简洁浮层", position: "bottom_right", scale: 0.2, borderWidth: 0, shadow: false, opacity: 0.92, animation: "fade" },
]

const ANIMATIONS = [
  { value: "none", label: "无动画" },
  { value: "fade", label: "淡入淡出" },
]

function isVideoPath(path: string) {
  return /\.(mp4|mov|mkv|webm|avi|m4v)$/i.test(path)
}

function isImagePath(path: string) {
  return /\.(png|jpe?g|webp|gif|bmp)$/i.test(path)
}

function assetTypeText(asset?: AssetRecord) {
  return `${asset?.kind ?? ""} ${asset?.category ?? ""} ${asset?.mime_type ?? ""}`.toLowerCase()
}

function isVideoAsset(asset: AssetRecord | undefined, path: string) {
  const typeText = assetTypeText(asset)
  return isVideoPath(path) || typeText.includes("video")
}

function isImageAsset(asset: AssetRecord | undefined, path: string) {
  const typeText = assetTypeText(asset)
  return isImagePath(path) || typeText.includes("image")
}

function assetKindLabel(asset: AssetRecord) {
  return isVideoAsset(asset, asset.path) ? "视频" : "图片"
}

export function PictureInPictureSection(props: Props) {
  const uploadRef = useRef<HTMLInputElement | null>(null)
  const [uploading, setUploading] = useState(false)
  const baseReady = Boolean(props.baseVideoPath.trim())
  const sourceReady = Boolean(props.sourcePath.trim())
  const previewPath = props.resultVideoPath
  const disabledReason = props.disabledReason || ""
  const selectedSourceAsset = props.sourceAssets.find((item) => item.path === props.sourcePath)
  const sourceIsVideo = isVideoAsset(selectedSourceAsset, props.sourcePath)
  const sourceIsImage = !sourceIsVideo && isImageAsset(selectedSourceAsset, props.sourcePath)

  const handleUpload = async (file?: File | null) => {
    if (!file) return
    const kind: "image" | "video" =
      file.type.startsWith("video/") || isVideoPath(file.name) ? "video" : "image"
    setUploading(true)
    try {
      const response = await props.onUpload(kind, file)
      const path = assetPathFromUploadResponse(response, kind === "video" ? ["video_path"] : ["image_path"]) || file.name
      props.onSourcePathChange(path)
      props.onSourceNameChange(file.name)
      props.onEnabledChange(true)
    } finally {
      setUploading(false)
    }
  }

  const handleTemplateChange = (value: string) => {
    const template = PIP_TEMPLATES.find((item) => item.value === value)
    props.onTemplateChange(value)
    if (!template) return
    props.onPositionChange(template.position)
    props.onScaleChange(template.scale)
    props.onBorderWidthChange(template.borderWidth)
    props.onShadowChange(template.shadow)
    props.onOpacityChange(template.opacity)
    props.onAnimationChange(template.animation)
  }

  return (
    <PanelShell
      id="pip"
      eyebrow="画中画"
      title="画中画叠加"
      description="可选步骤。默认全程显示素材，也可以指定开始和结束时间；字幕会在画中画之后再加载。"
      actions={
        <>
          <div className="flex h-9 items-center gap-2 rounded-md border border-border bg-background px-3 text-sm">
            <Switch checked={props.enabled} onCheckedChange={props.onEnabledChange} />
            <span>{props.enabled ? "已启用" : "未启用"}</span>
          </div>
          {props.enabled ? (
            <>
              <Button
                onClick={() => uploadRef.current?.click()}
                variant="quiet"
                loading={uploading}
              >
                <Upload className="h-4 w-4" />
                上传素材
              </Button>
              <Button
                onClick={() => void props.onGenerate()}
                loading={props.busy}
                disabled={Boolean(disabledReason)}
                title={disabledReason || "生成画中画视频"}
              >
                <PictureInPicture2 className="h-4 w-4" />
                生成画中画
              </Button>
            </>
          ) : null}
        </>
      }
    >
      <input
        ref={uploadRef}
        type="file"
        accept="image/*,video/*"
        className="hidden"
        onChange={(event) => {
          void handleUpload(event.target.files?.[0])
          event.currentTarget.value = ""
        }}
      />

      {!props.enabled ? (
        <div className="flex flex-wrap items-center gap-3 rounded-md border border-dashed border-border bg-background/45 px-4 py-3 text-sm text-muted-foreground">
          <TokenBadge tone="muted">已折叠</TokenBadge>
          <span>画中画未启用，后续步骤会直接使用数字人视频作为基座。</span>
        </div>
      ) : (
        <>
          <div className="grid items-start gap-4 xl:grid-cols-[minmax(0,0.95fr)_minmax(0,1.05fr)]">
            <div className="order-2 grid content-start gap-3 xl:order-2">
              <VideoPreviewPanel
                title="画中画视频预览"
                subtitle={props.resultVideoPath || "生成后显示画中画视频"}
                badges={
                  <>
                    <TokenBadge tone={baseReady ? "success" : "warning"}>
                      {baseReady ? "数字人视频已就绪" : "等待数字人视频"}
                    </TokenBadge>
                    {props.resultVideoPath ? <Badge variant="secondary">画中画: {pathBasename(props.resultVideoPath)}</Badge> : null}
                  </>
                }
                footer={props.statusNote || "等待画中画处理。"}
              >
                <VideoPreviewFrame
                  src={previewPath}
                  fileUrl={props.fileUrl}
                  placeholder="等待生成画中画视频"
                />
              </VideoPreviewPanel>
            </div>

            <div className="order-1 grid content-start gap-4 xl:order-1">
              <div className="grid gap-3 rounded-md border border-border bg-background/55 p-4">
                <div className="grid gap-2">
                  <Label>画中画素材</Label>
                  <Select
                    value={props.sourcePath || undefined}
                    onValueChange={(value) => {
                      const asset = props.sourceAssets.find((item) => item.path === value)
                      props.onSourcePathChange(value)
                      props.onSourceNameChange(asset?.name || pathBasename(value))
                      props.onEnabledChange(true)
                    }}
                  >
                    <SelectTrigger>
                      <SelectValue placeholder={props.sourceAssets.length ? "选择图片或视频素材" : "先上传画中画素材"} />
                    </SelectTrigger>
                    <SelectContent>
                      {props.sourceAssets.map((asset) => (
                        <SelectItem key={asset.id || asset.path} value={asset.path}>
                          {assetKindLabel(asset)} · {asset.name || pathBasename(asset.path)}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                  {props.sourcePath ? (
                    <div className="flex items-center gap-2 text-xs text-muted-foreground">
                      {sourceIsImage ? <ImageIcon className="h-3.5 w-3.5" /> : <Film className="h-3.5 w-3.5" />}
                      <span className="truncate-path">{props.sourcePath}</span>
                    </div>
                  ) : null}
                  {props.sourcePath ? (
                    <VideoPreviewPanel
                      title="素材预览"
                      subtitle={props.sourceName || pathBasename(props.sourcePath)}
                      badges={<Badge variant="outline">{sourceIsImage ? "图片" : sourceIsVideo ? "视频" : "素材"}</Badge>}
                    >
                        {sourceIsImage ? (
                          <img
                            src={props.fileUrl(props.sourcePath)}
                            alt="画中画素材预览"
                            className="aspect-video w-full bg-black object-contain"
                          />
                        ) : sourceIsVideo ? (
                          <video
                            key={props.sourcePath}
                            controls
                            muted
                            className="aspect-video w-full bg-black object-contain"
                            src={props.fileUrl(props.sourcePath)}
                          />
                        ) : (
                          <div className="flex aspect-video items-center justify-center text-xs text-zinc-400">
                            当前素材类型不能直接预览
                          </div>
                        )}
                    </VideoPreviewPanel>
                  ) : null}
                </div>

                <div className="grid gap-3 sm:grid-cols-2">
                  <div className="grid gap-2">
                    <Label>模板</Label>
                    <Select value={props.template} onValueChange={handleTemplateChange}>
                      <SelectTrigger>
                        <SelectValue placeholder="选择画中画模板" />
                      </SelectTrigger>
                      <SelectContent>
                        {PIP_TEMPLATES.map((item) => (
                          <SelectItem key={item.value} value={item.value}>
                            {item.label}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </div>
                  <div className="grid gap-2">
                    <Label>动画</Label>
                    <Select value={props.animation} onValueChange={props.onAnimationChange}>
                      <SelectTrigger>
                        <SelectValue placeholder="选择动画" />
                      </SelectTrigger>
                      <SelectContent>
                        {ANIMATIONS.map((item) => (
                          <SelectItem key={item.value} value={item.value}>
                            {item.label}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </div>
                </div>

                <div className="grid gap-2">
                  <div className="flex items-center justify-between gap-3">
                    <Label>显示时间</Label>
                    <label className="flex items-center gap-2 text-xs text-muted-foreground">
                      <Switch checked={props.fullDuration} onCheckedChange={props.onFullDurationChange} />
                      全程显示
                    </label>
                  </div>
                  {!props.fullDuration ? (
                    <div className="grid gap-3 sm:grid-cols-2">
                      <div className="grid gap-2">
                        <Label className="text-xs text-muted-foreground">开始秒数</Label>
                        <Input
                          type="number"
                          min={0}
                          step={0.1}
                          value={props.startSec}
                          onChange={(event) => props.onStartSecChange(Number(event.target.value))}
                        />
                      </div>
                      <div className="grid gap-2">
                        <Label className="text-xs text-muted-foreground">结束秒数</Label>
                        <Input
                          type="number"
                          min={0}
                          step={0.1}
                          value={props.endSec}
                          onChange={(event) => props.onEndSecChange(Number(event.target.value))}
                        />
                      </div>
                    </div>
                  ) : (
                    <div className="rounded-md border border-border bg-card px-3 py-2 text-xs text-muted-foreground">
                      当前会从 0 秒显示到主视频结束。
                    </div>
                  )}
                </div>

                <div className="grid gap-2">
                  <Label>位置</Label>
                  <div className="grid grid-cols-2 gap-2">
                    {POSITIONS.map((item) => {
                      const active = props.position === item.value
                      return (
                        <Button
                          key={item.value}
                          type="button"
                          variant={active ? "secondary" : "quiet"}
                          className={cn("justify-center", active && "border-primary")}
                          onClick={() => props.onPositionChange(item.value)}
                        >
                          {active ? <Check className="h-4 w-4" /> : null}
                          {item.label}
                        </Button>
                      )
                    })}
                  </div>
                </div>

                <div className="grid gap-2">
                  <div className="flex items-center justify-between gap-3">
                    <Label>画中画显示大小</Label>
                    <span className="text-xs font-medium tabular-nums text-muted-foreground">
                      {Math.round(props.scale * 100)}%
                    </span>
                  </div>
                  <div className="flex h-9 items-center rounded-md border border-input bg-background px-3">
                    <Slider
                      min={0.1}
                      max={0.3}
                      step={0.01}
                      value={[props.scale]}
                      onValueChange={(value) => props.onScaleChange(value[0] ?? props.scale)}
                    />
                  </div>
                  <p className="text-xs leading-5 text-muted-foreground">
                    按主视频长边计算画中画最大边长，素材会保持原比例。
                  </p>
                </div>

                <div className="grid gap-3 rounded-md border border-border bg-card/65 p-3">
                  <div className="grid gap-3 sm:grid-cols-2">
                    <div className="grid gap-2">
                      <div className="flex items-center justify-between gap-3">
                        <Label>边框</Label>
                        <span className="text-xs font-medium tabular-nums text-muted-foreground">
                          {props.borderWidth}px
                        </span>
                      </div>
                      <div className="flex h-9 items-center rounded-md border border-input bg-background px-3">
                        <Slider
                          min={0}
                          max={16}
                          step={1}
                          value={[props.borderWidth]}
                          onValueChange={(value) => props.onBorderWidthChange(value[0] ?? props.borderWidth)}
                        />
                      </div>
                    </div>
                    <div className="grid gap-2">
                      <div className="flex items-center justify-between gap-3">
                        <Label>透明度</Label>
                        <span className="text-xs font-medium tabular-nums text-muted-foreground">
                          {Math.round(props.opacity * 100)}%
                        </span>
                      </div>
                      <div className="flex h-9 items-center rounded-md border border-input bg-background px-3">
                        <Slider
                          min={0.3}
                          max={1}
                          step={0.01}
                          value={[props.opacity]}
                          onValueChange={(value) => props.onOpacityChange(value[0] ?? props.opacity)}
                        />
                      </div>
                    </div>
                  </div>
                  <div className="grid gap-3 sm:grid-cols-2">
                    <div className="grid gap-2">
                      <Label>边框颜色</Label>
                      <Input
                        type="color"
                        value={props.borderColor}
                        onChange={(event) => props.onBorderColorChange(event.target.value)}
                        className="color-control"
                      />
                    </div>
                    <label className="flex items-center justify-between gap-3 rounded-md border border-border bg-background px-3 py-2 text-sm">
                      <span>阴影</span>
                      <Switch checked={props.shadow} onCheckedChange={props.onShadowChange} />
                    </label>
                  </div>
                  {props.animation === "fade" ? (
                    <div className="grid gap-2">
                      <div className="flex items-center justify-between gap-3">
                        <Label>淡入淡出时长</Label>
                        <span className="text-xs font-medium tabular-nums text-muted-foreground">
                          {props.fadeDuration.toFixed(1)}s
                        </span>
                      </div>
                      <div className="flex h-9 items-center rounded-md border border-input bg-background px-3">
                        <Slider
                          min={0.1}
                          max={2}
                          step={0.1}
                          value={[props.fadeDuration]}
                          onValueChange={(value) => props.onFadeDurationChange(value[0] ?? props.fadeDuration)}
                        />
                      </div>
                    </div>
                  ) : null}
                </div>

                <label className="flex items-center justify-between gap-3 rounded-md border border-border bg-card px-3 py-2 text-sm">
                  <span>视频素材循环补齐</span>
                  <Switch checked={props.loop} onCheckedChange={props.onLoopChange} />
                </label>
              </div>

              <div className="flex flex-wrap items-center gap-2">
                <TokenBadge tone={props.enabled ? "success" : "muted"}>{props.enabled ? "画中画开启" : "可跳过"}</TokenBadge>
                <TokenBadge tone={sourceReady ? "success" : "warning"}>{sourceReady ? "素材已选择" : "等待素材"}</TokenBadge>
                <TokenBadge tone={props.resultVideoPath ? "success" : "muted"}>{props.resultVideoPath ? "已有画中画结果" : "未生成"}</TokenBadge>
              </div>
              {disabledReason ? (
                <div className="rounded-md border border-warning/30 bg-warning/10 px-3 py-2 text-xs leading-5 text-warning-foreground">
                  {disabledReason}
                </div>
              ) : null}
            </div>
          </div>
          <div className="mt-4 text-xs text-muted-foreground">
            当前工作空间：{props.workspace || "未选择"}。图片会按时间段显示；视频会默认静音并循环补齐到结束时间。
          </div>
        </>
      )}
    </PanelShell>
  )
}
