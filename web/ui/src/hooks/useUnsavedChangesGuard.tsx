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
  readonly dirty: boolean
  readonly message: UnsavedMessageKey
}

/** 统一保护应用导航、前进后退及刷新关闭时的未保存草稿。 */
export function useUnsavedChangesGuard({ dirty, message }: UnsavedChangesGuardOptions): void {
  const { modal } = App.useApp()

  const blocker = useBlocker(
    ({ currentLocation, nextLocation }) =>
      shouldBlockNavigation({ dirty, currentLocation, nextLocation }),
  )

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

  useEffect(() => {
    if (!dirty) return
    const handler = (event: BeforeUnloadEvent) => applyBeforeUnload(event, true)
    window.addEventListener('beforeunload', handler)
    return () => window.removeEventListener('beforeunload', handler)
  }, [dirty])
}
