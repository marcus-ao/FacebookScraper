import { useState } from 'react'
import { Alert, App, Button, Input, Modal, Space, Tag } from 'antd'
import type { TaskDetail } from '@/types/domain'
import { idPath, isConflict, putBody, request } from '@/services/http'
import { ConflictRecovery } from '@/components/ConflictRecovery'

export function CategoryEditor({ detail, disabled, apply, refresh }: { detail: TaskDetail; disabled: boolean; apply: (detail: TaskDetail) => void; refresh: () => Promise<TaskDetail> }) {
  const [open, setOpen] = useState(false), [input, setInput] = useState(''), [busy, setBusy] = useState(false), [error, setError] = useState<unknown>(null)
  // 重新读取最新分类走自己的状态，不复用保存的 busy。另外三个冲突恢复入口
  // （正文草稿 useLocalization、排期 useApproval、设置 SettingsPage）本来就是分开的，
  // 只有这里把两件事挤在一个 busy 上：恢复只是重新读一遍，不该让「保存分类」转圈 ——
  // 转圈的应该是恢复按钮自己。少一次 confirmLoading 的开关，也少一次
  // global.css 末尾记的那个 antd loading 图标离场问题的触发机会。
  const [recovering, setRecovering] = useState(false)
  const { message } = App.useApp()
  const save = async () => {
    setBusy(true)
    try { apply(await request<TaskDetail>(`/api/tasks/${idPath(detail.id)}/tags`, putBody({ tags: input.split(/[,，\n]/).map(value => value.trim()).filter(Boolean), tags_revision: detail.tags_revision, source_text_sha256: detail.text.source_text_sha256 }))); setOpen(false); void message.success('分类已保存') }
    catch (cause) { setError(cause) } finally { setBusy(false) }
  }
  return <Space wrap><span>产品分类</span>{detail.tags.length ? detail.tags.map(tag => <Tag key={tag}>{tag}</Tag>) : <span>未分类</span>}{!detail.read_only && <Button type="text" size="small" disabled={disabled} onClick={() => { setInput(detail.tags.join('，')); setError(null); setOpen(true) }}>编辑分类</Button>}
    <Modal title="编辑产品分类" open={open} onCancel={() => { if (!busy && !recovering) setOpen(false) }} okText="保存分类" cancelText="取消" confirmLoading={busy} okButtonProps={{ disabled: recovering }} onOk={() => void save()}>
      <label>多个分类用逗号隔开；清空后归为未分类。<Input.TextArea aria-label="产品分类" value={input} onChange={event => setInput(event.target.value)} disabled={busy || recovering} rows={2} /></label>
      {error ? isConflict(error) ? <ConflictRecovery kind="tags" recovering={recovering} onRecover={() => { setRecovering(true); void refresh().then(() => setError(null)).catch(setError).finally(() => setRecovering(false)) }} /> : <Alert type="error" title="分类未保存，请重试" /> : null}
    </Modal>
  </Space>
}
