import { describe, expect, it, vi } from 'vitest'
import { renderToStaticMarkup } from 'react-dom/server'
import type { TableColumnsType } from 'antd'

import reviewList from '@/types/__fixtures__/review-list.json'
import historyList from '@/types/__fixtures__/history-list.json'
import type { TaskId } from '@/types/brands'
import {
  POST_COLUMN_KEYS,
  POST_ROW_HEIGHT,
  createPostColumns,
  postRowClassName,
} from './columns'
import type { PostRowBase } from './columns'

interface ReviewRow extends PostRowBase {
  readonly hard_alerts: readonly { code: string; label: string }[]
  readonly risk_count: number
  readonly author_flag: string | null
  readonly schedule: { at: string | null } | null
}

interface HistoryRow extends PostRowBase {
  readonly created_at?: string
  readonly account: string
  readonly read_only: boolean
}

const reviewRow: ReviewRow = {
  id: 'fa_x/1' as TaskId,
  platform: 'facebook',
  thumbnail_url: '/api/tasks/fa_x/1/image/0?variant=de',
  text_de_excerpt: 'Der neue Roboter räumt hinter deiner Katze auf.',
  image_count: 3,
  tags: ['自动猫砂盆', '新品', '促销'],
  status: 'pending_review',
  hard_alerts: [],
  risk_count: 2,
  author_flag: null,
  schedule: { at: '2026-09-13T17:00:00+02:00' },
}

const historyRow: HistoryRow = {
  id: 'in_y/2' as TaskId,
  platform: 'instagram',
  thumbnail_url: '/api/tasks/in_y/2/image/0?variant=de',
  text_de_excerpt: '',
  image_count: 1,
  tags: [],
  status: 'skipped',
  created_at: '2026-08-31T02:10:58Z',
  account: 'in_y',
  read_only: true,
}

const reviewColumns = () =>
  createPostColumns<ReviewRow>({
    columns: ['thumbnail', 'problem', 'summary', 'status', 'time', 'platform', 'tags', 'actions'],
    problem: (row) => row,
    time: { title: '排期时刻', zone: 'berlin', at: (row) => row.schedule?.at ?? null },
    actions: { render: () => <button type="button">更多</button> },
  })

const historyColumns = () =>
  createPostColumns<HistoryRow>({
    columns: ['thumbnail', 'summary', 'status', 'time', 'platform', 'tags'],
    time: { title: '原帖时间', zone: 'shanghai', at: (row) => row.created_at ?? null },
  })

type CellRenderer<T> = (value: unknown, row: T, index: number) => React.ReactNode

function renderers<T>(columns: TableColumnsType<T>): CellRenderer<T>[] {
  const out: CellRenderer<T>[] = []
  for (const column of columns) {
    if (!('render' in column)) continue
    const render = column.render
    if (typeof render !== 'function') continue
    out.push(render as CellRenderer<T>)
  }
  return out
}

function cell<T>(columns: TableColumnsType<T>, key: string, row: T): string {
  const column = columns.find((item) => item.key === key)
  if (!column || !('render' in column)) throw new Error(`没有 ${key} 这一列`)
  const render = column.render
  if (typeof render !== 'function') throw new Error(`${key} 列没有 render`)
  return renderToStaticMarkup(<>{(render as CellRenderer<T>)(undefined, row, 0)}</>)
}

describe('⛔ 没有批量，所以没有勾选', () => {
  it('列键里根本没有 selection 这个概念', () => {
    expect([...POST_COLUMN_KEYS]).not.toContain('selection')
    expect([...POST_COLUMN_KEYS]).not.toContain('checkbox')
  })

  it('造出来的列里没有 checkbox', () => {
    const markup = [
      ...renderers(reviewColumns()).map((render) =>
        renderToStaticMarkup(<>{render(undefined, reviewRow, 0)}</>),
      ),
      ...renderers(historyColumns()).map((render) =>
        renderToStaticMarkup(<>{render(undefined, historyRow, 0)}</>),
      ),
    ].join('')
    expect(markup).not.toContain('type="checkbox"')
    expect(markup).not.toContain('ant-checkbox')
  })

  it('工厂不产出 rowSelection —— 它只返回列', () => {
    const result: unknown = reviewColumns()
    expect(Array.isArray(result)).toBe(true)
  })
})

describe('队列与历史共用同一套列', () => {
  it('队列有问题列，历史没有', () => {
    expect(reviewColumns().map((column) => column.key)).toContain('problem')
    expect(historyColumns().map((column) => column.key)).not.toContain('problem')
  })

  it('列的顺序就是传进去的顺序', () => {
    expect(reviewColumns().map((column) => column.key)).toEqual([
      'thumbnail',
      'problem',
      'summary',
      'status',
      'time',
      'platform',
      'tags',
      'actions',
    ])
  })

  it('没传适配器的列整根消失，不是渲染成空', () => {
    const bare = createPostColumns<ReviewRow>({ columns: ['time', 'actions', 'status'] })
    expect(bare.map((column) => column.key)).toEqual(['status'])
  })

  it('两边的状态列用的是同一个 StatusTag', () => {
    expect(cell(reviewColumns(), 'status', reviewRow)).toContain('data-status="pending_review"')
    expect(cell(historyColumns(), 'status', historyRow)).toContain('data-status="skipped"')
  })

  it('两边的平台列用的是同一个 PlatformLabel', () => {
    expect(cell(reviewColumns(), 'platform', reviewRow)).toContain('data-platform="facebook"')
    expect(cell(historyColumns(), 'platform', historyRow)).toContain('data-platform="instagram"')
  })
})

describe('时刻列按各自的时区语义', () => {
  it('队列用业务时刻', () => {
    const markup = cell(reviewColumns(), 'time', reviewRow)
    expect(markup).toContain('data-zone="business"')
    expect(markup).toContain('17:00 北京')
  })

  it('历史用上海时刻', () => {
    const markup = cell(historyColumns(), 'time', historyRow)
    expect(markup).toContain('data-zone="shanghai"')
    expect(markup).toContain('上海')
  })

  it('没有时刻时显示占位', () => {
    const markup = cell(reviewColumns(), 'time', { ...reviewRow, schedule: null })
    expect(markup).toContain('—')
  })
})

describe('⛔ 不依赖详情字段', () => {
  const DETAIL_ONLY = [
    'text',
    'de_human',
    'de_machine',
    'machine_current',
    'machine_prompt_version',
    'localization',
    'body_highlights',
    'body_risks',
    'risk_scan',
    'images',
    'trail',
    'meta',
    'publication',
    'delivery',
    'read_only',
    'created_at',
  ]

  it('每一列的 render 都只读列表载荷里有的字段', () => {
    // 用 Proxy 把详情字段变成"一碰就抛"，然后把每个 cell 都渲染一遍。
    const guarded = new Proxy(reviewRow, {
      get(target, key) {
        if (typeof key === 'string' && DETAIL_ONLY.includes(key)) {
          throw new Error(`列工厂读了详情才有的字段：${key}`)
        }
        return Reflect.get(target, key)
      },
    })
    for (const render of renderers(reviewColumns())) {
      expect(() => renderToStaticMarkup(<>{render(undefined, guarded, 0)}</>)).not.toThrow()
    }
  })

  it('⛔ 没有「译文来源」四态列', () => {
    const titles = [...reviewColumns(), ...historyColumns()]
      .map((column) => String(column.title ?? ''))
      .join(' ')
    for (const forbidden of ['译文来源', '机器', '人工', '旧提示词']) {
      expect(titles).not.toContain(forbidden)
    }
  })

  it('"还没有德语译文"只从 text_de_excerpt 是否为空串判，不从 status 猜', () => {
    expect(cell(reviewColumns(), 'summary', { ...reviewRow, text_de_excerpt: '' })).toContain(
      '还没有德语译文',
    )
    const markup = cell<ReviewRow>(reviewColumns(), 'summary', {
      ...reviewRow,
      status: 'not_ready',
      text_de_excerpt: 'Hallo',
    })
    expect(markup).toContain('Hallo')
    expect(markup).not.toContain('还没有德语译文')
  })
})

describe('对真实载荷跑一遍', () => {
  it('26 条真实队列数据的每一列都渲染得出来', () => {
    const all = renderers(reviewColumns())
    for (const task of reviewList.tasks as unknown as ReviewRow[]) {
      for (const render of all) {
        expect(() => renderToStaticMarkup(<>{render(undefined, task, 0)}</>)).not.toThrow()
      }
    }
  })

  it('30 条真实历史数据同理', () => {
    const all = renderers(historyColumns())
    for (const task of historyList.tasks as unknown as HistoryRow[]) {
      for (const render of all) {
        expect(() => renderToStaticMarkup(<>{render(undefined, task, 0)}</>)).not.toThrow()
      }
    }
  })
})

describe('单元格细节', () => {
  it('缩略图带张数角标，只有一张时不带', () => {
    expect(cell(reviewColumns(), 'thumbnail', reviewRow)).toContain('aria-label="3 张图"')
    expect(cell(reviewColumns(), 'thumbnail', { ...reviewRow, image_count: 1 })).not.toContain(
      '张图',
    )
  })

  it('缩略图是装饰，alt 为空 —— 摘要列已经说了这是什么', () => {
    expect(cell(reviewColumns(), 'thumbnail', reviewRow)).toContain('alt=""')
  })

  it('没有缩略图时画空位，不渲染 <img src="">', () => {
    const markup = cell(reviewColumns(), 'thumbnail', { ...reviewRow, thumbnail_url: '' })
    expect(markup).not.toContain('<img')
    expect(markup).toContain('aria-hidden="true"')
  })

  it('分类最多两个，多的收进 +N', () => {
    const markup = cell(reviewColumns(), 'tags', reviewRow)
    expect(markup).toContain('自动猫砂盆')
    expect(markup).toContain('新品')
    expect(markup).toContain('+1')
    expect(markup).not.toContain('促销')
  })

  it('没有分类时说"未分类"，不是空白', () => {
    expect(cell(historyColumns(), 'tags', historyRow)).toContain('未分类')
  })

  it('行尾动作由调用方给，工厂不定业务动作', () => {
    expect(cell(reviewColumns(), 'actions', reviewRow)).toContain('更多')
  })
})

describe('行的视觉', () => {
  it('终态行整行降饱和', () => {
    expect(postRowClassName({ ...reviewRow, status: 'skipped' })).not.toBe('')
    expect(postRowClassName({ ...reviewRow, status: 'handed_off' })).not.toBe('')
  })

  it('not_ready 与进行中的行不降饱和', () => {
    expect(postRowClassName({ ...reviewRow, status: 'not_ready' })).toBe('')
    expect(postRowClassName({ ...reviewRow, status: 'pending_review' })).toBe('')
    expect(postRowClassName({ ...reviewRow, status: 'approved' })).toBe('')
  })

  it('行高从 token 来，是 48', () => {
    expect(POST_ROW_HEIGHT).toBe(48)
  })
})

describe('工厂不发起任何请求', () => {
  it('渲染整套列不碰 fetch', () => {
    const spy = vi.fn()
    vi.stubGlobal('fetch', spy)
    for (const render of renderers(reviewColumns())) {
      renderToStaticMarkup(<>{render(undefined, reviewRow, 0)}</>)
    }
    expect(spy).not.toHaveBeenCalled()
    vi.unstubAllGlobals()
  })
})
