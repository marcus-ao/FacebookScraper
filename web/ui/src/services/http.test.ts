import { afterEach, describe, expect, it, vi } from 'vitest'

import {
  ApiError,
  OBJECT_URL_TTL_MS,
  filenameFromDisposition,
  idPath,
  isApiError,
  isConflict,
  jsonBody,
  putBody,
  queryString,
  request,
  requestFile,
  triggerDownload,
} from './http'

afterEach(() => {
  vi.unstubAllGlobals()
  vi.useRealTimers()
  vi.restoreAllMocks()
})

const jsonResponse = (status: number, body: unknown): Response =>
  ({
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
    headers: new Headers(),
  }) as unknown as Response

describe('idPath：逐段编码，斜杠必须活下来', () => {
  const realId = 'in_neakasa.tech/3965025107383038890'

  it('普通 id 原样通过', () => {
    expect(idPath(realId)).toBe(realId)
    expect(idPath('fa_neakasaofficial/122100548013379375')).toBe(
      'fa_neakasaofficial/122100548013379375',
    )
  })

  it('斜杠不被编码', () => {
    expect(idPath(realId)).toContain('/')
    expect(idPath(realId)).not.toContain('%2F')
  })

  it('整体编码会坏掉 —— 证明上面那条不是巧合', () => {
    expect(encodeURIComponent(realId)).toContain('%2F')
    expect(idPath(realId)).not.toBe(encodeURIComponent(realId))
  })

  it('段内的特殊字符仍然被编码', () => {
    expect(idPath('fa_x/a b')).toBe('fa_x/a%20b')
    expect(idPath('fa_x/a#b')).toBe('fa_x/a%23b')
    expect(idPath('fa_x/a?b')).toBe('fa_x/a%3Fb')
  })

  it('账号目录里的点不被编码（真实 id 全是这种形状）', () => {
    expect(idPath('in_neakasa.global/123')).toBe('in_neakasa.global/123')
  })
})

describe('ApiError：status 与 payload 是有意的降级路径，不是防御性代码', () => {
  it('把 body.detail 提成 message', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(jsonResponse(409, { detail: '本地化选择已有更新，请载入最新内容后再保存' })),
    )
    await expect(request('/api/x')).rejects.toThrow('本地化选择已有更新，请载入最新内容后再保存')
  })

  it('保留 status，让调用方能判 409', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(409, { detail: 'x' })))
    const error = await request('/api/x').catch((exc: unknown) => exc)
    expect(isApiError(error)).toBe(true)
    expect((error as ApiError).status).toBe(409)
    expect(isConflict(error)).toBe(true)
  })

  it('保留整个 payload —— 排期冲突要读 suggestions 渲染可点的备选时刻', async () => {
    const body = {
      detail: '此时刻与同渠道已有排期冲突',
      suggestions: ['2026-09-14T10:00:00+02:00', '2026-09-14T17:00:00+02:00'],
    }
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(409, body)))
    const error = (await request('/api/x').catch((exc: unknown) => exc)) as ApiError
    expect(error.payload).toEqual(body)
    expect((error.payload as typeof body).suggestions).toHaveLength(2)
  })

  it('保留整个 payload —— 月历刷新失败仍要用返回体里的 cards 渲染', async () => {
    const body = { detail: '刷新超时，保留上次读取的数据。', cards: [{ at: '2026-09-13T08:00:00Z' }] }
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(502, body)))
    const error = (await request('/api/calendar/refresh').catch((exc: unknown) => exc)) as ApiError
    expect(Array.isArray((error.payload as typeof body).cards)).toBe(true)
  })

  it('响应不是 JSON 时回落到状态码，payload 为 null', async () => {
    const broken = {
      ok: false,
      status: 500,
      json: async () => {
        throw new SyntaxError('not json')
      },
      headers: new Headers(),
    } as unknown as Response
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(broken))
    const error = (await request('/api/x').catch((exc: unknown) => exc)) as ApiError
    expect(error.message).toBe('HTTP 500')
    expect(error.payload).toBeNull()
  })

  it('body 里没有 detail 时也回落到状态码', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(400, { other: 1 })))
    const error = (await request('/api/x').catch((exc: unknown) => exc)) as ApiError
    expect(error.message).toBe('HTTP 400')
    expect(error.payload).toEqual({ other: 1 })
  })

  it('2xx 正常返回解析后的 JSON', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(200, { tasks: [] })))
    await expect(request<{ tasks: unknown[] }>('/api/tasks')).resolves.toEqual({ tasks: [] })
  })

  it('isConflict 只认 409', async () => {
    expect(isConflict(new ApiError('x', 400, null))).toBe(false)
    expect(isConflict(new ApiError('x', 409, null))).toBe(true)
    expect(isConflict(new Error('x'))).toBe(false)
  })
})

describe('请求体助手', () => {
  it('jsonBody 是 POST + JSON 头', () => {
    const init = jsonBody({ a: 1 })
    expect(init.method).toBe('POST')
    expect(init.headers).toEqual({ 'Content-Type': 'application/json' })
    expect(init.body).toBe('{"a":1}')
  })

  it('putBody 只把方法换成 PUT', () => {
    expect(putBody({ a: 1 }).method).toBe('PUT')
    expect(putBody({ a: 1 }).body).toBe('{"a":1}')
  })
})

describe('queryString：空串与 null 必须丢掉', () => {
  it('丢掉空串、null、undefined', () => {
    expect(queryString({ platform: '', month: null, tag: undefined, page: 1 })).toBe('page=1')
  })

  it('保留 0 与 false —— 它们是有意义的取值', () => {
    const result = queryString({ page: 0, alerts: false })
    expect(result).toContain('page=0')
    expect(result).toContain('alerts=false')
  })

  it('值被正确编码', () => {
    expect(queryString({ tag: 'M1 Pro' })).toBe('tag=M1+Pro')
  })

  it('真实的历史查询串', () => {
    const result = queryString({
      scope: 'history',
      page: 2,
      limit: 50,
      platform: 'instagram',
      month: '2026-08',
      tag: '',
    })
    expect(result).toBe('scope=history&page=2&limit=50&platform=instagram&month=2026-08')
  })
})

describe('export 下载：文件名解析与延迟 revoke', () => {
  it('从 Content-Disposition 取文件名', () => {
    expect(filenameFromDisposition('attachment; filename="post_de_122100548013379375.zip"')).toBe(
      'post_de_122100548013379375.zip',
    )
  })

  it('头缺失或格式不符时返回 undefined', () => {
    expect(filenameFromDisposition(null)).toBeUndefined()
    expect(filenameFromDisposition('attachment')).toBeUndefined()
  })

  it('requestFile 在取不到文件名时用默认值', async () => {
    const response = {
      ok: true,
      status: 200,
      headers: new Headers(),
      blob: async () => new Blob(['zip']),
    } as unknown as Response
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(response))
    const file = await requestFile('/api/x/export', jsonBody({}), 'post_de.zip')
    expect(file.filename).toBe('post_de.zip')
  })

  it('requestFile 用响应头里的文件名', async () => {
    const response = {
      ok: true,
      status: 200,
      headers: new Headers({ 'Content-Disposition': 'attachment; filename="real.zip"' }),
      blob: async () => new Blob(['zip']),
    } as unknown as Response
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(response))
    const file = await requestFile('/api/x/export', jsonBody({}), 'post_de.zip')
    expect(file.filename).toBe('real.zip')
  })

  it('objectURL 在 30 秒后才 revoke，不是立刻', () => {
    vi.useFakeTimers()
    const createObjectURL = vi.fn().mockReturnValue('blob:fake')
    const revokeObjectURL = vi.fn()
    const link = { href: '', download: '', click: vi.fn(), remove: vi.fn() }
    vi.stubGlobal('URL', { createObjectURL, revokeObjectURL })
    vi.stubGlobal('document', {
      createElement: vi.fn().mockReturnValue(link),
      body: { appendChild: vi.fn() },
    })

    triggerDownload({ blob: new Blob(['zip']), filename: 'post_de.zip' })

    expect(createObjectURL).toHaveBeenCalledOnce()
    expect(link.download).toBe('post_de.zip')
    expect(link.click).toHaveBeenCalledOnce()
    expect(link.remove).toHaveBeenCalledOnce()

    expect(revokeObjectURL).not.toHaveBeenCalled()
    vi.advanceTimersByTime(OBJECT_URL_TTL_MS - 1)
    expect(revokeObjectURL).not.toHaveBeenCalled()
    vi.advanceTimersByTime(1)
    expect(revokeObjectURL).toHaveBeenCalledWith('blob:fake')
  })

  it('TTL 就是旧代码里的 30 秒', () => {
    expect(OBJECT_URL_TTL_MS).toBe(30_000)
  })
})
