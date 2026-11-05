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

/** The SharePoint folder last opened for a client (task 4.17, decision 83): the way to it from
 * the list of sites. Kept as the screen saved it; `isStep` checks each step is still readable. */
const LAST_SHAREPOINT = 'clearbook.lastSharePoint.'

export function lastTrail<T>(clientId: string, isStep: (step: unknown) => step is T): T[] | undefined {
  try {
    const saved = localStorage.getItem(LAST_SHAREPOINT + clientId)
    const parsed: unknown = saved ? JSON.parse(saved) : undefined
    return Array.isArray(parsed) && parsed.every(isStep) ? parsed : undefined
  } catch {
    return undefined
  }
}

export function rememberTrail(clientId: string, trail: unknown[]): void {
  try {
    localStorage.setItem(LAST_SHAREPOINT + clientId, JSON.stringify(trail))
  } catch {
    // not remembered this time; nothing else depends on it
  }
}
