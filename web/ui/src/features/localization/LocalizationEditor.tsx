import { useEffect, useState } from 'react'
import { Alert, Button, Checkbox, Collapse, Input, Select, Space, Tag, Typography } from 'antd'
import { LockOutlined } from '@ant-design/icons'
import type { LocalizationDraft, TaskDetail } from '@/types/domain'
import { suggestHashtags } from '@/services/hashtags'
import type { HashtagSuggestions } from '@/services/hashtags'
import { ShanghaiTime } from '@/components/Time'
import { PaidActionButton } from '@/components/PaidActionButton'
import styles from './LocalizationEditor.module.css'

export function parseSemanticTags(value: string, protectedTags: readonly string[]) {
  return [...new Set([...protectedTags, ...value.split(/[\s,，]+/u).filter(Boolean).map(tag => tag.startsWith('#') ? tag : '#' + tag)])]
}
const metrics = { trend_score: 'Google Trends 德国组内指数', media_count: 'Instagram 全球累计帖子数', peer_uses_14d: '德国同类账号近 14 天使用次数' } as const
const canOpen = (value: string) => /^https?:\/\/[^\s]+$/i.test(value)

export function LocalizationEditor({ detail, draft, editing, onChange, onInsert }: { detail: TaskDetail; draft: LocalizationDraft; editing: boolean; onChange: (draft: LocalizationDraft) => void; onInsert: (index: number) => void }) {
  const semantic = draft.tags.filter(tag => !draft.protected_tags.includes(tag))
  const source = draft.source_tags.filter(tag => !draft.protected_tags.includes(tag))
  const [input, setInput] = useState(semantic.join(' '))
  const [suggestion, setSuggestion] = useState<HashtagSuggestions | null>(null)
  const [selected, setSelected] = useState<string[]>([])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(false)
  useEffect(() => { setInput(semantic.join(' ')) }, [editing])
  const update = (fields: Partial<LocalizationDraft>) => onChange({ ...draft, ...fields })
  const suggest = async () => {
    setBusy(true); setError(false)
    try { const result = await suggestHashtags(detail); setSuggestion(result); setSelected(result.selected) }
    catch { setError(true) } finally { setBusy(false) }
  }
  const linkUpdate = (index: number, fields: Partial<LocalizationDraft['links'][number]>) => update({ links: draft.links.map((link, i) => i === index ? { ...link, ...fields } : link) })
  return <div className={styles.grid}>
    <section className={styles.section} aria-label="话题标签选择"><h2>话题标签 <Typography.Text type={draft.platform === 'instagram' && draft.tags.length >= 27 ? 'warning' : 'secondary'}>{draft.tags.length}{draft.platform === 'instagram' ? ' / 30' : ''} 个</Typography.Text></h2>
      <Space wrap><Typography.Text type="secondary">品牌与型号 · 保持原样</Typography.Text>{draft.protected_tags.map(tag => <Tag key={tag} icon={<LockOutlined />}>{tag}</Tag>)}</Space>
      {editing ? <label className={styles.field}>本篇语义标签（空格分隔，可整段粘贴）<Input.TextArea aria-label="本篇语义标签" rows={2} value={input} onChange={event => {
        setInput(event.target.value); update({ tags: parseSemanticTags(event.target.value, draft.protected_tags), hashtags_confirmed: false })
      }} /></label> : null}
      <Space wrap className={styles.difference ?? ''}>{semantic.length ? semantic.map(tag => <Tag key={tag} closable={editing} onClose={event => {
        event.preventDefault(); const tags = semantic.filter(value => value !== tag); setInput(tags.join(' ')); update({ tags: [...draft.protected_tags, ...tags], hashtags_confirmed: false })
      }}>{source.includes(tag) ? '' : '本篇新增 '}{tag}</Tag>) : '无语义标签'}</Space>
      <div className={styles.difference}>{source.filter(tag => !semantic.includes(tag)).map(tag => <Tag key={tag}>原帖未采用 {tag}</Tag>)}</div>
      {(source.length > 0 || semantic.length > 0) && (editing ? <Checkbox checked={draft.hashtags_confirmed} onChange={event => update({ hashtags_confirmed: event.target.checked })}>我已确认本篇使用的话题标签</Checkbox> : <p>{draft.hashtags_confirmed ? '已人工确认选择' : '语义标签尚待人工确认'}</p>)}
      {source.length > 0 && !detail.read_only && detail.hashtag_suggestions_enabled !== false && <p><PaidActionButton label="生成德语标签建议" amount="按实际用量计费" {...(!editing ? { disabledReason: '请先进入编辑德语' } : {})} loading={busy} onClick={() => void suggest()} /></p>}
      {error && <Alert type="warning" title="建议暂时不可用，仍可手动编辑" />}
      {suggestion && <Collapse items={[{ key: 'suggestions', label: '德语标签建议与采样依据', children: <>
        <p>{suggestion.notice}</p><p>候选生成：<ShanghaiTime at={suggestion.generated_at} />（不是热度采样时间）</p>
        {suggestion.sampling?.status !== 'sampled' && <p>采样未完整取得：{suggestion.sampling?.reason || '可继续按语义手动选择'}</p>}
        {suggestion.groups.filter(group => !group.protected).map(group => <div key={group.source_tag}><h3>{group.source_tag}</h3>{group.candidates.map(candidate => <div key={candidate.tag} className={styles.candidate}>
          <Checkbox disabled={!editing} checked={selected.includes(candidate.tag)} onChange={event => setSelected(event.target.checked ? [...new Set([...selected, candidate.tag])] : selected.filter(tag => tag !== candidate.tag))}>{candidate.tag}</Checkbox>
          {Object.entries(metrics).map(([metric, label]) => { const signal = candidate.signals[metric]; return <div key={metric} className={styles.help}>{label}：{signal ? <>{signal.value} · {signal.geo === 'DE' ? '德国' : '全球'} · <ShanghaiTime at={signal.sampled_at} /> · {signal.source}
            {metric === 'trend_score' && <> · 比较组 {signal.comparison_group ?? '未提供'} · 采样批次 {signal.sample_batch ?? '未提供'} · 时段 {signal.time_range ?? '未提供'}</>}
            {!candidate.current_signals?.[metric] && '（已过期或不可跨批比较）'}</> : '未采样，不能当作 0'}</div> })}
        </div>)}</div>)}
        <Button disabled={!editing || suggestion.source_text_sha256 !== detail.text.source_text_sha256} onClick={() => { const tags = parseSemanticTags(selected.join(' '), draft.protected_tags); setInput(tags.filter(tag => !draft.protected_tags.includes(tag)).join(' ')); update({ tags, hashtags_confirmed: false }) }}>采用勾选到编辑区</Button>
        <span className={styles.help}> 勾选不会自动生效，按这个按钮才替换编辑区的标签。</span>
      </> }]} />}
      {draft.platform === 'instagram' && draft.tags.length >= 27 && <p className={styles.help}>接近 30 个上限。保存不会替你删标签，超出的部分要自己取舍。</p>}
    </section>
    <section className={styles.section} aria-label="链接与引导"><h2>{draft.platform === 'instagram' ? 'Instagram 主页引导' : 'Facebook 德语落地页'}</h2>
      {!draft.links.length && <p className={styles.help}>原帖没有链接。</p>}
      {draft.links.map((link, index) => <div key={index} className={styles.link}>
        <p>链接 {index + 1} · 原文：{link.source_url ? <a href={link.source_url.startsWith('www.') ? 'https://' + link.source_url : link.source_url} target="_blank" rel="noopener noreferrer">{link.source_url}</a> : '人工添加'}</p>
        {draft.platform === 'facebook' && <>
          {link.mapped_url && <p className={styles.help}>已配置的映射（只读）：{link.mapped_url}</p>}
          {editing ? <label className={styles.field}>本篇德语落地页<Input aria-label={`链接 ${index + 1} 德语落地页`} type="url" value={link.target_url} onChange={event => linkUpdate(index, { target_url: event.target.value, confirmed: false })} /></label> : <p>{link.target_url || '尚未填写德语落地页'}</p>}
          <Space wrap>{canOpen(link.target_url) && <a href={link.target_url} target="_blank" rel="noopener noreferrer">打开检查 ↗</a>}{editing ? <Checkbox checked={link.confirmed} onChange={event => linkUpdate(index, { confirmed: event.target.checked })}>我已确认落地页适用于德国站</Checkbox> : <span>{link.confirmed ? '本篇链接已确认' : '待人工确认'}</span>}</Space>
          {editing && <p><Button onMouseDown={event => event.preventDefault()} onClick={() => onInsert(index)}>将链接 {index + 1} 插入正文光标处</Button></p>}
        </>}
      </div>)}
      {draft.platform === 'instagram' ? <>
        <p className={styles.help}>原文链接从发布文案中移除，使用引导话术前往主页。</p>
        {editing ? <><label className={styles.field}>常用引导话术<Select aria-label="常用引导话术" value={draft.cta_presets.includes(draft.ig_cta) ? draft.ig_cta : draft.ig_cta ? '__custom__' : ''} onChange={value => { if (value !== '__custom__') update({ ig_cta: value }) }} options={[{ value: '', label: '不添加' }, ...draft.cta_presets.map(value => ({ value, label: value })), { value: '__custom__', label: '自定义（下方输入）' }]} /></label><label className={styles.field}>自定义 bio 引导<Input value={draft.ig_cta} onChange={event => update({ ig_cta: event.target.value })} aria-label="自定义 bio 引导" /></label></> : <p>{draft.ig_cta || '未添加引导话术'}</p>}
        <p className={styles.help}>当前 bio（只读）：{canOpen(draft.ig_bio_url) ? <a href={draft.ig_bio_url} target="_blank" rel="noopener noreferrer">{draft.ig_bio_url}</a> : '未配置'}</p>
      </> : draft.links.length > 0 ? <p className={styles.help}>没有插入正文的链接会放在文末。</p> : null}
    </section>
  </div>
}
