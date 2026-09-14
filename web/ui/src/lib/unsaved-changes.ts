
export const UNSAVED_MESSAGES = {
  detail: '修改尚未保存，确定离开并放弃当前草稿？',
  settings: '设置尚未保存，离开会放弃这次修改。继续离开？',
} as const

export type UnsavedMessageKey = keyof typeof UNSAVED_MESSAGES

interface LocationLike {
  readonly pathname: string
}

export interface BlockDecisionInput {
  readonly dirty: boolean
  readonly currentLocation: LocationLike
  readonly nextLocation: LocationLike
}

/** 仅草稿已变且 pathname 改变时拦截；search 变化不算离开。 */
export function shouldBlockNavigation(input: BlockDecisionInput): boolean {
  if (!input.dirty) return false
  return input.currentLocation.pathname !== input.nextLocation.pathname
}

/** 浏览器自行提供离开文案，此处只决定是否拦截。 */
export function applyBeforeUnload(event: BeforeUnloadEvent, dirty: boolean): boolean {
  if (!dirty) return false
  event.preventDefault()
  event.returnValue = ''
  return true
}
