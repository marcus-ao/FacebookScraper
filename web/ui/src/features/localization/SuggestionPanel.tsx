import { useState } from 'react'
import { Alert, Button, Empty, Space, Tag, Typography } from 'antd'
import type { ContentJob, TaskDetail, TextSuggestion } from '@/types/domain'
import { refine, jobRunning } from '@/services/jobs'
import { useContentJob } from '@/hooks/useContentJob'
import { PaidActionButton } from '@/components/PaidActionButton'
import { ShanghaiTime } from '@/components/Time'
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
  const [ignored, setIgnored] = useState<readonly string[]>([])
  const flow = useContentJob('refinements', null, () => onRefreshed())
  const running = busy || jobRunning(flow.job)
  const ask = async () => {
    setBusy(true); setError(null)
    try { flow.accept(await refine(detail, 'suggest', '', 0) as ContentJob) }
    catch (cause) { setError(cause) } finally { setBusy(false) }
  }
  const reason = detail.read_only ? '冻结账号只供查阅'
    : !editing ? '请先进入编辑德语'
    : !body.trim() ? '还没有德语文案可以挑毛病'
    : running ? '正在生成建议' : ''
  const items = (stored?.items ?? []).filter(item => !ignored.includes(item.quote))
  return <section className={styles.wrap} aria-label="德语文案优化建议">
    <div className={styles.head}>
      <h2>优化建议 <Typography.Text type="secondary">模型给意见，改不改你定</Typography.Text></h2>
      <PaidActionButton label={stored ? '重新生成建议' : '让模型挑毛病'} amount="按实际用量计费"
        {...(reason ? { disabledReason: reason } : {})} loading={running} onClick={() => void ask()} />
    </div>
    {error || flow.error ? <Alert type="warning" showIcon title="这次没能生成建议，文案不受影响，可稍后重试" /> : null}
    {flow.job?.status === 'failed' && <Alert type="warning" showIcon title="模型没有给出可用的建议，请稍后重试；本次费用已记录在付费账本" />}
    {!stored && !flow.job && <Empty description="还没有生成过建议。这一步是可选的，不影响保存或通过。" />}
    {stored && <>
      <p className={styles.meta}>
        生成于 <ShanghaiTime at={stored.generated_at} />
        {stored.prompt_version !== stored.current_prompt_version && ` · 按旧版模板（v${stored.prompt_version}）生成`}
      </p>
      {!stored.current && <Alert type="warning" showIcon
        title="文案或原文在这之后改过了，下面的建议可能已经对不上，「采用」已停用。需要的话重新生成。" />}
      {stored.dropped.length > 0 && <Typography.Paragraph type="secondary">
        另有 {stored.dropped.length} 条建议被丢弃：{[...new Set(stored.dropped)].join('；')}。
      </Typography.Paragraph>}
      {items.length === 0
        ? <Empty description={stored.items.length ? '这一轮的建议都处理完了。' : '模型这一轮没挑出值得改的地方。'} />
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
                disabled={!editing || !stored.current || next === null}
                onClick={() => { if (next !== null) { onAdopt(next); setIgnored([...ignored, item.quote]) } }}>采用</Button>
              <Button size="small" onClick={() => setIgnored([...ignored, item.quote])}>忽略</Button>
              {next === null && stored.current && <Typography.Text type="secondary">这段已经改过，采用不了了</Typography.Text>}
            </Space>
          </article>
        })}
    </>}
  </section>
}
