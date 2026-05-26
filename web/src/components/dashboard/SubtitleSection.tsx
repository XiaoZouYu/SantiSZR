import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Slider } from "@/components/ui/slider"
import { Switch } from "@/components/ui/switch"
import { Textarea } from "@/components/ui/textarea"
import { pathBasename } from "@/lib/utils"
import { PanelShell, TokenBadge, VideoPreviewFrame, VideoPreviewPanel } from "./common"

type SubtitleStyle = {
  font_name: string
  font_size: number
  color: string
  outline_color: string
  bottom_margin: number
  template: string
  highlight_keywords: string
  highlight_color: string
}

type LLMStatus = {
  configured?: boolean
  provider?: string
  model?: string
  api_base?: string
  message?: string
}

type SubtitleStyleKey = keyof SubtitleStyle

type Props = {
  workspace: string
  audioPath: string
  videoPath: string
  enabled: boolean
  referenceText: string
  correctWithAI: boolean
  outputName: string
  style: SubtitleStyle
  srtText: string
  generatedSrtPath: string
  generatedAssPath: string
  resultVideoPath: string
  onAudioPathChange: (value: string) => void
  onEnabledChange: (value: boolean) => void
  onReferenceTextChange: (value: string) => void
  onCorrectWithAIChange: (value: boolean) => void
  onOutputNameChange: (value: string) => void
  onStyleChange: (key: SubtitleStyleKey, value: string | number) => void
  onSrtTextChange: (value: string) => void
  onGenerate: () => Promise<unknown>
  onApplyToVideo: () => Promise<unknown>
  busyGenerate: boolean
  busyApply: boolean
  generateDisabledReason?: string
  applyDisabledReason?: string
  llmStatus?: LLMStatus
  fileUrl: (path: string) => string
}

const SUBTITLE_TEMPLATES = [
  { value: "short_video", label: "短视频高亮" },
  { value: "classic", label: "经典白字" },
  { value: "black_bar", label: "黑底强调" },
  { value: "knowledge", label: "知识讲解" },
]

export function SubtitleSection(props: Props) {
  const derivedAudioPath = props.audioPath.trim()
  const targetVideoPath = props.videoPath.trim()
  const resultVideoPath = props.resultVideoPath.trim()
  const previewVideoPath = resultVideoPath || targetVideoPath
  const generateDisabledReason = props.generateDisabledReason || ""
  const applyDisabledReason = props.applyDisabledReason || ""
  const llmConfigured = Boolean(props.llmStatus?.configured)

  return (
    <PanelShell
      id="subtitle"
      eyebrow="字幕后处理"
      title="字幕生成"
      description="生成可编辑字幕文本，同时输出 ASS 字幕；加载到视频时会按模板和关键词高亮重新渲染。"
      actions={
        <>
          <div className="flex h-9 items-center gap-2 rounded-md border border-border bg-background px-3 text-sm">
            <Switch checked={props.enabled} onCheckedChange={props.onEnabledChange} />
            <span>{props.enabled ? "已启用" : "未启用"}</span>
          </div>
          {props.enabled ? (
            <>
              <Button
                onClick={() => void props.onGenerate()}
                loading={props.busyGenerate}
                disabled={Boolean(generateDisabledReason)}
                title={generateDisabledReason || "生成字幕"}
              >
                生成字幕
              </Button>
              <Button
                variant="quiet"
                onClick={() => void props.onApplyToVideo()}
                loading={props.busyApply}
                disabled={Boolean(applyDisabledReason)}
                title={applyDisabledReason || "把字幕加载到视频当中"}
              >
                加载到视频
              </Button>
            </>
          ) : null}
        </>
      }
    >
      {!props.enabled ? (
        <div className="flex flex-wrap items-center gap-3 rounded-md border border-dashed border-border bg-background/45 px-4 py-3 text-sm text-muted-foreground">
          <TokenBadge tone="muted">已折叠</TokenBadge>
          <span>字幕未启用，发布会使用未加字幕的视频结果。</span>
        </div>
      ) : (
        <>
          <div className="grid items-start gap-4 xl:grid-cols-[minmax(0,0.9fr)_minmax(0,1.1fr)]">
            <div className="flex flex-col gap-3">
              <div className="grid gap-2">
                <Label>音频路径</Label>
                <Input value={props.audioPath} onChange={(event) => props.onAudioPathChange(event.target.value)} placeholder="字幕生成所用音频路径" />
              </div>
              <div className="grid gap-2 rounded-md border border-border bg-background/55 p-3">
                <div className="flex items-center justify-between gap-2">
                  <Label>目标视频</Label>
                  <TokenBadge tone={targetVideoPath ? "success" : "warning"}>{targetVideoPath ? "视频已就绪" : "先生成视频"}</TokenBadge>
                </div>
                <p className="truncate-path text-xs text-muted-foreground">
                  {targetVideoPath || "生成数字人视频后，这里会自动使用原始数字人视频作为字幕烧录目标。"}
                </p>
              </div>
              <div className="grid gap-2">
                <Label>参考文本（可选）</Label>
                <Textarea
                  value={props.referenceText}
                  onChange={(event) => props.onReferenceTextChange(event.target.value)}
                  placeholder="可粘贴生成音频时使用的文案，字幕会按这段文本重建分句。"
                  className="min-h-[168px]"
                />
              </div>
              <div className="flex flex-wrap items-center gap-4 rounded-md border border-border bg-background/55 px-3 py-3 text-sm">
                <label className="flex items-center gap-2">
                  <Checkbox
                    checked={props.correctWithAI}
                    disabled={!llmConfigured}
                    onCheckedChange={(value) => props.onCorrectWithAIChange(Boolean(value))}
                  />
                  大模型纠错
                </label>
                <TokenBadge tone={llmConfigured ? "success" : "warning"}>{llmConfigured ? "AI 可用" : "未配置 AI"}</TokenBadge>
                <TokenBadge tone={derivedAudioPath ? "success" : "warning"}>{derivedAudioPath ? "音频已就绪" : "等待音频输入"}</TokenBadge>
                <TokenBadge tone={props.generatedSrtPath ? "success" : "muted"}>{props.generatedSrtPath ? "字幕已生成" : "等待字幕"}</TokenBadge>
                <TokenBadge tone={props.generatedAssPath ? "success" : "muted"}>{props.generatedAssPath ? "ASS 已生成" : "等待 ASS"}</TokenBadge>
              </div>
              {!llmConfigured ? (
                <div className="rounded-md border border-warning/30 bg-warning/10 px-3 py-2 text-xs leading-5 text-warning-foreground">
                  未配置大模型 API Key，字幕仍会正常生成；只是“大模型纠错”不会执行。
                </div>
              ) : null}
              {generateDisabledReason ? (
                <div className="rounded-md border border-warning/30 bg-warning/10 px-3 py-2 text-xs leading-5 text-warning-foreground">
                  {generateDisabledReason}
                </div>
              ) : null}
              {applyDisabledReason ? (
                <div className="rounded-md border border-warning/30 bg-warning/10 px-3 py-2 text-xs leading-5 text-warning-foreground">
                  {applyDisabledReason}
                </div>
              ) : null}
            </div>

            <div className="grid gap-4">
              <VideoPreviewPanel
                title="字幕视频预览"
                subtitle={previewVideoPath || "等待数字人或画中画视频"}
                badges={
                  <>
                    {resultVideoPath ? <Badge variant="secondary">字幕结果：{pathBasename(resultVideoPath)}</Badge> : null}
                    {!resultVideoPath && targetVideoPath ? <Badge variant="outline">基座：{pathBasename(targetVideoPath)}</Badge> : null}
                  </>
                }
              >
                <VideoPreviewFrame
                  src={previewVideoPath}
                  fileUrl={props.fileUrl}
                  placeholder="等待数字人或画中画视频"
                />
              </VideoPreviewPanel>
              <div className="grid gap-4 rounded-md border border-border bg-background/55 p-4">
                <div className="grid gap-3 sm:grid-cols-2">
                  <div className="grid gap-2">
                    <Label>字幕模板</Label>
                    <Select value={props.style.template} onValueChange={(value) => props.onStyleChange("template", value)}>
                      <SelectTrigger>
                        <SelectValue placeholder="选择模板" />
                      </SelectTrigger>
                      <SelectContent>
                        {SUBTITLE_TEMPLATES.map((item) => (
                          <SelectItem key={item.value} value={item.value}>
                            {item.label}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </div>
                  <div className="grid gap-2">
                    <Label>关键词高亮</Label>
                    <Input
                      value={props.style.highlight_keywords}
                      onChange={(event) => props.onStyleChange("highlight_keywords", event.target.value)}
                      placeholder="人工智能,效率,增长"
                    />
                  </div>
                </div>
                <div className="grid gap-3 sm:grid-cols-3">
                  <div className="grid gap-2">
                    <Label>字体</Label>
                    <Input value={props.style.font_name} onChange={(event) => props.onStyleChange("font_name", event.target.value)} />
                  </div>
                  <div className="grid gap-2">
                    <Label>字号</Label>
                    <div className="flex h-9 items-center gap-3 rounded-md border border-input bg-background px-3">
                      <Slider
                        className="min-w-0 flex-1 py-0"
                        min={18}
                        max={64}
                        step={1}
                        value={[props.style.font_size]}
                        onValueChange={(value) => props.onStyleChange("font_size", value[0] ?? props.style.font_size)}
                      />
                      <span className="w-12 text-right text-xs font-medium tabular-nums text-muted-foreground">
                        {props.style.font_size}px
                      </span>
                    </div>
                  </div>
                  <div className="grid gap-2">
                    <Label>底部边距</Label>
                    <Input
                      type="number"
                      min={0}
                      max={240}
                      value={props.style.bottom_margin}
                      onChange={(event) => props.onStyleChange("bottom_margin", Number(event.target.value))}
                    />
                  </div>
                </div>
                <div className="grid gap-3 sm:grid-cols-3">
                  <div className="grid gap-2">
                    <Label>文字颜色</Label>
                    <Input type="color" value={props.style.color} onChange={(event) => props.onStyleChange("color", event.target.value)} className="color-control" />
                  </div>
                  <div className="grid gap-2">
                    <Label>描边颜色</Label>
                    <Input
                      type="color"
                      value={props.style.outline_color}
                      onChange={(event) => props.onStyleChange("outline_color", event.target.value)}
                      className="color-control"
                    />
                  </div>
                  <div className="grid gap-2">
                    <Label>高亮颜色</Label>
                    <Input
                      type="color"
                      value={props.style.highlight_color}
                      onChange={(event) => props.onStyleChange("highlight_color", event.target.value)}
                      className="color-control"
                    />
                  </div>
                </div>
                <div className="grid gap-2">
                  <Label>输出名称</Label>
                  <Input value={props.outputName} onChange={(event) => props.onOutputNameChange(event.target.value)} />
                </div>
              </div>
              <div className="grid gap-2">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <Label>字幕预览（SRT 文本，可直接修改）</Label>
                  <div className="flex flex-wrap gap-2">
                    {props.generatedSrtPath ? <Badge variant="secondary">SRT：{pathBasename(props.generatedSrtPath)}</Badge> : null}
                    {props.generatedAssPath ? <Badge variant="secondary">ASS：{pathBasename(props.generatedAssPath)}</Badge> : null}
                  </div>
                </div>
                <Textarea
                  value={props.srtText}
                  onChange={(event) => props.onSrtTextChange(event.target.value)}
                  placeholder="生成后的 SRT 会显示在这里；点击加载到视频时，会按当前模板和关键词转成 ASS 效果。"
                  className="min-h-[260px] font-mono text-xs leading-6"
                />
              </div>
            </div>
          </div>
          <div className="mt-4 text-xs text-muted-foreground">
            当前工作空间：{props.workspace || "未选择"}。SRT 用来检查和修改文字，ASS 用来做模板样式、关键词高亮和最终烧录。
          </div>
        </>
      )}
    </PanelShell>
  )
}
