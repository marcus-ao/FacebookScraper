import type { CalendarPayload } from '@/types/domain'
import { jsonBody, request } from './http'
export const getCalendar = () => request<CalendarPayload>('/api/calendar')
export const refreshCalendar = () => request<CalendarPayload>('/api/calendar/refresh', jsonBody({}))
