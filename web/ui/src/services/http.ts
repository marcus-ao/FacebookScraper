
/** 保留 status 与 payload，供排期建议和月历失败回退使用。 */
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

export const isConflict = (value: unknown): boolean => isApiError(value) && value.status === 409

/** task id 自带斜杠，只逐段编码，保留路由分隔符。 */
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

export const jsonBody = (body: unknown): RequestInit => ({
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body),
})

export const putBody = (body: unknown): RequestInit => ({
  ...jsonBody(body),
  method: 'PUT',
})

export function filenameFromDisposition(header: string | null): string | undefined {
  return /filename="([^"]+)"/.exec(header ?? '')?.[1]
}

export interface DownloadedFile {
  readonly blob: Blob
  readonly filename: string
}

export async function requestFile(
  url: string,
  options: RequestInit,
  fallbackFilename: string,
): Promise<DownloadedFile> {
  const response = await fetchResponse(url, options)
  const filename = filenameFromDisposition(response.headers.get('Content-Disposition'))
  return { blob: await response.blob(), filename: filename ?? fallbackFilename }
}

export const OBJECT_URL_TTL_MS = 30_000

/** 延迟释放对象 URL，避免 Chromium 尚未读取就导致下载失败。 */
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

/** 丢弃空查询参数，避免 platform= 触发接口 422。 */
export function queryString(params: Readonly<Record<string, string | number | boolean | null | undefined>>): string {
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value === '' || value === null || value === undefined) continue
    search.set(key, String(value))
  }
  return search.toString()
}
