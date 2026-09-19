import type { LocalizationDraft, TaskDetail } from '@/types/domain'

export function editableFields(draft: LocalizationDraft) {
  return { body_de: draft.body_de, tags: draft.tags, hashtags_confirmed: draft.hashtags_confirmed,
    links: draft.links, ig_cta: draft.ig_cta, links_confirmed: draft.links_confirmed }
}

export function localizationBody(detail: TaskDetail, draft: LocalizationDraft) {
  return { ...editableFields(draft), source_text_sha256: detail.text.source_text_sha256,
    human_revision: detail.text.human_revision, review_revision: detail.review.revision,
    localization_revision: detail.localization.revision }
}

// 换入最新版本时保留运营输入；来源变化后的链接和话题标签必须重新确认。
export function recoverDraft(previous: TaskDetail, latest: TaskDetail, draft: LocalizationDraft): LocalizationDraft {
  const changed = previous.text.source_text_sha256 !== latest.text.source_text_sha256
  return { ...structuredClone(latest.localization), body_de: draft.body_de, ig_cta: draft.ig_cta,
    tags: [...new Set([...latest.localization.protected_tags,
      ...draft.tags.filter(tag => !previous.localization.protected_tags.includes(tag))])],
    hashtags_confirmed: changed ? false : draft.hashtags_confirmed,
    links_confirmed: changed ? false : draft.links_confirmed,
    links: latest.localization.links.map(link => {
      const old = draft.links.find(item => item.source_url === link.source_url)
      return old ? { ...link, target_url: old.target_url, confirmed: changed ? false : old.confirmed } : link
    }) }
}

export const displayLinks = (text: string) => text.replace(/\{\{link(\d+)\}\}/g, '〔链接 $1〕')
export const storeLinks = (text: string) => text.replace(/〔链接 (\d+)〕/g, '{{link$1}}')

// content_locked 也在内：冻结之后不能再改内容，这正是「编辑确认无误」要挡住的误触。
export const canEditTask = (detail: TaskDetail) => !detail.read_only
  && !['content_locked', 'approved', 'scheduled', 'skipped', 'handed_off'].includes(detail.status)
