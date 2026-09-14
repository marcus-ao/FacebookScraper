/**
 * 未保存草稿的离开守卫 —— **纯逻辑部分**。React 部分在
 * `src/hooks/useUnsavedChangesGuard.tsx`；判断放这里是为了能单测。
 *
 * 三层离开路径：
 *
 *   应用内导航    路由守卫
 *   浏览器前进后退 路由守卫
 *   关闭标签页    beforeunload
 *
 * 三层都必须保住。守卫抽到一处，让详情编辑与设置编辑共用判据、文案和边界行为。
 */

/**
 * 两句确认文案。
 *
 * ⛔ 不要改写、不要加解释。她已经认识这两句话了，换一套长解释只会让她
 * 在一个本来 0.5 秒就能过的对话框上停下来重读。
 */
export const UNSAVED_MESSAGES = {
  /** 详情页的德语草稿 / 标签 / 链接 / CTA。 */
  detail: '修改尚未保存，确定离开并放弃当前草稿？',
  /** 运营设置的两个可编辑字段。 */
  settings: '设置尚未保存，离开会放弃这次修改。继续离开？',
} as const

export type UnsavedMessageKey = keyof typeof UNSAVED_MESSAGES

interface LocationLike {
  readonly pathname: string
}

export interface BlockDecisionInput {
  /** 当前是否真的有未保存的改动。 */
  readonly dirty: boolean
  readonly currentLocation: LocationLike
  readonly nextLocation: LocationLike
}

/**
 * 要不要拦这一次导航。
 *
 * 两条规则：
 *
 *   1. **不脏就不拦。** 这一条比听起来重要：一个总是弹确认框的守卫，
 *      三天之内就会被她训练成"闭着眼点继续"，那时它已经不保护任何东西了。
 *
 *   2. **只看 pathname，不看 search。** 详情页切标签页（`?tab=images`）、
 *      列表改筛选都只动 search；那些不是"离开"，拦它们是纯粹的干扰。
 *      真正要拦的是换一个界面 —— 那时草稿确实会没。
 */
export function shouldBlockNavigation(input: BlockDecisionInput): boolean {
  if (!input.dirty) return false
  return input.currentLocation.pathname !== input.nextLocation.pathname
}

/**
 * `beforeunload` 的处理。
 *
 * 现代浏览器忽略自定义文案、只认"有没有阻止默认行为"，所以这里只负责
 * 决定要不要阻止。文案由浏览器自己出，我们控制不了，也不该假装能控制。
 */
export function applyBeforeUnload(event: BeforeUnloadEvent, dirty: boolean): boolean {
  if (!dirty) return false
  event.preventDefault()
  // 老浏览器要求 returnValue 非空才弹；给空串即可，不是给用户看的文案。
  event.returnValue = ''
  return true
}
