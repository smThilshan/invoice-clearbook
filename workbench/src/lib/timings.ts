/** Step timings (task 4.9): how long each pipeline step took, in words for the screen. */
import type { StepTimings } from '@/api/client'

/** 340 → "0.3 s", 6200 → "6.2 s", 14000 → "14 s", 228000 → "3 min 48 s"; null → "—". */
export function formatDuration(ms: number | null | undefined): string {
  if (ms === null || ms === undefined) return '—'
  if (ms < 10_000) return `${(ms / 1000).toFixed(1)} s`
  const seconds = Math.round(ms / 1000)
  if (seconds < 60) return `${seconds} s`
  const minutes = Math.floor(seconds / 60)
  const rest = seconds % 60
  return rest ? `${minutes} min ${rest} s` : `${minutes} min`
}

/** Time the machine spent on the document, without the time it waited in line. */
export function workMs(timings: StepTimings): number | null {
  if (timings.total_ms === null || timings.waiting_ms === null) return null
  return timings.total_ms - timings.waiting_ms
}

export interface RunTimeSummary {
  measured: number // documents with every step timed
  averageWorkMs: number | null // per document, without waiting
  totalWorkMs: number | null
}

export function summarize(all: (StepTimings | null | undefined)[]): RunTimeSummary {
  const work = all.flatMap((timings) => {
    const ms = timings ? workMs(timings) : null
    return ms === null ? [] : [ms]
  })
  if (work.length === 0) return { measured: 0, averageWorkMs: null, totalWorkMs: null }
  const total = work.reduce((sum, ms) => sum + ms, 0)
  return { measured: work.length, averageWorkMs: total / work.length, totalWorkMs: total }
}
