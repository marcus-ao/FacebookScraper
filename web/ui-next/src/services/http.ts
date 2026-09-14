// 后端接口的传输层。基础契约见 web/DESIGN.md；人工保存同时携带源文和人工稿版本。
//
// ⛔ 从 web/ui/src/api.js **算法 1:1 移植**，只加类型（DECISION_LOG.md §5.3）。
//    三条必须守住的行为：
//      1. task id 逐段 encode，不整体 encodeURIComponent；
//      2. error.status / error.payload 保留（两处调用方依赖它做降级）；
//      3. export 的下载文件名解析与 30 秒后 revokeObjectURL 保留。
//
// ⚠️ 任务 id 形如 `in_neakasa.tech/3965025107383038890`，**自带一个斜杠**。
// 拼 URL 时不能 encodeURIComponent 整个 id（那会把斜杠变成 %2F，
// 后端的 {task_id:path} 就匹配不上了），只逐段编码。

/**
 * 请求失败时抛出的错误。
 *
 * `status` 与 `payload` 不是防御性代码，是**有意的降级路径**：
 *   - 排期冲突（409）读 `payload.suggestions` 渲染可点的备选时刻；
 *   - 月历刷新失败读 `payload.cards`，失败时仍然用返回体里的卡片渲染。
 */
export class ApiError extends Error {
  readonly status: number
  readonly payload: unknown

  constructor(message: string, status: number, payload: unknown) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.payload = payload
  }
}

export const isApiError = (value: unknown): value is ApiError => value instanceof ApiError

/** 是否版本/锁/许可冲突。web/DESIGN.md §7：409 表示版本过期，需要恢复入口。 */
export const isConflict = (value: unknown): boolean => isApiError(value) && value.status === 409

/**
 * 把 task id 逐段编码。
 *
 * ⛔ 不许换成 `encodeURIComponent(taskId)`：id 自带一个斜杠，整体编码会把它
 * 变成 %2F，后端 `{task_id:path}` 匹配不上，请求直接 404。
 */
export function idPath(taskId: string): string {
  return String(taskId).split('/').map(encodeURIComponent).join('/')
}

async function fetchResponse(url: string, options?: RequestInit): Promise<Response> {
  const response = await fetch(url, options)
  if (!response.ok) {
    let detail = `HTTP ${response.status}`
    let payload: unknown = null
    try {
      const body: unknown = await response.json()
      payload = body
      if (body && typeof body === 'object' && 'detail' in body) {
        const value = (body as { detail?: unknown }).detail
        if (typeof value === 'string' && value) detail = value
      }
    } catch {
      /* 响应不是 JSON，就用状态码 */
    }
    throw new ApiError(detail, response.status, payload)
  }
  return response
}

export async function request<T>(url: string, options?: RequestInit): Promise<T> {
  return (await fetchResponse(url, options)).json() as Promise<T>
}

/** POST + JSON body。旧代码里的 `json()` 助手。 */
export const jsonBody = (body: unknown): RequestInit => ({
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body),
})

/** PUT + JSON body。旧代码写成 `{ ...json(body), method: 'PUT' }`，这里显式一点。 */
export const putBody = (body: unknown): RequestInit => ({
  ...jsonBody(body),
  method: 'PUT',
})

/** 从 Content-Disposition 里取文件名。取不到时由调用方给默认值。 */
export function filenameFromDisposition(header: string | null): string | undefined {
  return /filename="([^"]+)"/.exec(header ?? '')?.[1]
}

export interface DownloadedFile {
  readonly blob: Blob
  readonly filename: string
}

/**
 * 下载二进制响应（当前只有 export 的 ZIP 用它）。
 *
 * 文件名解析与默认值 `post_de.zip` 照搬 api.js:58-62。
 */
export async function requestFile(
  url: string,
  options: RequestInit,
  fallbackFilename: string,
): Promise<DownloadedFile> {
  const response = await fetchResponse(url, options)
  const filename = filenameFromDisposition(response.headers.get('Content-Disposition'))
  return { blob: await response.blob(), filename: filename ?? fallbackFilename }
}

/** 30 秒后再 revoke —— 照搬 ReviewActions.vue:47-57，不要改成立即 revoke。 */
export const OBJECT_URL_TTL_MS = 30_000

/**
 * 触发浏览器下载。
 *
 * 抽出来是为了让"创建 objectURL → 造 <a> → click → remove → 30 秒后 revoke"
 * 这一串只存在一份。立即 revoke 在部分 Chromium 上会让下载失败，所以保留延迟。
 */
export function triggerDownload(file: DownloadedFile, ttlMs: number = OBJECT_URL_TTL_MS): void {
  const url = URL.createObjectURL(file.blob)
  const link = document.createElement('a')
  link.href = url
  link.download = file.filename
  document.body.appendChild(link)
  link.click()
  link.remove()
  setTimeout(() => URL.revokeObjectURL(url), ttlMs)
}

/**
 * 拼查询串。**空串与 null/undefined 一律丢掉** —— 照搬 api.js:45 的过滤，
 * 否则 `platform=` 这种空参数会让后端的 pattern 校验 422。
 */
export function queryString(params: Readonly<Record<string, string | number | boolean | null | undefined>>): string {
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value === '' || value === null || value === undefined) continue
    search.set(key, String(value))
  }
  return search.toString()
}
