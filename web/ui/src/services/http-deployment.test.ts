import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const runtime = 'd'.repeat(64)
const response = (body: unknown, status = 200) => ({ ok: status === 200, status,
  headers: new Headers(), json: async () => body }) as Response
let http: typeof import('./http')
let store: typeof import('../app/deployment-store').deploymentStore
let fetch: ReturnType<typeof vi.fn>

beforeEach(async () => {
  vi.resetModules(); vi.useFakeTimers(); vi.stubEnv('VITE_FBSCRAPER_RUNTIME_ID', runtime)
  fetch = vi.fn(async (url: string): Promise<Response> => response(url.endsWith('/status')
    ? { managed: true, runtime_id: runtime, maintenance: { phase: 'open' }, deployment: {} } : { accepted: true }))
  vi.stubGlobal('fetch', fetch)
  http = await import('./http')
  store = (await import('../app/deployment-store')).deploymentStore
  await store.refresh(); fetch.mockClear()
})
afterEach(async () => { await vi.runAllTimersAsync(); vi.useRealTimers(); vi.unstubAllGlobals(); vi.unstubAllEnvs() })

describe('business request deployment lifecycle', () => {
  it('reports policy denial, retains a dirty draft, and blocks another save without mislabeling it as maintenance', async () => {
    store.setDirty('editor', true)
    const payload = { code: 'access_denied', detail: '当前地址或来源未获允许' }
    fetch.mockResolvedValueOnce(response(payload, 403))
    await expect(http.request('/api/save', http.putBody({ body: 'draft' }))).rejects.toMatchObject({
      status: 403, payload, message: expect.stringContaining('访问被拒绝'),
    })
    expect(store.getSnapshot()).toMatchObject({ accessDenied: true, frozen: true, dirty: true })
    await expect(http.request('/api/save', http.putBody({ body: 'draft' }))).rejects.toMatchObject({
      status: 403, message: expect.stringContaining('访问被拒绝'),
    })
    expect(fetch.mock.calls.filter(([url]) => url === '/api/save')).toHaveLength(1)
    await vi.runAllTimersAsync()
    expect(store.getSnapshot()).toMatchObject({ pending: 0, dirty: true, frozen: true })
  })

  it('sends the built runtime header and retains busy until JSON and consumer complete', async () => {
    let finish!: (body: unknown) => void
    fetch.mockResolvedValueOnce({ ...response(null), json: () => new Promise(done => { finish = done }) })
    const request = http.request('/api/save', http.jsonBody({ body: 'draft' }))
    await Promise.resolve(); await Promise.resolve()
    expect(new Headers(fetch.mock.calls[0]![1].headers).get('X-FBScraper-Runtime')).toBe(runtime)
    expect(store.getSnapshot().pending).toBe(1)
    finish({ body: 'saved' }); await request
    expect(store.getSnapshot().pending).toBe(1)
    store.setDirty('consumer-new-input', true)
    await vi.runAllTimersAsync()
    expect(store.getSnapshot()).toMatchObject({ pending: 0, dirty: true })
  })

  it('holds blob downloads until body consumption finishes', async () => {
    let finish!: (body: Blob) => void
    fetch.mockResolvedValueOnce({ ...response(null), blob: () => new Promise(done => { finish = done }) })
    const request = http.requestFile('/api/export', http.jsonBody({}), 'test.zip')
    await Promise.resolve(); await Promise.resolve()
    expect(store.getSnapshot().pending).toBe(1)
    finish(new Blob(['test'])); await request; await vi.runAllTimersAsync()
    expect(store.getSnapshot().pending).toBe(0)
  })

  it('does not retry a runtime conflict, clears busy and retains dirty state', async () => {
    store.setDirty('editor', true)
    fetch.mockResolvedValueOnce(response({ code: 'runtime_changed', detail: '保留草稿' }, 409))
    await expect(http.request('/api/save', http.putBody({}))).rejects.toMatchObject({ status: 409 })
    expect(fetch.mock.calls.filter(([url]) => url === '/api/save')).toHaveLength(1)
    expect(store.getSnapshot()).toMatchObject({ frozen: true, dirty: true, conflict: true })
    await vi.runAllTimersAsync()
    expect(store.getSnapshot().pending).toBe(0)
  })

  it('rejects new writes while uncertain without sending business traffic', async () => {
    store.uncertain()
    await expect(http.request('/api/save', http.putBody({}))).rejects.toMatchObject({ status: 503 })
    expect(fetch.mock.calls.filter(([url]) => url === '/api/save')).toHaveLength(0)
    await vi.runAllTimersAsync()
    expect(store.getSnapshot().pending).toBe(0)
  })
})
