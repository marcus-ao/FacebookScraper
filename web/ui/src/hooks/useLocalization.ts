import { useEffect, useMemo, useState } from 'react'
import { App } from 'antd'
import type { CheckResult, LocalizationDraft, TaskDetail } from '@/types/domain'
import { buildMarks, charLength } from '@/lib/marks'
import { checkLocalization, saveLocalization } from '@/services/localization'
import { editableFields, recoverDraft } from '@/features/localization/model'
import { useUnsavedChangesGuard } from './useUnsavedChangesGuard'

export function useLocalization(detail: TaskDetail, apply: (detail: TaskDetail) => void, refresh: () => Promise<TaskDetail>) {
  const { message } = App.useApp()
  const [draft, setDraft] = useState<LocalizationDraft | null>(null)
  const [validation, setValidation] = useState<{ signature: string; result: CheckResult } | null>(null)
  const [checking, setChecking] = useState(false)
  const [saving, setSaving] = useState(false)
  const [recovering, setRecovering] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const [active, setActive] = useState(-1)
  const editing = draft !== null
  const shown = draft ?? detail.localization
  const signature = JSON.stringify(shown)
  const dirty = editing && JSON.stringify(editableFields(shown)) !== JSON.stringify(editableFields(detail.localization))
  useUnsavedChangesGuard({ dirty, message: 'detail' })
  const marks = useMemo(() => buildMarks(detail.body_highlights, detail.body_risks), [detail])
  const live = validation?.signature === signature ? validation.result : null
  const shownMarks = live ? buildMarks(live.highlights, detail.body_risks) : marks
  useEffect(() => {
    if (!draft) { setChecking(false); return }
    let current = true
    setChecking(true)
    const timer = window.setTimeout(() => {
      void checkLocalization(detail.id, draft).then(result => {
        if (current) setValidation({ signature, result })
      }).catch(() => { /* 校验不可用不影响人工保存。 */ }).finally(() => { if (current) setChecking(false) })
    }, 250)
    return () => { current = false; window.clearTimeout(timer) }
  }, [detail.id, signature, editing])
  const discard = () => { setDraft(null); setError(null); setValidation(null); setActive(-1) }
  const save = async () => {
    if (!draft || saving) return
    setSaving(true); setError(null)
    try { apply(await saveLocalization(detail, draft)); discard(); void message.success('人工修改已保存') }
    catch (cause) { setError(cause) } finally { setSaving(false) }
  }
  const recover = async () => {
    setRecovering(true)
    try { const latest = await refresh(); if (draft) setDraft(recoverDraft(detail, latest, draft)); setError(null) }
    catch (cause) { setError(cause) } finally { setRecovering(false) }
  }
  const tail = shown.platform === 'instagram' ? shown.ig_cta : shown.links.map(link => link.target_url).filter(url => /^https?:\/\//.test(url)).join('\n')
  const count = !editing ? detail.localization_validation.char_count : live?.caption_length
    ?? charLength([shown.body_de.trim(), tail.trim(), shown.tags.join(' ')].filter(Boolean).join('\n\n'))
  // ⛔ 只用服务端算好的成品文案。前端近似值可以拿来显示"约 N 字符"，但复制出去的东西
  // 会被直接贴进 Business Suite——和实际发布内容不一致比没有这个按钮更糟。
  const caption = editing ? live?.caption : detail.localization_validation.caption
  return { draft, shown, editing, dirty, checking, saving, error, recovering, marks, shownMarks, active, setActive,
    setDraft, start: () => { setDraft(structuredClone(detail.localization)); setActive(-1) }, discard, save, recover,
    jump: (delta: number) => { if (shownMarks.length) setActive(old => (old + delta + shownMarks.length) % shownMarks.length) },
    count, caption, approximate: editing && !live, issues: live?.issues ?? detail.localization_validation.issues,
    warnings: live?.warnings ?? detail.localization_validation.warnings }
}
