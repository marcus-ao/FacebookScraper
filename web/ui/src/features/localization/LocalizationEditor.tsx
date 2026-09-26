import { useEffect, useState } from 'react'
import { Alert, Button, Checkbox, Collapse, Input, Select, Space, Tag, Typography } from 'antd'
import type { LocalizationDraft, TaskDetail } from '@/types/domain'
import { suggestHashtags } from '@/services/hashtags'
import type { HashtagSuggestions } from '@/services/hashtags'
import { PaidActionButton } from '@/components/PaidActionButton'
import { SourceConsentDialog, sourceConsentRequired } from '@/components/SourceConsentDialog'
import { bioTargetWarning, canEditTask, completeTagAndLinkReview } from './model'
import styles from './LocalizationEditor.module.css'

export function parsePublicationTags(value: string) {
  return [...new Set(value.split(/[\s,，]+/u).filter(Boolean).map(tag => tag.startsWith('#') ? tag : '#' + tag))]
}
const canOpen = (value: string) => /^https?:\/\/[^\s]+$/i.test(value)

export function LocalizationEditor({ detail, draft, editing, saving, onChange, onComplete, onSave, onDiscard, onInsert }: { detail: TaskDetail; draft: LocalizationDraft; editing: boolean; saving: boolean; onChange: (draft: LocalizationDraft) => void; onComplete: (draft: LocalizationDraft) => void; onSave: () => void; onDiscard: () => void; onInsert: (index: number) => void }) {
  const chosen = draft.tags
  const source = draft.source_tags
  const [input, setInput] = useState(chosen.join(' '))
  const [suggestion, setSuggestion] = useState<HashtagSuggestions | null>(null)
  const [selected, setSelected] = useState<string[]>([])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(false)
  const [consentOpen, setConsentOpen] = useState(false)
  useEffect(() => { setInput(chosen.join(' ')) }, [editing])
  const update = (fields: Partial<LocalizationDraft>) => onChange({ ...draft, ...fields })
  const needsTagDecision = source.length > 0 || chosen.length > 0
  const needsLinkDecision = draft.links.length > 0 || !!draft.ig_cta.trim()
  const decisionsSaved = (!needsTagDecision || draft.hashtags_confirmed) && (!needsLinkDecision || draft.links_confirmed)
  const missingTarget = draft.platform === 'facebook' && draft.links.some(link => !canOpen(link.target_url))
  const suggest = async () => {
    setBusy(true); setError(false)
    try { const result = await suggestHashtags(detail); setSuggestion(result); setSelected(result.selected) }
    catch { setError(true) } finally { setBusy(false) }
  }
  const linkUpdate = (index: number, fields: Partial<LocalizationDraft['links'][number]>) => update({ links: draft.links.map((link, i) => i === index ? { ...link, ...fields } : link), links_confirmed: false })
  return <div className={styles.grid}>
    <section className={styles.section} aria-label="话题标签选择"><h2>话题标签 <Typography.Text type={draft.platform === 'instagram' && draft.tags.length >= 27 ? 'warning' : 'secondary'}>{draft.tags.length}{draft.platform === 'instagram' ? ' / 30' : ''} 个</Typography.Text></h2>
      {editing ? <label className={styles.field}>本篇拟发布标签（空格分隔，可整段粘贴）<Input.TextArea aria-label="本篇拟发布标签" rows={2} value={input} onChange={event => {
        setInput(event.target.value); update({ tags: parsePublicationTags(event.target.value), hashtags_confirmed: false })
      }} /></label> : null}
      <Space wrap className={styles.difference ?? ''}>{chosen.length ? chosen.map(tag => <Tag key={tag} closable={editing} onClose={event => {
        event.preventDefault(); const tags = chosen.filter(value => value !== tag); setInput(tags.join(' ')); update({ tags, hashtags_confirmed: false })
      }}>{tag}</Tag>) : '本篇不使用话题标签'}</Space>
      {source.some(tag => !chosen.includes(tag)) || chosen.some(tag => !source.includes(tag)) ? <div className={styles.difference}>与原帖不同：{source.filter(tag => !chosen.includes(tag)).map(tag => <Tag key={tag}>未采用 {tag}</Tag>)}{chosen.filter(tag => !source.includes(tag)).map(tag => <Tag key={tag}>新增 {tag}</Tag>)}</div> : null}
      {source.length > 0 && !detail.read_only && detail.hashtag_suggestions_enabled !== false && <p><PaidActionButton label="生成德语标签建议" {...(!editing ? { disabledReason: '请先进入编辑' } : {})} loading={busy}
        onClick={() => sourceConsentRequired(detail) ? setConsentOpen(true) : void suggest()} /></p>}
      {error && <Alert type="warning" title="建议暂时不可用，仍可手动编辑" />}
      {suggestion && <Collapse items={[{ key: 'suggestions', label: '德语标签建议（可选）', children: <>
        {suggestion.groups.filter(group => !group.protected).map(group => <div key={group.source_tag}>
          <h3>原帖 {group.source_tag} 的德语候选</h3>
          {group.candidates.map(candidate => <div key={candidate.tag} className={styles.candidate}>
            <Checkbox disabled={!editing} checked={selected.includes(candidate.tag)} onChange={event => setSelected(event.target.checked ? [...new Set([...selected, candidate.tag])] : selected.filter(tag => tag !== candidate.tag))}>{candidate.tag}</Checkbox>
          </div>)}
        </div>)}
        <Button disabled={!editing || suggestion.source_text_sha256 !== detail.text.source_text_sha256} onClick={() => { const tags = parsePublicationTags(selected.join(' ')); setInput(tags.join(' ')); update({ tags, hashtags_confirmed: false }) }}>采用所选建议</Button>
        <span className={styles.help}> 建议只供参考，采用后仍可任意修改或删除标签。</span>
      </> }]} />}
      {draft.platform === 'instagram' && draft.tags.length >= 27 && <p className={styles.help}>接近 30 个上限。保存不会替你删标签，超出的部分要自己取舍。</p>}
    </section>
    <section className={styles.section} aria-label="链接与引导"><h2>{draft.platform === 'instagram' ? 'Instagram 主页引导' : 'Facebook 德语落地页'}</h2>
      {!draft.links.length && <p className={styles.help}>原帖没有链接。</p>}
      {draft.links.map((link, index) => <div key={index} className={styles.link}>
        <p>链接 {index + 1} · 原文：{link.source_url ? <a href={link.source_url.startsWith('www.') ? 'https://' + link.source_url : link.source_url} target="_blank" rel="noopener noreferrer">{link.source_url}</a> : '人工添加'}</p>
        {draft.platform === 'facebook' && <>
          {editing ? <label className={styles.field}>本篇德语落地页<Input aria-label={`链接 ${index + 1} 德语落地页`} type="url" value={link.target_url} onChange={event => linkUpdate(index, { target_url: event.target.value, confirmed: false })} /></label> : <p>{link.target_url || '尚未填写德语落地页'}</p>}
          {!canOpen(link.target_url) && <p className={styles.help}>链接 {index + 1} 尚未填写德语落地页</p>}
          <Space wrap>{canOpen(link.target_url) && <a href={link.target_url} target="_blank" rel="noopener noreferrer">打开德语落地页 ↗</a>}<span>{link.confirmed ? '本篇链接已确认' : '待人工确认'}</span></Space>
          {editing && <p><Button onMouseDown={event => event.preventDefault()} onClick={() => onInsert(index)}>插入链接 {index + 1}</Button></p>}
        </>}
      </div>)}
      {draft.platform === 'instagram' ? <>
        <p className={styles.help}>原文链接从发布文案中移除，使用引导话术前往主页。</p>
        {editing ? <><label className={styles.field}>常用引导话术<Select aria-label="常用引导话术" value={draft.cta_presets.includes(draft.ig_cta) ? draft.ig_cta : draft.ig_cta ? '__custom__' : ''} onChange={value => { if (value !== '__custom__') update({ ig_cta: value, links_confirmed: false }) }} options={[{ value: '', label: '不添加' }, ...draft.cta_presets.map(value => ({ value, label: value })), { value: '__custom__', label: '自定义（下方输入）' }]} /></label><label className={styles.field}>自定义 bio 引导<Input value={draft.ig_cta} onChange={event => update({ ig_cta: event.target.value, links_confirmed: false })} aria-label="自定义 bio 引导" /></label></> : <p>{draft.ig_cta || '未添加引导话术'}</p>}
        <p className={styles.help}>当前 bio（只读）：{canOpen(draft.ig_bio_url) ? <a href={draft.ig_bio_url} target="_blank" rel="noopener noreferrer">{draft.ig_bio_url}</a> : '未配置'}</p>
        {bioTargetWarning(draft) && <Alert type="warning" showIcon title={bioTargetWarning(draft)} />}
      </> : draft.links.length > 0 ? <p className={styles.help}>没有插入正文的链接会放在文末。</p> : null}
      {draft.platform === 'facebook' && draft.links.length > 0 && <p className={styles.help}>请打开核对每个目标页面；未放入正文的有效链接会在发布文案末尾出现一次。</p>}
    </section>
    <div className={styles.actions}>
      {decisionsSaved && !editing && <span role="status">{missingTarget ? '审核选择已保存，请补全德语落地页' : '标签与链接已审核'}</span>}
      {editing && <><Button disabled={saving} onClick={onDiscard}>放弃修改</Button>
        <Button disabled={saving} onClick={onSave}>仅保存修改</Button></>}
      {canEditTask(detail) && (!decisionsSaved || editing) && <Button type="primary" disabled={saving} loading={saving}
        onClick={() => onComplete(completeTagAndLinkReview(draft))}>完成标签与链接审核</Button>}
    </div>
    <SourceConsentDialog detail={detail} open={consentOpen} busy={busy} onClose={() => setConsentOpen(false)}
      onAccept={() => { setConsentOpen(false); void suggest() }} />
  </div>
}
