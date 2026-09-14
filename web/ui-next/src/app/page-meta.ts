/**
 * 页面标识：外壳需要知道"当前这个界面叫什么"，才能画顶栏标题与面包屑。
 *
 * 走 React Router 的 `handle`，不另建一份 context 或全局 state：
 * 路由表已经是"哪个 URL 对应哪个界面"的真相源，标题挂在同一处，
 * 就不会出现"URL 到了详情、标题还停在列表"这种不同步。
 *
 * 纯函数，能单测。
 */

import type { ListSource } from './search-params'

export interface PageMeta {
  /** 顶栏中部显示的标题，也是这个界面的 `h1`。 */
  readonly title: string
  /**
   * 设了就画面包屑：`来源列表 / 当前标题`。
   *
   * 值是"来源列表是队列还是历史"——外壳要用它把原来的筛选与页码
   * 还回列表链接上（DECISION_LOG.md §2.2 第 3 条）。
   */
  readonly backTo?: ListSource
  /** 内部核验页。设了它，外壳会挂一条"这不是业务界面"的说明。 */
  readonly internal?: true
}

/** React Router 的 `useMatches()` 返回项里，我们只关心这两个字段。 */
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

/**
 * 从匹配链里取最深的一个 `handle`。
 *
 * 取最深的那个而不是第一个：详情路由嵌在布局路由里，要的是详情的标题。
 * 一个 handle 都没有时返回 null，外壳画一个不带标题的顶栏 —— 不编造标题。
 */
export function resolvePageMeta(matches: readonly MatchLike[]): PageMeta | null {
  for (let index = matches.length - 1; index >= 0; index -= 1) {
    const handle = matches[index]?.handle
    if (isPageMeta(handle)) return handle
  }
  return null
}

/** 面包屑第一段的文案。 */
export const LIST_SOURCE_LABEL: Readonly<Record<ListSource, string>> = {
  review: '审校队列',
  history: '历史归档',
}
