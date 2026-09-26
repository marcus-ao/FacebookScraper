import { Tag, Tooltip } from 'antd'
import { FileTextOutlined, PictureOutlined, PlayCircleOutlined } from '@ant-design/icons'
import { useState } from 'react'
import { Link } from 'react-router'
import type { TableColumnsType } from 'antd'

import { PlatformLabel } from '@/components/PlatformLabel'
import { ProblemIndicator } from '@/components/ProblemIndicator'
import type { ProblemSource } from '@/components/ProblemIndicator'
import { StatusTag, isTerminalStatus } from '@/components/StatusTag'
import { BusinessTime, ShanghaiTime } from '@/components/Time'
import { tokens } from '@/app/theme'
import { cx } from '@/lib/css'
import type { DisplayStatus, Platform, PostType, PreviewKind } from '@/types/domain'
import type { TaskId } from '@/types/brands'
import styles from './columns.module.css'

/** 队列与历史共用列定义，只读取列表字段；差异由适配器提供。 */

export interface PostRowBase {
  readonly id: TaskId
  readonly platform: Platform
  readonly thumbnail_url: string
  readonly preview_kind: PreviewKind
  readonly post_type?: PostType
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

const PLACEHOLDERS = {
  video: { icon: PlayCircleOutlined, label: '视频帖' },
  text: { icon: FileTextOutlined, label: '纯文字帖' },
  image_pending: { icon: PictureOutlined, label: '图片待补齐' },
} as const

const HISTORY_TYPE_LABEL: Record<PostType, string> = {
  static_image_text: '静态图文', image_only: '纯图片', video: '视频',
  image_video: '图片＋视频', text_only: '纯文字', pending: '类型待核对',
}
type Artwork = 'video' | 'text' | 'mixed' | 'pending' | 'image'

function PreviewArtwork({ kind }: { kind: Artwork }) {
  return <svg viewBox="0 0 40 40" aria-hidden="true" focusable="false">
    {kind === 'video' && <>
      <rect x="5" y="9" width="30" height="22" rx="3" fill="var(--rc-preview-paper)" stroke="currentColor" strokeWidth="1.4" />
      <path d="M5 14h30M9 11h3m4 0h3m4 0h3" stroke="currentColor" strokeWidth="1.3" />
      <circle cx="20" cy="22" r="6" fill="currentColor" /><path d="m18.5 18.8 5 3.2-5 3.2z" fill="var(--rc-preview-paper)" />
    </>}
    {kind === 'text' && <>
      <rect x="8" y="5" width="24" height="30" rx="2.5" fill="var(--rc-preview-paper)" stroke="currentColor" strokeWidth="1.3" />
      <path d="M12 12h16M12 17h16M12 22h12M12 27h15" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" />
    </>}
    {kind === 'image' && <>
      <rect x="5" y="7" width="30" height="26" rx="2.5" fill="var(--rc-preview-paper)" stroke="currentColor" strokeWidth="1.3" />
      <circle cx="13" cy="15" r="3" fill="currentColor" opacity=".65" />
      <path d="m7 29 9-10 6 6 5-5 6 8" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
    </>}
    {kind === 'mixed' && <>
      <rect x="4" y="8" width="32" height="24" rx="2.5" fill="var(--rc-preview-paper)" stroke="currentColor" strokeWidth="1.3" />
      <path d="M20 9v22M6 29l6-8 6 6" fill="none" stroke="currentColor" strokeWidth="1.4" />
      <circle cx="11" cy="16" r="2.3" fill="currentColor" opacity=".65" />
      <circle cx="28" cy="20" r="6" fill="currentColor" /><path d="m26.5 16.8 5 3.2-5 3.2z" fill="var(--rc-preview-paper)" />
    </>}
    {kind === 'pending' && <>
      <rect x="6" y="8" width="28" height="24" rx="3" fill="var(--rc-preview-paper)" stroke="currentColor" strokeWidth="1.3" strokeDasharray="2 2" />
      <circle cx="14" cy="20" r="1.5" fill="currentColor" /><circle cx="20" cy="20" r="1.5" fill="currentColor" /><circle cx="26" cy="20" r="1.5" fill="currentColor" />
    </>}
  </svg>
}

function artworkFor(type: PostType, imageUnavailable: boolean): Artwork {
  if (imageUnavailable) return 'image'
  return ({ static_image_text: 'image', image_only: 'image', video: 'video',
    image_video: 'mixed', text_only: 'text', pending: 'pending' } as const)[type]
}

function Thumbnail({ row }: { row: PostRowBase }) {
  const [failed, setFailed] = useState(false)
  if (row.thumbnail_url && !failed) {
    const image = <img className={cx(styles.thumb)} src={row.thumbnail_url} alt="" loading="lazy"
      width={layout.thumbnailSize} height={layout.thumbnailSize} onError={() => setFailed(true)} />
    return row.post_type === 'image_video'
      ? <span className={styles.realMedia} role="img" aria-label="图片＋视频" title="图片＋视频">
          {image}<span className={styles.playCue} data-play-cue="true" aria-hidden="true" />
        </span>
      : image
  }
  const kind = failed || row.preview_kind === 'image' ? 'image_pending' : row.preview_kind
  if (row.post_type) {
    const artwork = artworkFor(row.post_type, kind === 'image_pending')
    const label = HISTORY_TYPE_LABEL[row.post_type] + (kind === 'image_pending' ? '，图片不可预览' : '')
    return <span className={cx(styles.thumb, styles.artwork, styles[`art${artwork}`])}
      role="img" aria-label={label} title={label} data-artwork={artwork}>
      <PreviewArtwork kind={artwork} />
    </span>
  }
  const { icon: Icon, label } = PLACEHOLDERS[kind]
  return <span className={cx(styles.thumb, styles.placeholder)} role="img" aria-label={label} title={label}>
    <Icon aria-hidden="true" />
  </span>
}

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
              <Thumbnail key={`${row.id}:${row.thumbnail_url}`} row={row} />
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
