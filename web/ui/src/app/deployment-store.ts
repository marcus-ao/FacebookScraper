export const RUNTIME_ID = import.meta.env.VITE_FBSCRAPER_RUNTIME_ID || ''

export interface DeploymentStatus {
  managed: boolean
  sha: string | null
  runtime_id: string | null
  maintenance: null | {
    phase: 'open' | 'announcing' | 'quiesced'
    epoch: string
    deferred_until?: number
    blockers: { session_id?: string; reason: string }[]
    operations: unknown[]
  }
  deployment: {
    phase?: string; current_sha?: string; observed_sha?: string; last_good_sha?: string
    candidate_sha?: string; blocked_sha?: string; error_code?: string; paused?: boolean
  }
}

export interface DeploymentSnapshot {
  status: DeploymentStatus | null
  connected: boolean
  registered: boolean
  dirty: boolean
  pending: number
  frozen: boolean
  conflict: boolean
  notice: string
}

type Transport = (url: string, options?: RequestInit) => Promise<Response>

/** Per-tab memory only. The server retains dirty sessions even when this tab disappears. */
export class DeploymentStore {
  private listeners = new Set<() => void>()
  private sources = new Set<string>()
  private sequence = 0
  private revision = 0
  private generation = 0
  private sending: Promise<void> | null = null
  private desired = false
  private reloaded = false
  private stopped = false
  private statusAt = 0
  private state: DeploymentSnapshot
  readonly sessionId: string
  readonly runtimeId: string
  private transport: Transport
  private reload: () => void

  constructor(
    runtimeId = RUNTIME_ID,
    transport: Transport = (url, options) => fetch(url, options),
    reload: () => void = () => window.location.reload(),
    sessionId: string = globalThis.crypto.randomUUID(),
  ) {
    this.sessionId = sessionId
    this.runtimeId = runtimeId
    this.transport = transport
    this.reload = reload
    this.state = { status: null, connected: !runtimeId, registered: false, dirty: false,
      pending: 0, frozen: !!runtimeId, conflict: false, notice: '' }
  }

  getSnapshot = (): DeploymentSnapshot => this.state
  subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener)
    return () => { this.listeners.delete(listener) }
  }

  private update(patch: Partial<DeploymentSnapshot>) {
    const next = { ...this.state, ...patch }
    const managed = next.status?.managed ?? !!this.runtimeId
    next.frozen = managed && (!next.connected || !next.registered || next.conflict
      || next.status?.maintenance?.phase === 'quiesced'
      || (next.status?.maintenance?.phase === 'announcing' && !next.dirty))
    this.state = next
    // Subscribers enforce the interaction barrier synchronously, before any acknowledgement.
    this.listeners.forEach(listener => listener())
  }

  setDirty(source: string, dirty: boolean) {
    if (this.sources.has(source) === dirty) return
    if (dirty) this.sources.add(source)
    else this.sources.delete(source)
    this.revision++
    this.update({ dirty: this.sources.size > 0 })
    this.reportSoon()
  }

  beginRequest(): () => void {
    this.revision++
    this.update({ pending: this.state.pending + 1 })
    this.reportSoon()
    let ended = false
    return () => {
      if (ended) return
      ended = true
      // Let promise consumers and React's commit record their resulting drafts first.
      setTimeout(() => {
        this.revision++
        this.update({ pending: this.state.pending - 1 })
        this.reportSoon()
        this.reloadIfSafe()
      }, 0)
    }
  }

  interactionsBlocked(): boolean {
    // A suspended foreground tab may resume without a visibility/focus event.
    // Recheck well before the server's 60-second clean-session expiry.
    if (this.state.status?.managed && this.state.connected && Date.now() - this.statusAt > 15_000) {
      this.uncertain()
      void this.refresh()
    }
    return this.state.frozen
  }

  mutationAllowed(): boolean { return !this.interactionsBlocked() }

  canStartEditing(): boolean {
    return !this.interactionsBlocked() && this.state.status?.maintenance?.phase !== 'announcing'
  }

  rejectBusiness(status: number, payload: unknown) {
    const code = payload && typeof payload === 'object' && 'code' in payload ? payload.code : null
    if (status === 409 && code === 'runtime_changed') {
      this.update({ conflict: true, connected: false, notice: '系统版本已更新。当前草稿保留，请先复制草稿；确认放弃或保存完毕后再刷新。' })
    } else if (status === 503 && code === 'maintenance') {
      this.uncertain('系统正在更新，正在重新连接。当前内容仍保留，请勿重复提交。')
    }
  }

  uncertain(notice = '正在重新确认服务状态，暂时暂停操作；当前草稿仍保留。') {
    this.generation++
    this.update({ connected: false, registered: false, notice })
  }

  async refresh(): Promise<void> {
    const generation = ++this.generation
    try {
      const response = await this.transport('/api/deployment/status', { cache: 'no-store', signal: AbortSignal.timeout(4000) })
      if (!response.ok) throw new Error('status unavailable')
      const status = await response.json() as DeploymentStatus
      if (generation !== this.generation || this.stopped) return
      if (!status || typeof status.managed !== 'boolean') throw new Error('invalid status')
      this.statusAt = Date.now()
      const conflict = status.managed && status.runtime_id !== this.runtimeId
      this.update({ status, connected: true, conflict, notice: '',
        registered: status.managed ? this.state.registered : true })
      if (status.managed && this.runtimeId) await this.report()
      else if (status.managed) this.update({ notice: '页面缺少部署版本，请安全刷新页面。' })
      this.reloadIfSafe()
    } catch {
      if (generation === this.generation && !this.stopped) this.uncertain()
    }
  }

  private reportSoon() {
    if (!this.state.status?.managed || this.stopped) return
    this.desired = true
    // Coalesce synchronous dirty/busy transitions; never send an intermediate clean snapshot.
    setTimeout(() => { void this.report() }, 0)
  }

  private snapshot(closed = false) {
    const { dirty, pending, status, connected, frozen } = this.state
    return { session_id: this.sessionId, runtime_id: this.runtimeId, sequence: ++this.sequence,
      dirty, busy: pending > 0, closed: closed && !dirty && pending === 0,
      ack_epoch: connected && Date.now() - this.statusAt <= 15_000 && frozen && !dirty && pending === 0 && status?.maintenance?.phase === 'announcing'
        ? status.maintenance.epoch : null }
  }

  async report(): Promise<void> {
    if (!this.runtimeId || !this.state.status?.managed || this.stopped) return
    this.desired = true
    if (this.sending) return this.sending
    this.sending = (async () => {
      while (this.desired && !this.stopped) {
        this.desired = false
        const revision = this.revision
        const generation = this.generation
        const body = this.snapshot()
        try {
          const response = await this.transport('/api/deployment/session', {
            method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body), signal: AbortSignal.timeout(4000),
          })
          if (!response.ok) throw new Error('session unavailable')
          await response.json()
          if (revision !== this.revision) this.desired = true
          else if (generation === this.generation && this.state.connected) this.update({ registered: true })
        } catch {
          this.desired = false
          this.uncertain()
          break
        }
      }
    })().finally(() => { this.sending = null })
    return this.sending
  }

  async defer(): Promise<void> {
    try {
      const response = await this.transport('/api/deployment/defer', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}', signal: AbortSignal.timeout(4000),
      })
      if (!response.ok) throw new Error('defer unavailable')
      await response.json()
      await this.refresh()
    } catch { this.uncertain('暂缓更新尚未确认，正在重新连接；请保留当前内容。') }
  }

  close(send: (body: string) => void) {
    if (!this.runtimeId || !this.state.status?.managed) return
    this.stopped = true
    send(JSON.stringify(this.snapshot(true)))
  }

  resume() { this.stopped = false; this.uncertain(); return this.refresh() }

  private reloadIfSafe() {
    if (this.state.conflict && this.state.connected && this.state.registered && !this.state.dirty
      && this.state.pending === 0 && this.state.status?.maintenance?.phase === 'open' && !this.reloaded) {
      this.reloaded = true
      this.reload()
    }
  }
}

export const deploymentStore = new DeploymentStore()
