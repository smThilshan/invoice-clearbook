import { useQuery } from '@tanstack/react-query'

import { api } from './client'

export const queryKeys = {
  readiness: ['readiness'] as const,
}

const READINESS_REFRESH_MS = 30_000

/** System status, refreshed every 30 seconds while the Workbench is open. */
export function useReadiness() {
  return useQuery({
    queryKey: queryKeys.readiness,
    queryFn: api.readiness,
    refetchInterval: READINESS_REFRESH_MS,
    retry: false, // show "API not reachable" straight away instead of retrying silently
  })
}
