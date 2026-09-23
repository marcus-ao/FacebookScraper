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
  const visibleCards = data?.cards ?? []
  const observedAt = data?.cached_at
  const visibleCoverage = data?.coverage
  const unresolved = data?.coverage?.unresolved_count ?? 0
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
      : <DisabledReason label="刷新月历" reason="发布日历读取条件尚未满足，请查看核验信息"><Button loading={busy} disabled>刷新月历</Button></DisabledReason>}</div>
    <p className={styles.help}>这一页是发布后台的只读同步：后台的已发布帖子和定时任务，按北京时间摆在月历上，供审核时避开已占用的时刻。每张卡同时标出德国受众那边的钟点。</p>
    <div className={styles.legend} aria-label="图例">
      <Tag color="processing">实线 · 后台读到的</Tag>
      <Tag>虚线 · 系统本地记录</Tag>
      <Typography.Text type="secondary">空档只由后台数据判定；本地记录只作展示。</Typography.Text>
    </div>
    <Space wrap><Typography.Text type="secondary">{data?.month_ui} · 数据截至 <BusinessTime at={observedAt ? zonedInput(observedAt) : null} fallback="尚未读取" /></Typography.Text>{data?.stale && <Tag>数据可能已过期</Tag>}{observedAt && <span>{visibleCards.length} 条可见记录</span>}{unresolved > 0 && <Tag color="warning">{unresolved} 条明细未读取</Tag>}</Space>
    {(failed || query.error || data?.error) && <Alert type="warning" title="本次月历未完整更新" description={data?.error || '读取失败，已保留此前完整数据。'} />}
    {unresolved > 0 && <Alert type="info" title={`本月有 ${unresolved} 条卡片没有读完明细`} description="这些卡片仍显示在月历上。渠道未知，或时刻没有独立核实的，不能用来确认任何目标时刻空闲；也不能把格子上的已发布或定时当成已经公开。" />}
    <Spin spinning={busy || query.isPending}>
      {show && data ? <div className={styles.grid}>
        {['周一','周二','周三','周四','周五','周六','周日'].map(day => <div key={day} className={styles.weekday}>{day}</div>)}
        {calendarDays(data.display_start, data.display_end_exclusive).map((day, index) => <div key={day ?? `pad-${index}`} className={cx(styles.day, day === today && styles.today)} {...(day ? { 'data-day': day } : {})} {...(day === today ? { 'data-today': '' } : {})}>
          {day && <><div className={styles.date}>{day.slice(5).replace('-', '/')} {day === today && <span className={styles.todayMark}>今天</span>} {day.slice(0, 7) !== data.month_ui && '· 跨月时差'}</div>
            {visibleCards.filter(card => card.at_business.slice(0, 10) === day).map((card, i) => <Popover key={`${card.card_sha256}-${i}`} trigger={['click']} title={`${card.at_business.slice(0,16).replace('T',' ')} 北京`} content={<div className={styles.caption}><p>{unread(card) ? '这条的明细没读出来。展示上的已发布或定时只说明格子里有没有链接，不能当作公开事实，也不能据此确认可排时段。' : card.caption_status === 'empty' ? '此内容无独立正文' : card.rendered || '正文尚未核实'}</p>{card.accounts && <p>{Object.entries(card.accounts).map(([channel, name]) => `${channel}: ${name}`).join(' / ')}</p>}{card.relationships?.length ? <p>关系：{card.relationships.map(value => ({ collaboration: '合作内容', shared: '分享内容', cross_platform: '跨平台关联' } as Record<string, string>)[value] ?? '待核实').join('、')}</p> : null}{card.audience && <p className={styles.audience}>德国 {card.audience.at.slice(5, 16).replace('T', ' ')}</p>}</div>}>
              <button type="button" className={styles.card}><strong>{card.at_business.slice(11,16)} 北京</strong>{card.audience && <span className={styles.audience}>德国 {card.audience.at.slice(11, 16)}</span>}<span>{card.channels.length ? card.channels.map(channel => channel === 'facebook' ? 'Facebook' : 'Instagram').join(' / ') : '渠道未读取'}</span>
                {!unread(card) && <span>{({ feed: 'Feed', story: 'Story', reel: 'Reel', live: '直播', ad: '广告', task: '任务', unknown: '位置待识别' })[card.placement ?? 'unknown']} · {({ text: '文字', link: '链接', image: '图片', carousel: '轮播/相册', mixed: '混合媒体', video: '视频', unknown: '形式待核实' })[card.media_kind ?? 'unknown']}</span>}
                <Tag color={card.delivery === 'published' ? 'success' : card.delivery === 'scheduled' ? 'processing' : 'default'}>{(unread(card)
                  ? { published: '后台已发布', scheduled: '后台定时任务' } as Record<string, string>
                  : { published: '已观测到公开发布', scheduled: '已创建定时任务', draft: '草稿', failed: '发布失败', processing: '处理中', unavailable: '不可访问', unknown: '发布状态待核验' } as Record<string, string>)[card.delivery] ?? '后台已占用'}</Tag>
                {unread(card) && <Tag color="warning">明细未读取</Tag>}</button>
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
    {/* 上次刷新没成功时展示的是旧数据，不能替它断言「这个月是空的」。 */}
    {show && !data?.stale && visibleCards.length === 0 && <p className={styles.help}>本次读取的月份中没有内容记录。</p>}
    {/* 冻结但还没选时刻的落不到任何一天，单独列出来，否则它就从视野里消失了。 */}
    {data && data.local.some(item => !item.at_business) && <div className={styles.help}>
      <strong>已冻结、等着选发布时间：</strong>
      <Space wrap>{data.local.filter(item => !item.at_business).map(item =>
        <Link key={item.task_id} to={`/review/${idPath(item.task_id)}?platform=${item.platform}`}>
          {item.platform === 'facebook' ? 'Facebook' : 'Instagram'} · {item.task_id.split('/').pop()}
        </Link>)}</Space>
    </div>}
    {data?.local_error && <Alert type="error" title="本地排期记录读不出来" description={<>{data.local_error}<br />⛔ 这不等于「本地没有排期」；在核对清楚之前，不要拿这个月历判断哪些时刻空着。</>} />}
    <p className={styles.help}>月份按发布后台的 {data?.ui_timezone ?? '已配置时区'} 计算。已核实渠道和时间的内容遵循同渠道 {data?.gap_minutes ?? 90} 分钟间隔。明细未读取、渠道未知或时刻未独立核实的条目会阻止空档确认。选时提示来自这份缓存，正式提交前会再读后台；整月回读和远端删除核实仍要求决策完整。</p>
    {data && <Collapse ghost items={[{ key: 'coverage', label: '月历覆盖与核验信息', children: <pre className={styles.diagnostic}>{JSON.stringify({ coverage: data.coverage, bounds: data.bounds, refresh_diagnostic: data.refresh_diagnostic, refresh_unavailable_reason: data.refresh_unavailable_reason, error: data.error, gap_minutes: data.gap_minutes }, null, 2)}</pre> }]} />}
  </section>
}
