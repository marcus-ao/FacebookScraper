import { describe, expect, it, vi } from 'vitest'
import { renderToStaticMarkup } from 'react-dom/server'

import { CONFLICT_COPY, ConflictRecovery } from './ConflictRecovery'
import type { ConflictKind } from './ConflictRecovery'

const KINDS: readonly ConflictKind[] = ['draft', 'tags', 'settings', 'schedule']

const html = (kind: ConflictKind) =>
  renderToStaticMarkup(<ConflictRecovery kind={kind} onRecover={vi.fn()} />)

/**
 * 这一组的全部意义：**运营看到的 409 不许长得像 409。**
 *
 * 后端的 409 表示"版本/锁/许可/能力冲突"（web/DESIGN.md §5）。
 * 对她来说这句话零信息量，而她需要知道的只有两件事：
 * 她的修改还在，以及按哪个按钮能继续。
 */

/** 一个都不许出现在界面上的词。 */
const ENGINEERING_TERMS = [
  '409',
  'HTTP',
  'http',
  'revision',
  'Revision',
  'CAS',
  'conflict',
  'Conflict',
  'mismatch',
  'precondition',
  'Precondition',
  'sha256',
  'etag',
  'ETag',
  'version',
  'payload',
  'API',
  'status',
]

describe('409 的展示不泄漏工程术语', () => {
  it.each(KINDS)('%s：渲染出来的每一个字都是业务语言', (kind) => {
    const markup = html(kind)
    const visible = markup.replace(/<[^>]+>/g, '')
    for (const term of ENGINEERING_TERMS) {
      expect(visible, `${kind} 里出现了「${term}」`).not.toContain(term)
    }
  })

  it.each(KINDS)('%s：文案表里也没有这些词', (kind) => {
    const copy = CONFLICT_COPY[kind]
    const all = `${copy.message} ${copy.description} ${copy.action}`
    for (const term of ENGINEERING_TERMS) {
      expect(all, `${kind} 的文案里出现了「${term}」`).not.toContain(term)
    }
  })
})

describe('说清两件事：修改还在，以及下一步按什么', () => {
  it.each(KINDS)('%s 明说内容没丢', (kind) => {
    const copy = CONFLICT_COPY[kind]
    expect(copy.description).toMatch(/还在/)
  })

  it.each(KINDS)('%s 有一个明确的恢复动作', (kind) => {
    expect(CONFLICT_COPY[kind].action.length).toBeGreaterThan(0)
    expect(html(kind)).toContain(CONFLICT_COPY[kind].action)
  })

  it('⛔ 不写成"保存失败" —— 她的修改没有丢，这正是要说清楚的部分', () => {
    for (const kind of KINDS) {
      const copy = CONFLICT_COPY[kind]
      expect(`${copy.message}${copy.description}`).not.toContain('失败')
      expect(`${copy.message}${copy.description}`).not.toContain('错误')
    }
  })

  it('恢复文案沿用她已经认识的说法', () => {
    // 旧 UI 的「载入最新分类」「刷新状态，保留填写内容」照搬。
    expect(CONFLICT_COPY.tags.action).toBe('载入最新分类')
    expect(CONFLICT_COPY.schedule.action).toBe('刷新状态，保留填写内容')
    // DESIGN.md §9.4 指定的那一句。
    expect(CONFLICT_COPY.draft.action).toBe('载入最新内容并保留我的修改')
  })
})

describe('形态', () => {
  it('是 warning，不是 error —— 这是保护，不是故障', () => {
    expect(html('draft')).toContain('ant-alert-warning')
    expect(html('draft')).not.toContain('ant-alert-error')
  })

  it('role="alert"：她可能正在输入框里，没看着这块', () => {
    expect(html('draft')).toContain('role="alert"')
  })

  it('带 data-conflict，给浏览器级断言用', () => {
    for (const kind of KINDS) {
      expect(html(kind)).toContain(`data-conflict="${kind}"`)
    }
  })

  it('恢复中时按钮转圈', () => {
    const markup = renderToStaticMarkup(
      <ConflictRecovery kind="draft" onRecover={vi.fn()} recovering />,
    )
    expect(markup).toContain('ant-btn-loading')
  })

  it('组件只负责展示，恢复动作由业务 feature 给', () => {
    // 它不知道"载入最新"具体做什么 —— 不同冲突的恢复完全不同。
    const onRecover = vi.fn()
    renderToStaticMarkup(<ConflictRecovery kind="draft" onRecover={onRecover} />)
    expect(onRecover).not.toHaveBeenCalled()
  })

  it('actionLabel 能覆盖默认文案', () => {
    const markup = renderToStaticMarkup(
      <ConflictRecovery kind="draft" onRecover={vi.fn()} actionLabel="重新读取这一篇" />,
    )
    expect(markup).toContain('重新读取这一篇')
  })
})
