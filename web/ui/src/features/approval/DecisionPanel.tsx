import { Alert, Button, Collapse, Input, Modal, Space, Typography } from 'antd'
import type { TaskDetail } from '@/types/domain'
import type { ApprovalController } from '@/hooks/useApproval'
import { useCalendar } from '@/hooks/useCalendar'
import { BerlinTime, ShanghaiTime } from '@/components/Time'
import { ConflictRecovery } from '@/components/ConflictRecovery'
import { DisabledReason } from '@/components/DisabledReason'
import { nearbyOccupancy } from './occupancy'
import { berlinInput, AUTHOR_KIND_LABEL, formatDate } from '@/lib/format'
import { isConflict } from '@/services/http'
import { isCompleteScheduleTime } from '@/lib/action-reasons'
import styles from './DecisionPanel.module.css'

export function ApprovalAction({ controller: c }: { controller: ApprovalController }) {
  // 这是整个界面唯一的主动作。它灰着的时候，「为什么」必须键盘也拿得到。
  return <DisabledReason label="通过并创建排期" reason={c.reason}>
    <Button aria-label="通过并创建排期" type="primary" disabled={!!c.reason} loading={c.busy} onClick={c.open}>通过并创建排期</Button>
  </DisabledReason>
}

export function DecisionPanel({ detail, controller: c, editing }: { detail: TaskDetail; controller: ApprovalController; editing: boolean }) {
  const calendar = useCalendar()
  const data = c.options.data
  const min = berlinInput(data?.earliest), max = berlinInput(data?.latest)
  const { cards: occupied, tooClose: near } = nearbyOccupancy(calendar.data?.cards, detail.platform, c.when, calendar.data?.gap_minutes ?? 0)
  return <section className={styles.panel} aria-label="审核与排期">
    <div className={styles.heading}><h2>审核与排期</h2><Button type="text" size="small" disabled={c.busy} onClick={() => void c.refresh()}>重新核对排期条件</Button></div>
    {(detail.status === 'scheduled' || c.confirmed) && <p role="status">排期已确认。<BerlinTime at={detail.schedule?.at} /> · 公开发布结果仍以远端观测为准。</p>}
    <div className={styles.row}>
      {c.eligible && <label>发布时间（柏林当地时间）<Input aria-label="发布时间（柏林当地时间）" type="datetime-local" value={c.when} min={min} max={max} disabled={editing || c.busy || !data?.available} onChange={event => c.setWhen(event.target.value)} /></label>}
      <div className={styles.facts}><span>作者：{AUTHOR_KIND_LABEL[detail.meta.author_kind]} {detail.meta.owner ? '@' + detail.meta.owner : ''}</span><span>原帖发布：{formatDate(detail.meta.created_at)}</span>{detail.meta.coauthors.length > 0 && <span>合作方：{detail.meta.coauthors.join('、')}</span>}{detail.meta.permalink && <a href={detail.meta.permalink} target="_blank" rel="noopener noreferrer">查看原帖 ↗</a>}</div>
    </div>
    {c.reason && <p className={styles.help}>{!data?.available && data ? /probe|config|验收证据/i.test(data.reason) ? '发布环境尚未完成本机核验，暂时不能创建排期。' : '内容或发布条件尚未满足；请复核正文、图片和本篇处理状态。' : c.reason}</p>}
    {min && max && <div className={styles.help}>可选 {min.replace('T', ' ')} 至 {max.replace('T', ' ')}（柏林） <Space wrap>{c.eligible && data?.default_times.map(time => <Button size="small" key={time} disabled={editing || c.busy || !data.available || !isCompleteScheduleTime(c.when)} onClick={() => c.setWhen(c.when.slice(0, 10) + 'T' + time)}>{time} 柏林</Button>)}</Space></div>}
    {c.eligible && <Collapse ghost items={[{ key: 'occupancy', label: `${near ? '附近已有同渠道排期 · ' : ''}查看所选时刻前后一天的同渠道占用（间隔 ${calendar.data?.gap_minutes ?? '—'} 分钟）`, children: <>
      <p>依据缓存：<BerlinTime at={calendar.data?.cached_at ? berlinInput(calendar.data.cached_at) : null} />{calendar.data?.stale ? ' · 可能已过期' : ''}；正式排期前会再次核对后台。</p>
      {occupied.length ? occupied.map((card, i) => <p key={i}><BerlinTime at={card.at_business} /> · {card.delivery === 'published' ? '已观测到公开发布' : card.delivery === 'scheduled' ? '已创建定时任务' : '发布状态待核验'}</p>) : <p>当前缓存未发现所选时刻附近的同渠道记录，不能据此保证空闲。</p>}
    </> }]} />}
    {!!c.error && <Alert type="warning" title={c.errorMessage} />}
    {c.suggestions.length > 0 && <Space wrap><span>可以改选：</span>{c.suggestions.map(value => <Button key={value} onClick={() => c.setWhen(berlinInput(value))}>{berlinInput(value).replace('T', ' ')} 柏林</Button>)}</Space>}
    {isConflict(c.error) && <ConflictRecovery kind="schedule" onRecover={() => void c.refresh()} recovering={c.options.isFetching} />}
    {(detail.publication || detail.status === 'approved') && <p><Button disabled={c.busy || editing} onClick={() => void c.recover()}>核对并补齐本地回执</Button> <Typography.Text type="secondary">只核对已有发布尝试，恢复结果需再次确认。</Typography.Text></p>}
    {detail.review.wake_at && <p>恢复审校：<ShanghaiTime at={detail.review.wake_at} /></p>}{detail.review.reason && <p>处理理由：{detail.review.reason}</p>}{detail.review.handoff_url && <a href={detail.review.handoff_url} target="_blank" rel="noopener noreferrer">查看手工发布的帖子</a>}
    {data?.reason && !data.available && <Collapse ghost items={[{ key: 'reason', label: '查看核验信息', children: <pre className={styles.diagnostic}>{data.reason}</pre> }]} />}
    <Modal title="确认本篇发布内容与时刻" open={!!c.snapshot} onCancel={() => { if (!c.busy) c.setSnapshot(null) }} okText="确认通过并创建排期" cancelText="继续核对" confirmLoading={c.busy} onOk={() => void c.submit()}>
      {c.snapshot && <><p>{c.snapshot.detail.platform === 'facebook' ? 'Facebook' : 'Instagram'} · {c.snapshot.body.scheduled_at.replace('T', ' ')} 柏林</p><p>{c.snapshot.detail.text.de_human ? '采用人工复核文案' : '采用当前德语文案'} · {c.snapshot.detail.images.length} 张图片，人工图片优先。</p>
        <div className={styles.preview}>{c.snapshot.detail.text.de_human || c.snapshot.detail.text.de_machine || c.snapshot.detail.localization.body_de}</div>
        <p>确认后将使用此刻核对的内容与版本创建排期。</p></>}
    </Modal>
  </section>
}
