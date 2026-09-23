import type { Platform } from '@/types/domain'

export const NAV_KEYS = ['review-facebook', 'review-instagram', 'history', 'calendar'] as const

export type NavKey = (typeof NAV_KEYS)[number]

export interface NavItem {
  readonly key: NavKey
  readonly label: string
  readonly path: string
}

/**
 * Facebook 与 Instagram 是两个独立入口，不是同一个列表的筛选项。
 * 两边的链接、标签和长度上限规则不同，混在一起看每翻一篇都要先想"这篇走哪套规则"。
 */
export const REVIEW_PLATFORMS: readonly Platform[] = ['facebook', 'instagram']
export const reviewPath = (platform: Platform) => `/review/${platform}`

export const NAV_ITEMS: readonly NavItem[] = [
  { key: 'review-facebook', label: 'Facebook 待审', path: reviewPath('facebook') },
  { key: 'review-instagram', label: 'Instagram 待审', path: reviewPath('instagram') },
  { key: 'history', label: '历史归档', path: '/history' },
  { key: 'calendar', label: '发布月历', path: '/calendar' },
]

const isPlatform = (value: string | null): value is Platform =>
  value !== null && (REVIEW_PLATFORMS as readonly string[]).includes(value)

/**
 * 详情沿用来源列表的选中项；非主导航路径返回 null。
 *
 * 审校详情（`/review/<account>/<postId>`）从路径上看不出平台——账号目录前缀是归档层的
 * 约定，不该让导航去解析。改由列表链接带上的 `?platform=` 定位；没带就一项都不亮。
 */
export function navKeyForPath(pathname: string, search = ''): NavKey | null {
  const parts = pathname.split('/').filter(Boolean)
  if (parts[0] === undefined) return null
  let path = `/${parts[0]}`
  if (parts[0] === 'review') {
    const fromPath = parts[1] ?? ''
    const platform = isPlatform(fromPath) ? fromPath : new URLSearchParams(search).get('platform')
    if (!isPlatform(platform)) return null
    path = reviewPath(platform)
  }
  return NAV_ITEMS.find(item => item.path === path)?.key ?? null
}

export function selectedNavKeys(pathname: string, search = ''): NavKey[] {
  const key = navKeyForPath(pathname, search)
  return key === null ? [] : [key]
}
