# 2026-09-12 开发机集成记录

**记录状态：本文件是 2026-09-12/13 的历史集成记录。全量 65 脚本与相关补跑、当时的 Vue 构建和最终 7 场景浏览器回归已有通过证据；远端排期图片适配与真实业务闭环仍未完成。** 这些结果不能改写成 2026-09-14 React 单目录版本的验收；当前进度与新结果统一写入 [OPTIMIAZATION.md](OPTIMIAZATION.md)。功能契约和全部原编号仍在 [FUNCTIONALITY.md](FUNCTIONALITY.md)。

本轮已接续并核验原运行数据，真实读取历史与月历；离线浏览器回归使用当时的 Vue 构建和临时 ASGI 服务。没有在本轮调用真实模型、发送飞书消息或提交新的 FB/IG 发布/排期。发布编辑器曾放入明确不可发布的技术长文案与 2 张历史原图用于控件观察，可能留下未保存或自动保存草稿，不能声称没有远端写入。两篇单渠道排期和运营从飞书到审校排期的完整流程尚未验收。

本记录的代码截至 `b1a56bb`，核心集成为 `565f17c`，包含此前 `829cbd2` 的主体集成、`afb6784` 的兜底过期修复和 `923091f` 的终态 outbox 归档；`b1a56bb` 仅把 run_web 默认入口统一为 `127.0.0.1:8765` 并保留 CRLF。随后文档提交只同步事实，两个计划文件由根任务独立维护。

## 1. 运行数据与身份边界

| 项目 | 已确认事实 | 状态 |
|---|---|---|
| 原数据核验备份 | worktree 内 `state/runtime-backups/20260912T100016Z/runtime.zip`；5,246 文件、465,064,677 字节；文件与备份一致性已核验 | 真实通过 |
| 同一运行绑定 | 忽略入库的 `config.local.toml` 指向原 archive、state、本机环境文件和原解释器；没有用空 worktree 状态重新开始 | 真实通过 |
| 旧激活边界 | `2026-09-03T09:00:58.277710Z` 保留，旧发布引用和账本未清空 | 真实通过 |
| 历史归档 | 共 1,067 篇：FB 47、冻结 `in_neakasa.tech` 1,020；SQLite 与本地文件实际一致 | 真实通过 |
| 真实历史查询 | 第 1/2 页按 20 条分页读取，页间未重复；2020-09-03 冻结 `.tech` 帖 2389976088430547749 直接详情 read_only=true，明确超过 90 天 | 真实通过 |
| 账本与激活保留 | 对原 10 份 .jsonl/activation 文件与备份核对 SHA，06:58:19Z 再核全部未变；证据在 worktree state/integration-20260913/historical-ledger-preservation.json | 真实通过 |
| 新 `.global` 基线 | 尚未首次人工回填；旧 `.tech` 内容不能代替新目标覆盖 | 待真实联调 |
| 三个 Chrome | 9222 回填、9224 探测、9223 发布均已启动；发布德国账号已确认 | 真实通过 |
| 探测会话可用性 | 9224 访问 `.global` 返回 HTTP 429，原 `delta_state.json` 已写 `detect_hard_blocked`，未重试 | 待真实联调 |
| 当前本机 Web 入口 | 127.0.0.1:8765 当前 worktree API 已后台启动、绑定原数据；07:13:27Z 首页/history limit20/2020 年冻结详情/runtime 四个 GET 均 200，冻结详情与 runtime 只读 | 真实通过 |

“三个 Chrome 已启动”只证明角色与进程存在，不证明三个会话都可抓取。探测恢复要先人工核对账号和出口，保留 C7 停机事实。原数据接续通过也不证明 `.global` 抓取、真实付费或新发布已经通过。

原运行 preflight 已再次只读执行并 exit 0，五阶段原始 status 依次为 blocked、available、not_observed、disabled、available；第五阶段的控件可用，但 FB/IG 的 acceptance.verified 均为 false。exit 0 表示本次状态查询完成，不能解释成五阶段业务可运行或真实发布已验收。证据在 worktree state/integration-20260913/preflight.json 和 runtime-status.json。

本文件中的 `state/...` 默认指绑定后的原运行 state；上述备份与离线浏览器输出另注明为 worktree 内文件。凭据和本机 `.env` 内容不属于共享证据。

本机 Web 服务留供查看，启动时 PID 为 18940；进程可能随后变化，以 worktree `state/integration-20260913/web-process.json` 与实际运行状态为准。`real-web-smoke.json` 保存这次四个 GET，未调用 POST、模型、通知或批准。服务只绑定 127.0.0.1，本次不证明运营网络可达或企业闭环。

本轮另实际只读执行 `python -m tools.schedule scheduler-status`，退出码 0，结果为 `FBScraperScheduler: 未注册`。常驻调度器尚未安装；Web 启动不代表监测已常驻运行。该查询不改变 Windows 任务状态，安装/停用/恢复仍按阶段一的真实验收项执行。

## 2. 五阶段代码与真实证据

| 阶段/验收单元 | 本轮已完成的代码或证据 | 状态 | 仍需取得的证据 |
|---|---|---|---|
| 一：月度监测、整批预算、调度生命周期 | 上个完整月样本/覆盖、不足保持计划、跨两平台整批截止预算、前移兜底、独立模型执行器、持久批次与旧任务冲突防护 | 离线通过 | `.global` 回填、实际会话和任务安装/停用/恢复 |
| 一：处理与晨间就绪指标 | `requested_at → content_ready` 的 P50/P95、样本数与晨间就绪率，缺数据返回未知；5 项定向测试通过 | 离线通过 | 当前数据上的连续批次观察；源帖发布至待审的端到端指标另列，不能混称 |
| 一：源帖发布至首次待审指标 | `source_created_at`、`post_content_ready` 按来源首次就绪输出独立 `published_to_ready` P50/P95，含发现等待；runtime_status 6 项含此指标通过 | 离线通过 | 真实新帖的完整时间事实；旧数据不补造 |
| 一：兜底中途过期 | afb6784 保留该平台普通探测，只有实际深扫才合并；过期跳过记技术/晨间告警，按日期/平台去重 | 离线通过 | 真实晨间批次的耗时、跳过与消息观察 |
| 二：严格读取与恢复 | 损坏审校账本失败闭合，模型/批准中断恢复，源图实际字节/数量/顺序指纹，统一冻结版与幂等投影 | 离线通过 | 真实新内容和真实排期回执的冻结一致性 |
| 二：统一来源文字摘要 | source_text_sha256 全链路统一并加 AST 守卫；风险 raw scan_text_sha256 保留高亮偏移，最终提交字节指纹不改 | 离线通过 | 原历史哈希不造假变化；真实新样本继续留证 |
| 二：历史与索引 | 原 1,067 篇文件/SQLite 一致，历史分页/旧详情和冻结只读已真实读取 | 真实通过 | 持续运行后的索引新鲜度与业务操作联合观察 |
| 二：周期备份与独立证据版本 | 主配置与不可重建 state 账本按周期封装，大型媒体/probe/截图按路径及 SHA-256 单独留版本；不重复把大图塞进每个周期账本包 | 离线通过 | 企业云盘上传/移动/版本/冻结补送与周期恢复实测 |
| 三：风险与三类标签信号 | 风险在翻译前扫描，来源/提示词绑定；Trends DE 同组 CSV、IG 全球累计、德语同类账号周更；C7 停机及 count 结构证据修复已复审 | 离线通过 | 真实风险请求/usage 和三来源实际采样，不能用夹具替代 |
| 三：Trends 公开导出 | `7f131a3` 的 22 项相关及 6 项 rank 通过；后续补共享锁/CAS、CSV 按钮专属 proof、BOM/CRLF 原始字节与完整摘要上下文，11 项导出测试通过，T1–T4 复审关闭 | 离线通过 | 访问恢复后被动录到真实唯一导出控件，再执行同组公开 CSV 导出 |
| 三：FB 链接和最终计数 | 正文光标插入 {{linkN}}、后端按最终 URL 替换/未插入链接末尾追加；非法 token 阻止通过，IG 不支持；/check 返回准确最终计数 | 离线通过 | 真实 FB 德国链接场景；单图待制作包不覆盖这项 |
| 四：通知、镜像与恢复 | 模型、标签周采样、远端镜像/消息 I/O 均不占调度主循环；飞书按帖聚合、两个接收组、固定尝试消息与人工 resolve，多收件人部分失效漏发已复审修复 | 离线通过 | 应用凭据、两组、云盘根目录、可达审校链接、真实投递及外部缺席告警 |
| 四：终态 outbox 归档 | 923091f：默认 30 天，完整关联终态组件按 SHA 归档，原 card/UUID/回执可读，主状态留事件去重；未决/待重试/待发/离岗未发保留 | 离线通过 | 长期真实投递和归档副本读取，不删除未结业务 |
| 五：真实月份只读读取 | 实际读取在 `2026-09-13T05:01:33Z` 返回 ready：35 日期格，范围 8 月 30 日至 10 月 3 日；4 条公开帖、2 个推荐时段，含 3 个独立 IG remote ID | 真实通过 | 新 scheduled 卡片形态、长文/多图/链接/CTA、两篇实际单渠道排期回读 |
| 五：单渠道/正文/身份/时刻回读 | workflow/planner_cache/engine 直接接 month_inventory/month_readback，精确全文、完整月份/日期格、唯一渠道/remote ID 因果；资产/双时间等 P1/P2 复审关闭、15 定点通过 | 离线通过 | 新受控排期实际正文/渠道/时刻/ID；当前录证窗口变动需重录 |
| 五：远端 scheduled 详情图片读取 | 适配器尚未实现，缺实际 scheduled DOM 图片控件证据；受控排期录证后补数量/顺序/来源绑定与失败闭合 | 代码未完成 | 有效德语图、具体样本/提交确认和真实详情控件；不是单纯权限阻塞 |
| 五：G8 完整媒体验收闸 | 全文相等、remote_images_verified、有序媒体 source SHA/正整数数量与冻结清单一致，严格 dict/list 类型；旧 scheduled 保留防重 | 离线通过 | 完成远端图片适配及真实证据前，G8 不因编辑器图或正文/ID 通过而放行 |
| 五：本次编辑器媒体准备 | `2026-09-13T06:01:17Z` FB-only 技术草稿的 2 张 1536×2048 原图，数量/顺序与视觉一致性核验通过，未提交 | 真实通过 | 仅限编辑器准备；真实德语业务图和远端 scheduled 图片另验 |
| 八批总体验收中的 Web 回归 | 实际 Vue dist + 临时真实 ASGI，初版 6 条，后续新增 FB 光标链接后最终新 dist 的 7 场景通过；入口已入库 | 离线通过 | 真实运营闭环另验；本轮最终完整批/补跑见第 6 节 |

阶段四的离线通过覆盖代码行为，不代表飞书或云盘已经上线。阶段五真实通过只限本次实际月历读取、公开观察和技术草稿编辑器媒体准备，不能据此升级新单渠道 G8。远端排期详情图片适配是具体尚缺代码，需真实控件证据后继续，不能改列成已经实现但只差权限。

## 3. 当前平台停机与人工依赖

Google Trends 公开页面只读访问已返回 HTTP 429，原 `state/trends_export_state.json` 为 blocked，本轮已停止。Instagram `.global` 的 429 也已持久记录。两个来源都不继续刷新试探，不通过换入口或清空文件绕过停机。

Trends 新适配器先验证同一英文标签的 2–5 个德语候选、`geo=DE`、统一起止日和已被动录证的精确 CSV 按钮，Sign in 或任意链接不能替代。CSV 保留原始 BOM/CRLF 字节，context 的三个 64 位十六进制摘要均必填且源文必须核对。只有人工确认访问恢复后，才能录证、持版本恢复停机并导出：先 `python -m tools.hashtag_sampling trends-status` 只读取得 revision，再 `trends-reset --reason "已人工核对" --version <revision>`，共享锁内 CAS 防止覆盖新失败。当前没有真实 CSV 导出/采样结果；导入器和测试通过不等于来源已可用。

尚缺飞书应用 Secret、德国运营/开发者两个接收组、云盘根目录授权以及运营机器可访问的审校 URL；外部心跳未启用。德语同类账号名单与稳定 bio 聚合页也按人工依赖核对。没有真实权限和目标，就不能给出有效的实际投递或运营操作验收。

## 4. 月历与发布证据的精确范围

| 证据 | 能证明什么 | 不能证明什么 |
|---|---|---|
| `state/channel_controls.json` | 当前 FB `Neakasa Deutschland`、IG `neakasa.de` 的单渠道控件与账号观察 | 新 Schedule 已提交或已回读 |
| `state/publish_probe_20260912_205317_056051.json` | 本轮 22 条事件记录 | 自动等同 G8 真实排期验收 |
| `state/planner_controls.json` | 月历日期格与条目控件来源 | 未加载内容可以被当成空档 |
| `planner_month_20260913T044722903276.png` | 当前月历观察；结合实际读取证明本次 35 格内容已读取 | 未来 scheduled 卡片的完整正文/图片回读 |
| `channel_facebook_20260913T044719316715.png`、`channel_instagram_20260913T044716773798.png` | 对应渠道当前可见状态 | 替代结构化资产 ID/remote ID 核验 |
| `2026-09-13T05:01:33Z` 实际 inventory | 4 条公开帖；2 个仅时刻项经正向 tooltip 确认为推荐时段并排除；3 个 IG remote ID 独立 | 将公开帖或推荐时段用作本次新建排期成功证明 |
| `state/composer_media_probe_20260913T054737Z_final.json/.png` | FB 单渠道编辑器技术长文案与 2 张历史原图的上传控件/缩略图数量顺序观察 | 业务候选就绪、长文完整远端回读或排期图片验证 |
| `state/composer_media_verification_20260913.json` | 2026-09-13T06:01:17Z，两张 1536×2048 原图按顺序与编辑器图比较：dHash 距离 0、RGB 平均误差 0、宽高比比值 1 | 远端发布/排期详情的图片数量、顺序或字节已确认 |

公开状态按 remote ID 和明确远端观察记录，不由当前时钟超过 scheduled_at 推算。旧 journal 中的双渠道排期是旧历史，不能充当本轮独立 FB/IG 的新 G8 验收。当前远端图片数/顺序仍未证实；本地快照保存了多少张不能替代平台回读看到多少张。

原账本另有一条 `submit_ambiguous`，本轮保留未改：attempt `d9853906-57d9-437c-9f71-4ed72d103a81`，post `3965025107383038890`，旧双渠道，时刻 `2026-09-09T10:00+02:00`，无 remote_id、无 snapshot。需人工核对原提交与平台记录；当前月历没卡不能证明当时没提交，不能据此重试，也不能把它当新单渠道证据。

编辑器中的文案明确写有“Technischer Entwurf…Nicht zur Veröffentlichung vorgesehen.”，仅为技术探查；未点击 Schedule、Publish、Finish later 或 Cancel。可能留下草稿，接手时须识别这份现场，不能当作用户批准的业务内容。媒体核验器以真实 DOM 中最近的 Remove photo 祖先确定每张缩略图，修复了嵌套容器重复计数后，与冻结原图作视觉比较并通过上述实测；这不是远端排期详情读取，不能升级为新发布或排期成功。

## 5. 联调样本准备结果

Facebook 旧机器译文的 prompt_version 为 5，当前 PROMPT_VERSION 为 6；当前读取没有可接受的德语图，原 FB 目录没有发现 media_de。实际只找到冻结 `.tech` 的 3 张历史 media_de（8 月 31 日、27 日、16 日），不作 FB 或新 `.global` 素材。这些证据不能证明“FB 已存德语图只是因版本失效”或“原 FB 德语图未丢”。`stale=false` 只说明原文没变，API 的 `machine_current`、`machine_prompt_version`、`current_prompt_version` 分别说明有效性与版本。本轮没有自动重做，原账本与激活另有备份哈希未变证据。过期 8 月活动和美元促销也不能因已有译文就作为当前德国站可发布内容。

已经准备一份具体 FB **待制作包**：worktree `state/integration-candidates/facebook-122120460231379375/`，含 README、manifest、`caption-de-proposed.txt`、`image-translation-proposed.txt`、英文原文和 `source-01.jpg`。来源为 2026-08-16“Cat or CCTV”单图，目标 Neakasa Deutschland，拟 2026-09-15 10:00 柏林，未占位。德语正文/语义标签提案和图片文字替换指令可审阅，最终德语图尚未生成；没有改原归档、没有付费或提交。历史来源须先确认作为受控样本和图片付费许可，图片完成后整包再确认提交；该包不覆盖 FB 短链、IG bio 或真实多图验收。

Instagram 还没有 `.global` 首次回填的新素材，冻结 `.tech` 不作为新来源替代。当前仍没有两篇已经齐备、可交用户确认提交的最终联调包。

后续每篇须明确来源/版本、当前德语正文、全部最终图片及顺序、FB 德国链接或 IG CTA、唯一渠道、目标账号和柏林时刻。准备完成后由用户确认，再分别提交一次 FB-only/IG-only 并回读。最后让运营从真实飞书卡片进入审校、修改检查、选期确认并收到排期回执；两次独立脚本提交不替代这一流程。

## 6. 离线浏览器与最终验证

已入库的浏览器入口为 `tests/tests_browser_workflow.py`，夹具为 `tests/browser_fixture.py`。在 worktree 根目录构建并运行：

```powershell
npm.cmd --prefix web/ui run build
scripts\run_python.bat tests/tests_browser_workflow.py -v
```

`42c8bc9` 对实际 Vue 构建运行 6 条测试，保存到 worktree `state/offline-browser-20260913T052052Z-16480/`。人工文案/本地化保存、来源冲突、历史分页/冻结只读、设置 CAS/原说明使用真实后端与临时文件；运行恢复、消息未知、模拟排期/公开状态使用明确浏览器 API 夹具，仅证明 UI 行为。没有调用真实 approve、模型或外部消息，未使用原业务数据做测试写入。

后续浏览器补 FB 光标插入 {{linkN}} 与替换后准确计数，当前 7 场景有通过证据。历史交接最后补齐后的两批各 4 脚本通过：worktree state/offline-validation-20260913T064652Z（browser/pipeline_service/publication_recovery/risk_scan）与 064517Z（hygiene/localization/review_notifications/web_review）。这些是定向结果，不替代最新全部 65 脚本的集成运行。

每次回归记录构建 JS 哈希、截图、断言、页面错误、阻断请求和允许的临时写请求。版本化全量入口 `tools/test_offline.py` 给各脚本独立 archive/state/环境和日志；它不使测试结果自动变成真实集成结果。

最终验证按“全量 + 相关补跑”记录，共 65 个不同 Python 脚本均有当前集成所需的通过证据；没有把首轮写成单次 65/65：

| 证据目录（均在 worktree state 下） | 结果与适用范围 |
|---|---|
| offline-validation-20260913T065156Z | 全量执行 65 脚本，64/65；唯一 tests_feishu 是新增测试 read_text 未指定 encoding，在默认 GBK 下失败，业务代码的 UTF-8 读写无此缺陷 |
| offline-validation-20260913T065631Z | 测试编码修正后，feishu/review_notifications/runtime_recovery 3/3 通过 |
| offline-validation-20260913T065531Z | 风险原始/规范摘要及 IG CTA 占位符复审修复后，hygiene/localization/risk_scan/web_review 4/4 通过，相关审查关闭 |
| offline-validation-20260913T070142Z | 最终新构建之后，browser/feishu/hygiene/localization/risk_scan 5/5 通过 |
| offline-validation-20260913T071023Z | run_web 端口补丁后 hygiene/runtime_config 2/2 通过；真实入口 --help exit 0，CRLF 保留 |
| offline-browser-20260913T070144Z-3108/report.json | 实际新 dist + 临时 ASGI，7/7 场景通过，页面错误/外部请求/禁止写入错误为 0 |
| integration-20260913/final-verification.json | 本轮汇总，记录全量/补跑、构建、浏览器及保留的真实依赖 |

最终构建 `npm.cmd --prefix web/ui run build` 成功，Vite 50 modules；JS 为 `index-Dk6O8FJl.js`，SHA-256 `51bdbd35904526f8821df8ef2f8aaa3a80a206a5247f3e3f1f1e49c25de94ed1`，CSS 为 `index-BQ0IVjSz.css`。最新修改的 12 个 Python 业务模块 Ruff 与 git diff --check 通过，Ruff 结论不外推整个仓库。早期 060822Z 外部中断只有两条结果，不参与完整结论；规划时 47 脚本和更早的局部结果只保留历史用途。

本轮仍未完成的实现是远端 scheduled 详情图片适配，须受控样本排期录证后继续；`.global` 回填、真实模型/三来源采样、企业飞书/云盘/心跳、具体两篇排期与运营完整流程均按清单保留。测试通过不改变这些状态。
