import { useEffect, useRef, useState } from 'react'
import { Alert, Button, Input, Modal, Progress, Space, Tag, Typography } from 'antd'
import type { CheckResult, TaskDetail } from '@/types/domain'
import type { ApprovalController } from '@/hooks/useApproval'
import { useCalendar } from '@/hooks/useCalendar'
import { BusinessTime, ShanghaiTime } from '@/components/Time'
import { CopyButton } from '@/components/CopyButton'
import { ConflictRecovery } from '@/components/ConflictRecovery'
import { DisabledReason } from '@/components/DisabledReason'
import { nearbyOccupancy } from './occupancy'
import { SchedulePreviewDialog } from './SchedulePreviewDialog'
import { PLATFORM_LABEL, wallMinutesApart, zonedInput } from '@/lib/format'
import { approvalBlockNotice } from '@/lib/action-reasons'
import { isConflict } from '@/services/http'
import { checkLocalization } from '@/services/localization'
import styles from './DecisionPanel.module.css'

export function ApprovalAction({ controller: c }: { controller: ApprovalController }) {
  if (!c.locked) {
    if (!c.lockable) return null
    return <DisabledReason label="编辑确认无误" reason={c.lockable ? '' : c.options.data?.lock_reason || '内容尚未准备好'}>
      <Button aria-label="编辑确认无误" type="primary" disabled={!c.lockable || c.busy} loading={c.busy} onClick={() => void c.lock()}>编辑确认无误</Button>
    </DisabledReason>
  }
  if (c.operation?.status === 'uncertain' || c.operation?.status === 'running') return null
  // 弹层打开时保留触发按钮节点，查询刷新不能让关闭后的焦点丢失。
  return <DisabledReason label="确认发布时间并排期" reason={c.snapshot ? '' : c.reason}>
    <Button aria-label="确认发布时间并排期" type="primary" disabled={!!c.reason} loading={c.busy} onClick={c.open}>确认发布时间并排期</Button>
  </DisabledReason>
}

/** 提交跑在请求之外：关掉页面再回来，这块还在。 */
function SubmissionProgress({ controller: c }: { controller: ApprovalController }) {
  const op = c.operation
  if (!op) return null
  if (op.status === 'running') {
    return <Alert type="info" title="正在创建排期"
      description={<><Progress percent={Math.round((op.step_index / op.step_total) * 100)} size="small" />
        <Typography.Text type="secondary">浏览器正在后台操作，通常需要几十秒到几分钟。可以离开这个页面，回来还能看到进度。</Typography.Text></>} />
  }
  if (op.status === 'succeeded') return <Alert type="success" title="定时排期已确认；公开发布仍待观测" />
  if (op.status === 'uncertain') {
    return <Alert type="warning" title="提交结果待核对"
      description="请先核对已有提交尝试是否被接收，确认结果前不要再次提交。" />
  }
  return <Alert type="warning" title="排期没有创建成功"
    description="请核对这次提交尝试的结果，再决定下一步。" />
}

export function ExactCaptionPreview({ detail, editing, frozenText, frozenOnly = false }: {
  detail: TaskDetail; editing: boolean; frozenText?: string | null; frozenOnly?: boolean;
}) {
  const [caption, setCaption] = useState<CheckResult | null>(null)
  const [open, setOpen] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(false)
  const version = `${detail.id}:${detail.localization.revision}:${detail.text.source_text_sha256}`
  const liveVersion = useRef(version)
  liveVersion.current = version
  useEffect(() => { setCaption(null); setOpen(false); setError(false) },
    [detail.id, detail.localization.revision, detail.text.source_text_sha256, frozenText])
  const load = async () => {
    if (editing || busy) return
    if (frozenText) { setOpen(true); return }
    if (frozenOnly) return
    setBusy(true); setError(false)
    try {
      const checked = await checkLocalization(detail.id, detail.localization)
      if (liveVersion.current !== version) return
      if (!checked.caption) throw new Error('完整文案暂不可读')
      setCaption(checked); setOpen(true)
    } catch { if (liveVersion.current === version) setError(true) }
    finally { setBusy(false) }
  }
  const text = frozenText ?? caption?.caption
  const count = frozenText ? [...frozenText].length : caption?.caption_length
  const nearLimit = detail.platform === 'instagram' && (count ?? 0) >= 2000
  return <section className={styles.captionPreview} aria-label="最终发布文案">
    <div className={styles.captionToolbar}>
      <Button disabled={editing || busy || (frozenOnly && !frozenText)} loading={busy} onClick={() => open ? setOpen(false) : void load()}>
        {open ? '收起完整发布文案' : '查看完整发布文案'}
      </Button>
      {text && <CopyButton text={text} label="复制发布文案" />}
    </div>
    {editing && <p className={styles.help}>请先保存当前修改，再核对服务端完整文案。</p>}
    {frozenOnly && !frozenText && <p className={styles.help}>冻结内容暂不可读，请先核对排期条件。</p>}
    {error && <Alert type="warning" showIcon title="完整文案暂不可读，请刷新后再核对" />}
    {(nearLimit || !!caption?.issues.length) && <p className={styles.help}>发布文案 {count} 字符{detail.platform === 'instagram' && ' / 2200'}</p>}
    {open && text && <pre className={styles.captionText} lang="de">{text}</pre>}
  </section>
}

export function DecisionPanel({ detail, controller: c, editing }: { detail: TaskDetail; controller: ApprovalController; editing: boolean }) {
  const calendar = useCalendar()
  const [undoOpen, setUndoOpen] = useState(false), [undoReason, setUndoReason] = useState('')
  const data = c.options.data
  const zone = data?.business_timezone
  const min = zonedInput(data?.earliest, zone), max = zonedInput(data?.latest, zone)
  const { cards: occupied, tooClose: near } = nearbyOccupancy(calendar.data?.cards, detail.platform, c.when, calendar.data?.gap_minutes ?? 0)
  const nearCards = occupied.filter(card => wallMinutesApart(c.when, card.at_business) <= Math.max(120, (calendar.data?.gap_minutes ?? 0) * 2)).slice(0, 3)
  if (detail.status === 'snoozed' || detail.status === 'skipped' || detail.status === 'handed_off') {
    return <section className={styles.panel} aria-label="处理结果">
      <h2>{detail.status === 'snoozed' ? '已暂缓审校' : detail.status === 'skipped' ? '已跳过本篇' : '已转交人工发布'}</h2>
      {detail.status === 'snoozed' && detail.review.wake_at && <p>恢复审校：<ShanghaiTime at={detail.review.wake_at} /></p>}
      {detail.review.reason && <p>处理理由：{detail.review.reason}</p>}
      {detail.review.handoff_url && <a href={detail.review.handoff_url} target="_blank" rel="noopener noreferrer">查看人工发布结果 ↗</a>}
    </section>
  }
  return <section className={styles.panel} aria-label="审核与排期">
    <div className={styles.heading}><h2>{detail.status === 'scheduled' ? '排期结果' : detail.status === 'approved' ? '提交结果' : c.locked ? '选择发布时间' : '发布前核对'}</h2>
      {c.locked && <Button type="text" size="small" disabled={c.busy} onClick={() => void c.refresh()}>刷新排期条件</Button>}</div>
    {(detail.status === 'scheduled' || c.confirmed) && <p role="status">定时排期已确认 · <BusinessTime at={detail.schedule?.at} />。公开发布仍待观测。</p>}
    <SubmissionProgress controller={c} />
    {c.locked && <p role="status" className={styles.help}>
      <Tag color="processing">内容已冻结</Tag>如需修改正文或图片，请先解除冻结。
      <Button type="link" size="small" disabled={c.busy || detail.read_only} onClick={() => void c.unlock()}>解除冻结</Button>
    </p>}
    {data?.preview?.target && c.locked && <p className={styles.target}>
      发布目标：<strong>{data.preview.target.account || '账号待核对'}</strong> · {PLATFORM_LABEL[data.preview.target.channel]}
    </p>}
    {!['scheduled', 'approved'].includes(detail.status) && <ExactCaptionPreview detail={detail} editing={editing}
      frozenOnly={c.locked} frozenText={c.locked ? data?.preview?.text ?? null : null} />}
    {c.eligible && <div className={styles.row}><label>发布时间<Input aria-label="发布时间" type="datetime-local"
      value={c.when} min={min} max={max} disabled={editing || c.busy || !data?.available}
      onChange={event => c.setWhen(event.target.value)} /></label></div>}
    {c.locked && c.reason && c.operation?.status !== 'uncertain' && <p className={styles.help}>
      {!data?.available && data?.reason ? approvalBlockNotice(data.reason) : c.reason}</p>}
    {c.eligible && min && max && <p className={styles.help}>可选时间：{min.replace('T', ' ')} 至 {max.replace('T', ' ')}</p>}
    {c.eligible && c.when && (near ? <Alert type="warning" showIcon title="所选时刻附近已有同渠道排期"
      description={nearCards.map((card, index) => <span key={index}><BusinessTime at={card.at_business} />{index < nearCards.length - 1 && '、'}</span>)} />
      : calendar.data?.stale || !calendar.data ? <p className={styles.help}>附近排期信息暂未核实；提交前会再次核对。</p>
        : nearCards.length > 0 ? <p className={styles.help}>附近已有同渠道内容，提交前会再次核对。</p> : null)}
    {c.eligible && c.when && near && calendar.data?.stale && <p className={styles.help}>附近排期信息可能已变化；提交前会再次核对。</p>}
    {!!c.error && <Alert type="warning" title={c.errorMessage} />}
    {c.suggestions.length > 0 && <Space wrap><span>可以改选：</span>{c.suggestions.map(value => <Button key={value} onClick={() => c.setWhen(zonedInput(value, zone))}>{zonedInput(value, zone).replace('T', ' ')}</Button>)}</Space>}
    {isConflict(c.error) && <ConflictRecovery kind="schedule" onRecover={() => void c.refresh()} recovering={c.options.isFetching} />}
    {(detail.publication || detail.status === 'approved' || c.operation?.status === 'uncertain') && <p><Button disabled={c.busy || editing || detail.read_only} onClick={() => void c.recover()}>核对并补齐本地回执</Button> <Typography.Text type="secondary">只核对已有提交尝试。</Typography.Text></p>}
    {detail.status === 'scheduled' && <p><Button danger disabled={c.busy || detail.read_only} onClick={() => setUndoOpen(true)}>我已在后台删除这条排期</Button> <Typography.Text type="secondary">系统不会替你删远端卡片；删完回来登记，它会重读月历核实。</Typography.Text></p>}
    <SchedulePreviewDialog snapshot={c.snapshot} busy={c.busy} onCancel={() => c.setSnapshot(null)} onConfirm={() => void c.submit()} />
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
