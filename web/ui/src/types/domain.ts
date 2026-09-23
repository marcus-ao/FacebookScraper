/** 接口类型对应 web/api 的实际响应；列表与详情分别建模。 */

import type { Revision, Sha256, TaskId } from './brands'


export type Platform = 'facebook' | 'instagram'

export type ReviewStatus =
  | 'pending_review'
  | 'edited'
  | 'content_locked'
  | 'snoozed'
  | 'approved'
  | 'scheduled'
  | 'skipped'
  | 'handed_off'

/** not_ready 是派生展示状态，不写入审校账本。 */
export type DisplayStatus = ReviewStatus | 'not_ready'

export type ReviewAction =
  | 'edited'
  | 'image_selected'
  | 'content_locked'
  | 'unlocked'
  | 'snoozed'
  | 'woke'
  | 'skipped'
  | 'handed_off'
  | 'handoff_link'
  | 'approved'
  | 'scheduled'
  | 'submit_failed'
  | 'unscheduled'

/** trail 将账本动作 edited 映射为 text_edited。 */
export type TrailAction = Exclude<ReviewAction, 'edited'> | 'text_edited'

export type ReviewActionRequest = 'snoozed' | 'woke' | 'skipped' | 'handed_off' | 'handoff_link'

export type AuthorKind = 'own' | 'collab' | 'third_party'

export type RiskKind = 'pun' | 'ambiguous' | 'us_only'

export type RiskScanStatus = 'not_scanned' | 'completed' | 'failed' | 'stale'

export type HighlightKind = 'money' | 'hashtag' | 'money_review' | 'size' | 'imperial'

/** 画在正文里的三档标记。error/warn 来自 highlights.severity，risk 来自 risks。 */
export type MarkType = 'error' | 'warn' | 'risk'
export type MarkSide = 'en' | 'de'

export type LinkOrigin = 'mapping' | 'manual' | 'missing'

export type JobStatus = 'pending' | 'running' | 'succeeded' | 'failed' | 'interrupted'

export type ContentJobKind = 'text' | 'image' | 'suggest'
export type TemplateKind = 'text' | 'image'

export type QueueBucket = 'review' | 'not_ready' | 'snoozed' | 'processed'

/** 码点下标区间。后端给的是 Python 字符下标，不是 UTF-16 码元。 */
export type CodepointSpan = readonly [number, number]


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


export interface HardAlert {
  readonly code: string
  readonly label: string
}

/** 账本有事件时会附加事件字段，因此第二组字段可选。 */
export interface ReviewStateRecord {
  readonly status: DisplayStatus
  readonly revision: Revision | null
  readonly wake_at: string | null
  readonly reason: string
  readonly handoff_url: string
  readonly recorded_at: string | null
  readonly source_stale: boolean
  readonly snooze_default_days: number

  // 以下仅在账本里有事件时出现。
  readonly post_id?: string
  readonly platform?: Platform
  readonly account?: string
  readonly action?: ReviewAction
  readonly previous_revision?: Revision | null
  readonly source_text_sha256?: Sha256
  /** actor 固定为 null，不构造操作人身份。 */
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
  /** 历史为 null，表示无时间上限。 */
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


export interface ReviewListItem {
  readonly id: TaskId
  readonly source_text_sha256: Sha256
  readonly platform: Platform
  readonly thumbnail_url: string
  /** 90 字摘要；空串表示尚无德语译文。 */
  readonly text_de_excerpt: string
  readonly image_count: number
  readonly tags: readonly string[]
  /** 'YYYY-MM' */
  readonly month: string
  readonly review: ReviewStateRecord
  readonly schedule: ScheduleSlot | null
  readonly hard_alerts: readonly HardAlert[]
  readonly risk_count: number
  readonly risk_scan_status: RiskScanStatus
  /** 常态 null，仅第三方作者有值。 */
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
    /** 页签计数由服务端提供，不能以当前页重算。 */
    readonly by_status: Readonly<Record<DisplayStatus, number>>
    /** 两个平台各自的页签计数；旧后端未提供时角标退回全量口径。 */
    readonly by_platform_status?: Readonly<Record<Platform, Readonly<Record<DisplayStatus, number>>>>
    readonly by_platform_hard_alerts?: Readonly<Record<Platform, number>>
    readonly tags: readonly string[]
  }
}


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
    readonly months: readonly string[]
  }
}


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
  readonly links_confirmed: boolean
  readonly links: readonly LocalizationLink[]
  readonly ig_cta: string
  readonly ig_bio_url: string
  readonly cta_presets: readonly string[]
  readonly revision: Revision | null
  readonly source_stale: boolean
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
  /** 服务端拼好的成品文案：正文 + 链接或引导话术 + 话题标签。复制用它，不在前端重拼。 */
  readonly caption?: string
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
  /** 改动像素占比。0 表示模型一个像素都没动，须人眼判断是无需改动还是白花钱。 */
  readonly changed_pixel_ratio?: number | null
}

export interface ImageVersion {
  readonly preview_url?: string | null
  readonly out_path: string
  readonly created_at: string | null
  readonly refine_id: string | null
  readonly refine_instruction: string | null
  readonly model: string | null
  readonly available: boolean
  readonly current: boolean
  readonly usable: boolean
  readonly unusable_reasons: readonly string[]
  readonly metrics: ImageMetrics
}

export interface ImageAsset {
  readonly index: number
  readonly original_url: string
  readonly de_url: string
  /** false 表示缺德语图，当前为原图回退。 */
  readonly de_present: boolean
  readonly selection: 'original' | 'original_confirmed' | 'manual' | 'generated' | 'conflict'
  readonly source_image_sha256: string
  readonly ready: boolean
  /** 当前用的是人工放置或上传的图；模型优化不会被采用，入口须禁用。 */
  readonly manual?: boolean
  readonly replaced_at?: string | null
  readonly warnings?: readonly string[]
  /** 没有程序生成记录时为 null（人工放的图，或没跑过德语图）。 */
  readonly metrics: ImageMetrics | null
}

export interface OperationRecord {
  /** 按上海时区显示。 */
  readonly at: string
  readonly actor: null
  readonly action: TrailAction
  readonly note?: string | null
  readonly wake_at?: string | null
  readonly revision?: Revision
}

export interface DeliveryStatus {
  /** 后端返回自由字符串，包括 `unknown`，没有枚举。 */
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
  readonly compose_warnings: readonly string[]
}

export interface TaskDetailText {
  readonly en: string
  readonly de_machine: string | null
  readonly de_human: string | null
  readonly human_revision: Revision | null
  readonly source_text_sha256: Sha256
  /** 机器译文绑定的原文哈希是否变化。 */
  readonly stale: boolean
  /** 机器结果是否符合当前提示词版本，仅详情提供。 */
  readonly machine_current: boolean
  readonly machine_prompt_version: number | null
  readonly current_prompt_version: number
}

export interface StorageMedia {
  readonly ordinal: number
  readonly kind: string
  readonly url: string | null
  readonly source_url: string | null
  readonly local_path: string | null
  readonly content_type: string | null
  readonly width: number | null
  readonly height: number | null
  readonly byte_size: number | null
  readonly sha256: Sha256 | null
  readonly storage_status: string
}

/** 一篇来源内容在本机、展示索引和飞书云盘中的独立只读事实。 */
export interface TaskStorage {
  readonly classified_by: 'auto' | 'manual' | 'legacy'
  readonly account_dir: string
  readonly folder: string | null
  readonly first_archived_at: string | null
  readonly local: { readonly status: string; readonly saved_images: number; readonly expected_images: number
    readonly source_media_complete?: boolean | null; readonly media_complete?: boolean | null
    readonly source_media_count?: number | null; readonly verified_images?: number }
  readonly database: { readonly status: string; readonly verified_at?: string | null; readonly message?: string | null }
  readonly feishu: MirrorStatus
  readonly media: readonly StorageMedia[]
}

export interface TaskDetail {
  readonly publish_operation?: PublishOperation | null
  readonly id: TaskId
  /** 冻结账号：只能查阅，不渲染任何加工/发布入口。 */
  readonly read_only: boolean
  /** 原样透传的发布账本记录。 */
  readonly publication: Readonly<Record<string, unknown>> | null
  readonly delivery: DeliveryStatus
  readonly platform: Platform
  readonly status: DisplayStatus
  readonly review: ReviewStateRecord
  readonly tags: readonly string[]
  readonly tags_revision: Sha256
  readonly hard_alerts?: readonly HardAlert[]
  readonly localization: LocalizationDraft
  readonly localization_validation: LocalizationValidation
  /** 本轮标签热度推荐是否启用；关闭时标签只由人工选取。旧详情未提供时按启用处理。 */
  readonly hashtag_suggestions_enabled?: boolean
  /** 最近一次的只读优化建议；从未生成过时为 null。 */
  readonly text_suggestions?: TextSuggestions | null
  /** 正文分区版，用于编辑对照。 */
  readonly body_highlights: readonly Highlight[]
  readonly body_risks: readonly BodyRisk[]
  readonly risk_scan: RiskScan
  readonly text: TaskDetailText
  /** 全文版，包含标签与链接。 */
  readonly highlights: readonly Highlight[]
  readonly risks: readonly BodyRisk[]
  readonly images: readonly ImageAsset[]
  readonly schedule: ScheduleSlot | null
  /** 新版服务提供；旧版详情未提供时页面仍可正常查看。 */
  readonly storage?: TaskStorage
  readonly meta: TaskDetailMeta
  readonly trail: readonly OperationRecord[]
}


export interface FrozenPreview {
  readonly snapshot_id: string
  readonly text: string
  readonly images: readonly { readonly index: number; readonly url: string }[]
  readonly target: {
    readonly channel: Platform
    readonly account: string
    readonly asset_id: string
    readonly business_id: string
  }
}

export interface ApprovalOptions {
  readonly available: boolean
  /** 当前阶段的具体原因。 */
  readonly reason: string
  /** 批准时必须回传同一个值；available 为假时是 null。 */
  readonly fingerprint: string | null
  /** 内容本身能否冻结。选时刻还要求能算出时间范围，两者都不看历史录证。 */
  readonly lockable: boolean
  readonly lock_reason: string
  /** 已冻结时是将要提交的正文和图片；未冻结或快照损坏时为 null。 */
  readonly preview: FrozenPreview | null
  readonly platform: Platform
  readonly business_timezone: string
  readonly audience_timezone: string
  /** [起, 止) 小时；落在区间内提示德国受众正在睡觉，但不阻断。 */
  readonly audience_quiet_hours: readonly [number, number]
  readonly default_times: readonly string[]
  readonly earliest: string
  readonly latest: string
  readonly ui_timezone: string
}

export interface ApproveReceipt {
  readonly ok: boolean
  /** 200 不等于成功：必须 ok === true 且 status === 'scheduled'。 */
  readonly status: string
  readonly message?: string
  readonly [key: string]: unknown
}

export interface ApprovalConflictPayload {
  readonly detail: string
  readonly suggestions?: readonly string[]
}


export interface CheckResult {
  readonly highlights: readonly Highlight[]
  /** 与 caption_length 出自同一次计算的成品文案；只有分区草稿路径会返回。 */
  readonly caption?: string
  readonly caption_length: number
  readonly hashtag_count: number
  readonly warnings: readonly ValidationIssue[]
  readonly issues: readonly ValidationIssue[]
}


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
  /** 文案候选的模板版本；旧任务缺少版本时不可采用。 */
  readonly prompt_version?: number | null
  readonly current_prompt_version?: number
  readonly prompt_current?: boolean
  /** 仅 text 任务成功时提供正文分区。 */
  readonly body_de?: string
  readonly text_de?: string
  /** 仅 suggest 任务成功时提供。 */
  readonly suggestions?: readonly TextSuggestion[]
  readonly dropped?: readonly string[]
}

export type SuggestionKind = 'grammar' | 'wording' | 'register' | 'terminology' | 'fluency'
export interface TextSuggestion {
  /** 当前德语正文里的原样片段，保证只出现一次，所以「采用」不会改错地方。 */
  readonly quote: string
  readonly replacement: string
  readonly kind: SuggestionKind
  /** 中文写的理由，读的人是中国运营。 */
  readonly why: string
}
export interface TextSuggestions {
  readonly job_id: string
  /** 点击生成时的正文快照，供尚未保存的编辑区逐字符核对。旧记录可能没有。 */
  readonly body_de: string | null
  readonly source_text_sha256: Sha256
  readonly text_de_sha256: Sha256
  readonly items: readonly TextSuggestion[]
  /** 越界被丢弃的条数说明，不静默吞掉。 */
  readonly dropped: readonly string[]
  /** 相对已保存正文是否有效；编辑区另与本次正文快照核对。 */
  readonly current: boolean
  readonly generated_at: string
  readonly prompt_version: number
  readonly current_prompt_version: number
}

export interface InitialTranslationCapabilities {
  readonly available: boolean
  readonly reason: string
  readonly source_fingerprint: Sha256
  readonly needs_consent: boolean
  readonly third_party: boolean
  readonly job: ContentJob | null
}

export interface RefinementCapabilities {
  readonly image_model?: string
  readonly max_image_count?: number | null
  readonly max_refine_per_media: number
  /** 键为媒体下标的字符串形式。 */
  readonly image_attempts: Readonly<Record<string, number>>
  readonly estimated_image_usd: number | null
  readonly estimate_basis: string
  readonly estimate_samples: number
  readonly jobs: readonly ContentJob[]
  /** 键为媒体下标的字符串形式；每张图生成过的历史版本，按落盘顺序。 */
  readonly image_versions?: Readonly<Record<string, readonly ImageVersion[]>>
}


export interface PromptTemplate {
  readonly kind: TemplateKind
  readonly content: string
  readonly read_only: true
}


export interface CalendarCard {
  readonly at: string
  /** 服务端已按 business_timezone 换算过，落格用它。 */
  readonly at_business: string
  readonly channels: readonly Platform[]
  readonly card_sha256: string
  /** 除 published、scheduled 外均显示待核验；read_status 非 complete 时它只是格子上的推断。 */
  readonly delivery: string
  readonly rendered: string
  readonly remote_ids?: Readonly<Partial<Record<Platform, string>>>
  readonly placement?: 'feed' | 'story' | 'reel' | 'live' | 'ad' | 'task' | 'unknown'
  readonly media_kind?: 'text' | 'link' | 'image' | 'carousel' | 'mixed' | 'video' | 'unknown'
  readonly caption_status?: 'present' | 'empty' | 'unknown'
  readonly accounts?: Readonly<Partial<Record<Platform, string>>>
  readonly relationships?: readonly string[]
  readonly read_status?: 'complete' | 'unsupported' | 'unavailable' | 'incomplete' | 'legacy'
  /** 为假时不能用这张卡的时刻证明它落在目标范围外。旧缓存缺这个字段时按未核实。 */
  readonly time_verified?: boolean
  readonly diagnostic_index?: number | null
  readonly audience?: AudienceMoment | null
}

/** 业务时刻在德国受众那边是几点。 */
export interface AudienceMoment {
  readonly timezone: string
  readonly at: string
  readonly quiet_hours: boolean
}

/** 月历的本地图层：系统自己知道的排期，来源是审校账本与发布账本。 */
export interface CalendarLocalEntry {
  readonly kind: 'content_locked' | 'submitting' | 'scheduled'
  readonly task_id: TaskId
  readonly platform: Platform
  readonly review_status: ReviewStatus
  readonly at: string | null
  readonly at_business: string | null
  readonly snapshot_id: string
  readonly remote_id: string
  readonly audience?: AudienceMoment | null
}

/** 一次浏览器提交的可观察记录。 */
export interface PublishOperation {
  readonly operation_id: string
  readonly task_id: TaskId
  readonly platform: Platform
  readonly scheduled_at: string
  readonly status: 'running' | 'succeeded' | 'failed' | 'uncertain'
  readonly step_index: number
  readonly step_total: number
  readonly step: string
  readonly message: string
  readonly result: { readonly suggestions?: readonly string[] } | null
  readonly started_at: string
  readonly updated_at: string
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
  readonly refresh_diagnostic?: Readonly<Record<string, unknown>> | null
  /** 本地图层只作展示；占用判定永远只看远端读到的 cards。 */
  readonly local: readonly CalendarLocalEntry[]
  readonly local_error: string | null
  readonly coverage: CalendarCoverage
  readonly bounds: Readonly<Record<Platform, CalendarBound>>
  readonly gap_minutes: number
  readonly refresh_available: boolean
  readonly refresh_unavailable_reason: string | null
  readonly advisory_only: boolean
  /** 'YYYY-MM' */
  readonly month_ui: string
  readonly ui_timezone: string
  readonly business_timezone: string
  readonly audience_timezone: string
  readonly display_start: string
  readonly display_end_exclusive: string
}

export interface CalendarCoverage {
  readonly visible_start: string | null
  readonly visible_end: string | null
  readonly matches_current_month: boolean
  readonly channels_complete: boolean
  readonly grid_complete?: boolean
  readonly entries_complete?: boolean
  readonly classification_complete?: boolean
  readonly decision_complete?: boolean
  /** 网格与卡片时刻对齐。不表示渠道、正文或每个聚合变体都已读取。 */
  readonly occupancy_complete?: boolean
  readonly unresolved_count?: number
}


export interface EditableSettings {
  /** 1–12 个不重复的业务时区 HH:MM；业务时区当前是北京。 */
  readonly default_times: readonly string[]
  /** 1–30 个上海工作日。 */
  readonly snooze_default_days: number
}

export interface ControlledField {
  readonly key: string
  readonly value: unknown
  /** 来自配置注释，仅在按需帮助中显示。 */
  readonly help?: string
}

export interface OperatingSettings {
  /** CAS 版本；PUT 时必须原样回传。 */
  readonly version: string
  readonly editable: EditableSettings
  readonly business_timezone: string
  readonly workday_timezone: string
  readonly controlled: Readonly<Record<string, unknown>>
  readonly controlled_fields: Readonly<Record<string, readonly ControlledField[]>>
  readonly editable_help: Readonly<Partial<Record<keyof EditableSettings, string>>>
}


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

/** 无批次时不含 batch_id 等字段，恢复前须检查其存在。 */
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
  readonly capture_keys?: readonly string[]
}

export interface RuntimeCheck {
  readonly name: string
  readonly available: boolean
  readonly reason?: string
}

/** 云盘镜像是本地队列的只读汇总，不代表已向云端再次查询。 */
export interface MirrorStatus {
  readonly enabled: boolean
  readonly status: string
  readonly counts: { readonly pending: number; readonly completed: number; readonly uncertain: number; readonly blocked: number }
  readonly last_success_at: string | null
  readonly last_error?: string | null
  readonly incomplete_source?: boolean
  readonly missing_media?: readonly number[]
  readonly operations: readonly { readonly id: string; readonly kind?: string; readonly status?: string; readonly sha256?: string | null; readonly remote_token?: string | null; readonly error?: unknown }[]
}

/** 各阶段无 kind 字段，以 number 辨识可选字段。 */
export interface RuntimeStage {
  readonly number: 1 | 2 | 3 | 4 | 5
  readonly name: string
  readonly status: string
  readonly detection?: Readonly<Record<string, unknown>>
  readonly mirror_status?: string
  readonly mirror?: MirrorStatus
  readonly tag_sampling?: { readonly status?: string; readonly message?: string }
  readonly trends_export?: {
    readonly status?: string
    readonly reason?: string
    readonly http_status?: number
  }
  readonly outbox?: {
    readonly enabled: boolean
    readonly credentials_present?: boolean
    /** 不同阶段复用了同一个机器人地址。 */
    readonly duplicate_bot_targets?: boolean
    readonly bot_configuration_valid?: boolean
    readonly bots?: ReadonlyArray<{ readonly role: string; readonly name: string; readonly configured: boolean; readonly valid: boolean }>
    readonly counts?: Readonly<Record<string, number>>
    readonly deliveries?: readonly FeishuDelivery[]
  }
  readonly checks?: readonly RuntimeCheck[]
  readonly acceptance?: Readonly<Record<Platform, { readonly verified: boolean }>>
  readonly calendar?: Readonly<Record<string, unknown>>
  /** 快照仅提供数量，无 task_ids，不能推断具体任务。 */
  readonly unconfirmed_attempts?: number
}

export interface RuntimeSnapshot {
  readonly observed_at: string
  readonly read_only: boolean
  readonly monitoring?: MonitorStatus
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
}

export interface CaptureItem {
  readonly key: string
  readonly scan_id: string
  readonly status: 'pending' | 'complete' | 'manual' | 'deferred'
  readonly post_id: string
  readonly platform: Platform
  readonly account_dir: string
  readonly classification: string
  readonly reason: string | null
  readonly archived: boolean
  readonly saved_images: number | null
  readonly source_media_count: number | null
  readonly source_media_complete: boolean | null
  readonly media_complete: boolean | null
  readonly first_seen_at: string
  readonly finished_at: string | null
  readonly permalink: string | null
  readonly discovery_wait_seconds?: number | null
  readonly capture_seconds?: number | null
}

export interface MonitorStatus {
  readonly status: string
  readonly revision: number | null
  readonly capture_revision: number | null
  readonly reason: string | null
  readonly baselines: Readonly<Record<string, { readonly enabled_at: string; readonly recent_count: number; readonly lookback_days: number }>>
  readonly platforms: Readonly<Record<string, { readonly paused: boolean; readonly failures: number; readonly reason: string | null
    readonly next_due_at: string; readonly homepage_used: number; readonly homepage_limit: number
    readonly detail_used: number; readonly detail_limit: number }>>
  readonly items: readonly CaptureItem[]
}
