/** 页面标题与面包屑由路由 handle 提供。 */

import type { ListSource } from './search-params'

export interface PageMeta {
  readonly title: string
  /** 来源列表类型；返回链接须携带原筛选与页码。 */
  readonly backTo?: ListSource
}

interface MatchLike {
  readonly handle?: unknown
}

function isPageMeta(value: unknown): value is PageMeta {
  return (
    typeof value === 'object' &&
    value !== null &&
    'title' in value &&
    typeof (value as { title: unknown }).title === 'string'
  )
}

/** 取最深层的 handle，使详情标题优先于布局标题。 */
export function resolvePageMeta(matches: readonly MatchLike[]): PageMeta | null {
  for (let index = matches.length - 1; index >= 0; index -= 1) {
    const handle = matches[index]?.handle
    if (isPageMeta(handle)) return handle
  }
  return null
}

export const LIST_SOURCE_LABEL: Readonly<Record<ListSource, string>> = {
  review: '审校队列',
  history: '历史归档',
}
