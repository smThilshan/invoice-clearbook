import { CalendarDays } from 'lucide-react'
import { type ComponentProps, useRef, useState } from 'react'

import { Input } from '@/components/ui/input'
import { fromDayMonthYear, isIsoDate, toDayMonthYear } from '@/lib/dates'
import { formatDate } from '@/lib/labels'

/** A date typed or picked as dd/mm/yyyy for everyone, never in the computer's own order (one
 * accountant saw 17/10, another 10/17). `value` and `onChange` are 2026-10-17; text that is not
 * a real day/month/year date is passed on as typed, flagged here, and never guessed into one. */
export function DateInput({
  value,
  onChange,
  className,
  ...props
}: Omit<ComponentProps<typeof Input>, 'type' | 'value' | 'onChange'> & {
  value: string
  onChange: (value: string) => void
}) {
  const picker = useRef<HTMLInputElement>(null)
  const [editing, setEditing] = useState(false)
  const typed = value !== '' && !isIsoDate(value)
  const flagged = typed && !editing // said once the typing ends, not at every key
  const hintId = props.id ? `${props.id}-hint` : undefined

  function openPicker() {
    const input = picker.current
    if (!input) return
    try {
      input.showPicker()
    } catch {
      input.click() // browsers without showPicker
    }
  }

  return (
    <div className="space-y-1">
      <div className="relative">
        <Input
          {...props}
          type="text"
          inputMode="numeric"
          placeholder="dd/mm/yyyy"
          autoComplete="off"
          aria-describedby={hintId}
          aria-invalid={flagged || props['aria-invalid'] || undefined}
          className={`pr-9 tabular-nums ${className ?? ''}`}
          value={toDayMonthYear(value)}
          onChange={(event) => onChange(fromDayMonthYear(event.target.value, { full: true }) ?? event.target.value)}
          onFocus={(event) => {
            setEditing(true)
            props.onFocus?.(event)
          }}
          onBlur={(event) => {
            setEditing(false)
            const date = fromDayMonthYear(event.target.value) // 7/1/2026 is taken once typing ends
            if (date && date !== value) onChange(date)
            props.onBlur?.(event)
          }}
        />
        <button
          type="button"
          aria-label="Pick from calendar"
          disabled={props.disabled || props.readOnly}
          className="absolute inset-y-0 right-0 flex w-9 items-center justify-center rounded-r-lg text-muted-foreground outline-none hover:text-foreground focus-visible:ring-3 focus-visible:ring-ring/50 disabled:pointer-events-none disabled:opacity-50"
          onClick={openPicker}
        >
          <CalendarDays className="size-4" aria-hidden />
        </button>
        {/* The browser's own calendar, opened by the button; only its pick is used. */}
        <input
          ref={picker}
          type="date"
          tabIndex={-1}
          aria-hidden
          data-testid={props.id ? `${props.id}-calendar` : undefined}
          className="pointer-events-none absolute right-0 bottom-0 size-px opacity-0"
          value={typed ? '' : value}
          onChange={(event) => onChange(event.target.value)}
        />
      </div>
      <p id={hintId} className={`text-xs ${flagged ? 'text-destructive' : 'text-muted-foreground'}`}>
        {flagged
          ? 'Not a date: write day/month/year, for example 17/10/2026.'
          : isIsoDate(value) ? formatDate(value) : 'Day/month/year'}
      </p>
    </div>
  )
}
