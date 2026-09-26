import { describe, expect, it } from 'vitest'
import { renderToStaticMarkup } from 'react-dom/server'
import type { TaskDetail } from '@/types/domain'
import fixture from '@/types/__fixtures__/task-detail-active.json'
import { LocalizationEditor, parsePublicationTags } from './LocalizationEditor'

const detail = fixture as unknown as TaskDetail

function markup(tags: string[]) {
  const draft = { ...detail.localization, source_tags: ['#Neakasa', '#CatCare'],
    protected_tags: ['#Neakasa'], tags }
  return renderToStaticMarkup(<LocalizationEditor detail={detail} draft={draft}
    editing saving={false} onChange={() => {}} onConfirm={() => {}} onInsert={() => {}} />)
}

describe('拟发布话题标签', () => {
  it('把品牌、型号和普通标签一同交给业务人员编辑，支持空列表', () => {
    expect(parsePublicationTags('#Neakasa M1Pro,猫咪 #Neakasa')).toEqual(['#Neakasa', '#M1Pro', '#猫咪'])
    expect(parsePublicationTags('')).toEqual([])
    const html = markup(['#Neakasa', '#CatCare'])
    expect(html).toContain('本篇拟发布标签')
    expect(html).not.toContain('品牌与型号 · 保持原样')
    expect(html).not.toContain('与原帖不同')
  })

  it('清空含品牌的来源标签后，仅在差异区说明未采用', () => {
    const html = markup([])
    expect(html).toContain('本篇不使用话题标签')
    expect(html).toContain('与原帖不同')
    expect(html).toContain('未采用 #Neakasa')
  })
})
