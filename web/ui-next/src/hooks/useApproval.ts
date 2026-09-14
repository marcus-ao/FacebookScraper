import { useEffect, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import type { TaskDetail } from '@/types/domain'
import { approvalBody, approvalOptions, approve, isScheduledReceipt, reconcilePublication } from '@/services/approval'
import type { ApprovalBody } from '@/services/approval'
import { isApiError, isConflict } from '@/services/http'
import { berlinInput } from '@/lib/format'

export function useApproval(detail: TaskDetail, editing: boolean, refresh: () => Promise<TaskDetail>) {
  const options = useQuery({ queryKey: ['approval-options', detail.id, detail.text.source_text_sha256, detail.text.human_revision, detail.review.revision, detail.localization.revision], queryFn: () => approvalOptions(detail.id), enabled: !detail.read_only })
  const [when, setWhen] = useState(''), [busy, setBusy] = useState(false), [error, setError] = useState<unknown>(null), [confirmed, setConfirmed] = useState(false)
  const [snapshot, setSnapshot] = useState<{ detail: TaskDetail; body: ApprovalBody } | null>(null)
  useEffect(() => { if (!when && options.data?.earliest) setWhen(berlinInput(detail.schedule?.at || options.data.earliest)) }, [options.data, when, detail.schedule])
  const eligible = ['pending_review', 'edited'].includes(detail.status)
  const reason = editing ? '请先保存或放弃正在编辑的文案' : busy ? '正在提交并核验，请等待' : options.isFetching ? '正在核对发布条件' : !eligible ? detail.status === 'scheduled' ? '这篇已有已确认的排期' : '请先恢复审校并准备好内容' : options.isError ? '发布条件读取失败，请重新核对' : !options.data?.available ? '发布条件尚未满足，请查看下方提示' : !when ? '请先填写柏林发布时间' : ''
  const submit = async () => {
    if (!snapshot || busy) return
    setBusy(true); setError(null)
    try {
      const result = await approve(snapshot.detail.id, snapshot.body)
      if (!isScheduledReceipt(result)) throw new Error('unconfirmed')
      setSnapshot(null); setConfirmed(true); await refresh()
    } catch (cause) { setError(cause); setSnapshot(null); await options.refetch() }
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
