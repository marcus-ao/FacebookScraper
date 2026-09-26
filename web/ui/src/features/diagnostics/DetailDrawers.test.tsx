import { describe, expect, it } from 'vitest'
import { businessTrail } from './DetailDrawers'
import type { OperationRecord } from '@/types/domain'

const record = (action: OperationRecord['action'], note?: string): OperationRecord => ({
  action, at: '2026-09-25T00:00:00Z', actor: null, note: note ?? null,
})

describe('业务处理记录', () => {
  it('只保留需要追溯的人工决策及原因', () => {
    const result = businessTrail([
      record('text_edited', 'raw revision abc'), record('image_selected', 'image path'),
      record('snoozed', '等确认素材'), record('woke'), record('skipped', '不适合本地发布'),
      record('content_locked', 'snapshot id'), record('handed_off', '人工接手'),
    ])
    expect(result.map(item => item.action)).toEqual(['snoozed', 'woke', 'skipped', 'handed_off'])
    expect(result.map(item => item.note)).toEqual(['等确认素材', null, '不适合本地发布', '人工接手'])
  })
})
