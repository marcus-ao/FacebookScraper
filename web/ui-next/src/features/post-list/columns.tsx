import { Tag, Tooltip } from 'antd'
import { Link } from 'react-router'
import type { TableColumnsType } from 'antd'

import { PlatformLabel } from '@/components/PlatformLabel'
import { ProblemIndicator } from '@/components/ProblemIndicator'
import type { ProblemSource } from '@/components/ProblemIndicator'
import { StatusTag, isTerminalStatus } from '@/components/StatusTag'
import { BerlinTime, ShanghaiTime } from '@/components/Time'
import { tokens } from '@/app/theme'
import { cx } from '@/lib/css'
import type { DisplayStatus, Platform } from '@/types/domain'
import type { TaskId } from '@/types/brands'
import styles from './columns.module.css'

/**
 * 帖子行的列工厂。**审校队列（Stage C）与历史归档（Stage E′）共用这一套。**
 *
 * 这是 B2 最要紧的一项。不做它的后果在旧 UI 里看得见：队列和历史各写一套行，
 * 于是同一个状态两种画法、同一个平台三种写法、缩略图两种尺寸（P2-3）。
 *
 * ──────────────────────────────────────────────────────────────────────────
 * 三条硬约束
 * ──────────────────────────────────────────────────────────────────────────
 *
 * **1. 没有批量，所以没有勾选。**
 *
 * DECISION_LOG D3：第一版不显示 checkbox 列、不启用 `rowSelection`、
 * 不做批量 API。架构上将来能加，但**不为"预留"给她显示一个没有功能的控件**。
 * 这个文件里因此连 `selection` 这个列键都不存在。
 *
 * **2. 只用列表载荷里真实存在的字段。**
 *
 * `PostRowBase` 就是队列项与历史项的交集。详情才有的
 * `text.de_human` / `machine_current` / `machine_prompt_version` 一个都不碰。
 *
 * ⛔ 所以**没有「译文来源」四态列**（DECISION_LOG §2.3）。列表 API 无法区分
 * "机器 / 人工 / 旧提示词"，从 `status` 猜是明令禁止的。列表侧唯一可靠的
 * 译文依据是 `text_de_excerpt` 是否为空串 —— 要用就只能表达
 * 「未就绪 / 有译文」这两态，不能更多。真要四态就加后端字段（延期项 DEF-5）。
 *
 * **3. 不做成一个 30 个 boolean 的巨型组件。**
 *
 * 差异只有三处（要不要问题列、时刻列取哪个字段、行尾动作画什么），
 * 所以接口是：一个列清单 + 三个可选适配器。队列和历史各传自己那份。
 */

/** 队列项与历史项**都有**的字段。这就是列工厂允许看见的全部。 */
export interface PostRowBase {
  readonly id: TaskId
  readonly platform: Platform
  readonly thumbnail_url: string
  /** 90 字截断；空串表示还没有德语译文。 */
  readonly text_de_excerpt: string
  readonly image_count: number
  readonly tags: readonly string[]
  readonly status: DisplayStatus
}

export const POST_COLUMN_KEYS = [
  'thumbnail',
  'problem',
  'summary',
  'status',
  'time',
  'platform',
  'tags',
  'actions',
] as const

export type PostColumnKey = (typeof POST_COLUMN_KEYS)[number]

/** 时刻列取哪个字段、按哪套时区语义渲染。 */
export interface TimeColumnSpec<T> {
  readonly title: string
  /** `berlin` = 排期时刻；`shanghai` = 她自己的时间（创建、唤醒）。 */
  readonly zone: 'berlin' | 'shanghai'
  readonly at: (row: T) => string | null
  readonly width?: number
}

export interface ActionsColumnSpec<T> {
  readonly title?: string
  readonly render: (row: T) => React.ReactNode
  readonly width?: number
}

export interface PostColumnOptions<T extends PostRowBase> {
  readonly summaryHref?: (row: T) => string
  readonly summaryNote?: (row: T) => React.ReactNode
  /** 要哪些列、什么顺序。**不传的列不渲染**，不是渲染成空。 */
  readonly columns: readonly PostColumnKey[]
  /**
   * 问题列的数据来源。队列传 `(row) => row`（它自己就有那三个字段），
   * 历史**不传** —— 历史载荷里没有 `hard_alerts` / `risk_count` / `author_flag`，
   * 硬造一个空对象就是在假装后端支持它。
   */
  readonly problem?: (row: T) => ProblemSource
  readonly time?: TimeColumnSpec<T>
  readonly actions?: ActionsColumnSpec<T>
}

const { layout } = tokens

/**
 * 造列。传了适配器才会出现对应的列 —— 少传一个适配器，那一列就整根消失，
 * 不会退化成一列空白。
 */
export function createPostColumns<T extends PostRowBase>(
  options: PostColumnOptions<T>,
): TableColumnsType<T> {
  const out: TableColumnsType<T> = []

  for (const key of options.columns) {
    switch (key) {
      case 'thumbnail':
        out.push({
          key,
          title: '',
          width: layout.thumbnailColumnWidth,
          render: (_value: unknown, row: T) => (
            <span className={cx(styles.thumbWrap)}>
              {row.thumbnail_url === '' ? (
                // 没有缩略图时画一个空位，**不要**渲染 <img src="">：
                // 那会变成浏览器的碎图标，看起来像故障，而"这一篇没有图"
                // 是一个正常状态。
                <span className={cx(styles.thumb, styles.thumbEmpty)} aria-hidden="true" />
              ) : (
                <img
                  className={cx(styles.thumb)}
                  src={row.thumbnail_url}
                  alt=""
                  loading="lazy"
                  // 内在尺寸也从 token 来：图还没加载时占住位置，行高不跳。
                  width={layout.thumbnailSize}
                  height={layout.thumbnailSize}
                />
              )}
              {row.image_count > 1 ? (
                <span className={cx(styles.count)} aria-label={`${row.image_count} 张图`}>
                  {row.image_count}
                </span>
              ) : null}
            </span>
          ),
        })
        break

      case 'problem': {
        const adapter = options.problem
        // 没有适配器就没有这一列。历史载荷里没有判据，不画。
        if (!adapter) break
        out.push({
          key,
          title: '',
          width: layout.problemColumnWidth,
          align: 'center',
          render: (_value: unknown, row: T) => <ProblemIndicator source={adapter(row)} />,
        })
        break
      }

      case 'summary':
        out.push({
          key,
          title: '摘要',
          // 自适应：这是行里唯一该抢宽度的列。
          ellipsis: true,
          render: (_value: unknown, row: T) => {
            const content = row.text_de_excerpt === '' ? (
              // 空串是列表侧唯一可靠的"还没有德语译文"依据。
              // 不从 status 猜，也不假装知道译文是机器的还是人工的。
              <span className={cx(styles.noText)}>还没有德语译文</span>
            ) : (
              <Tooltip title={row.text_de_excerpt} placement="topLeft">
                <span className={cx(styles.summary)}>{row.text_de_excerpt}</span>
              </Tooltip>
            )
            return options.summaryHref ? <span className={cx(styles.summaryCell)}>
              <Link className={cx(styles.summaryLink)} to={options.summaryHref(row)}>{content}</Link>
              {options.summaryNote?.(row)}
            </span> : content
          },
        })
        break

      case 'status':
        out.push({
          key,
          title: '状态',
          width: layout.statusColumnWidth,
          render: (_value: unknown, row: T) => <StatusTag status={row.status} />,
        })
        break

      case 'time': {
        const spec = options.time
        if (!spec) break
        out.push({
          key,
          title: spec.title,
          width: spec.width ?? layout.timeColumnWidth,
          render: (_value: unknown, row: T) =>
            spec.zone === 'berlin' ? (
              <BerlinTime at={spec.at(row)} />
            ) : (
              <ShanghaiTime at={spec.at(row)} />
            ),
        })
        break
      }

      case 'platform':
        out.push({
          key,
          title: '平台',
          width: layout.platformColumnWidth,
          render: (_value: unknown, row: T) => <span className={cx(styles.platform)}><PlatformLabel platform={row.platform} /></span>,
        })
        break

      case 'tags':
        out.push({
          key,
          title: '分类',
          width: layout.tagsColumnWidth,
          responsive: ['xxl'],
          render: (_value: unknown, row: T) => <TagCell tags={row.tags} />,
        })
        break

      case 'actions': {
        const spec = options.actions
        if (!spec) break
        out.push({
          key,
          title: spec.title ?? '',
          width: spec.width ?? layout.actionsColumnWidth,
          align: 'center',
          render: (_value: unknown, row: T) => spec.render(row),
        })
        break
      }
    }
  }

  return out
}

/**
 * 行的类名。**终态整行降饱和**，把「已经处理完」和「还没准备好」分开 ——
 * 旧 UI 里两者同一个灰底，视觉上完全不可区分（DESIGN.md §7 第 3 条）。
 */
export function postRowClassName(row: PostRowBase): string {
  return isTerminalStatus(row.status) ? cx(styles.terminal) : ''
}

/** 表格的固定行高。48px 的推导见 DESIGN.md §4.1（1366×768 首屏 12 行）。 */
export const POST_ROW_HEIGHT = layout.tableRowHeight

/** 最多两个分类，多的收进 `+N`。一屏 Tag 不超过 8 个（DESIGN.md §1.2）。 */
function TagCell({ tags }: { tags: readonly string[] }) {
  if (tags.length === 0) return <span className={cx(styles.noText)}>未分类</span>
  const shown = tags.slice(0, 2)
  const rest = tags.slice(2)
  return (
    <span className={cx(styles.tags)}>
      {shown.map((tag) => (
        <Tag key={tag} variant="filled" className={cx(styles.tag)}>
          {tag}
        </Tag>
      ))}
      {rest.length > 0 ? (
        <Tooltip title={rest.join('、')}>
          <Tag variant="filled" className={cx(styles.tag)}>
            +{rest.length}
          </Tag>
        </Tooltip>
      ) : null}
    </span>
  )
}
