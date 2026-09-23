import { useState } from 'react'
import { Alert, App, Button, Card, Collapse, Form, Input, InputNumber, Space, Spin, Tooltip, Typography } from 'antd'
import { PageTitle } from '@/app/PageTitle'
import { useSettings } from '@/hooks/useSettings'
import { useDeploymentDraft } from '@/hooks/useDeploymentDraft'
import { getSettings, saveSettings } from '@/services/settings'
import { isConflict } from '@/services/http'
import { ConflictRecovery } from '@/components/ConflictRecovery'
import type { EditableSettings, OperatingSettings } from '@/types/domain'
import styles from './SettingsPage.module.css'

export function validateSettings(values: EditableSettings): string {
  if (!values.default_times.length || values.default_times.length > 12) return '请填写 1 至 12 个常用时刻'
  if (values.default_times.some(time => !/^([01]\d|2[0-3]):[0-5]\d$/.test(time))) return '每个时刻请使用 HH:MM，例如 10:00'
  if (new Set(values.default_times).size !== values.default_times.length) return '常用时刻不能重复'
  if (!Number.isInteger(values.snooze_default_days) || values.snooze_default_days < 1 || values.snooze_default_days > 30) return '挂起期限须为 1 至 30 个工作日'
  return ''
}
const names: Record<string, string> = { targets: '监测来源账号', publish_identity: '发布账号核验名', price_map: '价格映射', trusted_owners: '信任名单', brand_accounts: '品牌自有账号', frozen_sources: '冻结来源', pipeline: '处理方式与预算', delta: '抓取控制' }

export function SettingsPage() {
  const query = useSettings()
  return <section className={styles.page}><PageTitle /><p className={styles.help}>默认排期时刻供下次选期使用；挂起期限按上海工作日计算。</p>
    {query.data ? <SettingsForm initial={query.data} /> : query.isPending ? <Spin description="正在读取设置…" /> : <Alert type="error" title="设置暂时不可读" action={<Button onClick={() => void query.refetch()}>重试</Button>} />}
  </section>
}

function SettingsForm({ initial }: { initial: OperatingSettings }) {
  const [base, setBase] = useState(initial), [times, setTimes] = useState(initial.editable.default_times.join(', ')), [days, setDays] = useState<number | null>(initial.editable.snooze_default_days)
  const [busy, setBusy] = useState(false), [error, setError] = useState<unknown>(null), [saved, setSaved] = useState(false)
  const { modal } = App.useApp()
  const values = { default_times: times.split(/[,，\s]+/).filter(Boolean), snooze_default_days: days ?? 0 }
  const dirty = JSON.stringify(values) !== JSON.stringify(base.editable), invalid = validateSettings(values)
  useDeploymentDraft(dirty)
  const apply = (data: OperatingSettings) => { setBase(data); setTimes(data.editable.default_times.join(', ')); setDays(data.editable.snooze_default_days) }
  const recover = async (keep: boolean) => {
    setBusy(true)
    try { const latest = await getSettings(); if (keep) setBase(latest); else apply(latest); setError(null) }
    catch (cause) { setError(cause) } finally { setBusy(false) }
  }
  const save = async () => {
    if (invalid || busy) return
    setBusy(true); setError(null); setSaved(false)
    try { apply(await saveSettings(values, base.version)); setSaved(true) }
    catch (cause) { setError(cause) } finally { setBusy(false) }
  }
  return <>
    {error ? isConflict(error) ? <ConflictRecovery kind="settings" recovering={busy} onRecover={() => void recover(true)} /> : <Alert type="error" title="设置未保存，已保留本次修改，请重试" /> : null}
    {saved && !dirty && <p role="status">设置已保存，下次选期或挂起时生效。</p>}
    <Card size="small" title="日常运营设置"><Form layout="vertical" onFinish={() => void save()} className={styles.form ?? ''} disabled={busy}>
      <Form.Item label="默认排期时间（北京）" required extra="用逗号分隔 1 至 12 个时刻，例如 16:00, 23:00（≈柏林 10:00/17:00）。已有排期保持原时刻。"><Input aria-label="默认排期时间（北京）" value={times} onChange={event => setTimes(event.target.value)} /></Form.Item>
      <Form.Item label="默认挂起期限" required extra="1 至 30 个上海工作日，周一至周五；到期后回到待我审。"><InputNumber aria-label="默认挂起期限" min={1} max={30} step={1} value={days} onChange={setDays} addonAfter="工作日" /></Form.Item>
      {dirty && invalid && <p className={styles.help} role="status">{invalid}</p>}
      <Space><Tooltip title={invalid || (!dirty ? '当前没有未保存的修改' : '')}><span><Button type="primary" htmlType="submit" loading={busy} disabled={!dirty || !!invalid}>保存设置</Button></span></Tooltip>
        <Button disabled={busy} onClick={() => { if (dirty) modal.confirm({ title: '设置尚未保存，离开会放弃这次修改。继续离开？', okText: '放弃并重新读取', cancelText: '保留修改', onOk: () => recover(false) }); else void recover(false) }}>重新读取</Button></Space>
    </Form></Card>
    <div className={styles.controlled}><h2>系统配置（只读）</h2><Typography.Paragraph type="secondary">以下信息供维护时核对。</Typography.Paragraph>
      <Collapse items={[...Object.entries(base.controlled).map(([key, value]) => ({ key, label: names[key] ?? '其他配置', children: base.controlled_fields[key]?.length ? <dl>{base.controlled_fields[key]?.map(field => <div key={field.key}><dt>{field.key}</dt><dd>{typeof field.value === 'string' ? field.value : JSON.stringify(field.value)}</dd>{field.help && <dd>{field.help}</dd>}</div>)}</dl> : <pre>{JSON.stringify(value, null, 2)}</pre> })), { key: 'editable_help', label: '运营字段的配置说明', children: <pre>{JSON.stringify(base.editable_help, null, 2)}</pre> }]} />
    </div>
  </>
}
