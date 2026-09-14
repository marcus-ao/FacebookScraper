import type { HistoryListResponse, ReviewListResponse, TaskDetail } from '@/types/domain'
import type { HistoryListQuery } from '@/app/search-params'
import { idPath, queryString, request } from './http'

export const listReviewTasks = () => request<ReviewListResponse>('/api/tasks')
export const getTask = (id: string) => request<TaskDetail>(`/api/tasks/${idPath(id)}`)
export const listHistoryTasks = (filters: HistoryListQuery) =>
  request<HistoryListResponse>(`/api/tasks?${queryString({scope:'history', ...filters})}`)
