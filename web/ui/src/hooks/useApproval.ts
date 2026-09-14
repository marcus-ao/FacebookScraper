import { useEffect, useRef, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import type { TaskDetail } from '@/types/domain'
import { approvalBody, approvalOptions, approve, isScheduledReceipt, reconcilePublication } from '@/services/approval'
import type { ApprovalBody } from '@/services/approval'
import { isApiError, isConflict } from '@/services/http'
import { approvalDisabledReason, seedScheduleTime } from '@/lib/action-reasons'
import { berlinInput } from '@/lib/format'

export function useApproval(detail: TaskDetail, editing: boolean, refresh: () => Promise<TaskDetail>) {
  const options = useQuery({ queryKey: ['approval-options', detail.id, detail.text.source_text_sha256, detail.text.human_revision, detail.review.revision, detail.localization.revision], queryFn: () => approvalOptions(detail.id), enabled: !detail.read_only })
  const [when, setWhenState] = useState(''), [busy, setBusy] = useState(false), [error, setError] = useState<unknown>(null), [confirmed, setConfirmed] = useState(false)
  const [snapshot, setSnapshot] = useState<{ detail: TaskDetail; body: ApprovalBody } | null>(null)
  // 她动过这个字段之后就不再自动填了（判据在 lib/action-reasons.ts 的 seedScheduleTime）。
  // 清空也算动过 —— 那是一个决定，不是一个待补的空值。
  const touched = useRef(false)
  const setWhen = (value: string) => { touched.current = true; setWhenState(value) }
  useEffect(() => { touched.current = false; setWhenState('') }, [detail.id])
  useEffect(() => {
    const seed = seedScheduleTime({ current: when, touched: touched.current, earliest: options.data?.earliest, scheduledAt: detail.schedule?.at })
    if (seed) setWhenState(berlinInput(seed))
  }, [options.data, when, detail.schedule, detail.id])
  const eligible = ['pending_review', 'edited'].includes(detail.status)
  const reason = approvalDisabledReason({ editing, busy, fetching: options.isFetching, eligible, status: detail.status,
    optionsFailed: options.isError, available: !!options.data?.available, when })
  const submit = async () => {
    if (!snapshot || busy) return
    setBusy(true); setError(null)
    try {
      const result = await approve(snapshot.detail.id, snapshot.body)
      if (!isScheduledReceipt(result)) throw new Error('unconfirmed')
      setSnapshot(null); setConfirmed(true); await refresh()
    } catch (cause) {
      setError(cause); setSnapshot(null)
      // 回执不是严格 scheduled 时后端已经留下发布尝试。不重新读这一篇，
      // detail.publication / detail.status 还停在提交前，DecisionPanel 的
      // “核对并补齐本地回执”就不出现，运营只能刷新整页才能收尾。
      // 重读失败不覆盖上面的原始错误。
      await refresh().catch(() => undefined)
      await options.refetch()
    }
    finally { setBusy(false) }
  }
  const recover = async () => { setBusy(true); setError(null); try { await reconcilePublication(detail.id); await refresh(); await options.refetch() } catch (cause) { setError(cause) } finally { setBusy(false) } }
  const suggestions = isApiError(error) && error.payload && typeof error.payload === 'object' && 'suggestions' in error.payload && Array.isArray(error.payload.suggestions) ? error.payload.suggestions.filter((value): value is string => typeof value === 'string') : []
  const errorMessage = isApiError(error) && /夏令时/.test(error.message) ? '这个柏林时刻在夏令时切换中不存在或出现两次，请选择其他时刻' : isConflict(error) ? '内容或时刻已变化，请重新核对后再确认' : '排期尚未确认，请核对回执后再处理'
  return { options, when, setWhen, eligible, busy, reason, error, errorMessage, suggestions, confirmed, snapshot, setSnapshot, submit, recover,
    open: () => { if (!reason && options.data) setSnapshot({ detail: structuredClone(detail), body: approvalBody(detail, when, options.data) }) },
    refresh: async () => { await refresh(); await options.refetch(); setError(null) } }
}
export type ApprovalController = ReturnType<typeof useApproval>
