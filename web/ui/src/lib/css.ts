/** 接收 CSS Modules 在 noUncheckedIndexedAccess 下的可选类名。 */
export function cx(...parts: readonly (string | false | null | undefined)[]): string {
  return parts.filter((part): part is string => typeof part === 'string' && part !== '').join(' ')
}
