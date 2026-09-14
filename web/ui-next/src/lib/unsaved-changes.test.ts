import { describe, expect, it, vi } from 'vitest'

import {
  UNSAVED_MESSAGES,
  applyBeforeUnload,
  shouldBlockNavigation,
} from './unsaved-changes'

const at = (pathname: string) => ({ pathname })

describe('确认文案与旧 UI 一字不差', () => {
  it('详情', () => {
    // 出处：web/ui/src/App.vue 的 window.confirm。
    expect(UNSAVED_MESSAGES.detail).toBe('修改尚未保存，确定离开并放弃当前草稿？')
  })

  it('设置', () => {
    expect(UNSAVED_MESSAGES.settings).toBe('设置尚未保存，离开会放弃这次修改。继续离开？')
  })

  it('只有这两句，没有自己发明的第三句', () => {
    expect(Object.keys(UNSAVED_MESSAGES).sort()).toEqual(['detail', 'settings'])
  })
})

describe('shouldBlockNavigation', () => {
  it('不脏就不拦', () => {
    // 总是弹确认框的守卫三天之内就会被训练成"闭着眼点继续"。
    expect(
      shouldBlockNavigation({
        dirty: false,
        currentLocation: at('/review/fa_x/1'),
        nextLocation: at('/review'),
      }),
    ).toBe(false)
  })

  it('脏 + 换界面 → 拦', () => {
    expect(
      shouldBlockNavigation({
        dirty: true,
        currentLocation: at('/review/fa_x/1'),
        nextLocation: at('/review'),
      }),
    ).toBe(true)
  })

  it('脏 + 同一个 pathname → 不拦（切标签页、改筛选都只动 search）', () => {
    expect(
      shouldBlockNavigation({
        dirty: true,
        currentLocation: at('/review/fa_x/1'),
        nextLocation: at('/review/fa_x/1'),
      }),
    ).toBe(false)
  })

  it('脏 + 换到另一篇详情 → 拦', () => {
    // 「下一篇」也会丢草稿，它不是例外。
    expect(
      shouldBlockNavigation({
        dirty: true,
        currentLocation: at('/review/fa_x/1'),
        nextLocation: at('/review/fa_x/2'),
      }),
    ).toBe(true)
  })

  it('脏 + 从队列详情跳到历史详情 → 拦', () => {
    expect(
      shouldBlockNavigation({
        dirty: true,
        currentLocation: at('/review/fa_x/1'),
        nextLocation: at('/history/fa_x/1'),
      }),
    ).toBe(true)
  })

  it('设置页同一套判断', () => {
    expect(
      shouldBlockNavigation({
        dirty: true,
        currentLocation: at('/settings'),
        nextLocation: at('/review'),
      }),
    ).toBe(true)
    expect(
      shouldBlockNavigation({
        dirty: false,
        currentLocation: at('/settings'),
        nextLocation: at('/review'),
      }),
    ).toBe(false)
  })
})

describe('applyBeforeUnload：关标签页那一层', () => {
  function fakeEvent() {
    return {
      preventDefault: vi.fn(),
      returnValue: undefined as unknown,
    } as unknown as BeforeUnloadEvent & { preventDefault: ReturnType<typeof vi.fn> }
  }

  it('不脏就不拦，也不碰 event', () => {
    const event = fakeEvent()
    expect(applyBeforeUnload(event, false)).toBe(false)
    expect(event.preventDefault).not.toHaveBeenCalled()
  })

  it('脏就 preventDefault 并给 returnValue', () => {
    const event = fakeEvent()
    expect(applyBeforeUnload(event, true)).toBe(true)
    expect(event.preventDefault).toHaveBeenCalledOnce()
    expect(event.returnValue).toBe('')
  })
})
