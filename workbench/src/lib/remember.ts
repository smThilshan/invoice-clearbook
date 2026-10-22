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
