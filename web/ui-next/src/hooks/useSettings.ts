import { useQuery } from '@tanstack/react-query'
import { getSettings } from '@/services/settings'
export const useSettings = () => useQuery({ queryKey: ['settings'], queryFn: getSettings })
