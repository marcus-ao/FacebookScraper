# 审校台设计与接口契约

**契约日期：2026-09-12。** 本文件描述当前界面和 API 的业务约束。功能决定以 [../docs/FUNCTIONALITY.md](../docs/FUNCTIONALITY.md) 为准。

## 1. 目的与模块边界

审校台让一名上海运营查看当前待办、修改德语本地化内容、核对图片、挂起/不发/人工接管、查看月历并发起一次经确认的单渠道排期。

`web/` 是模块图的叶子：它可以调用 `core/`、`pipeline/` 和 `publish/` 的入口；这些生产层不得 import `web/`。业务规则只实现一次，HTTP handler 只做请求校验、错误映射和响应组装。

当前写入是真实的：人工文案、标签、本地化和审校状态分别写入真相源。旧 `fake_writer.py` 与 `_fake_state.json` 不在请求路径。真实风险扫描已接入，夹具只供明确演示。主干绑定的是真实 archive/state，打开页面不再等于查看空演示库。

本文记录 API 与必须保持的业务契约，未提供的字段不会冒称存在。实现状态使用五种固定值；业务七态和运行码是另一层含义。逐项验收状态见 [REQUIREMENTS §10](../docs/REQUIREMENTS.md#10-五阶段验收状态)，证据边界见 [HANDOFF](../docs/HANDOFF.md)。

## 2. 用户与布局

目标用户使用中文桌面界面，工作时间是上海 08:00–19:00。列表负责回答“现在需要打开哪篇”；详情负责完成决定。

Facebook 与 Instagram 任务始终分行、分详情、分状态。不能按相似文案或媒体合并。每行显示来源渠道和目标账号。

常态信息保持简洁；下列异常应在列表可见：硬闸、风险、第三方作者、源文已变、缺图、投递/提交失败、Planner 缓存过期。

## 3. 数据来源

| 界面字段 | 来源 | 约束 |
|---|---|---|
| 英文原文/来源元数据 | `post.json` | 每次操作核对来源哈希 |
| 机器德文 | `translated.jsonl` | 只作基线 |
| 人工德文 | `translated_human.jsonl` | 存在时优先，机器不覆盖 |
| 正文/标签/链接分区 | `localization.jsonl` | 追加版本，保存时验证 revision |
| 德语图片 | `media_de/` + `images_de.jsonl` | 人工图优先 |
| 审校状态 | `review_items.jsonl` | 追加式；坏行必须使读取失败 |
| 排期状态 | `published.jsonl` 的成功回读 | 不能由按钮点击或 UI 假状态推导 |
| 月历 | `planner_cache.json` | 派生；显示缓存时间、覆盖和 stale |
| 确定性标记 | 本地规则 | 红色，事实性 |
| 模糊风险 | 真实风险结果真相源 | 黄色，提示性；已接风险扫描，实际模型结果仍待联调 |
| 风险演示 | `fixtures/risks.json` | 只能在明确演示模式出现 |

SQLite 可用于查询加速，但不是业务真相。不可用或与来源版本不符时回到本地真相读取，不能用旧索引完成写入。

## 4. 状态模型

| 状态 | 用户含义 | 可执行动作 |
|---|---|---|
| `pending_review` | 待审 | 编辑、挂起、不发、人工接管、通过 |
| `edited` | 已有人工修改 | 继续编辑、挂起、不发、人工接管、通过 |
| `snoozed` | 暂缓 | 到期/源文变化恢复；可继续编辑但保持挂起 |
| `approved` | 已批准且正在提交 | 禁止内容修改；等待成功或失败闭合 |
| `scheduled` | 已被 Planner 回读确认 | 只读；不等于已经公开 |
| `skipped` | 决定不发 | 终态，必须保存理由 |
| `handed_off` | 资源已交人处理 | 终态，可回填公开链接 |

所有事件保留 `actor` 字段且本轮固定为 `null`。界面不得显示虚构用户名。

源文哈希变化时，非终态任务回到待审并标记 stale，人工文案保留。终态任务不自动改决定；已排期任务只告警。

## 5. 列表与历史

默认列表只显示当前工作：待审、已编辑、到期挂起和提交失败返回项。允许按状态、渠道和分类标签筛选。

历史是独立入口。最近 90 天可以作为日常待办或默认查询范围，**不能成为历史访问上限**。历史列表服务端分页，支持平台、月份、tag、状态筛选，返回总条数、页码和每页数；选择更早月份后仍可查到旧帖，按 ID 查询详情不受 90 天限制。冻结 `.tech` 也可只读查看，但不显示可执行加工/发布操作。查询不触发翻译、图片生成、付费或发布，不裁剪本地归档。

`GET /api/tasks?scope=history&page=1&limit=50&platform=instagram&month=2026-08&tag=M1%20Pro` 是现有列表入口。现有响应包括 `tasks`、`scope: history`、`pagination: {page, limit, total}`、`summary: {total, tags, months}` 与索引新鲜度。`limit` 为 1–100，历史默认 50；`total` 是筛选后全部匹配数，不是本页长度。详情沿用 `GET /api/tasks/{id}`，不需要先加载全部历史。

响应现有 `range: {scope, days, month, platform}`；历史 `days: null` 明确没有 90 天上限，待办返回实际 days。tag/status 仍按请求筛选，不能从本页长度推 total。页末/空结果/组合筛选/跨来源 ID/90 天外详情由历史回归覆盖，实际原归档按每页 20 读取第 1/2 页无重复。SQLite 与 1,067 篇源文件实际一致；查询没有翻译、付费或发布副作用，失配仍明确回退/重建。

列表排序优先可行动性和业务时刻，不能因为风险夹具存在而把演示数据排在真实任务前。

## 6. 详情编辑

详情包含：

1. 英文来源与人工优先德文对照；
2. 正文、hashtags、链接三个独立编辑区；
3. 原图/德语图逐张对照和图片优化入口；
4. 作者、合作方、来源时间、永久链接和内容版本；
5. 红色确定性检查与黄色模糊风险的分开展示；
6. 状态动作、资源下载、Planner 占用和最终确认。

正文保存可以允许人有意修改金额，但要显示确定性告警。结构性约束仍需拒绝，例如删除来源链接行、破坏受保护标签或提交空正文。

FB 链接区的插入动作把 `{{linkN}}` 放到正文当前光标处；后端 render 以对应 target_url 替换，未插入的有效链接追加末尾，已插入的不重复。未知编号、损坏占位符或缺有效目标会形成结构问题并阻止通过，IG 返回不支持并使用 CTA。实时 `/check` 的 body 可带完整 `localization` 草稿及 `text_de`/`body_only`，返回最终 `caption_length`、`hashtag_count`、`warnings`、`issues`；字符数以替换后的完整正文为准。响应对应当前草稿才应用，等待期间 UI 显示“约”。

图片优化每张最多受理三次。任务返回 job ID，刷新后继续轮询同一任务；恢复入口按拥有进程、请求账本、来源版本和 job 版本核对。页面区分仍运行、已中断未发请求、结果已落盘、远端可能计费与需人工核账；恢复动作本身不再次调用模型。源文、源图或提示词变化后旧候选不可应用，人工版保留。费用区区分初次处理、优化、风险扫描和不确定费用，不用优化次数代替金额。

详情 `text.stale` 只表示机器译文绑定的原文哈希是否变化；另有 `text.machine_current`、`machine_prompt_version`、`current_prompt_version` 说明机器结果是否满足当前提示词版本。人工稿仍优先。当前旧 FB 机器版为 5、当前为 6，当前无可接受德语图且 FB 目录未发现 media_de；冻结 `.tech` 有 3 张历史图，不作为 FB 或新 `.global` 素材。页面不能从版本差异推断媒体文件历史，也不自动付费重做。

## 7. API 契约原则

具体字段以 FastAPI 响应为准，下列约束跨版本稳定：

- `GET /api/tasks` 返回当前列表；查询参数不能裁剪底层数据。
- `GET /api/tasks/{id}` 返回人工优先的当前内容、来源哈希、审校 revision 和各子资源 revision。
- 所有修改请求携带页面读取时的来源哈希和对应 revision；过期返回 409。
- 图片响应 `Cache-Control: no-cache`，避免继续展示旧人工图。
- 写入或状态转换持有账号级锁；批准、月历刷新和发布使用发布锁。
- 归档路径、来源版本、审校冲突和付费限制映射为可行动错误，不暴露本机敏感路径或凭据。
- 对损坏真相源返回明确失败；不得返回缺省空列表。

### 当前主要路由

| 方法/路径 | 作用 | 当前状态 |
|---|---|---|
| `GET /api/tasks` | 当前任务列表 | 离线通过 |
| `GET /api/tasks/{id}` | 任务详情 | 离线通过 |
| `GET /api/tasks/{id}/image/{index}` | 原图/德语图 | 离线通过 |
| `PUT /api/tasks/{id}/text_de` | 保存人工文案 | 离线通过 |
| `PUT /api/tasks/{id}/localization` | 保存正文/标签/链接 | 离线通过 |
| `PUT /api/tasks/{id}/tags` | 保存分类标签 | 离线通过 |
| `POST /api/tasks/{id}/review` | 挂起/恢复/不发/接管动作 | 离线通过 |
| `POST /api/tasks/{id}/export` | 生成 ZIP 并接管 | 离线通过 |
| `POST /api/tasks/{id}/check` | 即时确定性检查与完整本地化草稿最终计数，只算不写 | 离线通过 |
| `GET /api/calendar` | 读取 Planner 缓存 | 离线通过 |
| `POST /api/calendar/refresh` | 持锁刷新、缓存/错误契约有离线验证；其生产读取器本次真实 ready 另记于集成报告 | 离线通过 |
| `GET /api/tasks/{id}/approval-options`、`POST /api/tasks/{id}/approve` | 快照/范围与单渠道正文回读已有离线证据，远端 scheduled 图片适配仍缺，完整验收能力闸保持阻塞 | 代码未完成 |

### 新增与扩展的必需接口

| 入口/数据 | 当前接口与目标契约 | 状态和验收 |
|---|---|---|
| 历史 list/detail | `scope=history`、range/page/limit/total 和无限龄详情；冻结行/详情 `read_only:true` | 原数据只读验证真实通过；版本化浏览器另验临时数据 |
| 初次处理 | `GET/POST /api/initial-translation/task/{id}`、`GET /api/initial-translation/jobs/{job_id}`；POST 带来源指纹、源文哈希、人工/审校 revision 与内容级 consent | 离线通过；真实许可/模型另验 |
| 单篇优化 | `GET/POST /api/refinements/task/{id}`、`GET /api/refinements/jobs/{job_id}`；kind、instruction、media_index 与来源/revision | 离线通过；返回 202 和唯一 job，刷新不新建 |
| 模型任务恢复 | `POST /api/content-jobs/{job_id}/recover`，携带 `expected_updated_at`；任务结果显示分类、request ID、是否需人工核账 | 离线通过；后端恢复与浏览器 UI 替身范围分别记录 |
| 整批模型恢复 | `POST /api/runtime/processing/recover`，body 为 batch_id、version、outputs_reviewed；version 来自 processing.state_revision，按 operation_id 关联费用账本 | 离线通过；CAS 失败不修改，未决付费阻止恢复，恢复不 claim 后继或再请求模型 |
| 飞书不确定结果核对 | `POST /api/runtime/notifications/{delivery_id}/resolve`，action=delivered/not_delivered，附 version；已送达还需 message_id | 离线通过；人工核对后收敛本地状态，重试有效原卡保持 UUID/内容 |
| 发布恢复 | `POST /api/tasks/{id}/publication/reconcile`；核对已有 journal/冻结版后幂等补审校、镜像、通知 | 离线通过；不点击平台提交，不把 uncertain 改为成功 |
| 发布与来源快照 | 详情已有 publication、meta.source_text_sha256/source_fingerprint/snapshot_id/fingerprint_error；approval-options 给 content_fingerprint，批准带同值和 scheduled_at。每张源/最终媒体的实际字节与顺序保存在对应快照清单，不能用 URL 代替 | 来源/冻结恢复离线通过；fingerprint_error 时明确无法完整核对，真实远端图数/顺序仍未知 |
| 风险元信息 | 详情已有 `risk_scan`；至少 status、来源/提示词哈希与版本、模型、扫描时间、kind/位置/说明/失败原因、过期原因 | 风险路径离线通过；真实扫描待真实联调 |
| 标签建议与来源 | `POST /api/hashtags/task/{id}`；英文候选组、建议、三类来源状态/原始值/时间、Trends comparison_group/sample_batch/time_range/geo、降级原因 | 离线通过；C7/count 修复已复审，Trends 导出代码已测；两个平台 429 下没有真实采样 |
| 模板只读 | `GET /api/templates/{kind}`，text/image 返回 content、read_only；原说明和版本可核对 | 离线通过；禁止 Web 改模板 |
| 运营设置 | `GET/PUT /api/settings`，GET 返回 version、editable、controlled、controlled_fields、editable_help；PUT 严格 `{values, version}`，未知字段/过期 CAS 拒绝 | 离线通过；说明/隐藏配置保护与临时真实 ASGI 浏览器 CAS 已验证 |
| 五阶段状态 | `GET /api/runtime` 与 `scripts\run_pipeline.bat preflight --json` 共用 snapshot，含 processing.state_revision/paid_request_ids/cost_usd 和 business.timings | 离线通过；浏览器 runtime 用显式夹具验证 UI，不假称真实恢复操作 |

不存在的字段不能以空对象冒充已支持。增加字段保持已有任务入口兼容；语义性破坏须版本化并同时更新前端与回归夹具。404 只表示真实不存在，409 表示版本/锁/许可/能力冲突，损坏真相源显式报错。响应不得暴露本机 `.env`、token、原始凭据或敏感运行路径。

所有 `source_text_sha256` 写入/比较使用统一来源文字摘要，AST 守卫覆盖 web 与生产代码；它不是最终正文的字节摘要。风险另有 raw `scan_text_sha256` 确保高亮偏移不因首尾空白规范改变，source/prompt 任一证据变化仍过期。冻结最终文案、原始证据和媒体字节继续使用精确指纹。

### 设置白名单与运行状态

`editable` **只开放** `default_times`（1–12 个不重复 `HH:MM`，柏林，默认 10:00/17:00）和 `snooze_default_days`（1–30 个上海工作日，默认 3）。修改只影响后续预填/挂起，不移动已有排期。账号、轮询、预算、许可、全局 link/price_map、模板等受控值只读，并展示原配置/模板说明；“配置文件注释未丢”与“页面呈现注释”分别验收。CAS 比较配置版本，保留换行、注释与无关字段；多行等无法安全改写的格式须明确拒绝。

运行 JSON 返回 `observed_at`、`read_only`、旧 `activation`、进程存活/最近 tick、业务上次成功/处理批次、五个 `stages`、外部 heartbeat 与网络证据。阶段中分别表达探测状态、归档/镜像、德语处理、飞书 outbox、发布能力/未确认尝试/Planner。原 `detect_hard_blocked`、缺凭据、镜像 disabled、未知公开状态都应可见。页面和 CLI 只读已有事实，不能以刷新状态为由扫描平台、调用模型、发消息或提交。

`business.timings` 包括处理请求到内容就绪的 P50/P95、样本数、晨间就绪率；新增 `published_to_ready` 按来源引用首次 `post_content_ready` 与 `source_created_at` 统计源发帖至待审时效，包含发现等待，与批次处理耗时分开。只统计新记录的真实时间事实，旧数据不补造；缺数为 null/missing，不是假零延迟。阶段三另返回 tag_sampling 与 trends_export，包括当前 Trends blocked。GET 只读与上述显式 POST 恢复分开：POST 要版本和人工核对结果，不能因打开运行页自动执行。

## 8. 风险与 hashtag 呈现

红色确定性检查和黄色风险提示不可合并。真实扫描在翻译前覆盖 pun/ambiguous/us_only；源文和提示词绑定。`not_scanned`、`failed`、`succeeded` 且零风险分别显示，失败不能渲染为“安全”。来源或提示词变化后过期，保留人工劳动。所有模型阶段使用统一来源许可与日/月付费账本。

`fixtures/risks.json` 不满足上述契约，只能在明显的演示模式使用。真实页面不能默认加载夹具。

hashtag 建议须分别显示三类来源：Google Trends 公开 CSV 的同英文标签候选组、`geo=DE`、同时间范围/同批相对比较；IG 全球累计 media_count；德语同类账号近 14 天用词频次、每周采集。Trends 跨组归一化分数不能横排，IG 量级不能称德国近期热度。空同类名单明确跳过，失败/过期/缺失分别降级并显示采样时间；没有有效信号可人工选择，不用假零值伪装，推荐不阻塞保存/发布。

当前 IG 与 Trends 都有已持久记录的 429 停机。Trends 公开导出要求请求专属的被动 CSV 控件 proof，绑定候选组/DE/统一起止日；不能把登录按钮或任意链接当导出控件。真实 CSV 的 BOM/CRLF 字节与 context 哈希必须保持，未知结构降级。停止状态只读可查询，人工核对恢复还需版本 CAS 与共享锁，不能删除状态后重试。恢复/导出 CLI 见人工指南；目前无真实导出。

## 9. 月历与时刻

运营输入柏林时刻。API 同时返回业务时区、Business Suite UI 时区、缓存时间、可见起止日、渠道完整性和 stale 状态。

同渠道前后 90 分钟内有任何卡片即冲突；另一个渠道不冲突。人工选择冲突时返回 409 和建议候选，不自动顺延。

2026-09-01 曾观察日期选择器不能跨当时可见月份，不能永久写死“同月”。范围须跟可靠读取更新，日期选择器及新 scheduled 形态仍按单独证据验收。月历读完全部日期格/时刻条目、手工项和延迟加载，活跃时间建议不计帖子。本轮生产读取在 `2026-09-13T05:01:33Z` ready：35 格 8/30–10/3，4 条公开帖、2 个正向 tooltip 推荐时段，含 3 个 IG remote ID；当前观察完成不保证未来未知卡片形态也完整。范围/渠道不全或没有明确空态仍显示 unknown/incomplete。

卡片日期/时刻/渠道/远端 ID、缓存截至时间、可见起止日、完整性、stale 和刷新 busy 需可核对。`scheduled` 是回读确认排期；实际公开状态另附观测时间/来源/公开链接，没有证据写 `unknown`，不从“已经到点”推定已发，也不点 Publish now 验证。

## 10. 最终确认与发布

最终确认对话框必须从不可变发布快照读取并展示：

- 来源版本和当前人工优先德语文案；
- hashtags、链接或 IG bio 话术；
- 每张最终图片；
- 唯一渠道和目标账号；
- 柏林时刻、UI 换算和 90 分钟冲突结果；
- 预算/许可状态。

用户确认的 fingerprint 必须与提交 receipt 一致，快照含全部图片实际字节、数量、顺序及来源版本。确认后当前文件变化不能改变这次提交。审校台、CLI、人工结转共享快照/状态投影；已有定向恢复测试，仍需在本轮真实案例里验证冻结与回读一致。批准中断/回执不确定只补有证据的本地状态、镜像和通知，不重提。

2026-09-01 probe 未记录单渠道勾选。本轮已有单渠道控件/显示名和真实月份读取，生产上层由 month_inventory/month_readback 保留完整日期格、精确全文、唯一渠道/remote ID 因果和相同业务资产；双时间证据冲突则失败，相关 P1/P2 已复审关闭。远端 scheduled 详情图片适配器尚未实现，须受控排期后被动取得真实控件再补齐。`capabilities.acceptance` 同时要求 `full_caption_equal=true`、`remote_images_verified=true`、正整数图片数量和结构有效的有序 remote_media/source SHA 与冻结清单一致；只有正文/ID 或编辑器图片通过不能形成新 G8。原 scheduled 继续用于防重，批准/preflight/激活共用能力判据，缺证据失败闭合。

本轮 `composer_media_probe_20260913T054737Z_final.json/.png` 是 FB 编辑器中的技术长文案与 2 张历史原图探查；文案明确不可发布，未点发布、排期、稍后完成或取消，可能留下草稿。`composer_media_verification_20260913.json` 已确认两图在编辑器中的数量、顺序和视觉一致性；它与远端排期媒体回读是两个验收单元，不能用前者关闭后者或当成运营批准的样本。

## 11. 飞书与审校台链接

飞书配置德国运营/开发者两个接收组，业务待审、积压、排期成功/失败与系统告警分别路由。未尝试事件在发送前重新读取当前详情，按帖聚合不重复计数；终结或挂起项退出待审。卡片取当前有效德语首图、人工优先摘要与检查结果；回退原图或上传失败必须明示。晨间摘要显示补抓、分类跳过、延迟、待审和最早等待，无事不发。

发送一旦尝试，UUID、接收组、内容和图片引用冻结。超时/不确定先核对，有效原卡补送保持 UUID/内容。若合并卡部分失效，人工确认未送达后保留原 cancelled 记录，再只给漏收者创建仍有效内容的新提醒；不能覆盖旧卡、复活已失效帖或给已收者重发。该多收件人修复已复审，卡片/聚合/恢复后端有离线证据；真实飞书权限、图片上传、链接与运营流程仍待联调。

`keep_delivered_days` 默认 30。过期归档按完整关联事件/投递组件进行，SHA 地址的归档保留原卡片/UUID/回执，主状态保留 archived_events 去重引用，旧事件不重新排队；归档损坏显式失败。uncertain/retry/pending、离岗未发、仍缺接收回执的组件保留在线，不能按创建时间清掉。状态查询和恢复要保留这一事实边界，不把“退出热 outbox”显示成投递记录丢失。

审校链接需在真实企业网络环境测试。生产机地址尚未决定，因为生产迁移明确延期；开发机地址不能提前写成生产契约。

## 12. 验收

离线验收分别覆盖 API 冲突/锁/坏账本、七态/人工优先、历史服务端分页/total/组合筛选/90 天外详情、源图实际字节/数量/顺序、模型中断恢复/旧候选/不确定计费、消息 UUID/冻结内容、三来源降级、设置 CAS/注释、月界/DST/完整覆盖、快照与恢复幂等。

版本化浏览器入口是 `tests/browser_regression.py`，夹具是 `tests/browser_fixture.py` 与 `tests/ui_fixture.py`。构建实际 dist 后运行：临时 ASGI/真实保存、历史分页/老详情/冻结只读、设置注释/CAS/source 冲突均已验证。运行恢复/消息未知及模拟排期/公开状态使用明确 API 夹具，仅验 UI；不会调用真实 approve。报告落在已 gitignore 的 `state/ui-regression/`，页面错误/外部请求/禁止写入错误均要求为 0。

⛔ 它照不出部署形状——UIFixture 自带 SPA 回落。深链接刷新由 `tests/tests_spa_static.py` 单独钉住。

```powershell
npm --prefix web/ui-next run build
scripts\run_python.bat tests/browser_regression.py
```

| 真实验收项 | 必须留下的证据 |
|---|---|
| 当前数据与 `.global` | 备份/配置绑定/旧激活保留；新 `.global` 完整回填与合作方覆盖；429 停机不重试 |
| 三来源/模型 | 实际风险 request/usage；Trends DE 同组 CSV、IG 全球累计、同类账号周更各自来源/时间/降级 |
| 飞书与云盘 | 两组消息不串、当前德语首图/摘要/检查、原图回退、按帖去重/晨报、固定尝试 UUID/内容；云盘上传/移动/版本/冻结补送 |
| 长正文 | 实际最终德语正文完整回读，不以截断预览代替 |
| 多图 | 每张最终图的字节/数量/顺序与快照、远端一致 |
| Facebook 链接 | 德国落地页 URL 在最终 FB 文案保留且人工检查正确 |
| Instagram CTA | 最终 IG 话术和已确认稳定 bio 聚合页对应，无动态改 bio |
| 完整月历 | 整月格子/时刻项、手工任务、延迟加载、多个条目、明确空态、公开 unknown 分别观察 |
| 两篇具体单渠道排期 | 先准备文案/图/目标账号/唯一渠道/柏林时间给用户确认；分别完成 FB-only、IG-only 一次回读，留 remote ID/时刻/渠道/fingerprint |
| 运营完整流程 | 运营从真实飞书卡片打开当前任务、修改/检查文图标签链接、选时确认，最后收到可核的排期回执 |

每项记录日期、输入和远端证据，不用 probe、夹具、历史 `.tech` 或两个孤立提交替代完整操作流程。本轮目前未取得新的单渠道提交回读或上述运营闭环证据。

## 13. 明确延期

登录、RBAC、非空 actor、生产部署、视频、跨平台复用、`supervised`/无人审核发布和发布队列本轮不做。
