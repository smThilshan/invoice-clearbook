import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import {
  api,
  ApiError,
  type DocumentDetail,
  type DocumentUpdate,
  type QueueQuery,
  type RejectBody,
  type RunCreate,
} from './client'

export const queryKeys = {
  readiness: ['readiness'] as const,
  restaurants: ['restaurants'] as const,
  folders: (clientId: string, path: string) => ['folders', clientId, path] as const,
  run: (runId: string) => ['run', runId] as const,
  queue: (query: QueueQuery) => ['queue', query] as const,
  queues: ['queue'] as const,
  document: (id: string) => ['document', id] as const,
  codingOptions: (clientId: string) => ['coding-options', clientId] as const,
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

export function useRestaurants() {
  return useQuery({ queryKey: queryKeys.restaurants, queryFn: api.restaurants })
}

/** Sub-folders of `path`; idle until a restaurant is picked. */
export function useFolders(clientId: string | undefined, path: string) {
  return useQuery({
    queryKey: queryKeys.folders(clientId ?? '', path),
    queryFn: () => api.folders(clientId ?? '', path),
    enabled: clientId !== undefined,
    retry: false, // a missing folder will not appear by retrying
  })
}

export function useStartRun() {
  return useMutation({ mutationFn: (body: RunCreate) => api.startRun(body) })
}

/** A run's progress, refreshed every few seconds until every file has settled. */
export function useRun(runId: string) {
  return useQuery({
    queryKey: queryKeys.run(runId),
    queryFn: () => api.run(runId),
    refetchInterval: (query) => {
      if (query.state.data?.state === 'finished') return false
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

/** Idle until the document (and so its restaurant) is known. */
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

export function useRejectDocument() {
  return useDocumentChange<RejectBody>(api.rejectDocument)
}
