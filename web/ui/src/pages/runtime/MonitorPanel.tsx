import { useState } from 'react'
import { Alert, Button, Input, Modal, Space, Table } from 'antd'
import { Link, useSearchParams } from 'react-router'
import { ShanghaiTime } from '@/components/Time'
import { recoverCapture, recoverMonitor } from '@/services/runtime'
import type { CaptureItem, MonitorStatus } from '@/types/domain'

const categories: Record<string, string> = { new: '新发布', historical: '历史补获', source_updated: '源帖更新', recovered: '补齐完成', time_unknown: '时间待核对' }

export function MonitorPanel({ data, busy, action }: { data: MonitorStatus | undefined; busy: boolean; action: (operation: () => Promise<unknown>) => Promise<void> }) {
  const [params] = useSearchParams()
  const [selected, setSelected] = useState<CaptureItem | 'monitor' | null>(null), [reason, setReason] = useState('')
  if (!data) return <Alert type="info" title="此服务版本没有提供监测完整性信息" />
  const key = params.get('capture'), scan = params.get('scan')
  const items = data.items.filter(item => key ? item.key === key : scan ? item.scan_id === scan : true)
  const open = (item: CaptureItem | 'monitor') => { setSelected(item); setReason('') }
  const recover = () => {
    const operation = selected === 'monitor'
      ? () => recoverMonitor(data.revision!, reason)
      : () => recoverCapture((selected as CaptureItem).key, data.capture_revision!, reason)
    void action(operation).then(() => setSelected(null)).catch(() => {})
  }
  return <section aria-label="新帖采集状态"><h2>新帖采集状态</h2>
    {data.reason && <Alert type={data.status === 'ready' ? 'info' : 'warning'} title={data.reason} />}
    <p>北京时间每天 08:00–19:00 间隔 45–75 分钟，离岗间隔 135–225 分钟；06:30–07:30 一次受限补扫。</p>
    {Object.entries(data.platforms).map(([platform, state]) => <p key={platform}>{platform}：{state.paused ? '已暂停' : '按计划监测'} · 下次主页访问 <ShanghaiTime at={state.next_due_at} /> · 过去 24 小时主页 {state.homepage_used}/{state.homepage_limit} 次，详情 {state.detail_used}/{state.detail_limit} 次{state.reason && <> · {state.reason}</>}</p>)}
    {Object.entries(data.baselines).map(([platform, baseline]) => <p key={platform}>{platform} 基线：最近 {baseline.lookback_days} 天已核对 {baseline.recent_count} 篇 · 启用 <ShanghaiTime at={baseline.enabled_at} /></p>)}
    {data.revision !== null && (data.status === 'paused' || Object.values(data.platforms).some(p => p.paused)) && <Button disabled={busy} onClick={() => open('monitor')}>登记处理说明并恢复监测</Button>}
    {(key || scan) && <p><Link to="/runtime">查看全部采集记录</Link></p>}
    {/* ⚠️ pageSize 是受控值，会盖掉条数选择；defaultPageSize 只定初始 10 条。 */}
    <Table<CaptureItem> rowKey="key" size="small" dataSource={[...items].reverse()} scroll={{ x: 820 }}
      pagination={{ defaultPageSize: 10, showSizeChanger: true, pageSizeOptions: [10, 20, 50, 100] }} columns={[
      { title: '原帖', render: (_, item) => <>{item.platform} · {item.post_id}<br />{categories[item.classification] ?? '时间待核对'}</> },
      { title: '结果', render: (_, item) => <>{item.status === 'complete' ? '完整' : item.status === 'manual' ? '待人工' : item.status === 'deferred' ? '尚未开始，等待后续扫描' : '采集中'}<br />已校验 {item.saved_images ?? '未知'} / {item.source_media_count ?? '总数待核对'} 张{item.reason && <p>{item.reason}</p>}</> },
      { title: '发现 / 结束（北京时间）', render: (_, item) => <><ShanghaiTime at={item.first_seen_at} /><br /><ShanghaiTime at={item.finished_at} /><br />发现等待 {item.discovery_wait_seconds == null ? '未知' : `${Math.round(item.discovery_wait_seconds / 60)} 分钟`} · 本次抓取 {item.capture_seconds == null ? '未知' : `${Math.round(item.capture_seconds)} 秒`}</> },
      { title: '操作', render: (_, item) => <Space wrap>{item.archived && <Link to={`/history/${encodeURIComponent(item.account_dir)}/${encodeURIComponent(item.post_id)}`}>查看已存原帖</Link>}{item.permalink && <a href={item.permalink} target="_blank" rel="noreferrer">查看源帖</a>}{item.status === 'manual' && <Button disabled={busy || data.capture_revision === null} onClick={() => open(item)}>处理后尝试一次</Button>}</Space> },
    ]} />
    <Modal open={selected !== null} title={selected === 'monitor' ? '恢复监测' : '人工恢复这篇采集'} okText={selected === 'monitor' ? '登记并恢复' : '确认采集一次'} cancelText="取消" confirmLoading={busy} okButtonProps={{ disabled: !reason.trim() }} onOk={recover} onCancel={() => { if (!busy) setSelected(null) }}>
      <p>{selected === 'monitor' ? '登记你已处理的问题。恢复保留访问额度，下次访问仍按计划执行。' : '确认后对该帖执行一次采集，保留已有正文、图片和人工修改；仍受平台停机状态与访问额度约束。'}</p>
      <label>处理说明<Input.TextArea autoFocus value={reason} maxLength={1000} onChange={event => setReason(event.target.value)} /></label>
    </Modal>
  </section>
}
