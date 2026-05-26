import { useEffect, useState } from "react"
import type { ComponentProps, ReactNode } from "react"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Separator } from "@/components/ui/separator"
import { cn } from "@/lib/utils"

export function PanelShell({
  id,
  eyebrow,
  title,
  description,
  actions,
  children,
  className,
}: {
  id?: string
  eyebrow?: string
  title: string
  description?: string
  actions?: ReactNode
  children: ReactNode
  className?: string
}) {
  return (
    <section id={id} className={cn("dense-section scroll-mt-32 lg:scroll-mt-20", className)}>
      <div className="mb-4 flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          {eyebrow ? <p className="control-label mb-1">{eyebrow}</p> : null}
          <h2 className="text-lg font-semibold tracking-tight text-foreground sm:text-xl">{title}</h2>
          {description ? <p className="mt-1 max-w-4xl text-sm text-muted-foreground">{description}</p> : null}
        </div>
        {actions ? <div className="flex flex-wrap items-center gap-2">{actions}</div> : null}
      </div>
      <Separator className="mb-4" />
      {children}
    </section>
  )
}

export function FieldRow({
  label,
  hint,
  children,
  className,
}: {
  label: string
  hint?: string
  children: ReactNode
  className?: string
}) {
  return (
    <div className={cn("grid gap-2", className)}>
      <div className="flex items-end justify-between gap-3">
        <div className="min-w-0">
          <p className="control-label">{label}</p>
          {hint ? <p className="mt-1 text-xs text-muted-foreground">{hint}</p> : null}
        </div>
      </div>
      {children}
    </div>
  )
}

export function TokenBadge({
  children,
  tone = "default",
}: {
  children: ReactNode
  tone?: "default" | "muted" | "success" | "warning" | "destructive" | "idle" | "error"
}) {
  const variant =
    tone === "success"
      ? "success"
      : tone === "warning"
      ? "warning"
      : tone === "destructive" || tone === "error"
        ? "destructive"
        : tone === "muted" || tone === "idle"
            ? "secondary"
            : "default"
  return <Badge variant={variant as never}>{children}</Badge>
}

export function SectionButton(props: ComponentProps<typeof Button>) {
  return <Button size="sm" {...props} />
}

export function VideoPreviewPanel({
  title,
  subtitle,
  badges,
  children,
  footer,
  className,
  bodyClassName,
}: {
  title: string
  subtitle?: string
  badges?: ReactNode
  children: ReactNode
  footer?: ReactNode
  className?: string
  bodyClassName?: string
}) {
  return (
    <div className={cn("overflow-hidden rounded-md border border-border bg-background/55", className)}>
      <div className="flex min-h-14 flex-wrap items-center justify-between gap-3 border-b border-border bg-card/80 px-3 py-2">
        <div className="min-w-0">
          <div className="text-sm font-semibold text-foreground">{title}</div>
          {subtitle ? <div className="mt-0.5 truncate-path text-xs text-muted-foreground">{subtitle}</div> : null}
        </div>
        {badges ? <div className="flex shrink-0 flex-wrap items-center justify-end gap-2">{badges}</div> : null}
      </div>
      <div className={cn("bg-black", bodyClassName)}>{children}</div>
      {footer ? (
        <div className="border-t border-border bg-card/70 px-3 py-2 text-xs leading-5 text-muted-foreground">
          {footer}
        </div>
      ) : null}
    </div>
  )
}

export function VideoPreviewFrame({
  src,
  fileUrl,
  placeholder = "暂无可预览视频",
  muted = false,
  className,
}: {
  src: string
  fileUrl: (path: string) => string
  placeholder?: string
  muted?: boolean
  className?: string
}) {
  const [mediaError, setMediaError] = useState(false)

  useEffect(() => {
    setMediaError(false)
  }, [src])

  return src ? (
    <div className={cn("relative aspect-video w-full bg-black", className)}>
      <video
        key={src}
        controls
        muted={muted}
        className="h-full w-full object-contain"
        src={fileUrl(src)}
        onError={() => setMediaError(true)}
      />
      {mediaError ? (
        <div className="absolute inset-x-3 bottom-3 rounded-md border border-warning/30 bg-black/80 px-3 py-2 text-xs leading-5 text-warning">
          当前浏览器无法解码这个视频。请用 Chrome/Edge 打开页面，或重新导出为浏览器支持的 H.264/AAC。
        </div>
      ) : null}
    </div>
  ) : (
    <div className={cn("flex aspect-video items-center justify-center px-4 text-center text-sm text-zinc-400", className)}>
      {placeholder}
    </div>
  )
}
