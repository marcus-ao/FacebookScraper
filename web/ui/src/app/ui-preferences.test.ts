import { describe, expect, it } from 'vitest'

import {
  UI_PREFERENCE_KEYS,
  browserStore,
  readSiderCollapsed,
  writeSiderCollapsed,
} from './ui-preferences'
import type { PreferenceStore } from './ui-preferences'

function fakeStore(initial: Record<string, string> = {}) {
  const data = new Map(Object.entries(initial))
  const store: PreferenceStore & { data: Map<string, string> } = {
    data,
    getItem: (key) => data.get(key) ?? null,
    setItem: (key, value) => {
      data.set(key, value)
    },
  }
  return store
}

describe('侧边栏折叠偏好', () => {
  it('默认展开', () => {
    expect(readSiderCollapsed(fakeStore())).toBe(false)
  })

  it('存进去、读出来', () => {
    const store = fakeStore()
    writeSiderCollapsed(store, true)
    expect(readSiderCollapsed(store)).toBe(true)
    writeSiderCollapsed(store, false)
    expect(readSiderCollapsed(store)).toBe(false)
  })

  it('没有 storage 时不抛，回落默认展开', () => {
    expect(readSiderCollapsed(null)).toBe(false)
    expect(() => writeSiderCollapsed(null, true)).not.toThrow()
  })

  it('storage 一读一写就抛也不会把界面带崩', () => {
    const hostile: PreferenceStore = {
      getItem: () => {
        throw new Error('SecurityError')
      },
      setItem: () => {
        throw new Error('QuotaExceededError')
      },
    }
    expect(readSiderCollapsed(hostile)).toBe(false)
    expect(() => writeSiderCollapsed(hostile, true)).not.toThrow()
  })

  it('手改过的垃圾值当成"没设过"', () => {
    for (const junk of ['true', 'yes', '', '2', '{}']) {
      expect(readSiderCollapsed(fakeStore({ [UI_PREFERENCE_KEYS.siderCollapsed]: junk }))).toBe(
        false,
      )
    }
  })
})

describe('localStorage 只存 UI 偏好', () => {
  it('允许的键只有一个，而且带 ui 命名空间', () => {
    expect(Object.values(UI_PREFERENCE_KEYS)).toEqual(['rc.ui.sider-collapsed'])
    for (const key of Object.values(UI_PREFERENCE_KEYS)) {
      expect(key.startsWith('rc.ui.')).toBe(true)
    }
  })

  it('⛔ 没有筛选 / 当前帖子 / 草稿 / 业务状态的键', () => {
    const keys = Object.values(UI_PREFERENCE_KEYS).join(' ')
    for (const forbidden of [
      'queue',
      'filter',
      'platform',
      'month',
      'tag',
      'task',
      'draft',
      'status',
      'revision',
      'token',
    ]) {
      expect(keys).not.toContain(forbidden)
    }
  })

  it('写入只会落在允许的键上', () => {
    const store = fakeStore()
    writeSiderCollapsed(store, true)
    expect([...store.data.keys()]).toEqual(['rc.ui.sider-collapsed'])
  })

  it('node 环境里没有 localStorage，browserStore() 返回 null', () => {
    expect(browserStore()).toBeNull()
  })
})
