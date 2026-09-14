import { useEffect } from 'react'
import { App } from 'antd'
import { useBlocker } from 'react-router'

import {
  UNSAVED_MESSAGES,
  applyBeforeUnload,
  shouldBlockNavigation,
} from '@/lib/unsaved-changes'
import type { UnsavedMessageKey } from '@/lib/unsaved-changes'

export interface UnsavedChangesGuardOptions {
  /** 当前是否有未保存的改动。`false` 时守卫完全不介入。 */
  readonly dirty: boolean
  /** 用哪一句确认文案。两句都与旧 UI 一字不差。 */
  readonly message: UnsavedMessageKey
}

/**
 * 未保存草稿的离开守卫。**详情编辑与设置编辑共用这一个。**
 *
 * 覆盖旧 UI 的全部三层（BASELINE_BEHAVIOR.md §2.3 / 回归 B4）：
 *
 * | 层 | 旧 UI | 这里 |
 * |---|---|---|
 * | 应用内导航 | `App.mayLeave()` 串联子组件 | `useBlocker` |
 * | 浏览器前进后退 | 拒绝后 `writeLocation()` 推回 URL | `useBlocker`（它覆盖 popstate） |
 * | 关闭标签页 | 两个组件各自的 `beforeunload` | 下面那个 effect |
 *
 * 确认框从原生 `window.confirm` 换成 antd `Modal.confirm`，**文案一字不改**
 * （REACT_MIGRATION_PLAN.md §4.4）。换的理由不是好看：原生 confirm 不受
 * `ConfigProvider` 管，按钮是英文的 OK / Cancel，而这是一个中文界面。
 *
 * ⚠️ **Stage B1 只建立这个抽象，不往占位界面里塞一个假的 dirty 开关。**
 * 详情与设置都还是占位页，真正接上真实脏状态是 Stage D 与 Stage G 的事；
 * 浏览器级的 B4 回归也在那时才做得成（本会话 §18）。
 * 这里的核心判断由 `src/lib/unsaved-changes.ts` 的纯函数 + 单测守着。
 *
 * 用法：
 *
 * ```tsx
 * useUnsavedChangesGuard({ dirty: draft !== saved, message: 'detail' })
 * ```
 */
export function useUnsavedChangesGuard({ dirty, message }: UnsavedChangesGuardOptions): void {
  const { modal } = App.useApp()

  const blocker = useBlocker(
    ({ currentLocation, nextLocation }) =>
      shouldBlockNavigation({ dirty, currentLocation, nextLocation }),
  )

  // 应用内导航 + 浏览器前进后退：拦下来之后问一次。
  useEffect(() => {
    if (blocker.state !== 'blocked') return
    modal.confirm({
      title: UNSAVED_MESSAGES[message],
      okText: '离开',
      cancelText: '留在本页',
      okButtonProps: { danger: true },
      onOk: () => blocker.proceed?.(),
      onCancel: () => blocker.reset?.(),
    })
  }, [blocker, message, modal])

  // 关闭标签页 / 刷新。文案由浏览器出，我们只决定拦不拦。
  useEffect(() => {
    if (!dirty) return
    const handler = (event: BeforeUnloadEvent) => applyBeforeUnload(event, true)
    window.addEventListener('beforeunload', handler)
    return () => window.removeEventListener('beforeunload', handler)
  }, [dirty])
}
