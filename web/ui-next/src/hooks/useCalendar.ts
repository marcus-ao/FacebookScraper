import { queryOptions, useQuery } from '@tanstack/react-query'
import { getCalendar } from '@/services/calendar'
export const calendarOptions = () => queryOptions({ queryKey: ['calendar'], queryFn: getCalendar, refetchOnMount: false })
export const useCalendar = () => useQuery(calendarOptions())
