import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Textarea } from "@/components/ui/textarea"
import { PanelShell, TokenBadge } from "./common"

type LLMStatus = {
  configured?: boolean
  provider?: string
  model?: string
  api_base?: string
  message?: string
}

type Props = {
  workspace: string
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
  onSourceTypeChange: (value: string) => void
  onSourceInputChange: (value: string) => void
  onDownloadVideoChange: (value: boolean) => void
  onExtractAudioChange: (value: boolean) => void
  onStreamTranscriptionChange: (value: boolean) => void
  onSourceTextChange: (value: string) => void
  onExtractedTextChange: (value: string) => void
  onRewriteModeChange: (value: string) => void
  onRewritePromptChange: (value: string) => void
  onRewriteModelChange: (value: string) => void
  onTemperatureChange: (value: number) => void
  onRewriteTextChange: (value: string) => void
  onTitleChange: (value: string) => void
  onTagsChange: (value: string) => void
  onExtract: () => Promise<unknown>
  onRewrite: () => Promise<unknown>
  busyExtract: boolean
  busyRewrite: boolean
  extractDisabled?: boolean
  rewriteDisabled?: boolean
  extractDisabledReason?: string
  rewriteDisabledReason?: string
  llmStatus?: LLMStatus
  lastRewriteProvider?: string
}

const sourceTypes = [
  ["douyin_share_text", "抖音分享文案"],
  ["url", "链接"],
  ["local_video", "本地视频"],
  ["local_audio", "本地音频"],
  ["raw_text", "纯文本"],
] as const

const rewriteModes = [
  ["custom", "自定义"],
  ["correct", "纠错"],
  ["imitate", "仿写"],
] as const

export function CopywritingSection(props: Props) {
  const extractDisabled = Boolean(props.extractDisabled)
  const rewriteDisabled = Boolean(props.rewriteDisabled)
  const llmConfigured = Boolean(props.llmStatus?.configured)
  const lastProvider = props.lastRewriteProvider || ""
  const lastProviderIsLocal = lastProvider === "heuristic"
  const providerLabel = lastProvider
    ? lastProviderIsLocal
      ? "上次使用本地规则"
      : `上次使用 ${lastProvider}`
    : llmConfigured
      ? `AI 已配置：${props.llmStatus?.model || props.rewriteModel}`
      : "未配置 AI，当前会用本地规则"

  return (
    <PanelShell
      id="copywriting"
      eyebrow="文案 / 改写"
      title="原文提取与改写"
      description="从分享文案、链接或本地素材里提取原文，再把结果整理成适合音频和发布的改写稿。"
      actions={
        <>
          <Button
            variant="quiet"
            size="sm"
            onClick={() => void props.onExtract()}
            loading={props.busyExtract}
            disabled={extractDisabled}
            title={props.extractDisabledReason}
          >
            提取原文
          </Button>
          <Button
            size="sm"
            onClick={() => void props.onRewrite()}
            loading={props.busyRewrite}
            disabled={rewriteDisabled}
            title={props.rewriteDisabledReason}
          >
            改写文案
          </Button>
        </>
      }
    >
      <Tabs defaultValue="source" className="w-full">
        <TabsList className="mb-4 grid w-full max-w-xl grid-cols-2">
          <TabsTrigger value="source">原文</TabsTrigger>
          <TabsTrigger value="rewrite">改写</TabsTrigger>
        </TabsList>

        <TabsContent value="source" className="mt-0">
          <div className="grid gap-4 lg:grid-cols-[minmax(0,1.4fr)_minmax(320px,0.6fr)]">
            <div className="grid gap-4">
              <div className="grid gap-2">
                <Label>输入来源</Label>
                <Select value={props.sourceType} onValueChange={props.onSourceTypeChange}>
                  <SelectTrigger>
                    <SelectValue placeholder="选择来源类型" />
                  </SelectTrigger>
                  <SelectContent>
                    {sourceTypes.map(([value, label]) => (
                      <SelectItem key={value} value={value}>
                        {label}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <div className="grid gap-2">
                <Label>原始输入</Label>
                <Textarea
                  value={props.sourceInput}
                  onChange={(event) => props.onSourceInputChange(event.target.value)}
                  placeholder="粘贴分享文案、URL，或输入本地素材索引文本"
                  className="min-h-[180px]"
                />
              </div>
              {props.extractDisabledReason ? (
                <div className="rounded-md border border-warning/30 bg-warning/10 px-3 py-2 text-xs leading-5 text-warning-foreground">
                  {props.extractDisabledReason}
                </div>
              ) : null}
            </div>
            <div className="grid gap-2">
              <Label>提取结果</Label>
              <Textarea
                value={props.extractedText || props.sourceText}
                onChange={(event) => props.onExtractedTextChange(event.target.value)}
                placeholder="提取后的原文会显示在这里"
                className="min-h-[260px] font-mono text-xs leading-6"
              />
            </div>
          </div>
        </TabsContent>

        <TabsContent value="rewrite" className="mt-0">
          <div className="grid gap-4 lg:grid-cols-[minmax(0,1.2fr)_minmax(0,0.8fr)]">
            <div className="grid gap-4">
              <div className="flex flex-wrap items-center gap-2 rounded-md border border-border bg-background/55 px-3 py-3 text-sm">
                <TokenBadge tone={llmConfigured ? "success" : "warning"}>
                  {llmConfigured ? "AI 改写可用" : "本地规则模式"}
                </TokenBadge>
                <span className="text-muted-foreground">{providerLabel}</span>
                {!llmConfigured ? (
                  <span className="text-xs text-muted-foreground">
                    需要在后端配置 SANTISZR_LLM_API_KEY 或 DEEPSEEK_API_KEY 后，才会真正调用大模型。
                  </span>
                ) : null}
              </div>
              <div className="grid gap-3 sm:grid-cols-2">
                <div className="grid gap-2">
                  <Label>改写模式</Label>
                  <Select value={props.rewriteMode} onValueChange={props.onRewriteModeChange}>
                    <SelectTrigger>
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      {rewriteModes.map(([value, label]) => (
                        <SelectItem key={value} value={value}>
                          {label}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
                <div className="grid gap-2">
                  <Label>模型</Label>
                  <Input value={props.rewriteModel} onChange={(event) => props.onRewriteModelChange(event.target.value)} placeholder="deepseek" />
                </div>
              </div>
              <div className="grid gap-2">
                <Label>改写提示词</Label>
                <Textarea
                  value={props.rewritePrompt}
                  onChange={(event) => props.onRewritePromptChange(event.target.value)}
                  placeholder="可选，描述语气、平台、限制"
                  className="min-h-[112px]"
                />
              </div>
              {props.rewriteDisabledReason ? (
                <div className="rounded-md border border-warning/30 bg-warning/10 px-3 py-2 text-xs leading-5 text-warning-foreground">
                  {props.rewriteDisabledReason}
                </div>
              ) : null}
              <div className="grid gap-3 sm:grid-cols-2">
                <div className="grid gap-2">
                  <Label>标题</Label>
                  <Input value={props.title} onChange={(event) => props.onTitleChange(event.target.value)} placeholder="用于发布和封面" />
                </div>
                <div className="grid gap-2">
                  <Label>标签</Label>
                  <Input value={props.tags} onChange={(event) => props.onTagsChange(event.target.value)} placeholder="#短视频, #口播, #教程" />
                </div>
              </div>
              <div className="grid gap-2">
                <Label>温度</Label>
                <Input
                  type="number"
                  min={0}
                  max={2}
                  step={0.1}
                  value={props.temperature}
                  onChange={(event) => props.onTemperatureChange(Number(event.target.value))}
                />
              </div>
            </div>
            <div className="grid gap-2">
              <Label>改写结果</Label>
              <Textarea
                value={props.rewriteText}
                onChange={(event) => props.onRewriteTextChange(event.target.value)}
                placeholder="改写后的文案会显示在这里"
                className="min-h-[260px] font-mono text-xs leading-6"
              />
            </div>
          </div>
        </TabsContent>
      </Tabs>
      <div className="mt-4 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
        <span>当前工作空间：{props.workspace || "未选择"}</span>
        <span>•</span>
        <span>填写内容会保留在当前页面，提交任务后再写入工作空间</span>
      </div>
    </PanelShell>
  )
}
