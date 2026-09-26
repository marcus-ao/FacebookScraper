import { describe, expect, it, vi } from 'vitest'
import { getReviewDraftActions, registerReviewDraftActions } from './review-draft-actions'

describe('详情草稿离开动作', () => {
  it('按帖子身份注册；旧组件清理不会移除新组件的回调', () => {
    const first = { save: vi.fn(), discard: vi.fn() }
    const second = { save: vi.fn(), discard: vi.fn() }
    const unregisterFirst = registerReviewDraftActions('account/post', first)
    expect(getReviewDraftActions('/review/account/post')).toBe(first)
    const unregisterSecond = registerReviewDraftActions('account/post', second)
    unregisterFirst()
    expect(getReviewDraftActions('/review/account/post')).toBe(second)
    expect(getReviewDraftActions('/history/account/post')).toBe(second)
    unregisterSecond()
    expect(getReviewDraftActions('/review/account/post')).toBeNull()
  })

  it('列表和不相关路径没有详情离开动作', () => {
    expect(getReviewDraftActions('/review/facebook')).toBeNull()
    expect(getReviewDraftActions('/calendar')).toBeNull()
  })
})
