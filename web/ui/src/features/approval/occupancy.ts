import type { CalendarCard, Platform } from '@/types/domain'
import { wallMinutesApart } from '@/lib/format'

/** 向前后各看一天，覆盖跨午夜占用。 */
const WINDOW_MINUTES = 24 * 60

/** 按柏林墙上时刻提示邻近占用，DST 切换时可能相差一小时；最终由后端核验。 */
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
