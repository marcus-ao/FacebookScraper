import type { ContentJob, ImageVersion, InitialTranslationCapabilities, PromptTemplate, RefinementCapabilities, TaskDetail } from '@/types/domain'
import { idPath, jsonBody, request } from './http'
export type JobFamily = 'initial-translation' | 'refinements'
export const initialCapabilities = (id: string) => request<InitialTranslationCapabilities>(`/api/initial-translation/task/${idPath(id)}`)
export const refinementCapabilities = (id: string) => request<RefinementCapabilities>(`/api/refinements/task/${idPath(id)}`)
export const getContentJob = (family: JobFamily, id: string) => request<ContentJob>(`/api/${family}/jobs/${encodeURIComponent(id)}`)
export const jobVersions = (detail: TaskDetail) => ({ source_text_sha256: detail.text.source_text_sha256, human_revision: detail.text.human_revision, review_revision: detail.review.revision })
export const initialTranslate = (detail: TaskDetail, capability: InitialTranslationCapabilities, consent: boolean) => request<ContentJob>(`/api/initial-translation/task/${idPath(detail.id)}`, jsonBody({ consent, source_fingerprint: capability.source_fingerprint, ...jobVersions(detail) }))
export type RefineKind = 'text' | 'image' | 'suggest'
export const refine = (detail: TaskDetail, kind: RefineKind, instruction: string, mediaIndex: number, body?: string) => request<ContentJob>(`/api/refinements/task/${idPath(detail.id)}`, jsonBody({ kind, instruction, media_index: kind === 'image' ? mediaIndex : null, ...(kind === 'suggest' ? { body_de: body } : {}), ...jobVersions(detail) }))
export const recoverContentJob = (job: ContentJob) => request<ContentJob>(`/api/content-jobs/${encodeURIComponent(job.job_id)}/recover`, jsonBody({ expected_updated_at: job.recorded_at }))
export const getTemplate = (kind: 'text' | 'image') => request<PromptTemplate>(`/api/templates/${kind}`)
export const jobRunning = (job: ContentJob | null | undefined) => !!job && ['pending', 'running'].includes(job.status)
/** 换回历史版本：零模型调用、零费用，所以不占优化次数。 */
export const selectImageVersion = (detail: TaskDetail, mediaIndex: number, outPath: string) =>
  request<ImageVersion>(`/api/image-versions/task/${idPath(detail.id)}`, jsonBody({ media_index: mediaIndex, out_path: outPath, source_text_sha256: detail.text.source_text_sha256, review_revision: detail.review.revision }))
/** 上传自己处理好的图片替换这一张；状态不变，这篇继续走系统排期。 */
export const uploadImage = (detail: TaskDetail, mediaIndex: number, base64: string, filename: string) =>
  request<TaskDetail>(`/api/tasks/${idPath(detail.id)}/image/${mediaIndex}/upload`, jsonBody({ image_base64: base64, filename, source_text_sha256: detail.text.source_text_sha256, review_revision: detail.review.revision }))
