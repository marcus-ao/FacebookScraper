# 审校台前端重构最终实施报告

日期：2026-09-13。执行分支：`codex/business-workflow`。本报告相对本次开工快照 `execution-baseline.json`，不把此前 Stage A/B 或已有后端修改计作本次成果。

## A. Executive Summary

已按 [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md) 顺序完成 **C → E′ → D1–D5 → F → G → H → I → J**。审校队列、历史归档、单篇审核、发布月历、运营设置、运行状态均为正式功能页。Stage A/B 的领域契约、核心标记算法、双时区语义及主色继续沿用。

本次实施范围没有未完成 Stage，没有已知 UI 主流程 blocker。前端与隔离业务验收支持受控切换评估；实际生产发布能力仍由既有本机验收与远端回执决定。本次没有执行生产 cutover，没有 Git 提交/推送，没有真实发布、模型计费或飞书重发。额度重置卡 **0 张**，未触及最多 1 张的上限。

受控切换建议见 U；保留的后端能力缺口、一次既有 Python 测试波动和构建体积告警见 R。

## B. File Summary

本次新增 **50 个 React 源码/样式/测试文件**，修改 **9 个既有 React 文件**；没有删除既有文件。新增测试仅 3 个文件、15 个业务断言，原 415 项保持。完整逐文件分类与 SHA-256 见 [final-file-manifest.json](final-file-manifest.json)，其中包含文档、截图、浏览器请求与 Python 日志证据。

新增 React 文件，均位于 `web/ui-next/src/`：

| 目录 | 文件 |
|---|---|
| hooks | useApproval.ts、useCalendar.ts、useContentJob.ts、useDialogTabLoop.ts、useLocalization.ts、useRuntime.ts、useSettings.ts、useTaskDetail.ts、useTasks.ts |
| services | approval.ts、calendar.ts、hashtags.ts、jobs.ts、localization.ts、review.ts、runtime.ts、settings.ts、tasks.ts |
| features/approval | DecisionPanel.tsx、DecisionPanel.module.css |
| features/content-jobs | ContentJobs.tsx、ContentJobs.module.css |
| features/diagnostics | DetailDrawers.tsx |
| features/images | ImageWorkspace.tsx、ImageWorkspace.module.css |
| features/localization | CategoryEditor.tsx、LocalizationEditor.tsx、LocalizationEditor.module.css、TextWorkspace.tsx、TextWorkspace.module.css、model.ts、model.test.ts |
| features/post-list | ListFilters.tsx、PostTable.tsx、PostTable.module.css、model.ts、model.test.ts |
| features/review-actions | ReviewActions.tsx |
| features/runtime | model.ts、model.test.ts |
| pages/calendar | CalendarPage.tsx、CalendarPage.module.css |
| pages/history | HistoryPage.tsx |
| pages/review | ReviewPage.tsx |
| pages/review-detail | ReviewDetailPage.tsx、ReviewDetailPage.module.css |
| pages/runtime | RuntimePage.tsx、RuntimePage.module.css |
| pages/settings | SettingsPage.tsx、SettingsPage.module.css |

修改的既有 React 文件：`app/AppShell.tsx`、`app/AppShell.test.tsx`、`app/Navigation.tsx`、`app/router.tsx`、`app/RuntimeIndicator.tsx`、`app/theme.ts`、`lib/format.ts`、`features/post-list/columns.tsx`、`features/post-list/columns.module.css`。AppShell 原测试适配实时状态文案，原算法测试与领域 fixture 未改。

新增文档：IMPLEMENTATION_PLAN、STAGE_C/D/E/F/G/H/I/J_REPORT、FINAL_IMPLEMENTATION_REPORT；新增工具仅复用现有 BrowserFixture 的 `ui_fixture.py`、`browser_regression.py`、`network_compare.py`、`capture_final.py`。更新现有 README、DECISION_LOG、DESIGN、REACT_MIGRATION_PLAN、UI_ARCHITECTURE_PROPOSAL 的执行状态与最终定值。BASELINE_BEHAVIOR、原始审计、A/B 报告不改。截图和 JSON 是验收证据，Python 日志来自原有 65 个脚本的执行，不是新增业务代码。

## C. Dependency Diff

Vue 与 React 的 `package.json`、`package-lock.json` **均未变化**，四个文件的 SHA-256 与开工一致。新增依赖 **0**，升级依赖 **0**。继续使用已有 React、TypeScript、React Router、TanStack Query、Ant Design、icons、dayjs、Vite、Vitest；浏览器使用原 Python Playwright 运行环境。没有新增 npm 测试框架或 UI 库。

## D. API Contract

本轮后端修改 **0**；新增/删除/改名 endpoint **0**；新增 backend field **0**；响应字段、status 枚举和业务含义变更 **0**。领域类型保持 Stage A 的真实载荷契约。关键写入仍为：

| 操作 | endpoint | 请求内容保持 |
|---|---|---|
| 即时校验 | POST `/api/tasks/{id}/check` | text_de、body_only:true、完整 localization draft；250ms debounce |
| 保存 | PUT `/api/tasks/{id}/localization` | body_de、tags、links、ig_cta、hashtags_confirmed，加 source_text_sha256、human_revision、review_revision、localization_revision |
| 审校决定 | POST `/api/tasks/{id}/review` | source_text_sha256、review_revision、action、reason、handoff_url，按原条件附 wake_at |
| 导出 | POST `/api/tasks/{id}/export` | 原审校版本与交接字段；保留服务端文件名及下载完成后的 object URL 释放 |
| 产品分类 | PUT `/api/tasks/{id}/tags` | tags、source_text_sha256、tags_revision，独立于德语正文草稿 |
| 排期 | POST `/api/tasks/{id}/approve` | scheduled_at、source_text_sha256、human_revision、review_revision、content_fingerprint |
| 补回执 | POST `/api/tasks/{id}/publication/reconcile` | 空对象；随后读取单篇最新状态 |
| 初翻 | POST `/api/initial-translation/task/{id}` | consent、source_fingerprint、source_text_sha256、human_revision、review_revision |
| 优化 | POST `/api/refinements/task/{id}` | kind、instruction、media_index，加上述三个版本字段 |
| 中断任务恢复 | POST `/api/content-jobs/{job_id}/recover` | expected_updated_at；不再次提交模型生成 |
| 标签建议 | POST `/api/hashtags/task/{id}` | 保持旧版完整输入；已纳入新旧请求逐字段对照 |
| 月历刷新 | POST `/api/calendar/refresh` | 空对象 |
| 设置 | PUT `/api/settings` | values、version；values 仅两个运营字段 |
| 投递核对 | POST `/api/runtime/notifications/{id}/resolve` | action、version、message_id |
| 批次关闭 | POST `/api/runtime/processing/recover` | batch_id、version、outputs_reviewed:true |

task id 继续逐段编码；错误保留 status/payload；409 恢复继续使用服务端最新版本。审批 200 仅在 `ok === true && status === 'scheduled'` 时确认成功。未把接口“已受理”“条件可用”写成“已公开发布”。

## E. Review Queue

四桶映射：待我审=`pending_review/edited`；未就绪=`not_ready`；已挂起=`snoozed`；已处理=`approved/scheduled/skipped/handed_off`。计数来自 `summary.by_status`，即使待我审为 0 也不自动切桶；每行保留真实 status。

URL 是 queue/platform/month/tag/alerts 的上下文来源。共享 Query key 为 `['tasks','review']`；筛选在已取到的队列内完成。`refetchOnMount:false`、`refetchOnReconnect:false`，会话内保留缓存。成功写入更新详情和队列行/summary，不 invalidate 整个队列。用户显式点击主导航可刷新。

浏览器实测：首次载入、五项筛选及详情往返合计 **1 次列表 GET**；审校写入后额外列表 GET **0**；显式导航刷新额外 **1**。行高 **48px**；首屏 **12 行 @1366、19 行 @1920**。摘要为真实链接，整行点击排除交互控件，菜单项不误导航。

第三方作者硬闸显示授权/初翻业务提示，B21 PASS；八种状态的四桶分组与行内原状态，B22 PASS。行内无 danger 按钮；“这篇不发”放菜单，并以必填理由 Modal 确认。挂起、恢复、手工交接、补链接、下载沿用旧业务契约。

## F. History

历史走服务端查询和分页，Query key 包含 platform/month/tag/page/limit。默认 **50**，可选 **20/50/100**，完整写入 URL；三个筛选重置 page=1。可搜索月份并按年分组。详情刷新、上下篇和返回保留历史来源、筛选、页码及大小。

复用相同 Table 基础，增加日期和账号，冻结账号显示只读提示。首屏 **12/19 行**、行高 **48px**，分页均在首屏可达。31 条 2021 年冻结归档的筛选、总数、20 条分页、第二页 11 条、完整详情与返回均通过；B19/B24 PASS。

## G. Detail

56px 吸顶三段头：返回/序号/相邻篇，平台/真实状态/原文变更/标记/图片进度，编辑/唯一主操作/更多动作。当前筛选确定 n/N 及上一篇/下一篇；编辑期间禁用相邻切换并解释原因。`text/images/localization` 三个 tab 写入 URL。

正文 Grid 1:1，15px/1.7，两栏内滚；人工稿优先。标记沿用 Python 码点转换、排序及 error > risk > warn 重叠算法，视觉重量为 error > warn > risk。长正文与补充平面表情定位验证通过，跳转使用直接 scrollTop，保留 N/P/方向键。编辑用与正文一致的 textarea/mirror，保留光标插入；check 过期响应丢弃，issues 提示但不挡保存。

正文、标签、链接形成同一草稿及版本提交；409 可载入最新内容并保留输入，source hash 变化时保留人工正文/CTA/链接选择与语义标签，同时更新保护标签、撤销需要重新确认的确认项。应用导航、浏览器历史、关页三路径 dirty guard 均通过。

标签保留整段粘贴 textarea，实时解析去重并可单个删除；品牌/型号标签锁定，显示原帖未采用及本篇新增。建议保留德国 Trends 组内指数、Instagram 全球累计帖子数、德国同类账号近 14 天使用次数，注明来源/时间/批次；缺样不能当作 0，过期不能伪装为新样本。建议不自动写入。

Facebook 原始链接及配置映射只读，德语落地页与人工确认属于本篇；光标插入显示“〔链接 N〕”，存储继续使用原 link token。Instagram 使用主页引导/自定义 CTA 与只读 bio。产品分类独立 CAS，不混入正文保存。

图片 contact sheet、已看标记、前后切换、原图/德语图对照和放大 Modal；初始 seen={0}，换篇清零。缺德语图明确提示且回退原图；未看完图片只提醒，不禁止通过；技术指标收起。

排期区保留柏林当地时刻、可选范围/常用时刻、同渠道缓存占用和间隔、作者/原帖来源。主操作解释禁用原因；最终 Modal 固定确认当时的内容与版本。前端不加本地时区偏移，DST 拒绝由原后端解析器执行。409 备选时刻可点，异常回执可核对并补齐；成功须严格 scheduled 回执。

第三方初翻在正文前显示 warning Alert、consent 与付费按钮；无 consent 不可发起。初翻/文案优化/图片优化/标签建议均显示费用口径且不是 primary。initial 1500ms、refinement 1200ms 轮询，仅 pending/running 继续，succeeded/failed/interrupted 停止。中断恢复不重发模型；文案候选覆盖脏草稿须确认；人工图片继续优先。

处理记录用 Timeline Drawer，诊断与模板只读且默认下沉。冻结历史保留正文、图片、标签和来源查阅，直接不渲染编辑、初翻、优化、发布入口。B5–B18 结果见 L。

## H. Calendar

周一起始 CSS Grid，空日格最小 64px；卡片只常显时间、渠道、业务状态，正文进 Popover。`published` 为已观测到公开发布，`scheduled` 为已创建定时任务，未知为待核验。

初次只 GET 缓存；显式“刷新月历”为 default 按钮，含耗时说明/loading。没有自动 POST，也不新增从空槽排期。POST 失败时保留服务端 payload.cards 和已知缓存；刷新失败、缓存时效与月覆盖信息可查，不把失败当空月历。浏览器验证 503 降级和状态区分通过。

## I. Settings

上方表单只开放 `default_times` 和 `snooze_default_days`，下方七组受控配置及原注释收起只读。业务说明写明柏林排期时刻、上海工作日、已排期不受默认值变更影响。

时刻要求 HH:MM、1–12 个且不重复；挂起为 1–30 工作日。PUT 仍为 `{values,version}`。409 保留输入，读取新版本后重试；三类离开守卫均验证。B20 PASS。

## J. Runtime

入口只在 Header。五阶段逐阶段解释，未知、不可读、未观测不会显示成功；进程活跃与最近完整业务成功分开。阶段 2 的 available、阶段 3 的 completed、阶段 4 的 ready、阶段 5 的 available 才按各自语义显示条件/处理完成，顶栏不把它们合成为“发布成功”。

顶部列需要处理项，恢复动作仍有业务确认。已送达要求 message ID；未送达恢复确认接收组与原消息摘要、勾选后才能触发旧接口。当前 Outbox.status 不返回这两项，因此由运营从飞书核对后填写，绝不伪装成服务端原文，也不增加请求字段。关闭中断批次要求核对已付费用、文案和图片结果，携带原版本。

30 秒共享读取更新 Header；阅读页保留快照，变化提示“有更新”，仅显式刷新替换。技术信息在 Collapse/Drawer。未确认发布只有 count，说明无法直接定位并提供维护说明，未伪造 task_id 链接。DEF-1 和 DEF-10 见 R。

## K. Test Results

| 检查 | 精确结果 | 证据/范围 |
|---|---|---|
| React `npm test` | **430/430 PASS，22/22 文件** | 原 415 + 列表 6 + runtime 7 + 来源变化恢复 2；最终源码再次通过 |
| TypeScript | **0 错误** | `npm run typecheck`；最终 build 内再次 `tsc --noEmit` |
| React build | **PASS，3222 modules** | JS 1,398.67 kB / gzip 446.93 kB；CSS 17.71 kB / gzip 3.82 kB；index 0.60 kB |
| Vue build | **PASS** | JS 164.54 kB / gzip 59.68 kB；CSS 31.27 kB；保留旧 dist |
| Python 原套件 | **首轮 64/65 脚本 PASS** | [完整结果](python-evidence/full/results.json)；未改 runner 或后端测试 |
| Python 单项复跑 | **1/1 脚本 PASS，5/5 用例** | [settings retry](python-evidence/settings-retry/results.json)；所有 65 个脚本均取得通过记录 |
| 浏览器集成 | **12/12 场景组 PASS** | [browser-stage-all.json](browser-stage-all.json)，C/E/D1/D2/D3/D4/D5/D/F/G/H/I |
| 新旧网络 | **16/16 写入契约一致，32 次 UI 流程** | [network-comparison.json](network-comparison.json) |
| 最终视觉 | **20/20 页面截图** | 10 视图 × 两种分辨率，[final-measurements.json](final-measurements.json) |
| 最终键盘/标签 | **PASS** | Modal/Drawer 正反 Tab、Esc/焦点返回、浮层关闭后快捷键、编辑输入不被抢、chip 删除、reduced motion |

Python 首轮唯一失败为 `tests_operating_settings` 的快速配置重载断言 `3 != 4`；原样独立复跑通过。Config 使用 mtime_ns+size 判断重载，现象与快速等长改写的时间戳识别有关，但本轮没有证明完整根因，也没有修改业务代码来掩盖它。不能把首轮写成 65/65。

Stage J 用实际失败推动了有限修正：CSS 高度回归到 token 单一来源；首尾 Tab 循环；隐藏弹窗不再屏蔽 N/P。原有约束测试最终通过。一次网络脚本与 build 同时运行产生 dist 暂时不可读，结束 build 后独立重跑 32 条流程通过；这不是业务请求失败。后续最终键盘修正不涉及请求层。没有为这些检查新增大套测试基础设施。

## L. Baseline Regression

| 编号 | 结果 | 验证内容与证据 |
|---|---|---|
| B1 | PASS | `/`、旧 task/view 参数及正式路由直接打开；Stage I |
| B2 | PASS | 工作区和详情前进/后退还原；Stage I |
| B3 | PASS | 历史详情返回历史并保留分页；Stage E |
| B4 | PASS | 详情与设置的应用导航、历史导航、关页三路径 dirty guard；D1/D/G |
| B5 | PASS | 七态+not_ready 的分桶、编辑、动作与审批判据；C/D |
| B6 | PASS | 16 个 Vue/React 流程完整 method/path/body 一致；network comparison |
| B7 | PASS | 草稿、排期、产品分类、审校决定及设置 CAS；D1/D4/I/G |
| B8 | PASS | 校验 issues 仍可保存；D1 |
| B9 | PASS | 表情后的金额位置及长正文跳转；D |
| B10 | PASS | 重叠算法 error > risk > warn；原 marks 测试与 D |
| B11 | PASS | 下一处落在内滚动视口，无 smooth；D |
| B12 | PASS | 浏览器 America/New_York 下原样柏林 wall time；2026-03-29 02:30 与 2026-10-25 02:30 经原解析器拒绝；D4/D |
| B13 | PASS | wake_at 发出 +08:00；C/I 与新旧网络 |
| B14 | PASS | 跳过理由为空时确认 disabled；C |
| B15 | PASS | 图片未看提示存在，但不禁用本来可用的排期；D3/D |
| B16 | PASS | de_present:false 回退原图并明示；D3 |
| B17 | PASS | 冻结账号无加工/发布入口；D/E |
| B18 | PASS | 200 非严格 scheduled 成功回执仍报待核对；D4 |
| B19 | PASS | 历史后端分页、总数、三筛选、90 天外完整详情；E 与原 Python tests_history |
| B20 | PASS | 两个可编辑字段及 CAS；G |
| B21 | PASS | unknown_collaborator 的授权/初翻提示可见；C |
| B22 | PASS | 四桶分类及已处理内各行真实 status；C |
| B23 | PASS | 带筛选刷新详情仍保持 n/N、相邻篇及返回上下文；D |
| B24 | PASS | limit URL，默认 50，可选 20/50/100；E |

上述是逐项业务验收，不代表已真实创建远端排期。所有有远端影响的动作均使用明确 stub。

## M. Network Sequence Diff

相同临时帖子、版本、运营输入，分别实际驱动 Vue 和 React。以下 **16 个写入完全一致**：save、snooze、wake、skip、handoff、handoff_link、tags、export、approve、calendar_failure、settings、initial、refine_text、refine_image、recover_job、hashtags。保存末次 `/check` 的完整 body 也一致。

**16 条只读请求序列有差异**，没有假称整体序列相同：React Header 共享 runtime GET；详情增加 calendar GET 供同渠道占用；图片因 tab 常驻但隐藏而提前加载；Query 缓存改变重复读取和时机。确认 Modal、Tabs 与 250ms debounce 也改变发起时序。写入次数、endpoint、字段、值与旧版相同。每条完整 trace 在 network-comparison.json，runtime 的三条恢复另由 Stage H 逐字段验证。

## N. Visual QA

截图全部来自临时归档和显式 calendar/runtime 缓存夹具，图片使用隔离色块，不冒充真实运营素材。截图逐张查看；尺寸按浏览器 DOM 量取。非列表 rowsAboveFold=0。primaryCount 包含不可用但仍占唯一动作位的主按钮；冻结只读详情无需主操作。

| 页面 / 分辨率 | rowsAboveFold | screensOfScroll | documentHeight | h1 | primary | danger | bordered | overflow | heading sizes |
|---|---:|---:|---:|---:|---:|---:|---:|---|---|
| review@1366x768 | 12 | 2.02 | 1551px | 1 | 0 | 0 | 0 | 无 | H1 20px |
| history@1366x768 | 12 | 3.32 | 2553px | 1 | 0 | 0 | 0 | 无 | H1 20px |
| review-detail@1366x768 | 0 | 1.47 | 1132px | 1 | 1 | 0 | 2 | 无 | H1 20px, H2 14px |
| review-detail-images@1366x768 | 0 | 1.48 | 1139px | 1 | 1 | 0 | 1 | 无 | H1 20px, H2 14px |
| review-detail-tags@1366x768 | 0 | 1.20 | 925px | 1 | 1 | 0 | 3 | 无 | H1 20px, H2 14px |
| review-detail-thirdparty@1366x768 | 0 | 1.47 | 1132px | 1 | 1 | 0 | 3 | 无 | H1 20px, H2 14px |
| history-detail@1366x768 | 0 | 1.07 | 825px | 1 | 0 | 0 | 1 | 无 | H1 20px, H2 14px |
| calendar@1366x768 | 0 | 1.08 | 832px | 1 | 0 | 0 | 33 | 无 | H1 20px |
| settings@1366x768 | 0 | 1.24 | 953px | 1 | 1 | 0 | 2 | 无 | H1 20px, H2 14px |
| runtime@1366x768 | 0 | 1.46 | 1120px | 1 | 0 | 0 | 1 | 无 | H1 20px, H2 14px |
| review@1920x1080 | 19 | 1.44 | 1551px | 1 | 0 | 0 | 0 | 无 | H1 20px |
| history@1920x1080 | 19 | 2.36 | 2553px | 1 | 0 | 0 | 0 | 无 | H1 20px |
| review-detail@1920x1080 | 0 | 1.34 | 1444px | 1 | 1 | 0 | 2 | 无 | H1 20px, H2 14px |
| review-detail-images@1920x1080 | 0 | 1.34 | 1451px | 1 | 1 | 0 | 1 | 无 | H1 20px, H2 14px |
| review-detail-tags@1920x1080 | 0 | 1.00 | 1080px | 1 | 1 | 0 | 3 | 无 | H1 20px, H2 14px |
| review-detail-thirdparty@1920x1080 | 0 | 1.34 | 1444px | 1 | 1 | 0 | 3 | 无 | H1 20px, H2 14px |
| history-detail@1920x1080 | 0 | 1.05 | 1137px | 1 | 0 | 0 | 1 | 无 | H1 20px, H2 14px |
| calendar@1920x1080 | 0 | 1.00 | 1080px | 1 | 0 | 0 | 33 | 无 | H1 20px |
| settings@1920x1080 | 0 | 1.00 | 1080px | 1 | 1 | 0 | 2 | 无 | H1 20px, H2 14px |
| runtime@1920x1080 | 0 | 1.04 | 1120px | 1 | 0 | 0 | 1 | 无 | H1 20px, H2 14px |

两分辨率的列表 rowHeights 均为 [48]，历史分页首屏可达。所有页面 h1=1，行内 danger=0，无横向溢出与吸顶头溢出；每个可操作详情首屏 primary=1。详情总高 ≤1.5 viewport，边框区域 ≤3。同级标题字号一致：h1 20px、h2 14px、h3 13px。

月历的 33 个几何边框包括日格，详情“三块”标准不套用于日历网格。列表真实状态标签逐行显示，不能为了满足旧“全屏最多 8 个 Tag”指标隐藏状态；分类/正文标签为业务内容。动态展开优化、诊断或长候选会增加高度，表中度量的是默认主路径。

## O. Accessibility

Modal 打开焦点进入；正向/反向 Tab 均留在弹窗；Esc 关闭并回到原触发按钮。Drawer 正反 Tab 与 Esc 验证通过。当前 antd 依赖依靠 focusin 回收焦点，在 Chromium 转入浏览器工具栏时收不到事件；`useDialogTabLoop` 只补可见 dialog 的首尾 Tab，不替代组件的打开/关闭生命周期。

正文保留 N/P/上下箭头、Esc 返回；编辑输入与可见 Modal/Dropdown/Select 浮层抑制快捷键。关闭后隐藏节点不再抑制操作。字号、按钮 focus ring 延用共享 token；`lang=zh-CN`，antd zhCN 与 dayjs zh-cn 均保留。`prefers-reduced-motion` 实测 transition/animation 为 **0.00001s**，scrollBehavior 为 auto。金额与柏林时间用等宽数字；时间输入保留原生 datetime-local，使用明确的中文业务 label。

## P. Safety Evidence

| 真实外部动作 | 本次次数 |
|---|---:|
| approve / 创建远端排期 | 0 |
| calendar refresh / 远端刷新 | 0 |
| paid model / 模型计费 | 0 |
| Feishu resend / 飞书重发 | 0 |

BrowserFixture 使用临时 archive/state，浏览器只允许回环宿主；真实可写接口仅限隔离配置/草稿/check，其余动作显式 stub。最终套件的 page_errors、未预期服务器拒绝为空。没有请求或使用额度重置工具。费用按钮只是完成原功能，验证没有产生账单。

## Q. Data Integrity

原七个 dirty 文件、config.toml、config.local.toml、两套 package 与 lock、BASELINE_BEHAVIOR 共 **14/14 SHA-256 一致**，证据见 [integrity-check.json](integrity-check.json)。原后端、旧 Vue 源码及生产挂载配置未改。

浏览器的 archive/state 全部属于临时 fixture，退出时还原隔离上下文。本次未向真实归档、账本或业务状态写入。Python runner 创建的两次 workspace offline-validation 日志已移至 `docs/ui-refactor/python-evidence/`；它们是检查结果，不是生产业务记录。没有声称对整个真实归档做过全量哈希审计；证据来自隔离宿主、请求记录、已有文件哈希及实际执行边界。

## R. Deferred Items

| 编号 | 保留项 | 本次处置/影响 |
|---|---|---|
| DEF-1 | runtime 未确认提交缺 task_ids | 保留计数+维护说明；不新增后端字段 |
| DEF-2 | 历史全文/摘要搜索 | 需后端查询契约，未排期 |
| DEF-3 | 批量挂起/不发 | 单篇动作完整；批量需求未排期 |
| DEF-4 | 月历空槽直接排期 | 只读占用+单篇排期完成，空槽入口不做 |
| DEF-5 | 译文四态列表列 | 列表载荷不足，不推测或伪造 |
| DEF-6 | 正文分栏拖动比例 | 当前 Grid 1:1；待实际需求 |
| DEF-7 | Table 虚拟滚动 | 当前 48px 普通 Table 达到密度，未引入 |
| DEF-8 | 演示模式 risks.json | 按已锁定决定不做 |
| DEF-9 | 删除旧 Vue、改名 ui-next、取消可选 dist | 未获该动作授权，双构建保留 |
| DEF-10 | 飞书恢复自动展示原接收组/消息 | Outbox.status 当前不返回；确认时要求运营核对填写，接口不变 |
| DEF-11 | 原 tests_operating_settings 快速重载偶发失败 | 65 脚本首轮仅此项失败，原样复跑通过；根因未完全证明，后端未改 |
| DEF-12 | React 单包大于 500 kB | 构建成功但保留 Vite 告警；JS gzip 446.93 kB，后续按部署体验评估拆包 |
| DEF-13 | 真实排期/刷新回执形状与 publication 细分类型 | 本次仅源码契约+stub+原后端测试；生产本机与远端验收在另行受控操作中完成 |

这些事项不阻断本次前端实现及受控 UI 切换评估。DEF-1/10 是明确可见的能力限制；DEF-13 不得被报告成真实发布链路已经验收。

## S. Git

开工与最终的已跟踪文件 diff 一致。以下 7 个文件本轮完全未改；UI/文档目录在开工时已是 untracked，因此普通 git diff --stat 不包含本次新增页面。逐文件差异以开工哈希 manifest 为准。

```text
 M .gitignore
 M core/index_db.py
 M core/review.py
 M tests/tests_history.py
 M web/api/app.py
 M web/api/query_index.py
 M web/ui/src/components/TaskList.vue
?? docs/ui-refactor/
?? web/ui-next/
```

```text
 .gitignore                         |  4 ++++
 core/index_db.py                   |  4 +++-
 core/review.py                     | 11 ++++++++--
 tests/tests_history.py             | 27 +++++++++++++++++++++++-
 web/api/app.py                     | 37 ++++++++++++++++++++++++++++++---
 web/api/query_index.py             | 42 ++++++++++++++++++++++++++++++++------
 web/ui/src/components/TaskList.vue |  8 +++++++-
 7 files changed, 119 insertions(+), 14 deletions(-)
```

没有 commit、push、PR、restore、reset、stash、clean。

## T. Cutover Instructions

以下是后续受控切换步骤，**本次未执行**。

1. 在部署所用 checkout 分别进入 `web/ui-next` 与 `web/ui`，按各自已有 lock 安装环境（如尚未安装），执行 `npm run build`。保留两份 dist。当前工作区两份已经构建成功。
2. 记录部署 checkout 的原 `config.toml` 中 `[paths].web_dist` 值。在**同一个** `[paths]` 表内临时设 `web_dist = "web/ui-next/dist"`，不要新建重复表，也不要放入 config.local.toml：后者白名单只允许 archive/state。
3. 重启加载该 checkout 的 Web 进程，例：`scripts\run_python.bat -m uvicorn web.api.app:app --host 127.0.0.1 --port 8765`。DIST 在模块载入时确定，单独刷新浏览器不能代替重启。
4. 先核对 `/review`、`/history`、一个真实只读详情、`/calendar`、`/settings`、`/runtime` 和旧 task/view 深链接；确认静态资源 200、筛选上下文、时间标签和现有 GET 数据一致。再按受控运营安排验证必要写入；真实发布/付费动作仍需对应业务授权与后端前置条件。
5. 回滚时把该配置恢复为记录的原值；原先没有该字段则删除新增字段，或设 `web_dist = "web/ui/dist"`。重启同一 Web 进程，再核对旧页和 GET。无需变更接口、归档、账本或业务状态。

## U. Final Recommendation

**READY_WITH_NON_BLOCKING_DEFERRED_ITEMS**

理由：约定 Stage 全部完成，B1–B24 通过，430 前端测试及双构建通过，16 条新旧写入契约一致，20 张截图达到主路径密度并完成键盘验收。原有 65 个 Python 脚本均有通过记录，同时如实保留首轮单项波动。生产配置、旧 UI 和既有修改保持；R 所列能力缺口不被隐藏。该结论支持后续受控 UI 切换，不等同真实发布环境已经通过验收。

Remaining implementation complete.
Production cutover not executed.
