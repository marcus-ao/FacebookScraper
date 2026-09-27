import { describe, expect, it } from 'vitest'

import type { CalendarCard } from '@/types/domain'
import { nearbyOccupancy } from './occupancy'

const card = (at: string, channels: CalendarCard['channels'] = ['facebook']): CalendarCard => ({
  at, at_business: at, channels, card_sha256: at, delivery: 'scheduled', rendered: '',
})


describe('同渠道占用按真实时间差算，不按"同一个日历日"', () => {
  const gap = 90

  it('1 分钟配置允许相邻整分钟，同刻提示，跨渠道独立', () => {
    const before = card('2026-09-13T23:59:00+02:00')
    expect(nearbyOccupancy([before], 'facebook', '2026-09-14T00:00', 1).tooClose).toBe(false)
    expect(nearbyOccupancy([before], 'facebook', '2026-09-13T23:59', 1).tooClose).toBe(true)
    expect(nearbyOccupancy([before], 'instagram', '2026-09-13T23:59', 1).tooClose).toBe(false)
  })

  it('23:30 与次日 00:30 只差 60 分钟，必须提醒', () => {
    const result = nearbyOccupancy([card('2026-09-13T23:30:00+02:00')], 'facebook', '2026-09-14T00:30', gap)
    expect(result.tooClose).toBe(true)
    expect(result.cards).toHaveLength(1)
  })

  it('反过来也一样：选 23:30，前面挡着的是次日 00:30', () => {
    const result = nearbyOccupancy([card('2026-09-14T00:30:00+02:00')], 'facebook', '2026-09-13T23:30', gap)
    expect(result.tooClose).toBe(true)
  })

  it('同一天但隔得够远就不提醒', () => {
    const result = nearbyOccupancy([card('2026-09-14T08:00:00+02:00')], 'facebook', '2026-09-14T10:30', gap)
    expect(result.tooClose).toBe(false)
    expect(result.cards).toHaveLength(1)
  })

  it('正好等于间隔不算冲突，差一分钟才算', () => {
    expect(nearbyOccupancy([card('2026-09-14T09:00:00+02:00')], 'facebook', '2026-09-14T10:30', gap).tooClose).toBe(false)
    expect(nearbyOccupancy([card('2026-09-14T09:01:00+02:00')], 'facebook', '2026-09-14T10:30', gap).tooClose).toBe(true)
  })

  it('另一个渠道的排期不算占用', () => {
    const result = nearbyOccupancy([card('2026-09-14T00:30:00+02:00', ['instagram'])], 'facebook', '2026-09-13T23:30', gap)
    expect(result.cards).toEqual([])
    expect(result.tooClose).toBe(false)
  })

  it('两天以外的不列进来，免得把整月的卡片都倒出来', () => {
    const result = nearbyOccupancy([card('2026-09-10T10:00:00+02:00'), card('2026-09-14T09:30:00+02:00')],
      'facebook', '2026-09-14T10:30', gap)
    expect(result.cards).toHaveLength(1)
  })

  it('按离得近的排在前面', () => {
    const result = nearbyOccupancy(
      [card('2026-09-14T20:00:00+02:00'), card('2026-09-14T11:00:00+02:00')],
      'facebook', '2026-09-14T10:30', gap)
    expect(result.cards.map(row => row.at_business.slice(11, 16))).toEqual(['11:00', '20:00'])
  })

  it('还没选时刻、或者月历没读到时什么都不说', () => {
    expect(nearbyOccupancy([card('2026-09-14T10:00:00+02:00')], 'facebook', '', gap))
      .toEqual({ cards: [], tooClose: false })
    expect(nearbyOccupancy(undefined, 'facebook', '2026-09-14T10:30', gap))
      .toEqual({ cards: [], tooClose: false })
  })

  it('⛔ 这不是闸门：它只回答"近不近"，不回答"能不能排"', () => {
    expect(nearbyOccupancy([card('2026-09-14T10:29:00+02:00')], 'facebook', '2026-09-14T10:30', 0).tooClose)
      .toBe(false)
  })
})
