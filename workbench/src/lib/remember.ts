/** Per-computer conveniences (the last client picked). Storage may be blocked or cleared:
 * every call is safe, and the Workbench works the same without it. */
const LAST_CLIENT = 'clearbook.lastClient'
const LAST_CLIENT_BEFORE = 'clearbook.lastRestaurant' // older name: still read once

export function lastClient(): string | undefined {
  try {
    return localStorage.getItem(LAST_CLIENT) ?? localStorage.getItem(LAST_CLIENT_BEFORE) ?? undefined
  } catch {
    return undefined
  }
}

export function rememberClient(clientId: string): void {
  try {
    localStorage.setItem(LAST_CLIENT, clientId)
    localStorage.removeItem(LAST_CLIENT_BEFORE)
  } catch {
    // not remembered this time; nothing else depends on it
  }
}

/** The folder last run or opened for a client (task 4.17): its place and the path to it. */
export interface RememberedFolder {
  place: string
  crumbs: { name: string; path: string; date: string | null }[]
}

const LAST_FOLDER = 'clearbook.lastFolder.'

export function lastFolder(clientId: string): RememberedFolder | undefined {
  try {
    const saved = localStorage.getItem(LAST_FOLDER + clientId)
    const parsed: unknown = saved ? JSON.parse(saved) : undefined
    if (typeof parsed !== 'object' || parsed === null) return undefined
    const { place, crumbs } = parsed as Partial<RememberedFolder>
    return typeof place === 'string' && Array.isArray(crumbs) ? { place, crumbs } : undefined
  } catch {
    return undefined
  }
}

export function rememberFolder(clientId: string, folder: RememberedFolder): void {
  try {
    localStorage.setItem(LAST_FOLDER + clientId, JSON.stringify(folder))
  } catch {
    // not remembered this time; nothing else depends on it
  }
}
