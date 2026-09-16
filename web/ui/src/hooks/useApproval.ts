import { useEffect, useRef, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import type { PublishOperation, TaskDetail } from '@/types/domain'
import { approvalBody, approvalOptions, approve, lockContent, publishOperation, reconcilePublication, unlockContent, unschedulePublication } from '@/services/approval'
import type { ApprovalBody } from '@/services/approval'
import { isApiError, isConflict } from '@/services/http'
import { approvalDisabledReason, seedScheduleTime } from '@/lib/action-reasons'
import { zonedInput } from '@/lib/format'

/** 提交跑在请求之外，页面每两秒问一次进度；关掉再回来也能接着看。 */
const POLL_MS = 2000

export function useApproval(detail: TaskDetail, editing: boolean, refresh: () => Promise<TaskDetail>) {
  const options = useQuery({ queryKey: ['approval-options', detail.id, detail.text.source_text_sha256, detail.text.human_revision, detail.review.revision, detail.localization.revision], queryFn: () => approvalOptions(detail.id), enabled: !detail.read_only })
  const [when, setWhenState] = useState(''), [busy, setBusy] = useState(false), [error, setError] = useState<unknown>(null), [confirmed, setConfirmed] = useState(false)
  const [snapshot, setSnapshot] = useState<{ detail: TaskDetail; body: ApprovalBody } | null>(null)
  const [operation, setOperation] = useState<PublishOperation | null>(null)
  // 人工修改或清空后不再自动预填。
  const touched = useRef(false)
  const setWhen = (value: string) => { touched.current = true; setWhenState(value) }
  useEffect(() => { touched.current = false; setWhenState(''); setOperation(null) }, [detail.id])
  useEffect(() => {
    const seed = seedScheduleTime({ current: when, touched: touched.current, earliest: options.data?.earliest, scheduledAt: detail.schedule?.at })
    if (seed) setWhenState(zonedInput(seed, options.data?.business_timezone))
  }, [options.data, when, detail.schedule, detail.id])

  // ⚠️ 依赖只能是操作编号和状态。把 options / refresh 放进依赖会每次渲染都重建定时器，
  // 于是它在第一次触发之前就被清掉，进度永远停在第一帧。
  const settle = useRef<(next: PublishOperation) => void>(() => undefined)
  settle.current = async (next: PublishOperation) => {
    setOperation(next)
    if (next.status === 'running') return
    setBusy(false)
    if (next.status === 'succeeded') setConfirmed(true)
    await refresh().catch(() => undefined)
    await options.refetch()
  }
  const runningId = operation?.status === 'running' ? operation.operation_id : null
  useEffect(() => {
    if (!runningId) return
    let live = true
    const timer = setInterval(() => {
      void publishOperation(runningId)
        .then(next => { if (live) settle.current(next) })
        .catch(() => undefined)
    }, POLL_MS)
    return () => { live = false; clearInterval(timer) }
  }, [runningId])

  const locked = detail.status === 'content_locked'
  const eligible = locked
  const lockable = ['pending_review', 'edited'].includes(detail.status) && !!options.data?.lockable
  const reason = approvalDisabledReason({ editing, busy, fetching: options.isFetching, eligible, status: detail.status,
    optionsFailed: options.isError, available: !!options.data?.available, when })

  const lock = async () => {
    if (!options.data?.fingerprint || busy) return
    setBusy(true); setError(null)
    try {
      await lockContent(detail.id, { source_text_sha256: detail.text.source_text_sha256,
        review_revision: detail.review.revision, content_fingerprint: options.data.fingerprint })
      await refresh(); await options.refetch()
    } catch (cause) { setError(cause) } finally { setBusy(false) }
  }
  const unlock = async () => {
    if (busy) return
    setBusy(true); setError(null)
    try {
      await unlockContent(detail.id, { source_text_sha256: detail.text.source_text_sha256,
        review_revision: detail.review.revision })
      setWhenState(''); touched.current = false
      await refresh(); await options.refetch()
    } catch (cause) { setError(cause) } finally { setBusy(false) }
  }
  const submit = async () => {
    if (!snapshot || busy) return
    setBusy(true); setError(null)
    try {
      setOperation(await approve(snapshot.detail.id, snapshot.body))
      setSnapshot(null)
      // busy 保持为真，直到轮询看到终态。
    } catch (cause) {
      setError(cause); setSnapshot(null); setBusy(false)
      await refresh().catch(() => undefined)
      await options.refetch()
    }
  }
  const unschedule = async (reasonText: string) => {
    setBusy(true); setError(null)
    try { await unschedulePublication(detail.id, reasonText); await refresh(); await options.refetch() }
    catch (cause) { setError(cause) } finally { setBusy(false) }
  }
  const recover = async () => { setBusy(true); setError(null); try { await reconcilePublication(detail.id); await refresh(); await options.refetch() } catch (cause) { setError(cause) } finally { setBusy(false) } }
  const payload = isApiError(error) && error.payload && typeof error.payload === 'object' ? error.payload as Record<string, unknown> : null
  const fromError = Array.isArray(payload?.suggestions) ? payload.suggestions : []
  const fromOperation = operation?.status === 'failed' && Array.isArray(operation.result?.suggestions) ? operation.result.suggestions : []
  const suggestions = [...fromError, ...fromOperation].filter((value): value is string => typeof value === 'string')
  const errorMessage = operation && operation.status !== 'running' && operation.status !== 'succeeded' ? operation.message
    : isApiError(error) && /夏令时/.test(error.message) ? '这个时刻在夏令时切换中不存在或出现两次，请选择其他时刻'
    : isConflict(error) ? '内容或时刻已变化，请重新核对后再确认' : '排期尚未确认，请核对回执后再处理'
  return { options, when, setWhen, eligible, lockable, locked, busy, reason, error, errorMessage, suggestions, confirmed,
    snapshot, setSnapshot, operation, submit, lock, unlock, unschedule, recover,
    open: () => { if (!reason && options.data) setSnapshot({ detail: structuredClone(detail), body: approvalBody(detail, when, options.data) }) },
    refresh: async () => { await refresh(); await options.refetch(); setError(null); setOperation(null) } }
}
export type ApprovalController = ReturnType<typeof useApproval>
