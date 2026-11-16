import { Loader2, Pause } from 'lucide-react'
import { Link } from 'react-router'

import type { OpenRun } from '@/api/client'
import { useOpenRuns } from '@/api/queries'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import { formatDateTime } from '@/lib/labels'

/** Runs still going or stopped part way, so a run left for another screen is found again;
 * the form below still starts another one. */
export function OpenRuns() {
  const runs = useOpenRuns().data ?? []
  if (runs.length === 0) return null
  return (
    <Alert>
      <Loader2 className="animate-spin" aria-hidden />
      <AlertTitle>{runs.length === 1 ? 'A run is not finished yet' : `${runs.length} runs are not finished yet`}</AlertTitle>
      <AlertDescription>
        <ul aria-label="Runs not finished" className="mt-2 w-full space-y-2">
          {runs.map((run) => (
            <li key={run.run_id} className="flex flex-wrap items-center justify-between gap-2">
              <span>
                <span className="font-medium text-foreground">{run.client_name}</span>
                {run.folder && ` · ${run.folder}`} · {progressOf(run)} · started {formatDateTime(run.created_at)}
              </span>
              <Button size="sm" variant="outline" asChild>
                <Link to={`/run/${run.run_id}`} aria-label={`Open the run of ${run.client_name}`}>
                  {run.status === 'STOPPED' && <Pause aria-hidden />}
                  Open
                </Link>
              </Button>
            </li>
          ))}
        </ul>
      </AlertDescription>
    </Alert>
  )
}

function progressOf(run: OpenRun): string {
  if (run.status === 'STOPPED') return `stopped, ${run.settled} of ${run.files_found} done`
  if (run.state === 'fetching') return 'fetching from SharePoint'
  if (run.state === 'scanning') return 'looking for files'
  return `${run.settled} of ${run.files_found} done`
}
