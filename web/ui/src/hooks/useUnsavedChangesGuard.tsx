import { useEffect, useSyncExternalStore } from 'react'
import { App } from 'antd'
import { useBlocker, useLocation } from 'react-router'

import {
  UNSAVED_MESSAGES,
  shouldBlockNavigation,
} from '@/lib/unsaved-changes'
import { deploymentStore } from '@/app/deployment-store'

/** 统一保护应用导航、前进后退及刷新关闭时的未保存草稿。 */
export function useUnsavedChangesGuard(): void {
  const { modal } = App.useApp()
  const { dirty } = useSyncExternalStore(deploymentStore.subscribe, deploymentStore.getSnapshot, deploymentStore.getSnapshot)
  const location = useLocation()
  const message = location.pathname === '/settings' ? 'settings' : 'detail'

  const blocker = useBlocker(
    ({ currentLocation, nextLocation }) =>
      shouldBlockNavigation({ dirty, currentLocation, nextLocation }),
  )

  useEffect(() => {
    if (blocker.state !== 'blocked') return
    modal.confirm({
      rootClassName: 'deployment-unsaved-dialog',
      title: UNSAVED_MESSAGES[message],
      okText: '离开',
      cancelText: '留在本页',
      okButtonProps: { danger: true },
      onOk: () => blocker.proceed?.(),
      onCancel: () => blocker.reset?.(),
    })
  }, [blocker, message, modal])

}
