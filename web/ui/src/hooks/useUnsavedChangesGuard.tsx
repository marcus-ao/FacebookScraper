import { useEffect, useState, useSyncExternalStore } from 'react'
import { Alert, Button, Modal, Space } from 'antd'
import { useBlocker, useLocation } from 'react-router'

import {
  UNSAVED_MESSAGES,
  shouldBlockNavigation,
} from '@/lib/unsaved-changes'
import { deploymentStore } from '@/app/deployment-store'
import { getReviewDraftActions } from '@/app/review-draft-actions'

/** 统一保护应用导航、前进后退及刷新关闭时的未保存草稿。 */
export function useUnsavedChangesGuard() {
  const location = useLocation()
  const [saving, setSaving] = useState(false)
  const [saveError, setSaveError] = useState(false)
  const { dirty } = useSyncExternalStore(deploymentStore.subscribe, deploymentStore.getSnapshot, deploymentStore.getSnapshot)
  const blocker = useBlocker(
    ({ currentLocation, nextLocation }) =>
      shouldBlockNavigation({ dirty, currentLocation, nextLocation }),
  )

  useEffect(() => { if (blocker.state !== 'blocked') { setSaving(false); setSaveError(false) } }, [blocker.state])
  const actions = getReviewDraftActions(location.pathname)
  const stay = () => { if (!saving) blocker.reset?.() }
  const saveAndLeave = async () => {
    if (!actions || actions.canSave?.() === false || saving) return
    setSaving(true); setSaveError(false)
    try {
      const result = await actions.save()
      if (result === 'saved') {
        await new Promise<void>(resolve => requestAnimationFrame(() => resolve()))
        if (!deploymentStore.getSnapshot().dirty) blocker.proceed?.()
        else setSaveError(true)
      } else setSaveError(true)
    } catch { setSaveError(true) }
    finally { setSaving(false) }
  }
  const discardAndLeave = () => { if (!actions || saving) return; actions.discard(); blocker.proceed?.() }
  return <Modal open={blocker.state === 'blocked'} title={UNSAVED_MESSAGES.detail}
    rootClassName="deployment-unsaved-dialog" onCancel={stay} closable={!saving} keyboard={!saving}
    footer={actions ? <Space wrap>
      <Button onClick={stay} disabled={saving}>继续编辑</Button>
      <Button type="primary" loading={saving} disabled={actions.canSave?.() === false}
        onClick={() => void saveAndLeave()}>保存并离开</Button>
      <Button danger disabled={saving} onClick={discardAndLeave}>放弃修改并离开</Button>
    </Space> : <Space><Button onClick={stay}>留在本页</Button><Button danger onClick={() => blocker.proceed?.()}>离开</Button></Space>}>
    {actions?.canSave?.() === false && <p>当前修改需留在页面继续处理，或明确放弃后离开。</p>}
    {saveError && <Alert type="warning" showIcon title="尚未保存，修改仍在当前页面，请重试" />}
  </Modal>
}
