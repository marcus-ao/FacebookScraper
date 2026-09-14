import { describe, expect, it } from 'vitest'

import { NAV_ITEMS, navKeyForPath, selectedNavKeys } from './nav-model'

describe('主导航只有四项业务界面', () => {
  it('就是这四项，顺序也是这个', () => {
    expect(NAV_ITEMS.map((item) => item.key)).toEqual([
      'review',
      'history',
      'calendar',
      'settings',
    ])
    expect(NAV_ITEMS.map((item) => item.label)).toEqual([
      '审校队列',
      '历史归档',
      '发布月历',
      '运营设置',
    ])
  })

  it('⛔ 不包含「运行状态」', () => {
    // DECISION_LOG：它降级为顶栏入口，主导航里不同时保留。
    const labels = NAV_ITEMS.map((item) => item.label)
    expect(labels).not.toContain('运行状态')
    expect(NAV_ITEMS.map((item) => item.path)).not.toContain('/runtime')
  })
})

describe('navKeyForPath：选中项由路由算，不另存 state', () => {
  it.each([
    ['/review', 'review'],
    ['/history', 'history'],
    ['/calendar', 'calendar'],
    ['/settings', 'settings'],
  ])('%s → %s', (path, expected) => {
    expect(navKeyForPath(path)).toBe(expected)
  })

  it.each([
    ['/review/fa_neakasaofficial/122100548013379375', 'review'],
    ['/history/in_neakasa.tech/3975547640610092585', 'history'],
  ])('详情页仍然亮着它所属的列表：%s → %s', (path, expected) => {
    // 她是从队列进去的，导航上就该继续亮着「审校队列」。
    expect(navKeyForPath(path)).toBe(expected)
  })

  it('带查询串不影响判断', () => {
    // 传进来的是 pathname，但真实代码万一传了整串也不该乱亮。
    expect(navKeyForPath('/review')).toBe('review')
    expect(selectedNavKeys('/review')).toEqual(['review'])
  })

  it.each([
    ['/runtime'],
    ['/nope'],
    ['/'],
    [''],
  ])('不属于主导航的 %s 一项都不亮', (path) => {
    // 宁可一项都不亮，也不要亮错一项。
    expect(navKeyForPath(path)).toBeNull()
    expect(selectedNavKeys(path)).toEqual([])
  })

  it('前缀相同但不是同一个界面的路径不会误判', () => {
    expect(navKeyForPath('/reviewer')).toBeNull()
    expect(navKeyForPath('/settings-backup')).toBeNull()
  })
})
