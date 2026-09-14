/**
 * 类名拼接。
 *
 * 存在的理由是一个类型细节：`tsconfig` 开了 `noUncheckedIndexedAccess`
 * （DECISION_LOG.md §5.1 指定，不许放宽），而 CSS Modules 的导出是一个
 * 索引签名对象，所以 `styles.foo` 的类型是 `string | undefined` ——
 * 即使那个类名确实存在。
 *
 * 与其在每个组件里写断言把 `undefined` 硬压掉，不如收敛到这一个函数：
 * 类名拼不出来时就是没有类名，**这正是运行期真实会发生的事**，
 * 断言只会把它藏起来。
 */
export function cx(...parts: readonly (string | false | null | undefined)[]): string {
  return parts.filter((part): part is string => typeof part === 'string' && part !== '').join(' ')
}
