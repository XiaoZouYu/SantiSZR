import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Slider } from "@/components/ui/slider"
import { Textarea } from "@/components/ui/textarea"
import { pathBasename } from "@/lib/utils"
import { PanelShell, TokenBadge } from "./common"

type SubtitleStyle = {
  font_name: string
  font_size: number
  color: string
  outline_color: string
  bottom_margin: number
}

type LLMStatus = {
  configured?: boolean
  provider?: string
  model?: string
  api_base?: string
  message?: string
}

type Props = {
  workspace: string
  audioPath: string
  videoPath: string
  referenceText: string
  correctWithAI: boolean
  outputName: string
  style: SubtitleStyle
  srtText: string
  generatedSrtPath: string
  onAudioPathChange: (value: string) => void
  onReferenceTextChange: (value: string) => void
  onCorrectWithAIChange: (value: boolean) => void
  onOutputNameChange: (value: string) => void
  onStyleChange: (key: "font_name" | "font_size" | "color" | "outline_color" | "bottom_margin", value: string | number) => void
  onSrtTextChange: (value: string) => void
  onGenerate: () => Promise<unknown>
  onApplyToVideo: () => Promise<unknown>
  busyGenerate: boolean
  busyApply: boolean
  generateDisabledReason?: string
  applyDisabledReason?: string
  llmStatus?: LLMStatus
}

export function SubtitleSection(props: Props) {
  const derivedAudioPath = props.audioPath.trim()
  const targetVideoPath = props.videoPath.trim()
  const generateDisabledReason = props.generateDisabledReason || ""
  const applyDisabledReason = props.applyDisabledReason || ""
  const llmConfigured = Boolean(props.llmStatus?.configured)

  return (
    <PanelShell
      id="subtitle"
      eyebrow="字幕后处理"
      title="字幕生成"
      description="先用音频生成 SRT；确认字幕没问题后，再把字幕加载到数字人视频当中。"
      actions={
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
            title={applyDisabledReason || "把字幕加载到视频当中去"}
          >
            加载到视频
          </Button>
        </>
      }
    >
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
              placeholder="可粘贴改写后的文案作为对齐参考"
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
            <TokenBadge tone={targetVideoPath ? "success" : "muted"}>{targetVideoPath ? "视频已就绪" : "等待视频"}</TokenBadge>
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
          <div className="grid gap-4 rounded-md border border-border bg-background/55 p-4">
            <div className="grid gap-3 sm:grid-cols-2">
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
            </div>
            <div className="grid gap-3 sm:grid-cols-2">
              <div className="grid gap-2">
                <Label>颜色</Label>
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
            </div>
            <div className="grid gap-2">
              <Label>底部边距 {props.style.bottom_margin}px</Label>
              <Input
                type="number"
                min={0}
                max={240}
                value={props.style.bottom_margin}
                onChange={(event) => props.onStyleChange("bottom_margin", Number(event.target.value))}
              />
            </div>
            <div className="grid gap-2">
              <Label>输出名称</Label>
              <Input value={props.outputName} onChange={(event) => props.onOutputNameChange(event.target.value)} />
            </div>
          </div>
          <div className="grid gap-2">
            <div className="flex items-center justify-between">
              <Label>字幕预览</Label>
              {props.generatedSrtPath ? <Badge variant="secondary">{pathBasename(props.generatedSrtPath)}</Badge> : null}
            </div>
            <Textarea
              value={props.srtText}
              onChange={(event) => props.onSrtTextChange(event.target.value)}
              placeholder="生成后的 SRT 会显示在这里"
              className="min-h-[260px] font-mono text-xs leading-6"
            />
          </div>
        </div>
      </div>
      <div className="mt-4 text-xs text-muted-foreground">
        当前工作空间：{props.workspace || "未选择"}。字幕会先生成 SRT，加载到视频时才会进行视频后处理。
      </div>
    </PanelShell>
  )
}
