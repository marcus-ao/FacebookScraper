import { useState } from 'react'
import { Button, Drawer, Empty, Space, Timeline } from 'antd'
import type { TaskDetail } from '@/types/domain'
import { ShanghaiTime } from '@/components/Time'
import { ACTION_LABEL as TRAIL_ACTION_LABEL } from '@/lib/format'
import styles from '@/features/content-jobs/ContentJobs.module.css'

export function DetailDrawers({ detail }: { detail: TaskDetail }) {
  const [open, setOpen] = useState<'trail' | 'technical' | null>(null)
  return <Space><Button type="text" size="small" onClick={() => setOpen('trail')}>处理记录</Button><Button type="text" size="small" onClick={() => setOpen('technical')}>技术诊断</Button>
    <Drawer title={open === 'trail' ? '处理记录 · 上海时间' : '技术诊断'} open={!!open} onClose={() => setOpen(null)} size="large">
      {open === 'trail' ? detail.trail.length ? <Timeline items={detail.trail.map(record => ({ content: <><ShanghaiTime at={record.at} /><p>{TRAIL_ACTION_LABEL[record.action] ?? '处理记录'} {record.note}</p>{record.wake_at && <p>恢复审校：<ShanghaiTime at={record.wake_at} /></p>}</> }))} /> : <Empty description="尚无人工处理记录" /> : <pre className={styles.diagnostic}>{JSON.stringify({ id: detail.id, meta: detail.meta, text: { ...detail.text, en: undefined, de_human: undefined, de_machine: undefined }, review: detail.review, localization: { ...detail.localization, body_de: undefined, source_body: undefined }, publication: detail.publication, delivery: detail.delivery, risk_scan: detail.risk_scan, images: detail.images }, null, 2)}</pre>}
    </Drawer>
  </Space>
}
