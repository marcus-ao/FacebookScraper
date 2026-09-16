import { useEffect, useRef, useState } from 'react'
import { Alert, App, Button, Space, Spin, Tabs, Tooltip, Typography } from 'antd'
import { LeftOutlined, RightOutlined } from '@ant-design/icons'
import { Link, useNavigate, useParams, useSearchParams } from 'react-router'
import { useQuery } from '@tanstack/react-query'
import { PageTitle } from '@/app/PageTitle'
import { buildDetailSearch, buildListSearch, parseHistoryListQuery, parseReviewListQuery } from '@/app/search-params'
import type { ListSource } from '@/app/search-params'
import { useTaskDetail } from '@/hooks/useTaskDetail'
import { historyListOptions, reviewListOptions } from '@/hooks/useTasks'
import { useLocalization } from '@/hooks/useLocalization'
import { filterReviewRows } from '@/features/post-list/model'
import { canEditTask } from '@/features/localization/model'
import { TextWorkspace } from '@/features/localization/TextWorkspace'
import type { TextWorkspaceHandle } from '@/features/localization/TextWorkspace'
import { LocalizationEditor } from '@/features/localization/LocalizationEditor'
import { SuggestionPanel } from '@/features/localization/SuggestionPanel'
import { CategoryEditor } from '@/features/localization/CategoryEditor'
import { ImageWorkspace } from '@/features/images/ImageWorkspace'
import { ApprovalAction, DecisionPanel } from '@/features/approval/DecisionPanel'
import { useApproval } from '@/hooks/useApproval'
import { ContentJobs } from '@/features/content-jobs/ContentJobs'
import { DetailDrawers } from '@/features/diagnostics/DetailDrawers'
import { ReviewActions } from '@/features/review-actions/ReviewActions'
import { ConflictRecovery } from '@/components/ConflictRecovery'
import { CopyButton } from '@/components/CopyButton'
import { PlatformLabel } from '@/components/PlatformLabel'
import { StatusTag } from '@/components/StatusTag'
import { idPath, isConflict } from '@/services/http'
import { AUTHOR_KIND_LABEL, formatDate, formatTrailTime } from '@/lib/format'
import type { ContentJob, MirrorStatus, TaskDetail, TaskStorage } from '@/types/domain'
import styles from './ReviewDetailPage.module.css'

export function ReviewDetailPage({ source }: { source: ListSource }) {
  const { account, postId } = useParams()
  const query = useTaskDetail(`${account}/${postId}`)
  if (query.isPending) return <><PageTitle /><Spin description="正在载入文案…" /></>
  if (!query.data) return <><PageTitle /><Alert type="error" title="暂时无法载入这篇内容" action={<Button onClick={() => void query.refetch()}>重试</Button>} /></>
  return <DetailWorkspace detail={query.data} apply={query.apply} refresh={query.refresh} source={source} />
}

function DetailWorkspace({ detail, apply, refresh, source }: { detail: TaskDetail; apply: (detail: TaskDetail) => void; refresh: () => Promise<TaskDetail>; source: ListSource }) {
  const { modal } = App.useApp()
  const [search, setSearch] = useSearchParams()
  const navigate = useNavigate()
  const review = useQuery({ ...reviewListOptions(), enabled: source === 'review' })
  const historyFilters = parseHistoryListQuery(search)
  const history = useQuery({ ...historyListOptions(historyFilters), enabled: source === 'history' })
  const rows = source === 'review' ? filterReviewRows(review.data?.tasks ?? [], parseReviewListQuery(search)) : history.data?.tasks ?? []
  const index = rows.findIndex(row => row.id === detail.id)
  const total = source === 'review' ? rows.length : history.data?.pagination.total ?? 0
  const position = index < 0 ? '已移出当前筛选' : `${index + 1 + (source === 'history' ? (historyFilters.page - 1) * historyFilters.limit : 0)} / ${total}`
  const loc = useLocalization(detail, apply, refresh)
  const approval = useApproval(detail, loc.editing, refresh)
  const textRef = useRef<TextWorkspaceHandle>(null)
  const [initialContainer, setInitialContainer] = useState<HTMLDivElement | null>(null)
  const [unseen, setUnseen] = useState(Math.max(0, detail.images.length - 1))
  const tab = ['text', 'images', 'localization'].includes(search.get('tab') ?? '') ? search.get('tab')! : 'text'
  const changeTab = (value: string) => setSearch(buildDetailSearch(search, source, { tab: value }), { replace: true })
  // 标签页首次打开才挂载，之后保留结果；切换任务时由 Outlet key 重建。
  const opened = useRef(new Set([tab]))
  opened.current.add(tab)
  const adoptCandidate = (job: ContentJob) => {
    const adopt = () => {
      let body = job.body_de ?? ''
      const base = loc.draft ?? structuredClone(detail.localization)
      if (detail.platform === 'instagram' && base.ig_cta && body.endsWith(base.ig_cta)) body = body.slice(0, -base.ig_cta.length).trimEnd()
      loc.setDraft({ ...base, body_de: body }); changeTab('text')
    }
    if (loc.dirty) modal.confirm({ title: '采用候选将替换编辑区中的正文，继续吗？', okText: '采用候选', cancelText: '保留修改', onOk: adopt })
    else adopt()
  }
  const back = `/${source}?${buildListSearch(search, source)}`
  const adjacent = (delta: number) => { const row = rows[index + delta]; if (index >= 0 && row) void navigate(`/${source}/${idPath(row.id)}?${buildDetailSearch(search, source, { tab })}`) }
  useEffect(() => {
    const key = (event: KeyboardEvent) => {
      const overlay = [...document.querySelectorAll<HTMLElement>('[role="dialog"], .ant-select-dropdown, .ant-dropdown')]
        .some(element => element.getClientRects().length > 0 && getComputedStyle(element).visibility !== 'hidden')
      if (loc.editing || overlay
        || (event.target instanceof Element && event.target.closest('input,textarea,select,[contenteditable="true"],[role="combobox"]')) || event.ctrlKey || event.metaKey || event.altKey) return
      if (['n', 'ArrowDown', 'p', 'ArrowUp'].includes(event.key)) { event.preventDefault(); loc.jump(['n', 'ArrowDown'].includes(event.key) ? 1 : -1) }
      if (event.key === 'Escape') { event.preventDefault(); void navigate(back) }
    }
    window.addEventListener('keydown', key)
    return () => window.removeEventListener('keydown', key)
  }, [loc.editing, loc.shownMarks.length, back, navigate])
  return <article className={styles.page}>
    <header className={styles.bar}>
      <Space size="small"><Link to={back}>返回列表</Link><span className={styles.position}>{position}</span>
        <Tooltip title={loc.editing ? '请先保存或放弃修改' : '上一篇'}><Button aria-label="上一篇" icon={<LeftOutlined />} disabled={loc.editing || index <= 0} onClick={() => adjacent(-1)} /></Tooltip>
        <Tooltip title={loc.editing ? '请先保存或放弃修改' : '下一篇'}><Button aria-label="下一篇" icon={<RightOutlined />} disabled={loc.editing || index < 0 || index >= rows.length - 1} onClick={() => adjacent(1)} /></Tooltip></Space>
      <Space className={styles.center ?? ''} size="small"><PlatformLabel platform={detail.platform} /><StatusTag status={detail.status} />
        {detail.text.stale && <Typography.Text type="danger">原文已变更，请复核</Typography.Text>}
        <span className={styles.markCount}>{loc.shownMarks.filter(mark => mark.type === 'error').length} 错 · {loc.shownMarks.filter(mark => mark.type === 'warn').length} 待确认 · {loc.shownMarks.filter(mark => mark.type === 'risk').length} 注意</span>
        {detail.images.length > 0 && <Tooltip title={unseen ? `还有 ${unseen} 张图片没查看，点击逐张核对；此提示不阻止排期` : '图片都看过了'}><Button type="text" size="small" onClick={() => changeTab('images')}>图片 {detail.images.length - unseen}/{detail.images.length}</Button></Tooltip>}
      </Space>
      <Space size="small" className={styles.actions ?? ''}>{loc.editing ? <><Button disabled={loc.saving || loc.recovering} onClick={loc.discard}>放弃修改</Button><Button aria-label="保存" type="primary" loading={loc.saving} disabled={loc.recovering} onClick={() => void loc.save()}>保存</Button></> : <>
        {canEditTask(detail) && <Button onClick={loc.start}>编辑德语</Button>}
        {!detail.read_only && <ApprovalAction controller={approval} />}
        {!detail.read_only && <ReviewActions detail={detail} onChanged={apply} />}
      </>}</Space>
    </header>
    <div className={styles.body}>
      <div className={styles.title}><PageTitle /><Typography.Text type="secondary">{detail.read_only ? '冻结账号的历史归档 · 仅供查阅' : '对照原文，完成这篇内容的人工审核'}</Typography.Text></div>
      {detail.read_only && <div className={styles.source}><span>作者：{AUTHOR_KIND_LABEL[detail.meta.author_kind]} {detail.meta.owner}</span><span>原帖发布：{formatDate(detail.meta.created_at)}</span>{detail.meta.permalink && <a href={detail.meta.permalink} target="_blank" rel="noopener noreferrer">查看原帖 ↗</a>}</div>}
      {detail.storage && <StorageFacts storage={detail.storage} />}
      <div ref={setInitialContainer} />
      {loc.error ? isConflict(loc.error) ? <ConflictRecovery kind="draft" onRecover={() => void loc.recover()} recovering={loc.recovering} /> : <Alert type="error" title="保存未完成，你的修改仍在编辑区，请重试" /> : null}
      {!detail.text.stale && detail.text.de_machine && !detail.text.machine_current && !detail.text.de_human && <Alert type="warning" title="旧版机器译文，请重新翻译或保存人工复核后的文案" />}
      <Tabs activeKey={tab} onChange={changeTab} items={[{ key: 'text', label: '正文对照' }, { key: 'images', label: `图片 ${detail.images.length}` }, { key: 'localization', label: '话题标签与链接' }]} />
      <div hidden={tab !== 'text'}><TextWorkspace ref={textRef} en={detail.localization.source_body} de={loc.shown.body_de} marks={loc.marks} liveMarks={loc.shownMarks}
        active={loc.active} editing={loc.editing} checking={loc.checking} human={!!detail.text.de_human} scan={detail.risk_scan}
        onChange={body_de => loc.setDraft({ ...loc.shown, body_de })} onSelect={loc.setActive} onJump={loc.jump} />
        <SuggestionPanel detail={detail} body={loc.shown.body_de} editing={loc.editing}
          onAdopt={body_de => loc.setDraft({ ...loc.shown, body_de })} onRefreshed={() => void refresh()} /></div>
      {opened.current.has('localization') && <div hidden={tab !== 'localization'}><LocalizationEditor detail={detail} draft={loc.shown} editing={loc.editing} onChange={loc.setDraft} onInsert={index => {
        changeTab('text'); requestAnimationFrame(() => textRef.current?.insertAtCursor(`{{link${index + 1}}}`))
      }} /></div>}
      {opened.current.has('images') && <div hidden={tab !== 'images'}><ImageWorkspace key={detail.id} images={detail.images} onProgress={setUnseen} /></div>}
      <div className={styles.counter}>
        <span>发布文案 {loc.approximate ? '约 ' : ''}{loc.count}{detail.platform === 'instagram' ? ' / 2,200' : ''} 字符（含话题标签与链接或引导话术）</span>
        <CopyButton text={loc.caption} label="复制发布文案"
          {...(loc.approximate ? { disabledReason: '正在校验，请稍候取准确文案' } : {})} />
      </div>
      {loc.issues.length > 0 && <Alert type="warning" title={loc.issues.map(item => item.message).join('；')} />}
      {loc.warnings.length > 0 && <Typography.Paragraph type="secondary">{loc.warnings.map(item => item.message).join('；')}</Typography.Paragraph>}
      <CategoryEditor detail={detail} disabled={loc.editing} apply={apply} refresh={refresh} />
      {!detail.read_only && <DecisionPanel detail={detail} controller={approval} editing={loc.editing} />}
      {!detail.read_only && <ContentJobs detail={detail} editing={loc.editing} refresh={refresh} onCandidate={adoptCandidate} initialContainer={initialContainer} />}
      <DetailDrawers detail={detail} />
    </div>
  </article>
}

function StorageFacts({ storage }: { storage: TaskStorage }) {
  return <section className={styles.storage} aria-label="归档存储情况">
    <span>归档位置：{storage.account_dir}{storage.folder ? ` / ${storage.folder}` : ''}</span>
    <span>{({ auto: '自动分类', manual: '人工分类', legacy: '历史记录，分类来源未知' } as const)[storage.classified_by]}</span>
    {storage.first_archived_at && <span>首次归档：{formatTrailTime(storage.first_archived_at)} 上海</span>}
    <span>{localStorageCopy(storage)}</span><span>{databaseCopy(storage)}</span><span>{feishuCopy(storage.feishu)}</span>
  </section>
}

function localStorageCopy(storage: TaskStorage): string {
  const count = storage.local.verified_images === undefined
    ? `已保存 ${storage.local.saved_images} 张，历史记录未提供校验证据`
    : `已保存并校验 ${storage.local.verified_images} / ${storage.local.source_media_count ?? '总数待确认'} 张`
  if (storage.local.source_media_complete !== true) return `本地媒体：${count}，原帖媒体列表待核对`
  if (storage.local.status === 'complete') return `本地媒体：${count}，文件完整`
  if (storage.local.status === 'partial') return `本地媒体：${count}，仍有待补齐内容`
  if (storage.local.status === 'corrupt') return `本地媒体：${count}，发现损坏文件，请人工核对`
  return `本地媒体：${count}，状态尚未确认`
}

function databaseCopy(storage: TaskStorage): string {
  if (storage.database.status === 'unbuilt') return '展示索引：尚未建立'
  if (storage.database.status === 'stale') return '展示索引：需要刷新后核对'
  if (storage.database.status === 'verified') return `展示索引：已于 ${formatTrailTime(storage.database.verified_at)} 上海核对`
  return '展示索引：状态尚未确认'
}

function feishuCopy(feishu: MirrorStatus): string {
  let message = '飞书云盘：状态尚未确认'
  if (feishu.status === 'disabled') message = '飞书云盘：未启用'
  else if (feishu.status === 'pending') message = `飞书云盘：有 ${feishu.counts.pending} 项等待同步`
  else if (feishu.status === 'uncertain') message = `飞书云盘：有 ${feishu.counts.uncertain} 项结果待人工核对`
  else if (feishu.status === 'blocked') message = `飞书云盘：有 ${feishu.counts.blocked} 项已暂停，请由维护人员核对`
  else if (feishu.status === 'completed') message = '飞书云盘：本地记录已有完成项，未再次向云端确认'
  else if (feishu.status === 'idle') message = '飞书云盘：尚无待同步内容'

  const details = [
    feishu.incomplete_source ? '源内容待补齐' : '',
    feishu.missing_media?.length ? `缺少 ${feishu.missing_media.length} 个媒体` : '',
  ].filter(Boolean).join('，')
  return details ? `${message}；${details}` : message
}
