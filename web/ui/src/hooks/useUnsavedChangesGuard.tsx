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
  /** 用于确认离开的文案。 */
  readonly message: UnsavedMessageKey
}

/**
 * 未保存草稿的离开守卫。**详情编辑与设置编辑共用这一个。**
 *
 * 覆盖全部三层离开路径：
 *
 * | 层 | 守卫方式 |
 * |---|---|---|
 * | 应用内导航 | `useBlocker` |
 * | 浏览器前进后退 | `useBlocker`（覆盖 popstate） |
 * | 关闭标签页或刷新 | `beforeunload` effect |
 *
 * 确认框使用 antd `Modal.confirm`。原生 confirm 不受
 * `ConfigProvider` 管，按钮是英文的 OK / Cancel，而这是一个中文界面。
 *
 * 详情和设置将各自的真实脏状态传入；核心判断由
 * `src/lib/unsaved-changes.ts` 的纯函数和单测守护。
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
