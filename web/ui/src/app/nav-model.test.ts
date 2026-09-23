import { describe, expect, it } from 'vitest'

import { NAV_ITEMS, navKeyForPath, selectedNavKeys } from './nav-model'

describe('主导航只有四项业务界面', () => {
  it('就是这四项，顺序也是这个', () => {
    expect(NAV_ITEMS.map((item) => item.key)).toEqual([
      'review-facebook',
      'review-instagram',
      'history',
      'calendar',
    ])
    expect(NAV_ITEMS.map((item) => item.label)).toEqual([
      'Facebook 待审',
      'Instagram 待审',
      '历史归档',
      '发布月历',
    ])
  })

  it('两个平台是并列入口，不是同一个列表的筛选项', () => {
    expect(NAV_ITEMS.map((item) => item.path)).toContain('/review/facebook')
    expect(NAV_ITEMS.map((item) => item.path)).toContain('/review/instagram')
    expect(NAV_ITEMS.map((item) => item.path)).not.toContain('/review')
  })

  it('⛔ 不包含「运行状态」', () => {
    const labels = NAV_ITEMS.map((item) => item.label)
    expect(labels).not.toContain('运行状态')
    expect(NAV_ITEMS.map((item) => item.path)).not.toContain('/runtime')
  })
})

describe('navKeyForPath：选中项由路由算，不另存 state', () => {
  it.each([
    ['/review/facebook', 'review-facebook'],
    ['/review/instagram', 'review-instagram'],
    ['/history', 'history'],
    ['/calendar', 'calendar'],
  ])('%s → %s', (path, expected) => {
    expect(navKeyForPath(path)).toBe(expected)
  })

  it('历史详情仍然亮着历史归档', () => {
    expect(navKeyForPath('/history/in_neakasa.tech/3975547640610092585')).toBe('history')
  })

  it('审校详情按链接带的平台亮对应入口', () => {
    const path = '/review/fa_neakasaofficial/122100548013379375'
    expect(navKeyForPath(path, '?platform=facebook')).toBe('review-facebook')
    expect(navKeyForPath(path, '?queue=review&platform=instagram')).toBe('review-instagram')
  })

  it('⛔ 不从账号目录前缀猜平台 —— 那是归档层的约定，不归导航解析', () => {
    expect(navKeyForPath('/review/fa_neakasaofficial/122100548013379375')).toBeNull()
    expect(navKeyForPath('/review/fa_x/1', '?platform=nonsense')).toBeNull()
  })

  it('selectedNavKeys 同样认查询串里的平台', () => {
    expect(selectedNavKeys('/review/facebook')).toEqual(['review-facebook'])
    expect(selectedNavKeys('/review/fa_x/1', '?platform=instagram')).toEqual(['review-instagram'])
  })

  it.each([
    ['/runtime'],
    ['/nope'],
    ['/'],
    [''],
  ])('不属于主导航的 %s 一项都不亮', (path) => {
    expect(navKeyForPath(path)).toBeNull()
    expect(selectedNavKeys(path)).toEqual([])
  })

  it('前缀相同但不是同一个界面的路径不会误判', () => {
    expect(navKeyForPath('/reviewer')).toBeNull()
    expect(navKeyForPath('/settings-backup')).toBeNull()
  })
})
