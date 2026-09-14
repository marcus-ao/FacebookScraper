/**
 * 主导航的模型。**纯数据 + 纯函数，没有 React 依赖**，所以能单测。
 *
 * 两条已冻结的决定：
 *
 *   1. **主导航只有四项业务界面**（DECISION_LOG.md D9）。「运行状态」是一天
 *      用不到一次的诊断页，它从主导航降级为顶栏右侧的入口；`/runtime` 路由
 *      继续存在，但**不在这里**。
 *
 *   2. **选中项由当前路由算出来，不另存一份 state**。自己维护 selected 迟早
 *      会和 URL 不同步 —— 尤其是浏览器前进后退、以及外部链接直接落地时。
 */

export const NAV_KEYS = ['review', 'history', 'calendar', 'settings'] as const

export type NavKey = (typeof NAV_KEYS)[number]

export interface NavItem {
  readonly key: NavKey
  readonly label: string
  readonly path: string
}

/** 顺序就是她一天的动线：审校 → 回头查 → 看排期 → 偶尔改设置。 */
export const NAV_ITEMS: readonly NavItem[] = [
  { key: 'review', label: '审校队列', path: '/review' },
  { key: 'history', label: '历史归档', path: '/history' },
  { key: 'calendar', label: '发布月历', path: '/calendar' },
  { key: 'settings', label: '运营设置', path: '/settings' },
]

/**
 * 当前路径属于哪一项主导航。
 *
 * 详情页（`/review/:account/:postId`）算在它所属的列表上 —— 她是从队列进去的，
 * 导航上就该继续亮着「审校队列」。
 *
 * 不属于主导航的路径（`/runtime`、内部核验页、404）返回 `null`：
 * **宁可一项都不亮，也不要亮错一项。**
 */
export function navKeyForPath(pathname: string): NavKey | null {
  const first = pathname.split('/').filter(Boolean)[0]
  if (first === undefined) return null
  const hit = NAV_ITEMS.find((item) => item.path === `/${first}`)
  return hit?.key ?? null
}

/** 给 `Menu selectedKeys` 用：没有命中时是空数组，不是 `['']`。 */
export function selectedNavKeys(pathname: string): NavKey[] {
  const key = navKeyForPath(pathname)
  return key === null ? [] : [key]
}
