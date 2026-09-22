/** 仅开发构建检查响应形状；发现差异记录错误，仍返回原数据。 */

type Checker = (value: unknown) => boolean

const isObject = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null && !Array.isArray(value)

export const shape = {
  string: (value: unknown) => typeof value === 'string',
  number: (value: unknown) => typeof value === 'number' && Number.isFinite(value),
  boolean: (value: unknown) => typeof value === 'boolean',
  array: (value: unknown) => Array.isArray(value),
  object: isObject,
  nullable:
    (inner: Checker): Checker =>
    (value) =>
      value === null || inner(value),
  oneOf:
    (...allowed: readonly string[]): Checker =>
    (value) =>
      typeof value === 'string' && allowed.includes(value),
} as const

export type ShapeSpec = Readonly<Record<string, Checker>>

export function checkShape(value: unknown, spec: ShapeSpec): string[] {
  if (!isObject(value)) return ['<不是对象>']
  const problems: string[] = []
  for (const [key, check] of Object.entries(spec)) {
    if (!(key in value)) {
      problems.push(`${key} 缺失`)
      continue
    }
    if (!check(value[key])) problems.push(`${key} 类型不符`)
  }
  return problems
}

export function assertShape<T>(value: T, spec: ShapeSpec, label: string): T {
  if (!import.meta.env.DEV) return value
  const problems = checkShape(value, spec)
  if (problems.length) {
    console.error(`[契约] ${label} 的响应形状和前端类型不符：${problems.join('、')}`)
  }
  return value
}

/** 数组逐项检查，只报告首个问题。 */
export function assertItemShape<T>(items: readonly T[], spec: ShapeSpec, label: string): readonly T[] {
  if (!import.meta.env.DEV) return items
  const first = items[0]
  if (first !== undefined) assertShape(first, spec, `${label}[0]`)
  return items
}


const PLATFORM = shape.oneOf('facebook', 'instagram')
const DISPLAY_STATUS = shape.oneOf(
  'not_ready',
  'pending_review',
  'edited',
  'snoozed',
  'approved',
  'scheduled',
  'skipped',
  'handed_off',
)
const RISK_SCAN_STATUS = shape.oneOf('not_scanned', 'completed', 'failed', 'stale')

export const REVIEW_LIST_ITEM_SHAPE: ShapeSpec = {
  id: shape.string,
  source_text_sha256: shape.string,
  platform: PLATFORM,
  thumbnail_url: shape.string,
  text_de_excerpt: shape.string,
  image_count: shape.number,
  tags: shape.array,
  month: shape.string,
  review: shape.object,
  schedule: shape.nullable(shape.object),
  hard_alerts: shape.array,
  risk_count: shape.number,
  risk_scan_status: RISK_SCAN_STATUS,
  author_flag: shape.nullable(shape.string),
  status: DISPLAY_STATUS,
}

export const REVIEW_LIST_SHAPE: ShapeSpec = {
  tasks: shape.array,
  pagination: shape.object,
  range: shape.object,
  index: shape.object,
  summary: shape.object,
}

export const HISTORY_LIST_ITEM_SHAPE: ShapeSpec = {
  id: shape.string,
  platform: PLATFORM,
  month: shape.string,
  account: shape.string,
  read_only: shape.boolean,
  text_de_excerpt: shape.string,
  tags: shape.array,
  status: DISPLAY_STATUS,
  image_count: shape.number,
  thumbnail_url: shape.string,
}

export const HISTORY_LIST_SHAPE: ShapeSpec = {
  tasks: shape.array,
  index: shape.object,
  scope: shape.oneOf('history'),
  range: shape.object,
  pagination: shape.object,
  summary: shape.object,
}

export const TASK_DETAIL_SHAPE: ShapeSpec = {
  id: shape.string,
  read_only: shape.boolean,
  publication: shape.nullable(shape.object),
  delivery: shape.object,
  platform: PLATFORM,
  status: DISPLAY_STATUS,
  review: shape.object,
  tags: shape.array,
  tags_revision: shape.string,
  localization: shape.object,
  localization_validation: shape.object,
  body_highlights: shape.array,
  body_risks: shape.array,
  risk_scan: shape.object,
  text: shape.object,
  highlights: shape.array,
  risks: shape.array,
  images: shape.array,
  schedule: shape.nullable(shape.object),
  meta: shape.object,
  trail: shape.array,
}

export const LOCALIZATION_DRAFT_SHAPE: ShapeSpec = {
  platform: PLATFORM,
  body_de: shape.string,
  source_body: shape.string,
  source_tags: shape.array,
  protected_tags: shape.array,
  tags: shape.array,
  hashtags_confirmed: shape.boolean,
  links_confirmed: shape.boolean,
  links: shape.array,
  ig_cta: shape.string,
  ig_bio_url: shape.string,
  cta_presets: shape.array,
  revision: shape.nullable(shape.string),
  source_stale: shape.boolean,
  record_stale: shape.boolean,
  has_record: shape.boolean,
  source_text_sha256: shape.string,
}

export const TASK_DETAIL_TEXT_SHAPE: ShapeSpec = {
  en: shape.string,
  de_machine: shape.nullable(shape.string),
  de_human: shape.nullable(shape.string),
  human_revision: shape.nullable(shape.string),
  source_text_sha256: shape.string,
  stale: shape.boolean,
  machine_current: shape.boolean,
  machine_prompt_version: shape.nullable(shape.number),
  current_prompt_version: shape.number,
}

export const IMAGE_ASSET_SHAPE: ShapeSpec = {
  index: shape.number,
  original_url: shape.string,
  de_url: shape.string,
  de_present: shape.boolean,
  metrics: shape.nullable(shape.object),
}

export const APPROVAL_OPTIONS_SHAPE: ShapeSpec = {
  available: shape.boolean,
  reason: shape.string,
  fingerprint: shape.nullable(shape.string),
  lockable: shape.boolean,
  lock_reason: shape.string,
  preview: shape.nullable(shape.object),
  platform: PLATFORM,
  business_timezone: shape.string,
  audience_timezone: shape.string,
  audience_quiet_hours: shape.array,
  default_times: shape.array,
  earliest: shape.string,
  latest: shape.string,
  ui_timezone: shape.string,
}

export const CALENDAR_SHAPE: ShapeSpec = {
  status: shape.string,
  cached_at: shape.nullable(shape.string),
  stale: shape.boolean,
  error: shape.nullable(shape.string),
  cards: shape.array,
  local: shape.array,
  local_error: shape.nullable(shape.string),
  coverage: shape.object,
  bounds: shape.object,
  gap_minutes: shape.number,
  refresh_available: shape.boolean,
  refresh_unavailable_reason: shape.nullable(shape.string),
  advisory_only: shape.boolean,
  month_ui: shape.string,
  ui_timezone: shape.string,
  business_timezone: shape.string,
  audience_timezone: shape.string,
  display_start: shape.string,
  display_end_exclusive: shape.string,
}

/** 本地图层：来源是审校账本与发布账本，未选时刻的条目 at/at_business 为 null。 */
export const CALENDAR_LOCAL_SHAPE: ShapeSpec = {
  kind: shape.string,
  task_id: shape.string,
  platform: PLATFORM,
  review_status: shape.string,
  at: shape.nullable(shape.string),
  at_business: shape.nullable(shape.string),
  snapshot_id: shape.string,
  remote_id: shape.string,
}

export const PUBLISH_OPERATION_SHAPE: ShapeSpec = {
  operation_id: shape.string,
  task_id: shape.string,
  platform: PLATFORM,
  scheduled_at: shape.string,
  status: shape.string,
  step_index: shape.number,
  step_total: shape.number,
  step: shape.string,
  message: shape.string,
  started_at: shape.string,
  updated_at: shape.string,
}

export const CALENDAR_CARD_SHAPE: ShapeSpec = {
  at: shape.string,
  at_business: shape.string,
  channels: shape.array,
  card_sha256: shape.string,
  delivery: shape.string,
  rendered: shape.string,
}

export const SETTINGS_SHAPE: ShapeSpec = {
  version: shape.string,
  editable: shape.object,
  business_timezone: shape.string,
  workday_timezone: shape.string,
  controlled: shape.object,
  controlled_fields: shape.object,
  editable_help: shape.object,
}

export const RUNTIME_SHAPE: ShapeSpec = {
  observed_at: shape.string,
  read_only: shape.boolean,
  activation: shape.nullable(shape.string),
  process: shape.object,
  business: shape.object,
  stages: shape.array,
  heartbeat: shape.object,
}

export const RUNTIME_STAGE_SHAPE: ShapeSpec = {
  number: shape.number,
  name: shape.string,
  status: shape.string,
}

export const INITIAL_CAPABILITIES_SHAPE: ShapeSpec = {
  available: shape.boolean,
  reason: shape.string,
  source_fingerprint: shape.string,
  needs_consent: shape.boolean,
  third_party: shape.boolean,
  job: shape.nullable(shape.object),
}

export const REFINEMENT_CAPABILITIES_SHAPE: ShapeSpec = {
  max_refine_per_media: shape.number,
  image_attempts: shape.object,
  estimated_image_usd: shape.number,
  estimate_basis: shape.string,
  estimate_samples: shape.number,
  jobs: shape.array,
}

export const TEMPLATE_SHAPE: ShapeSpec = {
  kind: shape.oneOf('text', 'image'),
  content: shape.string,
  read_only: shape.boolean,
}
