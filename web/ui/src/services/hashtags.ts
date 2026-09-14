import type { TaskDetail } from '@/types/domain'
import { idPath, jsonBody, request } from './http'

export interface HashtagSignal { value: number; geo: string; sampled_at: string; source: string; comparison_group?: string; sample_batch?: string; time_range?: string }
export interface HashtagSuggestions {
  source_text_sha256: string; generated_at: string; notice: string; selected: string[];
  sampling?: { status?: string; reason?: string; sources?: Record<string, unknown> };
  groups: { source_tag: string; protected: boolean; candidates: { tag: string; signals: Record<string, HashtagSignal>; current_signals?: Record<string, HashtagSignal> }[] }[];
}
export const suggestHashtags = (detail: TaskDetail) => request<HashtagSuggestions>(`/api/hashtags/task/${idPath(detail.id)}`,
  jsonBody({ source_text_sha256: detail.text.source_text_sha256, human_revision: detail.text.human_revision, review_revision: detail.review.revision }))
