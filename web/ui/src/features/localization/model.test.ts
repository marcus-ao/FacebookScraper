import { describe, expect, it } from 'vitest'
import type { TaskDetail } from '@/types/domain'
import fixture from '@/types/__fixtures__/task-detail-active.json'
import { recoverDraft } from './model'

describe('来源变化时的草稿恢复', () => {
  const previous: TaskDetail = { ...fixture as unknown as TaskDetail, localization: {
    ...(fixture as unknown as TaskDetail).localization, protected_tags: ['#OldBrand'], tags: ['#OldBrand','#Cats'],
    links: [{ source_url: 'https://us.example/a', target_url: 'https://de.example/a', mapped_url: 'https://de.example/a', confirmed: true, origin: 'mapping' }] } }
  const draft = { ...previous.localization, body_de: '人工未保存 😀', tags: ['#OldBrand','#MeineWahl'], hashtags_confirmed: true, links_confirmed: true,
    links: previous.localization.links.map(link => ({ ...link, target_url: 'https://de.example/manual' })) }
  it('保留正文与自选链接，来源变化后重新确认并换入新保护标签', () => {
    const latest = { ...previous, text: { ...previous.text, source_text_sha256: 'new-source' as TaskDetail['text']['source_text_sha256'] }, localization: { ...previous.localization, protected_tags: ['#NewBrand'] } }
    const result = recoverDraft(previous, latest, draft)
    expect(result.body_de).toBe('人工未保存 😀')
    expect(result.tags).toEqual(['#NewBrand','#MeineWahl'])
    expect(result.hashtags_confirmed).toBe(false)
    expect(result.links_confirmed).toBe(false)
    expect(result.links[0]).toMatchObject({ target_url: 'https://de.example/manual', confirmed: false })
    expect(draft.hashtags_confirmed).toBe(true)
  })
  it('来源未变时保留已确认的选择', () => {
    const result = recoverDraft(previous, previous, draft)
    expect(result.hashtags_confirmed).toBe(true)
    expect(result.links_confirmed).toBe(true)
    expect(result.links[0]?.confirmed).toBe(true)
  })
})
