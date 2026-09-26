import { useEffect, useRef, useImperativeHandle } from 'react'
import type { Ref } from 'react'
import { Button, Space, Typography } from 'antd'
import { segment } from '@/lib/marks'
import type { Mark, RiskScan } from '@/types/domain'
import { displayLinks, storeLinks } from './model'
import styles from './TextWorkspace.module.css'

export interface TextWorkspaceHandle { insertAtCursor: (text: string) => void }
interface Props {
  en: string; de: string; marks: readonly Mark[]; liveMarks: readonly Mark[]; active: number;
  editing: boolean; checking: boolean; human: boolean; scan: RiskScan;
  onChange: (text: string) => void; onSelect: (index: number) => void; onJump: (delta: number) => void;
  onStartEdit?: () => void; onGenerateDraft?: () => void;
  ref?: Ref<TextWorkspaceHandle>;
}

export function TextWorkspace({ en, de, marks, liveMarks, active, editing, checking, human, scan, onChange, onSelect, onJump, onStartEdit, onGenerateDraft, ref }: Props) {
  const english = useRef<HTMLDivElement>(null)
  const german = useRef<HTMLDivElement>(null)
  const mirror = useRef<HTMLDivElement>(null)
  const textarea = useRef<HTMLTextAreaElement>(null)
  const render = (text: string, items: readonly Mark[], side: 'en' | 'de', interactive = true) => segment(text, items, side).map((part, i) =>
    part.type ? <mark key={i} className={`mk mk-${part.type}${active === part.index ? ' mk-active' : ''}`}
      data-mark={part.index} tabIndex={interactive ? 0 : undefined} role={interactive ? 'button' : undefined}
      aria-label={interactive ? `${part.text}：${items[part.index]?.label ?? '查看标记说明'}` : undefined}
      onClick={interactive ? () => onSelect(part.index) : undefined} onKeyDown={interactive ? event => {
        if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); onSelect(part.index) }
      } : undefined}>{displayLinks(part.text)}</mark> : displayLinks(part.text))
  useEffect(() => {
    if (active < 0) return
    for (const root of [english.current, editing ? mirror.current : german.current]) {
      const target = root?.querySelector<HTMLElement>(`[data-mark="${active}"]`)
      if (root && target) root.scrollTop += target.getBoundingClientRect().top - root.getBoundingClientRect().top - root.clientHeight / 2
    }
    if (editing && textarea.current && mirror.current) textarea.current.scrollTop = mirror.current.scrollTop
  }, [active, editing])
  useImperativeHandle(ref, () => ({ insertAtCursor(text) {
    const el = textarea.current
    if (!el) return
    const value = displayLinks(de), insert = displayLinks(text), start = el.selectionStart, end = el.selectionEnd
    onChange(storeLinks(value.slice(0, start) + insert + value.slice(end)))
    requestAnimationFrame(() => { el.focus(); el.setSelectionRange(start + insert.length, start + insert.length) })
  } }), [de, onChange])
  return <section aria-label="正文对照">
    {liveMarks.length > 0 && <div className={styles.controls}>
      <Typography.Text type="secondary">{active < 0 ? `共 ${liveMarks.length} 处标记` : `${active + 1} / ${liveMarks.length}`} · 快捷键 N / P</Typography.Text>
      <Space><Button size="small" disabled={!liveMarks.length} onClick={() => onJump(-1)}>上一处</Button>
        <Button size="small" disabled={!liveMarks.length} onClick={() => onJump(1)}>下一处</Button></Space>
    </div>}
    {scan.status === 'not_scanned' && <p className={styles.scanNote}>尚未扫描；请人工核对原文与德语正文。</p>}
    {scan.status === 'failed' && <p className={styles.scanNote}>风险扫描未完成；仍可人工核对并确认正文。</p>}
    {scan.status === 'stale' && <p className={styles.scanNote}>扫描结果已过期；请按当前原文人工复核。</p>}
    <div className={styles.compare}>
      <section className={styles.pane}><h2>英文原文</h2>
        <div className={styles.prose} ref={english} data-testid="english-prose">{render(en, marks, 'en')}</div></section>
      <section className={styles.pane}><h2>德语文案 <span className={styles.meta}>{editing ? checking ? '正在校验…' : '编辑中' : !de.trim() ? '待补充' : human ? '人工修订' : '机器初译'}</span></h2>
        {editing ? <div className={styles.editor}>
          <div className={`${styles.prose} ${styles.mirror}`} ref={mirror} aria-hidden="true">{render(de, liveMarks, 'de', false)}{'\n'}</div>
          <textarea ref={textarea} className={styles.textarea} aria-label="德语正文" spellCheck={false} value={displayLinks(de)}
            onChange={event => onChange(storeLinks(event.target.value))}
            onScroll={event => { if (mirror.current) mirror.current.scrollTop = event.currentTarget.scrollTop }} />
        </div> : <div className={styles.prose} ref={german} data-testid="german-prose">{de.trim() ? render(de, liveMarks, 'de') : <div className={styles.empty}><p>尚无德语文案</p><Space wrap>
          {onStartEdit && <Button onClick={onStartEdit}>手写德语正文</Button>}
          {onGenerateDraft && <Button type="link" onClick={onGenerateDraft}>生成德语初稿</Button>}
        </Space></div>}</div>}
      </section>
    </div>
    {active >= 0 && liveMarks[active] && <p className={styles.explanation} role="status">{liveMarks[active]?.label}</p>}
  </section>
}

export function BodyReviewActions({ canEdit, hasBody, editing, saving, confirming, confirmed, onEdit, onSave, onDiscard, onConfirm }: {
  canEdit: boolean; hasBody: boolean; editing: boolean; saving: boolean; confirming: boolean; confirmed: boolean;
  onEdit: () => void; onSave: () => void; onDiscard: () => void; onConfirm: (confirmed: boolean) => void;
}) {
  if (editing) return <div className={styles.reviewActions}>
    <Button disabled={saving} onClick={onDiscard}>放弃修改</Button>
    <Button type="primary" disabled={saving} loading={saving} onClick={onSave}>保存修改</Button>
  </div>
  if (!hasBody || saving) return null
  if (confirming) return <p className={styles.confirming} role="status">正在保存正文确认…</p>
  return <div className={styles.reviewActions}>
    {confirmed && <span role="status">当前正文已确认</span>}
    {canEdit && <>
      <Button onClick={onEdit}>编辑德语</Button>
      {confirmed ? <Button onClick={() => onConfirm(false)}>撤销正文确认</Button>
        : <Button type="primary" onClick={() => onConfirm(true)}>确认当前德语正文</Button>}
    </>}
  </div>
}
