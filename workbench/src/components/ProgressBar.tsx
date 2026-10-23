import { cn } from '@/lib/utils'

/** A thin progress bar; `value` from 0 to 1. */
export function ProgressBar({ value, label, className }: { value: number; label: string; className?: string }) {
  const percent = Math.round(Math.min(Math.max(value, 0), 1) * 100)
  return (
    <div
      role="progressbar"
      aria-label={label}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={percent}
      className={cn('h-2 w-full overflow-hidden rounded-full bg-muted', className)}
    >
      <div className="h-full rounded-full bg-primary transition-[width] duration-500" style={{ width: `${percent}%` }} />
    </div>
  )
}
