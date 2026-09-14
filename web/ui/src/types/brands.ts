// 区分任务 ID、哈希与版本，避免不同资源的版本值混用。

declare const brand: unique symbol

export type TaskId = string & { readonly [brand]: 'TaskId' }
export type Sha256 = string & { readonly [brand]: 'Sha256' }
export type Revision = string & { readonly [brand]: 'Revision' }

const SHA256_RE = /^[0-9a-f]{64}$/
const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i

export const isSha256 = (value: unknown): value is Sha256 =>
  typeof value === 'string' && SHA256_RE.test(value)

export const isRevision = (value: unknown): value is Revision =>
  typeof value === 'string' && UUID_RE.test(value)

/** `<账号目录>/<post_id>`：恰好一个斜杠，两段都非空。 */
export const isTaskId = (value: unknown): value is TaskId => {
  if (typeof value !== 'string') return false
  const parts = value.split('/')
  return parts.length === 2 && !!parts[0] && !!parts[1]
}

export const taskId = (account: string, postId: string): TaskId =>
  `${account}/${postId}` as TaskId

export const splitTaskId = (value: string): { account: string; postId: string } | null => {
  const parts = value.split('/')
  const [account, postId] = parts
  if (parts.length !== 2 || !account || !postId) return null
  return { account, postId }
}
