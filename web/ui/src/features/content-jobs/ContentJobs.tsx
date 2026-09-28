import { useEffect, useState } from 'react'
import { createPortal } from 'react-dom'
import { Alert, Button, Collapse, Input, Space, Tooltip } from 'antd'
import { useQuery } from '@tanstack/react-query'
import type { ContentJob, TaskDetail } from '@/types/domain'
import { initialCapabilities, initialTranslate, refinementCapabilities, refine, jobRunning } from '@/services/jobs'
import { useContentJob } from '@/hooks/useContentJob'
import { PaidActionButton } from '@/components/PaidActionButton'
import { SourceConsentDialog, sourceConsentRequired } from '@/components/SourceConsentDialog'
import { initialTranslationDisabledReason, refinementDisabledReason, textCandidateDisabledReason } from '@/lib/action-reasons'
import { isConflict } from '@/services/http'
import { displayLinks } from '@/features/localization/model'
import { useDeploymentDraft } from '@/hooks/useDeploymentDraft'
import styles from './ContentJobs.module.css'

const labels: Record<string, string> = { pending: '已受理，等待处理', running: '正在生成', succeeded: '本轮处理完成', failed: '处理尚未完成', interrupted: '处理已中断，待核对' }
type Action = 'initial' | 'text' | 'image'

/** 可按实际图中文字调整的示例；点击后只填入指令，不会自动发起生成。 */
const IMAGE_PRESETS: readonly { readonly text: string; readonly why: string }[] = [
  { text: '这一段德语太长把版面挤了，请用更短的说法重排，不要缩小其它文字。', why: '先缩短文字，再调整版面' },
  { text: '文字压到产品上了，请在原来的文字框范围内重新断行。', why: '指明需要调整的位置' },
  { text: '第 __ 行的译法不对，请改成「__」。', why: '填上准确的替换文字' },
  { text: '保持原图不动，只把 CTA 按钮上的文字换成德语。', why: '限定改动范围' },
  { text: '型号 / 优惠码被改了，请逐字符还原成「__」。', why: '保护必须保持原样的字符' },
]

export function ContentJobs({ detail, editing, refresh, onCandidate, initialContainer, imageContainer, imageIndex, imageGenerationRequest }: {
  detail: TaskDetail; editing: boolean; refresh: () => Promise<TaskDetail>; onCandidate: (job: ContentJob) => void;
  initialContainer: HTMLDivElement | null; imageContainer: HTMLDivElement | null; imageIndex: number;
  imageGenerationRequest?: { index: number; serial: number } | null;
}) {
  const initial = useQuery({ queryKey: ['initial-capabilities', detail.id, detail.text.source_text_sha256, detail.review.revision], queryFn: () => initialCapabilities(detail.id) })
  const capabilities = useQuery({ queryKey: ['refinement-capabilities', detail.id], queryFn: () => refinementCapabilities(detail.id) })
  const [busy, setBusy] = useState(false), [error, setError] = useState<unknown>(null)
  const [pending, setPending] = useState<Action | null>(null)
  const [textInstruction, setTextInstruction] = useState(''), [submittedText, setSubmittedText] = useState('')
  const [imageInstructions, setImageInstructions] = useState<Record<number, string>>({})
  const [submittedImages, setSubmittedImages] = useState<Record<number, string>>({})
  const imageInstruction = imageInstructions[imageIndex] ?? ''
  const setImageInstruction = (value: string | ((old: string) => string)) => setImageInstructions(old => ({ ...old,
    [imageIndex]: typeof value === 'function' ? value(old[imageIndex] ?? '') : value }))
  const [textOpen, setTextOpen] = useState(false), [imageOpen, setImageOpen] = useState(false)
  useDeploymentDraft(textInstruction !== submittedText || Object.keys(imageInstructions).some(key =>
    imageInstructions[Number(key)] !== submittedImages[Number(key)]))
  useEffect(() => {
    if (!imageGenerationRequest) return
    setImageOpen(true)
    requestAnimationFrame(() => imageContainer?.scrollIntoView({ block: 'center' }))
  }, [imageGenerationRequest?.serial, imageContainer])
  const first = useContentJob('initial-translation', initial.data?.job, job => {
    void initial.refetch(); if (!editing && ['succeeded', 'failed'].includes(job.status)) void refresh()
  })
  const lastJob = capabilities.data?.jobs.find(job => jobRunning(job)) ?? capabilities.data?.jobs.at(-1)
  const next = useContentJob('refinements', lastJob, job => {
    void capabilities.refetch(); if (!editing && job.kind === 'image' && job.status === 'succeeded') void refresh()
  })
  useEffect(() => { setPending(null) }, [detail.text.source_text_sha256, detail.review.revision])
  const eligible = ['pending_review', 'edited', 'not_ready'].includes(detail.status) && !detail.text.stale
  const remaining = Math.max(0, (capabilities.data?.max_refine_per_media ?? 0) - (capabilities.data?.image_attempts[String(imageIndex)] ?? 0))
  const firstReason = initialTranslationDisabledReason({ editing, busy, running: jobRunning(first.job),
    interrupted: first.job?.status === 'interrupted', available: !!initial.data?.available, consented: true,
    reason: initial.data?.reason ?? '' })
  const nextReason = (kind: 'text' | 'image') => refinementDisabledReason({ editing, busy, eligible,
    running: jobRunning(next.job), interrupted: next.job?.status === 'interrupted',
    instruction: kind === 'text' ? textInstruction : imageInstruction, capabilitiesLoaded: !!capabilities.data,
    kind, remaining, manualImage: !!detail.images[imageIndex]?.manual,
    originalConfirmed: detail.images[imageIndex]?.selection === 'original_confirmed',
    hasGerman: !!detail.localization.body_de.trim() })
  const act = async (action: Action, consent = false) => {
    setBusy(true); setError(null)
    try {
      if (action === 'initial' && initial.data) {
        first.accept(await initialTranslate(detail, initial.data, consent)); await initial.refetch()
      } else if (action !== 'initial') {
        const instruction = action === 'text' ? textInstruction : imageInstruction
        next.accept(await refine(detail, action, instruction, imageIndex))
        if (action === 'text') setSubmittedText(instruction)
        else setSubmittedImages(old => ({ ...old, [imageIndex]: instruction }))
        await capabilities.refetch()
      }
    } catch (cause) {
      setError(cause)
      if (isConflict(cause) && !editing) {
        try { await refresh(); await initial.refetch() }
        catch { /* 保留提交错误与输入。 */ }
      }
    } finally { setBusy(false) }
  }
  const start = (action: Action) => {
    if (sourceConsentRequired(detail, initial.data?.third_party)) setPending(action)
    else void act(action)
  }
  const refreshStatus = async (action: 'initial' | 'refine') => {
    setBusy(true)
    try {
      const query = action === 'initial' ? initial : capabilities
      const flow = action === 'initial' ? first : next
      const [, result] = await Promise.all([query.refetch({ throwOnError: true }), flow.refresh()])
      if (result?.error) throw result.error
      setError(null)
    } catch (cause) { setError(cause) }
    finally { setBusy(false) }
  }
  const status = (flow: typeof first, action: 'initial' | 'text' | 'image') => flow.job
    && (action === 'initial' || flow.job.kind === action) && <div className={styles.job}>
      <p role="status">{labels[flow.job.status] ?? '处理状态待核对'}</p>
      {flow.job.status === 'interrupted' && <Button disabled={busy || editing}
        onClick={() => { setBusy(true); void flow.recover().catch(setError).finally(() => setBusy(false)) }}>核对并恢复处理状态</Button>}
      {flow.job.worker_state === 'unknown' && <p>处理状态需要人工核对。</p>}
      {action === 'text' && flow.job.status === 'succeeded' && <>
        <pre className={styles.candidate}>{displayLinks(flow.job.body_de ?? '')}</pre>
        <Button disabled={!!textCandidateDisabledReason(flow.job, detail.text.source_text_sha256, eligible)}
          onClick={() => { if (flow.job) onCandidate(flow.job) }}>采用到正文编辑区</Button>
        <p className={styles.help}>采用后请保存并重新确认正文。</p>
      </>}
      {action === 'image' && flow.job.status === 'succeeded' && <p>新版图片已生成，请在上方核对并确认。</p>}
    </div>
  const currentError = error || initial.error || capabilities.error || first.error || next.error
  const errorDisplay = currentError && <Alert type="warning" showIcon
    title="本次处理未完成或状态暂不可读，请刷新状态后再决定是否重试" />
  return <div className={styles.wrap} data-content-jobs>
    {initialContainer && (sourceConsentRequired(detail, initial.data?.third_party) || !detail.localization.body_de.trim())
      && createPortal(<section className={styles.initial} data-initial-translation>
        {!jobRunning(first.job) && <PaidActionButton label="生成德语初稿"
          {...(firstReason ? { disabledReason: firstReason } : {})} loading={busy} onClick={() => start('initial')} />}
        {!initial.data && <p className={styles.help}>正在核对初稿生成条件…</p>}
        {/* 禁用原因只放在悬停提示里，点按钮的人看到的就是“没反应”。 */}
        {initial.data && !jobRunning(first.job) && firstReason && <p className={styles.help} role="status">{firstReason}</p>}
        {status(first, 'initial')}
        {first.job && <Button size="small" type="link" disabled={busy} onClick={() => void refreshStatus('initial')}>刷新处理状态</Button>}
      </section>, initialContainer)}
    {errorDisplay}
    <Collapse activeKey={textOpen ? ['text'] : []} onChange={keys => setTextOpen(keys.includes('text'))}
      items={[{ key: 'text', label: '调整德语文案（可选）', children: <>
        <label className={styles.field}>希望怎样调整文案<Input.TextArea aria-label="希望怎样调整文案" rows={2} maxLength={4000}
          value={textInstruction} disabled={busy || jobRunning(next.job)} onChange={event => setTextInstruction(event.target.value)} /></label>
        <PaidActionButton label="生成文案候选" {...(nextReason('text') ? { disabledReason: nextReason('text') } : {})}
          loading={busy} onClick={() => start('text')} />
        <p className={styles.help}>使用模型额度；候选只有被采用并保存后才会进入正文。</p>
        {status(next, 'text')}
        <Button type="link" disabled={busy} onClick={() => void refreshStatus('refine')}>刷新处理状态</Button>
      </> }]} />
    {imageContainer && detail.images.length > 0 && createPortal(<section className={styles.imageTools} aria-label="图片生成与调整">
      <Collapse activeKey={imageOpen ? ['image'] : []} onChange={keys => setImageOpen(keys.includes('image'))}
        items={[{ key: 'image', label: `生成或调整第 ${imageIndex + 1} 张图片（可选）`, children: <>
          <label className={styles.field}>希望怎样调整这张图片<Input.TextArea aria-label="希望怎样调整这张图片" rows={2}
            maxLength={4000} value={imageInstruction} disabled={busy || jobRunning(next.job)}
            onChange={event => setImageInstruction(event.target.value)} /></label>
          <div className={styles.presets}><span className={styles.help}>可参考以下说法，再按实际图片修改：</span>
            <Space wrap size={[4, 4]}>{IMAGE_PRESETS.map(preset => <Tooltip key={preset.text} title={preset.why}>
              <Button size="small" disabled={busy || jobRunning(next.job)}
                onClick={() => setImageInstruction(current => current.trim() ? `${current.trim()}\n${preset.text}` : preset.text)}>{preset.text}</Button>
            </Tooltip>)}</Space></div>
          <p className={styles.help}>{capabilities.data ? `本张还可调整 ${remaining} 次；失败的尝试也计入次数。` : '正在核对本张剩余次数…'}使用模型额度。</p>
          <PaidActionButton label="生成图片" {...(nextReason('image') ? { disabledReason: nextReason('image') } : {})}
            loading={busy} onClick={() => start('image')} />
          {status(next, 'image')}
          {errorDisplay}
          <Button type="link" disabled={busy} onClick={() => void refreshStatus('refine')}>刷新处理状态</Button>
        </> }]} />
    </section>, imageContainer)}
    <SourceConsentDialog detail={detail} open={!!pending} busy={busy} onClose={() => setPending(null)}
      onAccept={() => { const action = pending; setPending(null); if (action) void act(action, true) }} />
  </div>
}
