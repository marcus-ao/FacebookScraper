import { readFileSync } from 'node:fs'
import { describe, expect, it } from 'vitest'
import type { TaskDetail } from '@/types/domain'
import fixture from '@/types/__fixtures__/task-detail-active.json'
import { sourceConsentRequired } from '@/components/SourceConsentDialog'

const detail = fixture as unknown as TaskDetail

describe('单篇模型工具的业务展示边界', () => {
  it('第三方来源的模型动作先要求来源确认，普通来源直接使用既有服务端闸门', () => {
    expect(sourceConsentRequired(detail)).toBe(false)
    expect(sourceConsentRequired({ ...detail, meta: { ...detail.meta, author_kind: 'third_party' } })).toBe(true)
    expect(sourceConsentRequired(detail, true)).toBe(true)
  })
  it('正常页面不再从模型任务与标签建议渲染金额、模型或原始诊断字段', () => {
    const files = [
      new URL('./ContentJobs.tsx', import.meta.url),
      new URL('../localization/LocalizationEditor.tsx', import.meta.url),
      new URL('../localization/SuggestionPanel.tsx', import.meta.url),
      new URL('../diagnostics/DetailDrawers.tsx', import.meta.url),
    ]
    const renderedSource = files.map(file => readFileSync(file, 'utf8')).join('\n')
    expect(renderedSource).not.toMatch(/cost_usd|estimated_image_usd|estimate_basis|candidate\.signals|sample_batch|JSON\.stringify\(flow\.job|技术诊断|查看处理依据|按实际用量计费|US\$/)
  })
})
