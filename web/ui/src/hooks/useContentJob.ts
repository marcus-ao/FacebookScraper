import { useEffect, useRef, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import type { ContentJob } from '@/types/domain'
import { getContentJob, jobRunning, recoverContentJob } from '@/services/jobs'
import type { JobFamily } from '@/services/jobs'

export function useContentJob(family: JobFamily, initial: ContentJob | null | undefined, onSettled: (job: ContentJob) => void) {
  const [local, setLocal] = useState<ContentJob | null>(null)
  const selected = local ?? initial
  const client = useQueryClient()
  const key = ['content-job', family, selected?.job_id] as const
  const query = useQuery({ queryKey: key, queryFn: () => getContentJob(family, selected!.job_id), enabled: !!selected,
    ...(selected ? { initialData: selected } : {}),
    refetchInterval: state => jobRunning(state.state.data) ? family === 'initial-translation' ? 1500 : 1200 : false })
  const reported = useRef('')
  useEffect(() => {
    const job = query.data
    if (!job || jobRunning(job) || query.isFetching) return
    const identity = job.job_id + ':' + job.status
    if (reported.current === identity) return
    reported.current = identity; onSettled(job)
  }, [query.data, query.isFetching, onSettled])
  return { job: query.data ?? selected, error: query.error,
    accept: (job: ContentJob) => { setLocal(job); client.setQueryData(['content-job', family, job.job_id], job) },
    recover: async () => { if (!query.data) return; const result = await recoverContentJob(query.data); setLocal(result); client.setQueryData(key, result) },
    refresh: () => selected ? query.refetch() : Promise.resolve() }
}
