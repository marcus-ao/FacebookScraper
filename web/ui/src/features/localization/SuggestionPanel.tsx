import { useState } from 'react'
import { Alert, Button, Collapse, Empty, Space, Tag, Typography } from 'antd'
import { useQuery } from '@tanstack/react-query'
import type { TaskDetail, TextSuggestion, TextSuggestions } from '@/types/domain'
import { refine, jobRunning, refinementCapabilities } from '@/services/jobs'
import { useContentJob } from '@/hooks/useContentJob'
import { PaidActionButton } from '@/components/PaidActionButton'
import { SourceConsentDialog, sourceConsentRequired } from '@/components/SourceConsentDialog'
import styles from './SuggestionPanel.module.css'

const KIND_LABEL = { grammar: '语法', wording: '用词', register: '语域', terminology: '术语', fluency: '流畅度' } as const

/** 建议只能在编辑区里被人采用；这里不写任何一份译文。 */
export function adopt(body: string, item: TextSuggestion): string | null {
  // 生成时保证 quote 唯一，但页面上的正文此后可能已被改动，所以采用前再确认一次。
  if (body.split(item.quote).length !== 2) return null
  // ⚠️ 替换串必须走函数形式。即使搜索的是普通字符串，`$&`、`$'` 这类在替换串里仍会被
  // 当成特殊模式展开——Python 的 str.replace 没有这一条，两边看起来一样但行为不同。
  return body.replace(item.quote, () => item.replacement)
}

export function suggestionsCurrent(stored: TextSuggestions, sourceHash: string, body: string): boolean {
  // 本次可能审的是未保存稿，因此不能只使用服务端相对磁盘正文计算的 current。
  return stored.source_text_sha256 === sourceHash && stored.body_de === body
    && stored.prompt_version === stored.current_prompt_version
}

export function SuggestionPanel({ detail, body, editing, onAdopt, onRefreshed }: {
  detail: TaskDetail
  body: string
  editing: boolean
  onAdopt: (body: string) => void
  onRefreshed: () => void
}) {
  const stored = detail.text_suggestions ?? null
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const [open, setOpen] = useState(!!stored?.items.length)
  const [consentOpen, setConsentOpen] = useState(false)
  const [dismissed, setDismissed] = useState<{ jobId: string; quotes: readonly string[] } | null>(null)
  const ignored = dismissed?.jobId === stored?.job_id ? dismissed?.quotes ?? [] : []
  const ignore = (quote: string) => { if (stored) setDismissed({ jobId: stored.job_id, quotes: [...ignored, quote] }) }
  const capabilities = useQuery({ queryKey: ['refinement-capabilities', detail.id], queryFn: () => refinementCapabilities(detail.id) })
  const lastJob = capabilities.data?.jobs.find(job => jobRunning(job) || job.status === 'interrupted')
    ?? capabilities.data?.jobs.filter(job => job.kind === 'suggest').at(-1)
  const flow = useContentJob('refinements', lastJob, () => { void capabilities.refetch(); onRefreshed() })
  const running = busy || jobRunning(flow.job)
  const ask = async () => {
    setBusy(true); setError(null)
    try { flow.accept(await refine(detail, 'suggest', '', 0, body)); void capabilities.refetch() }
    catch (cause) { setError(cause) } finally { setBusy(false) }
  }
  const reason = detail.read_only ? '冻结账号只供查阅'
    : !['pending_review', 'edited'].includes(detail.status) || detail.text.stale ? '请先恢复审校并核对原文'
    : !editing ? '请先进入编辑德语'
    : !body.trim() ? '还没有德语文案可以挑毛病'
    : !capabilities.data ? '正在读取处理状态'
    : flow.job?.status === 'interrupted' ? '请先在内容处理区核对中断任务'
    : running ? '正在处理内容，请等待结果' : ''
  const current = stored ? suggestionsCurrent(stored, detail.text.source_text_sha256, body) : false
  const items = (stored?.items ?? []).filter(item => !ignored.includes(item.quote))
  return <section className={styles.wrap} aria-label="德语文案优化建议">
    <Collapse activeKey={open ? ['suggestions'] : []} onChange={keys => setOpen(keys.includes('suggestions'))}
      items={[{ key: 'suggestions', label: '文案优化建议（可选）', children: <>
      <div className={styles.head}><p className={styles.help}>候选只供参考，采用后请保存正文。</p>
        <PaidActionButton label={stored ? '重新生成建议' : '生成文案建议'}
          {...(reason ? { disabledReason: reason } : {})} loading={running}
          onClick={() => sourceConsentRequired(detail) ? setConsentOpen(true) : void ask()} />
      </div>
      {error || flow.error ? <Alert type="warning" showIcon title="建议状态暂不可读，请刷新核对后再决定是否重试。" /> : null}
      {flow.job?.kind === 'suggest' && flow.job.status === 'failed' && <Alert type="warning" showIcon title="建议未完成，请核对状态后再决定是否重试。" />}
      {!stored && !flow.job && <p className={styles.help}>暂无建议；可继续人工审校。</p>}
      {stored && <>
      {!current && <Alert type="warning" showIcon
        title="文案或原文在这之后改过了，下面的建议可能已经对不上，「采用」已停用。需要的话重新生成。" />}
      {items.length === 0
        ? <Empty description={stored.items.length ? '这一轮的建议都处理完了。' : '这一轮没有建议；仍需人工审校。'} />
        : items.map(item => {
          const next = adopt(body, item)
          return <article key={item.quote} className={styles.item}>
            <Tag>{KIND_LABEL[item.kind] ?? item.kind}</Tag>
            <div className={styles.diff}>
              <del>{item.quote}</del>
              <ins>{item.replacement}</ins>
            </div>
            <p className={styles.why}>{item.why}</p>
            <Space size="small">
              <Button size="small" type="primary" ghost
                disabled={!editing || !current || next === null}
                onClick={() => { if (current && next !== null) { onAdopt(next); ignore(item.quote) } }}>采用</Button>
              <Button size="small" onClick={() => ignore(item.quote)}>忽略</Button>
              {next === null && current && <Typography.Text type="secondary">这段已经改过，采用不了了</Typography.Text>}
            </Space>
          </article>
        })}
      </>}
    </> }]} />
    <SourceConsentDialog detail={detail} open={consentOpen} busy={busy} onClose={() => setConsentOpen(false)}
      onAccept={() => { setConsentOpen(false); void ask() }} />
  </section>
}
