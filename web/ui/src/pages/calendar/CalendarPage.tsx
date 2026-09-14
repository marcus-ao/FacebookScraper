import { useState } from 'react'
import { Alert, Button, Collapse, Empty, Popover, Space, Spin, Tag, Tooltip, Typography } from 'antd'
import { useQueryClient } from '@tanstack/react-query'
import { PageTitle } from '@/app/PageTitle'
import { calendarOptions, useCalendar } from '@/hooks/useCalendar'
import { refreshCalendar } from '@/services/calendar'
import { isApiError } from '@/services/http'
import type { CalendarPayload } from '@/types/domain'
import { BerlinTime } from '@/components/Time'
import { DisabledReason } from '@/components/DisabledReason'
import { berlinInput, berlinToday, calendarDays } from '@/lib/format'
import { cx } from '@/lib/css'
import styles from './CalendarPage.module.css'

export function CalendarPage() {
  const query = useCalendar(), client = useQueryClient()
  const [busy, setBusy] = useState(false), [failed, setFailed] = useState(false)
  const data = query.data
  // 柏林日期，不是浏览器本地日期（lib/format.ts 的 berlinToday）。
  const today = berlinToday()
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
    {/* 可以刷新时提示的是耗时；不能刷新时提示的是原因 —— 后者要键盘也拿得到，
        所以走 DisabledReason 而不是挂在灰按钮上的 Tooltip。 */}
    <div className={styles.heading}><PageTitle />{data?.refresh_available
      ? <Tooltip title="会打开发布浏览器读取后台，通常需要数十秒"><Button loading={busy} onClick={() => void refresh()}>刷新月历</Button></Tooltip>
      : <DisabledReason label="刷新月历" reason="发布日历读取条件尚未满足，请查看核验信息"><Button loading={busy} disabled>刷新月历</Button></DisabledReason>}</div>
    <p className={styles.help}>以柏林时间查看后台的排期和已发布内容，也包含人工创建的帖子。</p>
    <Space wrap><Typography.Text type="secondary">{data?.month_ui} · 数据截至 <BerlinTime at={data?.cached_at ? berlinInput(data.cached_at) : null} fallback="尚未读取" /></Typography.Text>{data?.stale && <Tag>数据可能已过期</Tag>}{data?.cached_at && <span>{data.cards.length} 条可见记录</span>}</Space>
    {(failed || query.error || data?.error) && <Alert type="warning" title="本次月历未完整更新，仍展示已取得的记录，请稍后重试" />}
    {data?.status === 'partial' && <Alert type="warning" title="部分帖子的渠道尚未识别，当前无法可靠判断可用时刻" />}
    <Spin spinning={busy || query.isPending}>
      {/* ⛔ 这里曾经是 role="list" + role="listitem"。它是错的：list 的直接子节点里
          还混着七个星期标题和月初的补位格，两样都不是 listitem，读屏拿到的是一个
          结构无效的列表。想改成合法的 ARIA 就得动布局（CSS Grid 靠的正是这些
          直接子节点），而「没有错误 ARIA」比「有错误 ARIA」强。日期格改用
          data-day 定位，浏览器断言照常拿得到，读屏按 section 的 aria-label
          「发布月历」进来读可见日期文字。 */}
      {show && data ? <div className={styles.grid}>
        {['周一','周二','周三','周四','周五','周六','周日'].map(day => <div key={day} className={styles.weekday}>{day}</div>)}
        {calendarDays(data.display_start, data.display_end_exclusive).map((day, index) => <div key={day ?? `pad-${index}`} className={cx(styles.day, day === today && styles.today)} {...(day ? { 'data-day': day } : {})} {...(day === today ? { 'data-today': '' } : {})}>
          {day && <><div className={styles.date}>{day.slice(5).replace('-', '/')} {day === today && <span className={styles.todayMark}>今天</span>} {day.slice(0, 7) !== data.month_ui && '· 跨月时差'}</div>
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
