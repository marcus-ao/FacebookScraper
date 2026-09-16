import { useState } from 'react'
import { Link } from 'react-router'
import { Alert, Button, Collapse, Empty, Popover, Space, Spin, Tag, Tooltip, Typography } from 'antd'
import { useQueryClient } from '@tanstack/react-query'
import { PageTitle } from '@/app/PageTitle'
import { calendarOptions, useCalendar } from '@/hooks/useCalendar'
import { refreshCalendar } from '@/services/calendar'
import { idPath, isApiError } from '@/services/http'
import type { CalendarPayload } from '@/types/domain'
import { BusinessTime } from '@/components/Time'
import { DisabledReason } from '@/components/DisabledReason'
import { zonedInput, businessToday, calendarDays } from '@/lib/format'
import { cx } from '@/lib/css'
import styles from './CalendarPage.module.css'

export function CalendarPage() {
  const query = useCalendar(), client = useQueryClient()
  const [busy, setBusy] = useState(false), [failed, setFailed] = useState(false)
  const data = query.data
  const today = businessToday(data?.business_timezone)
  const refresh = async () => {
    setBusy(true); setFailed(false)
    try { client.setQueryData(calendarOptions().queryKey, await refreshCalendar()) }
    catch (error) { setFailed(true); if (isApiError(error) && error.payload && typeof error.payload === 'object' && 'cards' in error.payload && Array.isArray(error.payload.cards)) {
      const fallback = error.payload as Partial<CalendarPayload>
      if (data) client.setQueryData(calendarOptions().queryKey, { ...data, ...fallback })
    } } finally { setBusy(false) }
  }
  const show = data?.cached_at && (data.coverage.matches_current_month || (failed && data.cards.length > 0))
  return <section aria-label="发布月历" aria-busy={busy}>
    <div className={styles.heading}><PageTitle />{data?.refresh_available
      ? <Tooltip title="会打开发布浏览器读取后台，通常需要数十秒"><Button loading={busy} onClick={() => void refresh()}>刷新月历</Button></Tooltip>
      : <DisabledReason label="刷新月历" reason="发布日历读取条件尚未满足，请查看核验信息"><Button loading={busy} disabled>刷新月历</Button></DisabledReason>}</div>
    <p className={styles.help}>以北京时间查看后台的排期和已发布内容，也包含人工创建的帖子。每张卡同时标出德国受众那边的钟点。</p>
    <div className={styles.legend} aria-label="图例">
      <Tag color="processing">实线 · 后台读到的</Tag>
      <Tag>虚线 · 系统本地记录</Tag>
      <Typography.Text type="secondary">空档只由后台数据判定；本地记录只作展示。</Typography.Text>
    </div>
    <Space wrap><Typography.Text type="secondary">{data?.month_ui} · 数据截至 <BusinessTime at={data?.cached_at ? zonedInput(data.cached_at) : null} fallback="尚未读取" /></Typography.Text>{data?.stale && <Tag>数据可能已过期</Tag>}{data?.cached_at && <span>{data.cards.length} 条可见记录</span>}</Space>
    {(failed || query.error || data?.error) && <Alert type="warning" title="本次月历未完整更新，仍展示已取得的记录，请稍后重试" />}
    {data?.status === 'partial' && <Alert type="warning" title="部分帖子的渠道尚未识别，当前无法可靠判断可用时刻" />}
    <Spin spinning={busy || query.isPending}>
      {show && data ? <div className={styles.grid}>
        {['周一','周二','周三','周四','周五','周六','周日'].map(day => <div key={day} className={styles.weekday}>{day}</div>)}
        {calendarDays(data.display_start, data.display_end_exclusive).map((day, index) => <div key={day ?? `pad-${index}`} className={cx(styles.day, day === today && styles.today)} {...(day ? { 'data-day': day } : {})} {...(day === today ? { 'data-today': '' } : {})}>
          {day && <><div className={styles.date}>{day.slice(5).replace('-', '/')} {day === today && <span className={styles.todayMark}>今天</span>} {day.slice(0, 7) !== data.month_ui && '· 跨月时差'}</div>
            {data.cards.filter(card => card.at_business.slice(0, 10) === day).map((card, i) => <Popover key={`${card.card_sha256}-${i}`} trigger={['click']} title={`${card.at_business.slice(0,16).replace('T',' ')} 北京`} content={<div className={styles.caption}><p>{card.rendered || '正文未提供'}</p>{card.audience && <p className={styles.audience}>德国 {card.audience.at.slice(5, 16).replace('T', ' ')}</p>}</div>}>
              <button type="button" className={styles.card}><strong>{card.at_business.slice(11,16)} 北京</strong>{card.audience && <span className={styles.audience}>德国 {card.audience.at.slice(11, 16)}</span>}<span>{card.channels.length ? card.channels.map(channel => channel === 'facebook' ? 'Facebook' : 'Instagram').join(' / ') : '渠道待确认'}</span>
                <Tag color={card.delivery === 'published' ? 'success' : card.delivery === 'scheduled' ? 'processing' : 'default'}>{card.delivery === 'published' ? '已观测到公开发布' : card.delivery === 'scheduled' ? '已创建定时任务' : '发布状态待核验'}</Tag></button>
            </Popover>)}
            {/* 本地图层：系统自己知道的，还没从后台读回来 */}
            {data.local.filter(item => item.at_business?.slice(0, 10) === day).map(item => <Link key={item.task_id} to={`/review/${idPath(item.task_id)}?platform=${item.platform}`} className={cx(styles.card, styles.localCard)}>
              <strong>{item.at_business?.slice(11, 16)} 北京</strong>{item.audience && <span className={styles.audience}>德国 {item.audience.at.slice(11, 16)}</span>}
              <span>{item.platform === 'facebook' ? 'Facebook' : 'Instagram'}</span>
              <Tag>{item.kind === 'scheduled' ? '本地记为已排期' : '已提交，待后台回读'}</Tag></Link>)}
          </>}
        </div>)}
      </div> : !query.isPending && <Empty description={!data?.cached_at ? '还没有读取过发布日历' : '当前缓存没有覆盖本月；上次数据已保留'} />}
    </Spin>
    {show && data?.cards.length === 0 && <p className={styles.help}>本次读取的月份中没有排期记录。</p>}
    {/* 冻结但还没选时刻的落不到任何一天，单独列出来，否则它就从视野里消失了。 */}
    {data && data.local.some(item => !item.at_business) && <div className={styles.help}>
      <strong>已冻结、等着选发布时间：</strong>
      <Space wrap>{data.local.filter(item => !item.at_business).map(item =>
        <Link key={item.task_id} to={`/review/${idPath(item.task_id)}?platform=${item.platform}`}>
          {item.platform === 'facebook' ? 'Facebook' : 'Instagram'} · {item.task_id.split('/').pop()}
        </Link>)}</Space>
    </div>}
    {data?.local_error && <Alert type="error" title="本地排期记录读不出来" description={<>{data.local_error}<br />⛔ 这不等于「本地没有排期」；在核对清楚之前，不要拿这个月历判断哪些时刻空着。</>} />}
    <p className={styles.help}>月份按发布后台的美西自然月计算；北京比美西快 15–16 小时，所以北京 10 月 1 日白天那一段仍属于上一个发布月份。选时提示来自缓存，正式排期前会再次读取后台核对。</p>
    {data && <Collapse ghost items={[{ key: 'coverage', label: '月历覆盖与核验信息', children: <pre className={styles.diagnostic}>{JSON.stringify({ coverage: data.coverage, bounds: data.bounds, refresh_unavailable_reason: data.refresh_unavailable_reason, error: data.error, gap_minutes: data.gap_minutes }, null, 2)}</pre> }]} />}
  </section>
}
