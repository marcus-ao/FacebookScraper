import type { CalendarCard, Platform } from '@/types/domain'
import { wallMinutesApart } from '@/lib/format'

/** 往前后各看一天。同渠道最小间隔是分钟级的，一天足够把跨午夜的邻居都圈进来。 */
const WINDOW_MINUTES = 24 * 60

/**
 * 选定时刻附近的同渠道占用。**纯函数**，所以能单测。
 *
 * ⛔ 不要改回「同一个日历日」。23:30 与次日 00:30 只差 60 分钟，按日期筛会让
 *    这条最该提醒的情况恰好不提醒 —— 后端 90 分钟同渠道闸门当然还会 409，
 *    但那时她已经点过「确认通过并创建排期」了，排期没建成还得回头重来。
 *
 * 两边都按柏林墙上时刻比：她填的 `datetime-local` 不带偏移，换算成 epoch
 * 等于替她猜一个偏移，而夏令时那天猜错就是错一小时。
 * 代价是夏令时切换当晚的那一小时会有 ±60 分钟误差 —— 这只是本地提示，
 * 真正判定在后端（web/DESIGN.md）。
 */
export function nearbyOccupancy(
  cards: readonly CalendarCard[] | undefined,
  platform: Platform,
  when: string,
  gapMinutes: number,
): { readonly cards: readonly CalendarCard[]; readonly tooClose: boolean } {
  if (!when || !cards) return { cards: [], tooClose: false }
  const nearby = cards
    .filter(card => card.channels.includes(platform))
    .map(card => ({ card, apart: wallMinutesApart(when, card.at_business) }))
    .filter(row => Number.isFinite(row.apart) && row.apart <= WINDOW_MINUTES)
    .sort((a, b) => a.apart - b.apart)
  return {
    cards: nearby.map(row => row.card),
    tooClose: nearby.some(row => row.apart < gapMinutes),
  }
}
