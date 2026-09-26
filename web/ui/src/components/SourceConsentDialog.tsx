import { useEffect, useState } from 'react'
import { Checkbox, Modal } from 'antd'
import type { TaskDetail } from '@/types/domain'

export function sourceConsentRequired(detail: TaskDetail, thirdPartyCapability = false): boolean {
  return detail.meta.author_kind === 'third_party' || thirdPartyCapability
}

export function SourceConsentDialog({ detail, open, busy, onClose, onAccept }: {
  detail: TaskDetail; open: boolean; busy: boolean; onClose: () => void; onAccept: () => void;
}) {
  const [accepted, setAccepted] = useState(false)
  useEffect(() => { if (!open) setAccepted(false) }, [open])
  return <Modal title="确认第三方内容来源" open={open} onCancel={onClose} onOk={onAccept}
    okText="确认并开始" okButtonProps={{ disabled: !accepted, loading: busy }} cancelText="返回">
    <p>这篇来自第三方作者。请先查看原帖，确认可以用于德国站内容运营。</p>
    <p>作者：{detail.meta.owner || '待核对'}{detail.meta.permalink && <>
      {' · '}<a href={detail.meta.permalink} target="_blank" rel="noopener noreferrer">查看原帖 ↗</a>
    </>}</p>
    <Checkbox checked={accepted} onChange={event => setAccepted(event.target.checked)}>我已核对来源并确认可以处理这篇内容</Checkbox>
  </Modal>
}
