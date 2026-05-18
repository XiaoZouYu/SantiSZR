import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { ScrollArea } from "@/components/ui/scroll-area"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { PanelShell, TokenBadge } from "./common"
import type { BackendStateResponse, HealthResponse } from "@/types"

type DiagnosticItem = {
  label: string
  ok: boolean
  detail?: string
  path?: string
}

type Props = {
  health: HealthResponse | null
  stateSnapshot: BackendStateResponse | null
  diagnostics: DiagnosticItem[]
  connection: {
    live: boolean
    message: string
    lastError: string
    lastSynced: string
  }
  settings: {
    apiBase: string
    apiKey: string
    llmApiBase: string
    rewriteModel: string
  }
  onApiBaseChange: (value: string) => void
  onApiKeyChange: (value: string) => void
  onLlmApiBaseChange: (value: string) => void
  onRewriteModelChange: (value: string) => void
  onSaveLlmSettings: () => Promise<unknown>
  onTestLlmSettings: () => Promise<unknown>
  onRefreshHealth: () => void
  onRefreshState: () => void
  loading: {
    health: boolean
    state: boolean
  }
}

export function SettingsSection(props: Props) {
  const healthy = Boolean(props.health?.ok ?? props.connection.live)
  const llm = props.health?.llm
  const llmConfigured = Boolean(llm?.configured)
  const llmModel = llm?.model || props.settings.rewriteModel
  const llmApiBase = llm?.api_base || props.settings.llmApiBase || "https://api.deepseek.com/v1"

  return (
    <PanelShell
      id="settings"
      eyebrow="设置 / 诊断"
      title="后端健康、模型状态与本地配置"
      description="这里显示后端联通状态和本地运行依赖检查，也保留 API Base、API Key 和默认模型的入口。"
      actions={
        <>
          <Button variant="quiet" size="sm" onClick={props.onRefreshHealth} loading={props.loading.health}>
            刷新健康
          </Button>
          <Button size="sm" onClick={props.onRefreshState} loading={props.loading.state}>
            刷新状态
          </Button>
        </>
      }
    >
      <Tabs defaultValue="diagnostics" className="w-full">
        <TabsList className="mb-4 grid w-full max-w-lg grid-cols-2">
          <TabsTrigger value="diagnostics">诊断</TabsTrigger>
          <TabsTrigger value="base">基础设置</TabsTrigger>
        </TabsList>

        <TabsContent value="diagnostics" className="mt-0">
          <div className="grid gap-4 xl:grid-cols-[minmax(0,0.9fr)_minmax(0,1.1fr)]">
            <div className="grid gap-3 rounded-md border border-border bg-background/55 p-4">
              <div className="flex flex-wrap items-center gap-2">
                <TokenBadge tone={healthy ? "success" : "warning"}>{healthy ? "后端在线" : "后端离线"}</TokenBadge>
                <Badge variant="secondary">{props.settings.apiBase}</Badge>
                <Badge variant="outline">{props.connection.message || "等待同步"}</Badge>
              </div>
              <div className="grid gap-2 text-sm text-muted-foreground">
                <div>最后同步：{props.connection.lastSynced || "—"}</div>
                <div>最后错误：{props.connection.lastError || "无"}</div>
                <div>健康状态：{props.health?.status || (healthy ? "ok" : "unknown")}</div>
              </div>
              <div className="grid gap-2">
                <Label>状态快照</Label>
                <ScrollArea className="h-[170px] rounded-md border border-border bg-card">
                  <pre className="p-3 font-mono text-xs leading-5 text-muted-foreground">
                    {JSON.stringify(
                      {
                        workspace: props.stateSnapshot?.workspace ?? props.stateSnapshot?.current_workspace ?? "",
                        recent_workspaces: props.stateSnapshot?.recent_workspaces ?? [],
                        title: props.stateSnapshot?.title ?? "",
                        subtitle_path: props.stateSnapshot?.subtitle_path ?? "",
                        tts_audio_path: props.stateSnapshot?.tts_audio_path ?? "",
                        avatar_video_path: props.stateSnapshot?.avatar_video_path ?? "",
                      },
                      null,
                      2,
                    )}
                  </pre>
                </ScrollArea>
              </div>
            </div>

            <div className="grid gap-3">
              <div className="grid gap-2">
                <div className="flex items-center justify-between">
                  <Label>运行依赖</Label>
                  <Badge variant="secondary">{props.diagnostics.length}</Badge>
                </div>
                <div className="grid gap-2">
                  {props.diagnostics.map((item) => (
                    <div key={item.label} className="flex flex-wrap items-start justify-between gap-3 rounded-md border border-border bg-card px-3 py-2">
                      <div className="min-w-0">
                        <p className="text-sm font-medium">{item.label}</p>
                        <p className="mt-1 text-xs text-muted-foreground">{item.detail || "未返回"}</p>
                        {item.path ? <p className="mt-1 truncate-path text-xs text-muted-foreground">{item.path}</p> : null}
                      </div>
                      <Badge variant={item.ok ? "success" : "destructive"}>{item.ok ? "可用" : "异常"}</Badge>
                    </div>
                  ))}
                </div>
              </div>
            </div>
          </div>
        </TabsContent>

        <TabsContent value="base" className="mt-0">
          <div className="grid gap-4">
            <div className="grid gap-2">
              <Label>API Base</Label>
              <Input
                value={props.settings.apiBase}
                onChange={(event) => props.onApiBaseChange(event.target.value)}
                placeholder="http://127.0.0.1:7860"
              />
              <p className="text-xs text-muted-foreground">默认指向本机后端，改这里会立即影响后续请求。</p>
            </div>

            <div className="grid gap-3 rounded-md border border-border bg-background/55 p-4">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div className="min-w-0">
                  <Label>大模型配置</Label>
                  <p className="mt-1 text-xs text-muted-foreground">
                    保存后会写入本机后端配置，刷新页面和重启后端后仍然保留。
                  </p>
                </div>
                <div className="flex flex-wrap items-center gap-2">
                  <TokenBadge tone={llmConfigured ? "success" : "warning"}>
                    {llmConfigured ? "已配置" : "未配置"}
                  </TokenBadge>
                  {llm?.key_preview ? <Badge variant="secondary">{llm.key_preview}</Badge> : null}
                </div>
              </div>

              <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(220px,0.45fr)]">
                <div className="grid gap-2">
                  <Label>API Key</Label>
                  <Input
                    type="password"
                    value={props.settings.apiKey}
                    onChange={(event) => props.onApiKeyChange(event.target.value)}
                    placeholder={llmConfigured ? "已保存 Key；需要更换时重新输入" : "填写 DeepSeek 或兼容 OpenAI 的 API Key"}
                  />
                </div>
                <div className="grid gap-2">
                  <Label>后端大模型</Label>
                  <Input value={props.settings.rewriteModel} onChange={(event) => props.onRewriteModelChange(event.target.value)} placeholder="deepseek-chat" />
                </div>
              </div>

              <div className="grid gap-2">
                <Label>大模型 API Base</Label>
                <Input
                  value={props.settings.llmApiBase}
                  onChange={(event) => props.onLlmApiBaseChange(event.target.value)}
                  placeholder="https://api.deepseek.com/v1"
                />
              </div>

              <div className="grid gap-2 text-xs text-muted-foreground">
                <div>后端已保存的大模型 API Base：{llmApiBase}</div>
                <div>当前模型：{llmModel || "未设置"}</div>
                <div>{llm?.message || "等待后端返回大模型状态。"}</div>
              </div>

              <div className="flex flex-wrap items-center gap-2">
                <Button size="sm" onClick={() => void props.onSaveLlmSettings()}>
                  保存大模型配置
                </Button>
                <Button size="sm" variant="quiet" onClick={() => void props.onTestLlmSettings()}>
                  测试大模型连接
                </Button>
                <Badge variant={props.connection.lastError ? "destructive" : "secondary"}>
                  {props.connection.lastError || props.connection.message || "等待操作"}
                </Badge>
              </div>
            </div>

            <div className="grid gap-2 rounded-md border border-border bg-background/55 p-4 text-sm text-muted-foreground">
              <div>API Key 不会显示在健康接口里，只会显示脱敏后的保存状态。</div>
              <div>API Base 可临时切到别的后端，默认值是本机后端地址。</div>
            </div>
          </div>
        </TabsContent>
      </Tabs>
    </PanelShell>
  )
}
