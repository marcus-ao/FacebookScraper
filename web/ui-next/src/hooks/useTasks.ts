import { queryOptions, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import type { QueryClient } from '@tanstack/react-query'
import type { ReviewListResponse, TaskDetail } from '@/types/domain'
import { getTask, listHistoryTasks, listReviewTasks } from '@/services/tasks'
import type { HistoryListQuery } from '@/app/search-params'
import { applyDecision } from '@/services/review'
import type { DecisionForm, ReviewContext } from '@/services/review'
import { patchReviewList } from '@/features/post-list/model'

export const REVIEW_KEY = ['tasks', 'review'] as const
export const taskKey = (id: string) => ['task', id] as const
export const reviewListOptions = () => queryOptions({
  queryKey: REVIEW_KEY, queryFn: listReviewTasks,
  refetchOnMount: false, refetchOnReconnect: false,
  // 与旧 App 内存列表相同：一次会话中保留，详情读多久都不因 GC 重拉整表。
  gcTime: Infinity,
})

export const useReviewList = () => useQuery(reviewListOptions())
export const historyListOptions = (filters: HistoryListQuery) => queryOptions({
  queryKey: ['tasks', 'history', filters] as const,
  queryFn: () => listHistoryTasks(filters), refetchOnMount: false,
})
export const useHistoryList = (filters: HistoryListQuery) => useQuery(historyListOptions(filters))

export function cacheTask(client: QueryClient, detail: TaskDetail) {
  client.setQueryData(taskKey(detail.id), detail)
  client.setQueryData<ReviewListResponse>(REVIEW_KEY, old => old ? patchReviewList(old, detail) : old)
}

export function useReviewDecision() {
  const client = useQueryClient()
  const mutation = useMutation({
    mutationFn: ({ context, form }: { context: ReviewContext; form: DecisionForm }) => applyDecision(context, form),
    onSuccess: detail => cacheTask(client, detail),
  })
  const refresh = async (id: string) => {
    const detail = await getTask(id)
    cacheTask(client, detail)
    mutation.reset()
    return detail
  }
  return { ...mutation, refresh }
}
