export type ReviewDraftSaveResult = 'saved' | 'still_dirty' | 'failed'

export interface ReviewDraftActions {
  save: () => Promise<ReviewDraftSaveResult>
  discard: () => void
  canSave?: () => boolean
}

const actions = new Map<string, ReviewDraftActions>()

export function registerReviewDraftActions(taskId: string, value: ReviewDraftActions): () => void {
  actions.set(taskId, value)
  return () => { if (actions.get(taskId) === value) actions.delete(taskId) }
}

export function getReviewDraftActions(pathname: string): ReviewDraftActions | null {
  const match = /^\/(?:review|history)\/([^/]+)\/([^/]+)$/.exec(pathname)
  if (!match) return null
  try { return actions.get(`${decodeURIComponent(match[1]!)}/${decodeURIComponent(match[2]!)}`) ?? null }
  catch { return null }
}
