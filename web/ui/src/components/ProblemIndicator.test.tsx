import { describe, expect, it } from 'vitest'
import { renderToStaticMarkup } from 'react-dom/server'

import { ProblemIndicator, problemOf, problemTooltip } from './ProblemIndicator'
import type { ProblemSource } from './ProblemIndicator'

const none: ProblemSource = { hard_alerts: [], risk_count: 0, author_flag: null }
const alert = { code: 'unknown_collaborator', label: '第三方作者 @partner 不在白名单' }

const html = (source: ProblemSource) => renderToStaticMarkup(<ProblemIndicator source={source} />)

describe('优先级：硬闸 > 风险 > 第三方 > 无', () => {
  it('三个都有时选硬闸', () => {
    expect(
      problemOf({ hard_alerts: [alert], risk_count: 5, author_flag: '第三方作者 @x' }),
    ).toBe('hard_alert')
  })

  it('没有硬闸时选风险', () => {
    expect(problemOf({ hard_alerts: [], risk_count: 5, author_flag: '第三方作者 @x' })).toBe('risk')
  })

  it('只剩第三方时选第三方', () => {
    expect(problemOf({ hard_alerts: [], risk_count: 0, author_flag: '第三方作者 @x' })).toBe(
      'third_party',
    )
  })

  it('都没有就是 none', () => {
    expect(problemOf(none)).toBe('none')
  })

  it('author_flag 常态是 null —— 真实数据 26/26 篇都是', () => {
    // reader.py `_author_kind`：合作帖是常态，每行都标就是噪声。
    expect(problemOf({ hard_alerts: [], risk_count: 0, author_flag: null })).toBe('none')
    // 空串也当没有，不因为后端给了空字符串就画一个图标。
    expect(problemOf({ hard_alerts: [], risk_count: 0, author_flag: '' })).toBe('none')
  })

  it('risk_count 为 0 不算风险', () => {
    expect(problemOf({ hard_alerts: [], risk_count: 0, author_flag: null })).toBe('none')
  })
})

describe('渲染', () => {
  it.each([
    ['hard_alert', { hard_alerts: [alert], risk_count: 0, author_flag: null }],
    ['risk', { hard_alerts: [], risk_count: 2, author_flag: null }],
    ['third_party', { hard_alerts: [], risk_count: 0, author_flag: '第三方作者 @x' }],
    ['none', none],
  ] as const)('%s 标在 data-problem 上', (kind, source) => {
    expect(html(source)).toContain(`data-problem="${kind}"`)
  })

  it('没有问题时占位但不画图标 —— "一切正常"不需要宣告', () => {
    const markup = html(none)
    expect(markup).toContain('data-problem="none"')
    expect(markup).not.toContain('anticon')
    expect(markup).toContain('aria-hidden="true"')
  })

  it('有问题时图标带无障碍名字，名字就是那句说明', () => {
    const markup = html({ hard_alerts: [alert], risk_count: 0, author_flag: null })
    expect(markup).toContain('role="img"')
    expect(markup).toContain(`aria-label="${alert.label}"`)
  })
})

describe('Tooltip 里那句话', () => {
  it('硬闸列出全部告警，多条用分号连', () => {
    const second = { code: 'material_gate', label: '素材不齐（正文/图片/轮播完整性未通过）' }
    expect(
      problemTooltip({ hard_alerts: [alert, second], risk_count: 0, author_flag: null }),
    ).toBe(`${alert.label}；${second.label}`)
  })

  it('风险说清是几处', () => {
    expect(problemTooltip({ hard_alerts: [], risk_count: 3, author_flag: null })).toBe(
      '3 处语义风险待确认',
    )
  })

  it('第三方直接用后端给的那句话', () => {
    expect(
      problemTooltip({ hard_alerts: [], risk_count: 0, author_flag: '第三方作者 @creator' }),
    ).toBe('第三方作者 @creator')
  })

  it('没有问题就没有 Tooltip', () => {
    expect(problemTooltip(none)).toBeNull()
  })

  it('⛔ 不出现字段名这类工程语言', () => {
    const tips = [
      problemTooltip({ hard_alerts: [alert], risk_count: 0, author_flag: null }),
      problemTooltip({ hard_alerts: [], risk_count: 3, author_flag: null }),
    ]
    for (const tip of tips) {
      expect(tip).not.toContain('hard_alerts')
      expect(tip).not.toContain('risk_count')
      expect(tip).not.toContain('author_flag')
    }
  })
})

describe('这一列的数据来源只有列表载荷里的三个字段', () => {
  it('传一个只有这三个键的对象就够了', () => {
    // 类型上 ProblemSource 就是这三个键；这条是运行期再确认一次，
    // 免得实现里偷偷读了第四个字段（比如详情才有的 text 或 risk_scan）。
    const proxy = new Proxy(
      { hard_alerts: [], risk_count: 1, author_flag: null } as ProblemSource,
      {
        get(target, key) {
          if (typeof key === 'string' && !['hard_alerts', 'risk_count', 'author_flag'].includes(key)) {
            throw new Error(`ProblemIndicator 读了不属于列表载荷的字段：${key}`)
          }
          return Reflect.get(target, key)
        },
      },
    )
    expect(() => html(proxy)).not.toThrow()
  })
})
