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
  const partial = Boolean(data?.partial_cached_at)
  const visibleCards = partial ? data?.partial_cards ?? [] : data?.cards ?? []
  const observedAt = partial ? data?.partial_cached_at : data?.cached_at
  const visibleCoverage = partial ? data?.attempt_coverage : data?.coverage
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
      : <DisabledReason label="刷新月历" reason="发布日历读取条件尚未满足，请查看核验信息"><Button loading={busy} disabled>刷新月历</Button></DisabledReason>}</div>
    <p className={styles.help}>以北京时间查看后台的排期和已发布内容，也包含人工创建的帖子。每张卡同时标出德国受众那边的钟点。</p>
    <div className={styles.legend} aria-label="图例">
      <Tag color="processing">实线 · 后台读到的</Tag>
      <Tag>虚线 · 系统本地记录</Tag>
      <Typography.Text type="secondary">空档只由后台数据判定；本地记录只作展示。</Typography.Text>
    </div>
    <Space wrap><Typography.Text type="secondary">{data?.month_ui} · {partial ? '本次部分读取截至' : '数据截至'} <BusinessTime at={observedAt ? zonedInput(observedAt) : null} fallback="尚未读取" /></Typography.Text>{data?.stale && <Tag>{partial ? '部分结果 · 不可判断空档' : '数据可能已过期'}</Tag>}{observedAt && <span>{visibleCards.length} 条可见记录</span>}</Space>
    {(failed || query.error || data?.error) && <Alert type="warning" title="本次月历未完整更新" description={data?.error || '读取失败，已保留此前完整数据。'} />}
    {partial && <Alert type="info" title="当前展示最近一次部分结果；上次完整缓存及其时间仍保留" description={data?.cached_at ? `上次完整读取：${zonedInput(data.cached_at)}。未核实内容不能作为空档依据。` : '尚未取得完整月历，当前不能判断可排期时段。'} />}
    <Spin spinning={busy || query.isPending}>
      {show && data ? <div className={styles.grid}>
        {['周一','周二','周三','周四','周五','周六','周日'].map(day => <div key={day} className={styles.weekday}>{day}</div>)}
        {calendarDays(data.display_start, data.display_end_exclusive).map((day, index) => <div key={day ?? `pad-${index}`} className={cx(styles.day, day === today && styles.today)} {...(day ? { 'data-day': day } : {})} {...(day === today ? { 'data-today': '' } : {})}>
          {day && <><div className={styles.date}>{day.slice(5).replace('-', '/')} {day === today && <span className={styles.todayMark}>今天</span>} {day.slice(0, 7) !== data.month_ui && '· 跨月时差'}</div>
            {visibleCards.filter(card => card.at_business.slice(0, 10) === day).map((card, i) => <Popover key={`${card.card_sha256}-${i}`} trigger={['click']} title={`${card.at_business.slice(0,16).replace('T',' ')} 北京`} content={<div className={styles.caption}><p>{card.caption_status === 'empty' ? '此内容无独立正文' : card.rendered || '正文尚未核实'}</p>{card.accounts && <p>{Object.entries(card.accounts).map(([channel, name]) => `${channel}: ${name}`).join(' / ')}</p>}{card.relationships?.length ? <p>关系：{card.relationships.map(value => ({ collaboration: '合作内容', shared: '分享内容', cross_platform: '跨平台关联' } as Record<string, string>)[value] ?? '待核实').join('、')}</p> : null}{card.audience && <p className={styles.audience}>德国 {card.audience.at.slice(5, 16).replace('T', ' ')}</p>}</div>}>
              <button type="button" className={styles.card}><strong>{card.at_business.slice(11,16)} 北京</strong>{card.audience && <span className={styles.audience}>德国 {card.audience.at.slice(11, 16)}</span>}<span>{card.channels.length ? card.channels.map(channel => channel === 'facebook' ? 'Facebook' : 'Instagram').join(' / ') : '渠道待确认'}</span>
                <span>{({ feed: 'Feed', story: 'Story', reel: 'Reel', live: '直播', ad: '广告', task: '任务', unknown: '位置待识别' })[card.placement ?? 'unknown']} · {({ text: '文字', link: '链接', image: '图片', carousel: '轮播/相册', mixed: '混合媒体', video: '视频', unknown: '形式待核实' })[card.media_kind ?? 'unknown']}</span>
                <Tag color={card.delivery === 'published' ? 'success' : card.delivery === 'scheduled' ? 'processing' : 'default'}>{({ published: '已观测到公开发布', scheduled: '已创建定时任务', draft: '草稿', failed: '发布失败', processing: '处理中', unavailable: '不可访问', unknown: '发布状态待核验' } as Record<string, string>)[card.delivery] ?? '发布状态待核验'}</Tag>
                {card.read_status && card.read_status !== 'complete' && <Tag color="warning">此条尚未核实</Tag>}</button>
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
    {show && !partial && data?.coverage.decision_complete && visibleCards.length === 0 && <p className={styles.help}>本次完整读取的月份中没有内容记录。</p>}
    {/* 冻结但还没选时刻的落不到任何一天，单独列出来，否则它就从视野里消失了。 */}
    {data && data.local.some(item => !item.at_business) && <div className={styles.help}>
      <strong>已冻结、等着选发布时间：</strong>
      <Space wrap>{data.local.filter(item => !item.at_business).map(item =>
        <Link key={item.task_id} to={`/review/${idPath(item.task_id)}?platform=${item.platform}`}>
          {item.platform === 'facebook' ? 'Facebook' : 'Instagram'} · {item.task_id.split('/').pop()}
        </Link>)}</Space>
    </div>}
    {data?.local_error && <Alert type="error" title="本地排期记录读不出来" description={<>{data.local_error}<br />⛔ 这不等于「本地没有排期」；在核对清楚之前，不要拿这个月历判断哪些时刻空着。</>} />}
    <p className={styles.help}>月份按发布后台的 {data?.ui_timezone ?? '已配置时区'} 计算。已核实的 Story、Reel 等内容继续遵循同渠道 {data?.gap_minutes ?? 90} 分钟间隔；未识别内容不能当作空档。选时提示来自完整缓存，正式排期前会再次读取后台核对。</p>
    {data && <Collapse ghost items={[{ key: 'coverage', label: '月历覆盖与核验信息', children: <pre className={styles.diagnostic}>{JSON.stringify({ coverage: data.coverage, attempt_coverage: data.attempt_coverage, bounds: data.bounds, refresh_diagnostic: data.refresh_diagnostic, refresh_unavailable_reason: data.refresh_unavailable_reason, error: data.error, gap_minutes: data.gap_minutes }, null, 2)}</pre> }]} />}
  </section>
}
