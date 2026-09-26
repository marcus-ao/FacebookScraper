import { describe, expect, it } from 'vitest'
import { renderToStaticMarkup } from 'react-dom/server'
import type { TaskDetail } from '@/types/domain'
import fixture from '@/types/__fixtures__/task-detail-active.json'
import { LocalizationEditor, parsePublicationTags } from './LocalizationEditor'
import { bioTargetWarning, completeTagAndLinkReview } from './model'

const detail = fixture as unknown as TaskDetail

function markup(tags: string[]) {
  const draft = { ...detail.localization, source_tags: ['#Neakasa', '#CatCare'],
    protected_tags: ['#Neakasa'], tags }
  return renderToStaticMarkup(<LocalizationEditor detail={detail} draft={draft}
    editing saving={false} onChange={() => {}} onComplete={() => {}} onSave={() => {}} onDiscard={() => {}} onInsert={() => {}} />)
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

describe('标签与链接一次完成', () => {
  it('独立确认品牌标签被删除、任意标签被加入以及全部标签清空', () => {
    for (const tags of [[], ['#MeineWahl']]) {
      const reviewed = completeTagAndLinkReview({ ...detail.localization, source_tags: ['#Neakasa'], tags })
      expect(reviewed.tags).toEqual(tags)
      expect(reviewed.hashtags_confirmed).toBe(true)
    }
    expect(markup([])).toContain('完成标签与链接审核')
  })
  it('Facebook 逐条保持原文顺序，缺目标不能伪称该条已确认', () => {
    const draft = { ...detail.localization, links: [
      { ...detail.localization.links[0]!, target_url: 'https://de.example/one' },
      { ...detail.localization.links[1]!, target_url: '' },
    ] }
    const result = completeTagAndLinkReview(draft)
    expect(result.links.map(link => link.source_url)).toEqual(draft.links.map(link => link.source_url))
    expect(result.links.map(link => link.confirmed)).toEqual([true, false])
    expect(result.links_confirmed).toBe(true)
    expect(completeTagAndLinkReview({ ...draft, links: [{ ...draft.links[0]!, target_url: 'not-a-url' }] })
      .links[0]?.confirmed).toBe(false)
    const html = renderToStaticMarkup(<LocalizationEditor detail={detail} draft={draft} editing saving={false}
      onChange={() => {}} onComplete={() => {}} onSave={() => {}} onDiscard={() => {}} onInsert={() => {}} />)
    expect(html).toContain('打开德语落地页')
    expect(html).toContain('链接 2 尚未填写德语落地页')
    expect(html).toContain('插入链接 1')
    expect(html).not.toContain('已配置的映射')
  })
  it('Instagram 主页目标缺失只提醒，不阻断确认', () => {
    const ig = { ...detail.localization, platform: 'instagram' as const, ig_cta: 'Link in Bio', ig_bio_url: '', links: [] }
    expect(bioTargetWarning(ig)).toContain('主页目标尚未配置')
    expect(completeTagAndLinkReview(ig).links_confirmed).toBe(true)
    const html = renderToStaticMarkup(<LocalizationEditor detail={{ ...detail, platform: 'instagram' }} draft={ig}
      editing={false} saving={false} onChange={() => {}} onComplete={() => {}} onSave={() => {}} onDiscard={() => {}} onInsert={() => {}} />)
    expect(html).toContain('主页目标尚未配置')
    expect(html).toContain('完成标签与链接审核')
  })
})
