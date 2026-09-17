import { afterEach, describe, expect, it, vi } from 'vitest'
import { DeploymentStore } from './deployment-store'
import type { DeploymentStatus } from './deployment-store'
import { deploymentMessage } from './DeploymentBanner'

const runtime = 'a'.repeat(64)
const json = (body: unknown, ok = true) => ({ ok, json: async () => body }) as Response
function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>(done => { resolve = done })
  return { promise, resolve }
}
function fixture() {
  let status: DeploymentStatus = { managed: true, sha: '1'.repeat(40), runtime_id: runtime, public_base_url: 'http://192.168.1.10:8765',
    maintenance: { phase: 'open', epoch: 'epoch-one', blockers: [], operations: [] }, deployment: {} }
  const reports: Record<string, unknown>[] = []
  const fetch = vi.fn(async (url: string, options?: RequestInit): Promise<Response> => {
    if (url.endsWith('/status')) return json(status)
    if (url.endsWith('/session')) reports.push(JSON.parse(String(options?.body)) as Record<string, unknown>)
    return json({ accepted: true })
  })
  const reload = vi.fn()
  const store = new DeploymentStore(runtime, fetch, reload, 'test-tab')
  return { store, fetch, reload, reports, setStatus: (value: Partial<DeploymentStatus>) => { status = { ...status, ...value } },
    announce: (epoch = 'epoch-one') => { status = { ...status, maintenance: { phase: 'announcing', epoch, blockers: [], operations: [] } } } }
}
afterEach(() => { vi.useRealTimers(); vi.restoreAllMocks(); vi.unstubAllGlobals() })

describe('deployment session coordination', () => {
  it('registers distinct per-tab IDs over HTTP when randomUUID is unavailable', async () => {
    const getRandomValues = globalThis.crypto.getRandomValues.bind(globalThis.crypto)
    vi.stubGlobal('crypto', { getRandomValues })
    const f = fixture()
    const first = new DeploymentStore(runtime, f.fetch, f.reload)
    const second = new DeploymentStore(runtime, f.fetch, f.reload)
    await first.refresh(); await second.refresh(); await first.report()
    const ids = f.reports.map(report => report.session_id)
    expect(ids).toEqual([first.sessionId, second.sessionId, first.sessionId])
    expect(first.sessionId).not.toBe(second.sessionId)
    expect(first.sessionId).toMatch(/^[A-Za-z0-9_-]{1,80}$/)
  })

  it.each(['refresh', 'report', 'defer'] as const)('distinguishes a denied %s from disconnection and retains drafts', async (operation) => {
    vi.useFakeTimers()
    const f = fixture(); await f.store.refresh(); f.store.setDirty('editor', true)
    f.fetch.mockResolvedValueOnce({ ...json({ code: 'access_denied' }, false), status: 403 } as Response)
    await f.store[operation]()
    expect(f.store.getSnapshot()).toMatchObject({ accessDenied: true, dirty: true, frozen: true,
      registered: false, status: { managed: true } })
    expect(deploymentMessage(f.store.getSnapshot())).toContain('访问被拒绝')
    expect(deploymentMessage(f.store.getSnapshot())).not.toContain('重新连接')
    expect(f.reload).not.toHaveBeenCalled()
    f.store.uncertain()
    expect(deploymentMessage(f.store.getSnapshot())).toContain('访问被拒绝')
    await f.store.refresh()
    expect(f.store.getSnapshot()).toMatchObject({ accessDenied: false, registered: true, dirty: true, frozen: false })
  })

  it('holds a denied unmanaged page and shows the policy notice', async () => {
    const fetch = vi.fn().mockResolvedValue({ ...json({ code: 'access_denied' }, false), status: 403 })
    const store = new DeploymentStore('', fetch, vi.fn(), 'dev-tab')
    await store.refresh()
    expect(store.getSnapshot()).toMatchObject({ accessDenied: true, frozen: true })
    expect(deploymentMessage(store.getSnapshot())).toContain('公布的审校台入口')
  })

  it('holds a managed initial page until status AND session registration complete', async () => {
    const f = fixture(), response = deferred<Response>()
    f.fetch.mockImplementationOnce(async () => json({ managed: true, runtime_id: runtime, maintenance: { phase: 'open' }, deployment: {} }))
      .mockImplementationOnce(() => response.promise)
    expect(f.store.getSnapshot().frozen).toBe(true)
    const refresh = f.store.refresh()
    await vi.waitFor(() => expect(f.fetch).toHaveBeenCalledTimes(2))
    expect(f.store.getSnapshot().frozen).toBe(true)
    response.resolve(json({ accepted: true })); await refresh
    expect(f.store.getSnapshot().frozen).toBe(false)
  })

  it('keeps independent dirty sources until the last source is clean', async () => {
    const f = fixture(); await f.store.refresh()
    f.store.setDirty('editor', true); f.store.setDirty('settings', true)
    f.store.setDirty('editor', false); await f.store.report()
    expect(f.reports.at(-1)?.dirty).toBe(true)
    f.store.setDirty('settings', false); await f.store.report()
    expect(f.reports.at(-1)?.dirty).toBe(false)
  })

  it('serializes an in-flight clean report before the newest dirty snapshot', async () => {
    const f = fixture(); await f.store.refresh()
    const delayed = deferred<Response>()
    f.fetch.mockImplementationOnce(() => delayed.promise)
    const first = f.store.report()
    f.store.setDirty('editor', true)
    const latest = f.store.report()
    expect(f.fetch).toHaveBeenCalledTimes(3)
    delayed.resolve(json({ accepted: true })); await Promise.all([first, latest])
    expect(f.reports.at(-1)).toMatchObject({ dirty: true, sequence: 3, ack_epoch: null })
  })

  it('freezes synchronously before acknowledging the matching epoch, never dirty or busy', async () => {
    const f = fixture(); await f.store.refresh(); f.announce('epoch-two')
    f.store.subscribe(() => { if (f.store.getSnapshot().status?.maintenance?.phase === 'announcing') expect(f.store.getSnapshot().frozen).toBe(true) })
    await f.store.refresh()
    expect(f.reports.at(-1)?.ack_epoch).toBe('epoch-two')
    const d = fixture(); await d.store.refresh(); d.store.setDirty('editor', true); d.announce()
    await d.store.refresh()
    expect(d.store.getSnapshot().frozen).toBe(false)
    expect(d.reports.at(-1)?.ack_epoch).toBeNull()
    const b = fixture(); await b.store.refresh(); b.store.beginRequest(); b.announce(); await b.store.refresh()
    expect(b.reports.at(-1)).toMatchObject({ busy: true, ack_epoch: null })
  })

  it('keeps busy through consumer callbacks and only then acknowledges clean', async () => {
    vi.useFakeTimers()
    const f = fixture(); await f.store.refresh()
    const end = f.store.beginRequest(); f.announce(); await f.store.refresh()
    end(); end()
    expect(f.store.getSnapshot().pending).toBe(1)
    f.store.setDirty('response-created-draft', true)
    await vi.runAllTimersAsync()
    expect(f.store.getSnapshot().pending).toBe(0)
    expect(f.reports.at(-1)).toMatchObject({ dirty: true, busy: false, ack_epoch: null })
  })

  it('preserves known deployment and dirty state when status or heartbeat fails', async () => {
    const f = fixture(); await f.store.refresh(); f.store.setDirty('editor', true)
    f.fetch.mockRejectedValueOnce(new Error('network'))
    await f.store.report()
    expect(f.store.getSnapshot()).toMatchObject({ connected: false, dirty: true, frozen: true, status: { managed: true } })
    f.fetch.mockRejectedValueOnce(new Error('restart')); await f.store.refresh()
    expect(f.store.getSnapshot().status?.sha).toBe('1'.repeat(40))
  })

  it('re-establishes status and registration after focus before reopening interactions', async () => {
    const f = fixture(); await f.store.refresh()
    const delayed = deferred<Response>(); f.fetch.mockImplementationOnce(() => delayed.promise)
    const reconnect = f.store.resume()
    expect(f.store.getSnapshot()).toMatchObject({ frozen: true, registered: false })
    delayed.resolve(json({ managed: true, runtime_id: runtime, maintenance: { phase: 'open' }, deployment: {} }))
    await reconnect
    expect(f.store.getSnapshot().frozen).toBe(false)
  })

  it('does not let an old pre-resume response reopen an uncertain tab', async () => {
    const f = fixture(); await f.store.refresh()
    const delayed = deferred<Response>(); f.fetch.mockImplementationOnce(() => delayed.promise)
    const old = f.store.refresh(); f.store.uncertain()
    delayed.resolve(json({ managed: true, runtime_id: runtime, maintenance: { phase: 'open' }, deployment: {} })); await old
    expect(f.store.getSnapshot().frozen).toBe(true)
  })

  it('blocks the first interaction after foreground suspension even without a focus event', async () => {
    vi.useFakeTimers()
    const f = fixture(); await f.store.refresh()
    vi.setSystemTime(Date.now() + 61_000)
    expect(f.store.mutationAllowed()).toBe(false)
    expect(f.store.getSnapshot().registered).toBe(false)
    await f.store.refresh()
    expect(f.store.mutationAllowed()).toBe(true)
  })

  it('retains dirty content across runtime changes and reloads once only after safe cleanup', async () => {
    vi.useFakeTimers()
    const f = fixture(); await f.store.refresh(); f.store.setDirty('editor', true)
    f.setStatus({ runtime_id: 'b'.repeat(64) }); await f.store.refresh()
    expect(f.reload).not.toHaveBeenCalled()
    expect(f.store.getSnapshot()).toMatchObject({ dirty: true, conflict: true, frozen: true })
    const end = f.store.beginRequest(); f.store.setDirty('editor', false)
    await f.store.refresh(); expect(f.reload).not.toHaveBeenCalled()
    end(); await vi.runAllTimersAsync(); await f.store.refresh()
    expect(f.reload).toHaveBeenCalledOnce()
  })

  it('runtime 409 never reloads blindly and preserves a dirty draft', async () => {
    const f = fixture(); await f.store.refresh(); f.store.setDirty('editor', true)
    f.store.rejectBusiness(409, { code: 'runtime_changed' })
    expect(f.reload).not.toHaveBeenCalled()
    expect(f.store.getSnapshot()).toMatchObject({ dirty: true, conflict: true, frozen: true })
  })

  it('dirty or busy pagehide never claims clean or closed; clean close increments sequence', async () => {
    const f = fixture(); await f.store.refresh(); f.store.setDirty('editor', true)
    const send = vi.fn(); f.store.close(send)
    expect(JSON.parse(send.mock.calls[0]![0] as string)).toMatchObject({ dirty: true, closed: false, sequence: 2 })
    const b = fixture(); await b.store.refresh(); b.store.beginRequest(); b.store.close(send)
    expect(JSON.parse(send.mock.calls[1]![0] as string)).toMatchObject({ busy: true, closed: false })
    const c = fixture(); await c.store.refresh(); c.store.close(send)
    expect(JSON.parse(send.mock.calls[2]![0] as string)).toMatchObject({ dirty: false, busy: false, closed: true, sequence: 2 })
  })

  it('defers using control traffic without introducing business busy state', async () => {
    const f = fixture(); await f.store.refresh(); f.announce(); await f.store.refresh(); await f.store.defer()
    expect(f.fetch.mock.calls.some(([url, options]) => url.endsWith('/defer') && options?.body === '{}')).toBe(true)
    expect(f.store.getSnapshot().pending).toBe(0)
  })

  it('keeps unmanaged development compatible and does not send invalid session ids', async () => {
    const fetch = vi.fn().mockResolvedValue(json({ managed: false }))
    const store = new DeploymentStore('', fetch, vi.fn(), 'dev-tab')
    await store.refresh(); store.setDirty('dev', true)
    expect(store.getSnapshot().frozen).toBe(false)
    expect(fetch).toHaveBeenCalledTimes(1)
  })

  it('explains maintenance, recovery, and blocked updates without claiming checked SHA is running', async () => {
    const f = fixture(); await f.store.refresh(); f.announce(); await f.store.refresh()
    expect(deploymentMessage(f.store.getSnapshot())).toContain('准备更新')
    f.setStatus({ maintenance: { phase: 'open', epoch: '', blockers: [], operations: [] }, deployment: { phase: 'blocked' } })
    await f.store.refresh()
    expect(deploymentMessage(f.store.getSnapshot())).toContain('技术人员')
  })
})
