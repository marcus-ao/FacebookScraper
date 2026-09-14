import { useEffect, useRef, useImperativeHandle } from 'react'
import type { Ref } from 'react'
import { Alert, Button, Space, Typography } from 'antd'
import { segment } from '@/lib/marks'
import type { Mark, RiskScan } from '@/types/domain'
import { displayLinks, storeLinks } from './model'
import styles from './TextWorkspace.module.css'

export interface TextWorkspaceHandle { insertAtCursor: (text: string) => void }
interface Props {
  en: string; de: string; marks: readonly Mark[]; liveMarks: readonly Mark[]; active: number;
  editing: boolean; checking: boolean; human: boolean; scan: RiskScan;
  onChange: (text: string) => void; onSelect: (index: number) => void; onJump: (delta: number) => void;
  ref?: Ref<TextWorkspaceHandle>;
}

export function TextWorkspace({ en, de, marks, liveMarks, active, editing, checking, human, scan, onChange, onSelect, onJump, ref }: Props) {
  const english = useRef<HTMLDivElement>(null)
  const german = useRef<HTMLDivElement>(null)
  const mirror = useRef<HTMLDivElement>(null)
  const textarea = useRef<HTMLTextAreaElement>(null)
  const render = (text: string, items: readonly Mark[], side: 'en' | 'de') => segment(text, items, side).map((part, i) =>
    part.type ? <mark key={i} className={`mk mk-${part.type}${active === part.index ? ' mk-active' : ''}`}
      data-mark={part.index} onClick={() => onSelect(part.index)}>{displayLinks(part.text)}</mark> : displayLinks(part.text))
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
    <div className={styles.controls}>
      <Typography.Text type="secondary">{active < 0 ? `共 ${liveMarks.length} 处标记` : `${active + 1} / ${liveMarks.length}`} · 快捷键 N / P</Typography.Text>
      <Space><Button size="small" disabled={!liveMarks.length} onClick={() => onJump(-1)}>上一处</Button>
        <Button size="small" disabled={!liveMarks.length} onClick={() => onJump(1)}>下一处</Button></Space>
    </div>
    {(scan.status === 'failed' || scan.status === 'stale') && <Alert type="warning" showIcon title={scan.status === 'failed' ? '风险扫描失败，请人工审校' : '风险扫描已失效，请重新复核原文'} />}
    <div className={styles.compare}>
      <section className={styles.pane}><h2>英文原文 <span className={styles.meta}>{scan.status === 'not_scanned' ? '尚未扫描，请人工检查' : scan.status === 'completed' ? '扫描完成，仍需人工复核' : ''}</span></h2>
        <div className={styles.prose} ref={english} data-testid="english-prose">{render(en, marks, 'en')}</div></section>
      <section className={styles.pane}><h2>德语文案 <span className={styles.meta}>{editing ? checking ? '正在校验…' : '编辑中' : human ? '人工已复核' : '机器初译'}</span></h2>
        {editing ? <div className={styles.editor}>
          <div className={`${styles.prose} ${styles.mirror}`} ref={mirror} aria-hidden="true">{render(de, liveMarks, 'de')}{'\n'}</div>
          <textarea ref={textarea} className={styles.textarea} aria-label="德语正文" spellCheck={false} value={displayLinks(de)}
            onChange={event => onChange(storeLinks(event.target.value))}
            onScroll={event => { if (mirror.current) mirror.current.scrollTop = event.currentTarget.scrollTop }} />
        </div> : <div className={styles.prose} ref={german} data-testid="german-prose">{de ? render(de, liveMarks, 'de') : <Typography.Text type="secondary">尚无德语文案</Typography.Text>}</div>}
      </section>
    </div>
    {active >= 0 && liveMarks[active] && <p className={styles.explanation} role="status">{liveMarks[active]?.label}</p>}
  </section>
}
