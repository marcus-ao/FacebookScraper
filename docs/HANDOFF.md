# 项目交接

**交接记录：2026-09-12 成文，事实同步至 2026-09-14；规划基线 `c67c56a`，当前主干 `3718c0d`。** 业务功能以 [FUNCTIONALITY.md](FUNCTIONALITY.md) 为准，每个验收单元的状态以 [REQUIREMENTS §10](REQUIREMENTS.md#10-五阶段验收状态) 为准；**本文件管的是边界与证据**——哪些是红线、真实 UI 长什么样、踩过什么坑、哪份证据能证明到哪一步。

## 1. 当前工作区事实

主干 `main` 就是日常工作的地方，代码截至 `3718c0d`。

**数据绑定。** 主干没有 `config.local.toml`，它直接按 `config.toml` 的 `[paths]` 读同目录下的 `archive/` 与 `state/`——**这两个目录里就是真实生产数据**，打开审校台、点保存都会落到真实账本。副本工作区（如 `.worktrees/business-workflow`）通过忽略入库的 `config.local.toml` 指回这同一份数据，所以「在副本里跑」不等于「跑在空数据上」。

**归档现状。** 原激活 `2026-09-03T09:00:58.277710Z` 保留。归档 1,067 篇为 FB 47 + 冻结 `.tech` 1,020，SQLite 实际记录/字段一致，历史第 1/2 页各 20 条且无重复，冻结详情已真实只读访问。核验备份 `runtime.zip` 共 5,246 文件、465,064,677 字节，含运行配置核验，凭据不进证据报告；原 10 份账本/激活与备份 SHA 于 06:58:19Z 再核未变。**这些不是 `.global` 回填基线**，不能重新 activate 或清空发布历史。

**审校台。** 2026-09-14 生产已从旧 Vue 切到 React（`web/ui-next/`），挂载点是 `config.toml` 的 `[paths].web_dist`。

> ⚠️ **这一行改动目前还没提交。** `git checkout -- config.toml` 会让审校台悄悄退回旧 Vue（缺省值是 `web/ui/dist`），而页面不会报错。切换语义、回滚步骤和冒烟清单在 [MANUAL_STEPS 第 13 节](MANUAL_STEPS.md#13-审校台前端的切换与回滚)。

**浏览器会话。** 三个 Chrome 均已启动，但「启动」只证明进程和角色存在，不证明会话可抓取：

- 9224 访问 `.global` 真实 HTTP 429，`delta_state.json` 已记 `detect_hard_blocked` 与失败；Google Trends 公开页同样实际 HTTP 429，`trends_export_state.json` 为 blocked。两者均已停止，**先人工核对会话与出口**，不清空状态、不连续请求。
- 9223 已登录并观察到目标 FB `Neakasa Deutschland`、IG `neakasa.de`。2026-09-13T05:01:33Z 生产月历读到完整 35 格、4 条公开帖与 2 个明确推荐时段，含 3 个独立 IG remote ID。尚无本轮新发布/排期提交。
- 发布浏览器里曾放入明确写有 "Technischer Entwurf…Nicht zur Veröffentlichung vorgesehen." 的技术长文案和 2 张历史原图，仅探查编辑器/缩略图数量顺序；未点 Schedule、Publish、Finish later 或 Cancel。**可能留有未保存或自动保存草稿**——不把它当业务候选，也不声称没有远端写入。

**外部依赖缺口。** 未调用真实模型或发送飞书消息。飞书缺 AppSecret、两个接收组、云盘根目录与运营可达的审校 URL；镜像与外部心跳未启用，运营从飞书到排期尚未验收。只读 preflight exit 0，但阶段仍有 blocked/not_observed/disabled，两渠道 `acceptance.verified=false`。

**一条挂着的旧账。** attempt `d9853906-57d9-437c-9f71-4ed72d103a81`、post `3965025107383038890`，2026-09-09 10:00 +02:00 双渠道，无 remote ID 与快照。当前月历没卡**不能**判成未提交，需人工核对；不能重试，也不计入新 G8。

**接手前先看 `git status`。** 主干与副本工作区都可能有未提交修订，只提交自己范围，不覆盖别人的改动。

## 2. 红线

1. 不自动登录。人在三个专用 Chrome profile 登录，代码只附着。
2. 不自动滚历史。回填由人滚；增量和兜底对账始终受 C7 深度、频率、会话和失败预算约束。
3. 抓取、探测、发布三种身份分离：9222 / 9224 / 9223。不要恢复旧“两浏览器”设计。
4. Facebook 与 Instagram 是独立车道，不配对、不合并、不沿用 composer 默认双选。
5. 所有 Business Suite 定位和成功判据必须能回查真实 probe dump 的交互/快照。截图见到文字不等于存在可定位元素。
6. 人工文案、人工图片和人工决定不能被重建、重译或后台任务覆盖。
7. 付费请求必须经过统一预算和来源许可。不确定计费先核账，不自动重放。
8. 发布前先冻结并展示具体文案、图片、账号、单一渠道和时刻，等用户确认后才能提交。
9. `review_items.jsonl`、`paid_requests.jsonl`、`published.jsonl` 的坏数据必须失败闭合。不得把损坏当空文件继续运行。
10. 凭据只在本机配置，不写日志、dump、截图或版本库。
11. 出口类型和稳定性是 F1-8 预检要求，展示 ASN、近期出口变化、采样时间/过期/未知；不把旧网络提醒当当前合格证据。

## 3. 架构与真相源

```text
浏览器响应/人工回填
        ↓
post.json + 原图
        ↓
机器译文/图片/付费账本
        ↓
人工文案 + 本地化选择 + review_items.jsonl
        ↓
不可变发布快照
        ↓
Business Suite intent / 回读 / published.jsonl

manifest / SQLite / HTML / Planner cache / 飞书云盘
        ↑ 只能由上述本地真相派生
```

对账器只读各阶段真相并调用阶段入口，不建立发布队列，也不自己解析响应、拼模型提示词或点浏览器。`web/` 可以依赖核心层，核心层不得依赖 `web/`。

`published.jsonl` 不可重建。它和激活边界共同阻止旧帖被再次提交。云盘是单向镜像，不是恢复时的自动反向数据源。

## 4. 当前关键缺口

逐项状态在 [REQUIREMENTS §10](REQUIREMENTS.md#10-五阶段验收状态)，代码与真实验收分开列。下面是摘要，不沿用旧缺口快照：

- 已补严格审校账本读取、模型恢复、源图内容指纹、冻结快照/恢复投影、历史分页/旧详情、SQLite 实际一致性和 runtime binding；原历史/索引读取另有真实证据，最终全量加相关补跑已通过。
- scheduler 已有 install/status/disable/enable；月度样本/覆盖不足保留、双平台整批截止预算、模型/标签周采样/远端 I/O 独立执行器已有定向证据。批次 operation_id 与付费成本关联，CAS 恢复不重发付费请求。实际安装/停用/恢复未验收。
- 处理 P50/P95 和晨间就绪率、源发帖至首次待审的 source_created_at/post_content_ready 关联已通过 6 项 runtime_status 测试，源发帖指标含发现等待，不为旧数据补造事实。
- 风险路径与三类采样已有离线验证。IG 采样遵守 C7 持久停机、仅接受明确同名 hashtag 容器的累计 count。Trends 新增 CSV 专属被动控件、完整摘要上下文、BOM/CRLF/周月解析；恢复共享锁并要求 `trends-status` 的 revision。新增 4 项审查修复的 11 项导出测试通过，T1–T4 复审已关闭；真实 CSV/模型/平台采样仍未验收。
- 设置只开放两项 CAS，原注释/受控说明已经展示，版本化浏览器对保存与冲突做了临时真实后端验证。旧 FB 机器译文 prompt 5、当前 prompt 6；当前无可接受德语图，原 FB 目录未发现 media_de。找到的 3 张历史德语图均属冻结 `.tech`，不作 FB 或新 `.global` 素材。`stale=false` 与 `machine_current` 分开解释，不从版本差异推断图文件历史或自动重译。
- 飞书按帖聚合/两组/晨报/有效首图与摘要、原卡冻结/人工 resolve、多收件人部分失效漏发和镜像周期包/大证据独立版本已有离线验证。有效原卡补送保留 UUID；部分失效保留旧 cancelled 卡，只给漏收者生成仍有效的新提醒。企业投递和恢复仍待权限。
- 完整月历生产读取本次已真实通过，公开观察按 remote ID，不按时钟；生产上层直接使用 month_inventory/month_readback，旧 Business Suite 接口仅保留兼容。新提交全文/唯一渠道/资产/remote ID/时刻因果和窗口/DST 已离线验证、P1/P2 复审关闭。远端 scheduled 详情图片适配器仍未实现，须受控排期获取真实控件后补齐；编辑器缩略图检查不替代它。G8 要求全文相等、远端媒体验证及有序 source SHA 与冻结清单一致，旧 scheduled 仍防重。
- 第八批 API 已接历史 range/page/total、来源/快照指纹、风险/三来源元信息、恢复/设置和 Web/CLI 共用五阶段只读状态。版本化浏览器已新增 FB 链接插入/准确计数，最终新 dist 的 7 场景通过，输出为 state/offline-browser-20260913T070144Z-3108/report.json。
- 历史交接六项已逐项补上：canonical source_text_sha256/AST 守卫，风险 raw scan_text_sha256 保留偏移；补扫中途过期仍跑普通探测并给技术/晨间告警；付费恢复；outbox 默认 30 天完整终态组件按 SHA 归档且主状态保留去重；FB 光标 {{linkN}} 与 /check 最终计数；消除重复 alias。`afb6784` 为调度过期修复，`923091f` 为飞书终态归档，最终整合在 `565f17c`；共同回归已单列。
- `.global` 回填、企业飞书/云盘/外部心跳与运营完整流程仍依赖人工会话/权限和具体发布确认。FB 待制作包 `integration-candidates/facebook-122120460231379375`（在当时开发副本的 `state/` 下），来源 2026-08-16“Cat or CCTV”单图、拟 2026-09-15 10:00 柏林但未占位，缺最终德语图/受控样本许可/整包确认；不覆盖短链或多图。IG 没有新素材，过期活动/美元促销不是可以直接提交的联调包。
- 审校台前端已整体换成 React。深链接刷新由 `web/api/app.py::SinglePageFiles` 保住，契约钉在 `tests/tests_spa_static.py`；历史页首屏缩略图成本是已知待观察项，不是回滚触发条件（数字见 §7）。

生产迁移、登录/RBAC/actor、视频、跨平台复用、`supervised` 和无人审核发布是明确延期，单独管理。

## 5. 2026-08/09 抓取侧历史事实

### Instagram

- `web_profile_info` 对登出访客已关闭，2026-08-30 首次请求即 429。
- 登录态时间线可通过真实浏览器请求拦截获得；不要硬编码每 2–4 周可能轮换的 GraphQL `doc_id`。
- 旧 `doc_id` 返回 401 且提示稍后重试，这不是限流，等待不会恢复。
- `web_profile_info` 形态不含完整轮播子项时必须标 `media_complete=false`。
- 合作帖是否在目标时间线用 `parse.on_timeline_of()`，不能用 `owner == account`。

### Facebook

- 登录态通过 CDP 附着真实 Chrome 并拦截 `/api/graphql` 响应。
- `mbasic.facebook.com` 已在 2024-12-03 宣布下线，2026 年行为不稳定，不得作为地基。

### 通用

- 媒体 CDN URL 有签名和时效，拿到响应后立即下载；不能假设以后能重抓。
- 数据中心 IP 的真实尝试曾在首次请求被拦；抓取应使用稳定住宅网络。
- 历史归档的“1051 条待译正文”不是可发布口径。2026-08/09 旧归档统计为：

| 平台 | 总数 | 有正文 | 静态图文可发 | 纯视频 | 无媒体 |
|---|---:|---:|---:|---:|---:|
| Facebook | 47 | 46 | 27 | 17 | 2 |
| `in_neakasa.tech` | 1020 | 1011 | 443 | 568 | 0 |

这些数字只描述旧归档，不能外推 `neakasa.global`。

## 6. 2026-09-01 Business Suite 真实探查

历史来源是 `state/publish_probe_20260901_054226_378622.json`：56 条可信交互、71 条被动语义快照、117 张截图。旧工作区曾用下列命令检查信号：

```powershell
scripts\run_probe_signals.bat --report state\publish_probe_20260901_054226_378622.json
```

该旧文件来自原运行目录，绑定后的入口可能可读，但它没有单渠道切换交互。下列只作 2026-09-01 历史事实；新的选择器需对应本机新录证据。

### Composer

| 要素 | 2026-09-01 观察 | 证据位置 |
|---|---|---|
| 渠道图标 | `img 'Facebook'` 与 `img 'Instagram'`，不带账号名 | 快照 24 #2/#3 |
| FB 主页 | FB 预览中的 `heading h2 'Neakasa Deutschland'` | 快照 24 #43/#44 |
| IG 账号 | 46 张 composer 快照未命中可访问名 | 全部 composer 快照 |
| 正文框 | `combobox`，富文本/contenteditable | 交互 #14–21 |
| 定时开关 | `switch`，`input type=checkbox` | 交互 #34 |
| 日期 | 每个渠道一套 `textbox 'mm/dd/yyyy'` | 交互 #37/#44 |
| 时间 | 每个渠道一组 `spinbutton` | 交互 #40–43/#46–49 |
| 提交 | `button 'Schedule'` | 交互 #50 |
| 成功 | dialog 内 `heading 'Your post is scheduled'` | 快照 50 |

`Schedule` 与 `Publish` 是不同按钮，误点后者会立即发布。两个渠道各有日期和时间控件；只设置第一组可能使另一渠道保持默认时刻。FB 主页 heading 要到图片上传后才出现，因此账号核对放在上传后、提交前。历史失败截图曾显示 IG 下拉值 `Neakasa Deutschland and neakasa.de`，但 probe 没采到可访问定位，不能仅凭截图实现定位。

### Planner

| 要素 | 2026-09-01 观察 |
|---|---|
| 可见范围 | 月份和年份是两条独立 heading |
| 月视图条目 | link 可访问名只有时刻 |
| 周视图条目 | link 可访问名含正文、完整日期和时刻 |
| 图片数 | 25 张 Planner 快照没有数量语义 |
| 渠道详情 | FB/IG 各有独立弹窗和 remote ID |

历史 probe 的 FB remote ID 为 `1887083152480681`，IG 为 `4378984725697354`。这些 ID 只属于那次测试，不能写成配置。Planner 弹窗除 Escape 关闭外不要点击；其中的 `Publish now` 会立即发布。

旧“页面出现月份 heading 就算数据就绪”曾被真实 UI 推翻：外壳先出现，卡片仍在加载。应等到可解析时刻的条目或明确空态。

### UI 上限的历史实测

| 项 | 2026-09-01 观察 |
|---|---|
| 单帖图片 | 最多 10 张 |
| 画幅比 | 4:5 至 1.91:1 |
| IG 正文 | UI 提示最多 2200 characters；旧代码曾保守按 bytes。当前已确认契约是字符计数，不能沿用 bytes 代替 |
| hashtags | 最多 30 个 |
| 定时下限 | 当时未观察到最小提前量 |
| 定时上限 | 当时日期选择器不能跨当前可见月份 |

“不能跨月”是当时 UI 事实，不是永久业务决定。每次真实联调要记录当前 UI 覆盖范围，代码需支持月度适配。

### 2026-09-12/13 本轮新增观察

- 单渠道控件：`state/channel_controls.json`；被动 v2 探查：`state/publish_probe_20260912_205317_056051.json`，22 条事件。已观察 FB `Neakasa Deutschland` 与 IG `neakasa.de`，尚无本轮新 Schedule 提交回读。
- 月历控件：`state/planner_controls.json`；最新截图：`planner_month_20260913T044722903276.png`。早期 `043415712413` 截图仅证明 35 格控件可定位；随后 2026-09-13T05:01:33Z 生产 inventory 返回 ready，范围 8 月 30 日至 10 月 3 日、35 格、4 条公开帖，含 3 个独立 IG remote ID。
- 真实详情页可由头部 Facebook/Instagram 图标、`Published on` 与合作作者信息区分渠道；未来两个仅显示时刻的项经 tooltip 确认为推荐时段，须排除于帖子/占用数。
- 本次完整月历读取已通过，仍不能外推新的 scheduled 卡片、跨月/DST 或真实长文/多图回读。读取覆盖所有格子/时刻条目、手工任务和延迟加载，不能用 shell 就绪或已读一周声明空档；公开状态无充分依据仍显示 unknown。
- 编辑器媒体探查：原 state 的 `composer_media_probe_20260913T054737Z_final.json/.png` 记录技术长文案和 2 张原图；后续 `composer_media_verification_20260913.json` 在 06:01:17Z 对两张 1536×2048 图片按顺序核验，dHash/RGB 误差 0、宽高比比值 1。未提交发布或排期，可能留下草稿；这不是有效业务联调内容，也不是远端 scheduled 图片证据。

探查与提交是不同证据。最终两篇具体内容经用户确认后，分别完成 FB-only/IG-only 排期及回读；还要覆盖长文、多图、FB 链接、IG CTA 和运营从飞书开卡片到收到排期回执的完整流程。

### 每份证据能证明到哪

**最容易犯的错是把左边一列读成右边一列。** 下表原在 `docs/INTEGRATION_2026-09-12.md`，2026-09-14 并到这里。

| 证据 | 能证明什么 | **不能**证明什么 |
|---|---|---|
| `state/channel_controls.json` | 当前 FB `Neakasa Deutschland`、IG `neakasa.de` 的单渠道控件与账号观察 | 新 Schedule 已提交或已回读 |
| `state/publish_probe_20260912_205317_056051.json` | 本轮 22 条事件记录 | 自动等同 G8 真实排期验收 |
| `state/planner_controls.json` | 月历日期格与条目控件来源 | 未加载内容可以被当成空档 |
| `planner_month_20260913T044722903276.png` | 当前月历观察；结合生产读取证明本次 35 格内容已读取 | 未来 scheduled 卡片的完整正文/图片回读 |
| `channel_facebook_*.png`、`channel_instagram_*.png` | 对应渠道当前可见状态 | 替代结构化资产 ID / remote ID 核验 |
| 2026-09-13T05:01:33Z 生产 inventory | 4 条公开帖；2 个仅时刻项经正向 tooltip 确认为推荐时段并排除；3 个 IG remote ID 独立 | 把公开帖或推荐时段当成本次新建排期成功的证明 |
| `state/composer_media_probe_20260913T054737Z_final.json/.png` | FB 单渠道编辑器技术长文案与 2 张历史原图的上传控件/缩略图数量顺序观察 | 业务候选就绪、长文完整远端回读或排期图片验证 |
| `state/composer_media_verification_20260913.json` | 2026-09-13T06:01:17Z，两张 1536×2048 原图按顺序与编辑器图比较：dHash 距离 0、RGB 平均误差 0、宽高比比值 1 | 远端发布/排期详情的图片数量、顺序或字节已确认 |

公开状态按 remote ID 和明确远端观察记录，**不由当前时钟超过 `scheduled_at` 推算**。旧 journal 里的双渠道排期是旧历史，不能充当本轮独立 FB/IG 的新 G8 验收。当前远端图片数与顺序仍未证实——本地快照保存了多少张，不能替代平台回读看到多少张。

媒体核验器以真实 DOM 中最近的 `Remove photo` 祖先确定每张缩略图，修复了嵌套容器重复计数后才与冻结原图作视觉比较。这是编辑器侧的准备证据，不是远端排期详情读取。

## 7. 审校台切换后实测到的两个数

2026-09-14 从 Vue 切到 React 之后测的，都留在这里免得下次再测一遍：

- **历史页首屏缩略图成本。** 隔离夹具（63 篇）上是每张 0.03–0.11 秒、50 行首屏约 3.9 秒；换到生产归档（1,067 篇）直接向运行中的宿主取 8 张，中位 1.57 秒、最大 2.0 秒——按每源六连接算，首屏约 12–13 秒。成本随账号目录规模走（`assert_physical_direct_path` 要遍历它），所以夹具数字注定偏乐观。文字行仍然立刻出来，页面可用，**不是回滚触发条件**；但"每页 20 条"这个缓解手段比夹具数字显示的更值得做。
- **一个量不准的陷阱。** 缩略图是 `loading="lazy"`，没有被绘制的标签页发出的图片请求数为零。`document.visibilityState === "hidden"` 时 38 秒内什么都没加载——看起来像卡死，其实不是。**要量就从前台窗口量，或者直接取 URL。**

## 8. 内容安全规则

- 优惠码、品牌、型号、@提及、合作方水印/署名和配置中的保留词不改。
- 图片中的数值和单位不自动换算；不确定时保持原样。
- 图片和正文共用同一术语表。
- 没有当前版正文译文时，不开始图片本地化。
- 确定性错误与模型风险提示分开展示。风险夹具不得混入真实任务。

## 9. 工作协议

动手前读取本文件、[FUNCTIONALITY.md](FUNCTIONALITY.md) 和相关模块测试。先用离线输入复现，再决定是否需要真实浏览器。任何真实发布前都准备具体内容给用户确认。

验证报告必须写清：运行了什么、使用的是离线夹具还是真实账号、通过日期、哪些外部依赖仍未联调。不得用测试名称或 mock 回执替代真实证据。

## 10. 被移走的材料在哪

主干只留承重的文档。下面这些被删过，但**都还能取回来**——用 `git show <提交>:<路径>` 即可，不需要恢复分支。

| 材料 | 删于 | 怎么取回 | 里面有什么值得回头看的 |
|---|---|---|---|
| 权威长版文档（`FUNCTIONALITY.md` 1513 行、`HANDOFF.md` 518 行、`CONTEXT.md` 145 行、`REQUIREMENTS.md` 496 行、`OPTIMIAZATION.md` 453 行、`MANUAL_STEPS.md` 469 行、`web/DESIGN.md` 462 行、`README.md` 342 行） | 2026-09-12 `2ec1fae` 精简 | `git show 0eeb099:docs/FUNCTIONALITY.md` | 业务访谈原话、13 条被推翻的旧决定、配置键的「安全 / 运营 / 业务决策」三分、47 篇发帖时刻直方图（83% 落在离岗窗，这是「在岗窗 / 离岗窗」命名的全部依据）、S0 三条硬前置与依赖图、刻意接受的代价 |
| UI 重构全过程材料：`docs/ui-refactor/` 187 个文件 5.5 MB（其中 4 MB 截图）——审计、阶段报告、浏览器与网络取证 dump、审计期的抓取/脱敏脚本 | 2026-09-14 `4b5e68e` | `git show 227ddba:docs/ui-refactor/<文件>` | 从审计到切换的完整纸面轨迹 |
| `docs/CONTEXT.md`（术语表）、`docs/OPTIMIAZATION.md`（五阶段清单）、`docs/INTEGRATION_2026-09-12.md`（集成记录）、`docs/superpowers/plans/` 四份执行计划 | 2026-09-14 本次合并 | `git show 3718c0d:docs/<文件>` | 术语表已并入 [FUNCTIONALITY 附录 A](FUNCTIONALITY.md#附录-a-术语表)，五阶段清单已并入 [REQUIREMENTS §10](REQUIREMENTS.md#10-五阶段验收状态)，集成记录的结论已并入本文件 §4 与 §6；被丢下的是逐个证据目录的文件清单和已完成计划的任务勾选 |

**UI 重构的承重部分没有跟着删**，它们被搬到了引用它们的地方：测试工具进 `tests/`（`browser_regression.py`、`network_compare.py`、`cutover_rehearsal.py`、`review_probe.py`、`history_thumbnail_cost.py` 及配套 `ui_fixture.py`、`audit_fixture_host.py`，产物改写到已 gitignore 的 `state/ui-regression/`），参考文档进 `web/ui-next/`（`web/ui-next/src` 里有 107 处注释引用它们），操作性知识进 [MANUAL_STEPS 第 13 节](MANUAL_STEPS.md#13-审校台前端的切换与回滚) 和 `web/README.md`。
