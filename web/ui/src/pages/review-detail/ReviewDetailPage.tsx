import { useEffect, useRef, useState } from 'react'
import { Alert, App, Button, Space, Spin, Tooltip, Typography } from 'antd'
import { LeftOutlined, RightOutlined } from '@ant-design/icons'
import { Link, useNavigate, useParams, useSearchParams } from 'react-router'
import { useQuery } from '@tanstack/react-query'
import { PageTitle } from '@/app/PageTitle'
import { buildDetailSearch, buildListSearch, parseDetailStep, parseHistoryListQuery, parseReviewListQuery } from '@/app/search-params'
import type { DetailStepId, ListSource } from '@/app/search-params'
import { reviewPath } from '@/app/nav-model'
import { useTaskDetail } from '@/hooks/useTaskDetail'
import { historyListOptions, reviewListOptions } from '@/hooks/useTasks'
import { useLocalization } from '@/hooks/useLocalization'
import { filterReviewRows } from '@/features/post-list/model'
import { bioTargetWarning, canEditTask } from '@/features/localization/model'
import { BodyReviewActions, TextWorkspace } from '@/features/localization/TextWorkspace'
import type { TextWorkspaceHandle } from '@/features/localization/TextWorkspace'
import { LocalizationEditor } from '@/features/localization/LocalizationEditor'
import { SuggestionPanel } from '@/features/localization/SuggestionPanel'
import { CategoryEditor } from '@/features/localization/CategoryEditor'
import { ImageWorkspace } from '@/features/images/ImageWorkspace'
import { ApprovalAction, DecisionPanel } from '@/features/approval/DecisionPanel'
import { useApproval } from '@/hooks/useApproval'
import { approvalBlockNotice } from '@/lib/action-reasons'
import { ContentJobs } from '@/features/content-jobs/ContentJobs'
import { registerReviewDraftActions } from '@/app/review-draft-actions'
import type { ReviewDraftActions } from '@/app/review-draft-actions'
import { ReviewActions } from '@/features/review-actions/ReviewActions'
import { ConflictRecovery } from '@/components/ConflictRecovery'
import { PlatformLabel } from '@/components/PlatformLabel'
import { StatusTag } from '@/components/StatusTag'
import { idPath, isConflict } from '@/services/http'
import { refinementCapabilities } from '@/services/jobs'
import type { ContentJob, TaskDetail } from '@/types/domain'
import { defaultStep, deriveSteps } from './step-model'
import { Arrow, ReviewStepNav, ReviewStepPanel, ReviewTodoSummary, SourceStrip } from './ReviewShellParts'
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
  const listLoaded = source === 'review' ? !!review.data : !!history.data
  const index = rows.findIndex(row => row.id === detail.id)
  const total = source === 'review' ? rows.length : history.data?.pagination.total ?? 0
  const position = !listLoaded ? '正在核对位置' : index < 0 ? '不在当前筛选' : `${index + 1 + (source === 'history' ? (historyFilters.page - 1) * historyFilters.limit : 0)} / ${total}`
  const loc = useLocalization(detail, apply, refresh)
  const draftActions = useRef<ReviewDraftActions>({ save: () => loc.save(), discard: () => loc.discard(), canSave: () => loc.dirty })
  draftActions.current.save = () => loc.save()
  draftActions.current.discard = () => loc.discard()
  draftActions.current.canSave = () => loc.dirty
  useEffect(() => registerReviewDraftActions(detail.id, draftActions.current), [detail.id])
  const approval = useApproval(detail, loc.editing || loc.saving, refresh)
  const textRef = useRef<TextWorkspaceHandle>(null)
  const [initialContainer, setInitialContainer] = useState<HTMLDivElement | null>(null)
  const [todosExpanded, setTodosExpanded] = useState(false)
  const [imageContainer, setImageContainer] = useState<HTMLDivElement | null>(null)
  const [imageIndex, setImageIndex] = useState(0)
  const [imageGenerationRequest, setImageGenerationRequest] = useState<{ index: number; serial: number } | null>(null)
  useEffect(() => { setImageIndex(0); setImageGenerationRequest(null) }, [detail.id])
  const imageVersions = useQuery({
    queryKey: ['refinement-capabilities', detail.id],
    queryFn: () => refinementCapabilities(detail.id),
  })
  const initialStep = useRef<DetailStepId>(defaultStep(detail))
  const tab = parseDetailStep(search) ?? initialStep.current
  const changeTab = (value: DetailStepId) => setSearch(buildDetailSearch(search, source, { tab: value }), { replace: true })
  const opened = useRef(new Set<DetailStepId>([tab]))
  opened.current.add(tab)
  const steps = deriveSteps(detail)
  const todos = steps.flatMap(step => step.todos)
  const finished = ['scheduled', 'approved', 'skipped', 'handed_off', 'snoozed'].includes(detail.status)
  // 前三步都完成、服务端仍不许冻结时（如金额不一致），必须说明原因，不能只留一个不出现的按钮。
  const freezeBlocked = canEditTask(detail) && ['pending_review', 'edited'].includes(detail.status)
    && !loc.editing && !loc.saving && !approval.options.isFetching
    && steps.every(step => step.id === 'final' || step.status === 'complete' || step.status === 'not_required')
    && (approval.options.isError || (!!approval.options.data && !approval.options.data.lockable))

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
  const back = source === 'review'
    ? `${reviewPath(detail.platform)}?${buildListSearch(search, source)}`
    : `/${source}?${buildListSearch(search, source)}`
  useEffect(() => {
    if (source !== 'review' || search.get('platform') === detail.platform) return
    const next = new URLSearchParams(search)
    next.set('platform', detail.platform)
    setSearch(next, { replace: true })
  }, [source, detail.platform, search.get('platform')])
  const adjacent = (delta: number) => {
    const row = rows[index + delta]
    if (listLoaded && index >= 0 && row) void navigate(`/${source}/${idPath(row.id)}?${buildDetailSearch(search, source, { tab })}`)
  }
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
      <h1 className={styles.screenReaderOnly}>单篇审核</h1>
      <Space size="small"><Link to={back}>返回列表</Link><span className={styles.position}>{position}</span>
        <Tooltip title="上一篇"><Button aria-label="上一篇" icon={<LeftOutlined />} disabled={!listLoaded || index <= 0} onClick={() => adjacent(-1)} /></Tooltip>
        <Tooltip title="下一篇"><Button aria-label="下一篇" icon={<RightOutlined />} disabled={!listLoaded || index < 0 || index >= rows.length - 1} onClick={() => adjacent(1)} /></Tooltip></Space>
      <Space className={styles.center ?? ''} size="small"><PlatformLabel platform={detail.platform} /><span className={styles.account}>{detail.meta.account}</span><StatusTag status={detail.status} />
        {detail.text.stale && <Typography.Text type="danger">原文已变更，请复核</Typography.Text>}
        {!['skipped', 'handed_off', 'snoozed'].includes(detail.status) && <span className={styles.position}>步骤 {steps.filter(step => step.status === 'complete' || step.status === 'not_required').length} / 4</span>}
      </Space>
      <Space size="small" className={styles.actions ?? ''}><ReviewActions detail={detail} onChanged={apply} /></Space>
    </header>
    <div className={styles.body}>
      <SourceStrip detail={detail} />
      <CategoryEditor detail={detail} disabled={loc.editing || loc.saving} apply={apply} refresh={refresh} />
      {listLoaded && index < 0 && <p className={styles.filterNotice}>这篇不在当前筛选结果中。<Link to={back}>返回原筛选列表</Link></p>}
      {loc.error ? isConflict(loc.error) ? <ConflictRecovery kind="draft" onRecover={() => void loc.recover()} recovering={loc.recovering} /> : <Alert type="error" title={loc.editing ? '保存未完成，你的修改仍在编辑区，请重试' : '确认未保存，请重试'} /> : null}
      {!detail.text.stale && detail.text.de_machine && !detail.text.machine_current && !detail.text.de_human && <Alert type="warning" title="旧版机器译文，请重新翻译或保存人工复核后的文案" />}
      {tab !== 'final' && !finished && <ReviewTodoSummary todos={todos} expanded={todosExpanded} onExpand={() => setTodosExpanded(value => !value)} onNavigate={changeTab} />}
      <ReviewStepNav steps={steps} active={tab} onChange={changeTab} />
      <ReviewStepPanel id="text" active={tab} opened>
        <div ref={setInitialContainer} />
        <TextWorkspace ref={textRef} en={detail.localization.source_body} de={loc.shown.body_de} marks={loc.marks} liveMarks={loc.shownMarks}
          active={loc.active} editing={loc.editing} checking={loc.checking} human={!!detail.text.de_human} scan={detail.risk_scan}
          onChange={body_de => loc.setDraft({ ...loc.shown, body_de })} onSelect={loc.setActive} onJump={loc.jump}
          {...(canEditTask(detail) ? { onStartEdit: loc.start, onGenerateDraft: () => initialContainer?.scrollIntoView({ block: 'center' }) } : {})} />
        <SuggestionPanel key={detail.id} detail={detail} body={loc.shown.body_de} editing={loc.editing}
          onAdopt={body_de => loc.setDraft({ ...loc.shown, body_de })} onRefreshed={() => void refresh()} />
        {!detail.read_only && <ContentJobs key={detail.id} detail={detail} editing={loc.editing || loc.saving} refresh={refresh} onCandidate={adoptCandidate}
          initialContainer={initialContainer} imageContainer={imageContainer} imageIndex={imageIndex} imageGenerationRequest={imageGenerationRequest} />}
        <BodyReviewActions canEdit={canEditTask(detail)} hasBody={!!detail.localization.body_de.trim()}
          editing={loc.editing} saving={loc.saving || loc.recovering} confirming={loc.bodyConfirming}
          confirmed={detail.content_review.body.confirmed} onEdit={loc.start} onSave={() => void loc.save()}
          onDiscard={loc.discard} onConfirm={confirmed => void loc.confirmCurrentBody(confirmed)} />
      </ReviewStepPanel>
      <ReviewStepPanel id="images" active={tab} opened={opened.current.has('images')}>
        <ImageWorkspace key={detail.id} images={detail.images} detail={detail}
          versions={imageVersions.data?.image_versions ?? {}} editing={loc.editing || loc.saving}
          onChanged={async () => { await refresh(); await imageVersions.refetch() }}
          onCurrentChange={setImageIndex}
          onGenerate={index => { setImageGenerationRequest(old => ({ index, serial: (old?.serial ?? 0) + 1 })) }} />
        <div ref={setImageContainer} />
      </ReviewStepPanel>
      <ReviewStepPanel id="localization" active={tab} opened={opened.current.has('localization')}>
        <LocalizationEditor detail={detail} draft={loc.shown} editing={loc.editing} saving={loc.saving || loc.recovering}
          onChange={loc.setDraft} onComplete={next => void loc.completeLocalization(next)} onSave={() => void loc.save()} onDiscard={loc.discard} onInsert={index => {
            changeTab('text'); requestAnimationFrame(() => textRef.current?.insertAtCursor(`{{link${index + 1}}}`))
          }} />
        {!loc.editing && canEditTask(detail) && <div className={styles.stepActions}><Button onClick={loc.start}>编辑标签与链接</Button></div>}
      </ReviewStepPanel>
      <ReviewStepPanel id="final" active={tab} opened={opened.current.has('final')}>
        {bioTargetWarning(loc.shown) && <Alert type="warning" showIcon title={bioTargetWarning(loc.shown)} />}
        {!finished && <section className={styles.finalSummary} aria-label="发布前审核情况"><h2>发布前审核情况</h2>
          <ul>{steps.filter(step => step.id !== 'final').map(step => <li key={step.id}>
            <span>{step.label}</span><span>{step.status === 'complete' ? '已完成' : step.status === 'not_required' ? '无需审核' : step.todos[0]?.text ?? '待核对'}</span>
            <button type="button" aria-label={`${step.label}：${step.status === 'pending' ? '去处理' : '查看'}`}
              onClick={() => changeTab(step.id)}>{step.status === 'pending' ? '去处理' : '查看'}<Arrow /></button>
          </li>)}</ul>
          {approval.locked && <p>内容已冻结，请在下方选择发布时间。</p>}
        </section>}
        <DecisionPanel detail={detail} controller={approval} editing={loc.editing || loc.saving} />
        {freezeBlocked && <Alert type="warning" showIcon title="暂时不能冻结内容"
          description={approval.options.isError ? '发布条件读取失败，请刷新后重试。' : approvalBlockNotice(approval.options.data?.lock_reason)}
          action={<Button size="small" onClick={() => void approval.options.refetch()}>重新核对</Button>} />}
        {!detail.read_only && !loc.editing && !loc.saving && (approval.lockable || approval.locked) && <div className={styles.stepActions}><ApprovalAction controller={approval} /></div>}
      </ReviewStepPanel>
      {loc.issues.length > 0 && <Alert type="warning" title={loc.issues.map(item => item.message).join('；')} />}
      {loc.warnings.length > 0 && <Typography.Paragraph type="secondary">{loc.warnings.map(item => item.message).join('；')}</Typography.Paragraph>}
    </div>
  </article>
}
