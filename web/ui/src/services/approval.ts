import type { ApprovalOptions, ApproveReceipt, PublishOperation, TaskDetail } from '@/types/domain'
import { idPath, jsonBody, request } from './http'
export const approvalOptions = (id: string) => request<ApprovalOptions>(`/api/tasks/${idPath(id)}/approval-options`)
export const approvalBody = (detail: TaskDetail, scheduled_at: string, options: ApprovalOptions) => ({ scheduled_at,
  source_text_sha256: detail.text.source_text_sha256, human_revision: detail.text.human_revision,
  review_revision: detail.review.revision, content_fingerprint: options.fingerprint,
  publish_target: options.preview?.target })
export type ApprovalBody = ReturnType<typeof approvalBody>
/** 提交立刻返回操作编号；浏览器那几分钟在请求之外跑，页面轮询它。 */
export const approve = (id: string, body: ApprovalBody) => request<PublishOperation>(`/api/tasks/${idPath(id)}/approve`, jsonBody(body))
export const publishOperation = (operationId: string) => request<PublishOperation>(`/api/publish-operations/${operationId}`)
export const reconcilePublication = (id: string) => request<unknown>(`/api/tasks/${idPath(id)}/publication/reconcile`, jsonBody({}))
export const isScheduledReceipt = (receipt: ApproveReceipt) => receipt.ok === true && receipt.status === 'scheduled'

export const lockContent = (id: string, body: { source_text_sha256: string; review_revision: string | null; content_fingerprint: string }) =>
  request<TaskDetail>(`/api/tasks/${idPath(id)}/content-lock`, jsonBody(body))
export const unlockContent = (id: string, body: { source_text_sha256: string; review_revision: string | null }) =>
  request<TaskDetail>(`/api/tasks/${idPath(id)}/content-lock`, { ...jsonBody(body), method: 'DELETE' })
/** 人已在 Business Suite 手删之后来登记；系统实时读整月核实，不自己去删。 */
export const unschedulePublication = (id: string, reason: string) =>
  request<unknown>(`/api/tasks/${idPath(id)}/publication/unschedule`, jsonBody({ confirmed_deleted: true, reason }))
