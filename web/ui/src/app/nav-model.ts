
export const NAV_KEYS = ['review', 'history', 'calendar', 'settings'] as const

export type NavKey = (typeof NAV_KEYS)[number]

export interface NavItem {
  readonly key: NavKey
  readonly label: string
  readonly path: string
}

export const NAV_ITEMS: readonly NavItem[] = [
  { key: 'review', label: '审校队列', path: '/review' },
  { key: 'history', label: '历史归档', path: '/history' },
  { key: 'calendar', label: '发布月历', path: '/calendar' },
  { key: 'settings', label: '运营设置', path: '/settings' },
]

/** 详情沿用来源列表的选中项；非主导航路径返回 null。 */
export function navKeyForPath(pathname: string): NavKey | null {
  const first = pathname.split('/').filter(Boolean)[0]
  if (first === undefined) return null
  const hit = NAV_ITEMS.find((item) => item.path === `/${first}`)
  return hit?.key ?? null
}

export function selectedNavKeys(pathname: string): NavKey[] {
  const key = navKeyForPath(pathname)
  return key === null ? [] : [key]
}
