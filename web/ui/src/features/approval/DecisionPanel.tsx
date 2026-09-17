import { useState } from 'react'
import { Alert, Button, Collapse, Input, Modal, Progress, Space, Tag, Typography } from 'antd'
import type { TaskDetail } from '@/types/domain'
import type { ApprovalController } from '@/hooks/useApproval'
import { useCalendar } from '@/hooks/useCalendar'
import { BusinessTime, ShanghaiTime } from '@/components/Time'
import { ConflictRecovery } from '@/components/ConflictRecovery'
import { DisabledReason } from '@/components/DisabledReason'
import { nearbyOccupancy } from './occupancy'
import { audienceHint, zonedInput, AUTHOR_KIND_LABEL, formatDate } from '@/lib/format'
import { isConflict } from '@/services/http'
import { isCompleteScheduleTime } from '@/lib/action-reasons'
import styles from './DecisionPanel.module.css'

export function ApprovalAction({ controller: c }: { controller: ApprovalController }) {
  if (!c.locked) {
    return <DisabledReason label="编辑确认无误" reason={c.lockable ? '' : c.options.data?.lock_reason || '内容尚未准备好'}>
      <Button aria-label="编辑确认无误" type="primary" disabled={!c.lockable || c.busy} loading={c.busy} onClick={() => void c.lock()}>编辑确认无误</Button>
    </DisabledReason>
  }
  return <DisabledReason label="确认发布时间并排期" reason={c.reason}>
    <Button aria-label="确认发布时间并排期" type="primary" disabled={!!c.reason} loading={c.busy} onClick={c.open}>确认发布时间并排期</Button>
  </DisabledReason>
}

/** 提交跑在请求之外：关掉页面再回来，这块还在。 */
function SubmissionProgress({ controller: c }: { controller: ApprovalController }) {
  const op = c.operation
  if (!op) return null
  if (op.status === 'running') {
    return <Alert type="info" title={`正在创建排期：第 ${op.step_index}/${op.step_total} 步 · ${op.step}`}
      description={<><Progress percent={Math.round((op.step_index / op.step_total) * 100)} size="small" />
        <Typography.Text type="secondary">浏览器正在后台操作，通常需要几十秒到几分钟。可以离开这个页面，回来还能看到进度。</Typography.Text></>} />
  }
  if (op.status === 'succeeded') return <Alert type="success" title="排期已创建并回读确认" />
  if (op.status === 'uncertain') {
    return <Alert type="warning" title="这次提交结果不明确"
      description={<>{op.message}<br />⛔ 不要直接重新提交：先用下面的「核对并补齐本地回执」查清远端到底有没有收下。</>} />
  }
  return <Alert type="warning" title="排期没有创建成功" description={op.message} />
}

export function DecisionPanel({ detail, controller: c, editing }: { detail: TaskDetail; controller: ApprovalController; editing: boolean }) {
  const calendar = useCalendar()
  const [undoOpen, setUndoOpen] = useState(false), [undoReason, setUndoReason] = useState('')
  const data = c.options.data
  const zone = data?.business_timezone
  const min = zonedInput(data?.earliest, zone), max = zonedInput(data?.latest, zone)
  const { cards: occupied, tooClose: near } = nearbyOccupancy(calendar.data?.cards, detail.platform, c.when, calendar.data?.gap_minutes ?? 0)
  const audience = audienceHint(c.when, zone)
  return <section className={styles.panel} aria-label="审核与排期">
    <div className={styles.heading}><h2>审核与排期</h2><Button type="text" size="small" disabled={c.busy} onClick={() => void c.refresh()}>重新核对排期条件</Button></div>
    {(detail.status === 'scheduled' || c.confirmed) && <p role="status">排期已确认。<BusinessTime at={detail.schedule?.at} /> · 公开发布结果仍以远端观测为准。</p>}
    <SubmissionProgress controller={c} />
    {c.locked && <p role="status" className={styles.help}>
      <Tag color="processing">内容已冻结</Tag>正文与图片已按当前版本锁定，不会再被误改。要改内容请先解除冻结。
      <Button type="link" size="small" disabled={c.busy} onClick={() => void c.unlock()}>解除冻结</Button>
    </p>}
    <div className={styles.row}>
      {c.eligible && <label>发布时间（北京时间）<Input aria-label="发布时间（北京时间）" type="datetime-local" value={c.when} min={min} max={max} disabled={editing || c.busy || !data?.available} onChange={event => c.setWhen(event.target.value)} /></label>}
      <div className={styles.facts}><span>作者：{AUTHOR_KIND_LABEL[detail.meta.author_kind]} {detail.meta.owner ? '@' + detail.meta.owner : ''}</span><span>原帖发布：{formatDate(detail.meta.created_at)}</span>{detail.meta.coauthors.length > 0 && <span>合作方：{detail.meta.coauthors.join('、')}</span>}{detail.meta.permalink && <a href={detail.meta.permalink} target="_blank" rel="noopener noreferrer">查看原帖 ↗</a>}</div>
    </div>
    {/* 选时刻的人在北京，看帖子的人在德国；这一行不显示出来就只能靠记时差。 */}
    {c.eligible && audience && <p className={styles.help} role="note">德国受众那边是 <strong>{audience.text}</strong>
      {audience.quiet && <Alert type="warning" title="这个时刻德国还在凌晨，粉丝多半看不到" description="可以继续排期；如果不是有意选的，换一个白天的时刻。" />}</p>}
    {c.reason && <p className={styles.help}>{!data?.available && data ? /probe|config|验收证据/i.test(data.reason) ? '发布环境尚未完成本机核验，暂时不能创建排期。' : '内容或发布条件尚未满足；请复核正文、图片和本篇处理状态。' : c.reason}</p>}
    {min && max && <div className={styles.help}>可选 {min.replace('T', ' ')} 至 {max.replace('T', ' ')}（北京） <Space wrap>{c.eligible && data?.default_times.map(time => <Button size="small" key={time} disabled={editing || c.busy || !data.available || !isCompleteScheduleTime(c.when)} onClick={() => c.setWhen(c.when.slice(0, 10) + 'T' + time)}>{time} 北京</Button>)}</Space></div>}
    {c.eligible && <Collapse ghost items={[{ key: 'occupancy', label: `${near ? '附近已有同渠道排期 · ' : ''}查看所选时刻前后一天的同渠道占用（间隔 ${calendar.data?.gap_minutes ?? '—'} 分钟）`, children: <>
      <p>依据缓存：<BusinessTime at={calendar.data?.cached_at ? zonedInput(calendar.data.cached_at, zone) : null} />{calendar.data?.stale ? ' · 可能已过期' : ''}；正式排期前会再次核对后台。</p>
      {occupied.length ? occupied.map((card, i) => <p key={i}><BusinessTime at={card.at_business} /> · {card.delivery === 'published' ? '已观测到公开发布' : card.delivery === 'scheduled' ? '已创建定时任务' : '发布状态待核验'}</p>) : <p>当前缓存未发现所选时刻附近的同渠道记录，不能据此保证空闲。</p>}
    </> }]} />}
    {!!c.error && <Alert type="warning" title={c.errorMessage} />}
    {c.suggestions.length > 0 && <Space wrap><span>可以改选：</span>{c.suggestions.map(value => <Button key={value} onClick={() => c.setWhen(zonedInput(value, zone))}>{zonedInput(value, zone).replace('T', ' ')} 北京</Button>)}</Space>}
    {isConflict(c.error) && <ConflictRecovery kind="schedule" onRecover={() => void c.refresh()} recovering={c.options.isFetching} />}
    {(detail.publication || detail.status === 'approved') && <p><Button disabled={c.busy || editing} onClick={() => void c.recover()}>核对并补齐本地回执</Button> <Typography.Text type="secondary">只核对已有发布尝试，恢复结果需再次确认。</Typography.Text></p>}
    {detail.status === 'scheduled' && <p><Button danger disabled={c.busy} onClick={() => setUndoOpen(true)}>我已在后台删除这条排期</Button> <Typography.Text type="secondary">系统不会替你删远端卡片；删完回来登记，它会重读月历核实。</Typography.Text></p>}
    {detail.review.wake_at && <p>恢复审校：<ShanghaiTime at={detail.review.wake_at} /></p>}{detail.review.reason && <p>处理理由：{detail.review.reason}</p>}{detail.review.handoff_url && <a href={detail.review.handoff_url} target="_blank" rel="noopener noreferrer">查看手工发布的帖子</a>}
    {data?.reason && !data.available && <Collapse ghost items={[{ key: 'reason', label: '查看核验信息', children: <pre className={styles.diagnostic}>{data.reason}</pre> }]} />}
    <Modal title="确认本篇发布内容与时刻" open={!!c.snapshot} onCancel={() => { if (!c.busy) c.setSnapshot(null) }} okText="确认并创建排期" cancelText="继续核对" confirmLoading={c.busy} onOk={() => void c.submit()}>
      {c.snapshot && <><p>{c.snapshot.detail.platform === 'facebook' ? 'Facebook' : 'Instagram'} · {c.snapshot.body.scheduled_at.replace('T', ' ')} 北京{audience && <> · 德国 {audience.text}</>}</p><p>{c.snapshot.detail.text.de_human ? '采用人工复核文案' : '采用当前德语文案'} · {c.snapshot.detail.images.length} 张图片，人工图片优先。</p>
        <div className={styles.preview}>{c.snapshot.detail.text.de_human || c.snapshot.detail.text.de_machine || c.snapshot.detail.localization.body_de}</div>
        <p>确认后将使用已冻结的这一份内容创建排期。</p></>}
    </Modal>
    <Modal title="登记：已在 Business Suite 删除这条排期" open={undoOpen} okText="我已删除，去核实" cancelText="取消"
      okButtonProps={{ danger: true, disabled: !undoReason.trim() }} confirmLoading={c.busy}
      onCancel={() => { if (!c.busy) setUndoOpen(false) }}
      onOk={() => { void c.unschedule(undoReason).then(() => { setUndoOpen(false); setUndoReason('') }) }}>
      <p>请先在 Business Suite 里删掉这条排期，再回来登记。系统会重新读一遍整月核实卡片确实不在了，核实不过不会改状态。</p>
      <Input.TextArea aria-label="处理说明" rows={3} value={undoReason} maxLength={2000}
        placeholder="说明这条排期是怎么处理的，例如：业务临时改期，已在后台删除" onChange={event => setUndoReason(event.target.value)} />
    </Modal>
  </section>
}
