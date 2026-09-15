import { useState } from 'react'
import { Alert, App, Badge, Button, Checkbox, Collapse, Drawer, Input, Modal, Space, Spin, Typography } from 'antd'
import { useQueryClient } from '@tanstack/react-query'
import { Link } from 'react-router'
import { PageTitle } from '@/app/PageTitle'
import { useRuntime } from '@/hooks/useRuntime'
import { runtimeSignature, stageSummary } from '@/features/runtime/model'
import { getRuntime, recoverProcessing, resolveNotification } from '@/services/runtime'
import { idPath, isConflict } from '@/services/http'
import { ShanghaiTime } from '@/components/Time'
import type { FeishuDelivery, RuntimeSnapshot } from '@/types/domain'
import styles from './RuntimePage.module.css'

export function RuntimePage() {
  const query = useRuntime()
  return <section><PageTitle />{query.data ? <RuntimeView initial={query.data} current={query.data} /> : query.isPending ? <Spin description="正在读取状态…" /> : <Alert type="warning" title="运行状态暂时不可读" action={<Button onClick={() => void query.refetch()}>重试</Button>} />}</section>
}
function RuntimeView({ initial, current }: { initial: RuntimeSnapshot; current: RuntimeSnapshot }) {
  const [data, setData] = useState(initial), [busy, setBusy] = useState(false), [error, setError] = useState<unknown>(null)
  const [delivery, setDelivery] = useState<FeishuDelivery | null>(null), [messageId, setMessageId] = useState(''), [maintenance, setMaintenance] = useState(false)
  const { modal } = App.useApp(), client = useQueryClient()
  const batch = data.business.processing
  const load = async () => { const latest = await getRuntime(); client.setQueryData(['runtime'], latest); setData(latest); setError(null) }
  const action = async (operation: () => Promise<unknown>) => { setBusy(true); setError(null); try { await operation(); await load() } catch (cause) { setError(cause); throw cause } finally { setBusy(false) } }
  const deliveries = data.stages.flatMap(stage => stage.outbox?.deliveries ?? []).filter(item => ['retry','uncertain'].includes(item.status))
  const unconfirmed = data.stages.find(stage => stage.number === 5)?.unconfirmed_attempts
  const interrupted = ['interrupted','uncertain'].includes(batch.status ?? '')
  const resend = (item: FeishuDelivery) => {
    let group = '', summary = '', checked = false
    const dialog = modal.confirm({ title: '核对未送达后恢复 · 会重新发送飞书消息', okText: '确认未送达并恢复投递', cancelText: '取消', okButtonProps: { disabled: true },
      content: <ResendContext onChange={(g, s, c) => { group = g; summary = s; checked = c; dialog.update({ okButtonProps: { disabled: !group.trim() || !summary.trim() || !checked } }) }} />,
      onOk: () => action(() => resolveNotification(item, 'not_delivered')) })
  }
  const closeBatch = () => modal.confirm({ title: '核对后关闭中断批次', okText: '已核对，关闭批次', cancelText: '继续核对', content: <><p>{batch.paid_request_ids.length ? '请先核对本批次的费用、已保存文案和图片。确认后只关闭中断批次，不会再次调用模型。' : '关闭这个未完成批次？本次操作不会调用模型。'}</p><p>{batch.paid_request_ids.length} 次已记录请求 · US${batch.cost_usd.toFixed(4)}</p></>, onOk: () => action(() => recoverProcessing(batch)) })
  return <>
    <div className={styles.toolbar}><Typography.Text type="secondary">记录于 <ShanghaiTime at={data.observed_at} /> · 每 30 秒核对状态</Typography.Text><Button loading={busy} onClick={() => { setBusy(true); void load().catch(setError).finally(() => setBusy(false)) }}>刷新状态</Button></div>
    {runtimeSignature(current) !== runtimeSignature(data) && <Alert type="info" title="运行状态有更新，点击刷新后查看" />}
    {error ? <Alert type="warning" title={isConflict(error) ? '状态已在别处更新，请刷新后重新核对' : '本次操作未完成，请刷新状态核对'} /> : null}
    <section className={styles.attention} aria-label="需要你处理"><h2>需要你处理</h2>
      {!deliveries.length && !interrupted && !unconfirmed && <p>当前没有可直接恢复的事项；阶段条件请见下方。</p>}
      {interrupted && <p>一批内容处理已中断。 <Button disabled={busy || !batch.batch_id || !batch.state_revision} onClick={closeBatch}>核对后关闭中断批次</Button></p>}
      {deliveries.map((item, index) => <div key={item.delivery_id} className={styles.delivery}><Space wrap><span>提醒 {index + 1} · {item.status === 'uncertain' ? '送达结果待核对' : '等待重试'}</span><ShanghaiTime at={item.created_at} />
        <Button disabled={busy} onClick={() => { setMessageId(''); setDelivery(item) }}>登记已送达</Button><Button disabled={busy} onClick={() => resend(item)}>核对未送达后恢复</Button></Space>
        {item.preview_error && <p>这张卡片发出时未能重新读取当前德语稿，内容可能不是最新，请进入审校页核对。</p>}
        <Space wrap>{item.task_ids?.filter((id): id is NonNullable<typeof id> => !!id).map((id, i) => <Link key={id} to={`/review/${idPath(id)}`}>查看相关帖子 {i + 1}</Link>)}</Space>
      </div>)}
      {!!unconfirmed && <p>{unconfirmed} 次提交结果需要核对。当前记录只有计数，暂时无法直接定位帖子。 <Button type="link" onClick={() => setMaintenance(true)}>查看维护说明</Button></p>}
    </section>
    <div className={styles.facts}><span>调度进程：{data.process.alive === true ? '活跃' : data.process.alive === false ? '已退出' : '尚未确认'}</span><span>最近完整业务处理成功：<ShanghaiTime at={data.business.last_successful_run} fallback="尚无记录" /></span></div>
    <section aria-label="五阶段概览">{data.stages.map(stage => { const summary = stageSummary(stage); return <article key={stage.number} className={styles.stage}>
      <div className={styles.stageHeading}><h2>{stage.number}. {stage.name}</h2><Badge status={summary.tone} text={summary.label} /></div><p>{summary.conclusion}</p>
      {stage.number === 3 && <p className={styles.help}>本轮开始 <ShanghaiTime at={batch.started_at} /> · 完成 <ShanghaiTime at={batch.finished_at} />{stage.trends_export?.status === 'blocked' && ' · 趋势采样已暂停，仍可使用语义候选。'}</p>}
      {stage.number === 4 && stage.outbox?.enabled && stage.outbox.credentials_present === false && <p>两个飞书群的机器人地址尚未配置完整，请联系维护人员。</p>}
      {stage.number === 4 && stage.outbox?.groups_merged && <p className={styles.help}>业务组与技术组当前指向同一个群，系统告警会和待审提醒混在一起。</p>}
      {stage.number === 5 && <p className={styles.help}>Facebook 单渠道验收：{stage.acceptance?.facebook?.verified ? '真实通过' : '尚未完成'} · Instagram 单渠道验收：{stage.acceptance?.instagram?.verified ? '真实通过' : '尚未完成'}</p>}
      <Collapse ghost items={[{ key: 'details', label: '查看技术细节', children: <pre className={styles.diagnostic}>{JSON.stringify({ ...stage, ...(stage.number === 3 ? { processing: batch, timings: data.business.timings } : {}) }, null, 2)}</pre> }]} />
    </article> })}</section>
    <Collapse ghost items={[{ key: 'runtime', label: '进程、网络与外部心跳记录', children: <pre className={styles.diagnostic}>{JSON.stringify({ process: data.process, activation: data.activation, heartbeat: data.heartbeat, network: data.network }, null, 2)}</pre> }]} />
    <Modal title="登记已送达" open={!!delivery} okText="登记已送达" cancelText="取消" confirmLoading={busy} okButtonProps={{ disabled: !messageId.trim() }} onCancel={() => { if (!busy) setDelivery(null) }} onOk={() => { if (delivery) void action(() => resolveNotification(delivery, 'delivered', messageId)).then(() => setDelivery(null)).catch(() => {}) }}>
      <label>群机器人不返回消息 ID，请填写你在群里核对到的情况<Input autoFocus aria-label="送达核对说明" placeholder="例如：业务群 21:07 已收到监测卡" value={messageId} maxLength={200} onChange={event => setMessageId(event.target.value)} /></label>
    </Modal>
    <Drawer title="未确认发布的维护说明" open={maintenance} onClose={() => setMaintenance(false)} size="large"><p>请由维护人员核对发布尝试记录，找出未确认的帖子，再打开该篇审核页的“核对并补齐本地回执”。当前接口未提供帖子列表，请勿根据数量推断具体帖子。</p><p>核对本地发布记录和对应的远端排期回读结果后再处理，避免重复提交。</p><pre className={styles.diagnostic}>publish/journal.py · pipeline/runtime_status.py</pre></Drawer>
  </>
}

function ResendContext({ onChange }: { onChange: (group: string, summary: string, checked: boolean) => void }) {
  const [group, setGroup] = useState(''), [summary, setSummary] = useState(''), [checked, setChecked] = useState(false)
  return <><p>确认后将恢复原消息向原接收对象的投递。当前状态记录未提供接收对象和正文，请先在飞书核对并填写以下信息。</p>
    <label>已核对的接收组<Input autoFocus aria-label="已核对的接收组" value={group} onChange={event => { setGroup(event.target.value); onChange(event.target.value, summary, checked) }} /></label>
    <label>原消息内容摘要<Input.TextArea aria-label="原消息内容摘要" value={summary} onChange={event => { setSummary(event.target.value); onChange(group, event.target.value, checked) }} /></label>
    <Checkbox checked={checked} onChange={event => { setChecked(event.target.checked); onChange(group, summary, event.target.checked) }}>已在飞书核对未送达，确认接收组和摘要无误</Checkbox>
  </>
}
