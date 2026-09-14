import type { CheckResult, LocalizationDraft, TaskDetail } from '@/types/domain'
import { idPath, jsonBody, putBody, request } from './http'
import { localizationBody } from '@/features/localization/model'

export const checkLocalization = (id: string, draft: LocalizationDraft) => request<CheckResult>(
  `/api/tasks/${idPath(id)}/check`, jsonBody({ text_de: draft.body_de, body_only: true, localization: draft }))
export const saveLocalization = (detail: TaskDetail, draft: LocalizationDraft) => request<TaskDetail>(
  `/api/tasks/${idPath(detail.id)}/localization`, putBody(localizationBody(detail, draft)))
