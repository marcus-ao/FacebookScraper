/** localStorage 只存可丢失的界面偏好；筛选在 URL，草稿在组件，业务状态在服务端。 */

const PREFIX = 'rc.ui.'

export const UI_PREFERENCE_KEYS = {
  siderCollapsed: `${PREFIX}sider-collapsed`,
} as const

export type UiPreferenceKey = (typeof UI_PREFERENCE_KEYS)[keyof typeof UI_PREFERENCE_KEYS]

export interface PreferenceStore {
  getItem(key: string): string | null
  setItem(key: string, value: string): void
}

export function browserStore(): PreferenceStore | null {
  try {
    const store = globalThis.localStorage
    if (!store) return null
    // 存储可能可读却不可写，先探测再使用。
    const probe = `${PREFIX}__probe`
    store.setItem(probe, '1')
    store.removeItem(probe)
    return store
  } catch {
    return null
  }
}

function readRaw(store: PreferenceStore | null, key: UiPreferenceKey): string | null {
  if (!store) return null
  try {
    return store.getItem(key)
  } catch {
    return null
  }
}

function writeRaw(store: PreferenceStore | null, key: UiPreferenceKey, value: string): void {
  if (!store) return
  try {
    store.setItem(key, value)
  } catch {
  }
}

export function readSiderCollapsed(store: PreferenceStore | null): boolean {
  const raw = readRaw(store, UI_PREFERENCE_KEYS.siderCollapsed)
  if (raw === '1') return true
  if (raw === '0') return false
  return false
}

export function writeSiderCollapsed(store: PreferenceStore | null, collapsed: boolean): void {
  writeRaw(store, UI_PREFERENCE_KEYS.siderCollapsed, collapsed ? '1' : '0')
}
