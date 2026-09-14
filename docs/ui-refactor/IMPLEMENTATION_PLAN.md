# Remaining UI Implementation Plan

> 执行方式：使用 writing-plans / executing-plans，按下列复选项逐项实施。用户已在本任务批准 C → E′ → D → F → G → H → I → J 连续执行；旧文档的逐阶段批准要求不再适用。

**Goal:** 完成六个正式 React 页面和完整审校工作流，保留冻结业务行为，提供可核对的回归、视觉证据及最终交付报告。

**Architecture:** 延续 Stage A/B 的 Router、Query、antd 和共享基元。服务层只封装既有 HTTP 契约；查询与写入缓存集中在 hooks；页面组织功能组件，草稿保留在本地，筛选和详情上下文只取 URL。

**Tech Stack:** React/DOM 19.3.0、Router 7.18.3、Query 5.102.8、antd 6.6.3、icons 6.3.4、dayjs 1.11.23、TS 7.0.2、Vite 8.3.0、Vitest 5.0.0；Python Playwright + BrowserFixture。

**Spec:** 用户附件 `pasted-text-1.txt`（153 条）；业务真相为 `web/DESIGN.md` 和实际接口，视觉真相为 `docs/ui-refactor/DESIGN.md`，冻结行为为 `BASELINE_BEHAVIOR.md`。指定 11 份文档已按用户顺序读取。

## 全局约束

- 严格顺序 C → E′ → D1 → D2 → D3 → D4 → D5 → F → G → H → I → J。阶段验证通过后立即继续，不另开产品讨论。
- 不修改既有无关 Python，不覆盖 A/B 和用户原有修改；不 commit/push/PR、restore/reset/stash/clean。
- 不新增依赖；已有依赖可用时直接使用，需恢复安装时用 npm ci。
- 保留 Header 48、Sider 200/48、详情头 56、主色 #155EEF、token 单一来源、焦点 2/2、禁用 smooth。
- 不改 API、状态语义或生产默认 dist；DEF-1 保留 count 限制和维护入口，不伪造 task_ids。
- `/check` 不拦保存，未看完图片不拦通过；人工文案和图片优先；所有写入保留原版本字段。
- 柏林排期提交 `YYYY-MM-DDTHH:MM` 无 offset；挂起明确 `+08:00`；marks 保留 Python codepoint 与 error > risk > warn。
- 所有自动化使用临时 fixture；真实 approve/calendar refresh/paid LLM/Feishu resend 均须为 0。仅实施所需验证，不扩展无关测试或基础设施。
- 额度重置卡使用上限 **1 张**，仅额度不足时使用。当前累计使用 **0 张**。没有可调用的卡片工具时如实报告，不能假称已重置。

## 开工快照

`git status --short`：

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

`git diff --stat`：7 files changed, 119 insertions(+), 14 deletions(-)。各文件 +4、+3/-1、+9/-2、+26/-1、+34/-3、+36/-6、+7/-1，完整原始输出另存 execution-baseline.json。四个早于 UI 重构的修改为 core/index_db.py、core/review.py、tests/tests_history.py、web/api/query_index.py。

## Stage C — 审校队列（完成，见 STAGE_C_REPORT.md）

**文件**：services/tasks.ts、services/review.ts；hooks/useTasks.ts；features/post-list/model.ts、ListFilters.tsx、PostTable.tsx、columns.tsx；features/review-actions/ReviewActions.tsx；pages/review/ReviewPage.tsx；app/router.tsx、Navigation.tsx；必要 layout token。测试放 model.test.ts 与 tools/browser_regression.py，复用 BrowserFixture。

- [x] 读取旧 TaskList、ReviewActions、App.patchRow 和 api，建立相同 body 与成功后 row/summary patch。
- [x] `reviewListOptions` 使用 `['tasks','review']`、`refetchOnMount:false`、不因 URL 筛选变化重新取数；整个任务期间保留 review 缓存，显式导航允许刷新。
- [x] URL 四桶 Tabs + platform/month/tag Select + 条件硬闸 chip；counts 取 summary.by_status；默认待我审即使为 0 也不切换。
- [x] 48px Table，共享列，摘要真实 Link，整行排除交互控件，分类窄屏隐藏；第三方作者说明；中性终态。
- [x] Dropdown + 同意图 Modal：snooze/wake/skip；skip 必填理由且 danger；409 保留填写内容并刷新单篇。
- [x] 验证四桶 B21/B22、body/wake_at、缓存 patch、筛选 GET 次数及详情往返 GET 次数；typecheck/build。记录 Stage C 报告后继续。

## Stage E′ — 历史归档

**文件**：pages/history/HistoryPage.tsx，扩展 services/tasks.ts、hooks/useTasks.ts、共享过滤器/列/表格；复用 browser_regression.py。

- [x] 读取旧 HistoryPanel；history key 包含实际 platform/month/tag/page/limit 服务端参数。
- [x] 默认 page=1/limit=50，可选 20/50/100 入 URL；三个筛选重置页码；月份可搜索、按年分组。
- [x] 同一 Table 基础，多日期/账号列，冻结图标 Tooltip；分页放在首屏可达区域；列表/详情保留完整 history context。
- [x] 验证 B19/B24：服务端总数、分页、三个筛选、90 天外详情和返回原页（详情完整行为待 D 接入后复跑）；typecheck/build，记录 E 报告。

## Stage D — 单篇审核（按 D1–D5 连续推进）

**文件**：pages/review-detail/ReviewDetailPage.tsx；hooks/useTaskDetail.ts；features/localization/TextWorkspace.tsx、LocalizationEditor.tsx、CategoryEditor.tsx；features/images/ImageWorkspace.tsx；features/approval/DecisionPanel.tsx；features/content-jobs/ContentJobs.tsx；features/diagnostics/DetailDrawers.tsx；services/review.ts、approval.ts、jobs.ts；相关纯逻辑模块。

- [x] D1：先读 TaskDetail/TextCompare/RiskPanel；56px 三段头、来自筛选缓存的 n/N 和相邻帖子，三个 URL tab；正文 1:1 内滚，镜像 textarea 与 marks；250ms check 丢弃过期结果；人工保存和冲突保留草稿；接 dirty guard 与旧快捷键抑制。
- [x] D2：读 HashtagEditor/LinkEditor/TagEditor；整段粘贴、保护标签、差异 chips、建议的三来源状态；FB 光标插入底层 link token、显示层业务词；IG CTA；分类独立 CAS。校验 issues 显示但不拦保存。
- [x] D3：读 ImageCompare；contact sheet、初始 seen={0}、换篇重置、原/德并排、缺德语图明示、指标下沉。
- [x] D4：读 ApprovalPanel/MetaPanel 与后端 approval 实际字段；决策状态、柏林时间、范围/常用时刻、同渠道 gap 占用、来源；唯一 primary 及具体禁用原因；提交完整版本/fingerprint、严格 ok && scheduled，409 可点备选时刻和恢复回执入口。
- [x] D5：读 InitialTranslationPanel/RefinementPanel；第三方 consent、PaidActionButton、1500/1200ms 轮询至终态、恢复不再次调用模型、候选采用确认；默认收起优化；处理记录/诊断/模板 Drawer；冻结详情不渲染写入口。
- [x] 对 D1–D5 每个交付做相关纯逻辑/浏览器验证；D 结束完整 B4–B18/B23、typecheck/build，记录 D 报告。

## Stage F — 发布月历

**文件**：services/calendar.ts、hooks/useCalendar.ts、pages/calendar/CalendarPage.tsx、日期逻辑模块；browser_regression.py。

- [x] 读取旧 CalendarPanel 和错误降级；GET 缓存，显式 POST refresh。
- [x] 周一起始 CSS Grid，空格 min 64、时间/渠道/状态在格内，正文进 Popover；cached/stale/coverage 可核对；default 刷新按钮 Tooltip/loading。
- [x] 验证 scheduled/published 区分、跨月时区、失败 payload.cards 仍可见；typecheck/build，记录 F 报告。

## Stage G — 运营设置

**文件**：services/settings.ts、hooks/useSettings.ts、pages/settings/SettingsPage.tsx。

- [x] 读取旧 SettingsPanel；上方 editable Form/Card，下方只读 Collapse；只有 default_times string[] 和 snooze_default_days。
- [x] 时间 HH:MM、1–12 不重复；天数 1–30；帮助用业务语言，保留原注释于受控技术详情；PUT 严格 {values,version}。
- [x] dirty guard、CAS 恢复；浏览器 B4 设置三路径和 B20；typecheck/build，记录 G 报告。

## Stage H — 运行状态

**文件**：services/runtime.ts、hooks/useRuntime.ts、features/runtime/model.ts、pages/runtime/RuntimePage.tsx、app/RuntimeIndicator.tsx。

- [x] 读取旧 RuntimePanel、snapshot 五阶段真实字段；建立保守汇总 mapping，未知不变绿。
- [x] 顶部需要处理、五阶段业务结论和 Collapse 技术细节；30s 只更新指示/提示有更新，阅读页由用户刷新。
- [x] 飞书重发确认含接收组/摘要，登记送达要求 message ID；关闭中断批次保留核账和版本；未确认发布 count 说明不能直接定位并提供维护说明。
- [x] 验证 mapping 和三个恢复请求 body/确认；typecheck/build，记录 H 报告。

## Stage I — 全量行为与新旧请求对照

**文件**：tools/browser_regression.py、tools/network_compare.py（如需独立脚本）、已有 fixture/capture 工具的必要扩展，回归证据 JSON。

- [x] 原有 415 测试保留，npm test/typecheck/build；旧 Vue build；tools/test_offline.py 原 65 脚本全量一次。
- [x] 实际 fixture 浏览器覆盖 B1–B24，逐条保存结果；网络 body 不省 revision；回归外部调用为 0。
- [x] 同 fixture/操作定义跑 Vue 和 React：edit/save、snooze、wake、skip、handoff、tags、export、approve stub、calendar failure、settings、jobs，记录 method/path/body keys/关键值并解释允许差异。

## Stage J — 视觉 QA 与交付

**文件**：tools/capture_final.py（复用 capture_baseline/capture_shell 的量值）、screenshots/final-* 与 final-measurements.json；现有迁移/视觉/架构文档及 FINAL_IMPLEMENTATION_REPORT.md。

- [x] 所有正式页面 1366×768 和 1920×1080 截图并检查；review/history ≥12/18、48 行高、分页可达；详情 ≤1.5 屏、primary 1、行内 danger 0、bordered ≤3。
- [x] 核查 focus enters/trap/Esc/returns、overlay 快捷键抑制、zh-CN、每页 h1=1、reduced motion 和技术细节不常驻。
- [x] 仅修验证发现且影响需求的缺口；更新计划完成状态和有证据的视觉/架构调整。BASELINE 不改。
- [x] FINAL_IMPLEMENTATION_REPORT.md 完整 A–U：精确测试数、B1–B24 表、请求差异、两分辨率各页指标、文件/依赖/API/数据/Git、DEF 项、只写 cutover/rollback 步骤和三选一建议。
- [x] 保留两份 dist、生产配置不变，无外部真实操作、无 Git 提交。完成后输出用户指定结束语。
