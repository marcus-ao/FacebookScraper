import { useState } from 'react'
import { Link } from 'react-router'
import { Alert, Button, Empty, Popover, Spin, Tag, Tooltip, Typography } from 'antd'
import { useQueryClient } from '@tanstack/react-query'
import { PageTitle } from '@/app/PageTitle'
import { calendarOptions, useCalendar } from '@/hooks/useCalendar'
import { refreshCalendar } from '@/services/calendar'
import { idPath, isApiError } from '@/services/http'
import type { CalendarCard, CalendarPayload } from '@/types/domain'
import { BusinessTime } from '@/components/Time'
import { DisabledReason } from '@/components/DisabledReason'
import { zonedInput, businessToday, calendarDays } from '@/lib/format'
import { cx } from '@/lib/css'
import styles from './CalendarPage.module.css'

/** 卡片状态用短文案，颜色承载可信度；明细没读出来的卡追加警告。 */
function cardTags(card: CalendarCard, unread: boolean) {
  const delivery = ({ published: '已发布', scheduled: '定时', draft: '草稿', failed: '发布失败', processing: '处理中' } as Record<string, string>)[card.delivery] ?? '待核验'
  const color = card.delivery === 'published' ? 'success' : card.delivery === 'scheduled' ? 'processing' : 'default'
  return <>
    <Tag color={color}>{delivery}</Tag>
    {unread && <Tag color="warning">未读全</Tag>}
  </>
}

function accountNames(card: CalendarCard): string {
  return card.accounts ? [...new Set(Object.values(card.accounts).filter(Boolean))].join(' / ') : ''
}

export function CalendarPage() {
  const query = useCalendar(), client = useQueryClient()
  const [busy, setBusy] = useState(false), [failed, setFailed] = useState(false)
  const data = query.data
  const visibleCards = data?.cards ?? []
  const observedAt = data?.cached_at
  const visibleCoverage = data?.coverage
  const today = businessToday(data?.business_timezone)
  // 明细没读出来的卡：后台那一刻确实有内容，只是不知道渠道和正文。
  const unread = (card: { read_status?: string }) => Boolean(card.read_status && card.read_status !== 'complete')
  const refresh = async () => {
    setBusy(true); setFailed(false)
    try { client.setQueryData(calendarOptions().queryKey, await refreshCalendar()) }
    catch (error) { setFailed(true); if (isApiError(error) && error.payload && typeof error.payload === 'object' && 'cards' in error.payload && Array.isArray(error.payload.cards)) {
      const fallback = error.payload as Partial<CalendarPayload>
      if (data) client.setQueryData(calendarOptions().queryKey, { ...data, ...fallback })
    } } finally { setBusy(false) }
  }
  const show = observedAt && (visibleCoverage?.matches_current_month || (failed && visibleCards.length > 0))
  return <section aria-label="发布月历" aria-busy={busy}>
    <div className={styles.heading}><PageTitle />{data?.refresh_available
      ? <Tooltip title="会打开发布浏览器读取后台，通常需要数十秒"><Button loading={busy} onClick={() => void refresh()}>刷新月历</Button></Tooltip>
      : <DisabledReason label="刷新月历" reason="发布日历读取条件尚未满足"><Button loading={busy} disabled>刷新月历</Button></DisabledReason>}</div>
    <Typography.Text type="secondary">{data?.month_ui} · 数据截至 <BusinessTime at={observedAt ? zonedInput(observedAt) : null} fallback="尚未读取" />{data?.stale && <Tag>数据可能已过期</Tag>}</Typography.Text>
    {(failed || query.error || data?.error) && <Alert type="warning" title="本次月历未完整更新" description={data?.error || '读取失败，已保留此前完整数据。'} />}
    <Spin spinning={busy || query.isPending}>
      {show && data ? <div className={styles.grid}>
        {['周一','周二','周三','周四','周五','周六','周日'].map(day => <div key={day} className={styles.weekday}>{day}</div>)}
        {calendarDays(data.display_start, data.display_end_exclusive).map((day, index) => <div key={day ?? `pad-${index}`} className={cx(styles.day, day === today && styles.today, day != null && day.slice(0, 7) !== data.month_ui && styles.outside)} {...(day ? { 'data-day': day } : {})} {...(day === today ? { 'data-today': '' } : {})}>
          {day && <><div className={styles.date}>{day.slice(5).replace('-', '/')} {day === today && <span className={styles.todayMark}>今天</span>}</div>
            {visibleCards.filter(card => card.at_business.slice(0, 10) === day).map((card, i) => <Popover key={`${card.card_sha256}-${i}`} trigger={['click']} title={card.at_business.slice(0, 16).replace('T', ' ')} content={<div className={styles.caption}><p>{unread(card) ? '这条的明细没读出来，不能据此确认可排时段。' : card.caption_status === 'empty' ? '此内容无独立正文' : card.rendered || '正文尚未核实'}</p>{accountNames(card) && <p>{accountNames(card)}</p>}<p className={styles.note}>系统观测到的后台记录，正式提交前会再核对。</p></div>}>
              <button type="button" className={styles.card}><strong>{card.at_business.slice(11, 16)}</strong>{accountNames(card) && <span>{accountNames(card)}</span>}{cardTags(card, unread(card))}</button>
            </Popover>)}
            {/* 本地图层：系统自己知道的，还没从后台读回来 */}
            {data.local.filter(item => item.at_business?.slice(0, 10) === day).map(item => <Link key={item.task_id} to={`/review/${idPath(item.task_id)}?platform=${item.platform}`} className={cx(styles.card, styles.localCard)}>
              <strong>{item.at_business?.slice(11, 16)}</strong>
              <span>{item.platform === 'facebook' ? 'Facebook' : 'Instagram'}</span>
              <Tag>{({ scheduled: '本地', submitting: '待回读', content_locked: '待提交' } as const)[item.kind]}</Tag></Link>)}
          </>}
        </div>)}
      </div> : !query.isPending && <Empty description={!data?.cached_at ? '还没有读取过发布日历' : '当前缓存没有覆盖本月；上次数据已保留'} />}
    </Spin>
    {/* 上次刷新没成功时展示的是旧数据，不能替它断言「这个月是空的」。 */}
    {show && !data?.stale && data?.coverage?.decision_complete && visibleCards.length === 0 && <p className={styles.help}>本次读取的月份中没有内容记录。</p>}
    {data?.local_error && <Alert type="error" title="本地排期记录读不出来" description={<>{data.local_error}<br />⛔ 这不等于「本地没有排期」；在核对清楚之前，不要拿这个月历判断哪些时刻空着。</>} />}
  </section>
}
