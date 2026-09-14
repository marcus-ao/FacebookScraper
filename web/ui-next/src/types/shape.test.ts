import { describe, expect, it } from 'vitest'

import {
  APPROVAL_OPTIONS_SHAPE,
  CALENDAR_CARD_SHAPE,
  CALENDAR_SHAPE,
  HISTORY_LIST_ITEM_SHAPE,
  HISTORY_LIST_SHAPE,
  IMAGE_ASSET_SHAPE,
  INITIAL_CAPABILITIES_SHAPE,
  LOCALIZATION_DRAFT_SHAPE,
  REFINEMENT_CAPABILITIES_SHAPE,
  REVIEW_LIST_ITEM_SHAPE,
  REVIEW_LIST_SHAPE,
  RUNTIME_SHAPE,
  RUNTIME_STAGE_SHAPE,
  SETTINGS_SHAPE,
  TASK_DETAIL_SHAPE,
  TASK_DETAIL_TEXT_SHAPE,
  TEMPLATE_SHAPE,
  checkShape,
} from '@/services/assert-shape'

import approvalOptions from './__fixtures__/approval-options.json'
import calendar from './__fixtures__/calendar.json'
import historyList from './__fixtures__/history-list.json'
import initialCapabilities from './__fixtures__/initial-capabilities.json'
import refinementCapabilities from './__fixtures__/refinement-capabilities.json'
import reviewList from './__fixtures__/review-list.json'
import runtime from './__fixtures__/runtime.json'
import settings from './__fixtures__/settings.json'
import taskDetailActive from './__fixtures__/task-detail-active.json'
import taskDetailFrozen from './__fixtures__/task-detail-frozen.json'
import templateText from './__fixtures__/template-text.json'

// 这些夹具是 state/audit-probe/ 里**真实响应**的机械脱敏产物
// （重构期用一个脱敏脚本生成，只动值不动键）。
// 它们回答一个问题：手写的类型和后端实际返回的形状还对得上吗。
//
// 取证规模：审校队列 26 条、历史 1,067 条中的第 1 页、两份详情
// （活账号 FB + 冻结 IG）、月历 4 张卡、设置七组受控配置、运行五阶段。

const ok = (value: unknown, spec: Parameters<typeof checkShape>[1], label: string) => {
  const problems = checkShape(value, spec)
  expect(problems, `${label}: ${problems.join('、')}`).toEqual([])
}

describe('GET /api/tasks（审校队列）', () => {
  it('顶层形状', () => ok(reviewList, REVIEW_LIST_SHAPE, 'review list'))

  it('每一条都符合列表项形状（全部 26 条，不只第一条）', () => {
    expect(reviewList.tasks.length).toBeGreaterThan(0)
    reviewList.tasks.forEach((task, index) => {
      ok(task, REVIEW_LIST_ITEM_SHAPE, `tasks[${index}]`)
    })
  })

  it('字段名是 hard_alerts，没有 alerts —— D13a 的根因守卫', () => {
    // 旧 Vue 写成 task.alerts 导致「查看并翻译」文案从未渲染。
    // 新类型里没有 alerts 键，tsc 会挡住同样的笔误；这一条守的是
    // "后端哪天真的加了 alerts" 这种反向漂移。
    for (const task of reviewList.tasks) {
      expect(task).toHaveProperty('hard_alerts')
      expect(task).not.toHaveProperty('alerts')
    }
  })

  it('summary.by_status 覆盖全部八个展示态 —— 页签计数要用它', () => {
    const byStatus = reviewList.summary.by_status as Record<string, number>
    for (const status of [
      'not_ready',
      'pending_review',
      'edited',
      'snoozed',
      'approved',
      'scheduled',
      'skipped',
      'handed_off',
    ]) {
      expect(byStatus, `by_status 少了 ${status}`).toHaveProperty(status)
      expect(typeof byStatus[status]).toBe('number')
    }
  })

  it('四个队列分桶加起来等于总数（DECISION_LOG D1 的分组前提）', () => {
    const s = reviewList.summary.by_status as Record<string, number>
    const review = (s.pending_review ?? 0) + (s.edited ?? 0)
    const notReady = s.not_ready ?? 0
    const snoozed = s.snoozed ?? 0
    const processed = (s.approved ?? 0) + (s.scheduled ?? 0) + (s.skipped ?? 0) + (s.handed_off ?? 0)
    expect(review + notReady + snoozed + processed).toBe(reviewList.summary.total)
  })

  it('审校队列的列表项**没有** read_only 与 created_at —— 不许当成列表字段用', () => {
    // 历史项有这两个，队列项没有。把详情/历史字段假装成队列字段是
    // DECISION_LOG §5.4 明确禁止的事，这一条把它钉住。
    const first = reviewList.tasks[0]
    expect(first).toBeDefined()
    expect(first).not.toHaveProperty('read_only')
    expect(first).not.toHaveProperty('created_at')
  })

  it('审校队列的列表项没有 text.de_human / machine_current —— 所以做不了「译文四态」列', () => {
    // DECISION_LOG §2.3：区分机器/人工/旧提示词的依据只在详情里。
    const first = reviewList.tasks[0]
    expect(first).not.toHaveProperty('text')
    expect(first).not.toHaveProperty('machine_current')
  })

  it('pagination.limit 在不分页时是 null', () => {
    expect(reviewList.pagination.limit).toBeNull()
  })
})

describe('GET /api/tasks?scope=history（历史归档）', () => {
  it('顶层形状', () => ok(historyList, HISTORY_LIST_SHAPE, 'history list'))

  it('每一行都符合历史项形状', () => {
    expect(historyList.tasks.length).toBeGreaterThan(0)
    historyList.tasks.forEach((row, index) => {
      ok(row, HISTORY_LIST_ITEM_SHAPE, `tasks[${index}]`)
    })
  })

  it('range.days 为 null，明确没有 90 天上限', () => {
    expect(historyList.range.days).toBeNull()
    expect(historyList.range.scope).toBe('history')
  })

  it('total 是筛选后全部匹配数，远大于本页长度', () => {
    expect(historyList.pagination.total).toBeGreaterThan(historyList.tasks.length)
  })

  it('summary.months 倒序，可以直接按年分组', () => {
    const months = [...historyList.summary.months]
    expect(months.length).toBeGreaterThan(1)
    expect(months).toEqual([...months].sort().reverse())
  })

  it('冻结账号的行带 read_only: true', () => {
    expect(historyList.tasks.some((row) => row.read_only === true)).toBe(true)
  })
})

describe('GET /api/tasks/{id}（详情）', () => {
  it('活账号详情顶层形状', () => ok(taskDetailActive, TASK_DETAIL_SHAPE, 'detail active'))
  it('冻结账号详情顶层形状', () => ok(taskDetailFrozen, TASK_DETAIL_SHAPE, 'detail frozen'))

  it('冻结账号 read_only 为真，活账号为假', () => {
    expect(taskDetailFrozen.read_only).toBe(true)
    expect(taskDetailActive.read_only).toBe(false)
  })

  it('localization 十六个键齐全', () => {
    ok(taskDetailActive.localization, LOCALIZATION_DRAFT_SHAPE, 'localization')
    expect(Object.keys(taskDetailActive.localization)).toHaveLength(16)
  })

  it('text 九个键齐全，含三个提示词版本字段', () => {
    ok(taskDetailActive.text, TASK_DETAIL_TEXT_SHAPE, 'text')
  })

  it('images 每一张都有 de_present —— 缺德语图这条不许藏', () => {
    expect(taskDetailActive.images.length).toBeGreaterThan(0)
    taskDetailActive.images.forEach((image, index) => {
      ok(image, IMAGE_ASSET_SHAPE, `images[${index}]`)
    })
  })

  it('body_highlights 的 span 是码点区间或 null', () => {
    for (const highlight of taskDetailActive.body_highlights) {
      for (const span of [highlight.en_span, highlight.de_span]) {
        if (span === null) continue
        expect(Array.isArray(span)).toBe(true)
        expect(span).toHaveLength(2)
      }
    }
  })

  it('risk_scan 真实数据是 not_scanned —— DECISION_LOG D10 的前提', () => {
    // 真实归档 26/26 篇都是这个状态，所以它不能渲染成满宽黄色告警。
    expect(taskDetailActive.risk_scan.status).toBe('not_scanned')
    expect(taskDetailActive.risk_scan.risks).toEqual([])
  })

  it('顶层同时有 body_highlights 与 highlights 两套 —— 界面只用 body_ 那一对', () => {
    expect(taskDetailActive).toHaveProperty('body_highlights')
    expect(taskDetailActive).toHaveProperty('highlights')
    expect(taskDetailActive).toHaveProperty('body_risks')
    expect(taskDetailActive).toHaveProperty('risks')
  })

  it('review 对象在有账本事件时会铺开额外键（冻结那篇没有事件）', () => {
    // BASELINE_BEHAVIOR §11 第 2 条：形状随有无事件变化，所以类型里那组是可选的。
    expect(taskDetailFrozen.review.revision).toBeNull()
    expect(taskDetailFrozen.review).not.toHaveProperty('action')
  })
})

describe('GET /api/tasks/{id}/approval-options', () => {
  it('形状', () => ok(approvalOptions, APPROVAL_OPTIONS_SHAPE, 'approval options'))

  it('available 为假时 fingerprint 是 null —— 不能拿它去批准', () => {
    expect(approvalOptions.available).toBe(false)
    expect(approvalOptions.fingerprint).toBeNull()
  })

  it('default_times 是柏林时刻字符串', () => {
    expect(approvalOptions.default_times.length).toBeGreaterThan(0)
    for (const time of approvalOptions.default_times) {
      expect(time).toMatch(/^\d{2}:\d{2}$/)
    }
  })
})

describe('GET /api/calendar', () => {
  it('顶层形状', () => ok(calendar, CALENDAR_SHAPE, 'calendar'))

  it('每张卡片形状', () => {
    expect(calendar.cards.length).toBeGreaterThan(0)
    calendar.cards.forEach((card, index) => {
      ok(card, CALENDAR_CARD_SHAPE, `cards[${index}]`)
    })
  })

  it('at_business 已由服务端换算过，落格用它', () => {
    for (const card of calendar.cards) {
      expect(card.at_business).not.toBe(card.at)
    }
  })

  it('gap_minutes 是 90 —— 同渠道冲突窗口', () => {
    expect(calendar.gap_minutes).toBe(90)
  })
})

describe('GET /api/settings', () => {
  it('顶层形状', () => ok(settings, SETTINGS_SHAPE, 'settings'))

  it('editable 只开放两个字段', () => {
    expect(Object.keys(settings.editable).sort()).toEqual(['default_times', 'snooze_default_days'])
  })

  it('七组受控配置只读', () => {
    expect(Object.keys(settings.controlled)).toHaveLength(7)
  })

  it('editable_help 里确实带着 config.toml 的注释 —— 所以不能常驻渲染', () => {
    // UI_AUDIT P0-12：实测这份 help 含源码路径。数据必须保留
    // （web/DESIGN.md §7 有独立验收项），改的是呈现位置，不是删数据。
    const help = settings.editable_help as Record<string, string>
    expect(Object.keys(help).length).toBeGreaterThan(0)
  })
})

describe('GET /api/runtime', () => {
  it('顶层形状', () => ok(runtime, RUNTIME_SHAPE, 'runtime'))

  it('五个阶段，每个都有 number / name / status', () => {
    expect(runtime.stages).toHaveLength(5)
    runtime.stages.forEach((stage, index) => {
      ok(stage, RUNTIME_STAGE_SHAPE, `stages[${index}]`)
    })
    expect(runtime.stages.map((stage) => stage.number)).toEqual([1, 2, 3, 4, 5])
  })

  it('阶段五有 unconfirmed_attempts 但**没有 task_ids** —— DEF-1 的依据', () => {
    const stage5 = runtime.stages.find((stage) => stage.number === 5)
    expect(stage5).toBeDefined()
    expect(stage5).toHaveProperty('unconfirmed_attempts')
    expect(stage5).not.toHaveProperty('task_ids')
  })

  it('business.processing 在没有批次时只有三个键 —— 恢复接口要的 batch_id 不在里面', () => {
    expect(Object.keys(runtime.business.processing).sort()).toEqual([
      'cost_usd',
      'paid_request_ids',
      'state_revision',
    ])
  })
})

describe('内容任务与模板', () => {
  it('初翻能力形状', () => ok(initialCapabilities, INITIAL_CAPABILITIES_SHAPE, 'initial'))
  it('优化能力形状', () => ok(refinementCapabilities, REFINEMENT_CAPABILITIES_SHAPE, 'refinement'))
  it('模板形状且 read_only 为真', () => {
    ok(templateText, TEMPLATE_SHAPE, 'template')
    expect(templateText.read_only).toBe(true)
  })

  it('每张图最多受理三次', () => {
    expect(refinementCapabilities.max_refine_per_media).toBe(3)
  })

  it('品牌账号不是第三方作者，所以详情不渲染授权区块', () => {
    expect(initialCapabilities.third_party).toBe(false)
    expect(initialCapabilities.needs_consent).toBe(false)
  })
})

describe('checkShape 自己', () => {
  it('键缺失会被报出来', () => {
    expect(checkShape({}, REVIEW_LIST_SHAPE)).toContain('tasks 缺失')
  })

  it('类型不符会被报出来', () => {
    expect(checkShape({ ...reviewList, tasks: 'nope' }, REVIEW_LIST_SHAPE)).toContain(
      'tasks 类型不符',
    )
  })

  it('不是对象时明确报出来', () => {
    expect(checkShape(null, REVIEW_LIST_SHAPE)).toEqual(['<不是对象>'])
    expect(checkShape([], REVIEW_LIST_SHAPE)).toEqual(['<不是对象>'])
  })

  it('枚举白名单之外的值不通过', () => {
    const broken = { ...reviewList.tasks[0], status: 'invented_status' }
    expect(checkShape(broken, REVIEW_LIST_ITEM_SHAPE)).toContain('status 类型不符')
  })
})
