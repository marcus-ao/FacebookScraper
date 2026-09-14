# Vue → React + TypeScript 迁移方案

**成文日期：2026-09-13。** 前置：[BASELINE_BEHAVIOR.md](BASELINE_BEHAVIOR.md)（要保住的行为）、[UI_ARCHITECTURE_PROPOSAL.md](UI_ARCHITECTURE_PROPOSAL.md)（要做成的形状）。

**执行状态：Stage A–J 已完成，生产 cutover 未执行。** 下文保留原迁移设计；当前执行清单与实际文件映射见 [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md)，最终结果、B1–B24、切换及回滚步骤见 [FINAL_IMPLEMENTATION_REPORT.md](FINAL_IMPLEMENTATION_REPORT.md)。原有阶段批准停点已由 2026-09-13 的连续实施授权替代。

---

## 1. 这一步谁在做什么

迁移期间业务不能停：上海运营每天 08:00–19:00 在岗，要审的帖子照旧来。所以这套方案的第一条约束不是技术选型，而是**任何一天的下班时刻，生产机上那份 `dist/` 都必须是能用的**。

现在的部署形状帮了大忙：`npm run build` 产出 `web/ui/dist/`，手工拷到生产机，FastAPI 用 `StaticFiles` 挂在 `/`，生产机不需要 Node（`web/api/app.py:41,256-259`）。这意味着**新旧两份 `dist/` 是可以互换的两个文件夹**——回滚就是换回上一份，不涉及后端、不涉及数据、不需要重启之外的任何操作。整套方案就架在这一点上。

---

## 2. 总体路线：整体替换，不做双栈共存

### 2.1 为什么不渐进共存

先说否掉的方案。"Vue 与 React 并存、一个界面一个界面换"（`veaury` 之类的互操作层，或两个入口 HTML）在这里不合适：

| 理由 | 依据 |
|---|---|
| 前端规模太小，共存的成本高于重写 | `web/ui/src/` 一共 3,169 行，其中约 900 行是 `<style scoped>`。纯逻辑不到 1,800 行 |
| 共享的东西全是纯函数，不是组件 | `marks.js`（125 行）、`format.js`（61 行）、`api.js`（96 行）——这三份**可以直接复用**，不需要框架互操作 |
| 全局 CSS 会互相污染 | `styles.css` 的 `.btn`/`.tag`/`mark.mk` 是全局类，两栈共存时 antd 的 reset 与它会打架 |
| 只有一个入口页 | 单个 `index.html` + 单个 `#app` 挂载点，没有天然的界面级切分点 |
| 回滚已经足够便宜 | 见 §2.2 |

### 2.2 取而代之：并行目录 + 整体切换

```
web/ui/            ← 现有 Vue 应用，迁移期间不动，继续可构建
web/ui-next/       ← 新的 React + TS 应用，独立 package.json
```

两者构建到**不同目录**：

```
web/ui/dist/          ← 现有产物（FastAPI 当前挂载点）
web/ui-next/dist/     ← 新产物
```

FastAPI 的挂载点由一个环境变量或 `config.toml` 键选择（`web/api/app.py:41` 的 `DIST` 常量改为可配）。**这是本方案对后端唯一的改动，且只改一行常量的来源，不改任何接口、任何字段。**

好处：

- 任何时候都能构建出两份产物，A/B 对照（同一台机器、同一份数据、两个端口）；
- 切换与回滚都是改一个配置值 + 重启进程；
- 迁移期间 `web/ui/` 的 Vue 代码是**活的参考实现**，行为对不上时可以当场并排跑；
- 全部完成并稳定运行一段时间后才删 `web/ui/`（时间点由产品定）。

代价：迁移期间两份前端代码并存，`npm install` 两次。对 3,169 行的规模这是可接受的。

### 2.3 三份可直接复用的纯逻辑

| 文件 | 行 | 迁移动作 |
|---|---|---|
| `web/ui/src/marks.js` | 125 | 加类型注解搬成 `marks.ts`，**算法一字不改**。码点换算、`PRIORITY` 重叠裁决、排序规则都是踩过坑的结论（[BASELINE_BEHAVIOR.md §5.3](BASELINE_BEHAVIOR.md)），重写风险远大于收益 |
| `web/ui/src/format.js` | 61 | 搬成 `format.ts` + 两个展示组件（`BerlinTime`/`ShanghaiTime`）。`formatSchedule` 的手工正则解析**必须保留**，不许改成 `Date` + `toLocaleString` |
| `web/ui/src/api.js` | 96 | 搬成 `services/http.ts`。`idPath()` 的"逐段编码、不整体 `encodeURIComponent`"是硬要求（task id 自带斜杠，后端 `{task_id:path}` 依赖它），`fetchResponse` 的 `error.status` / `error.payload` 语义有两处调用方依赖 |

这三份先搬、先测、先冻结，是 Stage A 的内容。

---

## 3. TypeScript 引入

### 3.1 配置

`web/ui-next/tsconfig.json`：`strict: true` 从第一天开始。3,169 行的重写，没有历史包袱，没有理由先宽后严。

```
target: ES2022, module: ESNext, moduleResolution: bundler
jsx: react-jsx
strict: true
noUncheckedIndexedAccess: true      ← 列表索引与 image_attempts[index] 需要
exactOptionalPropertyTypes: true
verbatimModuleSyntax: true
paths: { "@/*": ["./src/*"] }        ← 沿用现有 vite.config.js 的 @ 别名
```

`noUncheckedIndexedAccess` 这条值得单说：现有代码里 `capabilities.image_attempts?.[mediaIndex] || 0`、`shownMarks[active]`、`props.images[current.value]` 都是靠可选链与默认值兜住的索引访问。打开这个开关会强制把它们显式处理，正好是最容易出 `undefined` 的地方。

### 3.2 类型来自哪里

**不从后端生成。** 后端是 FastAPI，但路由返回的是手工拼的 `dict` / `JSONResponse`（`web/api/reader.py` 的 `task_detail()` 就是一个 600 行函数拼出来的字典），**没有 Pydantic 响应模型，因此没有可用的 OpenAPI schema**。`/api/docs` 存在，但响应体是空 schema。

所以类型是**手写的，依据是实测载荷**。我已经把真实响应存到 `state/audit-probe/`（列表、历史、月历、设置、运行状态、两份详情），Stage A 的第一件事是按这些样本逐字段写出类型，并加一层运行期校验（见 §3.4）。

### 3.3 领域类型（从实测载荷推导，不发明字段）

下面每个字段都在实测响应里出现过。**标 `?` 的是实测中出现过 `null` 或缺失的**。

```ts
// ── 枚举与基础 ────────────────────────────────────────────────
type Platform = 'facebook' | 'instagram'

/** core/review.py:16 的七态 */
type ReviewStatus =
  | 'pending_review' | 'edited' | 'snoozed'
  | 'approved' | 'scheduled' | 'skipped' | 'handed_off'

/** reader.py:77 的展示态，不在 core.review.STATUSES 里 */
type DisplayStatus = ReviewStatus | 'not_ready'

/** core/review.py:19 */
type ReviewAction =
  | 'edited' | 'snoozed' | 'woke' | 'skipped' | 'handed_off'
  | 'handoff_link' | 'approved' | 'scheduled' | 'submit_failed'
  | 'text_edited'          // ← 仅前端 trail 用（reader.py:610 把 edited 改写成它）

type RiskKind = 'pun' | 'ambiguous' | 'us_only'
type RiskScanStatus = 'not_scanned' | 'completed' | 'failed' | 'stale'
type HighlightKind = 'money' | 'hashtag' | 'money_review' | 'size' | 'imperial'
type MarkType = 'error' | 'warn' | 'risk'
type LinkOrigin = 'mapping' | 'manual' | 'missing'
type JobStatus = 'pending' | 'running' | 'succeeded' | 'failed' | 'interrupted'
type DeliveryKind = 'published' | 'scheduled' | (string & {})  // 月历卡片，其余按"待核验"

/** `<account_dir>/<post_id>`，自带一个斜杠 */
type TaskId = string & { readonly __brand: 'TaskId' }
/** 64 位小写 hex */
type Sha256 = string & { readonly __brand: 'Sha256' }
/** UUID */
type Revision = string & { readonly __brand: 'Revision' }

// ── 列表 ─────────────────────────────────────────────────────
interface HardAlert { code: string; label: string }

interface ReviewStateRecord {
  status: DisplayStatus
  revision: Revision | null
  wake_at: string | null
  reason: string
  handoff_url: string
  recorded_at: string | null
  source_stale: boolean
  snooze_default_days: number
  // 有事件时额外出现（reader.py 把账本事件整条铺开）：
  post_id?: string
  platform?: Platform
  account?: string
  action?: ReviewAction
  previous_revision?: Revision | null
  source_text_sha256?: Sha256
  actor?: null            // web/DESIGN.md §4：本轮固定 null，界面不得显示用户名
  snapshot_id?: string
}

interface ScheduleSlot { at: string | null; channel: Platform }

interface ReviewListItem {
  id: TaskId
  source_text_sha256: Sha256
  platform: Platform
  thumbnail_url: string
  text_de_excerpt: string          // 90 字截断，空串表示还没有译文
  image_count: number
  tags: string[]
  month: string                    // 'YYYY-MM'
  review: ReviewStateRecord
  schedule: ScheduleSlot | null
  hard_alerts: HardAlert[]
  risk_count: number
  risk_scan_status: RiskScanStatus
  author_flag: string | null       // 常态 null，只有第三方作者才给值
  status: DisplayStatus
}

interface HistoryListItem {
  id: TaskId
  platform: Platform
  month: string
  created_at?: string
  account: string                  // 账号目录名
  read_only: boolean
  text_de_excerpt: string
  tags: string[]
  status: DisplayStatus
  image_count: number
  thumbnail_url: string
}

interface Pagination { page: number; limit: number | null; total: number }
interface RangeInfo { scope: 'review' | 'history'; days: number | null; month: string | null; platform: Platform | null }
interface IndexFreshness { available: boolean; stale: boolean; rebuilt_at: string | null; error: string | null }

interface ReviewListResponse {
  tasks: ReviewListItem[]
  pagination: Pagination
  range: RangeInfo
  index: IndexFreshness
  summary: {
    total: number
    with_hard_alerts: number
    by_status: Record<DisplayStatus, number>
    tags: string[]
  }
}

interface HistoryListResponse {
  tasks: HistoryListItem[]
  index: IndexFreshness
  scope: 'history'
  range: RangeInfo
  pagination: Pagination
  summary: { total: number; tags: string[]; months: string[] }
}

// ── 详情 ─────────────────────────────────────────────────────
interface Highlight {
  kind: HighlightKind
  en_span: [number, number] | null   // ⚠ 码点下标，不是 UTF-16
  de_span: [number, number] | null
  severity: 'error' | 'warn'
  label: string
}

interface BodyRisk {
  kind: RiskKind
  en_span: [number, number]
  label: string
  quote: string
}

interface RiskScan {
  status: RiskScanStatus
  risks: BodyRisk[]
  source_text_sha256: Sha256
  scan_text_sha256: Sha256
  prompt_sha256: Sha256
  prompt_version: number
  source: { provider?: string; model?: string } | null
  scanned_at: string | null
  message: string
  previous_status?: RiskScanStatus   // 仅 stale 时
  previous_risk_count?: number
}

interface LocalizationLink {
  source_url: string
  target_url: string
  mapped_url: string
  confirmed: boolean
  origin: LinkOrigin
}

interface LocalizationDraft {
  platform: Platform
  body_de: string
  source_body: string
  source_tags: string[]
  protected_tags: string[]
  tags: string[]
  hashtags_confirmed: boolean
  links: LocalizationLink[]
  ig_cta: string
  ig_bio_url: string
  cta_presets: string[]
  revision: Revision | null
  source_stale: boolean
  record_stale: boolean      // 服务端在算，当前前端未消费
  has_record: boolean        // 同上
  source_text_sha256: Sha256
}

interface ValidationIssue { code: string; message: string }

interface LocalizationValidation {
  issues: ValidationIssue[]
  warnings: ValidationIssue[]
  ready: boolean
  char_count: number
  body_char_count: number
  hashtag_count: number
}

interface ImageAsset {
  index: number
  original_url: string
  de_url: string
  de_present: boolean            // false = 缺德语图，当前显示的是原图
  metrics: {
    dhash_distance: number | null
    aspect_drift: number | null
    scale_ratio: number | null
    elapsed_s: number | null
  } | null
}

interface OperationRecord {
  at: string                     // 上海时间渲染
  actor: null
  action: ReviewAction
  note?: string | null
  wake_at?: string | null
  revision?: Revision
}

interface TaskDetail {
  id: TaskId
  read_only: boolean
  publication: Record<string, unknown> | null   // 见 §3.5 的不确定项
  delivery: { status: string; observed_at: string | null; message: string }
  platform: Platform
  status: DisplayStatus
  review: ReviewStateRecord
  tags: string[]
  tags_revision: Sha256
  localization: LocalizationDraft
  localization_validation: LocalizationValidation
  body_highlights: Highlight[]
  body_risks: BodyRisk[]
  risk_scan: RiskScan
  text: {
    en: string
    de_machine: string | null
    de_human: string | null
    human_revision: Revision | null
    source_text_sha256: Sha256
    stale: boolean               // 机器译文绑定的原文哈希是否变了
    machine_current: boolean     // 是否满足当前提示词版本
    machine_prompt_version: number | null
    current_prompt_version: number
  }
  highlights: Highlight[]        // 全文版，当前前端未消费
  risks: BodyRisk[]              // 同上
  images: ImageAsset[]
  schedule: ScheduleSlot | null
  meta: {
    platform: Platform
    account: string
    source_text_sha256: Sha256
    source_fingerprint: Sha256 | null
    fingerprint_error: string | null
    snapshot_id: string | null
    permalink: string | null
    created_at: string | null
    owner: string | null
    coauthors: string[]
    author_kind: 'own' | 'collab' | 'third_party'
    compose_warnings: string[]   // 契约外新增字段，见 reader.py:675-680 的理由
  }
  trail: OperationRecord[]
}
```

另有六组类型对应各自端点，字段同样按实测响应写：`ApprovalOptions`、`ApproveResult`、`CalendarPayload` + `CalendarCard`、`OperatingSettings`、`RuntimeSnapshot` + `RuntimeStage`、`ContentJob`（初翻与优化共用形状）、`HashtagSuggestion`。这里不铺开，Stage A 按 `state/audit-probe/*.json` 逐字段落。

### 3.4 运行期校验的边界

**只在开发构建里校验，生产构建不校验。**

理由：这是内网工具，后端是同一个仓库同一次提交出去的，契约漂移的风险来自开发过程而不是运行时；而生产机上多一层校验就多一种"页面因为校验失败而白屏"的故障形态，这个后果比字段缺失更糟。

做法：一个薄的断言层（`services/assert-shape.ts`），在 `import.meta.env.DEV` 下对每个响应做浅层检查（顶层必需键存在、枚举值在白名单内），不通过就 `console.error` 并继续渲染。**不用 zod**——它会把契约定义变成两份（类型 + schema），而我们已经有一份手写类型；也不引入新依赖。

这一条是我的判断，不是硬要求。如果产品希望生产环境也校验并显式失败，说一声改掉。

### 3.5 数据模型里模糊或不一致的地方

这些是写类型时暴露出来的，**我没有替它们下结论**，逐条也在 [OPEN_QUESTIONS.md](OPEN_QUESTIONS.md) 里：

| # | 问题 | 影响 |
|---|---|---|
| 1 | `not_ready` 不在 `core.review.STATUSES` 里，但和七态共用同一个 `status` 字段 | 类型上只能写成联合类型。前端四处判据（分组/可编辑/动作可见/排期可用）都要显式处理它。真实数据 24/26 篇是这个状态 |
| 2 | `review` 对象的形状**随有无账本事件变化**：没有事件时是 7 个键的默认对象，有事件时把整条账本事件铺开（多出 `post_id`/`platform`/`account`/`action`/`source_text_sha256`/`snapshot_id` 等） | 类型只能把后半组标可选。这是 `core/review.py:113` 的 `dict(event) if event else {...}` 造成的 |
| 3 | `publication` 是原样透传的 journal 记录，形状未在任何契约文件里定义 | 只能写 `Record<string, unknown>`。前端目前只读 `attempt_id` 与 `status` 两个键 |
| 4 | `highlights`/`risks`（顶层，全文）与 `body_highlights`/`body_risks`（正文分区）并存，前者前端不用 | 两份数据同源不同范围，不知道顶层那份是给谁的 |
| 5 | `localization.record_stale` / `has_record` 服务端在算、前端不用 | 不知道是否有 UI 需求未实现 |
| 6 | `summary.by_status` 服务端在算、前端不用（页签计数是客户端重算的） | 改服务端分页会立刻打破计数 |
| 7 | 列表项没有 `created_at`，历史项有 | 审校队列无法按原帖时间排序/显示（排序由服务端按 `schedule.at` 定） |
| 8 | 列表项没有 `read_only`，历史项有 | 队列里无法区分冻结账号——目前靠 90 天窗口"碰巧"挡住（`reader.py:331-335` 的注释说明了这件事） |
| 9 | `delivery.status` 是自由字符串（实测 `'unknown'`），没有枚举 | 只能写 `string` |
| 10 | 月历卡片的 `delivery` 同样是自由字符串，前端只判 `'published'`/`'scheduled'` 两个值 | 第三种情形一律显示"待核验" |
| 11 | `ReviewAction` 里 `text_edited` 只存在于前端 `trail`（`reader.py:610` 把账本里的 `edited` 改写成它） | 类型上要把它和其余动作分开，否则会以为账本里有这个值 |
| 12 | `RuntimeStage` 五个阶段的字段各不相同（阶段 1 有 `detection`、3 有 `tag_sampling`/`trends_export`、4 有 `outbox`、5 有 `checks`/`acceptance`/`unconfirmed_attempts`） | 需要可辨识联合（按 `number` 辨识），但后端没有 `kind` 字段，只能按 `number` 判 |

---

## 4. 路由

**React Router v7（data router，`createBrowserRouter`）。**

### 4.1 路由表

```
/                                → redirect /review
/review                          → 审校队列（筛选进 searchParams）
/review/:account/:postId         → 单篇审核
/history                         → 历史归档（筛选与页码进 searchParams）
/history/:account/:postId        → 单篇审核（返回回到历史）
/calendar                        → 发布月历
/settings                        → 运营设置
/runtime                         → 运行状态
*                                → Result 404 + 回队列
```

详情用两条父路由挂同一个界面组件，这样"从哪来回哪去"（[BASELINE_BEHAVIOR.md §2.2](BASELINE_BEHAVIOR.md) 的第一条）由路由结构表达，不再靠 `openTask()` 里的 `if (view !== 'history')` 判断。

### 4.2 旧 URL 必须继续可用

`?task=` 与 `?view=` 不能废：`web/DESIGN.md §11` 规定飞书卡片里带审校链接，在外部系统里可能已经存在这种 URL。

做法：`/` 上一个重定向 loader

| 旧 | 新 |
|---|---|
| `/?task=<account>/<id>` | `/review/<account>/<id>` |
| `/?view=history&task=<account>/<id>` | `/history/<account>/<id>` |
| `/?view=history` | `/history` |
| `/?view=calendar` | `/calendar` |
| `/?view=settings` | `/settings` |
| `/?view=runtime` | `/runtime` |
| `/?view=<未知>` | `/review` |

`task` 参数里的斜杠：`account` 与 `postId` 拆成两段路由参数，拼回 `TaskId` 时用 `${account}/${postId}`。请求 URL 仍走 `idPath()` 的逐段编码。

### 4.3 筛选与来源上下文进 URL（P0-3）

口径见 [DECISION_LOG.md §2.1–2.2](DECISION_LOG.md)。

| 界面 | searchParams |
|---|---|
| `/review` | `queue`（页签）、`platform`、`month`、`tag`、`alerts=1` |
| `/history` | `platform`、`month`、`tag`、`page`、`limit` |
| `/review/:account/:postId` | **`queue`、`platform`、`month`、`tag`、`alerts`** + `tab` |
| `/history/:account/:postId` | **`platform`、`month`、`tag`、`page`、`limit`** + `tab` |

两条关键决定：

1. **页签参数叫 `queue`，不叫 `status`。** 取值 `review` / `not_ready` / `snoozed` / `processed`，分别对应「待我审 / 未就绪 / 已挂起 / 已处理」四个分桶（[DECISION_LOG.md D1](DECISION_LOG.md)）。一个分桶含多个真实 status，所以不能复用 `status` 这个名字——`status` 留给"按单个真实状态筛选"以及后端已有的同名查询参数。`queue` 缺省或取未知值时回落 `review`。

2. **详情 URL 必须携带来源列表的全套筛选。** 这样刷新详情页仍知道「第 n / N 篇」、上一篇/下一篇按进入时的筛选结果走、返回列表恢复原筛选与页码。实现上：详情页用这组参数发起**同一个列表查询**（TanStack Query 命中列表页缓存，无额外请求），从结果算 index 与相邻项。**`location.state` 只能当加速手段，不能当唯一真相源**——它刷新后是 `null`，也不能分享。

除 `queue` 与 `alerts` 外，参数名与后端 query 参数同名（`platform`/`month`/`tag`/`page`/`limit`），少一层映射。

### 4.4 离开守卫

现在是三层手写（[BASELINE_BEHAVIOR.md §2.3](BASELINE_BEHAVIOR.md)）。React Router 有对应设施：

| 层 | 现在 | 目标 |
|---|---|---|
| 应用内导航 | `App.mayLeave()` 串联子组件 `defineExpose` | `useBlocker()`，条件是编辑草稿脏或设置脏 |
| 浏览器前进后退 | 拒绝后 `writeLocation()` 推回 | `useBlocker()` 同一套（它覆盖 popstate） |
| 关闭标签页 | `beforeunload` | 保留原生 `beforeunload` |

确认框从 `window.confirm` 改 `Modal.confirm`，文案**一字不改**（"修改尚未保存，确定离开并放弃当前草稿？" / "设置尚未保存，离开会放弃这次修改。继续离开？"）。

---

## 5. API 层

### 5.1 分三层

```
services/
  http.ts            ← fetchResponse / request / json / idPath（从 api.js 搬）
  tasks.ts           ← 列表、历史、详情、图片 URL、check
  review.ts          ← review action、tags、localization、export
  approval.ts        ← approval-options、approve、publication/reconcile
  jobs.ts            ← initial-translation、refinements、content-jobs、templates、hashtags
  calendar.ts
  settings.ts
  runtime.ts
```

拆分依据是**后端路由模块**（`web/api/` 下就是 `app.py`/`approval.py`/`jobs.py`/`calendar.py`/`settings.py`/`runtime.py`），不是界面。这样后端改一个模块，前端只动一个文件。

### 5.2 三条不许动的实现细节

1. **`idPath()` 逐段编码**（`api.js:7-9`）。task id 自带斜杠，整体 `encodeURIComponent` 会把它变成 `%2F`，后端 `{task_id:path}` 就匹配不上。这个坑 `api.js` 的文件头注释专门写了。
2. **`error.status` 与 `error.payload`**。`ApprovalPanel` 读 `payload.suggestions`，`App.loadCalendar` 读 `payload.cards`（失败时仍用返回体里的卡片渲染）。这两处不是防御性代码，是有意的降级路径。
3. **`exportPost` 的 `Content-Disposition` 文件名解析 + `URL.createObjectURL` + 30 秒后 `revokeObjectURL`**（`api.js:58-62`、`ReviewActions.vue:47-57`）。整套下载流程照搬。

### 5.3 图片 URL

`imageUrl()` 是纯函数，不经 Query。注意 `_images_of()` 给 `de_url` 带了 `?v=<source>-<text>-<output_sha>` 的版本串（`reader.py:522-528`），而列表的 `thumbnail_url` 没有版本串——两者的缓存行为不同，迁移时不要"统一"掉。后端响应带 `Cache-Control: no-cache`。

---

## 6. TanStack Query 的使用边界

### 6.1 只管服务端状态

按 [UI_ARCHITECTURE_PROPOSAL.md §13](UI_ARCHITECTURE_PROPOSAL.md) 的四类划分，**只有第一类进 Query**。具体不进的：编辑草稿、标记游标、图片 `seen` 集合、当前图片索引、模态框开关、当前标签页、侧边导航折叠。

尤其：**编辑草稿绝不放 Query 缓存**。它是本地未提交状态，放进缓存会被 `invalidate` 抹掉，而这里的草稿代表运营已经做过的人工劳动（`web/DESIGN.md §8`：源文或提示词变化"过期，保留人工劳动"）。

### 6.2 Query key 设计（概念层）

```
['tasks', 'review', { status, platform, month, tag, alerts }]
['tasks', 'history', { platform, month, tag, page, limit }]
['task', taskId]                          ← 详情
['task', taskId, 'approval-options']
['task', taskId, 'initial-capabilities']
['task', taskId, 'refinement-capabilities']
['job', 'initial', jobId]
['job', 'refinement', jobId]
['template', kind]
['calendar']
['settings']
['runtime']
```

### 6.3 失效边界

这是最需要想清楚的部分。现在的做法是**行内打补丁不重拉列表**（`App.patchRow()`，[BASELINE_BEHAVIOR.md §5.2](BASELINE_BEHAVIOR.md) 第 4 条），这个行为有价值：列表 `GET /api/tasks` 会触发 SQLite 展示索引的按需重建，在 1,067 篇的归档上不便宜。迁移后用 Query 的 `setQueryData` 复现同一效果。

| 变更 | 失效 / 更新 |
|---|---|
| 保存本地化 | `setQueryData(['task', id], 响应)`；`setQueryData` 就地改列表里那一行（等价于 `patchRow`）；**不** `invalidate` 列表 |
| 审校动作（挂起/不发/接管/恢复/补链接） | 同上。状态变了可能让该行离开当前页签——用乐观的就地更新 + 一个"该项已移出当前筛选"的轻提示，而不是整列表重拉 |
| 保存分类 | 同上 |
| export | `setQueryData(['task', id], 重新 GET 的结果)`（现有代码就是 export 后再 GET 一次） |
| 排期成功 | `invalidate(['task', id])` + `invalidate(['calendar'])` + 就地更新列表行 |
| 初翻 / 优化任务完成 | `invalidate(['task', id])` + 对应 capabilities |
| 月历刷新 | `setQueryData(['calendar'], 响应)`（失败时用 `error.payload.cards`） |
| 保存设置 | `setQueryData(['settings'], 响应)` |
| 运行状态恢复动作 | `invalidate(['runtime'])` |
| 显式点"审校列表"导航 | `invalidate(['tasks','review'])` —— 这是现在 `openReviews()` 的行为，唯一的整列表重拉入口 |

### 6.4 轮询

两处 `setInterval` 改 `refetchInterval`：

| 查询 | 间隔 | 停止条件 |
|---|---|---|
| `['job','initial',id]` | 1500ms（沿用） | `status ∈ {succeeded, failed, interrupted}` |
| `['job','refinement',id]` | 1200ms（沿用） | 同上 |
| `['runtime']` | 30000ms（沿用） | 界面不可见时暂停（`refetchIntervalInBackground: false`） |

现有代码用 `alive` 标志 + `polling` 互斥锁避免重入与卸载后写入（`RefinementPanel.vue:12-18`），Query 自带这两件事，这部分手写逻辑可以去掉。

### 6.5 全局配置

```
staleTime: 0                       ← 内网工具，数据要新；不做乐观缓存
gcTime: 5 分钟
refetchOnWindowFocus: false        ← 她会在编辑中切窗口去看飞书，回来不能被重拉打断
retry: 0                           ← 409/400 重试没有意义，5xx 由人点重试
throwOnError: false                ← 错误进界面，不进 error boundary
```

`retry: 0` 值得说明：这里的失败大多是 409（版本冲突）或 400（输入不合法），重试只会重复失败；而 `POST /api/calendar/refresh` 这类重试有外部副作用。

---

## 7. 目录结构

**从仓库现状推导，不照抄通用模板。** 依据两条：后端按路由模块分文件（§5.1），前端界面按 [UI_ARCHITECTURE_PROPOSAL.md §3](UI_ARCHITECTURE_PROPOSAL.md) 分六个。

```
web/ui-next/
  package.json
  tsconfig.json
  vite.config.ts              ← base '/'、@ 别名、sourcemap、/api proxy 到 8765（沿用现状）
  index.html                  ← lang="zh-CN"、标题沿用
  src/
    main.tsx
    app/
      router.tsx              ← createBrowserRouter + 旧 URL 重定向（§4.2）
      AppShell.tsx            ← Layout/Sider/Header/Content
      Navigation.tsx
      RuntimeIndicator.tsx    ← 顶栏状态指示器（运行状态降级后的入口）
      theme.ts                ← ConfigProvider 的 token 与组件级覆盖
      queryClient.ts
    pages/
      review/                 ← 审校队列
      review-detail/          ← 单篇审核（本目录最大，见下）
      history/
      calendar/
      settings/
      runtime/
    features/
      post-list/              ← PostRow 列工厂、筛选条、批量条（队列与历史共用）
      localization/           ← 正文对照、标记渲染、标签区、链接区、字符计数
      images/                 ← 接触表、单张对照、seen 追踪
      review-actions/         ← 三种决定 Modal（挂起 / 终止 / 转交）
      approval/               ← 决策卡、时刻选择、冲突建议
      content-jobs/           ← 初翻、优化、任务恢复
      diagnostics/            ← 处理记录 Drawer、诊断 Drawer
    components/               ← 与领域无关的展示件
      StatusTag.tsx  PlatformLabel.tsx  BerlinTime.tsx  ShanghaiTime.tsx
      ProblemIndicator.tsx  ConflictRecovery.tsx  PaidActionButton.tsx
      SectionCard.tsx  IssueList.tsx  EmptyState.tsx
    services/                 ← §5.1 的八个文件
    hooks/                    ← useReviewList / useTaskDetail / useSaveLocalization / …
    types/
      domain.ts               ← §3.3
      api.ts                  ← 请求与响应包装
      brands.ts               ← TaskId / Sha256 / Revision
    lib/
      marks.ts                ← 从 marks.js 搬，算法不改
      format.ts               ← 从 format.js 搬，算法不改
    styles/
      tokens.css              ← 语义层 CSS 变量（见 DESIGN.md §2）
      global.css              ← 极少：滚动条、prefers-reduced-motion、mark.mk 三档
      *.module.css            ← 与组件同目录
```

四条分层规则：

| 层 | 允许依赖 | 不允许 |
|---|---|---|
| `pages/` | `features/`、`components/`、`hooks/`、`types/` | 直接 import `services/`（走 hooks） |
| `features/` | `components/`、`hooks/`、`types/`、`lib/` | import `pages/`；互相 import（需要共享就上提到 `components/`） |
| `components/` | `types/`、`lib/` | 任何业务查询 |
| `services/` | `types/`、`lib/` | React（纯函数，可在 node 里测） |

`services/` 不依赖 React 这一条是为了让契约层可以单独测：把 `state/audit-probe/*.json` 当夹具喂给它，断言解析结果，不需要起浏览器。

### 7.1 CSS Modules

| 归属 | 放哪 |
|---|---|
| 设计 token | `styles/tokens.css`，语义命名，由 `ConfigProvider` 的 token 映射过来（单一来源见 [DESIGN.md §2](DESIGN.md)） |
| 全局 | `styles/global.css`：滚动条、`prefers-reduced-motion`、`mark.mk-*` 三档标记 |
| 组件 | 同目录 `X.module.css` |
| antd 覆盖 | **优先走 `ConfigProvider` 的 token 与 `components` 配置**；必须写 CSS 时用 `:global()` 限定在组件的 module 作用域内 |

`mark.mk-*` 留在全局的理由：它作用在 `dangerouslySetInnerHTML` 之外的动态片段序列上，且要在只读层与编辑镜像层两处同样生效，走 module 会让两层的类名对不上。现有 `styles.css:143-158` 那几条直接搬，但按 [UI_AUDIT.md P0-7](UI_AUDIT.md) 调整 `warn` 与 `risk` 的视觉重量。

**不引入 Tailwind、shadcn/ui、Radix、Framer Motion、Lucide、Sonner、React Hook Form、Ant Design Pro Components。** 表单用 antd 的 `Form`（它自带校验与布局，这里的表单只有设置页与几个小输入，不需要 RHF）。

### 7.2 依赖清单（Stage A 安装，不多不少）

```
dependencies:    react react-dom react-router @tanstack/react-query
                 antd @ant-design/icons
                 dayjs                     ← 仅因代码直接 import 它的 locale（antd 的 DatePicker 依赖链）
devDependencies: typescript @types/react @types/react-dom
                 vite @vitejs/plugin-react
                 vitest                    ← 单测运行器；见下
```

两点说明：

- **`@playwright/test` 不装。** 浏览器行为回归沿用现有 Python Playwright + `tests/browser_fixture.py`，理由见 §9.2。
- **`vitest` 是产品指定清单之外唯一新增的一项。** Stage A 的完成判据里有九项单测（[DECISION_LOG.md §5.6](DECISION_LOG.md)），需要一个运行器；Vitest 与 Vite 同源、复用同一份 `vite.config.ts` 与 `tsconfig`，是这里成本最低的选择，且只是 devDependency，不进生产产物。它不在禁装清单里。

**不装**：Tailwind、shadcn/ui、Radix、Framer Motion、Lucide、Sonner、React Hook Form、Ant Design Pro Components。

---

## 8. Ant Design 集成

### 8.1 单一 token 来源

```
theme.ts  →  ConfigProvider theme={{ token, components, cssVar: true }}
                      ↓ (cssVar 把 token 暴露成 CSS 变量)
             styles/tokens.css 里的语义变量引用它们
                      ↓
             *.module.css 只用语义变量，不写字面值
```

`cssVar: true` 的可用性与变量命名是 §0 里标的第三个**待核**项。拿不到就反过来：`tokens.css` 写字面值，`theme.ts` 从同一份 TS 常量生成两边。无论哪种，**颜色与间距的字面值只允许出现在一个文件里**。

### 8.2 语言与主题

- `ConfigProvider locale={zhCN}`（`antd/locale/zh_CN`），`dayjs` 的 `zh-cn` locale 一并装。界面是中文，日期选择器不能是英文。
- **只做浅色主题。** 没有需求，且深色会让"红=错了、黄=注意看"两种标记色需要第二套验证。
- `@ant-design/icons` 按需 import（它是 tree-shakable 的）。`Icon.vue` 里那 15 条 Lucide 路径逐个对应到 antd 图标，对不上的（几乎不会有）保留内联 SVG。

### 8.3 不用 antd 的地方

| 位置 | 为什么 |
|---|---|
| 正文标记 `<mark>` | 需要逐字符控制，没有对应组件 |
| 德语编辑器 | 透明 `textarea` + 等排版镜像层，`Input.TextArea` 的内边距与包装层不可控（[BASELINE_BEHAVIOR.md §5.4](BASELINE_BEHAVIOR.md) 第 3 条） |
| 月历网格 | `Calendar` 的 `dateCellRender` 不适合多条目 + 跨月标记 + 自适应格高 |
| 吸顶审校头 | 三段布局，没有现成组件（`PageHeader` 已从 antd 移除） |
| 标记地图 | 按位置绝对定位的刻度条 |

原则：**antd 提供基元与行为，不决定信息架构。** 哪里自己写页面组合更简单就自己写，但控件（按钮、输入、选择、弹层、表格）一律用 antd，不再手写 `.btn`/`.tag`。

---

## 9. 迁移顺序

### 9.1 阶段

评估与调整的理由在 [UI_ARCHITECTURE_PROPOSAL.md §15](UI_ARCHITECTURE_PROPOSAL.md)。

| Stage | 内容 | 完成判据 |
|---|---|---|
| **A** | `web/ui-next/` 骨架：Vite + React + TS(strict) + Router（含旧 URL 重定向与 §4.3 的 search params）+ Query + antd；`marks.ts` / `format.ts` / `services/http.ts` 搬迁并单测；按 `state/audit-probe/*.json` 写出全部领域类型；`web/api/app.py` 的 `DIST` 改可配 | `npm run build` 通过；九项单测全绿（marks 码点 / 重叠 / 排序、format 柏林 / DST / 上海、http `idPath` / `error` / export）；真实载荷 shape smoke test 通过；现有 Python 测试不回退；antd 6 三项核验有结论 |
| **B1** | AppShell + 导航 + `theme.ts` + `tokens.css` + 路由表 + 旧 URL 重定向 + 离开守卫 | 回归 B1–B4 绿；六个路由都能打开（内容可为占位） |
| **B2** | 共享基元：`StatusTag`、`PlatformLabel`、`BerlinTime`/`ShanghaiTime`、`ProblemIndicator`、`ConflictRecovery`、`PaidActionButton`、`PostRow` 列工厂 | 时刻组件的 DST 用例绿（B12）；八态 `Tag` 有截图 |
| **C** | 审校队列 | 回归 B6（列表部分）绿；1366 首屏 ≥12 行 |
| **E′** | 历史归档（提前，与 C 共用 `PostRow`） | 回归 B19 绿；分页不需滚到底 |
| **D** | 单篇审核（最大的一个 Stage，建议内部再分：D1 吸顶头+正文对照 → D2 标签与链接 → D3 图片 → D4 决策卡与排期 → D5 优化与抽屉） | 回归 B5–B18 全绿 |
| **F** | 发布月历 | 日格不溢出；表头吸顶 |
| **G** | 运营设置 | 回归 B20 绿；`editable_help` 不再常驻 |
| **H** | 运行状态（含降级为顶栏入口） | 技术原文全部在折叠层 |
| **I** | 全量行为回归（20 条）+ 新旧并行对照 | 20 条全绿；两份 `dist/` 在同一数据上行为一致 |
| **J** | 视觉 QA：1920×1080 与 1366×768 | `capture_baseline.py` 指向新应用，量值达标 |

C 与 E′ 相邻是刻意的：两者共用 `PostRow`，隔着 D 做完再回来会让共享层被详情的需求带偏（[UI_ARCHITECTURE_PROPOSAL.md §15](UI_ARCHITECTURE_PROPOSAL.md)）。

**C + D 是参考实现。** 它们定下的模式（列定义、吸顶头三段、渐进披露的承载方式、Query 失效边界）是后面四个 Stage 照着做的样板，所以这两个 Stage 的评审要比其它严。

### 9.2 Playwright 基线

现有的 `tests/tests_browser_workflow.py` 用 **Python Playwright** + `Config.chrome_exe` + `launch_persistent_context(headless=True)`，跑在 `tests/browser_fixture.py` 的隔离数据上，已有 7 个场景通过。

**沿用这一套，不引入 `@playwright/test`。** 理由：

1. 隔离夹具（临时 archive/state、回环 socket 限制、非 GET 白名单、退出时校验 `config.toml` 未变）是 Python 侧的资产，重写一份 Node 版本要把这些安全边界再实现一遍；
2. 它已经在 CI 语境里跑过（`scripts\run_python.bat tests/tests_browser_workflow.py -v`）；
3. 新增用例是往同一个文件/同一套夹具里加，不是建第二套体系。

⚠️ 这些测试只准打夹具。`POST /api/tasks/{id}/approve` 与 `POST /api/calendar/refresh` 会动到平台侧，夹具的 ASGI 白名单已经挡住它们。

**本轮新增的测试资产**（已入库，不改动生产代码）：

| 文件 | 作用 |
|---|---|
| `docs/ui-refactor/tools/audit_fixture_host.py` | 起一个带 18 个多样样本（硬闸/风险/人工稿/多图/IG/挂起/不发/已接管）的隔离宿主，供人工观察与截图 |
| `docs/ui-refactor/tools/capture_baseline.py` | 在 1920×1080 与 1366×768 截 9 屏 + 量 11 项尺寸，产出 `screenshots/measurements.json` |

迁移期的用例来源是 [BASELINE_BEHAVIOR.md §12](BASELINE_BEHAVIOR.md) 的 20 条。建议按 Stage 分批加，而不是一次写 20 个：

| Stage | 新增用例 |
|---|---|
| B1 | B1 B2 B3 B4 |
| B2 | B12 B13 |
| C / E′ | B6（列表）B19；新增「筛选进出详情后仍在」（修 P0-3 的守卫） |
| D | B5 B7 B8 B9 B10 B11 B14 B15 B16 B17 B18；新增「上一篇/下一篇」 |
| G | B20 |

### 9.3 回归策略

三层，逐层收窄：

| 层 | 覆盖 | 何时跑 |
|---|---|---|
| **单测**（Vitest 或 node:test） | `marks.ts`（码点/重叠/排序）、`format.ts`（柏林/上海/DST）、`services/*`（用 `state/audit-probe/*.json` 当夹具断言解析与请求体拼装） | 每次提交 |
| **浏览器行为回归**（Python Playwright + 隔离夹具） | 20 条基线 | 每个 Stage 结束 |
| **新旧并行对照** | 同一台机、同一份夹具数据、两个端口分别跑 `web/ui/dist` 与 `web/ui-next/dist`，对同一组操作比对**网络请求序列与请求体** | Stage I |

第三层是这套方案里最有价值的一项：它直接回答"业务行为有没有变"。实现上只需要在两个宿主前各挂一个记录中间件，把 `method + path + body 的键集合 + 关键字段值` 存成序列再 diff。**不比对像素**——旧 UI 难看，像素级一致不是目标（提纲第 19 节明确写了这一点）。

### 9.4 回滚

| 触发 | 动作 | 代价 |
|---|---|---|
| 新 UI 有阻塞缺陷 | 把 FastAPI 的挂载点配置切回 `web/ui/dist`，重启进程 | 一次重启。数据无影响（后端与账本完全没动） |
| 某个 Stage 做坏了 | 该 Stage 的分支不合并 | 零 |
| 发现契约被无意改动 | Stage I 的并行对照会在合并前抓到 | 零 |

⛔ 唯一真正不可逆的风险不在前端，而在**测试意外打到真实排期或真实飞书**。防线是夹具的 ASGI 白名单 + 绝不把测试指向 8765 上连真实归档的那个宿主。迁移期间本机同时跑两个宿主时，端口要写在 README 里并且固定：真实数据 8765，夹具随机高端口。

### 9.5 收尾

| 步骤 | 条件 |
|---|---|
| 删 `web/ui/` | 新 UI 在生产稳定运行一段时间（由产品定，建议至少覆盖一个完整的审校周） |
| `web/ui-next/` 改名回 `web/ui/` | 同上之后 |
| FastAPI 挂载点常量改回硬编码 | 同上之后 |
| `web/DESIGN.md` 更新前端章节 | Stage I 结束时（它是接口契约的真相源，不能滞后） |

---

## 10. 本轮没做、也不该在批准前做的事

- 没有创建 `web/ui-next/`；
- 没有 `npm install` 任何迁移依赖；
- 没有改 `web/ui/` 下任何文件；
- 没有改 `web/api/` 下任何文件（包括 §2.2 提到的 `DIST` 常量）；
- 没有改任何接口、字段名、业务流程；
- 没有动 git 里已有的 4 个未提交修改（`core/index_db.py`、`core/review.py`、`tests/tests_history.py`、`web/api/query_index.py`），它们在我开始之前就在那里。

新增的文件全部在 `docs/ui-refactor/` 下。
