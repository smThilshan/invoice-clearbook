import { TriangleAlert } from 'lucide-react'

import { useReadiness } from '@/api/queries'
import type { ModelStatus, Readiness } from '@/api/client'

const ASK = 'Ask the person who runs the system to start it.'

const MODEL_DETAILS: Record<ModelStatus, string> = {
  ok: 'ready',
  unreachable: 'model server not reachable (make llm-load)',
  missing: 'model not installed',
  incompatible: 'wrong model variant (needs the Instruct one)',
  not_loaded: 'model not loaded with enough context (make llm-load)',
}

interface Problem {
  says: string // for the accountant: what does not work, and what still does
  detail: string // for the person who runs the system (shown on hover)
}

/** What is down, in the words of someone who uses the Workbench; worst first. */
function problems(readiness: Readiness): Problem[] {
  const found: Problem[] = []
  if (readiness.database !== 'ok') {
    found.push({
      says: 'Nothing can be loaded or saved right now.',
      detail: 'Database not reachable (make up)',
    })
  }
  if (readiness.redis !== 'ok') {
    found.push({
      says: 'New runs and sending bills cannot start right now. Reviewing still works.',
      detail: 'Job queue not reachable (make up)',
    })
  }
  if (readiness.model !== 'ok') {
    found.push({
      says: 'Invoices cannot be read right now. Reviews, approvals and sending still work.',
      detail: `AI model: ${MODEL_DETAILS[readiness.model]}`,
    })
  }
  return found
}

/** Nothing at all while everything the Workbench needs is running: users do not need to know.
 * When something is down, one plain banner: what does not work, what still does, who to ask.
 * The technical cause is in the tooltip, for whoever runs the system. (GET /health/ready) */
export function SystemStatus() {
  const { data, isPending, isError } = useReadiness()

  if (isPending) return null
  const found: Problem[] = isError
    ? [{ says: 'The Workbench cannot reach its service: nothing can be loaded or saved right now.',
         detail: 'API not reachable (make api)' }]  // prettier-ignore
    : problems(data)
  if (found.length === 0) return null

  return (
    <div
      role="alert"
      aria-label="System problem"
      title={found.map((p) => p.detail).join(' · ')}
      className="flex items-start gap-2 border-b border-destructive/30 bg-destructive/10 px-4 py-2 text-sm text-destructive"
    >
      <TriangleAlert className="mt-0.5 size-4 shrink-0" aria-hidden />
      <p>
        {found.map((p) => p.says).join(' ')} {ASK}
      </p>
    </div>
  )
}
