# 审校台接口契约

业务决定见 [FUNCTIONALITY.md](../docs/FUNCTIONALITY.md)，实现缺口与验收状态见 [REQUIREMENTS.md](../docs/REQUIREMENTS.md)。本文件只定义接口边界；启动和验证见 [README.md](README.md)。

## 模块与数据

`web/` 调用 `core/`、`pipeline/`、`publish/`，业务层不反向依赖 Web。HTTP 层负责校验、错误映射和响应组装，业务规则复用已有入口。写入测试使用临时 archive/state。

| 数据 | 来源与约束 |
|---|---|
| 原文与元信息 | `post.json`，操作前核对来源哈希 |
| 机器/人工德文 | `translated.jsonl` / `translated_human.jsonl`，人工优先 |
| 正文、标签、链接 | `localization.jsonl`，追加版本 |
| 德语图片 | `media_de/`、`images_de.jsonl`，人工图优先 |
| 审校状态 | `review_items.jsonl`，追加事件，actor 为 null |
| 排期回执 | `published.jsonl`，以完整远端回读为准 |
| 月历 | `planner_cache.json`，包含缓存时间、覆盖范围和过期状态 |

SQLite 仅作查询索引，失配时回退来源或重建；写入始终核对业务文件。坏账本明确报错，不返回空列表。FB、IG 任务独立，冻结账号只读。

## 请求与版本

- task ID 为 `<account>/<post_id>`，前端逐段编码，保留斜杠。
- 修改请求携带来源哈希和对应资源 revision；来源、人工稿、本地化与审校版本不能混用。
- `source_text_sha256` 使用统一规范化摘要；风险位置另绑定原始 `scan_text_sha256`，冻结正文和媒体使用精确字节指纹。
- 409 表示版本、锁、许可或能力冲突；404 表示不存在。错误保留结构化 payload，避免丢失建议时刻或缓存卡片；不泄露凭据和敏感本机路径。
- 内容写入持账号锁；批准、月历刷新和发布共用发布锁。图片响应使用 `Cache-Control: no-cache`。
- API 与缺失静态资源不做 SPA 回落；HTML 业务路径支持直接访问和刷新，保留既有查询链接。

## 读取接口

| 接口 | 契约 |
|---|---|
| `GET /api/tasks` | 当前任务及服务端分组计数；查询不触发模型或发布。两个 scope 的行都只在确有第 0 张图时给 `thumbnail_url`，无图给空串，`image_count` 同样只数 image 媒体 |
| `GET /api/tasks?scope=history` | page、limit、platform、month、tag、status 筛选；limit 为 1–100，默认 50；pagination.total 为全部匹配数，range.days 为 null |
| `GET /api/tasks/{id}` | 人工优先内容、来源指纹、子资源版本、风险、模型任务与发布状态；按 ID 查询不受 90 天限制 |
| `GET /api/tasks/{id}/image/{index}` | 原图或德语图，缺德语图须显式标识回退 |
| `GET /api/tasks/{id}/approval-options` | `lockable` 表示内容可冻结，`available` 表示还能算出可选时间；`preview` 在已冻结时给出将提交的正文、图片和目标。历史录证缺失不能代替内容原因 |
| `GET /api/calendar` | 月历缓存、业务/UI/受众时区、完整性与 stale；`local` 为本地图层，`local_error` 表示本地账本读不出来 |
| `GET /api/publish-operations/{id}` | 一次提交的当前步骤与终态；进程消失的记录读回来是 uncertain |
| `GET /api/templates/{kind}` | text/image 模板，只读 |
| `GET /api/settings` | version、editable、controlled、controlled_fields、editable_help |
| `GET /api/runtime` | 与 CLI 共用五阶段 snapshot；读取不扫描平台、不调用模型、不发送消息 |

阶段一 `monitoring` 对象包含以下字段。缺少显式访问初始化或状态损坏时展示失败闭合，旧归档缺字段显示未知。

| 字段 | 含义 |
|---|---|
| `status`、`revision`、`reason` | 访问状态、访问恢复使用的版本和暂停/初始化原因 |
| `capture_revision`、`baselines` | 单帖恢复使用的独立版本；各平台基线的启用时刻、回填天数及核对数量 |
| `platforms[platform]` | `paused`、`failures`、`reason`、`next_due_at`，以及滚动 24 小时 `homepage_used/homepage_limit`、`detail_used/detail_limit` |
| `items[]` | 稳定 `key`、`scan_id`、身份、分类、状态、归档入口、原帖链接、两层完整性、实际校验数量与来源总数；未知数值为 null |
| `items[].discovery_wait_seconds`、`items[].capture_seconds` | 来源发布到首次发现、单次抓取开始到结束的秒数；没有有效时间证据时为 null |

飞书 delivery 保留 `capture_keys` 和 `captured_at` 列表，供逐帖核对。单篇卡的投递耗时由对应抓取结束时间与 `sent_at` 计算；未发送或结果未知时不补造送达耗时。`/runtime?capture={key}` 定位单项，`/runtime?scan={scan_id}` 定位本轮结果。

## 写入与恢复接口

| 接口 | 契约 |
|---|---|
| `PUT /api/tasks/{id}/text_de` | 保存人工德文，校验来源与人工稿版本 |
| `PUT /api/tasks/{id}/localization` | 保存完整正文/标签/链接草稿与版本；前端只清理与已提交快照相同的草稿，响应等待期间的新编辑保留并接续新版本 |
| `PUT /api/tasks/{id}/tags` | 独立保存分类，不提交其他区域草稿 |
| `POST /api/tasks/{id}/check` | 只计算；接收 localization、text_de/body_only，返回 caption、caption_length、hashtag_count、warnings、issues。`caption` 是 `localization.render()` 的成品，供审校台复制；前端不另拼一份 |
| `POST /api/refinements/task/{id}` | `kind` 为 `text`、`image` 或 `suggest`。`suggest` 必填本次编辑区的 `body_de`，不需要 `instruction`；任务冻结正文快照，产出只读清单，不保存译文 |
| `GET /api/tasks/{id}` 的 `text_suggestions` | 最近一次建议：`job_id`、`body_de` 快照、源文/正文摘要、`items`、`dropped`、`current`、`generated_at` 与新旧 prompt 版本。`current` 只相对已保存正文；编辑区按源文版本、当前正文与快照逐字符相等、新旧 prompt 版本判断是否允许采用，任意正文改动立即失效 |
| `GET /api/tasks` 的 `summary.by_platform_status` | 两平台各自的状态计数。审校台按平台分了入口，角标要按平台数，且不能由当前页重算 |
| `GET /api/tasks` 的 `summary.by_platform_hard_alerts` | 两平台各自包含硬闸的帖数，和状态计数一样在分页前计算 |
| 任务详情的 `hard_alerts` | 当前源帖的付费前硬闸；人工重新复核或逐篇授权后，详情刷新同步更新列表行及该平台硬闸计数 |
| `POST /api/tasks/{id}/review` | 挂起、恢复、不发或人工接管；理由与 revision 按动作校验 |
| `POST /api/tasks/{id}/export` | 完整生成 ZIP；`text_de.txt` 复用 `localization.render(effective_draft(...))`，包含本地标签与平台链接/CTA；`mode=handoff`（默认）才记录人工接管，`mode=download` 只下载不改状态 |
| `POST /api/tasks/{id}/image/{index}/upload` | JSON + base64 上传人工图替换该张；校验格式/动图/体积，旧文件备份至 `media_de/superseded/`；明确选择追加到 `manual_uploads.jsonl`，**不写 `images_de.jsonl`**；审校记为 `edited`，继续系统排期 |
| `POST /api/image-versions/task/{id}` | 当前账号采用某历史版本为当前版；冻结账号拒绝写入；零模型调用、零费用、不占优化次数 |
| `GET /api/image-versions/task/{id}/preview?media_index=N&out_path=...` | 预览本帖本张有生成记录的版本；不改变当前选择，也可查看上传时备份的旧件 |
| 任务详情的 `publish_operation` | 当前冻结快照的最近提交操作；刷新后恢复轮询，撤销成功后不显示旧的排期成功操作 |
| `POST /api/tasks/{id}/content-lock` | 回传 content_fingerprint，冻结正文与图片字节并关掉编辑入口 |
| `DELETE /api/tasks/{id}/content-lock` | 解除冻结，快照标 discarded 但保留字节 |
| `POST /api/tasks/{id}/approve` | 回传 content_fingerprint 与 scheduled_at；能当场判定的失败同步返回，其余返回 202 与操作编号，浏览器在请求外跑 |
| `POST /api/tasks/{id}/publication/unschedule` | 登记人工已在后台删除；持锁实时读整月核实后才解除防重 |
| `POST /api/tasks/{id}/publication/reconcile` | 按已有回执和冻结版补本地状态、镜像、通知，不重复提交 |
| `POST /api/calendar/refresh` | 持发布锁重新读取远端，失败时保留可用卡片与错误状态 |
| `GET/POST /api/initial-translation/task/{id}` | 能力/初翻；POST 含来源、人工/审校版本与内容级许可 |
| `GET/POST /api/refinements/task/{id}` | 能力/优化；POST 含 kind、instruction、media_index 与来源/版本 |
| `GET /api/initial-translation/jobs/{job_id}`、`GET /api/refinements/jobs/{job_id}` | 新任务返回 202 与唯一 job ID，刷新继续轮询同一任务；text 任务回传 `prompt_version`、`current_prompt_version`、`prompt_current`，旧版/缺失版本禁止采用 |
| `POST /api/content-jobs/{job_id}/recover` | expected_updated_at 校验，先核对费用与产物，不隐含再次调用模型 |
| `POST /api/runtime/processing/recover` | batch_id、version、outputs_reviewed；version 来自 processing.state_revision，未决付费阻止恢复 |
| `POST /api/runtime/capture/recover` | key、version、reason；对指定帖子执行一次受控恢复，沿用详情配额、停机、身份匹配和媒体校验 |
| `POST /api/runtime/monitor/recover` | version、reason、可选 platform；只恢复持久监测状态，不发起平台请求，不清空配额或访问历史 |
| `POST /api/runtime/notifications/{delivery_id}/resolve` | action=delivered/not_delivered、version；已送达需 message_id，但群机器人不返回平台 ID，这里收的是人写的核对说明 |
| `POST /api/hashtags/task/{id}` | 语义候选和三类来源的状态、原值、统计口径与时间 |
| `PUT /api/settings` | 严格接收 values、version，未知字段或过期版本拒绝 |

## 编辑与状态

八个持久状态为 pending_review、edited、content_locked、snoozed、approved、scheduled、skipped、handed_off；not_ready 仅为准备状态。content_locked 表示人已确认内容并冻结、时刻待选，它算在待审口径里；approved 表示提交处理中；scheduled 只表示排期已核验。来源变化保留人工稿，非终态重新审核，已排期内容仅提醒比较。

FB 正文在光标处插入 `{{linkN}}`，后端换成确认过的 target_url，未插入链接追加末尾，已插入的不重复。IG 使用 bio CTA。缺目标、未知编号或损坏 token 阻止通过；金额等可人工解释的差异仅提示。即时检查只应用于对应草稿，字符数按最终完整正文计算。

每张图片最多受理三次人工优化，失败的那次也算；来源、源图或提示词变化使旧候选失效，人工版本保留。费用区分初次处理、优化、风险扫描和不确定金额。`text.stale` 只表示原文变化，提示词版本另看 machine_current 等字段。

图片详情带 `manual`、`replaced_at`、`warnings`；人工图地址随实际字节哈希变化，避免连续上传仍显示旧图。`metrics.changed_pixel_ratio` 忽略小于等于 24 的通道差；`null` 表示未量过，0 表示未检测到明显变化，不证明未翻译。`GET /api/refinements/task/{id}` 附只读 `image_model` 及按媒体下标分组的 `image_versions`，每版含预览 URL、时间、指令、指标、是否当前及不可用原因。

设置仅开放 default_times（1–12 个不重复的业务时区 HH:MM）和 snooze_default_days（1–30 个工作日）。只影响后续预填，不移动既有排期；CAS 改写保留无关值、注释和换行，不安全的格式拒绝改写。2026-09-23 起界面不再提供设置页，这两个接口为维护者通道。

## 风险、标签与运行状态

风险分 pun、ambiguous、us_only，绑定源文和提示词；未扫描、失败、过期、成功无风险及有风险分别呈现。标记按 Python 码点定位，确定性错误与语义风险分开。

标签来源分别保留 Trends 德国同组同批同时间范围指数、IG 全球累计量级、德语同类账号近期频次。跨批 Trends 不混排，全球量级不称德国热度；失败、过期、空名单降级为语义候选，不制造零值。Trends 导出须绑定候选组、DE、起止日及实际 CSV 控件证据，保留原始字节和摘要；停止状态经核对、CAS 与共享锁恢复。

运行状态区分进程存活与业务成功；业务耗时、发帖到就绪时效和晨间就绪率分别统计，缺数为 null/missing。读取不执行恢复；显式恢复请求须带版本及人工核对结果。

原帖详情返回 `source_media_complete`、`media_complete`、`source_media_count` 与 `verified_images`。前两项分别表示来源媒体列表已知完整，以及来源完整且所有静态图片已完成全图解码、实际 SHA 校验与原子落盘；未知不能推成 true。抓取卡主链接按是否已归档选择 history 或 runtime capture 查询，次链接保留源 permalink。

## 排期与通知

月历读取完整可见范围、所有日期/时刻项、手工任务和延迟加载；推荐时段不算帖子。缺覆盖、未知卡片或无明确空态显示 incomplete/unknown。公开状态附观测时间和来源，不按到点推定成功。

同渠道间隔至少 90 分钟，冲突返回建议而不改用户时间；提交前在发布锁内重读占用。最终确认展示人工优先完整文案、标签/链接或 CTA、有序图片、来源版本、唯一目标渠道、业务时刻及许可。冻结 fingerprint 与回执一致，文件后续变化不影响该次提交；内容冻结与时刻绑定分两步，绑定过不同时刻的快照不得改绑。

成功验收要求全文相等、remote_images_verified=true、图片数量及有序 remote_media/source SHA 与冻结清单一致。仅编辑器图片或远端 ID 不足以通过；中断和不确定结果先核对，恢复只补有证据的记录。界面严格以 ok=true 且 status=scheduled 判定排期成功。

通知首次尝试前读取当前人工优先内容，首次尝试后冻结 UUID、接收组、正文和图片。结果不确定先核对；确认未送达后仅向漏收者补有效内容，保留原记录。终态投递按完整关联组件归档并保留去重引用，未决投递不按时间清除。真实联调须由运营打开审校链接。
