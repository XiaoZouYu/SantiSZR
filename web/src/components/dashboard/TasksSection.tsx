import { useMemo } from "react"
import { Ban, RefreshCcw, TimerReset } from "lucide-react"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Progress } from "@/components/ui/progress"
import { ScrollArea } from "@/components/ui/scroll-area"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { PanelShell, TokenBadge } from "./common"
import { pathBasename } from "@/lib/utils"
import type { TaskRecord } from "@/types"

type Props = {
  currentTask: TaskRecord | null
  tasks: TaskRecord[]
  logsByTask: Record<string, string[]>
  onCancelTask: (taskId: string) => Promise<void> | void
  onRefresh: () => void
  compactDateTime: (value: string | undefined | null) => string
  statusTone: (status: string | undefined) => "idle" | "warning" | "success" | "error"
}

function toneLabel(tone: "idle" | "warning" | "success" | "error"): "secondary" | "warning" | "success" | "destructive" {
  if (tone === "warning") return "warning"
  if (tone === "success") return "success"
  if (tone === "error") return "destructive"
  return "secondary"
}

export function TasksSection(props: Props) {
  const history = useMemo(
    () =>
      [...props.tasks].sort((a, b) => (b.updated_at ?? b.created_at ?? "").localeCompare(a.updated_at ?? a.created_at ?? "")),
    [props.tasks],
  )
  const current = props.currentTask
  const currentLogs = current ? [...(props.logsByTask[current.task_id] ?? []), ...(current.logs ?? [])] : []
  const progress = current ? Math.round((current.progress ?? 0) * 100) : 0
  const currentTone = props.statusTone(current?.status)

  return (
    <PanelShell
      id="tasks"
      eyebrow="任务中心"
      title="当前进度、阶段与日志流"
      description="这里显示后台任务的实时状态和历史轨迹，取消按钮只会作用于当前运行中的任务。"
      actions={
        <Button variant="quiet" size="sm" onClick={props.onRefresh}>
          <RefreshCcw className="h-4 w-4" />
          刷新
        </Button>
      }
    >
      <div className="grid gap-4">
        <div className="grid gap-3 rounded-md border border-border bg-background/55 p-4">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="flex min-w-0 flex-wrap items-center gap-2">
              <TokenBadge tone={currentTone}>{current ? current.status || "pending" : "无当前任务"}</TokenBadge>
              {current ? <Badge variant="secondary">{current.task_kind}</Badge> : null}
              {current ? <Badge variant="outline">阶段: {current.stage || "—"}</Badge> : null}
              {current ? <Badge variant="outline">{progress}%</Badge> : null}
            </div>
            {current?.status === "running" ? (
              <Button variant="destructive" size="sm" onClick={() => void props.onCancelTask(current.task_id)}>
                <Ban className="h-4 w-4" />
                取消任务
              </Button>
            ) : null}
          </div>
          <Progress value={(current?.progress ?? 0) || 0} />
          <div className="grid gap-2 text-sm text-muted-foreground sm:grid-cols-3">
            <div>任务 ID：{current?.task_id || "无"}</div>
            <div>消息：{current?.message || "等待新的任务"}</div>
            <div>更新时间：{props.compactDateTime(current?.updated_at) || "—"}</div>
          </div>
        </div>

        <div className="grid gap-2">
          <div className="flex items-center justify-between">
            <p className="control-label">日志流</p>
            <Badge variant="secondary">{currentLogs.length}</Badge>
          </div>
          <ScrollArea className="h-[220px] rounded-md border border-border bg-black/95">
            <div className="grid gap-2 p-3 font-mono text-xs leading-5 text-emerald-100">
              {currentLogs.length > 0 ? (
                currentLogs.map((line, index) => (
                  <div key={`${line}-${index}`} className="rounded-sm border border-emerald-500/20 bg-black/40 px-2 py-1">
                    {line}
                  </div>
                ))
              ) : (
                <div className="flex h-[180px] items-center justify-center text-sm text-emerald-100/70">等待任务日志输出。</div>
              )}
            </div>
          </ScrollArea>
        </div>

        <div className="grid gap-2">
          <div className="flex items-center justify-between">
            <p className="control-label">历史任务</p>
            <Badge variant="secondary">{history.length}</Badge>
          </div>
          <div className="rounded-md border border-border bg-background/60">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>任务</TableHead>
                  <TableHead>类型</TableHead>
                  <TableHead>阶段</TableHead>
                  <TableHead>进度</TableHead>
                  <TableHead>状态</TableHead>
                  <TableHead>时间</TableHead>
                  <TableHead className="w-[96px]">操作</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {history.length > 0 ? (
                  history.map((task) => {
                    const tone = props.statusTone(task.status)
                    return (
                      <TableRow key={task.task_id}>
                        <TableCell className="font-mono text-xs">{pathBasename(task.task_id) || task.task_id}</TableCell>
                        <TableCell>{task.task_kind}</TableCell>
                        <TableCell>{task.stage || "—"}</TableCell>
                        <TableCell>{Math.round((task.progress ?? 0) * 100)}%</TableCell>
                        <TableCell>
                          <Badge variant={toneLabel(tone)}>{task.status || "pending"}</Badge>
                        </TableCell>
                        <TableCell className="text-xs text-muted-foreground">
                          <div className="grid gap-1">
                            <span>{props.compactDateTime(task.updated_at)}</span>
                            <span>{task.message || "—"}</span>
                          </div>
                        </TableCell>
                        <TableCell>
                          {task.status === "running" ? (
                            <Button size="sm" variant="quiet" onClick={() => void props.onCancelTask(task.task_id)}>
                              <TimerReset className="h-4 w-4" />
                              取消
                            </Button>
                          ) : (
                            <span className="text-xs text-muted-foreground">—</span>
                          )}
                        </TableCell>
                      </TableRow>
                    )
                  })
                ) : (
                  <TableRow>
                    <TableCell colSpan={7}>
                      <div className="py-10 text-center text-sm text-muted-foreground">暂无历史任务。</div>
                    </TableCell>
                  </TableRow>
                )}
              </TableBody>
            </Table>
          </div>
        </div>
      </div>
    </PanelShell>
  )
}
