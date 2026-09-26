import { Drawer, Empty, Timeline } from 'antd'
import type { OperationRecord, TaskDetail } from '@/types/domain'
import { ShanghaiTime } from '@/components/Time'
import { ACTION_LABEL as TRAIL_ACTION_LABEL } from '@/lib/format'

const businessActions = new Set<OperationRecord['action']>([
  'snoozed', 'woke', 'skipped', 'handed_off', 'handoff_link', 'approved', 'scheduled', 'submit_failed', 'unscheduled',
])
const reasonActions = new Set<OperationRecord['action']>(['snoozed', 'skipped', 'handed_off'])

export function businessTrail(trail: readonly OperationRecord[]): OperationRecord[] {
  return trail.filter(record => businessActions.has(record.action))
    .map(record => ({ ...record, note: reasonActions.has(record.action) ? record.note ?? null : null }))
}

export function DetailDrawers({ detail, open, onClose, onAfterClose }: {
  detail: TaskDetail; open: boolean; onClose: () => void; onAfterClose?: () => void;
}) {
  const records = businessTrail(detail.trail)
  return <Drawer title="处理记录" open={open} onClose={onClose} afterOpenChange={visible => { if (!visible) onAfterClose?.() }} width={480}>
    {records.length ? <Timeline items={records.map(record => ({ content: <>
      <strong>{TRAIL_ACTION_LABEL[record.action]}</strong> · <ShanghaiTime at={record.at} />
      {record.note && <p>理由：{record.note}</p>}
      {record.action === 'snoozed' && record.wake_at && <p>恢复审校：<ShanghaiTime at={record.wake_at} /></p>}
    </> }))} /> : <Empty description="尚无需要回看的处理决定" />}
  </Drawer>
}
