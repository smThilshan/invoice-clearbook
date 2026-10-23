/** Where a review was opened from (task 4.15): the list to go back to, and that list's
 * documents in order for previous / next. Carried in the navigation state, so a direct link
 * (new tab, bookmark) simply has none. */
export interface ReviewContext {
  from: string // the list's address, e.g. "/queue?client=roast&queue=EXCEPTION"
  ids: string[]
  flash?: string // a message for the next page, e.g. "Approved: inv-1.pdf"
}

export function reviewContext(state: unknown): ReviewContext | null {
  if (typeof state !== 'object' || state === null) return null
  const { from, ids, flash } = state as Record<string, unknown>
  if (typeof from !== 'string' || !Array.isArray(ids)) return null
  return {
    from,
    ids: ids.filter((id): id is string => typeof id === 'string'),
    flash: typeof flash === 'string' ? flash : undefined,
  }
}

/** Previous and next document in the list, or null at either end (or without a list). */
export function neighbours(context: ReviewContext | null, id: string) {
  const index = context ? context.ids.indexOf(id) : -1
  if (!context || index < 0) return { previous: null, next: null, position: null, total: null }
  return {
    previous: context.ids[index - 1] ?? null,
    next: context.ids[index + 1] ?? null,
    position: index + 1,
    total: context.ids.length,
  }
}
