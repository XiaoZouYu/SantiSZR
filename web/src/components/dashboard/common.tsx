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
