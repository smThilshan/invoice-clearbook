import { ArrowLeft, FileSearch, LoaderCircle } from 'lucide-react'
import { Link, useParams } from 'react-router'

import type { FileStatus, RunProgress } from '@/api/client'
import { useRestaurants, useRun } from '@/api/queries'
import { ErrorAlert } from '@/components/ErrorAlert'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { formatDate, statusLabel, TYPE_LABELS, WORKING } from '@/lib/labels'

const STATE_LABELS: Record<RunProgress['state'], string> = {
  scanning: 'Looking through the folder…',
  processing: 'Processing',
  finished: 'Finished',
}

/** Live progress of one run, refreshed until every file has settled (task 4.3). */
export function RunProgressPage() {
  const { runId = '' } = useParams()
  const run = useRun(runId)
  const restaurants = useRestaurants()

  if (run.isPending) return <Skeleton className="h-40 w-full max-w-4xl" />
  if (run.isError) {
    return (
      <div className="max-w-4xl space-y-4">
        <ErrorAlert title="This run could not be loaded" error={run.error} />
        <BackToRun />
      </div>
    )
  }

  const progress = run.data
  const restaurant = restaurants.data?.find((r) => r.client_id === progress.client_id)
  const finished = progress.state === 'finished'
  const working = progress.files.filter((file) => WORKING.has(file.status)).length

  return (
    <div className="max-w-4xl space-y-6">
      <div className="space-y-1">
        <h2 className="text-xl font-semibold">Run: {restaurant?.name ?? progress.client_id}</h2>
        <p className="text-sm text-muted-foreground">
          Folder <span className="font-medium text-foreground">{progress.folder || '(all)'}</span>
          {' · '}
          {progress.folder_date ? `date ${formatDate(progress.folder_date)}` : 'no folder date'}
        </p>
      </div>

      {progress.status === 'FAILED' ? (
        <Alert variant="destructive">
          <AlertTitle>The run stopped</AlertTitle>
          <AlertDescription>{progress.failure_reason ?? 'Unknown reason.'}</AlertDescription>
        </Alert>
      ) : (
        <div role="status" className="flex flex-wrap items-center gap-3 text-sm">
          {!finished && <LoaderCircle className="size-4 animate-spin" aria-hidden />}
          <span className="font-medium">{STATE_LABELS[progress.state]}</span>
          {progress.state !== 'scanning' && (
            <span className="text-muted-foreground">
              {progress.files_found} file{progress.files_found === 1 ? '' : 's'} found
              {working > 0 && `, ${working} still being worked on`}
            </span>
          )}
        </div>
      )}

      {Object.keys(progress.counts).length > 0 && (
        <ul aria-label="Files by status" className="flex flex-wrap gap-2">
          {Object.entries(progress.counts).map(([status, count]) => (
            <li key={status}>
              <Badge variant={status === 'FAILED' ? 'destructive' : 'secondary'}>
                {statusLabel(status)}: {count}
              </Badge>
            </li>
          ))}
        </ul>
      )}

      {progress.files.length > 0 && <FileTable files={progress.files} />}
      {progress.state !== 'scanning' && progress.files.length === 0 && (
        <p className="text-sm text-muted-foreground">No documents found in this folder.</p>
      )}

      <div className="flex flex-wrap gap-2">
        {finished && (
          <Button asChild>
            <Link to={`/queue?client=${encodeURIComponent(progress.client_id)}&run=${progress.run_id}`}>
              <FileSearch aria-hidden />
              Open review queue
            </Link>
          </Button>
        )}
        <BackToRun />
      </div>
    </div>
  )
}

function FileTable({ files }: { files: FileStatus[] }) {
  return (
    <div className="rounded-lg border">
      <Table aria-label="Files">
        <TableHeader>
          <TableRow>
            <TableHead>File</TableHead>
            <TableHead>Status</TableHead>
            <TableHead>Type</TableHead>
            <TableHead className="text-right">Pages</TableHead>
            <TableHead>Note</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {files.map((file) => (
            <TableRow key={file.file}>
              <TableCell className="font-medium">{file.file}</TableCell>
              <TableCell>
                <Badge variant={file.status === 'FAILED' ? 'destructive' : 'outline'}>
                  {statusLabel(file.status)}
                </Badge>
              </TableCell>
              <TableCell>{file.document_type ? TYPE_LABELS[file.document_type] : ''}</TableCell>
              <TableCell className="text-right">{file.pages ?? ''}</TableCell>
              <TableCell className="whitespace-normal text-muted-foreground">
                {file.failure_reason ?? (file.from_earlier_run ? 'Already taken in by an earlier run' : '')}
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  )
}

function BackToRun() {
  return (
    <Button variant="outline" asChild>
      <Link to="/run">
        <ArrowLeft aria-hidden />
        Start another run
      </Link>
    </Button>
  )
}
