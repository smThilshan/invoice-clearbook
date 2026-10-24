import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import {
  api,
  ApiError,
  type ClientCreate,
  type DocumentDetail,
  type DocumentUpdate,
  type ValueKind,
  type QueueQuery,
  type RejectBody,
  type RunCreate,
  type RunProgress,
} from './client'

export const queryKeys = {
  readiness: ['readiness'] as const,
  clients: ['clients'] as const,
  places: (clientId: string) => ['places', clientId] as const,
  folder: (clientId: string, place: string, path: string) =>
    ['folder', clientId, place, path] as const,
  run: (runId: string) => ['run', runId] as const,
  runs: (clientId: string) => ['runs', clientId] as const,
  queue: (query: QueueQuery) => ['queue', query] as const,
  queues: ['queue'] as const,
  document: (id: string) => ['document', id] as const,
  codingOptions: (clientId: string) => ['coding-options', clientId] as const,
  locate: (id: string, kind: ValueKind, value: string) => ['locate', id, kind, value] as const,
}

const READINESS_REFRESH_MS = 30_000
export const RUN_REFRESH_MS = 3_000
export const QUEUE_REFRESH_MS = 30_000

/** System status, refreshed every 30 seconds while the Workbench is open. */
export function useReadiness() {
  return useQuery({
    queryKey: queryKeys.readiness,
    queryFn: api.readiness,
    refetchInterval: READINESS_REFRESH_MS,
    retry: false, // show "API not reachable" straight away instead of retrying silently
  })
}

export function useClients() {
  return useQuery({ queryKey: queryKeys.clients, queryFn: api.clients })
}

/** Creates a client; the client list is refreshed so it appears in every dropdown. */
export function useCreateClient() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (body: ClientCreate) => api.createClient(body),
    onSuccess: () => void client.invalidateQueries({ queryKey: queryKeys.clients }),
  })
}

/** `refreshMs`: poll while something is under way (sending bills). */
export function useAccounting(clientId: string, refreshMs: number | false = false) {
  return useQuery({
    queryKey: ['accounting', clientId],
    queryFn: () => api.accounting(clientId),
    refetchInterval: refreshMs,
  })
}

export function useDisconnectAccounting() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (clientId: string) => api.disconnectAccounting(clientId),
    onSuccess: (status, clientId) => client.setQueryData(['accounting', clientId], status),
  })
}

export function useSendWaiting() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (clientId: string) => api.sendWaiting(clientId),
    onSuccess: (status, clientId) => client.setQueryData(['accounting', clientId], status),
  })
}

/** After a sync, coding uses the accounting system's accounts and VAT rates. */
export function useSyncAccounting() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (clientId: string) => api.syncAccounting(clientId),
    onSuccess: (_result, clientId) => {
      void client.invalidateQueries({ queryKey: ['accounting', clientId] })
      void client.invalidateQueries({ queryKey: queryKeys.codingOptions(clientId) })
    },
  })
}

/** Where a folder may be picked; idle until a client is picked. */
export function usePlaces(clientId: string | undefined) {
  return useQuery({
    queryKey: queryKeys.places(clientId ?? ''),
    queryFn: () => api.places(clientId ?? ''),
    enabled: clientId !== undefined,
  })
}

/** Sub-folders of `path` in a place; idle until a client and a place are picked. */
export function useFolder(clientId: string | undefined, place: string | undefined, path: string) {
  return useQuery({
    queryKey: queryKeys.folder(clientId ?? '', place ?? '', path),
    queryFn: () => api.folder(clientId ?? '', place ?? '', path),
    enabled: clientId !== undefined && place !== undefined,
    retry: false, // a missing folder will not appear by retrying
  })
}

export function useTypicalTime() {
  return useQuery({ queryKey: ['typical-time'], queryFn: api.typicalTime, staleTime: 60_000 })
}

export function useStartRun() {
  return useMutation({ mutationFn: (body: RunCreate) => api.startRun(body) })
}

/** A client's latest runs, for the queue filter; idle until a client is picked. */
export function useRuns(clientId: string | undefined) {
  return useQuery({
    queryKey: queryKeys.runs(clientId ?? ''),
    queryFn: () => api.runs(clientId ?? ''),
    enabled: clientId !== undefined,
    refetchInterval: QUEUE_REFRESH_MS, // a run started elsewhere shows up by itself
  })
}

/** A run's progress, refreshed every few seconds until every file has settled. */
export function useRun(runId: string) {
  return useQuery({
    queryKey: queryKeys.run(runId),
    queryFn: () => api.run(runId),
    refetchInterval: (query) => {
      const data = query.state.data
      if (data?.state === 'finished') return false
      // Stopped and nothing still finishing its step: nothing will change until Resume.
      if (data?.status === 'STOPPED' && !data.files.some((f) => ['reading', 'ai', 'checks'].includes(f.step))) {
        return false
      }
      if (query.state.error instanceof ApiError && query.state.error.status === 404) return false
      return RUN_REFRESH_MS
    },
  })
}

/** One page of the review queue; refreshed so documents of a running run appear by themselves.
 * The previous page stays on screen while the next one loads (no flicker when paging). */
export function useQueue(query: QueueQuery | undefined) {
  return useQuery({
    queryKey: queryKeys.queue(query ?? { client_id: '', limit: 0, offset: 0 }),
    queryFn: () => api.queue(query as QueueQuery),
    enabled: query !== undefined,
    refetchInterval: QUEUE_REFRESH_MS,
    placeholderData: keepPreviousData,
  })
}

export function useDocument(documentId: string) {
  return useQuery({ queryKey: queryKeys.document(documentId), queryFn: () => api.document(documentId) })
}

/** Idle until the document (and so its client) is known. */
export function useCodingOptions(clientId: string | undefined) {
  return useQuery({
    queryKey: queryKeys.codingOptions(clientId ?? ''),
    queryFn: () => api.codingOptions(clientId ?? ''),
    enabled: clientId !== undefined,
    staleTime: 5 * 60_000, // master data changes rarely
  })
}

/** The server's answer replaces the cached document; queue lists are refreshed. */
function useDocumentChange<T>(change: (id: string, body: T) => Promise<DocumentDetail>) {
  const client = useQueryClient()
  return useMutation({
    mutationFn: ({ id, body }: { id: string; body: T }) => change(id, body),
    onSuccess: (document) => {
      client.setQueryData(queryKeys.document(document.document_id), document)
      void client.invalidateQueries({ queryKey: queryKeys.queues })
    },
  })
}

export function useUpdateDocument() {
  return useDocumentChange<DocumentUpdate>(api.updateDocument)
}

export function useApproveDocument() {
  return useDocumentChange<null>((id) => api.approveDocument(id))
}

/** Stop or resume a run (task 4.19); the run page shows the answer straight away. */
function useRunControl(control: (runId: string) => Promise<RunProgress>) {
  const client = useQueryClient()
  return useMutation({
    mutationFn: control,
    onSuccess: (progress) => client.setQueryData(queryKeys.run(progress.run_id), progress),
  })
}

export function useStopRun() {
  return useRunControl(api.stopRun)
}

export function useForceStopRun() {
  return useRunControl(api.forceStopRun)
}

export function useResumeRun() {
  return useRunControl(api.resumeRun)
}

/** Retry a failed file; the run it belongs to refreshes. */
export function useRetryDocument() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => api.retryDocument(id),
    onSuccess: () => void client.invalidateQueries({ queryKey: ['run'] }),
  })
}

export function useRejectDocument() {
  return useDocumentChange<RejectBody>(api.rejectDocument)
}

export interface LocateTarget {
  kind: ValueKind
  value: string
}

/** Where the active field's value is printed. The pages never change, so each value is asked
 * once; an empty value asks nothing. */
export function useLocate(documentId: string, target: LocateTarget | null) {
  const value = target?.value.trim() ?? ''
  return useQuery({
    queryKey: queryKeys.locate(documentId, target?.kind ?? 'text', value),
    queryFn: () => api.locate(documentId, target?.kind ?? 'text', value),
    enabled: target !== null && value !== '',
    staleTime: Infinity,
  })
}
