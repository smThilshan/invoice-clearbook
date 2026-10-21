import { useMutation, useQuery } from '@tanstack/react-query'

import { api, ApiError, type RunCreate } from './client'

export const queryKeys = {
  readiness: ['readiness'] as const,
  restaurants: ['restaurants'] as const,
  folders: (clientId: string, path: string) => ['folders', clientId, path] as const,
  run: (runId: string) => ['run', runId] as const,
}

const READINESS_REFRESH_MS = 30_000
export const RUN_REFRESH_MS = 3_000

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
