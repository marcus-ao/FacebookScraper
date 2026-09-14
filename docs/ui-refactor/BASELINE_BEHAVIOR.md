# 审校台现状行为基线（重构前冻结）

**成文日期：2026-09-13。** 代码基线 `3536e29`（核心集成 `565f17c`）。取证方式：通读 `web/ui/src/` 全部 23 个文件与 `web/api/` 全部 10 个模块；本机启动 `uvicorn web.api.app:app --port 8765` 连**真实归档**做只读 GET；另用隔离夹具起第二个宿主做交互观察。

这份文件回答一个问题：**重构之后，哪些行为必须一字不差地还在。**

它不评价好坏——评价在 [UI_AUDIT.md](UI_AUDIT.md)。这里只记录"现在按下去会发生什么"，包括那些界面做得难看但业务上不能丢的部分。

| 文件 | 管什么 |
|---|---|
| **本文件** | **现状行为，重构的回归清单** |
| [SCREEN_INVENTORY.md](SCREEN_INVENTORY.md) | 六个界面各自的任务、控件、状态、截图 |
| [UI_AUDIT.md](UI_AUDIT.md) | 按 P0–P3 分级的 UX 问题与处理方向 |
| [UI_ARCHITECTURE_PROPOSAL.md](UI_ARCHITECTURE_PROPOSAL.md) | 目标信息架构与 Ant Design 映射 |
| [REACT_MIGRATION_PLAN.md](REACT_MIGRATION_PLAN.md) | Vue → React + TypeScript 的迁移顺序与回归策略 |
| [DESIGN.md](DESIGN.md) | 未来设计系统草案 |
| [OPEN_QUESTIONS.md](OPEN_QUESTIONS.md) | 需要产品决定、我不替你定的事 |
| [../../web/DESIGN.md](../../web/DESIGN.md) | **接口契约的真相源**，本文件不覆盖它 |

---

## 0. 这一天是什么样的

上海运营早上 08:30 打开 `http://127.0.0.1:8765`，页面直接落在审校列表。她要做的事是：**从近 90 天的图文帖里挑出一篇，看懂它、改好德语、核对图片、选一个柏林时刻，然后去下一篇。**

2026-09-13 07:51 的真实一次读取里，这个列表是 26 篇：24 篇 `not_ready`、1 篇 `snoozed`、1 篇 `skipped`，`pending_review` 为 0，硬闸告警 0 条。也就是说她今天打开系统，**24 行文字完全相同**（都是"还没有德语译文 / 暂无建议时刻 · Facebook · 1 张图 · 待处理"），而这 24 篇一篇都还不能审。历史归档另有 1,067 篇，分 36 页。

她点进一篇，从上往下会依次遇到：一条吸顶动作条（13 个控件）、一条 10px 高的标记地图、一块"英文语义风险预扫"面板、英德双栏对照、话题标签区、链接区、字符计数、图片对比、元信息、审核与排期、单篇优化。整页在 1366×768 下是 2.6 个屏高；**她真正要按的"通过并创建排期"在第 1,524 像素处**，也就是往下滚两屏才出现。

改完一篇之后她按"返回列表"。列表回到默认筛选——她刚才设的状态页签、月份、分类全部丢失（§6.3 有实测）。然后她重新筛一次，再点下一篇。

这份基线记录的就是上面每一步背后的实际机制。

---

## 1. 技术基线

| 项 | 现状 |
|---|---|
| 框架 | Vue 3.5.13，`<script setup>` SFC，无 TypeScript |
| 构建 | Vite 6.0.7，`@vitejs/plugin-vue` 5.2.1，`base: '/'`，`sourcemap: true` |
| 依赖总数 | 生产依赖 **1 个**（`vue`）；无路由库、无状态库、无组件库、无 CSS 框架 |
| 代码量 | `web/ui/src/` 共 3,169 行：`TaskDetail.vue` 487、`TaskList.vue` 240、`App.vue` 235、`TextCompare.vue` 226、`ImageCompare.vue` 210，其余 18 个文件合计 1,771 |
| 样式 | 一份全局 `styles.css`（179 行，含 CSS 变量与 `.btn`/`.tag`/`mark.mk` 三套全局类）+ 每个组件的 `<style scoped>` |
| 图标 | `Icon.vue` 内联 15 条 Lucide 几何路径（ISC），无图标依赖 |
| 字体 | 系统栈（`Segoe UI` 优先）。生产机要求断网可跑，不引外链字体 |
| 部署 | `npm run build` → `web/ui/dist/` → FastAPI 用 `StaticFiles` 挂在 `/`。生产机不需要 Node |
| 开发 | Vite 5173 + `/api` proxy 到 127.0.0.1:8765；构建后同源，proxy 不参与 |
| 浏览器持久化 | **无**。实测 `localStorage`、`sessionStorage`、`document.cookie` 全为空 |

**改哪里**：`web/ui/package.json`、`web/ui/vite.config.js`、`web/api/app.py:41`（`DIST` 常量）、`web/api/app.py:245-259`（`/` 与 `StaticFiles` 挂载）。

---

## 2. 路由与导航

### 2.1 这一步谁在做什么

她在五个工作区之间切换，用的是页面顶部第二行那五个小按钮。点"历史归档"再点某一篇，看完按"返回列表"——回到的是历史，不是待审队列。这个"从哪来回哪去"是现在唯一一条有状态的导航逻辑。

### 2.2 机制

没有路由库。`App.vue` 自己读写 `window.location`：

```
locationState()  读 ?task= 与 ?view=
writeLocation()  history.pushState，不产生 hashchange
restoreLocation() 绑在 popstate 上
```

| URL | 渲染 |
|---|---|
| `/` | `TaskList` |
| `/?task=<account>/<post_id>` | `TaskDetail` |
| `/?view=history` | `HistoryPanel` |
| `/?view=history&task=<id>` | `TaskDetail`（返回时回到历史） |
| `/?view=calendar` | `CalendarPanel`，并触发 `loadCalendar()` |
| `/?view=settings` | `SettingsPanel` |
| `/?view=runtime` | `RuntimePanel` |
| `?view=` 任何其它值 | 白名单过滤，回落 `tasks` |

`view` 白名单硬编码在 `App.vue:20`：`['calendar','history','settings','runtime']`。

必须保留的两条细节：

1. **`openTask()` 在 `view === 'history'` 时不改 `view`**（`App.vue:57`）。所以从历史打开的详情，`?view=history&task=…`，返回按钮回到历史列表。
2. **只有"审校列表"页签会重新拉数据**（`openReviews()` 调 `load()`，`App.vue:77`）。切到月历会拉月历；切到历史/设置/运行由子组件自己 `onMounted` 拉。从详情返回列表**不重新拉列表**，靠 §5.2 的行内打补丁。

**改哪里**：`web/ui/src/App.vue:13-119`。

### 2.3 离开守卫

三层，全部必须保留：

| 层 | 实现 | 触发 |
|---|---|---|
| 应用内导航 | `App.mayLeave()` 串联 `detailView.mayLeave()` 与 `settingsView.mayLeave()`（`App.vue:15`） | 五个 `openX()` 与 `restoreLocation()` 全部先问 |
| 浏览器前进后退 | `restoreLocation()` 被拒时 `writeLocation()` 把 URL 推回去（`App.vue:100-108`） | `popstate` |
| 关闭标签页 | `TaskDetail.beforeUnload` 与 `SettingsPanel.beforeUnload` | `window.beforeunload` |

详情的判据是 `hasUnsavedChanges()`：草稿正文与 `localization.body_de` 不同，**或** `tags`/`links`/`hashtags_confirmed`/`ig_cta` 四个键的 JSON 序列化与基线不同（`TaskDetail.vue:70-75`）。设置的判据是 `dirty`：`{default_times, snooze_default_days}` 的 JSON 与 `data.editable` 不同。

两处确认文案：详情 `修改尚未保存，确定离开并放弃当前草稿？`，设置 `设置尚未保存，离开会放弃这次修改。继续离开？`。都是原生 `window.confirm`。

### 2.4 键盘

`TaskDetail` 在 `document` 上挂 `keydown`：

| 键 | 动作 |
|---|---|
| `n` / `ArrowDown` | 跳到下一处标记 |
| `p` / `ArrowUp` | 跳到上一处标记 |
| `Escape` | 返回列表（走 `mayLeave()`） |

三条抑制条件（`TaskDetail.vue:237-238`）：页面上存在 `[role="dialog"]`、正在编辑、或焦点在 `input`/`textarea`。界面上没有任何地方提示这三个键存在。

---

## 3. 七态状态模型与允许的动作

### 3.1 这一步谁在做什么

她对一篇的处理只有五种结局：改完通过排期、挂起等信息、决定不发、自己下载去手工发、或者留在原地。系统把这五种结局记成状态，写进 `review_items.jsonl`，每条带一个 UUID `revision`。

### 3.2 状态来源

状态**不是**前端算的。`core/review.py:16-18` 定义：

```
STATUSES = {pending_review, edited, snoozed, approved, scheduled, skipped, handed_off}
TERMINAL = {skipped, handed_off, scheduled}
ACTIONS  = {edited, snoozed, woke, skipped, handed_off, handoff_link, approved, scheduled, submit_failed}
```

`web/api/reader.py` 另加一个**展示态** `not_ready`（`reader.py:77`），表示内容尚未就绪（没有可用译文）。它不在 `STATUSES` 里，前端把它和其它七个一起当字符串用。

三条派生规则在 `review.state_for()`：

1. 传入 `scheduled=True`（`published.jsonl` 有成功回读）→ 状态强制 `scheduled`；
2. 源文哈希变了且当前不是终态 → 状态回落 `pending_review`，`wake_at` 清空；
3. 否则用账本里最后一条事件的状态，没有事件就用 `default_status`。

`default_status` 由 `reader._review_state()` 算：有人工稿且不 stale → `edited`，有人工稿但 stale → `pending_review`，没有人工稿且不可审 → `not_ready`。

### 3.3 前端对状态的三种消费

| 用途 | 位置 | 规则 |
|---|---|---|
| 列表分组 | `TaskList.vue:17-18` | `snoozed` → 已挂起；`scheduled`/`skipped`/`handed_off` → 已处理；其余（含 `not_ready`、`approved`）→ 待处理 |
| 详情可编辑 | `TaskDetail.vue:76` | `canEdit = !read_only && status ∉ {approved, scheduled, skipped, handed_off}` |
| 行内动作可见 | `ReviewActions.vue:12` | `active = status ∈ {not_ready, pending_review, edited, snoozed}` |
| 排期可用 | `ApprovalPanel.vue:27` | `eligible = status ∈ {pending_review, edited}` |
| 优化可用 | `RefinementPanel.vue:20` | `eligible = status ∈ {pending_review, edited, not_ready} 且 !text.stale` |

注意 `approved` 落在"待处理"分组里但既不能编辑也不能动作——这是当前行为，不是我加的判断。

### 3.4 中文状态词

`format.js:39-48` 的 `STATUS_LABEL` 是界面上唯一一份状态文案表：

| 键 | 显示 |
|---|---|
| `not_ready` | 待处理 |
| `pending_review` | 待我审 |
| `edited` | 已修改 |
| `snoozed` | 已挂起 |
| `approved` | 已通过 |
| `scheduled` | 已排期 |
| `skipped` | 这篇不发 |
| `handed_off` | 已交人工处理 |

`MetaPanel.vue:17-27` 另有一份 `ACTION_LABEL`（动作 → 中文），两份表都必须搬过去。

**改哪里**：`web/ui/src/format.js`、`web/ui/src/components/MetaPanel.vue:17-27`。

---

## 4. API 契约清单

下表按"UI 动作 → 前端处理 → 端点"逐条列出。**字段名一个都不许改**，重构只换渲染层。

### 4.1 只读

| UI 动作 | 前端 | 方法与路径 | 关键请求 | 关键响应 | 界面效果 |
|---|---|---|---|---|---|
| 打开首页 / 点"审校列表" | `App.load()` → `api.listTasks()` | `GET /api/tasks` | 无参数 | `tasks[]`、`summary{total,with_hard_alerts,by_status,tags}`、`pagination{page,limit,total}`、`range{scope,days,month,platform}`、`index{available,stale,rebuilt_at,error}` | 行、总数、`index.stale` 时顶部黄条 |
| 历史归档、改筛选、翻页 | `HistoryPanel.load(page)` | `GET /api/tasks?scope=history&page&limit=30&platform&month&tag` | 空串与 `null` 被 `api.listTasks` 过滤掉 | 同上 + `summary{tags,months}`、`scope:'history'`、行内 `read_only`、`thumbnail_url`、`account`、`created_at` | 历史行、`共 N 篇`、`第 p / ⌈total/30⌉ 页` |
| 点"查看" / "查看归档" | `TaskDetail.load()` | `GET /api/tasks/{task_id:path}` | — | 见 §4.4 | 整个详情页 |
| 缩略图与图片对比 | `api.imageUrl()` | `GET /api/tasks/{id}/image/{index}?variant=de\|original` | `variant` 正则 `^(de\|original)$` | 图片字节，`Cache-Control: no-cache` | 缺德语图时**回退原图**，由 `images[].de_present` 另行明示 |
| 打开排期区 / 点"重新核对排期条件" | `ApprovalPanel.load()` | `GET /api/tasks/{id}/approval-options` | — | `available`、`reason`、`fingerprint`、`platform`、`default_times[]`、`earliest`、`latest`、`ui_timezone` | 时间输入的 `min`/`max`、常用时间按钮、不可用原因 |
| 打开详情（第三方作者时） | `InitialTranslationPanel.refresh()` | `GET /api/initial-translation/task/{id}` | — | `third_party`、`available`、`reason`、`source_fingerprint`、`job` | 整个"这篇来自第三方作者"区块的存在与否 |
| 打开详情 | `RefinementPanel.loadCapabilities()` | `GET /api/refinements/task/{id}` | — | `jobs[]`、`max_refine_per_media`、`image_attempts{}`、`estimated_image_usd`、`estimate_basis` | 剩余次数、预估单价、当前任务卡 |
| 轮询 | 1500ms / 1200ms `setInterval` | `GET /api/initial-translation/jobs/{id}`、`GET /api/refinements/jobs/{id}` | — | `status ∈ {pending,running,succeeded,failed,interrupted}`、`kind`、`media_index`、`instruction`、`body_de`、`cost_usd`、`paid_request_ids[]`、`worker_state`、`message`、`error`、`source_text_sha256` | 任务状态文案、候选正文、费用行 |
| 点"查看模板（只读）" | `RefinementPanel.toggleTemplate()` | `GET /api/templates/{text\|image}` | — | `kind`、`content`、`read_only` | 折叠的 `<pre>` |
| 发布月历 | `App.loadCalendar()` | `GET /api/calendar` | — | 见 §4.5 | 月格 |
| 运营设置 | `SettingsPanel.load()` | `GET /api/settings` | — | `version`、`editable{default_times,snooze_default_days}`、`controlled{}`、`controlled_fields{}`、`editable_help{}` | 两个输入 + 七个折叠的只读配置 |
| 运行状态（含每 30s 重拉） | `RuntimePanel.load()` | `GET /api/runtime` | — | `observed_at`、`read_only`、`activation`、`process{alive,last_tick_at}`、`business{last_successful_run,processing,timings}`、`stages[5]`、`heartbeat`、`network` | 五个阶段卡 |
| 编辑时实时校验 | 250ms 防抖 `api.check()` | `POST /api/tasks/{id}/check` | `{text_de, body_only:true, localization:<完整草稿>}` | `highlights[]`、`caption_length`、`hashtag_count`、`warnings[]`、`issues[]` | 实时标记、字符计数、问题行 |

`POST …/check` 虽然是 POST，**只算不写**（`web/api/app.py:179-209` 的 docstring 明确写了它永不拒绝保存）。重构时它归在只读一侧。

### 4.2 写入

| UI 动作 | 方法与路径 | 关键请求 | 写到哪 | 409 恢复入口 |
|---|---|---|---|---|
| 详情"保存" | `PUT /api/tasks/{id}/localization` | `body_de`、`tags[]`、`hashtags_confirmed`、`links[]`、`ig_cta`、`source_text_sha256`、`human_revision`、`review_revision`、`localization_revision` | `translated_human.jsonl` + `localization.jsonl` + 一条 `edited` 审校事件 | "载入最新内容并保留草稿"（`refreshContext()`） |
| 稍后再审 / 恢复审校 / 这篇不发 / 我已自行处理 / 补充发布链接 | `POST /api/tasks/{id}/review` | `action`、`source_text_sha256`、`review_revision`、`reason`、`wake_at`、`handoff_url` | `review_items.jsonl` | "刷新状态，保留填写内容" |
| 编辑分类 → 保存分类 | `PUT /api/tasks/{id}/tags` | `tags[]`、`tags_revision`（64 位 hex）、`source_text_sha256` | `post.json` 的 `tags` | "载入最新分类" |
| 下载并由我处理 / 重新下载资源 | `POST /api/tasks/{id}/export` | `source_text_sha256`、`review_revision`、`handoff_url` | ZIP 响应 **+** 首次转 `handed_off` | 同审校动作 |
| 生成德语标签建议 | `POST /api/hashtags/task/{id}` | `source_text_sha256`、`human_revision`、`review_revision` | 采样账本；建议**不自动写入草稿** | 无（503 时提示"仍可手动编辑"） |
| 翻译这篇（第三方作者） | `POST /api/initial-translation/task/{id}` | `consent:true`、`source_fingerprint`、`source_text_sha256`、`human_revision`、`review_revision` | 付费任务队列，返回 **202** | 409 时自动重拉详情 |
| 生成文案候选 / 生成图片 | `POST /api/refinements/task/{id}` | `kind ∈ {text,image}`、`instruction`、`media_index`、三个版本字段 | 付费任务队列，返回 **202** | "刷新任务状态" |
| 核对并恢复本地状态 | `POST /api/content-jobs/{job_id}/recover` | `expected_updated_at` | 任务状态收敛，**不重新调模型** | — |
| 通过并创建排期 | `POST /api/tasks/{id}/approve` | `scheduled_at`（柏林本地无时区字符串）、`source_text_sha256`、`human_revision`、`review_revision`、`content_fingerprint` | 真实排期提交 | 409 带 `payload.suggestions[]`，渲染成可点的备选时刻 |
| 核对并补齐本地回执 | `POST /api/tasks/{id}/publication/reconcile` | 空对象 | 本地回执/镜像/通知补齐，**不重提** | — |
| 刷新月历 | `POST /api/calendar/refresh` | 空对象 | 持发布锁，驱动真实浏览器读后台 | 409/502 时保留上次缓存并显示原因 |
| 保存设置 | `PUT /api/settings` | 严格 `{values, version}` | `config.toml`（保留注释与换行） | 无（提示后需手动"重新读取"） |
| 登记已送达 / 核对未送达后恢复 | `POST /api/runtime/notifications/{delivery_id}/resolve` | `action ∈ {delivered,not_delivered}`、`version`、`message_id` | 飞书 outbox 状态收敛 | — |
| 核对后关闭中断批次 | `POST /api/runtime/processing/recover` | `batch_id`、`version`、`outputs_reviewed:true` | 监控账本 CAS 恢复 | — |

### 4.3 乐观并发

所有写入都带**页面读到的源文哈希 + 对应 revision**，服务端不匹配就 409（`web/DESIGN.md §7`）。前端的一致做法：

```
catch (exc) { error = exc.message; conflict = exc.status === 409 }
```

`api.js:11-27` 的 `fetchResponse` 把 `body.detail` 提成 `Error.message`，并把整个 JSON 挂在 `error.payload`、状态码挂在 `error.status`。两处依赖 `payload`：排期冲突读 `payload.suggestions`，月历读 `payload.cards`（`App.vue:96` —— 失败时仍然用返回体里的卡片渲染）。

`ApprovalPanel` 的成功判据比 HTTP 状态更严：`if (!result.ok || result.status !== 'scheduled') throw new Error(result.message || '排期尚未确认，请核对回执')`（`ApprovalPanel.vue:52`）。200 不等于排期成功。

### 4.4 详情载荷结构（实测，真实 FB 帖）

顶层 25 个键。前端消费情况逐一标注：

```
id                         ✔ 用于所有子请求的路径
read_only                  ✔ 冻结账号 → 隐藏所有加工/发布入口
publication                ✔ ApprovalPanel 显示 attempt_id
delivery                   ✔ ApprovalPanel 显示 message
platform                   ✔ 吸顶条、元信息、HashtagEditor、LinkEditor 分支
status                     ✔ 见 §3.3
review                     ✔ status/revision/wake_at/reason/handoff_url/snooze_default_days
tags, tags_revision        ✔ TagEditor
localization               ✔ 16 个键，编辑区的全部数据源
localization_validation    ✔ issues[] 与 char_count
body_highlights            ✔ 双栏红色标记
body_risks                 ✔ 双栏黄色标记
risk_scan                  ✔ RiskPanel 全部字段
text                       ✔ en / de_machine / de_human / human_revision /
                             source_text_sha256 / stale / machine_current /
                             machine_prompt_version / current_prompt_version
highlights                 ✘ 未消费（全文版，含标签与链接）
risks                      ✘ 未消费
images                     ✔ index / original_url / de_url / de_present / metrics
schedule                   ✔ {at, channel} 或 null
meta                       ✔ 12 个键，其中 snapshot_id / source_fingerprint 未消费
trail                      ✔ MetaPanel 操作记录
```

`localization` 的 16 个键：`platform`、`body_de`、`source_body`、`source_tags[]`、`protected_tags[]`、`tags[]`、`hashtags_confirmed`、`links[]`、`ig_cta`、`ig_bio_url`、`cta_presets[]`、`revision`、`source_stale`、`record_stale`、`has_record`、`source_text_sha256`。其中 `record_stale` 与 `has_record` 前端未消费。

`links[]` 每项：`source_url`、`target_url`、`mapped_url`、`confirmed`、`origin ∈ {mapping, manual, missing}`。

### 4.5 月历载荷

`GET /api/calendar` 18 个键。前端用到：`status`、`cached_at`、`stale`、`error`、`cards[]`、`coverage.matches_current_month`、`refresh_available`、`refresh_unavailable_reason`、`month_ui`、`business_timezone`、`display_start`、`display_end_exclusive`。未用到：`refresh_status`、`age_seconds`、`bounds`、`gap_minutes`、`advisory_only`、`ui_timezone`、`coverage.visible_start/visible_end/channels_complete`。

卡片每项用到：`at`、`at_business`、`channels[]`、`card_sha256`、`delivery ∈ {published, scheduled, 其它}`、`rendered`。

`CalendarPanel.grid` 的网格由 `display_start`/`display_end_exclusive` 算日期标签，卡片按 `at_business.slice(0,10)` 落格；跨月的格子加 `edge` 标记显示"跨月时差"。**周起始固定周一**（`weekdays` 数组硬编码）。

---

## 5. 详情页的编辑与校验机制

### 5.1 这一步谁在做什么

她点"编辑德语"，德语那一栏变成可输入。她改一个金额，250 毫秒后那个数字下面出现一条红线，右下角字符数跟着变。她不理这条红线继续保存——**系统允许**，因为原文可能本来就写错了。

### 5.2 编辑态的四个不变量

1. **显式进入**（`startEdit()`）。深拷贝 `detail.localization` 到 `localizationDraft`，`draft = localization.body_de`，`liveMarks = marks`，`active = -1`。
2. **实时校验只算不拦**。`watch([draft, localizationDraft], …, {deep:true})` 防抖 250ms 调 `/check`；用自增 `checkSequence` 丢弃过期响应；失败静默（`catch { }`）不影响编辑。等待期间字符计数前面显示"约"。
3. **风险标记不随德语变**。`liveMarks = buildMarks(result.highlights, detail.value.body_risks)` —— 风险扫的是英文原文，改德语不会让它变（`TaskDetail.vue:158`）。
4. **保存成功后行内打补丁，不重拉列表**。`emit('changed', detail)` → `App.patchRow()` 只更新 `status`、`review`、`tags`、`schedule`、`source_text_sha256`，并按 `de_human || de_machine` 重算 90 字摘要（`App.vue:109-117`）。

### 5.3 标记模型

`marks.js` 把后端的 `highlights` 与 `risks` 合成一个有序表：

| 类型 | 来源 | 语义 | 画法 |
|---|---|---|---|
| `error` | `highlights[].severity === 'error'` | 这里错了 | 红底 + 红下划线 |
| `warn` | `highlights[].severity === 'warn'` | 这处数字要人确认 | 仅浅红下划线 |
| `risk` | `risks[]` | 翻起来容易翻坏 | 黄底 + 黄下划线 |

三条必须保留的实现细节：

1. **下标是码点，不是 UTF-16 码元**。后端给的是 Python 字符下标，正文里 🚀🐱💛 这类补充平面表情在 JS 里算 2。`marks.js:62` 统一先 `Array.from()` 拆码点再切。实测 1498 字符那篇整体偏了 4 个字符。
2. **重叠时 error > risk > warn**（`marks.js:14` 的 `PRIORITY`）。同一个 `$219.99` 会同时被 money(error) 与 money_review(warn) 命中，两个 `<mark>` 套不起来，必须选一个。
3. **排序按英文原文位置**；只有德语侧有位置的（原文没有、译文里凭空多出来的金额）排在最后，不能丢掉。

`charLength()` 给界面上所有字符数用，和后端 Python 数出来的是同一个数。

**改哪里**：`web/ui/src/marks.js` 整个文件的行为必须 1:1 移植，含上面三条。

### 5.4 双栏对照

`TextCompare.vue` 的四条决定：

1. **两栏不做滚动同步**（德语更长，同步会逐渐错位）；点跳转时两边一起 `scrollIntoView({block:'center'})`。
2. **绝不用 `behavior:'smooth'`**——`TextCompare.vue:36-38` 记录了实测：内嵌 Chromium 里它静默不动，不报错也不滚，而"下一处"是这页唯一不能失灵的东西。
3. 编辑态用**透明 textarea 压在等排版镜像层上**，两层的字体、行高、内边距、换行规则必须逐像素一致。镜像层 `color: transparent` 只画底色。
4. 只读态 `.body { max-height: 52vh }`，编辑态 `.editing { height: 52vh; min-height: 320px }`——因为镜像层与 textarea 都是绝对定位，盒子没有内在高度。

`insertAtCursor(text)` 通过 `defineExpose` 暴露给父组件，`LinkEditor` 的"插入正文"按 `{{linkN}}` 调它。按钮上有 `@mousedown.prevent` 防止失焦丢光标。

### 5.5 图片核对

`ImageCompare.vue` 维护 `seen: Set`（初始 `{0}`），`watch(current)` 时加入。`unseen = images.length - seen.size` 通过 `@progress` 上抛，详情页顶部显示"还有 N 张图没查看"。

**这条提示从不禁用"通过"**（`TaskDetail.vue:326-329` 明确写了"提示不拦，硬拦人工是这个项目的反模式"）。切换 `taskId` 时 `current` 归零、`seen` 重置为 `{0}`。

缺德语图的那几张在缩略图条上有一条 3px 红条（`.miss`），不用逐张点过去才发现。

---

## 6. 列表行为

### 6.1 这一步谁在做什么

她在列表上做的唯一判断是"下一篇打开哪个"。现在支持的筛选是：三个状态页签、一个"只看有硬闸告警的"开关、分类下拉、月份下拉。

### 6.2 筛选与排序

全部在客户端。`TaskList.vue:24-29`：

```
groupOf(task) === tab
  && (!onlyAlerts || task.hard_alerts.length > 0)
  && (!selectedMonth || task.month === selectedMonth)
  && (!selectedTag || (selectedTag === '__untagged__' ? !tags.length : tags.includes(selectedTag)))
```

页签计数、分类选项、月份选项都由当前 `props.tasks` 全量算出（`tabs`/`tags`/`months` 三个 `computed`）。**服务端也返回 `summary.by_status` 与 `summary.tags`，前端不用**——这意味着一旦列表改成服务端分页，页签计数会立刻错。

排序由服务端定（`reader.list_tasks` 的 `sort_key`）：有 `schedule.at` 的按时刻升序在前，没有的按 id 排在后。前端不再排。

"只看有硬闸告警"这个筛选器在 `alertCount === 0` 时**整个不渲染**，改成一行绿色"没有硬闸告警"（`TaskList.vue:43-55`，注释写的是"一个永远是 0 的筛选器只是噪声"）。

### 6.3 筛选状态不持久（实测）

`tab`、`onlyAlerts`、`selectedTag`、`selectedMonth` 四个都是 `TaskList` 内部的 `ref`。`App.vue:163` 用 `v-else-if="!state.openId"` 渲染 `TaskList`——打开详情时组件卸载，返回时重新挂载，四个值全部回到初始。

2026-09-13 在隔离夹具上实测：

| 步骤 | 状态页签 | 月份 | 可见行数 |
|---|---|---|---|
| 初始 | 待处理（17） | 全部月份 | 17 |
| 切到"已处理" + 选 2026-09 | 已处理（2） | 2026-09 | 2 |
| 打开一篇详情 | — | — | — |
| 按返回列表 | **待处理（17）** | **全部月份** | **17** |

URL 全程没有携带筛选（`location.search` 为空串）。她每处理一篇就要重新筛一次。

`HistoryPanel` 的 `filters` 与 `page` 同理：`reactive` 局部状态，返回历史后回到第 1 页、全部平台/月份/分类。

### 6.4 行内动作

`TaskList` 给每行渲染 `<ReviewActions :detail="task" compact>`。`compact` 模式只出"稍后再审"（或"恢复审校"）与"这篇不发"，**隐藏**"下载并由我处理"与"我已自行处理"。

`ReviewActions.context()` 在 compact 模式下要兼容两种载荷形状：列表行没有 `text` 对象，所以取 `detail.text?.source_text_sha256 || detail.source_text_sha256`（`ReviewActions.vue:28`）。重构时这个兼容必须保留，或者统一两种载荷。

"查看"按钮的文案会变：命中 `unknown_collaborator` 告警时显示"查看并翻译"（`TaskList.vue:138`）。这条依赖 `task.alerts`——但列表载荷里字段名是 `hard_alerts`，`task.alerts` 恒为 `undefined`，所以**这个分支当前永不生效**。记录为现状缺陷，见 [UI_AUDIT.md](UI_AUDIT.md) P2-3。

### 6.5 行的视觉分级

四个 class，判据都在 `TaskList.vue:75-80`：

| class | 判据 | 表现 |
|---|---|---|
| `alert` | `hard_alerts.length > 0` | 左侧 3px 红边 |
| `quiet` | 无硬闸、无风险、无 `author_flag` | 不加任何装饰（"干净本身就是信号"） |
| `done` | `status ∈ {approved, scheduled, skipped, handed_off}` | 整行灰底 |
| `waiting` | `status === 'not_ready'` | 整行灰底 + 摘要斜体灰字 |

`done` 与 `waiting` 用**同一个灰底**。"已经处理完不用管"和"还没准备好不能管"在视觉上不可区分。

状态文字只在 `status !== 'pending_review'` 时出现在事实行里（`TaskList.vue:125`）。

---

## 7. 时区处理

这是整个前端最不能出错的一块，四种时刻各有各的算法：

| 数据 | 时区 | 实现 | 为什么 |
|---|---|---|---|
| `schedule.at` | 柏林 | **正则手取字符串里的年月日时分**，不经 `Date` 渲染（`format.js:9-16`） | 机器在中国，`toLocaleString` 会把 10:00 柏林显示成 16:00，夏令时切换日尤其错 |
| `trail[].at`、`review.wake_at` | 上海 | `Intl.DateTimeFormat('zh-CN', {timeZone:'Asia/Shanghai', hourCycle:'h23'})` | 操作记录是上海人的时刻 |
| 排期输入 | 柏林 | `<input type="datetime-local">` 无时区字符串，服务端 `approval.berlin_time()` 解释为 `Europe/Berlin`，并**拒绝** DST 不存在或出现两次的时刻 | 运营输入柏林当地时间 |
| `approval-options.earliest/latest` | 柏林 | `ApprovalPanel.berlinInput()` 用 `Intl` 的 `formatToParts` 换算成 `datetime-local` 字符串 | 给输入框的 `min`/`max` |
| 挂起的 `wake_at` | 上海 | 客户端拼 `` `${form.wakeAt}:00+08:00` ``（`ReviewActions.vue:46`） | 挂起按上海工作日算 |
| 月历卡片时刻 | 后端给的 `business_timezone` | `Intl.DateTimeFormat` 带 `timeZone: zone` | 服务端已用 IANA 时区换算过 `at_business` |
| 运行状态时刻 | 上海 | `Intl` `dateStyle:'short', timeStyle:'medium'` | — |

`formatSchedule` 的星期是用 `Date.UTC(y, mo-1, d)` 的 `getUTCDay()` 算的，避开本地时区。`WEEKDAYS` 数组从周日起。

**改哪里**：`web/ui/src/format.js` 整个文件、`ApprovalPanel.vue:14-23` 的 `berlinInput`、`ReviewActions.vue:46`、`CalendarPanel.vue:10-12,31-34`、`RuntimePanel.vue:12-13`。

---

## 8. 危险与不可逆动作

界面上能按到的动作里，下面这些有外部后果。重构时它们的确认流程不能简化。

| 动作 | 后果 | 现有保护 |
|---|---|---|
| 通过并创建排期 | **在 Business Suite 建真实定时任务**，外部不可撤销 | `content_fingerprint` 必须与 `approval-options` 给的一致；三个 revision；90 分钟同渠道冲突返回 409 + 建议时刻；`result.ok && result.status==='scheduled'` 才算成功 |
| 刷新月历 | 持发布锁，驱动 9223 号真实 Chrome 读后台 | `refresh_available` 为假时按钮 disabled；busy 返回 409 |
| 这篇不发 | 终态，系统停止后续处理 | 模态框 + **理由必填**（确认按钮 disabled 直到有非空理由） |
| 我已自行处理 / 下载并由我处理 | 终态；export 首次成功即转 `handed_off` | 模态框；export 另要求资源包完整准备成功才记录接管 |
| 翻译这篇（第三方作者） | **真实付费模型调用**，受日/月预算约束 | 复选框 consent + `source_fingerprint` |
| 生成文案候选 / 生成图片 | 真实付费调用；每张图最多三次 | 按钮上直接写预估金额；`remaining` 为 0 时 disabled |
| 核对未送达后恢复 | **重新发送飞书消息** | `window.confirm` + `version` CAS |
| 登记已送达 | 收敛投递状态 | `window.prompt` 要消息 ID，取消即中止 |
| 核对后关闭中断批次 | 关闭未完成的付费批次 | `window.confirm`，有 `paid_request_ids` 时换成更长的核账提示 |
| 保存设置 | 写 `config.toml` | `version` CAS；保存按钮 `!dirty` 时 disabled |

⛔ `POST /api/tasks/{id}/approve` 与 `POST /api/calendar/refresh` 是唯一两个会**动到平台侧**的前端入口。重构期间的任何自动化测试都不许打到它们的真实实现；现有 `tests/browser_fixture.py` 的 ASGI 包装层（只放行 `PUT /api/settings`、`PUT …/localization`、`POST …/check`，其余非 GET 返回 503）是既有做法，继续用它。

现有三处原生浏览器对话框必须保留同等确认强度：`window.confirm`（离开守卫 ×2、采用候选覆盖草稿、飞书未送达、关闭批次）、`window.prompt`（飞书消息 ID）。

---

## 9. 加载、空、错误、禁用状态

| 位置 | 加载 | 空 | 错误 |
|---|---|---|---|
| 列表 | `正在读归档…`（纯文字） | `当前筛选下没有帖子。` | 顶部红条 + "重试"按钮 |
| 详情 | `正在读这一篇…` | — | 红条；409+编辑中 → "载入最新内容并保留草稿"；无 detail → "重试" |
| 历史 | `正在查阅归档…` | `当前筛选下没有归档帖子。` | 行内红字 + "重试" |
| 月历 | `正在读取月历信息…` / 按钮变"正在读取…" | 三种不同空态：未读过、缓存没覆盖本月、本月无排期 | 黄框 `calendar-warning` |
| 设置 | `正在读取设置…` / 按钮变"正在保存…" | — | 红字 |
| 运行状态 | 按钮变"正在读取…"，`aria-busy` | — | `.warning` 黄字 |
| 排期区 | `正在核对本机记录、内容与发布条件…` | — | 红字 + 可点的备选时刻 |
| 优化区 | 按钮变"正在受理…"/"正在生成…" | — | 红字 + "刷新任务状态" |

全部是文字，**没有一处骨架屏或 spinner**。

禁用逻辑最密的是 `ApprovalPanel.disabled`（`ApprovalPanel.vue:28`）：`editing || loading || submitting || !eligible || !options?.available || !when`，六个条件或起来，界面上只有一个灰按钮，不说是哪一条。

---

## 10. 无障碍现状

做到的：全局 `:focus-visible` 轮廓（并注明"任何地方都不许把它去掉而不给替代"）、`prefers-reduced-motion` 全局降级、页签 `aria-pressed`、导航 `aria-label="工作区"`、错误条 `role="alert"`、状态条 `role="status"`、模态框 `role="dialog" aria-modal="true"`、图片 `alt`、缩略图 `aria-label="第 N 张（看过）"`、月历 `role="list"/"listitem"`、`aria-busy`。

没做到的（实测）：

1. **模态框不接管焦点**。打开"这篇不发"后 `document.activeElement` 仍是触发按钮（被遮罩盖住）。`<textarea autofocus>` 对动态插入的节点在多数浏览器不生效。
2. **Esc 关不掉模态框**。`@keydown.esc="close"` 绑在 `<section>` 上，焦点不在对话框内就收不到；同时 `TaskDetail.onKey` 见到 `[role="dialog"]` 就早退，所以 Esc 既不关框也不返回。
3. 无焦点陷阱，Tab 会走到遮罩后面的页面。
4. 标记地图 `aria-hidden="true"` + `tabindex="-1"`，键盘与读屏完全取不到。
5. 详情页没有 `<h1>`；`App.vue` 的 `<h1>审校台</h1>` 是全局品牌，页面级标题缺失。
6. 章节标题层级混乱：同为区块标题，`h3` 出现 12px / 13px / 14px 三种字号（实测 11 个标题）。

**改哪里**：`ReviewActions.vue:86-115`（模态框）、`TaskDetail.vue:337-346`（标记地图）、`styles.css:74-78`（焦点环，保留）。

---

## 11. 明确不确定的事

下面每一条我都没有替你下结论。要动它们之前先看 [OPEN_QUESTIONS.md](OPEN_QUESTIONS.md)。

1. **`web/api/fixtures/risks.json` 没有任何代码引用它。** `web/DESIGN.md §8` 规定它"只能在明显的演示模式使用"，但界面上不存在演示模式开关。是待建能力还是残留，我不知道。
2. **`web/api/fake_writer.py` 不在请求路径**（`web/README.md` 与 `web/DESIGN.md §1` 都写了），但 `tests/tests_web_review.py:23` 仍 import 它。它不是死代码，删不删是维护决定，不在本次审计范围。
3. **`POST /api/tasks/{task_id:path}/skip` 是活端点，前端从不调用。** 前端一律走 `/review` 带 `action=skipped`。不能因为 UI 没用就当它不存在——可能有 CLI 或集成消费者。
4. **`detail.highlights` 与 `detail.risks`（顶层）服务端在算、前端不用。** 它们针对全文（含标签与链接），`body_highlights`/`body_risks` 针对正文分区。两份并存的意图不明。
5. **`summary.by_status` 服务端在算、前端不用**，页签计数是客户端重算的。改服务端分页会立刻打破计数。
6. **历史页大小 30 与后端默认 50 不一致**：前端硬传 `limit: 30`，`web/DESIGN.md §5` 写的是"历史默认 50"。新 UI 该用哪个是产品决定。
7. **90 天窗口不可从界面调整**（`reader.DEFAULT_DAYS = 90`），列表顶部写死"近 90 天图文帖"。是否需要日期范围筛选未定。
8. **真实数据当前 0 篇 `pending_review`、24 篇 `not_ready`**，而 `not_ready` 被分到"待处理"页签。它们打不开审校动作（`ApprovalPanel.eligible` 为假）。`not_ready` 该不该出现在"待处理"里是业务口径问题。
9. **真实数据 26 篇 `risk_scan.status` 全是 `not_scanned`。** 风险面板在默认视图里是否该存在，取决于扫描是否应该在运营打开之前跑完。
10. **`index.stale` 两处文案不同**（列表"展示索引暂未更新，当前按归档文件读取。"，历史"索引暂未更新，当前结果来自本地归档文件。"），同一个条件两种说法。
11. **`TaskList` 里 `task.alerts` 恒为 `undefined`**（载荷字段叫 `hard_alerts`），所以"查看并翻译"的文案分支从未生效。这是缺陷，但修不修属于业务行为变更，不在审计里顺手改。

---

## 12. 回归清单（迁移后逐条验）

这份清单是 [REACT_MIGRATION_PLAN.md](REACT_MIGRATION_PLAN.md) 第 9 节的输入，也是 Playwright 基线的用例来源。

| # | 必须仍然成立 | 验法 |
|---|---|---|
| B1 | `/`、`?task=`、`?view=` 四种 URL 直接打开都渲染正确界面 | 逐个 goto |
| B2 | 浏览器前进/后退在五个工作区与详情之间正确还原 | `goBack`/`goForward` |
| B3 | 从历史打开详情，返回回到历史而非待审队列 | 断言列表标题 |
| B4 | 有未保存草稿时，应用内导航、前进后退、关标签页三处都拦 | 三条路径各一次 |
| B5 | 七态 + `not_ready` 的分组、可编辑、动作可见、排期可用四套判据不变 | 每态一条夹具 |
| B6 | 所有写入请求体的字段名与取值来源不变 | 网络断言（按 §4.2 逐字段） |
| B7 | 任一 revision 过期 → 409 → 对应恢复入口出现且能保留已填内容 | 夹具造 409 |
| B8 | `/check` 永不阻止保存 | 改金额后保存成功 |
| B9 | 码点下标标记定位正确（含补充平面表情） | 造一篇带 🚀 的正文，断言标记落在正确字符上 |
| B10 | 标记重叠时 error 压过 risk 压过 warn | 造一处同时命中 money 与 money_review |
| B11 | "下一处"能跳且不用 smooth 滚动 | 断言 `scrollIntoView` 后元素在视口内 |
| B12 | 柏林时刻不被本地时区改写；DST 不存在/重复的时刻被拒 | 造 3 月末与 10 月末两个时刻 |
| B13 | 挂起 `wake_at` 以 `+08:00` 发出 | 网络断言 |
| B14 | "这篇不发"理由必填，空理由时确认按钮 disabled | 断言 disabled |
| B15 | 图片"还有 N 张没看"提示存在且**不禁用通过** | 断言按钮 enabled |
| B16 | 缺德语图时回退原图并明示 `de_present: false` | 断言告警可见 |
| B17 | 冻结账号 `read_only: true` 详情无任何加工/发布入口 | 断言按钮不存在 |
| B18 | 排期 200 但 `ok !== true` 时视为失败并提示核对回执 | 夹具造该响应 |
| B19 | 历史服务端分页、总数、三个筛选、90 天外详情可读 | 已有 `tests/tests_history.py` 覆盖后端，前端补断言 |
| B20 | 设置只开放两个字段，CAS 冲突有提示 | 夹具造版本冲突 |

2026-09-13 决策收口后新增四条（[DECISION_LOG.md](DECISION_LOG.md)）。前三条是新行为，不是"保持现状"，所以单列：

| # | 必须成立 | 归属 Stage | 验法 |
|---|---|---|---|
| **B21** | 第三方作者（`unknown_collaborator` 硬闸）的帖子在队列里明示需要先授权/初翻；行内文案不再因 `task.alerts` 字段名错误而永不渲染（[DECISION_LOG.md D13a](DECISION_LOG.md)） | C | 夹具造一篇第三方作者帖，断言队列行上的 `ProblemIndicator` 与文案 |
| **B22** | 四个队列页签的分桶正确，且「已处理」里的行仍显示各自的真实 status（D1） | C | 夹具造八种状态各一篇，断言分桶与行内 `Tag` |
| **B23** | 详情页刷新后仍知道「第 n / N 篇」；上一篇/下一篇按进入时的筛选走；返回列表恢复原筛选与页码（[DECISION_LOG.md §2.2](DECISION_LOG.md)） | C+D | 设筛选 → 进详情 → `reload()` → 断言 n/N 与相邻项 → 返回 → 断言筛选与页码 |
| **B24** | 历史 `limit` 进 URL，默认 50，可选 20/50/100（D2） | E′ | 改页大小后 `reload()`，断言仍是该页大小 |

**Stage A 的覆盖范围**：B21 的根因（字段名）由类型层与契约层守住——`ReviewListItem` 不存在 `alerts` 键，任何 `.alerts` 访问在 `tsc` 阶段失败；shape 测试断言真实载荷有 `hard_alerts`、没有 `alerts`。浏览器层面的断言等 Stage C 有队列界面时补。B22–B24 需要界面，全部归 Stage C 及之后。

---

**这份基线到此为止。** 它不包含任何"应该改成什么"的结论——那在后面三份文件里。
