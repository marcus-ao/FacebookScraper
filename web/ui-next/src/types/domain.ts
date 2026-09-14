/**
 * 领域类型。**逐字段按 `state/audit-probe/` 里捕获的真实响应手写。**
 *
 * ⛔ 三条纪律（DECISION_LOG.md §5.4）：
 *    1. 不发明后端不存在的字段；
 *    2. 不把详情字段假装成列表字段；
 *    3. 不为了 UI 方便悄悄扩大 response shape。
 *
 * 后端是 FastAPI，但路由返回的是手工拼的 dict（`web/api/reader.py` 的
 * `task_detail()` 就是一个 600 行函数拼出来的字典），**没有 Pydantic 响应模型，
 * 所以没有可用的 OpenAPI schema**。这份类型因此是手写的，依据是实测载荷。
 *
 * 每个接口下面注明了取证来源。凡"未捕获"的都明确标出，不假称已验证。
 */

import type { Revision, Sha256, TaskId } from './brands'

// ─── 枚举 ────────────────────────────────────────────────────────────────────

export type Platform = 'facebook' | 'instagram'

/** core/review.py:16 的七态。 */
export type ReviewStatus =
  | 'pending_review'
  | 'edited'
  | 'snoozed'
  | 'approved'
  | 'scheduled'
  | 'skipped'
  | 'handed_off'

/** reader.py:77 的展示态，**不在** core.review.STATUSES 里。真实数据 24/26 篇是它。 */
export type DisplayStatus = ReviewStatus | 'not_ready'

/** core/review.py:19 的账本动作。 */
export type ReviewAction =
  | 'edited'
  | 'snoozed'
  | 'woke'
  | 'skipped'
  | 'handed_off'
  | 'handoff_link'
  | 'approved'
  | 'scheduled'
  | 'submit_failed'

/**
 * 详情 trail 里出现的动作。
 *
 * ⚠️ 和 ReviewAction 差一个：`reader.py:610` 把账本里的 `edited` 改写成
 * `text_edited` 再送给前端，所以 `text_edited` **只存在于 trail**，账本里没有。
 * 反过来 `edited` 不会出现在 trail 里。
 */
export type TrailAction = Exclude<ReviewAction, 'edited'> | 'text_edited'

/** 前端能发起的审校动作（writer.review_action 的白名单，writer.py:47）。 */
export type ReviewActionRequest = 'snoozed' | 'woke' | 'skipped' | 'handed_off' | 'handoff_link'

export type AuthorKind = 'own' | 'collab' | 'third_party'

/** pipeline/risk_scan.py:19 的 RISK_KINDS。 */
export type RiskKind = 'pun' | 'ambiguous' | 'us_only'

/** pipeline/risk_scan.py 的 current_view 四态。 */
export type RiskScanStatus = 'not_scanned' | 'completed' | 'failed' | 'stale'

/** reader.build_highlights 产出的五类（reader.py:147-180）。 */
export type HighlightKind = 'money' | 'hashtag' | 'money_review' | 'size' | 'imperial'

/** 画在正文里的三档标记。error/warn 来自 highlights.severity，risk 来自 risks。 */
export type MarkType = 'error' | 'warn' | 'risk'
export type MarkSide = 'en' | 'de'

/** core/localization.py:draft_for 给每条链接算出来的来源。 */
export type LinkOrigin = 'mapping' | 'manual' | 'missing'

/** 初翻与单篇优化共用的任务状态。 */
export type JobStatus = 'pending' | 'running' | 'succeeded' | 'failed' | 'interrupted'

export type ContentJobKind = 'text' | 'image'
export type TemplateKind = 'text' | 'image'

/**
 * 队列分桶。**URL 参数叫 `queue`，不叫 `status`**——一个分桶含多个真实 status
 * （DECISION_LOG.md D1 / §2.1）。
 */
export type QueueBucket = 'review' | 'not_ready' | 'snoozed' | 'processed'

/** 码点下标区间。后端给的是 Python 字符下标，不是 UTF-16 码元。 */
export type CodepointSpan = readonly [number, number]

// ─── 前端自己的形状（不是后端契约） ──────────────────────────────────────────

export interface Mark {
  readonly type: MarkType
  readonly kind: HighlightKind | RiskKind
  readonly label: string
  readonly en: CodepointSpan | null
  readonly de: CodepointSpan | null
  readonly index: number
}

export interface TextSegment {
  text: string
  readonly type: MarkType | null
  readonly index: number
}

// ─── 共用子结构 ──────────────────────────────────────────────────────────────

export interface HardAlert {
  readonly code: string
  readonly label: string
}

/**
 * 审校状态记录。
 *
 * ⚠️ **形状随有无账本事件变化**（core/review.py:113 的 `dict(event) if event else {...}`）：
 * 没有事件时只有下面这 7 个必有键；有事件时把整条账本事件铺开，多出第二组。
 * 所以第二组全部可选——这是后端行为，不是我偷懒。
 */
export interface ReviewStateRecord {
  readonly status: DisplayStatus
  readonly revision: Revision | null
  readonly wake_at: string | null
  readonly reason: string
  readonly handoff_url: string
  readonly recorded_at: string | null
  readonly source_stale: boolean
  /** reader._review_state 额外塞进来的（reader.py:491），来自 config。 */
  readonly snooze_default_days: number

  // 以下仅在账本里有事件时出现。
  readonly post_id?: string
  readonly platform?: Platform
  readonly account?: string
  readonly action?: ReviewAction
  readonly previous_revision?: Revision | null
  readonly source_text_sha256?: Sha256
  /** web/DESIGN.md §4：本轮固定 null，界面不得显示虚构用户名。 */
  readonly actor?: null
  readonly snapshot_id?: string
}

export interface ScheduleSlot {
  readonly at: string | null
  readonly channel: Platform
}

export interface Pagination {
  readonly page: number
  /** 审校队列不分页时为 null。 */
  readonly limit: number | null
  /** 筛选后的全部匹配数，不是本页长度。 */
  readonly total: number
}

export interface RangeInfo {
  readonly scope: 'review' | 'history'
  /** 历史为 null，明确表示没有 90 天上限。 */
  readonly days: number | null
  readonly month: string | null
  readonly platform: Platform | null
}

export interface IndexFreshness {
  readonly available: boolean
  readonly stale: boolean
  readonly rebuilt_at: string | null
  readonly error: string | null
}

// ─── GET /api/tasks（取证：state/audit-probe/tasks.json，26 条真实数据） ─────

export interface ReviewListItem {
  readonly id: TaskId
  readonly source_text_sha256: Sha256
  readonly platform: Platform
  readonly thumbnail_url: string
  /** 90 字截断。**空串表示还没有德语译文** —— 这是列表侧唯一可靠的译文依据。 */
  readonly text_de_excerpt: string
  readonly image_count: number
  readonly tags: readonly string[]
  /** 'YYYY-MM' */
  readonly month: string
  readonly review: ReviewStateRecord
  readonly schedule: ScheduleSlot | null
  /**
   * ⚠️ 字段名就是 `hard_alerts`。旧 Vue 里写成 `task.alerts` 导致
   * 「查看并翻译」文案从未渲染（DECISION_LOG.md D13a）。
   * 这个类型里**没有 `alerts` 键**，所以同样的笔误在 tsc 阶段就会失败。
   */
  readonly hard_alerts: readonly HardAlert[]
  readonly risk_count: number
  readonly risk_scan_status: RiskScanStatus
  /** 常态 null；只有第三方作者才给值（reader.py:252-272）。 */
  readonly author_flag: string | null
  readonly status: DisplayStatus
}

export interface ReviewListResponse {
  readonly tasks: readonly ReviewListItem[]
  readonly pagination: Pagination
  readonly range: RangeInfo
  readonly index: IndexFreshness
  readonly summary: {
    readonly total: number
    readonly with_hard_alerts: number
    /** 服务端已经算好，页签计数用它，不要在客户端重算（UI_AUDIT P0-3）。 */
    readonly by_status: Readonly<Record<DisplayStatus, number>>
    readonly tags: readonly string[]
  }
}

// ─── GET /api/tasks?scope=history（取证：history.json，1,067 条真实数据） ────

export interface HistoryListItem {
  readonly id: TaskId
  readonly platform: Platform
  readonly month: string
  readonly created_at?: string
  /** 账号目录名，例如 in_neakasa.tech。 */
  readonly account: string
  readonly read_only: boolean
  readonly text_de_excerpt: string
  readonly tags: readonly string[]
  readonly status: DisplayStatus
  readonly image_count: number
  readonly thumbnail_url: string
}

export interface HistoryListResponse {
  readonly tasks: readonly HistoryListItem[]
  readonly index: IndexFreshness
  readonly scope: 'history'
  readonly range: RangeInfo
  readonly pagination: Pagination
  readonly summary: {
    readonly total: number
    readonly tags: readonly string[]
    /** 倒序，实测 71 项（2020-09 .. 2026-08）。 */
    readonly months: readonly string[]
  }
}

// ─── GET /api/tasks/{id}（取证：detail_fb.json 活账号 + detail_frozen.json 冻结） ─

export interface Highlight {
  readonly kind: HighlightKind
  readonly en_span: CodepointSpan | null
  readonly de_span: CodepointSpan | null
  readonly severity: 'error' | 'warn'
  readonly label: string
}

export interface BodyRisk {
  readonly kind: RiskKind
  readonly en_span: CodepointSpan
  readonly label: string
  readonly quote: string
}

export interface RiskScan {
  readonly status: RiskScanStatus
  readonly risks: readonly BodyRisk[]
  readonly source_text_sha256: Sha256
  /** 和 source_text_sha256 分开，保证高亮偏移不因首尾空白规范而改变。 */
  readonly scan_text_sha256: Sha256
  readonly prompt_sha256: Sha256
  readonly prompt_version: number
  readonly source: { readonly provider?: string; readonly model?: string } | null
  readonly scanned_at: string | null
  readonly message: string
  /** 仅 status === 'stale' 时出现。 */
  readonly previous_status?: RiskScanStatus
  readonly previous_risk_count?: number
}

export interface LocalizationLink {
  readonly source_url: string
  readonly target_url: string
  readonly mapped_url: string
  readonly confirmed: boolean
  readonly origin: LinkOrigin
}

export interface LocalizationDraft {
  readonly platform: Platform
  readonly body_de: string
  readonly source_body: string
  readonly source_tags: readonly string[]
  readonly protected_tags: readonly string[]
  readonly tags: readonly string[]
  readonly hashtags_confirmed: boolean
  readonly links: readonly LocalizationLink[]
  readonly ig_cta: string
  readonly ig_bio_url: string
  readonly cta_presets: readonly string[]
  readonly revision: Revision | null
  readonly source_stale: boolean
  /** 服务端在算，当前前端未消费（BASELINE_BEHAVIOR §11 第 5 条）。 */
  readonly record_stale: boolean
  readonly has_record: boolean
  readonly source_text_sha256: Sha256
}

export interface ValidationIssue {
  readonly code: string
  readonly message: string
}

export interface LocalizationValidation {
  readonly issues: readonly ValidationIssue[]
  readonly warnings: readonly ValidationIssue[]
  readonly ready: boolean
  /** 替换链接占位符之后的最终发布文案长度。 */
  readonly char_count: number
  readonly body_char_count: number
  readonly hashtag_count: number
}

export interface ImageMetrics {
  readonly dhash_distance: number | null
  readonly aspect_drift: number | null
  readonly scale_ratio: number | null
  readonly elapsed_s: number | null
}

export interface ImageAsset {
  readonly index: number
  readonly original_url: string
  readonly de_url: string
  /** false = 缺德语图，当前显示的是原图。**这条不许藏**（DESIGN.md §10.2）。 */
  readonly de_present: boolean
  /** 没有程序生成记录时为 null（人工放的图，或没跑过德语图）。 */
  readonly metrics: ImageMetrics | null
}

export interface OperationRecord {
  /** 上海时区渲染（format.formatTrailTime）。 */
  readonly at: string
  readonly actor: null
  readonly action: TrailAction
  readonly note?: string | null
  readonly wake_at?: string | null
  readonly revision?: Revision
}

export interface DeliveryStatus {
  /** 实测 'unknown'。后端是自由字符串，没有枚举（BASELINE_BEHAVIOR §11 第 9 条）。 */
  readonly status: string
  readonly observed_at: string | null
  readonly message: string
}

export interface TaskDetailMeta {
  readonly platform: Platform
  readonly account: string
  readonly source_text_sha256: Sha256
  readonly source_fingerprint: Sha256 | null
  readonly fingerprint_error: string | null
  readonly snapshot_id: string | null
  readonly permalink: string | null
  readonly created_at: string | null
  readonly owner: string | null
  readonly coauthors: readonly string[]
  readonly author_kind: AuthorKind
  /** 契约外新增字段，理由见 reader.py:675-680：缺德语图这类降级必须让人看见。 */
  readonly compose_warnings: readonly string[]
}

export interface TaskDetailText {
  readonly en: string
  readonly de_machine: string | null
  readonly de_human: string | null
  readonly human_revision: Revision | null
  readonly source_text_sha256: Sha256
  /** 机器译文绑定的原文哈希是否变了。**不许藏**（DESIGN.md §10.2）。 */
  readonly stale: boolean
  /** 机器结果是否满足当前提示词版本。**只在详情里有，列表没有。** */
  readonly machine_current: boolean
  readonly machine_prompt_version: number | null
  readonly current_prompt_version: number
}

export interface TaskDetail {
  readonly id: TaskId
  /** 冻结账号：只能查阅，不渲染任何加工/发布入口。 */
  readonly read_only: boolean
  /** 原样透传的 journal 记录，形状未在任何契约文件里定义（§11 第 3 条）。 */
  readonly publication: Readonly<Record<string, unknown>> | null
  readonly delivery: DeliveryStatus
  readonly platform: Platform
  readonly status: DisplayStatus
  readonly review: ReviewStateRecord
  readonly tags: readonly string[]
  readonly tags_revision: Sha256
  readonly localization: LocalizationDraft
  readonly localization_validation: LocalizationValidation
  /** 正文分区版。**界面用的是这一对。** */
  readonly body_highlights: readonly Highlight[]
  readonly body_risks: readonly BodyRisk[]
  readonly risk_scan: RiskScan
  readonly text: TaskDetailText
  /** 全文版（含标签与链接）。服务端在算，当前前端未消费（§11 第 4 条）。 */
  readonly highlights: readonly Highlight[]
  readonly risks: readonly BodyRisk[]
  readonly images: readonly ImageAsset[]
  readonly schedule: ScheduleSlot | null
  readonly meta: TaskDetailMeta
  readonly trail: readonly OperationRecord[]
}

// ─── GET /api/tasks/{id}/approval-options（取证：approval_options.json） ─────

export interface ApprovalOptions {
  readonly available: boolean
  /** available 为假时的原因。⚠️ 实测里含帖子 ID 与"账本"字样，不能原样给运营看。 */
  readonly reason: string
  /** 批准时必须回传同一个值；available 为假时是 null。 */
  readonly fingerprint: string | null
  readonly platform: Platform
  readonly business_timezone: string
  readonly default_times: readonly string[]
  readonly earliest: string
  readonly latest: string
  readonly ui_timezone: string
}

/** POST /api/tasks/{id}/approve 的回执。**未捕获**（会真实提交排期，绝不测）。 */
export interface ApproveReceipt {
  readonly ok: boolean
  /** 200 不等于成功：必须 ok === true 且 status === 'scheduled'（ApprovalPanel.vue:52）。 */
  readonly status: string
  readonly message?: string
  readonly [key: string]: unknown
}

/** 409 冲突时 error.payload 的形状（ApprovalPanel 读 suggestions 渲染备选时刻）。 */
export interface ApprovalConflictPayload {
  readonly detail: string
  readonly suggestions?: readonly string[]
}

// ─── POST /api/tasks/{id}/check（只算不写，永不拒绝保存） ────────────────────

export interface CheckResult {
  readonly highlights: readonly Highlight[]
  readonly caption_length: number
  readonly hashtag_count: number
  readonly warnings: readonly ValidationIssue[]
  readonly issues: readonly ValidationIssue[]
}

// ─── 内容任务（取证：initial_capabilities.json / refinement_capabilities.json） ─

export interface ContentJob {
  readonly job_id: string
  readonly status: JobStatus
  readonly kind?: ContentJobKind
  readonly media_index?: number | null
  readonly instruction?: string
  readonly recorded_at?: string
  readonly message?: string
  readonly error?: string
  readonly cost_usd?: number
  readonly paid_request_ids?: readonly string[]
  readonly worker_state?: string
  readonly source_text_sha256?: Sha256
  /** 仅 kind==='text' 且 succeeded 时，由 jobs.py:104 拆出正文分区。 */
  readonly body_de?: string
  readonly text_de?: string
}

export interface InitialTranslationCapabilities {
  readonly available: boolean
  readonly reason: string
  readonly source_fingerprint: Sha256
  readonly needs_consent: boolean
  /** 为真时详情页才渲染"这篇来自第三方作者"区块。 */
  readonly third_party: boolean
  readonly job: ContentJob | null
}

export interface RefinementCapabilities {
  /** 每张图最多受理三次（web/DESIGN.md §6）。 */
  readonly max_refine_per_media: number
  /** 键是媒体下标的字符串形式。实测空对象 {}。 */
  readonly image_attempts: Readonly<Record<string, number>>
  readonly estimated_image_usd: number
  readonly estimate_basis: string
  readonly estimate_samples: number
  readonly jobs: readonly ContentJob[]
}

// ─── GET /api/templates/{kind}（取证：template_text.json） ───────────────────

export interface PromptTemplate {
  readonly kind: TemplateKind
  readonly content: string
  readonly read_only: true
}

// ─── GET /api/calendar（取证：calendar.json，真实 4 张卡） ───────────────────

export interface CalendarCard {
  readonly at: string
  /** 服务端已按 business_timezone 换算过，落格用它。 */
  readonly at_business: string
  readonly channels: readonly Platform[]
  readonly card_sha256: string
  /** 前端只判 'published' / 'scheduled'，第三种一律显示"待核验"（§11 第 10 条）。 */
  readonly delivery: string
  readonly rendered: string
  readonly remote_ids?: readonly string[]
}

export interface CalendarBound {
  readonly available: boolean
  readonly error: string | null
  readonly earliest?: string
  readonly latest?: string
  readonly start_inclusive?: string
  readonly end_exclusive?: string
}

export interface CalendarPayload {
  readonly status: string
  readonly cached_at: string | null
  readonly stale: boolean
  readonly error: string | null
  readonly refresh_status: string | null
  readonly age_seconds: number | null
  readonly cards: readonly CalendarCard[]
  readonly coverage: {
    readonly visible_start: string | null
    readonly visible_end: string | null
    readonly matches_current_month: boolean
    readonly channels_complete: boolean
  }
  readonly bounds: Readonly<Record<Platform, CalendarBound>>
  readonly gap_minutes: number
  readonly refresh_available: boolean
  readonly refresh_unavailable_reason: string | null
  readonly advisory_only: boolean
  /** 'YYYY-MM' */
  readonly month_ui: string
  readonly ui_timezone: string
  readonly business_timezone: string
  readonly display_start: string
  readonly display_end_exclusive: string
}

// ─── GET/PUT /api/settings（取证：settings.json） ────────────────────────────

export interface EditableSettings {
  /** 1–12 个不重复 HH:MM，柏林，默认 10:00 / 17:00。 */
  readonly default_times: readonly string[]
  /** 1–30 个上海工作日，默认 3。 */
  readonly snooze_default_days: number
}

export interface ControlledField {
  readonly key: string
  readonly value: unknown
  /**
   * 来自 config.toml 的注释。
   * ⚠️ 实测含源码路径与内部文档章节号，**不能常驻渲染给运营看**
   * （UI_AUDIT P0-12）。数据必须保留（web/DESIGN.md §7 有独立验收项），只改呈现。
   */
  readonly help?: string
}

export interface OperatingSettings {
  /** CAS 版本；PUT 时必须原样回传。 */
  readonly version: string
  readonly editable: EditableSettings
  readonly business_timezone: string
  readonly workday_timezone: string
  /** 七组只读配置：targets / delta / pipeline / network_evidence / publish_identity / price_map / trusted_owners。 */
  readonly controlled: Readonly<Record<string, unknown>>
  readonly controlled_fields: Readonly<Record<string, readonly ControlledField[]>>
  readonly editable_help: Readonly<Partial<Record<keyof EditableSettings, string>>>
}

// ─── GET /api/runtime（取证：runtime.json，真实五阶段） ─────────────────────

export interface RuntimeTimings {
  readonly status: string
  readonly window_days?: number
  readonly basis?: string
  readonly sample_count?: number
  readonly p50_minutes?: number | null
  readonly p95_minutes?: number | null
  readonly morning_sample_count?: number
  readonly morning_deadline_rate?: number | null
  readonly published_to_ready?: {
    readonly status: string
    readonly sample_count?: number
    readonly p50_minutes?: number | null
    readonly p95_minutes?: number | null
  }
}

/**
 * ⚠️ 实测里这个对象**只有三个键**（state_revision / paid_request_ids / cost_usd）。
 * batch_id、status、started_at、finished_at、recovery_reason 只在确实有批次时出现。
 * 恢复接口要 batch_id + state_revision，所以调用前必须先判存在。
 */
export interface RuntimeProcessing {
  readonly state_revision: string | null
  readonly paid_request_ids: readonly string[]
  readonly cost_usd: number
  readonly batch_id?: string
  readonly status?: string
  readonly started_at?: string | null
  readonly finished_at?: string | null
  readonly recovery_reason?: string
}

export interface FeishuDelivery {
  readonly delivery_id: string
  readonly version: string
  readonly status: string
  readonly created_at: string
  readonly error?: string | null
  readonly preview_error?: string | null
  readonly task_ids?: readonly (string | null)[]
}

export interface RuntimeCheck {
  readonly name: string
  readonly available: boolean
  readonly reason?: string
}

/**
 * 五个阶段的字段各不相同，而后端**没有 `kind` 字段**，只能按 `number` 辨识
 * （BASELINE_BEHAVIOR §11 第 12 条）。所以这里用一个宽结构 + 可选键，
 * 消费侧按 number 分支。
 */
export interface RuntimeStage {
  readonly number: 1 | 2 | 3 | 4 | 5
  readonly name: string
  readonly status: string
  /** 阶段 1 */
  readonly detection?: Readonly<Record<string, unknown>>
  /** 阶段 2 */
  readonly mirror_status?: string
  /** 阶段 3 */
  readonly tag_sampling?: { readonly status?: string; readonly message?: string }
  readonly trends_export?: {
    readonly status?: string
    readonly reason?: string
    readonly http_status?: number
  }
  /** 阶段 4 */
  readonly outbox?: {
    readonly enabled: boolean
    readonly credentials_present?: boolean
    readonly counts?: Readonly<Record<string, number>>
    readonly deliveries?: readonly FeishuDelivery[]
  }
  /** 阶段 5 */
  readonly checks?: readonly RuntimeCheck[]
  readonly acceptance?: Readonly<Record<Platform, { readonly verified: boolean }>>
  readonly calendar?: Readonly<Record<string, unknown>>
  /**
   * ⚠️ 只有计数，**没有 task_ids**，所以"N 次提交结果需要核对"现在指不到具体帖子。
   * 加字段是延期项 DEF-1（Stage H 评估），本轮不改 runtime snapshot。
   */
  readonly unconfirmed_attempts?: number
}

export interface RuntimeSnapshot {
  readonly observed_at: string
  readonly read_only: boolean
  readonly activation: string | null
  readonly process: { readonly alive: boolean | null; readonly last_tick_at: string | null }
  readonly business: {
    readonly last_successful_run: string | null
    readonly processing: RuntimeProcessing
    readonly timings?: RuntimeTimings
  }
  readonly stages: readonly RuntimeStage[]
  readonly heartbeat: {
    readonly status: string
    readonly last_success_at: string | null
    readonly age_seconds: number | null
  }
  readonly network: Readonly<Record<string, unknown>>
}
