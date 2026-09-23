import { useRef, useState } from 'react'
import { Alert, App, Button, Dropdown, Form, Input, Modal } from 'antd'
import type { MenuProps } from 'antd'
import { MoreOutlined } from '@ant-design/icons'
import { ConflictRecovery } from '@/components/ConflictRecovery'
import { useReviewDecision } from '@/hooks/useTasks'
import { isConflict } from '@/services/http'
import type { DecisionAction, DecisionForm, ReviewContext } from '@/services/review'
import type { TaskDetail } from '@/types/domain'
import { useDeploymentDraft } from '@/hooks/useDeploymentDraft'
import { deploymentStore } from '@/app/deployment-store'

const titles: Record<DecisionAction, string> = {
  snoozed: '稍后再审', woke: '恢复审校', skipped: '这篇不发',
  handed_off: '由我自行处理', handoff_link: '补充手工发布链接', export: '下载并交由我处理',
}
const activeStatuses = ['not_ready', 'pending_review', 'edited', 'snoozed']

export function ReviewActions({ detail, compact = false, onChanged }: {
  detail: ReviewContext; compact?: boolean; onChanged?: (detail: TaskDetail) => void
}) {
  const [form, setForm] = useState<DecisionForm | null>(null)
  useDeploymentDraft(form !== null)
  const [fresh, setFresh] = useState<TaskDetail | null>(null)
  const [refreshError, setRefreshError] = useState('')
  const trigger = useRef<HTMLButtonElement>(null)
  const mutation = useReviewDecision()
  const { message } = App.useApp()
  const context = fresh ?? detail
  const active = activeStatuses.includes(detail.status)
  const actions: DecisionAction[] = active
    ? [detail.status === 'snoozed' ? 'woke' : 'snoozed', ...(!compact ? ['export', 'handed_off'] as const : []), 'skipped']
    : detail.status === 'handed_off' && !compact ? ['export', 'handoff_link'] : []
  if (('read_only' in detail && detail.read_only) || actions.length === 0) return null
  const labels = { ...titles, export: detail.status === 'handed_off' ? '重新下载资源' : '下载并由我处理',
    handed_off: '我已自行处理', handoff_link: '补充发布链接' }
  const items: MenuProps['items'] = actions.map(action => ({ key: action, label: labels[action], danger: action === 'skipped' }))
  function open(action: DecisionAction) {
    if (!deploymentStore.canStartEditing()) return
    mutation.reset(); setFresh(null); setRefreshError('')
    setForm({ action, reason: '', wakeAt: '', handoffUrl: detail.review.handoff_url || '' })
  }
  function close() { if (!mutation.isPending) setForm(null) }
  async function confirm() {
    if (!form) return
    try {
      const changed = await mutation.mutateAsync({ context, form })
      onChanged?.(changed); setForm(null)
      void message.success(form.action === 'export' ? '资源已下载，交由你继续处理' : '处理结果已保存')
    } catch { /* 错误与恢复入口留在当前对话框，填写内容不丢。 */ }
  }
  return <span data-row-control>
    <Dropdown menu={{ items, onClick: ({ key }) => open(key as DecisionAction) }} trigger={['click']}>
      <Button ref={trigger} type="text" size="small" aria-label="更多处理动作" icon={<MoreOutlined />} />
    </Dropdown>
    <Modal open={form !== null} title={form ? titles[form.action] : ''} onCancel={close} onOk={() => void confirm()}
      afterClose={() => trigger.current?.focus()} confirmLoading={mutation.isPending}
      okText={form?.action === 'export' ? '下载并交给我' : '确认'} cancelText="取消"
      okButtonProps={{ danger: form?.action === 'skipped', disabled: form?.action === 'skipped' && !form.reason.trim() }}
      cancelButtonProps={{ disabled: mutation.isPending }} keyboard={!mutation.isPending} closable={!mutation.isPending}>
      {form && <Form layout="vertical" disabled={mutation.isPending}>
        {form.action === 'snoozed' && <>
          <p>默认在 {context.review.snooze_default_days || 3} 个工作日后回到待审列表。工作日按周一至周五计算。</p>
          <Form.Item label="指定回来时间"><Input type="datetime-local" aria-label="指定回来时间"
            value={form.wakeAt} onChange={event => setForm({ ...form, wakeAt: event.target.value })} /></Form.Item>
        </>}
        {form.action === 'skipped' && <p>这篇会移到“已处理”，系统将停止后续处理。请留下不发的理由。</p>}
        {['handed_off', 'export'].includes(form.action) && <p>这篇会标记为“已交人工处理”，由你继续调整或发布，系统不再自动推进。</p>}
        {form.action === 'export' && <p>下载包含当前德语文案、图片与元信息。缺少德语图时会使用原图，并在资源包中注明。</p>}
        {form.action === 'woke' && <p>恢复后，这篇会重新出现在待审列表。</p>}
        {['snoozed', 'skipped'].includes(form.action) && <Form.Item label="理由" required={form.action === 'skipped'}>
          <Input.TextArea autoFocus aria-label="理由" rows={3} maxLength={2000} value={form.reason}
            onChange={event => setForm({ ...form, reason: event.target.value })} />
        </Form.Item>}
        {['handed_off', 'handoff_link', 'export'].includes(form.action) && <Form.Item label="手工发布链接（选填）">
          <Input autoFocus aria-label="手工发布链接" type="url" placeholder="https://…" value={form.handoffUrl}
            onChange={event => setForm({ ...form, handoffUrl: event.target.value })} />
        </Form.Item>}
        {isConflict(mutation.error) ? <ConflictRecovery kind="schedule" onRecover={() => {
          void mutation.refresh(detail.id).then(value => { setFresh(value); onChanged?.(value) })
            .catch(() => setRefreshError('暂时无法载入最新状态，填写内容已保留，请重试'))
        }} /> : mutation.error ? <Alert type="error" showIcon title="本次处理未完成，填写内容已保留，请重试或核对最新状态" /> : null}
        {refreshError && <Alert type="error" title={refreshError} />}
      </Form>}
    </Modal>
  </span>
}
