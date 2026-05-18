import { useMemo, useState } from "react"
import { Check, FolderInput, RefreshCcw, Workflow } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle, DialogTrigger } from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Badge } from "@/components/ui/badge"
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip"
import { PanelShell, TokenBadge } from "./common"
import { pathBasename } from "@/lib/utils"

type Props = {
  current: string
  draft: string
  recent: string[]
  isSaving: boolean
  message: string
  onDraftChange: (value: string) => void
  onSelectWorkspace: (value: string) => Promise<void>
  onRefresh: () => void
}

export function WorkspaceSection({
  current,
  draft,
  recent,
  isSaving,
  message,
  onDraftChange,
  onSelectWorkspace,
  onRefresh,
}: Props) {
  const [open, setOpen] = useState(false)
  const empty = !current && recent.length === 0
  const displayCurrent = useMemo(() => current || draft, [current, draft])
  const canApplyWorkspace = Boolean(draft.trim())

  return (
    <PanelShell
      id="workspace"
      eyebrow="工作空间"
      title="工作空间与素材根目录"
      description="选择一个本地目录作为当前工作空间，后续文案、音频、字幕和数字人结果都会写入同一条生产链。"
      actions={
        <>
          <Button variant="quiet" size="sm" onClick={onRefresh} loading={isSaving}>
            <RefreshCcw className="h-4 w-4" />
            刷新
          </Button>
          <Dialog open={open} onOpenChange={setOpen}>
            <TooltipProvider>
              <DialogTrigger asChild>
                <Button variant="amber" size="sm">
                  <FolderInput className="h-4 w-4" />
                  选择目录
                </Button>
              </DialogTrigger>
            </TooltipProvider>
            <DialogContent>
              <DialogHeader>
                <DialogTitle>切换工作空间</DialogTitle>
                <DialogDescription>输入本地路径，或从最近工作空间里直接选一个。</DialogDescription>
              </DialogHeader>
              <div className="grid gap-3">
                <div className="grid gap-2">
                  <label className="control-label">工作空间路径</label>
                  <Input value={draft} onChange={(event) => onDraftChange(event.target.value)} placeholder="D:\\video-work\\project-a" />
                </div>
                <div className="grid gap-2">
                  <label className="control-label">最近工作空间</label>
                  <div className="flex flex-wrap gap-2">
                    {recent.length > 0 ? (
                      recent.map((path) => (
                        <Button
                          key={path}
                          type="button"
                          variant="quiet"
                          size="sm"
                          onClick={() => onDraftChange(path)}
                        >
                          {pathBasename(path)}
                        </Button>
                      ))
                    ) : (
                      <span className="text-sm text-muted-foreground">暂无最近工作空间。</span>
                    )}
                  </div>
                </div>
              </div>
              <DialogFooter>
                <Button variant="quiet" onClick={() => setOpen(false)}>
                  取消
                </Button>
                <Button
                  onClick={async () => {
                    await onSelectWorkspace(draft)
                    setOpen(false)
                  }}
                  loading={isSaving}
                  disabled={!canApplyWorkspace}
                  title={canApplyWorkspace ? "应用当前路径" : "请先填写工作空间路径"}
                >
                  <Check className="h-4 w-4" />
                  应用
                </Button>
              </DialogFooter>
            </DialogContent>
          </Dialog>
        </>
      }
    >
      {empty ? (
        <div className="grid gap-4 rounded-md border border-dashed border-border bg-background/40 p-5">
          <div className="flex items-start gap-3">
            <div className="rounded-md border border-border bg-card p-2 text-primary">
              <Workflow className="h-5 w-5" />
            </div>
            <div className="min-w-0">
              <p className="text-sm font-semibold">还没有工作空间</p>
              <p className="mt-1 max-w-2xl text-sm text-muted-foreground">
                先选择一个本地目录，后续的素材、字幕、视频和发布素材都会围绕这个目录展开。
              </p>
            </div>
          </div>
          <div className="flex flex-wrap gap-2">
            <Button onClick={() => setOpen(true)}>
              <FolderInput className="h-4 w-4" />
              选择工作空间
            </Button>
          </div>
        </div>
      ) : (
        <div className="grid gap-4">
          <div className="grid gap-3">
            <div className="flex flex-wrap items-center gap-2">
              <TokenBadge tone={current ? "success" : "warning"}>{current ? "当前已选" : "等待选择"}</TokenBadge>
              <Badge variant="secondary">{displayCurrent || "未命名工作空间"}</Badge>
              {message ? <Badge variant="outline">{message}</Badge> : null}
            </div>
            <div className="grid gap-2">
              <label className="control-label">工作空间路径</label>
              <Input value={draft} onChange={(event) => onDraftChange(event.target.value)} placeholder="D:\\video-work\\project-a" />
            </div>
            <div className="flex flex-wrap gap-2">
              <Button
                onClick={async () => await onSelectWorkspace(draft)}
                loading={isSaving}
                disabled={!canApplyWorkspace}
                title={canApplyWorkspace ? "保存当前路径" : "请先填写工作空间路径"}
              >
                <Check className="h-4 w-4" />
                保存当前路径
              </Button>
              <Button variant="quiet" onClick={() => setOpen(true)}>
                <FolderInput className="h-4 w-4" />
                从最近工作空间切换
              </Button>
            </div>
          </div>
        </div>
      )}
    </PanelShell>
  )
}
