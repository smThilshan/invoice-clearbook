import {
  ArrowLeft,
  CircleCheck,
  CircleDashed,
  CircleX,
  Clock,
  CirclePause,
  FileSearch,
  LoaderCircle,
  Play,
  RotateCcw,
  Square,
} from 'lucide-react'
import { useState } from 'react'
import { Link, useParams } from 'react-router'

import type { FileStatus, FileStep, RunProgress } from '@/api/client'
import {
  useClients,
  useForceStopRun,
  useResumeRun,
  useRetryDocument,
  useRun,
  useStopRun,
} from '@/api/queries'
import { ErrorAlert } from '@/components/ErrorAlert'
import { PageHeader } from '@/components/PageHeader'
import { ProgressBar } from '@/components/ProgressBar'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from '@/components/ui/dialog'
import { Skeleton } from '@/components/ui/skeleton'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { formatDate, statusLabel } from '@/lib/labels'
import { formatDuration, summarize } from '@/lib/timings'
import { useNow } from '@/lib/useNow'
import type { ReviewContext } from '@/lib/reviewList'
import { usePageTitle } from '@/lib/usePageTitle'
import { cn } from '@/lib/utils'

const SETTLED: ReadonlySet<FileStep> = new Set(['done', 'failed'])
const WORKING: ReadonlySet<FileStep> = new Set(['reading', 'ai', 'checks'])
const OPENABLE = new Set(['IN_REVIEW', 'NON_POSTABLE'])

/** Live progress of one run (tasks 4.3, 4.14): each file's step with a running timer, the
 * overall progress with the time left, and the finished files ready to open. Refreshed every
 * few seconds until every file has settled. */
export function RunProgressPage() {
  const { runId = '' } = useParams()
  const run = useRun(runId)
  const clients = useClients()
  const progress = run.data
  const finished = progress?.state === 'finished'
  const now = useNow(progress !== undefined && !finished)
  // Timers use the server's clock: the offset is measured when the data arrives.
  const offset = progress ? Date.parse(progress.server_time) - run.dataUpdatedAt : 0
  const serverNow = now + offset

  const client = clients.data?.find((c) => c.client_id === progress?.client_id)
  const total = progress?.files.length ?? 0
  const settled = progress?.files.filter((f) => SETTLED.has(f.step)).length ?? 0
  const stopped = progress?.status === 'STOPPED'
  usePageTitle(
    progress
      ? `${finished ? 'Finished' : stopped ? 'Stopped' : `(${settled}/${total})`} Run · ${client?.name ?? progress.client_id}`
      : 'Run',
  )

  if (run.isPending) return <Skeleton className="h-40 w-full max-w-4xl" />
  if (run.isError || !progress) {
    return (
      <div className="max-w-4xl space-y-4">
        <ErrorAlert title="This run could not be loaded" error={run.error} />
        <BackToRun />
      </div>
    )
  }

  const left = finished || stopped ? 0 : timeLeft(progress, serverNow)
  const failed = progress.files.filter((f) => f.step === 'failed').length
  const paused = progress.files.filter((f) => f.step === 'stopped').length
  // Stopped, but a step already under way finishes first (task 4.19).
  const finishing = stopped && progress.files.some((f) => WORKING.has(f.step))
  const running = progress.status === 'RUNNING' && !finished
  const queueLink = `/queue?client=${encodeURIComponent(progress.client_id)}&run=${progress.run_id}`
  // Opening a finished file reviews this run's finished files in order (task 4.15).
  const review: ReviewContext = {
    from: `/run/${progress.run_id}`,
    ids: progress.files.flatMap((f) => (f.step === 'done' && f.document_id && OPENABLE.has(f.status) ? [f.document_id] : [])),
  }

  return (
    <div className="max-w-4xl space-y-6">
      <PageHeader
        title={`Run: ${client?.name ?? progress.client_id}`}
        description={
          <>
            Folder <span className="font-medium text-foreground">{progress.folder || '(all)'}</span>
            {' · '}
            {progress.folder_date ? `date ${formatDate(progress.folder_date)}` : 'no folder date'}
          </>
        }
        actions={
          running ? (
            <StopRunButton runId={progress.run_id} />
          ) : stopped ? (
            <>
              {finishing && <ForceStopButton runId={progress.run_id} asked={progress.force_stop} />}
              <ResumeRunButton runId={progress.run_id} />
            </>
          ) : null
        }
      />

      {progress.status === 'FAILED' ? (
        <Alert variant="destructive">
          <AlertTitle>The run could not continue</AlertTitle>
          <AlertDescription>{progress.failure_reason ?? 'Unknown reason.'}</AlertDescription>
        </Alert>
      ) : progress.state === 'scanning' ? (
        <div role="status" className="flex items-center gap-2 text-sm">
          <LoaderCircle className="size-4 animate-spin text-info" aria-hidden />
          Looking through the folder…
        </div>
      ) : (
        <div className="space-y-2">
          <div role="status" className="flex flex-wrap items-baseline justify-between gap-2 text-sm">
            <span className="font-medium">
              {finishing
                ? progress.force_stop
                  ? 'Force stopping: ending the AI read now'
                  : 'Stopping: finishing the current file'
                : `${finished ? 'Finished' : stopped ? 'Stopped' : 'Processing'}: ${settled} of ${total} file${total === 1 ? '' : 's'} done`}
              {failed > 0 && <span className="text-destructive"> · {failed} failed</span>}
              {paused > 0 && <span className="text-muted-foreground"> · {paused} stopped</span>}
            </span>
            {!finished && !stopped && left !== null && (
              <span className="text-muted-foreground">about {formatDuration(left)} left</span>
            )}
          </div>
          <ProgressBar value={total ? settled / total : 0} label="Run progress" />
        </div>
      )}

      {progress.files.length > 0 && (
        <ol aria-label="Files" className="divide-y rounded-lg border">
          {progress.files.map((file) => (
            <FileRow key={file.file} file={file} serverNow={serverNow} typicalMs={progress.typical_ms}
              review={review} />
          ))}
        </ol>
      )}
      {progress.state !== 'scanning' && progress.files.length === 0 && (
        <p className="text-sm text-muted-foreground">No documents found in this folder.</p>
      )}

      {progress.files.some((file) => file.timings) && (
        <details className="group rounded-lg border p-3">
          <summary className="cursor-pointer text-sm font-medium">Time per step</summary>
          <div className="mt-3">
            <TimingTable files={progress.files} />
          </div>
        </details>
      )}

      <div className="flex flex-wrap gap-2">
        {settled > 0 && (
          <Button asChild variant={finished ? 'default' : 'outline'}>
            <Link to={queueLink}>
              <FileSearch aria-hidden />
              {finished ? 'Open review queue' : 'Review the finished ones'}
            </Link>
          </Button>
        )}
        <BackToRun />
      </div>
    </div>
  )
}

/** One file: where it is now, with a running timer while it is being worked on. */
function FileRow({
  file,
  serverNow,
  typicalMs,
  review,
}: {
  file: FileStatus
  serverNow: number
  typicalMs: number | null
  review: ReviewContext
}) {
  const retry = useRetryDocument()
  const elapsed = file.step_since ? Math.max(0, serverNow - Date.parse(file.step_since)) : null
  const openable = file.step === 'done' && file.document_id && OPENABLE.has(file.status)
  return (
    <li aria-label={file.file} className={cn('flex flex-wrap items-center gap-3 px-3 py-2.5', WORKING.has(file.step) && 'bg-info-soft')}>
      <StepIcon step={file.step} />
      <div className="min-w-0 flex-1">
        <p className="truncate text-sm font-medium" title={file.file}>
          {file.file}
        </p>
        <p className={cn('text-xs', file.step === 'failed' ? 'text-destructive' : 'text-muted-foreground')}>
          {stepText(file, elapsed, typicalMs)}
        </p>
      </div>
      {openable && file.document_id && (
        <Button asChild size="sm" variant="outline">
          <Link to={`/documents/${file.document_id}`} state={review}>Open</Link>
        </Button>
      )}
      {file.step === 'failed' && file.document_id && (
        <Button
          size="sm"
          variant="outline"
          disabled={retry.isPending}
          onClick={() => retry.mutate(file.document_id ?? '')}
        >
          <RotateCcw aria-hidden />
          Retry
        </Button>
      )}
      {retry.isError && <ErrorAlert title="Not retried" error={retry.error} />}
    </li>
  )
}

function StepIcon({ step }: { step: FileStep }) {
  if (step === 'done') return <CircleCheck className="size-5 shrink-0 text-success" aria-label="Done" />
  if (step === 'failed') return <CircleX className="size-5 shrink-0 text-destructive" aria-label="Failed" />
  if (WORKING.has(step)) {
    return <LoaderCircle className="size-5 shrink-0 animate-spin text-info" aria-label="Working" />
  }
  if (step === 'stopped') return <CirclePause className="size-5 shrink-0 text-muted-foreground" aria-label="Stopped" />
  if (step === 'queued') return <CircleDashed className="size-5 shrink-0 text-muted-foreground" aria-label="Not started" />
  return <Clock className="size-5 shrink-0 text-muted-foreground" aria-label="Waiting" />
}

function stepText(file: FileStatus, elapsed: number | null, typicalMs: number | null): string {
  const timer = elapsed !== null ? ` · ${clock(elapsed)}` : ''
  switch (file.step) {
    case 'queued':
      return 'Waiting to be taken in'
    case 'waiting':
      return 'Waiting for the next step'
    case 'reading':
      return `Reading the pages${timer}`
    case 'waiting_ai':
      return file.queue_position && file.queue_position > 1
        ? `Waiting for the AI · ${ordinal(file.queue_position)} in line`
        : 'Next for the AI'
    case 'ai':
      return `AI reading${timer}${typicalMs ? ` (a file usually takes about ${formatDuration(typicalMs)})` : ''}`
    case 'checks':
      return `Checking${timer}`
    case 'failed':
      return file.failure_reason ?? 'Failed'
    case 'stopped':
      return 'Stopped: Resume continues it'
    default:
      return file.from_earlier_run ? 'Already taken in by an earlier run' : statusLabel(file.status)
  }
}

/** Time left: the typical time for every file still to go, less what the working one has done. */
function timeLeft(progress: RunProgress, serverNow: number): number | null {
  const typical = progress.typical_ms
  if (!typical) return null
  let left = 0
  for (const file of progress.files) {
    if (SETTLED.has(file.step)) continue
    const done = file.step_since && WORKING.has(file.step) ? serverNow - Date.parse(file.step_since) : 0
    left += Math.max(typical - done, 0)
  }
  return left
}

/** 151000 → "2:31". */
function clock(ms: number): string {
  const seconds = Math.floor(ms / 1000)
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}`
}

function ordinal(n: number): string {
  const suffix = n % 10 === 1 && n % 100 !== 11 ? 'st' : n % 10 === 2 && n % 100 !== 12 ? 'nd' : n % 10 === 3 && n % 100 !== 13 ? 'rd' : 'th'
  return `${n}${suffix}`
}

/** Where the time went, per file (task 4.9). Waiting = queued behind other files: the worker
 * does one job at a time, so the work columns are the real time per invoice. */
function TimingTable({ files }: { files: FileStatus[] }) {
  const timed = files.filter((file) => file.timings)
  const summary = summarize(timed.map((file) => file.timings))
  return (
    <section aria-label="Time per step" className="space-y-2">
      <div className="rounded-lg border">
        <Table aria-label="Time per step">
          <TableHeader>
            <TableRow>
              <TableHead>File</TableHead>
              <TableHead className="text-right">Waiting</TableHead>
              <TableHead className="text-right">Read</TableHead>
              <TableHead className="text-right">Extract (model)</TableHead>
              <TableHead className="text-right">Coding (model)</TableHead>
              <TableHead className="text-right">Checks</TableHead>
              <TableHead className="text-right">Total</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {timed.map(({ file, timings: t }) => (
              <TableRow key={file}>
                <TableCell className="font-medium">{file}</TableCell>
                <TableCell className="text-right text-muted-foreground">
                  {formatDuration(t?.waiting_ms)}
                </TableCell>
                <TableCell className="text-right">{formatDuration(t?.read_ms)}</TableCell>
                <TableCell className="text-right">{formatDuration(t?.extract_ms)}</TableCell>
                <TableCell className="text-right">{formatDuration(t?.coding_ms)}</TableCell>
                <TableCell className="text-right">{formatDuration(t?.checks_ms)}</TableCell>
                <TableCell className="text-right font-medium">{formatDuration(t?.total_ms)}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>
      <p className="text-sm text-muted-foreground">
        {summary.measured > 0 ? (
          <>
            Per invoice, without waiting:{' '}
            <span className="font-medium text-foreground">{formatDuration(summary.averageWorkMs)}</span>{' '}
            on average ({summary.measured} timed, {formatDuration(summary.totalWorkMs)} in all).{' '}
          </>
        ) : null}
        Waiting is time in line behind other files. — = not measured yet, or processed before
        timing was added.
      </p>
    </section>
  )
}

/** Stop, with a confirmation that says what happens (task 4.19). */
function StopRunButton({ runId }: { runId: string }) {
  const stop = useStopRun()
  const [open, setOpen] = useState(false)
  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button variant="outline">
          <Square aria-hidden />
          Stop run
        </Button>
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Stop this run?</DialogTitle>
          <DialogDescription>
            Files not started yet stay as they are: Resume continues them later. A file the AI
            is reading right now finishes first (up to a few minutes). Files already in review
            are not affected.
          </DialogDescription>
        </DialogHeader>
        {stop.isError && <ErrorAlert title="Not stopped" error={stop.error} />}
        <DialogFooter>
          <Button variant="outline" onClick={() => setOpen(false)}>
            Keep running
          </Button>
          <Button
            variant="destructive"
            disabled={stop.isPending}
            onClick={() => stop.mutate(runId, { onSuccess: () => setOpen(false) })}
          >
            {stop.isPending ? 'Stopping…' : 'Stop run'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

/** Task 4.20: end the AI read still under way now instead of waiting for it. */
function ForceStopButton({ runId, asked }: { runId: string; asked: boolean }) {
  const force = useForceStopRun()
  const [open, setOpen] = useState(false)
  if (asked) {
    return (
      <Button variant="outline" disabled>
        <LoaderCircle className="animate-spin" aria-hidden />
        Force stopping…
      </Button>
    )
  }
  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button variant="outline">
          <Square aria-hidden />
          Force stop now
        </Button>
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>End the AI reading now?</DialogTitle>
          <DialogDescription>
            The file the AI is reading is abandoned within a few seconds. Nothing of it is
            saved: it is read again from the start when you resume. Other files are not affected.
          </DialogDescription>
        </DialogHeader>
        {force.isError && <ErrorAlert title="Not force-stopped" error={force.error} />}
        <DialogFooter>
          <Button variant="outline" onClick={() => setOpen(false)}>
            Let it finish
          </Button>
          <Button
            variant="destructive"
            disabled={force.isPending}
            onClick={() => force.mutate(runId, { onSuccess: () => setOpen(false) })}
          >
            Force stop now
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

function ResumeRunButton({ runId }: { runId: string }) {
  const resume = useResumeRun()
  return (
    <>
      <Button onClick={() => resume.mutate(runId)} disabled={resume.isPending}>
        <Play aria-hidden />
        {resume.isPending ? 'Resuming…' : 'Resume'}
      </Button>
      {resume.isError && <ErrorAlert title="Not resumed" error={resume.error} />}
    </>
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
