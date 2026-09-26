import { describe, expect, it } from 'vitest'
import type { TaskDetail } from '@/types/domain'
import fixture from '@/types/__fixtures__/task-detail-active.json'
import { defaultStep, deriveSteps } from './step-model'

const base = fixture as unknown as TaskDetail
const readyBody = { ...base, localization: { ...base.localization, body_de: 'Geprüfter Text' },
  text: { ...base.text, stale: false },
  content_review: { ...base.content_review, body: { confirmed: true, confirmed_at: '2026-09-24T00:00:00Z' } } }

describe('四步审核状态', () => {
  it('先打开首个未完成步骤，不能把看过图片当成已确认', () => {
    expect(defaultStep(base)).toBe('text')
    expect(defaultStep(readyBody)).toBe('images')
    expect(deriveSteps(readyBody)[1]?.status).toBe('pending')
  })

  it('无图片时说明无需审核图片，不要求虚构确认', () => {
    const noImages = { ...readyBody, images: [], content_review: { ...readyBody.content_review, images: [] } }
    expect(deriveSteps(noImages)[1]?.status).toBe('not_required')
    expect(defaultStep(noImages)).not.toBe('images')
  })

  it('标签和缺失链接指向第三步，正文版本变化指回第一步', () => {
    const withImage = { ...readyBody, images: [{ ...base.images[0]!, ready: true }],
      content_review: { ...readyBody.content_review, images: [{ index: 0, confirmed: true, confirmed_at: '2026-09-24T00:00:00Z' }] } }
    const steps = deriveSteps(withImage)
    expect(defaultStep(withImage)).toBe('localization')
    expect(steps[2]?.todos.some(item => item.text.includes('标签'))).toBe(true)
    expect(steps[2]?.todos.some(item => item.text.includes('链接 1'))).toBe(true)
    expect(steps[0]?.todos).toHaveLength(0)
    expect(defaultStep({ ...withImage, text: { ...withImage.text, stale: true } })).toBe('text')
  })

  it('待办含跳转步骤及图片编号；已冻结帖子直接显示最终步骤', () => {
    const steps = deriveSteps(base)
    expect(steps.map(step => step.id)).toEqual(['text', 'images', 'localization', 'final'])
    expect(steps.flatMap(step => step.todos).some(item => item.step === 'images' && item.text.includes('第 1 张'))).toBe(true)
    expect(defaultStep({ ...base, status: 'content_locked' })).toBe('final')
  })
})
