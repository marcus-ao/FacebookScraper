/**
 * 本地 UI 偏好。**这是 localStorage 在这个应用里唯一被允许的用途。**
 *
 * ⛔ 不许进 localStorage 的东西：
 *
 *    筛选条件 ────────▶ URL（分享得出去、刷新还在、前进后退能回）
 *    当前帖子 ────────▶ URL
 *    编辑草稿 ────────▶ 组件 local state（它代表已经做过的人工劳动，
 *                       放进任何会被 invalidate 的地方都会被抹掉）
 *    任何业务状态 ────▶ 服务端账本
 *
 * 判据只有一句：**这条信息丢了，业务上有没有后果？** 有 → 不许放这里。
 * 侧边栏折不折叠丢了，她重新点一下就行，所以它可以放。
 *
 * 边界从一开始就固定在这里，避免本地持久化扩张到业务状态。
 *
 * 真正阻止别处乱写的是 design-discipline.test.ts：它扫整个 src/，
 * 除本文件外任何地方出现 `localStorage` / `sessionStorage` 都会红。
 */

/** 唯一的命名空间。前缀里带 `ui` 是为了让"只存 UI 偏好"这件事写在键名上。 */
const PREFIX = 'rc.ui.'

/** 允许存在的键，**穷尽**。加键要同时加到这里，否则写不进去。 */
export const UI_PREFERENCE_KEYS = {
  siderCollapsed: `${PREFIX}sider-collapsed`,
} as const

export type UiPreferenceKey = (typeof UI_PREFERENCE_KEYS)[keyof typeof UI_PREFERENCE_KEYS]

/**
 * 存储的最小接口。
 *
 * 用接口而不是直接摸 `window.localStorage`，是为了能在测试里喂一个假的，
 * 以及在 storage 不可用时（隐私模式、被策略禁掉）整体降级成"不记住"。
 */
export interface PreferenceStore {
  getItem(key: string): string | null
  setItem(key: string, value: string): void
}

/** 浏览器里拿真实 storage；拿不到就返回 null，调用方走默认值。 */
export function browserStore(): PreferenceStore | null {
  try {
    const store = globalThis.localStorage
    if (!store) return null
    // 有些环境里 localStorage 存在但一写就抛，先探一次。
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
    // 写不进去就算了。偏好丢失没有业务后果，这正是它被允许放这里的原因。
  }
}

/**
 * 左侧导航是否折叠。**默认展开**。
 *
 * 任何非 `'1'` / `'0'` 的值都当成"没设过"，回落默认展开 ——
 * 手改过 localStorage 不应该让外壳进入奇怪状态。
 */
export function readSiderCollapsed(store: PreferenceStore | null): boolean {
  const raw = readRaw(store, UI_PREFERENCE_KEYS.siderCollapsed)
  if (raw === '1') return true
  if (raw === '0') return false
  return false
}

export function writeSiderCollapsed(store: PreferenceStore | null, collapsed: boolean): void {
  writeRaw(store, UI_PREFERENCE_KEYS.siderCollapsed, collapsed ? '1' : '0')
}
