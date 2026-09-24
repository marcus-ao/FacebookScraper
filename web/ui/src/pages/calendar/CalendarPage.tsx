import { useState } from 'react'
import { Link } from 'react-router'
import { Alert, Button, Empty, Popover, Spin, Tag, Tooltip, Typography } from 'antd'
import { useQueryClient } from '@tanstack/react-query'
import { PageTitle } from '@/app/PageTitle'
import { calendarOptions, useCalendar } from '@/hooks/useCalendar'
import { refreshCalendar } from '@/services/calendar'
import { idPath, isApiError } from '@/services/http'
import type { CalendarCard, CalendarPayload, Platform } from '@/types/domain'
import { BusinessTime } from '@/components/Time'
import { PlatformLabel } from '@/components/PlatformLabel'
import { DisabledReason } from '@/components/DisabledReason'
import { zonedInput, businessToday, calendarDays } from '@/lib/format'
import { cx } from '@/lib/css'
import styles from './CalendarPage.module.css'

const UNREAD_COPY = '该篇帖子的具体信息尚未成功获取，请前往Meta后台任务日历进行人工复核确认'
const NO_TEXT_COPY = '该篇帖子不含文本部分,请跳转原帖进行复核确认'

/** 卡片状态用短文案，颜色承载可信度。明细没读出来的说明放进详情，不占格子。 */
function cardTags(card: CalendarCard) {
  const delivery = ({ published: '已发布', scheduled: '定时', draft: '草稿', failed: '发布失败', processing: '处理中' } as Record<string, string>)[card.delivery] ?? '待核验'
  const color = card.delivery === 'published' ? 'success' : card.delivery === 'scheduled' ? 'processing' : 'default'
  return <Tag color={color}>{delivery}</Tag>
}

function accountNames(card: CalendarCard): string {
  return card.accounts ? [...new Set(Object.values(card.accounts).filter(Boolean))].join(' / ') : ''
}

function cardCopy(card: CalendarCard): string {
  if (card.read_status && card.read_status !== 'complete') return UNREAD_COPY
  if (card.caption_status === 'empty' || !card.rendered.trim()) return NO_TEXT_COPY
  return card.rendered
}

function CardLinks({ card }: { card: CalendarCard }) {
  const links = (['facebook', 'instagram'] as const).flatMap(channel => {
    const href = card.permalinks?.[channel]
    return href ? [[channel, href] as const] : []
  })
  const label: Record<Platform, string> = { facebook: '查看 Facebook 已发布帖子', instagram: '查看 Instagram 已发布帖子' }
  return <>
    {card.source_permalink && <a href={card.source_permalink} target="_blank" rel="noopener noreferrer">查看原帖 ↗</a>}
    {card.source_task_id && card.source_platform && <Link to={`/review/${idPath(card.source_task_id)}?platform=${card.source_platform}`}>审校详情</Link>}
    {links.map(([channel, href]) => <a key={channel} href={href} target="_blank" rel="noopener noreferrer">
      {links.length === 1 ? '查看已发布帖子' : label[channel]}</a>)}
  </>
}

export function CardDetail({ card }: { card: CalendarCard }) {
  const account = accountNames(card)
  return <div className={styles.caption}>
    <p className={styles.facts}>
      <span>{card.at_business.slice(0, 16).replace('T', ' ')}</span>
      {account && <span>{account}</span>}
      <CardLinks card={card} />
    </p>
    <p>{cardCopy(card)}</p>
  </div>
}

export function CalendarPage() {
  const query = useCalendar(), client = useQueryClient()
  const [busy, setBusy] = useState(false), [failed, setFailed] = useState(false)
  const data = query.data
  const visibleCards = data?.cards ?? []
  const observedAt = data?.cached_at
  const visibleCoverage = data?.coverage
  const today = businessToday(data?.business_timezone)
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
            {visibleCards.filter(card => card.at_business.slice(0, 10) === day).map((card, i) => <Popover key={`${card.card_sha256}-${i}`} trigger={['click']} content={<CardDetail card={card} />}>
              <button type="button" className={styles.card}><strong>{card.at_business.slice(11, 16)}</strong>{card.channels.map(channel => <PlatformLabel key={channel} platform={channel} />)}{cardTags(card)}</button>
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
