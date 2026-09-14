import { useQuery, useQueryClient } from '@tanstack/react-query'
import { getTask } from '@/services/tasks'
import { cacheTask, taskKey } from './useTasks'
import type { TaskDetail } from '@/types/domain'

export function useTaskDetail(id: string) {
  const client = useQueryClient()
  const query = useQuery({ queryKey: taskKey(id), queryFn: () => getTask(id) })
  return { ...query, apply: (detail: TaskDetail) => cacheTask(client, detail),
    refresh: async () => { const detail = await getTask(id); cacheTask(client, detail); return detail } }
}
