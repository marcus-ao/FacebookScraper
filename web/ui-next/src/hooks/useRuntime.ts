import { useQuery } from '@tanstack/react-query'
import { getRuntime } from '@/services/runtime'
export const useRuntime = () => useQuery({ queryKey: ['runtime'], queryFn: getRuntime, refetchOnMount: false, refetchInterval: 30000 })
