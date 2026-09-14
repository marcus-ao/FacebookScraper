import type { FeishuDelivery, RuntimeProcessing, RuntimeSnapshot } from '@/types/domain'
import { jsonBody, request } from './http'
export const getRuntime = () => request<RuntimeSnapshot>('/api/runtime')
export const resolveNotification = (item: FeishuDelivery, action: 'delivered' | 'not_delivered', messageId = '') => request<unknown>(`/api/runtime/notifications/${encodeURIComponent(item.delivery_id)}/resolve`, jsonBody({ action, version: item.version, message_id: messageId }))
export const recoverProcessing = (batch: RuntimeProcessing) => request<unknown>('/api/runtime/processing/recover', jsonBody({ batch_id: batch.batch_id, version: batch.state_revision, outputs_reviewed: true }))
