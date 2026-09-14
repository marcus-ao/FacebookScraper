import { useState } from 'react'
import { Alert, Button, Collapse, Empty, Popover, Space, Spin, Tag, Tooltip, Typography } from 'antd'
import { useQueryClient } from '@tanstack/react-query'
import { PageTitle } from '@/app/PageTitle'
import { calendarOptions, useCalendar } from '@/hooks/useCalendar'
import { refreshCalendar } from '@/services/calendar'
import { isApiError } from '@/services/http'
import type { CalendarPayload } from '@/types/domain'
import { BerlinTime } from '@/components/Time'
import { berlinInput, calendarDays } from '@/lib/format'
import styles from './CalendarPage.module.css'

export function CalendarPage() {
  const query = useCalendar(), client = useQueryClient()
  const [busy, setBusy] = useState(false), [failed, setFailed] = useState(false)
  const data = query.data
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
    <div className={styles.heading}><PageTitle /><Tooltip title={!data?.refresh_available ? '发布日历读取条件尚未满足，请查看核验信息' : '会打开发布浏览器读取后台，通常需要数十秒'}><span><Button loading={busy} disabled={!data?.refresh_available} onClick={() => void refresh()}>刷新月历</Button></span></Tooltip></div>
    <p className={styles.help}>以柏林时间查看后台的排期和已发布内容，也包含人工创建的帖子。</p>
    <Space wrap><Typography.Text type="secondary">{data?.month_ui} · 数据截至 <BerlinTime at={data?.cached_at ? berlinInput(data.cached_at) : null} fallback="尚未读取" /></Typography.Text>{data?.stale && <Tag>数据可能已过期</Tag>}{data?.cached_at && <span>{data.cards.length} 条可见记录</span>}</Space>
    {(failed || query.error || data?.error) && <Alert type="warning" title="本次月历未完整更新，仍展示已取得的记录，请稍后重试" />}
    {data?.status === 'partial' && <Alert type="warning" title="部分帖子的渠道尚未识别，当前无法可靠判断可用时刻" />}
    <Spin spinning={busy || query.isPending}>
      {show && data ? <div className={styles.grid} role="list" aria-label="按柏林日期排列的帖子">
        {['周一','周二','周三','周四','周五','周六','周日'].map(day => <div key={day} className={styles.weekday}>{day}</div>)}
        {calendarDays(data.display_start, data.display_end_exclusive).map((day, index) => <div key={day ?? `pad-${index}`} className={styles.day} role={day ? 'listitem' : undefined} aria-label={day ? `${day} 柏林时间` : undefined}>
          {day && <><div className={styles.date}>{day.slice(5).replace('-', '/')} {day.slice(0, 7) !== data.month_ui && '· 跨月时差'}</div>
            {data.cards.filter(card => card.at_business.slice(0, 10) === day).map((card, i) => <Popover key={`${card.card_sha256}-${i}`} trigger={['click']} title={`${card.at_business.slice(0,16).replace('T',' ')} 柏林`} content={<div className={styles.caption}>{card.rendered || '正文未提供'}</div>}>
              <button type="button" className={styles.card}><strong>{card.at_business.slice(11,16)} 柏林</strong><span>{card.channels.length ? card.channels.map(channel => channel === 'facebook' ? 'Facebook' : 'Instagram').join(' / ') : '渠道待确认'}</span>
                <Tag color={card.delivery === 'published' ? 'success' : card.delivery === 'scheduled' ? 'processing' : 'default'}>{card.delivery === 'published' ? '已观测到公开发布' : card.delivery === 'scheduled' ? '已创建定时任务' : '发布状态待核验'}</Tag></button>
            </Popover>)}
          </>}
        </div>)}
      </div> : !query.isPending && <Empty description={!data?.cached_at ? '还没有读取过发布日历' : '当前缓存没有覆盖本月；上次数据已保留'} />}
    </Spin>
    {show && data?.cards.length === 0 && <p className={styles.help}>本次读取的月份中没有排期记录。</p>}
    <p className={styles.help}>月份按发布后台的美西自然月计算；柏林月初的部分时刻可能属于上一发布月份。选时提示来自缓存，正式排期前会再次读取后台核对。</p>
    {data && <Collapse ghost items={[{ key: 'coverage', label: '月历覆盖与核验信息', children: <pre className={styles.diagnostic}>{JSON.stringify({ coverage: data.coverage, bounds: data.bounds, refresh_unavailable_reason: data.refresh_unavailable_reason, error: data.error, gap_minutes: data.gap_minutes }, null, 2)}</pre> }]} />}
  </section>
}
