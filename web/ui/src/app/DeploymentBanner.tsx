import { useLayoutEffect, useSyncExternalStore } from 'react'
import { Button, Space } from 'antd'
import { Link } from 'react-router'
import { deploymentStore } from './deployment-store'
import type { DeploymentSnapshot } from './deployment-store'
import styles from './AppShell.module.css'

export function deploymentMessage(state: DeploymentSnapshot): string {
  if (state.notice) return state.notice
  if (!state.connected) return '系统正在更新或连接中断，正在重新连接；当前内容仍保留。'
  if (state.conflict) return state.dirty || state.pending
    ? '系统版本已更新。请先复制未保存草稿，确认放弃草稿后再刷新；当前页面不会自动刷新。'
    : '系统已更新，正在安全刷新页面。'
  if (!state.registered) return '正在登记本页编辑状态，稍后即可操作。'
  const status = state.status
  if (status?.maintenance?.phase === 'quiesced') return '系统正在更新，请稍候；页面内容已保留。'
  if (status?.deployment.phase === 'blocked') return status.maintenance?.phase === 'open'
    ? '当前版本仍可用；本次更新需要技术人员处理。'
    : '更新需要技术人员处理，请保留当前页面。'
  if (status?.maintenance?.phase === 'open' && status.deployment.error_code
    && ['candidate_start_failed', 'candidate_health_failed', 'controller_interrupted_switch', 'operator_retry_recovery', 'running_worker_exited'].includes(status.deployment.error_code)) {
    return '已恢复旧版本，当前服务可用；本次更新需要技术人员核对。'
  }
  if (status?.deployment.blocked_sha && status.deployment.error_code) return '本次更新未完成，当前版本仍保留；需要技术人员处理。'
  if (status?.maintenance?.phase === 'announcing') return state.dirty || state.pending
    ? '准备更新，正在等待编辑或操作完成。请保存当前草稿，或暂缓 30 分钟。'
    : '准备更新，本页已暂停操作，正在等待其他页面或任务完成。'
  if (status?.deployment.phase === 'waiting') return '更新正在等待任务完成或暂缓时间结束。'
  if (status?.deployment.candidate_sha) return '正在准备更新，当前版本仍可使用。'
  return status?.managed ? '当前服务版本已就绪。' : ''
}

/** Also gates body-level Ant Design portals, which are outside the shell content. */
export function installDeploymentBarrier(doc: Document = document): () => void {
  const originals = new Map<HTMLElement, boolean>()
  const apply = () => {
    const state = deploymentStore.getSnapshot()
    const frozen = state.frozen && !state.dirty
    for (const node of doc.querySelectorAll<HTMLElement>('[data-deployment-content], .ant-modal-root, .ant-drawer, .ant-select-dropdown, .ant-picker-dropdown, .ant-popover')) {
      if (node.matches('.deployment-unsaved-dialog') || node.closest('.deployment-unsaved-dialog')) continue
      if (frozen && !originals.has(node)) { originals.set(node, node.inert); node.inert = true }
    }
    if (!frozen) {
      originals.forEach((inert, node) => { node.inert = inert })
      originals.clear()
    }
  }
  const guard = (event: Event) => {
    if (!deploymentStore.interactionsBlocked()) return
    const target = event.target
    if (target instanceof Element && target.closest('[data-deployment-controls]')) return
    if (target instanceof Element && target.closest('.deployment-unsaved-dialog')) return
    if (deploymentStore.getSnapshot().dirty && (target instanceof HTMLTextAreaElement
      || (target instanceof HTMLInputElement && ['text', 'url'].includes(target.type)))
      && ['pointerdown', 'click'].includes(event.type)) return
    // Preserve copying text while preventing keyboard shortcuts and portal edits.
    if (event instanceof KeyboardEvent && (event.ctrlKey || event.metaKey) && ['c', 'a'].includes(event.key.toLowerCase())) return
    event.preventDefault()
    event.stopImmediatePropagation()
  }
  const events = ['click', 'pointerdown', 'keydown', 'beforeinput', 'input', 'change', 'submit', 'drop', 'paste']
  events.forEach(name => doc.addEventListener(name, guard, true))
  const unsubscribe = deploymentStore.subscribe(apply)
  const observer = new MutationObserver(apply)
  observer.observe(doc.body, { childList: true, subtree: true })
  apply()
  return () => {
    unsubscribe(); observer.disconnect()
    events.forEach(name => doc.removeEventListener(name, guard, true))
    originals.forEach((inert, node) => { node.inert = inert })
  }
}

export function DeploymentBanner() {
  const state = useSyncExternalStore(deploymentStore.subscribe, deploymentStore.getSnapshot, deploymentStore.getSnapshot)
  useLayoutEffect(() => {
    const removeBarrier = installDeploymentBarrier()
    void deploymentStore.resume()
    const timer = window.setInterval(() => { if (!document.hidden) void deploymentStore.refresh() }, 5000)
    const reconnect = () => { void deploymentStore.resume() }
    const visibility = () => { deploymentStore.uncertain(); if (!document.hidden) reconnect() }
    const offline = () => deploymentStore.uncertain()
    const close = () => deploymentStore.close(body => {
      navigator.sendBeacon('/api/deployment/session', new Blob([body], { type: 'application/json' }))
    })
    window.addEventListener('focus', reconnect)
    window.addEventListener('online', reconnect)
    window.addEventListener('offline', offline)
    window.addEventListener('pageshow', reconnect)
    window.addEventListener('pagehide', close)
    document.addEventListener('visibilitychange', visibility)
    return () => {
      window.clearInterval(timer); removeBarrier()
      window.removeEventListener('focus', reconnect)
      window.removeEventListener('online', reconnect)
      window.removeEventListener('offline', offline)
      window.removeEventListener('pageshow', reconnect)
      window.removeEventListener('pagehide', close)
      document.removeEventListener('visibilitychange', visibility)
    }
  }, [])
  const message = deploymentMessage(state)
  if ((!state.status?.managed && !deploymentStore.runtimeId) || !message) return null
  const status = state.status
  const defer = status?.maintenance?.phase !== 'quiesced'
    && (status?.maintenance?.phase === 'announcing' || status?.deployment.phase === 'waiting')
  return <aside data-deployment-controls className={styles.deployment} aria-label="系统更新">
    <Space wrap><span role="status">{message}</span>
      {defer && <Button size="small" onClick={() => void deploymentStore.defer()}>暂缓 30 分钟</Button>}
      <Link to="/runtime">查看运行状态</Link>
    </Space>
    <details><summary>更新详情</summary>
      <div>实际运行版本：{status?.sha ?? '等待确认'}；最近检查的 GitHub 版本：{status?.deployment.observed_sha ?? '尚未检查'}</div>
      <div>候选版本：{status?.deployment.candidate_sha ?? '无'}；上次可用版本：{status?.deployment.last_good_sha ?? '等待确认'}</div>
      {status?.deployment.error_code && <div>需核对：{status.deployment.error_code}</div>}
      {status?.maintenance?.blockers.map((blocker, index) => <div key={index}>等待原因：{blocker.reason}</div>)}
    </details>
  </aside>
}
