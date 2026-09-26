import { useState } from 'react'
import { Button, Drawer, Empty, Space, Timeline } from 'antd'
import type { TaskDetail } from '@/types/domain'
import { ShanghaiTime } from '@/components/Time'
import { ACTION_LABEL as TRAIL_ACTION_LABEL } from '@/lib/format'

export function DetailDrawers({ detail }: { detail: TaskDetail }) {
  const [open, setOpen] = useState(false)
  return <Space><Button type="text" size="small" onClick={() => setOpen(true)}>处理记录</Button>
    <Drawer title="处理记录" open={open} onClose={() => setOpen(false)} size="large">
      {detail.trail.length ? <Timeline items={detail.trail.map(record => ({ content: <><ShanghaiTime at={record.at} /><p>{TRAIL_ACTION_LABEL[record.action] ?? '处理记录'} {record.note}</p>{record.wake_at && <p>恢复审校：<ShanghaiTime at={record.wake_at} /></p>}</> }))} /> : <Empty description="尚无人工处理记录" />}
    </Drawer>
  </Space>
}
