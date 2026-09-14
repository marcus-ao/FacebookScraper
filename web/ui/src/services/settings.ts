import type { EditableSettings, OperatingSettings } from '@/types/domain'
import { putBody, request } from './http'
export const getSettings = () => request<OperatingSettings>('/api/settings')
export const saveSettings = (values: EditableSettings, version: string) => request<OperatingSettings>('/api/settings', putBody({ values, version }))
