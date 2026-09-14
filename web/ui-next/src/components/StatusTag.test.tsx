import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'
import { renderToStaticMarkup } from 'react-dom/server'

import { STATUS_LABEL } from '@/lib/format'
import type { DisplayStatus } from '@/types/domain'
import { STATUS_TONE, StatusTag, isTerminalStatus } from './StatusTag'

const ALL: readonly DisplayStatus[] = [
  'not_ready',
  'pending_review',
  'edited',
  'snoozed',
  'approved',
  'scheduled',
  'skipped',
  'handed_off',
]

const html = (status: DisplayStatus) => renderToStaticMarkup(<StatusTag status={status} />)

describe('八个状态的文案', () => {
  it.each([
    ['not_ready', '未就绪'],
    ['pending_review', '待我审'],
    ['edited', '已修改'],
    ['snoozed', '已挂起'],
    ['approved', '已通过'],
    ['scheduled', '已排期'],
    ['skipped', '这篇不发'],
    ['handed_off', '已交人工处理'],
  ] as const)('%s 显示「%s」', (status, label) => {
    expect(html(status)).toContain(`>${label}<`)
  })

  it('not_ready 是「未就绪」，不是旧 UI 的「待处理」', () => {
    // DECISION_LOG D1 第 2 条：新队列有四个页签，「待我审」才是她的待办，
    // 把 not_ready 也叫「待处理」会让两个概念撞车。
    expect(STATUS_LABEL.not_ready).toBe('未就绪')
    expect(html('not_ready')).not.toContain('待处理')
  })

  it('scheduled 是「已排期」，⛔ 绝不写成「已发布」', () => {
    // 它只表示排期被回读确认，不表示内容已经公开（web/DESIGN.md §9）。
    // 这是业务口径，不是措辞偏好。
    expect(html('scheduled')).toContain('已排期')
    for (const status of ALL) {
      expect(html(status)).not.toContain('已发布')
    }
  })

  it('八个状态一个不少，文案两两不同', () => {
    const labels = ALL.map((status) => STATUS_LABEL[status])
    expect(new Set(labels).size).toBe(8)
  })
})

describe('skipped 不用红色', () => {
  it('它是中性档，不是错误档', () => {
    // 「这篇不发」是她有意做的正常业务决定，不是系统出了问题。
    expect(STATUS_TONE.skipped).toBe('neutral')
    expect(html('skipped')).toContain('data-tone="neutral"')
  })

  it('渲染出来不带 antd 的 error / danger / red 类', () => {
    const markup = html('skipped')
    for (const forbidden of ['ant-tag-error', 'ant-tag-red', 'ant-tag-danger', 'ant-tag-volcano']) {
      expect(markup).not.toContain(forbidden)
    }
  })

  it('中性档的 CSS 里一个错误色变量都没有', () => {
    // 断言到样式表这一层，免得以后有人在 .neutral 里加一句红色就过去了。
    const css = readFileSync(
      fileURLToPath(new URL('./StatusTag.module.css', import.meta.url)),
      'utf8',
    )
    const neutralBlock = /\.neutral\s*\{([^}]*)\}/.exec(css)?.[1] ?? ''
    expect(neutralBlock).not.toBe('')
    expect(neutralBlock).not.toContain('--rc-error')
    expect(neutralBlock).not.toContain('--rc-risk')
    // 整份样式表里也不该出现错误色 —— 八个状态没有一个配得上红色。
    expect(css).not.toContain('--rc-error')
  })
})

describe('状态不只靠颜色', () => {
  it('每一个都带文字', () => {
    // 色觉障碍、12px 小面积的红黄区分度、以及旧 UI 的实证：
    // .row.done 与 .row.waiting 用同一个灰底，两者视觉上完全不可区分。
    for (const status of ALL) {
      const text = html(status).replace(/<[^>]+>/g, '').trim()
      expect(text, status).toBe(STATUS_LABEL[status])
    }
  })

  it('每一个都带 data-status，给浏览器级断言用', () => {
    for (const status of ALL) {
      expect(html(status)).toContain(`data-status="${status}"`)
    }
  })
})

describe('三档视觉重量', () => {
  it('待办 / 中性 / 已推进，就这三档', () => {
    expect(new Set(Object.values(STATUS_TONE))).toEqual(new Set(['todo', 'neutral', 'done']))
  })

  it('只有她的待办是 todo 档', () => {
    const todo = ALL.filter((status) => STATUS_TONE[status] === 'todo')
    expect(todo).toEqual(['pending_review', 'edited'])
  })

  it('done 档只有已通过与已排期', () => {
    const done = ALL.filter((status) => STATUS_TONE[status] === 'done')
    expect(done).toEqual(['approved', 'scheduled'])
  })

  it('八个状态每一个都有档位，没有漏的', () => {
    for (const status of ALL) expect(STATUS_TONE[status]).toBeDefined()
  })
})

describe('终态', () => {
  it('只有 skipped 与 handed_off', () => {
    expect(ALL.filter(isTerminalStatus)).toEqual(['skipped', 'handed_off'])
  })

  it('approved 不是终态 —— 它是「正在提交」', () => {
    expect(isTerminalStatus('approved')).toBe(false)
  })

  it('not_ready 不是终态', () => {
    // 区分「已经处理完」和「还没准备好」正是这个判定存在的理由。
    expect(isTerminalStatus('not_ready')).toBe(false)
  })
})

describe('低视觉重量', () => {
  it('12px、无边框', () => {
    const css = readFileSync(
      fileURLToPath(new URL('./StatusTag.module.css', import.meta.url)),
      'utf8',
    )
    expect(css).toContain('var(--rc-font-meta)')
    // antd 6 的无边框写法是 variant="filled"，落成 ant-tag-filled。
    // （`bordered={false}` 在 antd 6 已废弃，会打 deprecation 警告。）
    expect(html('not_ready')).toContain('ant-tag-filled')
    expect(html('not_ready')).not.toContain('ant-tag-outlined')
  })
})
