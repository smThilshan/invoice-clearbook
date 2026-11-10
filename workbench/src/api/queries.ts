import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import {
  api,
  ApiError,
  type ChosenFile,
  type ClientCreate,
  type ClientSettings,
  type LineGrouping,
  type LoginBody,
  type Me,
  type DocumentDetail,
  type DocumentUpdate,
  type ValueKind,
  type QueueQuery,
  type RejectBody,
  type SharePointRunCreate,
  type RunProgress,
  uploadRun,
} from './client'
import { WORKING } from '@/lib/labels'

export const queryKeys = {
  me: ['me'] as const,
  readiness: ['readiness'] as const,
  clients: ['clients'] as const,
  removedClients: ['clients', 'removed'] as const,
  sharePoint: ['sharepoint'] as const,
  run: (runId: string) => ['run', runId] as const,
  runs: (clientId: string) => ['runs', clientId] as const,
  queue: (query: QueueQuery) => ['queue', query] as const,
  queues: ['queue'] as const,
  document: (id: string) => ['document', id] as const,
  answerKey: (id: string) => ['answer-key', id] as const,
  codingOptions: (clientId: string) => ['coding-options', clientId] as const,
  locate: (id: string, kind: ValueKind, value: string) => ['locate', id, kind, value] as const,
}

const READINESS_REFRESH_MS = 30_000
export const RUN_REFRESH_MS = 3_000
export const QUEUE_REFRESH_MS = 30_000

/** Who is logged in; null when nobody is (decision 71). A 401 from any other call sets it to
 * null too (api/queryClient.ts). */
export function useMe() {
  return useQuery({
    queryKey: queryKeys.me,
    queryFn: async (): Promise<Me | null> => {
      try {
        return await api.me()
      } catch (error) {
        if (error instanceof ApiError && error.status === 401) return null
        throw error
      }
    },
    staleTime: 5 * 60_000,
    retry: false,
  })
}

export function useLogin() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (body: LoginBody) => api.login(body),
    onSuccess: (me) => client.setQueryData(queryKeys.me, me),
  })
}

/** Everything cached belonged to the session: it all goes. */
export function useLogout() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: api.logout,
    onSettled: () => {
      client.clear()
      client.setQueryData(queryKeys.me, null)
    },
  })
}

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

/** Decision 91: only an admin may ask, so the page asks only for one. */
export function useRemovedClients(enabled: boolean) {
  return useQuery({ queryKey: queryKeys.removedClients, queryFn: api.removedClients, enabled })
}

/** Removing or restoring changes both lists; `clients` is a prefix of `removedClients`. */
export function useRemoveClient() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (clientId: string) => api.removeClient(clientId),
    onSuccess: () => void client.invalidateQueries({ queryKey: queryKeys.clients }),
  })
}

export function useRestoreClient() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (clientId: string) => api.restoreClient(clientId),
    onSuccess: () => void client.invalidateQueries({ queryKey: queryKeys.clients }),
  })
}

/** Asked when the delete dialog opens, so the counts are current. */
export function useDeletionPreview(clientId: string, enabled: boolean) {
  return useQuery({
    queryKey: ['deletion-preview', clientId],
    queryFn: () => api.deletionPreview(clientId),
    enabled,
    staleTime: 0,
  })
}

export function useDeleteClient() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: ({ clientId, name }: { clientId: string; name: string }) => api.deleteClient(clientId, name),
    onSuccess: () => void client.invalidateQueries({ queryKey: queryKeys.clients }),
  })
}

export function useSetLineGrouping() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: ({ clientId, lineGrouping }: { clientId: string; lineGrouping: LineGrouping }) =>
      api.setLineGrouping(clientId, lineGrouping),
    onSuccess: () => void client.invalidateQueries({ queryKey: queryKeys.clients }),
  })
}

export function useSetExpenseSending() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: ({
      clientId,
      settings,
    }: {
      clientId: string
      settings: Pick<ClientSettings, 'expenses_sent_as' | 'paid_through'>
    }) => api.setExpenseSending(clientId, settings),
    onSuccess: () => void client.invalidateQueries({ queryKey: queryKeys.clients }),
  })
}

/** `refreshMs`: poll while something is under way (sending bills). */
export function useAccounting(clientId: string, refreshMs: number | false = false) {
  return useQuery({
    queryKey: ['accounting', clientId],
    queryFn: () => api.accounting(clientId),
    refetchInterval: refreshMs,
    enabled: clientId !== '',
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

/** Missan's SharePoint sign-in (decision 83). */
export function useSharePointStatus() {
  return useQuery({ queryKey: [...queryKeys.sharePoint, 'status'], queryFn: api.sharePointStatus })
}

export function useDisconnectSharePoint() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: api.disconnectSharePoint,
    onSuccess: () => client.invalidateQueries({ queryKey: queryKeys.sharePoint }),
  })
}

/** SharePoint answers are read live, every time: what is there now is what a run takes. A
 * refused sign-in is marked by the server, so the status is read again after an error. */
function useSharePoint<T>(key: readonly unknown[], read: () => Promise<T>, enabled = true) {
  const client = useQueryClient()
  return useQuery({
    queryKey: [...queryKeys.sharePoint, ...key],
    queryFn: async () => {
      try {
        return await read()
      } catch (error) {
        void client.invalidateQueries({ queryKey: [...queryKeys.sharePoint, 'status'] })
        throw error
      }
    },
    enabled,
    retry: false,
  })
}

export function useSharePointSites(enabled: boolean) {
  return useSharePoint(['sites'], api.sharePointSites, enabled)
}

export function useSharePointLibraries(siteId: string | undefined) {
  return useSharePoint(['libraries', siteId], () => api.sharePointLibraries(siteId ?? ''), siteId !== undefined)
}

export function useSharePointFolder(driveId: string | undefined, itemId: string | undefined) {
  return useSharePoint(
    ['folder', driveId, itemId],
    () => api.sharePointFolder(driveId ?? '', itemId ?? ''),
    driveId !== undefined && itemId !== undefined,
  )
}

export function useSharePointLink() {
  return useMutation({ mutationFn: api.sharePointLink })
}

export function useTypicalTime() {
  return useQuery({ queryKey: ['typical-time'], queryFn: api.typicalTime, staleTime: 60_000 })
}

export function useStartSharePointRun() {
  return useMutation({ mutationFn: (body: SharePointRunCreate) => api.startSharePointRun(body) })
}

/** Upload files chosen on this computer and start their run (decision 78). */
export function useUploadRun() {
  return useMutation({
    mutationFn: (upload: {
      clientId: string
      files: ChosenFile[]
      folderDate: string | null
      onProgress: (fraction: number) => void
    }) => uploadRun(upload.clientId, upload.files, upload.folderDate, upload.onProgress),
  })
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

/** Sending states that change within seconds (after Approve or Try again). */
const MOVING = new Set(['waiting', 'sending'])

/** Refreshed every few seconds while the document is still being read (after "Read again") or
 * on its way to the accounting system. */
export function useDocument(documentId: string) {
  return useQuery({
    queryKey: queryKeys.document(documentId),
    queryFn: () => api.document(documentId),
    refetchInterval: (query) => {
      const data = query.state.data
      const busy = WORKING.has(data?.status ?? '') || MOVING.has(data?.sending?.state ?? '')
      return busy ? RUN_REFRESH_MS : false
    },
  })
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

export function useReextractDocument() {
  return useDocumentChange<null>((id) => api.reextractDocument(id))
}

export function useSendAgain() {
  return useDocumentChange<null>((id) => api.sendAgain(id))
}

export function useSendBack() {
  return useDocumentChange<null>((id) => api.sendBack(id))
}

export function useReopenDeleted() {
  return useDocumentChange<null>((id) => api.reopenDeleted(id))
}

export function useRestoreDocument() {
  return useDocumentChange<null>((id) => api.restoreDocument(id))
}

export function useDeleteDocument() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => api.deleteDocument(id),
    onSuccess: (_answer, id) => {
      client.removeQueries({ queryKey: queryKeys.document(id) })
      void client.invalidateQueries({ queryKey: queryKeys.queues })
    },
  })
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

/** The answer key mark of one document (decision 63). */
export function useAnswerKey(documentId: string) {
  return useQuery({ queryKey: queryKeys.answerKey(documentId), queryFn: () => api.answerKey(documentId) })
}

export function useMarkCorrect(documentId: string) {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (correct: boolean) => (correct ? api.markCorrect(documentId) : api.unmarkCorrect(documentId)),
    onSuccess: (answer) => client.setQueryData(queryKeys.answerKey(documentId), answer),
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
