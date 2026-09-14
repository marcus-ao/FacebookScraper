// 三个品牌类型。目的不是好看，是防住三类实测踩过的错：
//   TaskId  —— 它自带一个斜杠（`<账号目录>/<post_id>`），整体 encodeURIComponent
//              会把斜杠变成 %2F，后端的 {task_id:path} 就匹配不上（见 services/http.ts）
//   Sha256  —— 64 位小写 hex；后端对 source_text_sha256 与 tags_revision 都有
//              `[0-9a-f]{64}` 的正则校验，传错格式是 400 不是 409
//   Revision —— UUID；localization / human / review 三个 revision 不能互相传

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

/**
 * 从路由参数拼回 TaskId。
 *
 * 路由把它拆成 :account/:postId 两段是刻意的——这样 React Router 不需要
 * 处理带斜杠的单个参数，而请求 URL 仍由 idPath() 逐段编码。
 */
export const taskId = (account: string, postId: string): TaskId =>
  `${account}/${postId}` as TaskId

/** 反向拆开。拆不出两段时返回 null，调用方自己决定怎么报错。 */
export const splitTaskId = (value: string): { account: string; postId: string } | null => {
  const parts = value.split('/')
  const [account, postId] = parts
  if (parts.length !== 2 || !account || !postId) return null
  return { account, postId }
}
