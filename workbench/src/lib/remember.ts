/** Per-computer conveniences (the last restaurant picked). Storage may be blocked or
 * cleared: every call is safe, and the Workbench works the same without it. */
const LAST_RESTAURANT = 'clearbook.lastRestaurant'

export function lastRestaurant(): string | undefined {
  try {
    return localStorage.getItem(LAST_RESTAURANT) ?? undefined
  } catch {
    return undefined
  }
}

export function rememberRestaurant(clientId: string): void {
  try {
    localStorage.setItem(LAST_RESTAURANT, clientId)
  } catch {
    // not remembered this time; nothing else depends on it
  }
}
