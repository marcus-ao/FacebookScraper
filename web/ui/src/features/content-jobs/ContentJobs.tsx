import { useEffect, useState } from 'react'
import { createPortal } from 'react-dom'
import { Alert, Button, Checkbox, Collapse, Drawer, Input, Select, Space, Tooltip, Typography } from 'antd'
import { useQuery } from '@tanstack/react-query'
import type { ContentJob, TaskDetail } from '@/types/domain'
import { initialCapabilities, initialTranslate, refinementCapabilities, refine, jobRunning, getTemplate } from '@/services/jobs'
import { useContentJob } from '@/hooks/useContentJob'
import { PaidActionButton } from '@/components/PaidActionButton'
import { initialTranslationDisabledReason, refinementDisabledReason, textCandidateDisabledReason } from '@/lib/action-reasons'
import { isConflict } from '@/services/http'
import { displayLinks } from '@/features/localization/model'
import { useDeploymentDraft } from '@/hooks/useDeploymentDraft'
import styles from './ContentJobs.module.css'

const labels: Record<string, string> = { pending: '已受理，等待处理', running: '正在生成', succeeded: '本轮处理完成', failed: '处理尚未完成', interrupted: '处理已中断，待核对' }

/** 点一下追加到输入框。每条对应一种实际出现过的失败，`why` 解释为什么这么说管用。 */
const IMAGE_PRESETS: readonly { readonly text: string; readonly why: string }[] = [
  { text: '这一段德语太长把版面挤了，请用更短的说法重排，不要缩小其它文字。', why: '模板要求先换更短的说法，再动版面' },
  { text: '文字压到产品上了，请在原来的文字框范围内重新断行。', why: '把问题指到具体位置，比“排版不好看”有用' },
  { text: '第 __ 行的译法不对，请改成「__」。', why: '填上你要的词，模型不用猜' },
  { text: '保持原图不动，只把 CTA 按钮上的文字换成德语。', why: '限定改动范围，避免模型顺手重画别处' },
  { text: '型号 / 优惠码被改了，请逐字符还原成「__」。', why: '这类字符串长得像单词，最容易被当成文案翻掉' },
]
export function ContentJobs({ detail, editing, refresh, onCandidate, initialContainer }: { detail: TaskDetail; editing: boolean; refresh: () => Promise<TaskDetail>; onCandidate: (job: ContentJob) => void; initialContainer: HTMLDivElement | null }) {
  const initial = useQuery({ queryKey: ['initial-capabilities', detail.id, detail.text.source_text_sha256, detail.review.revision], queryFn: () => initialCapabilities(detail.id) })
  const capabilities = useQuery({ queryKey: ['refinement-capabilities', detail.id], queryFn: () => refinementCapabilities(detail.id) })
  const [consent, setConsent] = useState(false), [busy, setBusy] = useState(false), [error, setError] = useState<unknown>(null)
  const [kind, setKind] = useState<'text' | 'image'>('text'), [media, setMedia] = useState(0), [instruction, setInstruction] = useState('')
  const [submittedInstruction, setSubmittedInstruction] = useState('')
  useDeploymentDraft(instruction !== submittedInstruction)
  const [templateOpen, setTemplateOpen] = useState(false)
  const template = useQuery({ queryKey: ['template', kind], queryFn: () => getTemplate(kind), enabled: templateOpen })
  const first = useContentJob('initial-translation', initial.data?.job, job => {
    void initial.refetch(); if (!editing && ['succeeded', 'failed'].includes(job.status)) void refresh()
  })
  const lastJob = capabilities.data?.jobs.find(job => jobRunning(job)) ?? capabilities.data?.jobs.at(-1)
  const next = useContentJob('refinements', lastJob, job => {
    void capabilities.refetch(); if (!editing && job.kind === 'image' && job.status === 'succeeded') void refresh()
  })
  useEffect(() => { setConsent(false) }, [detail.text.source_text_sha256, detail.review.revision])
  const eligible = ['pending_review', 'edited', 'not_ready'].includes(detail.status) && !detail.text.stale
  const remaining = Math.max(0, (capabilities.data?.max_refine_per_media ?? 0) - (capabilities.data?.image_attempts[String(media)] ?? 0))
  const firstReason = initialTranslationDisabledReason({ editing, busy, running: jobRunning(first.job),
    interrupted: first.job?.status === 'interrupted', available: !!initial.data?.available, consented: consent })
  const nextReason = refinementDisabledReason({ editing, busy, eligible, running: jobRunning(next.job),
    interrupted: next.job?.status === 'interrupted', instruction, capabilitiesLoaded: !!capabilities.data, kind, remaining,
    manualImage: !!detail.images[media]?.manual })
  const act = async (family: 'initial' | 'refine') => {
    setBusy(true); setError(null)
    try { if (family === 'initial' && initial.data) { first.accept(await initialTranslate(detail, initial.data, consent)); setConsent(false); await initial.refetch() }
      else { next.accept(await refine(detail, kind, instruction, media)); setSubmittedInstruction(instruction); await capabilities.refetch() } }
    catch (cause) { setError(cause); if (isConflict(cause) && !editing) { setConsent(false); await refresh(); await initial.refetch() } }
    finally { setBusy(false) }
  }
  const status = (flow: typeof first, initialFlow: boolean) => flow.job && <div className={styles.job}>
    <p role="status">{labels[flow.job.status] ?? '处理状态待核对'}{flow.job.cost_usd !== undefined && ` · 已记录费用 US$${flow.job.cost_usd.toFixed(4)}`}</p>
    {flow.job.status === 'interrupted' && <Button disabled={busy || editing} onClick={() => { setBusy(true); void flow.recover().catch(setError).finally(() => setBusy(false)) }}>核对并恢复本地状态（不重新生成）</Button>}
    {flow.job.worker_state === 'unknown' && <p>旧任务缺少进程依据，需要人工核对。</p>}
    {!initialFlow && flow.job.kind === 'text' && flow.job.status === 'succeeded' && <>
      <pre className={styles.candidate}>{displayLinks(flow.job.body_de ?? '')}</pre>
      <Button disabled={!!textCandidateDisabledReason(flow.job, detail.text.source_text_sha256, eligible)} onClick={() => { if (flow.job) onCandidate(flow.job) }}>采用到正文编辑区</Button>
      <p className={styles.help}>标签与链接选择保留，确认后请保存。{textCandidateDisabledReason(flow.job, detail.text.source_text_sha256, eligible)}</p>
    </>}
    {!initialFlow && flow.job.kind === 'image' && flow.job.status === 'succeeded' && <p>新版图片已生成；已有人工图片时继续优先使用人工图片。</p>}
    <Collapse ghost items={[{ key: 'job', label: '查看处理依据', children: <pre className={styles.diagnostic}>{JSON.stringify(flow.job, null, 2)}</pre> }]} />
  </div>
  return <div className={styles.wrap}>
    {initial.data?.third_party && initialContainer && createPortal(<section className={styles.initial} data-initial-translation>
      <Alert type="warning" showIcon title="这篇来自第三方作者，请先查看原帖，确认可用于德国站内容运营。" description={<Space wrap>
        {initial.data.available && !jobRunning(first.job) ? <><Checkbox checked={consent} disabled={editing || busy} onChange={event => setConsent(event.target.checked)}>我已确认可以处理这篇内容，开始本篇模型处理。</Checkbox>
          <PaidActionButton label="翻译这篇" amount="按实际用量计费" {...(firstReason ? { disabledReason: firstReason } : {})} loading={busy} onClick={() => void act('initial')} /></> : <span>{first.job ? '请查看本篇处理进度。' : '请继续审校；若仍缺正文或德语图，可刷新处理状态。'}</span>}
        <Button size="small" onClick={() => { void initial.refetch(); void first.refresh() }}>刷新处理状态</Button>
      </Space>} />{status(first, true)}
    </section>, initialContainer)}
    {error || initial.error || capabilities.error || first.error || next.error ? <Alert type="warning" title="本次处理未完成或状态暂不可读，已保留输入，请刷新状态核对" /> : null}
    <Collapse items={[{ key: 'refine', label: '单篇优化（可选）', children: <>
      <Space><label>优化内容 <Select aria-label="优化内容" value={kind} disabled={busy || jobRunning(next.job)} onChange={setKind} options={[{ value: 'text', label: '文案' }, { value: 'image', label: '图片' }]} /></label>
        {kind === 'image' && <Select aria-label="选择图片" value={media} disabled={busy || jobRunning(next.job)} onChange={setMedia} options={detail.images.map((_, index) => ({ value: index, label: `第 ${index + 1} 张` }))} />}
        <Button type="text" onClick={() => setTemplateOpen(true)}>查看模板（只读）</Button></Space>
      <label className={styles.field}>这一次希望怎样调整<Input.TextArea aria-label="这一次希望怎样调整" rows={2} maxLength={4000} value={instruction} disabled={busy || jobRunning(next.job)} onChange={event => setInstruction(event.target.value)} /></label>
      {kind === 'image' && <div className={styles.presets}>
        <span className={styles.help}>常用说法（点一下追加，再按实际情况改）：</span>
        <Space wrap size={[4, 4]}>{IMAGE_PRESETS.map(preset => <Tooltip key={preset.text} title={preset.why}>
          <Button size="small" type="dashed" disabled={busy || jobRunning(next.job)}
            onClick={() => setInstruction(current => current.trim() ? `${current.trim()}\n${preset.text}` : preset.text)}>{preset.text}</Button>
        </Tooltip>)}</Space>
      </div>}
      <PaidActionButton label={kind === 'image' ? '生成图片' : '生成文案候选'} amount={kind === 'image' && capabilities.data?.estimated_image_usd != null ? `约 US$${capabilities.data.estimated_image_usd.toFixed(3)}` : '按实际用量计费'} {...(nextReason ? { disabledReason: nextReason } : {})} {...(kind === 'image' ? { remaining } : {})} loading={busy} onClick={() => void act('refine')} />
      {kind === 'image' && <Typography.Paragraph type="secondary">当前模型：{capabilities.data?.image_model ?? '读取中'}。{capabilities.data?.estimate_basis}；费用以实际记录为准。每张图最多受理 {capabilities.data?.max_refine_per_media ?? 3} 次，<strong>失败的那次也算一次</strong>；生成过的版本可以在图片页比较后换回去。</Typography.Paragraph>}
      {status(next, false)}<Button type="text" onClick={() => { void capabilities.refetch(); void next.refresh() }}>刷新任务状态</Button>
    </> }]} />
    <Drawer title="生成模板（只读）" open={templateOpen} onClose={() => setTemplateOpen(false)} size="large"><pre className={styles.diagnostic}>{template.isPending ? '正在读取模板…' : template.data?.content ?? '模板暂时不可读'}</pre></Drawer>
  </div>
}
