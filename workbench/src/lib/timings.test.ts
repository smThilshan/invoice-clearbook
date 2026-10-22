import { describe, expect, it } from 'vitest'

import type { StepTimings } from '@/api/client'

import { formatDuration, summarize, workMs } from './timings'

const timed = (total: number, waiting: number): StepTimings => ({
  waiting_ms: waiting,
  read_ms: 6_000,
  extract_ms: 200_000,
  coding_ms: 15_000,
  checks_ms: 1_000,
  total_ms: total,
})

const notMeasured: StepTimings = {
  waiting_ms: null,
  read_ms: null,
  extract_ms: 195_000,
  coding_ms: null,
  checks_ms: null,
  total_ms: null,
}

describe('formatDuration', () => {
  it.each([
    [null, '—'],
    [340, '0.3 s'],
    [6_200, '6.2 s'],
    [14_000, '14 s'],
    [59_600, '1 min'],
    [228_000, '3 min 48 s'],
  ])('%s ms → %s', (ms, text) => {
    expect(formatDuration(ms)).toBe(text)
  })
})

describe('work time', () => {
  it('leaves out the time spent waiting in line', () => {
    expect(workMs(timed(300_000, 78_000))).toBe(222_000)
  })

  it('is unknown when a step was not timed', () => {
    expect(workMs(notMeasured)).toBeNull()
  })

  it('averages only the documents with every step timed', () => {
    expect(summarize([timed(300_000, 78_000), timed(230_000, 0), notMeasured, null])).toEqual({
      measured: 2,
      averageWorkMs: 226_000,
      totalWorkMs: 452_000,
    })
  })

  it('has nothing to average before any document is timed', () => {
    expect(summarize([notMeasured])).toEqual({ measured: 0, averageWorkMs: null, totalWorkMs: null })
  })
})
