import type { AccountingStatus } from '@/api/client'

/** Whether a client can run (decision 79): coding suggests each line's account from the client's
 * accounts, so they must be there first, synced from its accounting system or imported. */
export type Readiness = 'checking' | 'ready' | 'not-synced' | 'reconnect' | 'not-connected' | 'unknown'

export function readinessOf(status: AccountingStatus | undefined, failed: boolean): Readiness {
  if (failed) return 'unknown' // never block a run because the status could not be read
  if (!status) return 'checking'
  if (status.needs_reconnect) return 'reconnect'
  if (status.connected) return status.master_data_from === status.provider ? 'ready' : 'not-synced'
  return status.accounts > 0 ? 'ready' : 'not-connected'
}

/** True while a run may not start yet; 'unknown' does not block. */
export function blocksRun(readiness: Readiness): boolean {
  return readiness !== 'ready' && readiness !== 'unknown'
}
