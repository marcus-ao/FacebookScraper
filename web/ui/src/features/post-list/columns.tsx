import { Tag, Tooltip } from 'antd'
import { Link } from 'react-router'
import type { TableColumnsType } from 'antd'

import { PlatformLabel } from '@/components/PlatformLabel'
import { ProblemIndicator } from '@/components/ProblemIndicator'
import type { ProblemSource } from '@/components/ProblemIndicator'
import { StatusTag, isTerminalStatus } from '@/components/StatusTag'
import { BusinessTime, ShanghaiTime } from '@/components/Time'
import { tokens } from '@/app/theme'
import { cx } from '@/lib/css'
import type { DisplayStatus, Platform } from '@/types/domain'
import type { TaskId } from '@/types/brands'
import styles from './columns.module.css'

/** 队列与历史共用列定义，只读取列表字段；差异由适配器提供。 */

export interface PostRowBase {
  readonly id: TaskId
  readonly platform: Platform
  readonly thumbnail_url: string
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

export interface TimeColumnSpec<T> {
  readonly title: string
  readonly zone: 'business' | 'shanghai'
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
  readonly columns: readonly PostColumnKey[]
  /** 仅队列提供问题字段，历史不传此适配器。 */
  readonly problem?: (row: T) => ProblemSource
  readonly time?: TimeColumnSpec<T>
  readonly actions?: ActionsColumnSpec<T>
}

const { layout } = tokens

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
                // 无图时留空位，避免空 src 显示为加载失败。
                <span className={cx(styles.thumb, styles.thumbEmpty)} aria-hidden="true" />
              ) : (
                <img
                  className={cx(styles.thumb)}
                  src={row.thumbnail_url}
                  alt=""
                  loading="lazy"
                  // 预留图片尺寸，避免加载时行高跳动。
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
          ellipsis: true,
          render: (_value: unknown, row: T) => {
            const content = row.text_de_excerpt === '' ? (
              // 摘要为空才表示无德语译文，不能从 status 推断译文来源。
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
            spec.zone === 'business' ? (
              <BusinessTime at={spec.at(row)} />
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

export function postRowClassName(row: PostRowBase): string {
  return isTerminalStatus(row.status) ? cx(styles.terminal) : ''
}

export const POST_ROW_HEIGHT = layout.tableRowHeight

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
