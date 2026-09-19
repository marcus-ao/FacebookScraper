# 项目交接

**交接记录：2026-09-12 成文；存储、同群四机器人与阶段一实施同步至 2026-09-15（§1.1–1.3），图片、分平台本地化、整体复审、自动部署与局域网访问同步至 2026-09-16（§1.4–1.8、§1.10），排期整合及服务机网络配置同步至 2026-09-17（§1.9–1.10），Facebook 作者身份与图片空目录修复同步至 2026-09-18（§1.13–1.14），其余现场记录截至 2026-09-14。** 业务功能以 [FUNCTIONALITY.md](FUNCTIONALITY.md) 为准，每个验收单元的状态以 [REQUIREMENTS §10](REQUIREMENTS.md#10-五阶段验收状态) 为准；**本文件管的是边界与证据**——哪些是红线、真实 UI 长什么样、踩过什么坑、哪份证据能证明到哪一步。

## 1. 当前工作区事实

**2026-09-14 运行数据已整库清空，项目处在"从零逐阶段打磨"的起点。** 下面描述的是清空之后的真实状态，不是历史记录。

**数据绑定。** 主干没有 `config.local.toml`，直接按 `config.toml` 的 `[paths]` 读同目录的 `archive/` 与 `state/`。2026-09-16 复核：归档仍为 0 篇，审校、付费、发布、采集与处理批次的业务账本及激活边界均未建立；`state/` 已保存后续各轮离线测试证据，不能再按“空目录”删除。副本工作区可能用忽略入库的 `config.local.toml` 指回同一份数据，所以「在副本里跑」不等于「跑在空数据上」——接手前先看那个文件指向哪里。

**这意味着什么：**

| 项目 | 清空后的状态 |
|---|---|
| 归档 | 0 篇。FB 47 + 冻结 `.tech` 1,020 已删除，原图 CDN URL 带签名有时效，重抓不回来 |
| 激活边界 | 未激活（`state/pipeline_state.json` 已删） |
| 发布账本 | 空。原 45 条尝试记录（含 18 条可能已提交）已删，防重保护随之消失 |
| 付费账本 | 空。原 12 行、3 笔共 US$0.274 已删 |
| 持久停机记录 | 已删。IG `.global` 与 Google Trends 的 HTTP 429 停机标记不复存在，新访问状态缺失时拒绝浏览器访问，必须先显式初始化；删除旧记录不授权新的访问 |
| G6/G6c 发布闸 | 全关。`[publish].ui_probe_dump` 与 `ui_constraints_verified` 已退回未签字状态，见 [MANUAL_STEPS 第 8 节](MANUAL_STEPS.md#8-录制单渠道-business-suite-证据) |

**审校台。** 只有 `web/ui/` 一套 React + TypeScript 应用，`config.toml` 的 `[paths].web_dist` 指向 `web/ui/dist/`。清理后依赖与构建产物须按 [Web 构建说明](../web/README.md#构建与启动) 重建，不入版本库。本地 `config.local.toml` 只接续 archive/state，不选择前端。

**浏览器会话。** 三个 Chrome profile 在 `~/.fbscraper-*`（家目录，不在仓库内），清库没有动它们，登录态应仍在。登录态是否有效仍须人在对应 profile 核对。2026-09-14 删除了旧 429 记录，当前没有可复核的持久平台阻断证据。

**外部依赖缺口。** 用户已在同一业务群创建检测、爬取、发布、状态告警四个 webhook 机器人，并确认分工（[FUNCTIONALITY §4.4 F4-1/F4-2](FUNCTIONALITY.md)）。**2026-09-15 用户已在群里收到四个机器人的自检响应**——通道可达，但这不证明真实探测往返、运营可见性或审校链接可点。运营可达的审校 URL 仍待核验，`127.0.0.1:8765` 不能作为其他机器的卡片入口。

⛔ **云盘镜像本轮明确延期**（2026-09-15 业务决定，见 [REQUIREMENTS §9](REQUIREMENTS.md#9-明确延期与固定边界)）。`[mirror].enabled = false` 是这个决定，不是缺口；代码、目录规则与恢复路径都已离线验过，不要去补实现。**它留下的敞口是本轮没有异地备份**，而 `state/published.jsonl` 不可重建——按 [MANUAL_STEPS §1](MANUAL_STEPS.md#1-接续运行数据前先备份和核验) 由人定期外拷。外部心跳同样未启用；模型和企业流程仍待联调。

**接手前先看 `git status`。** 只提交自己范围，不覆盖别人的改动。

### 1.1 原始帖子存储实施现场（2026-09-15）

本次从 `main b400c030332f4596fec1dd3bcc1f5f3d4f7512b1` 创建分支 **`codex/raw-post-storage`**，当时在独立 worktree 里实施，用忽略入库的 `config.local.toml` 单独绑定该 worktree 的 `archive/`、`state/`、`.env`，只复用主工作区的 `.venv/Scripts/python.exe`。测试未绑定主工作区真相源。**该 worktree 已于 2026-09-15 清理，证据迁至主检出 `state/`**；分支内容此前已并入 `main`。

本轮边界是“抓取结果 → 本地原帖 → SQLite/飞书派生 → 可见状态与恢复”，对应原五阶段里的 F2 存储部分。监测预算、人工登录、付费处理与发布闸沿用既有契约；整体业务上线仍须逐阶段验收。

| 层 | 本次实施重点 | 证据边界 |
|---|---|---|
| 本地 | 新目录北京时间、规范型号别名及分类来源、完整图像解码/实际 SHA、原子落盘、旧字节/历史保留、带意图记录的分类移动及恢复 | 临时目录内验证；没有新抓取真实 FB/IG 帖子 |
| SQLite | 版本 2 三表、实际列/关系/JSON/媒体字节校验、候选库验证后替换、坏媒体单项降级、旧目录月份与分页兼容 | 隔离 SQLite，不能证明恢复后的业务库或大量图片性能 |
| 飞书 | 冻结字节及媒体可用性判版本、只改分类只移动、逐操作回执、未知结果暂停、分页元数据核对、分片进度与人工 resolve | FakeDrive/MockTransport；目标权限、实际上传与移动仍待真实联调 |
| 入口 | 帖子详情按当前帖展示独立存储状态，运行页展示全局积压/最近成功/待核对及安全错误原因，CLI status/preflight/run/resolve | 查询不上传；状态是本地回执记录，不表示实时远端回查 |

独立审查已复现并补回归的边界包括：历史原图在改分类后失联；带图分类移动误增版本；SQL 列被改但 JSON 未改仍被判一致；北京时间跨月列表漏帖；单篇错误路径拖垮全历史查询；最终源校验失败时旧库已被替换；单篇误用其他帖云盘完成状态；缺图恢复相同 SHA 被去重跳过；移动回执丢失/旧异步任务无法结转；认证延迟压缩移动限速；复用图片掩盖已有哈希不符；旧目录被重算时间；不完整轮播随文案修订退化；自动分类未随源文修订更新。

本地定向证据：[7 个归档相关脚本](../state/offline-validation-20260915T064836Z/results.json)；数据库/详情修复证据：[5 个脚本](../state/offline-validation-20260915T065039Z/results.json)。这些记录只对应各自运行时的代码，最终交付以本节末的整库回归记录为准。新 `tests_storage_flow` 将 FB 单图、IG 多图夹具依次送入实际解析、下载、本地归档、镜像假服务和历史数据库，比较身份、正文、顺序及哈希，并禁止 HTTP 外连、核查未生成付费/发布账本。

目标为[用户指定的飞书目录](https://genhigh.feishu.cn/drive/folder/TsQef8msClS2i6dlzpfcpfn6nAc)。仅配置目标标识，镜像关闭——**这是 2026-09-15 的延期决定，不是待办**。官方建目录、上传、移动、列表和任务查询文档已于 2026-09-15 核对；动态页面正文通过官方 `document_portal/v1/document/get_detail` 读取。具体权限、接口限制与受控验收步骤在 [MANUAL_STEPS §7.1](MANUAL_STEPS.md#71-原帖云盘接入与恢复)，开启前不执行。没有真实云端写入，也没有启用调度、付费处理或发布。

**存储提交 `47b14b7` 的回归（2026-09-15）；后续整合以 §1.2 为准：**

| 检查 | 结果与证据 |
|---|---|
| 全量 Python 离线 | **69/69 脚本通过**，包括浏览器工作流、历史分页、审校/发布兼容和新增存储链路；[结果及逐脚本日志入口](../state/offline-validation-20260915T071730Z/results.json) |
| 前端单测 | **26 个测试文件、511 项测试通过**；本任务已核对命令输出，原始 stdout 未另存磁盘 |
| 前端构建 | TypeScript 与 Vite 生产构建通过，先于浏览器回归；保留已有的大于 500 kB chunk 提示 |
| Hygiene | 独立运行通过，全量记录中也有 [hygiene 日志](../state/offline-validation-20260915T071730Z/tests_hygiene.log) |
| 汇总 | [本机核验记录](../state/storage-validation-20260915.json)，含命令、证据范围和外部验收缺口 |

首轮整库 [68/69 记录](../state/offline-validation-20260915T070810Z/results.json)保留：旧 replay 夹具在归档前制造 hardlink，被新增归档保护提前拒绝。夹具改为正常入档后模拟外部替换，并显式解码 Windows junction 命令输出；生产保护未放宽，[定向回归](../state/offline-validation-20260915T071716Z/results.json)和最终整库均通过。上述结论均为**离线通过**，不升级为真实账号、飞书权限或业务上线验收。

### 1.2 存储与同群四机器人整合（2026-09-15）

在同一隔离 worktree 中将存储提交 `47b14b7` 与远端 `main b727ca4` 的 webhook 实现整合（合并提交 `d854f72`），再按用户确认的四机器人分工完善路由。未改动主工作区的运行凭据或业务真相源。

- `monitor_found→detect`，`monitor_saved→capture`，`ready/scheduled→publish`，`backlog/schedule_failed/morning/system→alert`。卡片标题与四张自检卡标明对应机器人；同群符合预期，地址重复由 `duplicate_bot_targets` 报告并阻止发送。
- 发件箱模式版本升为 2：事件冻结收件角色，发送与 30 天归档读取同一份路由。接手时主工作区 `state/feishu_outbox.json` 不存在；仍为旧记录实现安全迁移，已送达不重发、未尝试改走新阶段、未知结果先人工核对。旧原卡与裁决记录保留。
- webhook 无远端去重。发送意图先持久化为未知；超时、进程中断、5xx 或无法确认的响应不自动重放，只有明确拒绝才退避重试。人工确认旧通道未送达后结转当前阶段，避免多个旧角色重复补发同一新机器人；合并卡部分过期时只补仍有效内容。
- 运行页同时保留原帖存储与云盘恢复状态，增加四机器人配置摘要；查看状态不上传、不投递、不暴露地址或签名密钥。取消原“两组隔离”要求的理由与噪声控制措施明确记在 [FUNCTIONALITY §0.2 与 F4-2](FUNCTIONALITY.md)，配置及真实核对步骤见 [MANUAL_STEPS §2.1](MANUAL_STEPS.md#21-配置同群四个机器人)。

独立代码复审复现了“两个旧收件人结转后重复补发”和“部分过期合并卡仍落到旧角色”两项问题；[失败证据](../state/offline-validation-20260915T082933Z/results.json)、[修复后定向证据](../state/offline-validation-20260915T082950Z/results.json)均保留。复审随后独立核对一次投递、仅含有效内容及到期归档，未发现新的阻断项。

**整合回归（2026-09-15，全部使用隔离数据）：**

| 检查 | 结果与证据 |
|---|---|
| 全量 Python 离线 | **71/71 脚本通过**；[结果及逐脚本日志](../state/offline-validation-20260915T083421Z/results.json) |
| 前端单测 | **26 个测试文件、511 项测试通过**；已核对命令输出，原始 stdout 未另存 |
| 前端构建 | TypeScript 与 Vite 生产构建通过，先于浏览器回归；已有的大于 500 kB chunk 提示保留 |
| 独立浏览器回归 | **12/12 组通过**，含运行页四机器人状态、重复地址提示、恢复交互与存储状态；[完整报告](../state/ui-regression/browser-stage-all.json) |
| Hygiene | 全量记录中通过；文档收尾后再次独立核验，见下方汇总 |
| 汇总 | [本机整合核验记录](../state/storage-four-bot-validation-20260915.json)，记录命令、证据边界及 Git 交付结果 |

以上 `state/` 证据**已于 2026-09-15 随 storage worktree 清理迁到主检出的 `state/`**，本文的相对链接从主检出解析。它不随 Git 提交，所以清理任何 worktree 之前都要先确认本文引用的证据不是只存在于那一个 worktree 里——证据没了，结论按[第三节](#三证据的说法要准)要跟着降级。代码状态为**离线通过**。未运行真实 webhook 自检、FB/IG 采集、模型请求或发布；四机器人真实发送者、运营可见性与链接、飞书云盘应用授权和文件哈希验收均为**待真实联调**。

### 1.3 阶段一实施现场（2026-09-15）

本轮从 `435adc2` 建立 `codex/tweet-monitor-capture-design`，worktree 为 `D:\VSCodeWorkspace\Facebook\FacebookScraper\.worktrees\tweet-monitor-capture-design`。archive/state/.env 独立绑定，仅复用主工作区 `.venv` 解释器。改动保留在该 worktree，未提交、未合并或推送；主工作区保持干净。

阶段一代码为 **离线通过**：FB `neakasaofficial` 与 IG `neakasa.global` 独立采集并保留真实合作关系；9222 人工 30 天回填建立独立监测基线；9224 普通轮次只读主页首屏，晨间 06:30–07:30 随机滚 2–4 屏。每天含周末按北京时间 08–19 点 45–75 分钟、19–08 点 135–225 分钟运行，共享持久下一次访问时刻。主页限 24 次/平台/滚动 24 小时；详情限 1 次/帖/轮、3 次/平台/轮、12 次/平台/滚动 24 小时。缺初始化、坏状态和平台阻断拒绝继续访问。

所有候选在下载前持久化，仅来源列表不完整者允许一次匹配详情；详情与主页证据合并。失败转人工，普通轮次不重试未完成任务。实际已校验文件数、来源列表完整性与本地媒体完整性分别保存。逐帖卡异步入持久发件箱，检测及人工汇总等全轮候选终结后冻结。网络条件相关功能已移除；实际请求失败与平台停机护栏保留。

| 验证 | 结果与证据 |
|---|---|
| 最终全量 Python 离线 | **74/74 脚本通过**；[逐脚本结果及日志入口](../state/offline-validation-20260915T121317Z/results.json) |
| 独立 hygiene | **9 组通过**；[日志](../state/stage1-hygiene.log) |
| 前端单测 | **26 个文件、511 项通过**；[日志](../state/stage1-ui-tests.log) |
| 前端生产构建 | TypeScript/Vite 通过，先于浏览器回归；[日志](../state/stage1-ui-build.log)。已有的大于 500 kB chunk 提示保留 |
| 隔离浏览器回归 | **9/9 场景通过**，包括逐帖定位和一次恢复；[报告与截图](../state/offline-browser-20260915T121321Z-33388/report.json)；没有真实外部操作 |
| 独立复审 | 访问控制审查的 3 项问题已关闭；整体审查的 4 项代码问题及文档问题全部关闭；[最终定向复审](../state/stage1-final-rereview-report.md)另行验证 9/9 关键用例 |
| 汇总 | [本机阶段一验证记录](../state/stage1-validation-20260915.json)，含测试、数据边界及真实联调缺口 |

审查修复覆盖：详情响应丢失主页已知字段/部分媒体；扫描结束前冻结不完整摘要；未知 IG 结构误报完整；详情普通失败被轮次成功覆盖；无来源访问的人工尝试误清失败计数。首次失败和修复后证据保存在本 worktree 的 `state/`，不随 Git 提交；最终交付以本节列出的整库结果为准。

本 worktree 的 archive 为空；真实访问状态、采集基线、调度、发件箱均未初始化。`.env` 为占位符，没有复制真实凭据。未运行真实平台、飞书、模型、标签或发布。30 天人工回填、真实 FB 多图/IG 轮播及合作关系、四机器人发送者与链接、自然新帖和正常节奏的 72 小时试运行继续为 **待真实联调**；按 [MANUAL_STEPS §14](MANUAL_STEPS.md#14-阶段一真实验收监测与原帖抓取) 执行，不能以夹具或一次历史采集替代。

### 1.4 图片阶段复审现场（2026-09-16）

在 `claude/social-post-image-generation-e10530` 的原 worktree 内复审 `7d9b875...5acbdfb` 并修复，
沿用该 worktree 独立的 archive/state/.env 绑定，只复用主检出解释器。范围是用户编号的阶段二、
本文档 F3 的正文与图片本地化；德语、正文 DeepSeek、图片 GPT-Image、每图最多受理 3 次优化的约定保持。

修复覆盖：下载按钮及 ZIP 说明与人工交接分开；人工图在后端受理和执行前禁止优化；免费换版不计预算；
历史用量按记录模型计价；历史版本独立预览及人工正文版本兼容；上传失败保留旧图与旧选择；连续上传立即换图。
人工上传明确选择追加到 `media_de/manual_uploads.jsonl`，不进入程序图片所有权账本；即便上传的是过去的生成图，
审校和发布也采用本次人工决定。该记录与图片一同备份、迁移。上传/换版持生成锁，排队优化复核审校版本。

| 检查 | 当前结果与证据 |
|---|---|
| 定向回归 | **16/16 用例通过**；[最终运行日志](../state/offline-validation-20260916T070233Z/tests_image_workflow_review.log) |
| 隔离浏览器 | **10/10 场景通过**，第 10 场景用实际本地 API 完成预览→采用→下载→连续跨格式上传→人工标记及禁用优化；[报告与截图](../state/offline-browser-20260916T070237Z-27016/report.json) |
| 前端单测 | **27 个文件、523 项通过**；[日志](../state/stage2-review-ui-tests.log) |
| 前端构建 | TypeScript/Vite 通过；[日志](../state/stage2-review-ui-build.log)，保留已有的大于 500 kB chunk 提示 |
| 全量 Python 离线 | **75/75 脚本通过**，含 hygiene、发布兼容与浏览器工作流；[逐脚本结果及日志](../state/offline-validation-20260916T070233Z/results.json) |
| 汇总 | [本机图片阶段复审验证记录](../state/stage2-review-validation-20260916.json)，含代码基点、测试与真实联调边界 |

[修复前 11 项失败证据](../state/offline-validation-20260916T063724Z/results.json)保留，用于核对缺陷是否真实复现。
[首轮整库 74/75](../state/offline-validation-20260916T065120Z/results.json)暴露 Windows 预览占用旧图阻止跨格式上传，
修复仅对共享冲突 32/33 限时重试，最终整库及定向失败注入均通过。证据保存在主检出的 `state/`，不随 Git 提交；原 worktree 的本机文件保全见 §1.7。
所有测试使用临时归档、合成图片或假服务；本 worktree 无真实帖子，没有模型付费、平台登录、抓取、消息或发布操作。
本轮只支持 **离线通过**：两模型的真实契约与费率、德语图版面及语义、业务上传图在远端排期中的采用仍为 **待真实联调**，
按 [MANUAL_STEPS §5](MANUAL_STEPS.md#5-付费模型操作) 与发布确认流程执行；浏览器夹具不升级这些结论。

### 1.5 阶段二分平台本地化复审（2026-09-15；证据 UTC 2026-09-16）

复审与修复使用 `stage2-platform-split-translation`，原独立 worktree 为 `.worktrees/stage2-platform-split`。
接手时提交为 `301a00b`、工作树干净；与 `7d9b875` 比较原有八个实施提交。该 worktree 的
`config.local.toml` 只复用主检出解释器，归档与状态独立，测试进一步使用临时数据。

主要修复：建议输入冻结点击时的编辑正文，生成与采用使用同一正文版本；语法建议保留人工定价和标签；
IG 无 URL 的 bio 引导转入独立 CTA；列表及积压提醒的平台计数一致；剪贴板失败有明确回退。
文本、图像和英文风险预扫均禁止 SDK 在统一账本外自动重试；超时或 5xx 进入不确定状态后须先核账。
`processing-preview` 复用实际处理资格和图片规划，显示逐篇作用域及费用参考；风险预扫与 reasoning
费用未包含在参考小计中，不能把小计当作完整费用或预算上界。
`--run --process --once` 等待当前处理批次及完成后的通知汇总投递，再关闭运行时；保留离岗静默，
不额外扫描或唤醒其它任务。`tests_scheduler` 使用隔离 Runtime 与假模型/飞书验证退出前收到当轮待审卡。

[预览夹具证据](../state/stage2-platform-split/stage2-processing-preview.json)与[阶段二浏览器记录](../state/stage2-platform-split/offline-browser-20260916T065723Z-18940/report.json)
按哈希复制到主检出及本 worktree 的 `state/stage2-platform-split/`；原记录保留，不随 Git 提交。
最终回归以 [REQUIREMENTS §10.3](REQUIREMENTS.md#103-阶段三本地化风险标签与恢复) 为准；
[复审核验汇总](../state/stage2-platform-split/stage2-review-validation.json)记录独立提交 `a8f4a6e` 的 78/78 脚本、前端 530 项及本地提交信息；合并结果另行验证。
所有模型/剪贴板回执均为离线替身，本地 API 与浏览器交互使用隔离夹具；没有真实抓取、付费、飞书投递或发布。
真实德语质量、图片效果、供应商账单、运营访问 URL、德国落地页和 IG 聚合页仍为 **待真实联调**。

### 1.6 阶段二整合验收（2026-09-16）

在分平台 worktree 中整合文案提交 `a8f4a6e` 与图片主干 `ec4621e`。共享图片规划保留画幅带排序与
人工图片不可优化的约定；详情页同时保留文案建议、准确复制、历史图片预览/采用和上传替换。
预览费用按当前图片模型区分，未知费用不当作零。当时用 `gpt-image-2.5` 占位验证跨模型估算；
当前准确调用名已按 §1.14 更正为 Flare/Sunburst，不能把旧占位名用于真实请求。

合并后的代码为 **离线通过**：Python **79/79 脚本**（含处理预览 16 项），前端 **29 个文件、542 项**及
TypeScript/Vite 构建通过。整库包含基础浏览器 10 项与文案交互 5 项，均使用最终构建和临时归档；
模型、剪贴板异常、飞书与平台回执为离线替身，没有真实业务外部操作。各项真实联调边界继续沿用 §1.4–1.5。

[全量结果与逐脚本日志](../state/stage2-platform-split/offline-validation-20260916T074332Z/results.json)、
[基础及图片浏览器记录](../state/stage2-platform-split/offline-browser-20260916T074335Z-17524/report.json)、
[文案浏览器记录](../state/stage2-platform-split/offline-browser-20260916T074807Z-21648/report.json)与
[整合及 Git 交付汇总](../state/stage2-platform-split/merge-validation-20260916.json)保存在主检出的
`state/stage2-platform-split/`，不随 Git 提交。按文件哈希复制，未覆盖图片分支已有的同名日志；
原报告里的绝对路径保持原样，副本中保留同目录日志与截图，主检出可独立复核。

### 1.7 阶段二整体复审与分支收尾（2026-09-16）

按用户要求，以 `35da19b` 中已整合的文案与图片实现为整体复审；范围是抓取结果进入处理、
正文编辑/建议/重写、图片生成/换版/上传、下载及发布素材衔接。没有启动真实抓取、模型、消息或发布。

本轮复现并修复：完整抓取结果在部分失败/人工恢复后漏入队；未采用文案候选使已选图片失效并要求再次
付费；审校判过期图片仍被发布组装采用；图片优化写盘失败丢失当前选择；保存等待期间新编辑被清空；
唯一历史版本入口隐藏；冻结账号仍可换版；下载包遗漏最终平台链接/CTA；旧提示词文案候选仍可采用。

完整采集结果与入队回执原子关联，重启不重新受理；中断恢复仍只结转、不隐含重放。图片保留同源有效
生成依据，显式优化仍用当前正文，源文/原图/prompt 变更仍需复核。失败图片请求保留费用及次数，上一
选择继续有效。保存只确认已提交快照；下载、复制及发布使用相同的文案渲染规则。

**最终验证：离线通过。** Python **80/80 脚本**，包含新增图片连续操作 7 项、抓取→处理与去重恢复回归；
前端 **29 个文件、547 项**，TypeScript/Vite 构建通过。最终构建下基础/图片浏览器 **10/10**、文案编辑
浏览器 **6/6** 通过，另有 D5 对旧/缺 prompt 候选禁用及恢复不重发的定向浏览器证据。
所有来源、图片和账本均为隔离夹具，模型/剪贴板异常/外部回执为替身；这不证明真实德语质量、图片效果、
供应商用量或业务账号发布可用。真实联调仍按 [MANUAL_STEPS §14.1](MANUAL_STEPS.md#141-打开内容处理抓到就翻译和出图) 执行。

证据集中在主检出 `state/stage2-integrated-audit/`，不随 Git 提交：

- [全量 Python 结果](../state/stage2-integrated-audit/offline-validation-20260916T084710Z/results.json)、[前端单测](../state/stage2-integrated-audit/ui-tests.log)、[构建日志](../state/stage2-integrated-audit/spec-ui-build.log)。
- [基础/图片浏览器](../state/stage2-integrated-audit/offline-browser-20260916T084717Z-9668/report.json)、[文案编辑浏览器](../state/stage2-integrated-audit/offline-browser-20260916T085221Z-31384/report.json)、[D5 定向浏览器](../state/stage2-integrated-audit/spec-ui/browser-stage-d5.json)。
- [交付与保全汇总](../state/stage2-integrated-audit/validation.json)记录最终提交、推送、分支/worktree 清理及文件哈希。`worktrees/text/` 与 `worktrees/image/` 保留两个 worktree 的完整 state、归档目录及本机配置；原报告生成路径不改写，按汇总内路径映射定位现存副本。

只清理用户指定的 `stage2-platform-split-translation`、`claude/social-post-image-generation-e10530`。
清理前核对主干包含其提交、工作树无未提交改动，并逐文件核对备份哈希；其它排期 worktree 保留。

### 1.8 Windows 自动部署实施（2026-09-16）

从 `4ecc564` 建立独立 `.worktrees/service-auto-update`，分支 `codex/service-auto-update`；archive/state/.env 均为隔离绑定，只复用主检出 Python。实现提交 `ab560c7` 已快进并入 `main`，包含 Actions 部署包、维护协议、受管进程、轮询回退与页面协调；未注册真实服务机任务。

固定控制器、各版本及共享数据分开。代码回退不恢复旧账本和人工稿；两个运营偏好保存在共享状态。
原子登记涵盖排队及执行生命周期；调度与 Web 自行退出，维护验证不触发真实抓取、模型、飞书或排期。
页面统一保护正文、设置、优化指令、审校对话框与自定义排期，失联草稿保持阻塞；冻结期间仍可明确选择留在本页或放弃草稿后离开。

独立复查中复现并关闭：调度心跳先于初始化、偏好 CAS 快照竞态、预告期间回退/模式变更竞态、普通重启恢复错误版本、失败候选每秒重试、旧 CLI 跨切换受理、故障版回退后重装循环，以及横幅跳转丢稿和确认框被维护冻结。实际安装还复现 Windows 目录重命名占用；仅重试本地重命名最多 3 秒，仍拒绝覆盖既有版本。

原实施证据已迁入主检出 `state/service-auto-update/worktree/state/`，不随 Git 提交：

- [完整 Python 隔离回归：88/88](../state/service-auto-update/worktree/state/offline-validation-20260916T104828Z/results.json)。
- [最终前端单测：31 文件 / 566 项](../state/service-auto-update/worktree/state/deployment-implementation/frontend-final-tests.log)、[TypeScript/Vite 构建](../state/service-auto-update/worktree/state/deployment-implementation/frontend-final-build.log)。
- [版本化浏览器：12/12 组](../state/service-auto-update/worktree/state/deployment-implementation/browser-all-final.log)、[部署页面：6/6 场景](../state/service-auto-update/worktree/state/deployment-implementation/frontend-browser.json)。
- [发布包、安装入口与 hygiene 定向复验](../state/service-auto-update/worktree/state/offline-validation-20260916T110734Z/results.json)、[控制器回退 25 项](../state/service-auto-update/worktree/state/offline-validation-20260916T111226Z/tests_deployment_controller.log)。
- [本机 Windows 完整部署演练：4/4](../state/service-auto-update/worktree/state/deployment-implementation/wr-20260916-04/report.json)、[逐阶段日志](../state/service-auto-update/worktree/state/deployment-implementation/windows-rehearsal-04.log)：实际离线安装 61.94 秒，A→B 切换 3.031 秒，健康失败恢复 6.094 秒，中断恢复 7.110 秒。预告及失败截止时间由夹具加速，测量不代表正式服务机停顿保证；11 份共享数据哈希不变，9 个受管启动确认退出，3 个独立最终路径环境的导入与 `pip check` 通过。
- [图片操作完整浏览器复验：10/10](../state/service-auto-update/worktree/state/offline-validation-20260916T111007Z/tests_browser_workflow.log)、[最终静态路由与资源检查](../state/service-auto-update/worktree/state/deployment-implementation/static-cutover.json)。
- [最终独立复查](../state/service-auto-update/worktree/state/deployment-implementation/final-integration-review.md)；保留失败及修复后的证据，不把重试前的失败报告改写为成功。

上述代码和隔离场景为 **离线通过**；GitHub 托管工作流的实际结果以本节交付核验记录为准。正式服务机计划任务/登录重启、真实飞书与业务仍为 **待真实联调**。前端保留已有大 chunk 提示；一次临时 HTTP 退出超时和图片提示等待失败的记录保留，定向及浏览器分组复验通过。没有真实通知、抓取、模型付费或发布操作。

交付前在最终实现提交上重新完成 **88/88 Python 脚本**和前端 **566 项**；[最终全量结果](../state/service-auto-update/worktree/state/offline-validation-20260916T112659Z/results.json)保留。快进合并后按锁文件重装主检出前端依赖，[566 项单测](../state/service-auto-update/main-ui-tests.log)、[生产构建](../state/service-auto-update/main-ui-build-final.log)、[静态路由演练](../state/service-auto-update/main-static-cutover.json)和[6 项部署页面场景](../state/deployment-implementation/frontend-browser.json)均通过；首次构建缺依赖的失败日志保留。

清理前已核对 **83,603 个文件的 SHA-256**，原 `state/`、本机配置、构建产物与依赖完整保全至 `state/service-auto-update/worktree/`，7 个目录联接改指向同一保全目录内的依赖。原报告绝对路径不改写，旧 worktree 根路径按[保全记录](../state/service-auto-update/preservation.json)映射；迁移后的虚拟环境仅作证据，不作为可搬移环境启动。
[交付核验记录](../state/service-auto-update/validation.json)保存最终提交、远端状态和本次分支/worktree 清理结果；其余排期 worktree 保留。

[首次托管运行](https://github.com/marcus-ao/FacebookScraper/actions/runs/35091541154)在构建前拒绝 job 级 `runner.temp` 表达式；修正为 runner 启动后的 PowerShell 步骤写入 `GITHUB_ENV`。`actionlint 1.7.12` 已[复现原错误](../state/service-auto-update/workflow-lint-before.log)，[修正后通过](../state/service-auto-update/workflow-lint-after.log)，实际步骤也在隔离环境文件上验证。托管测试与制品产出仍以对应提交的工作流结果为准。
[第二次托管运行](https://github.com/marcus-ao/FacebookScraper/actions/runs/35091838093)完成锁定依赖与浏览器安装后，复现 3 项单测因提前注入生产运行标识而进入维护状态的环境差异。工作流改为先运行普通模式单测，再为生产构建生成版本标识；[相同环境复现](../state/service-auto-update/ui-ci-environment-negative.log)和[修正后 566 项通过](../state/service-auto-update/ui-ci-environment-positive.log)均保留。受管模式继续由专门单测与实际构建浏览器场景覆盖。
[第三次托管运行](https://github.com/marcus-ao/FacebookScraper/actions/runs/35092261582)的前端检查和构建通过，Python 为 84/88：runner 的账户临时目录使用 `RUNNER~1` 短路径，影响三组采集夹具与安装路径断言。已用本机 8.3 别名[复现全部 4 组失败](../state/service-auto-update/short-temp-negative.log)，CI 改用 runner 下明确创建的临时目录，实际工作流步骤配置后[4/4 通过](../state/service-auto-update/short-temp-positive.log)。业务目录保护未放宽；托管环境与本机环境的原始日志均保留。

### 1.9 排期分支整合核验（2026-09-17）

排期分支的五个提交保留内容冻结、北京时间选期、异步提交进度、本地月历、公开观察与人工撤销登记。
原现场中已人工整合的三个冲突文件保留，再合入主干阶段二与 Windows 自动部署；
运营设置继续存入共享偏好，业务时区取配置，付费账本月界不变。

整合修复包括：冻结图片在上传或采用历史版本前拒绝写入；平台计数包含冻结状态；
异步发布在 HTTP 202 返回后仍独立登记维护操作，完成或取消后才释放；详情按冻结快照恢复
提交进度，刷新可继续轮询，核实撤销后不显示旧成功。独立复审发现项已关闭。

隔离定向五个脚本、前端 **31 文件 / 581 项**及 TypeScript/Vite 构建通过。
浏览器 **11/11** 场景通过，含提交期间刷新后继续轮询；首次定位器整段文本匹配失败的原日志保留。
全量首轮 **88/89**，仅上述浏览器定位器失败，修正后该组 **11/11** 通过；全部 **89 个脚本**已有通过记录。
[全量原始结果](../state/schedule-integration-20260917/worktree/state/offline-validation-20260917T031811Z/results.json)与
[浏览器修复后结果](../state/schedule-integration-20260917/worktree/state/offline-validation-20260917T032100Z/results.json)同时保留。
结论为 **离线通过**；真实控件与账号继续 **待真实联调**，远端排期图片读取适配器仍为 **代码未完成**。
未运行真实抓取、模型、消息、发布或服务机部署。

本机证据保全根目录为主检出 `state/schedule-integration-20260917/`：
`pre-merge/` 留存接手时的索引、补丁与三个冲突文件；`worktree/` 保留原排期工作区源码、
state、配置、依赖与构建产物，旧报告的 worktree 绝对路径按此映射。
[逐文件 SHA-256 清单](../state/schedule-integration-20260917/preservation.json)及
[整合交付记录](../state/schedule-integration-20260917/validation.json)不随 Git 提交。

### 1.10 局域网访问实施（2026-09-16）

2026-09-16 实施时用户确认：服务机尚未首次部署，2–5 人同权使用受控办公网 HTTP，暂不登录、不记录个人身份，继续 `actor: null`。实施分支 `codex/lan-access` 的局域网初版提交为 `162d107`，基于 `b70b786`；工作区 `.worktrees/lan-access` 使用隔离测试数据。初版为 **离线通过**；[原始验证汇总](../state/lan-access-validation.json)保留。下表记录纳入真实网络配置和排期主干后的交付验证，远端提交与构建以[交付记录](../state/lan-delivery-validation.json)为准。

用户本次授权配置、提交并推送该分支。已将 `origin/main f659581` 的排期实现纳入分支：保留冻结/选期/后台提交生命周期及北京时间设置；两处文档章节冲突保留双方内容，局域网手册改为第 17 节。新增排期夹具补齐真实网段的受管网络配置，验证远程 HTTP 202 返回后后台操作仍阻止维护切换。

用户随后明确交付目标为合入并推送 `main`，服务机拉取主分支测试。实现提交 `b464ad5` 的 [GitHub Windows release](https://github.com/marcus-ao/FacebookScraper/actions/runs/35180707812) 已成功，产出可下载的运行包；网络配置只在版本库的 `ops/service-machine.network.json`，不随制品发布。主分支发布结果需核对对应 main 运行，不能用功能分支成功代替。服务机从拉取 main、校验同 SHA 制品到安装的操作见 [MANUAL_STEPS §17.6](MANUAL_STEPS.md#176-拉取主分支后在服务机调试)。源码拉取不修改受管配置，首次安装仍使用新的隔离目录。

原局域网实施覆盖持久网络配置、受管监听、来源/同源检查、非安全 HTTP 前端、多标签页维护、统一飞书链接及防火墙步骤。当时使用 WLAN / `10.66.3.157` / `255.255.255.0` / 网关 `10.66.3.254`，入口 `http://10.66.3.157:8765`、允许来源 `10.66.3.0/24`；这些地址仅用于解释下方历史证据，不再作为当前安装参数。

**当前网络（2026-09-17 用户最新确认）：** 服务机在同一办公 Wi-Fi 内的 IPv4 已由 `10.66.4.35` 变为 `10.66.4.9`，沿用 WLAN / `255.255.255.0` / 网关 `10.66.4.254`。旧地址仅为历史 DHCP 采样，固定地址尚未落实。安装配置 `ops/service-machine.network.json` 采用入口 `http://10.66.4.9:8765`，只允许 `10.66.4.0/24`；开发机 `10.66.4.12` 在允许范围内。已安装实例仍读取自己的 `control/host.json`，须按 [MANUAL_STEPS §17.4.1](MANUAL_STEPS.md#1741-已有实例改为当前办公网) 维护改址。新网络的 DHCP 地址保留、防火墙生效、两台办公电脑访问、跨夜及登录恢复均为 **待真实联调**，旧网络证据不能替代。

当前 `10.66.4.9` 安装配置为 **离线通过**：[定向结果与日志](../state/offline-validation-20260917T115347Z/results.json)记录宿主 19 项、后台排期 25 项、Web 访问 12 项及 hygiene 9 组全部通过。使用临时实例、空凭据、ASGI 模拟客户端及假外部服务；没有服务机改址、防火墙变更或真实抓取、模型、飞书、发布操作。证据保存在 `codex/service-network-10-66-4` 独立工作树的 `state/`，并已按 SHA-256 核对复制到主检出的同名目录；原日志路径保持不变，主检出可从同目录日志复核。证据不随 Git 交付，清理前须保全。此前 `10.66.4.35` 的[历史定向结果](../state/offline-validation-20260917T100526Z/results.json)保留，不能作为新地址的现场可达性证据。

`control/host.json` 是网络配置唯一来源；受管健康由匹配 PID/创建时间及启动标识的心跳报告实际监听，配置与进程不一致拒绝 readiness。来源检查先于业务登记，代理头解释关闭，部署状态只输出页面所需字段。独立复审发现并修复了 Vite 代理改写 Host 后与浏览器 Origin 不符的问题，开发代理保留原 Host；防火墙脚本复审没有遗留阻断项。

| 检查 | 结果与证据 |
|---|---|
| 完整 Python 离线 | **91/91 脚本通过**；[逐脚本日志](../state/offline-validation-20260917T035654Z/results.json)，覆盖配置、部署、维护、后台排期及 LAN 检查 |
| 前端单测与构建 | **31 个文件、587 项通过**；[单测日志](../state/lan-delivery-frontend-tests.log)、[TypeScript/Vite 构建](../state/lan-delivery-frontend-build.log)。保留已有大 chunk 提示 |
| 浏览器主流程与静态入口 | **12/12 组通过**；[主流程报告](../state/ui-regression/browser-stage-all.json)、[18 个 HTTP 入口与深链刷新](../state/cutover-lan-delivery.json) |
| 部署页面及 HTTP 专项 | [部署协调 6/6](../state/deployment-implementation/frontend-browser.json)、[非安全 HTTP 7/7](../state/offline-lan-20260917T035402Z-36044/report.json)，5 个独立客户端及同浏览器多标签页 |
| Windows 实际受管实例 | [完整重跑 4/4](../state/windows-lan-delivery-recheck/report.json)：3 个最终路径环境、9 个受管启动确认退出、11 份共享文件及网络配置不变、源码无漂移 |
| 工作流与复审 | `actionlint 1.7.12` [通过](../state/lan-delivery-actionlint.log)；独立复审核对 17 项访问/维护拒绝及 4 个 PowerShell 指南代码块，安装引导不依赖尚未安装的第三方包 |
| 汇总 | [交付记录](../state/lan-delivery-validation.json)，包含代码指纹、远端提交/构建及现场验收缺口 |

本机 Windows 演练监听 `0.0.0.0` 临时端口，允许来源为文档保留网段，不把开发机作为业务入口；预检用独立回环端口。成功切换阶段实测约 3.9 秒，仅代表这套无业务负载夹具；回退与中断恢复使用推进的逻辑时限，不承诺服务机耗时。测试没有修改本机防火墙、注册计划任务、使用业务 Chrome profile 或发送飞书/模型/发布请求。飞书链接以假消息验证生成地址，实际点击仍须现场验收。

新增 LAN 浏览器检查已纳入发布工作流。合并后发布触发已收回到只有 `main`：功能分支不再产出与生产同形的制品，`codex/lan-access` 那次门禁结果记录于交付证据。真实办公网络、至少两台实体客户端及持续运行均为 **待真实联调**，按 [MANUAL_STEPS §17](MANUAL_STEPS.md#17-办公局域网接入) 留证。上述证据已从 `.worktrees/lan-access` 保全到主检出 `state/`，覆盖前的同名旧浏览器报告存于 `state/pre-lan-evidence-backup-*`；`state/` 不随 Git 提交，删除即导致相应结论降级。

整合定向首次 [6/7](../state/offline-validation-20260917T035133Z/results.json) 暴露新增排期夹具缺少受管网络配置；补全后改用旧网段 `10.66.3.42` 的 ASGI 模拟客户端和同源请求，[后台排期 25 项通过](../state/offline-validation-20260917T035334Z/results.json)。同时修正部署浏览器旧“柏林”标签定位器为当前“北京”；此前 [main 云端运行](https://github.com/marcus-ao/FacebookScraper/actions/runs/35178546562) 正是在该定位器失败，本分支 6 项浏览器检查已通过。

[Windows 首次安装失败现场](../state/windows-lan-delivery/report.json)保留：临时 probe 维护文件替换遇到 `WinError 5`，尚无业务进程；同路径稍后可正常原子写入，新目录完整重跑通过。没有将失败目录当作成功安装，不断言系统短暂拒绝的具体来源。若服务机复现，保留目录与日志，核对文件占用及权限后处理，不删除共享数据或放宽进程保护。

### 1.11 Windows CI 浏览器宿主退出诊断（2026-09-17）

`main bbeb3b5` 的 [发布运行 35218364147](https://github.com/marcus-ao/FacebookScraper/actions/runs/35218364147)
在 D3 图片断言通过后，隔离 HTTP 宿主退出失败；打包和运行包上传均未执行。
Python 3.12.9 的 Windows Proactor 连接清理遇到 `WinError 10054` 时，
`socket.shutdown()` 异常跳过 `close()` 和 server transport detach，导致 Uvicorn 的
`wait_closed()` 持续等待；不是 D3 业务断言失败，也不能通过忽略退出错误放行制品。

修复工作树为 `.worktrees/ci-browser-shutdown`，分支 `codex/ci-browser-shutdown`。
仅复用主检出 Python，archive/state/.env 独立绑定；GitHub 原始日志与摘要核验后的失败 ZIP
保存在该工作树 `state/ci-browser-shutdown/`。其中 `reset-negative.log` 用真实 HTTP 宿主和
socket 关闭边界故障注入复现同一错误：请求与连接集合已空，server 仍挂接 1 个 transport。
自然重跑 D3 曾通过，不能代替这个确定性失败证据。

测试 HTTP 宿主统一通过 `tests/http_fixture.py` 的 `asyncio.Runner` 使用 Selector 循环，
不改全局事件循环策略，Playwright 子进程仍用 Windows 默认循环；运行服务的宿主不变。
`tests_browser_fixture` 的 2 项回归校验关闭异常后线程/端口释放，以及宿主运行期间真实异步
子进程可用；在锁定 Uvicorn 0.52.4 和声明的最低版本 0.30.0 均通过，兼容验证只解包 wheel，
没有改动开发环境依赖。对应日志为 `reset-runner-positive.log`、`uvicorn-030-regression.log`。

首轮定向 [3/4 记录](../state/offline-validation-20260917T124605Z/results.json)还保留了连续上传
得到 409 的测试失败。诊断确认旧测试会在按钮禁用时直接设置隐藏文件输入，绕过页面的刷新
等待；`upload-diagnostic-2.log` 记录了这一行为。上传测试改为点击可用按钮后经文件选择器
上传，保留两次 200、图片刷新、人工标记及无付费断言，不放宽业务版本检查，也不自动重试。

最终修复为 **离线通过**，证据均在上述工作树，未使用业务数据或真实外部服务：

| 检查 | 结果与证据 |
|---|---|
| 定向脚本 | [4/4](../state/offline-validation-20260917T125835Z/results.json)：宿主 2 项、浏览器工作流 11 项、文案交互 6 项、Hygiene 9 组 |
| 完整 CI 浏览器步骤 | [12/12 主流程](../state/ui-regression/browser-stage-all.json)、[18 个 HTTP 入口与深链](../state/ci-browser-shutdown/final-cutover.json)、[部署页面 6 项](../state/deployment-implementation/frontend-browser.json)、[LAN 7 项](../state/offline-lan-20260917T130702Z-38800/report.json)；四个命令连续退出 0 |
| 构建与复审 | 当前前端生产构建通过；运行输入指纹与构建一致，独立复审无遗留阻断；[本机汇总](../state/ci-browser-shutdown/validation.json) |

修复代码提交为 `a9909e5`；本机验证时，远端 `main bbeb3b5` 的上述托管运行仍失败且只有
失败证据制品。用户已确认合并并推送，后续提交、main 工作流与制品核验回执记入上述本机汇总。
托管构建恢复须核对新 main 的完整 SHA、工作流成功结论及 `fbscraper-windows` 包身份；
本地通过不替代这三项证据。服务机部署与真实业务继续为 **待真实联调**。

### 1.12 DeepSeek Flash 模型名修复（2026-09-17）

分支 `codex/deepseek-flash-model`，工作树 `.worktrees/deepseek-flash-model`；独立 archive/state/.env，
只复用主检出的 Python 解释器。用户提供的服务机日志对应旧名称 `deepseek-v4-flash` 请求、
`deepseek-flash` 响应；[官方更新说明](https://api-docs.deepseek.com/zh-cn/updates/)确认旧模型于
2026-09-10 退役并暂时兼容路由到 V4.1 Flash。默认配置和白名单已改用当前名称，Pro 仍允许；
不匹配提示只陈述实际请求/响应并指向官方说明，不把所有名称变化断言成异常回退。

状态为 **离线通过**：[翻译回归](../state/offline-validation-20260918T024929Z/results.json)通过，
覆盖当前 Flash 的实际 SDK + MockTransport 请求、旧失败留账后单篇成功、Pro 收到 Flash 拒绝、
旧两次拒绝阻止第三次请求。[相关定向记录](../state/offline-validation-20260918T024853Z/results.json)
中的其余 7 个脚本通过：付费账本、初次翻译、风险扫描、优化、建议、标签及 hygiene；该次翻译
唯一失败是新断言误写终态名称，改成既有 `accepted` 后单独复验通过。修复前同症状的
[失败复现](../state/offline-validation-20260918T024714Z/results.json)保留。证据位于上述工作树
`state/`，不随 Git 交付，清理前须保全。

没有修改费用账本、任务标识、提示词版本、保守估算费率或两次拒绝上限；没有新增付费请求。
本机隔离夹具不能证明服务机账单或该帖真实译文通过，当前也没有服务机账本可供判断是否已达
拒绝上限。真实复验为 **待真实联调**，按 [MANUAL_STEPS §5.4](MANUAL_STEPS.md#54-deepseek-flash-模型名升级后的单篇复验)
先核账、确认来源许可与预算，再对指定单篇执行；`--check` 本身付费，不作为必跑前置。

### 1.13 Facebook 作者身份修复（2026-09-18）

分支 `codex/facebook-owner-identity`，工作树 `.worktrees/facebook-owner-identity`，基点 `64f1422`。
16 条用户拒绝记录的直接原因是 `id:61591381265280` 与 `neakasaofficial` 直接比较；
过滤在图片下载之前。同帖媒体较全的数字 ID 片段还会掩盖另一片段的已知用户名。

用户随后提供的真实捕获前缀含 `ProfileActionMessage.profile_owner.id = 61591381265280`，
同一对象的 `uri` 为 `https://www.facebook.com/messages/t/neakasaofficial/`。
[精简身份夹具](../tests/fixtures/facebook_profile_identity.json)保留这条绑定与外层结构，
删除无关联系信息、密钥配置、CDN 查询串和界面字段；搭配的 story 为构造样例，不能称为完整真实帖重放。
用户给出的 `122125125675379375` 归档确实有 `kind=video`、`source_media_id=3751016248369637`，
`local_path=null` 符合视频只存元数据的契约；没有证据证明该帖被误判为纯文字。

解析器仅用作者对象、明确主页对象及消息动作的 ID/URL 关联规范化 owner，保存 `owner_evidence`。
跨片段、滚动进度和详情补图共用该结果，冲突记录 `owner_conflict` 并阻止详情下载；
未提供身份关联的数字 ID 仍拒绝，不从目标配置、显示名或帖子链接放行。

**验证状态：离线通过。** [最终 12/12 个定向脚本结果与逐脚本日志](../state/offline-validation-20260918T030307Z/results.json)
覆盖身份回归（12 项）、解析、回填、增量入口与离线重放、采集生命周期与媒体、归档链路、下游来源检查、replay 和 hygiene。
所有写入使用临时归档、合成图片及假传输，没有真实浏览器、CDN、模型、飞书或发布操作。
失败复现保存在同工作树 `state/offline-validation-20260918T025331Z/`，真实前缀修复前失败保存在
`state/offline-validation-20260918T025714Z/`；这些本机日志不随 Git 提交，清理工作树前须保全。

独立只读复审发现并关闭“旧拒绝记录按 post_id 去重吞掉新详情冲突证据”：拒绝账目按身份判据去重，
不同证据追加，旧行保留；[失败复现](../state/offline-validation-20260918T030102Z/results.json)与
[修复后定向结果](../state/offline-validation-20260918T030130Z/results.json)均保留。最终增量入口回归同时
验证 URL 解析兼容项目既有下划线账号格式；独立复核无遗留阻断项。

与主干 `193e990`（DeepSeek Flash 修复）整合后，[13/13 个定向脚本通过](../state/offline-validation-20260918T031033Z/results.json)，
包含上述抓取回归及翻译测试；业务代码无冲突，交接文档保留两项修复并调整章节编号。
本节引用的测试证据已按 SHA-256 核对复制到主检出 `state/` 的同名目录，原工作树副本保留；
日志内运行时绝对路径不改写。合并验证仍为隔离夹具，不替代服务机部署或真实补抓。

**待真实联调：** 尚无服务机业务目录，未向其补入那 16 篇图文帖的图片。完整捕获的解析与单图下载证据见下，
服务机全部图片 URL 有效性、实际文件与人工处理结果仍须核验。部署代码不会自动改写历史拒绝列表或恢复缺图；按
[MANUAL_STEPS §14 的误拒核验与恢复](MANUAL_STEPS.md#facebook-误拒记录的核验与恢复)执行。

**缺图续查（2026-09-18）。** 用户重跑后的 `122123783349379375/post.json` 已有正确作者和身份依据，
但 `media=[]`、两层 completeness 均为 `true`、`source_media_count=0`。因此该帖在下载前已没有媒体项，
不能归因为 CDN 下载失败。截图只证明目录没有图片，不能证明原始响应中的附件位置。
在同一工作树基于主干 `e22c9b1` 复现并修复：缺附件字段误判完整空帖、空片段覆盖部分图片、
旧完整空档阻止部分图片补入。明确空数组仍支持纯文案帖；视频身份保持不变。
[失败复现](../state/offline-validation-20260918T070515Z/results.json)和
[6/6 定向脚本通过](../state/offline-validation-20260918T070545Z/results.json)均为隔离构造响应；
归档用例实际写入合成 PNG，并核验旧来源历史、人工分类和人工稿保留。
[另外 5/5 入口、生命周期、存储链路及 hygiene 脚本通过](../state/offline-validation-20260918T070957Z/results.json)，
独立复审未发现本次补丁的新增阻断项。上述证据保留在工作树，并按 SHA-256 核对复制到主检出 `state/`，
未接入服务机数据。该轮代码为 **离线通过**，当时仍缺完整 story，未据此宣称现场图片已恢复。

**完整捕获核验。** 用户现已提供 `D:\Download\_capture_1789714625.json`，3,381,341 字节、78 个 payload，
SHA-256 为 `302dff3727f9c92ea3590a0356e83b2b7e278832f585e6ea569625b73e80b7dc`。
对该文件重放 `8b88fda` 得到 21 帖、0 图片、5 视频、16 空媒体帖；真实缺口是
`attachments[].media` 仅含类型/ID，而图片 URL 位于 `styles.attachment.media.photo_image`，
两篇相册分别在 `styles.attachment.all_subattachments.nodes` 中包含 5 和 4 项。
解析器现解包明确的 Photo/Album renderer，再复用逐媒体选图与完整性检查；不递归收集整帖的任意图片。

[完整捕获前后比较](../state/facebook-media-capture-20260918/comparison.json)确认修后仍为 21 帖、0 拒绝，
恢复 23 图片、保留 5 视频，全部来源列表完整，无空媒体帖。以捕获时刻计算最近 30 天则为 12 帖、13 图片，
其它 9 帖仍受日期窗口过滤；不能把整份捕获数量当成 30 天归档数量。
完整捕获还走过临时归档补入验证：23 次假图片响应实际生成 23 个文件，旧空档可升级，人工分类与旧来源历史保留。
[真实精简夹具](../tests/fixtures/facebook_media_styles.json)包含单图、两篇相册和视频的原始嵌套结构，
CDN URL 改成 `cdn.invalid`，移除追踪、评论和无关主页资料；
[修前失败](../state/offline-validation-20260918T071943Z/results.json)与
[修后 4/4 定向脚本通过](../state/offline-validation-20260918T072011Z/results.json)可复核。
[入口、生命周期、存储链路及 hygiene 另外 5/5 脚本通过](../state/offline-validation-20260918T072228Z/results.json)，
合计 9 个定向脚本。独立复审重放全文件及反转顺序，核对两篇相册逐项 ID、未知 renderer 和残缺降级，
未发现阻断项；[独立 4/4 脚本记录](../state/offline-validation-20260918T072212Z/results.json)另存。

另以无浏览器、无 Cookie 的 Playwright 请求上下文调用实际下载器，只实测目标帖
`122123783349379375` 的一个 CDN URL；[下载记录](../state/facebook-media-capture-20260918/cdn-check/result.json)
及其同目录隔离归档保留 512×640、39,748 字节 JPEG，SHA-256 为
`a29b5287b76eaf1e1dc9151c571daf9f668e01f00bebd46bfe7c6cfc2831f60f`，人工图像核对为 IFA 海报。
这是捕获附件提供的尺寸，不证明已取得平台原始最大分辨率；也不代表其它 22 张图或服务机整轮回填验收。
代码及完整捕获重放状态为 **离线通过**；服务机完整补抓仍为 **待真实联调**。
本次报告、定向日志及单图隔离归档均保留在工作树，并按 SHA-256 核对复制到主检出 `state/`；
报告中的运行时绝对路径不改写。完整原始 capture 保持用户提供的位置，不随 Git 提交。
整合主干 `d1f5880` 的 Instagram 单图修复后，[5/5 定向脚本通过](../state/offline-validation-20260918T072609Z/results.json)，
覆盖 Facebook 媒体、共用解析、Instagram 单帖修复、存储链路及 hygiene；两边功能和文档均保留。

### 1.14 图片模型空目录修复（2026-09-18）

分支 `codex/image-model-catalog`，工作树 `.worktrees/image-model-catalog`，基点 `5bb1751`。
archive/state/.env 独立绑定，只复用主检出的 Python。用户提供的服务机诊断为 HTTP 200、
顶层 `data/object`、`data` 是列表、有效模型 ID 数为 0；未提供条目总数或原始响应，
因此不能断言 `data` 一定为空，也不能确认是 Key 配置还是供应商目录故障。

[供应商模型管理文档](https://docs.aihubmix.com/cn/api/Models-API)明确带 Key 的列表反映 token
配置，并提供精确型号详情。旧实现把空列表、坏结构与非空缺型号都归为模型不可用。
现仅对合法空列表补查同一端点、同一凭据的 `/v1/models/{model}`，要求详情 `id` 精确匹配；
其它失败仍在付费前关闭本批，缓存失败不逐图重试。保留原模型、提示词、费率和 `catalog`
记录语义；它证明元数据确认，不证明实际远端路由。edits 返回不同模型仍拒绝并保留用量。

状态为 **离线通过**：[修复前失败复现](../state/offline-validation-20260918T033152Z/results.json)，
[修复后定向结果](../state/offline-validation-20260918T033241Z/results.json)为 3/3 脚本通过，
包含新增模型目录 9 项、原图片测试与付费账本。使用实际 SDK + MockTransport、合成图片和
临时账本，验证空列表→精确详情→一次 edits、目录异常零付费、失败缓存、预算与响应模型拒绝。
[相关回归 4/4](../state/offline-validation-20260918T033446Z/results.json)补验图片生成依据、图片工作流、
来源许可与 hygiene，共 7 个定向脚本通过；未改前端或运行全量。§5.5 免费诊断代码块另以
MockTransport 执行，确认仅两个 GET、无 edits。证据位于本工作树 `state/`，不随 Git 交付，清理前须保全。

没有使用服务机凭据、上传业务图片或新增付费请求。当前 Key 的详情响应和该图真实生成、
德语质量、账单为 **待真实联调**；按 [MANUAL_STEPS §5.5](MANUAL_STEPS.md#55-图片模型目录为空时的核验与单图复验)
先免费查元数据，再在已有来源许可与预算内单图复验。文案翻译无需重做。

**2.5 型号更正（同分支后续）：** 用户提供的 [Flare](https://aihubmix.com/model/gpt-image-2.5-flare)
与 [Sunburst](https://aihubmix.com/model/gpt-image-2.5-sunburst) 官方页确认调用名分别为
`gpt-image-2.5-flare`、`gpt-image-2.5-sunburst`，两者公开 token 费率均为文本输入 5、
图片输入 8、图片输出 30 USD / 百万 token。白名单与费率表按两个精确型号保存，拒绝旧占位名
`gpt-image-2.5`，默认仍是 `gpt-image-2`。旧账本不改写，不将未知旧型号猜成某个变体；
保留按记录型号计费、预览不借用其它型号样本的规则。此次只校正型号与费率来源，
两款模型的真实 edits、usage、尺寸及德语图效果仍为 **待真实联调**。

型号更正为 **离线通过**：[更正前回归失败](../state/offline-validation-20260918T033900Z/results.json)，
[更正后 6/6 定向脚本通过](../state/offline-validation-20260918T034012Z/results.json)，覆盖模型目录
12 项（含两变体的 SDK 请求/响应/账本身份及笼统名称拒绝）、图片处理、图片工作流、
处理预览、付费账本与 hygiene。仍使用隔离数据及 MockTransport，没有真实付费请求。

### 1.15 减少动画模式下的审校菜单定位

分支 `codex/review-menu-position`，工作树 `.worktrees/review-menu-position`，基点 `4b8516c`。
用户现场菜单已打开但 `y=-7296`。隔离 Chromium 在 `prefers-reduced-motion: reduce`、
827×730 视口下复现 `x=729, y=-7300`，没有前端异常；普通动画模式通过。
全局非零 `transition-duration: 0.01ms !important` 使弹层同步测量读到 `-1000vh` 的过渡起点；
[上游 #618](https://github.com/react-component/trigger/issues/618)描述同一机制，本次结论以本地前后对照为依据。
过渡时间改为 `0s`，动画时长仍为 `0.01ms`，保留减少动画偏好及菜单业务条件。

状态为 **离线通过**：[修复前失败日志](../state/review-menu-position/browser-before.log)、
[修复后浏览器报告](../state/ui-regression/browser-stage-review_menu.json)记录 24 个组合：
普通/减少动画、827/1366 宽度、列表四种可处理状态及 FB/IG 详情。菜单项完全进入视口、
点击打开对话框、取消返回焦点、列表重新展开和点击外部关闭均通过；未发送业务写请求。
原失败组合修复后为 `x=729, y=199`；[减少动画截图](../state/ui-regression/review-menu-reduced-motion.png)保留。
[相关前端 48 项](../state/review-menu-position/ui-tests.log)与
[TypeScript/Vite 构建](../state/review-menu-position/build-after.log)通过，构建仍有既有大 chunk 提示。
[既有列表流程 C](../state/review-menu-position/browser-list.log)、
[详情冲突恢复 I](../state/review-menu-position/browser-detail.log)及
[hygiene 9 组](../state/review-menu-position/hygiene.log)通过，独立只读复审无遗留阻断项。
测试使用临时归档、合成图片与独立浏览器 profile，没有接入业务 Chrome、模型、飞书或发布。
本节证据在上述工作树的 `state/`，不随 Git 交付，清理工作树前须保全。

附件另报 `/api/refinements/task/...` 返回 500。本机 FB/IG 隔离样例均为
[HTTP 200](../state/review-menu-position/capabilities-check.log)，未复现，未修改该接口。
服务机菜单复验及这条 500 的 Traceback 取证为 **待真实联调**，按
[MANUAL_STEPS 第 13 节](MANUAL_STEPS.md#13-更新并启动审校台)执行；不能宣称此 CSS 修复解决了后端 500。

**源码启动补验。** 用户确认服务机只执行 `git pull`、停止旧 Web 后重新运行 `scripts/run_web.bat`，
没有执行前端构建。旧脚本仅启动 Uvicorn，Git 忽略 `web/ui/dist`，因此重启继续提供旧 CSS；
本机主检出也保留了 `index-DOeRiS6m.css` 中的 `.01ms` 规则。上面的 CSS 回归先手动构建，
没有覆盖这个实际启动入口，不能证明拉取源码后重启已生效。

源码版 `run_web.bat` 现每次按锁文件安装依赖并构建，全部成功后才启动 Python；缺 Node/npm、
安装或构建失败会退出并明确报错。带 `release.json` 的运行包使用包内前端，继续不要求 Node。
[启动修复前失败](../state/review-menu-startup/launcher-before.log)与
[修复后 5 项通过](../state/review-menu-startup/launcher-after.log)使用真实 Windows 批处理和临时目录，
npm/Python 边界为替身；覆盖连续源码更新后的旧产物替换、顺序、失败停止、空格路径与参数转发、
运行包跳过构建。状态为 **离线通过**，服务机运行该入口及页面复验仍为 **待真实联调**。
另将本机主检出的旧前端产物复制到隔离工作树，再执行真实 `scripts/run_web.bat --help`：
[安装、构建与 Uvicorn 入口日志](../state/review-menu-startup/real-launch-build.log)通过；
`--help` 只用于构建后退出，不启动业务服务。
[旧 CSS](../state/review-menu-startup/stale-build-before.json)已替换为
[含 `0s` 的新 CSS](../state/review-menu-startup/fresh-build-after.json)。用这次脚本生成的产物运行
[24 个菜单组合](../state/review-menu-startup/browser-report.json)和
[18 个实际 FastAPI HTTP 入口及深链刷新](../state/review-menu-startup/static-http.json)均通过；
[hygiene 9 组](../state/review-menu-startup/hygiene.log)通过。
这些 HTTP/浏览器检查仍用临时归档与合成图片；服务机地址本机访问超时，没有远程部署或真实页面验收。
本次续修证据在同工作树 `state/review-menu-startup/`，旧菜单证据保留。

### 1.16 Instagram 单图完整性与优化错误提示修复

分支 `codex/instagram-single-image-completeness`，工作树 `.worktrees/instagram-single-image-completeness`，基点 `e22c9b1`。archive/state/.env 独立绑定，仅复用主检出 Python。用户提供的服务机字段摘要为 `media_type=1`、`__typename=XDTMediaDict`、`product_type=feed`、`carousel_media=null`；旧解析器按字段存在性进入轮播分支，导致已校验单图仍被素材硬闸拒绝。没有拿到服务机完整 capture 或业务目录。

明确单图现兼容空轮播字段，真实轮播缺子项及矛盾类型仍不完整。单帖命令 `tools.repair_ig_completeness` 默认预览，执行前核对指定 capture 的同帖证据和既有原图；备份原字节及修复依据后只改三个完整性字段，追加 manifest 并重建两个展示索引。不改原文、原图、人工记录、业务账本、采集历史或通知。manifest/索引中断可用原命令继续；服务机步骤见 [MANUAL_STEPS §4.1](MANUAL_STEPS.md#41-ig-单图被误标不完整时的离线修复)。

页面直接展示 API 错误原因。刷新失败保留错误，成功后清除本地提交提示并保留优化要求；状态刷新不再提交生成。409 后详情刷新异常也被捕获，原拒绝原因不被吞掉。

状态为 **离线通过**：

| 检查 | 证据与边界 |
|---|---|
| 解析失败复现 | [修复前结果](../state/offline-validation-20260918T065437Z/results.json)记录单图 `null`/空数组错误判为不完整；样本由用户字段摘要构造，不是完整真实响应重放 |
| 定向后端 | [7/7 脚本](../state/offline-validation-20260918T070250Z/results.json)：解析、单帖修复 5 项、采集媒体、存储链路/状态、优化及 hygiene；临时归档、合成图片、假传输，无外部请求 |
| 浏览器 | [修复前失败](../state/refinement-browser-before.log)、[修复后通过](../state/refinement-browser-after.log)、[报告及截图](../state/offline-browser-20260918T070054Z-20376/report.json)；真实本地 API/构建页面，409/503 使用浏览器响应夹具，验证原因、保留输入、失败/成功刷新及仅一次 POST |
| 前端与入口 | [相关前端 92 项](../state/ui-tests.log)、[TypeScript/Vite 构建](../state/ui-build-after.log)通过；保留既有大 chunk 提示。`scripts/run_python.bat -m tools.repair_ig_completeness --help` 入口通过 |

独立只读复审未发现阻断项，并另行运行解析回归及 5 项单帖修复测试通过。本节证据保存在上述工作树 `state/`，不随 Git 交付，清理前须保全。服务机 `3984612646028833441` 的实际修复、详情回读以及真实模型请求仍为 **待真实联调**；代码部署不自动改写旧 `post.json`，也不代表付费功能已真实验收。

### 1.17 历史 Instagram 完整性自动核验（2026-09-18）

分支 `codex/instagram-archive-reconciliation`，工作树 `.worktrees/instagram-archive-reconciliation`，
基点 `92e8718`；archive/state/.env 独立绑定，只复用主检出的 Python。
用户服务机输出确认 21 篇归档中 7 篇历史记录来源完整性未确认，3 篇图片为 `saved`、
4 篇视频为 `metadata_only`，均为 `not_in_capture_state`；本轮采集的 12 篇均为 `complete`。
这是用户提供的字段摘要，本机没有该服务机目录或这批 IG capture，不能据此证明 7 篇都可离线修复。

当前监测与晨间对账在访问平台、清理 capture 前，自动用账号目录已有 capture 核验历史误标。
单图及单视频共用指定单帖工具的核验/备份实现；所有匹配片段一致、身份/URL/媒体 ID 匹配、
本地图片实际校验通过才修三个完整性字段及 manifest。无证据、坏文件、矛盾类型、真实轮播和
坏图片保留，输出逐帖原因；不自动重抓历史、不改变访问配额、采集历史、通知或业务账本。
真相更新后由现有展示入口刷新索引；已写真相但 manifest 中断可继续同步。

`--status` 增加全归档实际文件诊断与采集状态；扫描“修复旧帖”改为“处理已有帖”。
告警区分首次观察与上次保存计数，不把来源列表问题和视频元数据说成图片下载失败，
也不再承诺异常项下次自动重抓。自动处理及剩余缺口解释见 [MANUAL_STEPS §4.2](MANUAL_STEPS.md#42-历史-ig-完整性自动核验)。

**验证状态：离线通过。** [修复前失败复现](../state/offline-validation-20260918T114435Z/results.json)
与 [8 项新增回归通过](../state/offline-validation-20260918T114954Z/results.json)使用临时归档、
合成图片与构造 capture，覆盖 3 图+4 视频自动修复、只读状态、幂等、缺证据/冲突/坏图拒绝、
坏 capture、manifest 中断后 capture 消失仍可恢复、dry-run 和晨间共用入口。
[12 个相关脚本的定向记录](../state/offline-validation-20260918T115046Z/results.json)中 11 个直接通过，
增量测试唯一失败来自旧断言在整行 JSON 中搜索 ID `222`，会被 `archived_at` 小数秒偶然命中；
改为比较 `post_id` 集合后，[增量入口与 hygiene 2/2 通过](../state/offline-validation-20260918T115206Z/results.json)。
全部 12 个脚本已有通过记录，覆盖新增自动核验、旧单帖工具、告警、解析、媒体、采集生命周期、
调度及恢复、存储链路与展示索引。本轮未改前端，没有运行本机全量。
独立只读复审无阻断项，另以隔离夹具确认归档锁占用时零写入、单图及单视频的矛盾总数拒绝。
本机证据保留于上述工作树 `state/`，已按 SHA-256 核对复制到主检出同名目录；不随 Git 提交，清理前须保全。
服务机自动修复后的真实数量与详情回读继续为 **待真实联调**；没有真实浏览器、模型、飞书或发布操作。

### 1.18 审核分区直接确认（2026-09-19）

分支 `codex/review-section-confirmations`，工作树 `.worktrees/review-section-confirmations`，基点 `1e15a12`；archive/state/.env 独立绑定，仅复用主检出 Python。标签确认原来只在编辑模式显示，Instagram 引导语没有对应确认状态。两个分区现可在浏览状态直接勾选／取消并保存，编辑时随草稿保存；Facebook 分区确认覆盖各落地页。空链接且无引导语可记录确认，不新增必做步骤。有链接或引导语的旧记录保留人工内容，并要求补确认。

浏览确认使用 `confirmation_only` 请求，后端拒绝过期正文、旧机器提示词及夹带内容改动；不因勾选分区而重新认证过期正文。浏览确认保存期间禁用重复确认和编辑，隐藏冻结入口；编辑草稿保存期间的新输入继续保留。源文变化后重置对应确认，冻结、只读和终态仍不可编辑。

**验证状态：离线通过。** [9/9 定向脚本](../state/offline-validation-20260919T123420Z/results.json)覆盖本地化 24 项、发布组装 3 项、Web 审校 54 项、冻结 12 项、后台发布 25 项、既有发布检查、hygiene，以及[基础浏览器 12 项](../state/offline-browser-20260919T123424Z-16088/report.json)和[文案浏览器 9 项](../state/offline-browser-20260919T123612Z-19756/report.json)。新增浏览器场景验证 IG 无链接直接确认／刷新、修改引导语后重新确认、失败与源文冲突、过期正文拒绝、FB 映射直接确认／取消、保存中禁用及只读／冻结／排期状态。另有[前端 105 项](../state/review-section-confirmations/frontend-final.log)、[TypeScript/Vite 构建](../state/review-section-confirmations/build-final.log)和[D1 保存／冲突契约](../state/ui-regression/browser-stage-d1.json)通过，保留已有大 chunk 提示。

独立复审发现的过期正文误认证和确认保存期间可冻结均已修复并复核；[过期正文修前失败](../state/review-section-confirmations/stale-confirmation-before.log)与[修后通过](../state/review-section-confirmations/stale-confirmation-after.log)保留。所有写入使用临时归档；模型、剪贴板异常、冻结可用性和终态展示为替身，没有真实抓取、付费、飞书或发布操作。证据位于该工作树 `state/`，不随 Git 提交；清理前须保全。服务机更新和运营实际页面复验为 **待真实联调**，按 [MANUAL_STEPS §13](MANUAL_STEPS.md#13-更新并启动审校台)执行。

## 2. 红线

1. 不自动登录。人在三个专用 Chrome profile 登录，代码只附着。
2. 不自动滚历史。回填由人滚；增量和兜底对账始终受 C7 深度、频率、会话和失败预算约束。
3. 抓取、探测、发布三种身份分离：9222 / 9224 / 9223。不要恢复旧“两浏览器”设计。
4. Facebook 与 Instagram 是独立车道，不配对、不合并、不沿用 composer 默认双选。
5. 所有 Business Suite 定位和成功判据必须能回查真实 probe dump 的交互/快照。截图见到文字不等于存在可定位元素。
6. 人工文案、人工图片和人工决定不能被重建、重译或后台任务覆盖。
7. 付费请求必须经过统一预算和来源许可。不确定计费先核账，不自动重放。
8. 发布前先冻结并展示具体文案、图片、账号、单一渠道和时刻，等用户确认后才能提交。**冻结与提交是两步**：`content_locked` 冻的是正文与图片字节，时刻在提交时才绑定一次；冻结期间四个编辑入口全关，要改内容须显式解除冻结。
9. `review_items.jsonl`、`paid_requests.jsonl`、`published.jsonl` 的坏数据必须失败闭合。不得把损坏当空文件继续运行。
10. 凭据只在本机配置，不写日志、dump、截图或版本库。

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
- 风险路径与三类采样已有离线验证。IG 采样遵守 C7 持久停机、仅接受明确同名 hashtag 容器的累计 count。Trends 新增 CSV 专属被动控件、完整摘要上下文、BOM/CRLF/周月解析；恢复共享锁并要求 `trends-status` 的 revision。新增 4 项审查修复的 11 项导出测试通过，T1–T4 复审已关闭；真实 CSV/模型/平台采样仍未验收。⛔ **标签热度推荐 2026-09-15 起明确延期**（[REQUIREMENTS §9](REQUIREMENTS.md#9-明确延期与固定边界)），`[hashtags].enabled = false` 是决定不是缺口，采集器代码保留不删。
- 阶段二本轮补上：翻译提示词按渠道渲染（IG 删掉原文的 bio 引导句，见第 8 节）、审校台按平台分成两个入口、一键复制服务端算好的成品文案、模型给只读优化建议交人逐条决定。**法语明确延期**，本轮不做 locale 抽象。
- 设置只开放两项 CAS，原注释/受控说明已经展示，版本化浏览器对保存与冲突做了隔离本地后端验证。当前翻译 prompt 7、图片 prompt 3；2026-09-16 业务归档仍为空，历史样本不作当前可发布素材。`stale=false` 与 `machine_current` 分开解释，不从版本差异推断图文件历史或自动重译。
- 飞书按帖聚合/阶段路由/晨报/有效首图状态与摘要、原卡冻结/人工 resolve、多收件人部分失效漏发和镜像周期包/大证据独立版本已有离线验证。当前角色有效原卡补送保留投递 ID；旧角色结转及部分失效保留旧 cancelled 卡，只给漏收者生成仍有效的新提醒。企业投递和恢复仍待权限。
- 完整月历实际读取本次已真实通过，公开观察按 remote ID，不按时钟；业务层直接使用 month_inventory/month_readback，旧 Business Suite 接口仅保留兼容。新提交全文/唯一渠道/资产/remote ID/时刻因果和窗口/DST 已离线验证、P1/P2 复审关闭。远端 scheduled 详情图片适配器仍未实现，须受控排期获取真实控件后补齐；编辑器缩略图检查不替代它。G8 要求全文相等、远端媒体验证及有序 source SHA 与冻结清单一致，旧 scheduled 仍防重。
- 第八批 API 已接历史 range/page/total、来源/快照指纹、风险/三来源元信息、恢复/设置和 Web/CLI 共用五阶段只读状态。
- 历史交接六项已逐项补上：canonical source_text_sha256/AST 守卫，风险 raw scan_text_sha256 保留偏移；补扫中途过期仍跑普通探测并给技术/晨间告警；付费恢复；outbox 默认 30 天完整终态组件按 SHA 归档且主状态保留去重；FB 光标 {{linkN}} 与 /check 最终计数；消除重复 alias。`afb6784` 为调度过期修复，`923091f` 为飞书终态归档，最终整合在 `565f17c`；共同回归已单列。
- 两个平台都没有任何可提交的联调素材：归档已清空，此前准备的 FB 待制作包也一并删除。`.global` 回填、企业飞书/云盘/外部心跳与运营完整流程仍依赖人工会话、权限和具体发布确认。
- 审校台前端已收敛为单一 React 应用。深链接刷新由 `web/api/app.py::SinglePageFiles` 保住，契约钉在 `tests/tests_spa_static.py`；历史页首屏缩略图成本是已知待观察项（数字见 §7）。
- 最新阶段二整合 Python **79/79**、前端 **542 项**及生产构建、浏览器 **15/15** 已通过，证据见 §1.6。这些测试跑在隔离夹具上，证明代码契约成立，不证明任何外部系统可用。
- 一项留待跟进的性能观察：清库前在 1,067 篇归档上，冻结账号详情页首次打开 8.6 秒、刷新 4.1 秒，其余页面 0.4–3.2 秒。数据重建到相当规模后要重新量，别把空库上的"很快"当成结论。

2026-09-14/15 阶段一打磨的成果只有离线证据：本地 tag 母目录布局、回填 `--days` 窗口、消息通道改走群自建机器人、帖子文件夹名改成业务可读的四段，以及本轮把监测重做成共享访问预算下的「发现即抓取」。**这些都不证明真实探测往返或真实新帖链路可用**；用户已在群里收到四个机器人的自检响应，真实探测、运营可达链接和首次回填仍待完成，云盘按 [REQUIREMENTS §9](REQUIREMENTS.md#9-明确延期与固定边界) 延期。

⚠️ **`legal_size()` 的 16 像素量化会把正好压线的原图推出 IG 画幅带。** 候选排序原先第一键是宽度取整
误差，画幅偏差只排第二，而它完全不知道发布侧那条 4:5 ~ 1.91:1 的窗口。实测：`1440x1800`（正好 4:5）
量化成 `1440x1808`，比值 0.7965 掉出 4:5；`640x800` 量化成 `720x912`，比值 0.7895 同样掉出，还附带
1.13 倍放大。两者的形变分别只有 0.442% 和 1.316%，都在 `aspect_drift_warn_percent = 2.0`（818 张归档
标定）以下，**告警不会响**——原帖发得出去，德语版发不出去，而且今天连报错都没有，因为 G6/G6c 全关。

修法是排序加一个前置键"源图在带内而候选出带 → 排到最后"，带由 `[image].preferred_aspect_band` 给出。
修后两例分别落到 `1440x1792`（0.8036）与 `736x912`（0.8070），形变 0.446% / 0.877%，仍远低于告警线。
⛔ **它只重排候选，从不拒绝**，也不修正原图本身就在带外的比值（`1536x2048`、`1200x628` 结果不变）——
那是擅自改画面。这条带来自 2026-09-01 历史观察，dump 已删，所以只能当偏好不能当闸；有已签字 probe
dump 时以 probe 为准。

⚠️ **模型把原图原样退回来，原先所有闸都会放行。** `validate_output()` 查尺寸、格式、纯色占位图和
dHash 距离，而 **dHash 越小越"好"**——原样退回的距离正好是 0，`dhash_max_distance` 又是 `-1`（关闭）。
于是"花了钱、图一个字没改"在代码里完全静默。现在逐像素比较并记录 `changed_pixel_ratio`：
逐**字节**相同作为回显结果硬拒绝；最大通道差大于 24 才计作改动，占比 0 **不拒绝**，只在 CLI 和审校台提示“未检测到明显像素变化”。
占比 0 不能证明像素完全相同；图里没有英文、模型未按要求处理或低对比度文字变化都可能得到 0。
而 `[publish].require_all_media_de = true` 要求每张都有德语图，在这里拒绝会让前一种情况的帖子永远发不出去。

⚠️ **`change_ratio_warn` 阈值不能凭感觉填，当前是 `-1`（关闭）。** 合成样本上实测：JPEG q60 重编码的
噪声占比 0.079%，而一次真实改写文字只有 0.062%——**两者会重叠**。必须用真实产出分组标定，步骤见
[MANUAL_STEPS §5.2](MANUAL_STEPS.md#52-标定图内文字到底改没改的告警线)。

⚠️ **文件夹名去掉 `_<post_id>` 之后，有一处原本无声的失败被接了出来。** 目录归属改按 `folder_name` 判定（`store.post_folder_matches()`），`images_de.jsonl` 因此要带 `folder_name` 字段；两条判据都不中的行会打印一条告警，而不是像以前那样默默把程序产出的德语图当成人工图、从此永不重做。旧记录与旧目录继续走 ID 后缀，迁移期两种名字会同时存在，两条都不能删。

`tests_month_inventory` 的推荐时段用例已修：基线 6 次跑失败 1 次，根因在夹具不在 production——用例只给 Playwright 的 hover 留了 300ms，而一个页面上的**首次** hover 要付一次性的可操作性开销，实测中位 641ms，其后每次 47ms。超时被 `is_recommendation` 吞成 False，正向断言就红。`read()` 调到它之前 `read_grid` 已扫完 35 个日期格，真实 Planner 路径早把这笔付过，`month_inventory.py` 未改。修后单测连跑 70 次、整文件连跑 12 次均无失败。

`tests_browser_workflow` 的 `test_03_history_pages_and_frozen_account_are_read_only` 也已修，「Python 66/66」可以直接采信。这一条的根因**在 production 不在夹具**：列表行无条件带上 `thumbnail_url`，而前端的契约是空串才改画占位符（`columns.tsx` 和它的单测早就有这一支，只是后端从不给空串），于是 32 条历史行里没有图的那 31 条也各发一次注定 404 的图片请求。浏览器每源只有 6 条连接，翻页的列表查询排在这些请求后面：实测 stall 中位 2223ms、最大 3018ms，而服务端本身只花 30ms——5000ms 预算有三分之二耗在排队上，慢一点的盘就越线。`reader.py` 改成无图时给空串后，stall 中位 0.8ms，点击到 12 行中位 86ms、最大 109ms。

登录/RBAC/actor、视频、跨平台复用、`supervised` 和无人审核发布是明确延期，单独管理。**运行机器迁移 2026-09-15 起不再延期**：服务机 24 小时运行业务，本机只做调试与回归，两台机器各一份 `archive/state`；顺序与代价见 [MANUAL_STEPS §15](MANUAL_STEPS.md#15-服务机部署与日常更新)。

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
- 历史归档的“1051 条待译正文”不是可发布口径。2026-08/09 旧归档统计为：

| 平台 | 总数 | 有正文 | 静态图文可发 | 纯视频 | 无媒体 |
|---|---:|---:|---:|---:|---:|
| Facebook | 47 | 46 | 27 | 17 | 2 |
| `in_neakasa.tech` | 1020 | 1011 | 443 | 568 | 0 |

这些数字只描述旧归档，不能外推 `neakasa.global`。

## 6. 2026-09-01 Business Suite 真实探查

⛔ **这一节记的是 Business Suite 界面长什么样，来源 dump 已随 2026-09-14 清库删除。** 下面的观察本身仍然有用——它描述的是远端平台的形态，不是本地数据——但**没有任何一条现在能回查**。红线 5 要求定位必须能回查真实 dump，所以重新录证之前这些只能当线索，不能当依据。

重录用 `scripts\run_python.bat -m tools._scaffolding.probe_publish`，复核用 `scripts\run_probe_signals.bat --report state\<新 dump>.json`。原来那份也没有单渠道切换交互，重录时要补上。

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

- 单渠道控件与 22 条事件的被动 v2 探查都已删除。已观察 FB `Neakasa Deutschland` 与 IG `neakasa.de`，尚无本轮新 Schedule 提交回读。
- 月历控件录证与截图均已删除。清库前 2026-09-13T05:01:33Z 实际 inventory 返回 ready，范围 8 月 30 日至 10 月 3 日、35 格、4 条公开帖，含 3 个独立 IG remote ID。
- 真实详情页可由头部 Facebook/Instagram 图标、`Published on` 与合作作者信息区分渠道；未来两个仅显示时刻的项经 tooltip 确认为推荐时段，须排除于帖子/占用数。
- 本次完整月历读取已通过，仍不能外推新的 scheduled 卡片、跨月/DST 或真实长文/多图回读。读取覆盖所有格子/时刻条目、手工任务和延迟加载，不能用 shell 就绪或已读一周声明空档；公开状态无充分依据仍显示 unknown。
- 编辑器媒体探查曾记录技术长文案和 2 张 1536×2048 原图，按顺序核验 dHash/RGB 误差 0、宽高比比值 1；文件已删。未提交发布或排期，可能留下草稿；这不是有效业务联调内容，也不是远端 scheduled 图片证据。

探查与提交是不同证据。最终两篇具体内容经用户确认后，分别完成 FB-only/IG-only 排期及回读；还要覆盖长文、多图、FB 链接、IG CTA 和运营从飞书开卡片到收到排期回执的完整流程。

### 2026-09-16 业务时区改成北京之后的边界算例

运营在上海，让她按自己的钟点填写（`[publish].timezone = "Asia/Shanghai"`）。
`ui_timezone` 仍是 `America/Los_Angeles`，两者是两件事：前者决定她看到和填写的墙上时刻，
后者决定日期选择器能选到哪个月。

北京比美西快 15–16 小时，所以**美西 9 月的最后一刻落在北京 10 月 1 日下午三点多**：

```
  北京 10-01 12:00  =  美西 09-30 21:00  →  美西还在 9 月，允许
  北京 10-01 16:00  =  美西 10-01 01:00  →  美西已进 10 月，不允许
```

⚠️ 界面写着北京时间却在 10 月 1 日下午戛然而止，**这会被当成 bug 报上来**；月历页的
「跨月时差」标记不要删。

北京没有夏令时，业务侧全年 `+08:00`；会切的是美西那一侧（2026-03-08 / 11-01），
窗口时差因此在 15 和 16 小时之间来回。`business_time()` 里"不存在/出现两次"两个分支照样
保留——当前时区没这问题，不等于这个函数以后不会换到有夏令时的时区。

**德国受众的钟点不再写在脸上。** 北京 16:00 是柏林 10:00，但北京 10:00 是柏林凌晨 4 点，
而后者看起来最像个正常时刻。每个时刻旁边并排显示柏林当地时刻，落在柏林 00:00–06:00 时
提示但不阻断。

### 每份证据能证明到哪

**最容易犯的错是把左边一列读成右边一列。** 下表统一保存当前仍需遵守的证据边界。

| 证据 | 能证明什么 | **不能**证明什么 |
|---|---|---|
| 单渠道控件录证（已删，需重录） | 当前 FB `Neakasa Deutschland`、IG `neakasa.de` 的单渠道控件与账号观察 | 新 Schedule 已提交或已回读 |
| 被动 v2 探查（已删，需重录） | 本轮 22 条事件记录 | 自动等同 G8 真实排期验收 |
| 月历控件录证（已删，需重录） | 月历日期格与条目控件来源 | 未加载内容可以被当成空档 |
| 月历截图（已删） | 当前月历观察；结合实际读取证明本次 35 格内容已读取 | 未来 scheduled 卡片的完整正文/图片回读 |
| 渠道截图（已删） | 对应渠道当前可见状态 | 替代结构化资产 ID / remote ID 核验 |
| 2026-09-13T05:01:33Z 实际 inventory | 4 条公开帖；2 个仅时刻项经正向 tooltip 确认为推荐时段并排除；3 个 IG remote ID 独立 | 把公开帖或推荐时段当成本次新建排期成功的证明 |
| 编辑器媒体探查（已删） | FB 单渠道编辑器技术长文案与 2 张历史原图的上传控件/缩略图数量顺序观察 | 业务候选就绪、长文完整远端回读或排期图片验证 |
| 编辑器两图视觉核验（已删） | 2026-09-13T06:01:17Z，两张 1536×2048 原图按顺序与编辑器图比较：dHash 距离 0、RGB 平均误差 0、宽高比比值 1 | 远端发布/排期详情的图片数量、顺序或字节已确认 |
| 群机器人的"发送成功" | HTTP 被飞书接受（`code` 或旧版 `StatusCode` 为 0） | **谁看到了、有没有重复。** 机器人不返回 `message_id`，本地回执是 `bot-accepted:<delivery_id>`；没有远端去重，未知结果必须暂停核对，人工误判未送达仍可能造成重复 |
| `notifications --self-test` 成功 | 四个不同机器人地址的自检请求被接受，签名或关键词校验通过 | 是否四个角色配对正确仍须在同一群中核对实际发送者与卡片名称；不证明业务内容正确、运营已看到或链接可达 |

公开状态按 remote ID 和明确远端观察记录，**不由当前时钟超过 `scheduled_at` 推算**。旧 journal 里的双渠道排期是旧历史，不能充当本轮独立 FB/IG 的新 G8 验收。当前远端图片数与顺序仍未证实——本地快照保存了多少张，不能替代平台回读看到多少张。

媒体核验器以真实 DOM 中最近的 `Remove photo` 祖先确定每张缩略图，修复了嵌套容器重复计数后才与冻结原图作视觉比较。这是编辑器侧的准备证据，不是远端排期详情读取。

## 7. 审校台缩略图实测

`3718c0d` 记录的 2026-09-14 实测如下：

- **历史页首屏缩略图成本。** 隔离夹具（63 篇）上是每张 0.03–0.11 秒、50 行首屏约 3.9 秒；换到原归档（1,067 篇）直接向运行中的宿主取 8 张，中位 1.57 秒、最大 2.0 秒——按每源六连接算，首屏约 12–13 秒。成本随账号目录规模走（`assert_physical_direct_path` 要遍历它），所以夹具数字注定偏乐观。文字行仍然立刻出来，页面可用；如果图片补齐太慢，先让运营选每页 20 条。
- **一个量不准的陷阱。** 缩略图是 `loading="lazy"`，没有被绘制的标签页发出的图片请求数为零。`document.visibilityState === "hidden"` 时 38 秒内什么都没加载——看起来像卡死，其实不是。**要量就从前台窗口量，或者直接取 URL。**
- **上面这两个数是按"每行都发一次请求"量的，2026-09-15 起不再成立。** 当时无图的帖子也带 `thumbnail_url`，于是也各占一次连接；现在无图就给空串，只有真有图的行才发请求（根因与实测见第 4 节）。首屏成本要按有图行数重算，1.57 秒的单张中位仍然有效。

## 8. 内容安全规则

- 优惠码、品牌、型号、@提及、合作方水印/署名和配置中的保留词不改。
- 图片中的数值和单位不自动换算；不确定时保持原样。
- 图片和正文共用同一术语表。
- 没有当前版正文译文时，不开始图片本地化。
- 确定性错误与模型风险提示分开展示。风险夹具不得混入真实任务。
- 图内英文放不下时按阶梯处理：换更短的德语说法 → 原文字块外接矩形内重新断行 → 同矩形内等比缩小字号 →
  **保留英文原文**。不得改写含义、截断、移动或缩放非文字元素、拉伸字形、加色块腾地方。
  图内的美国限定内容照译不替换——换掉它是营销决策，由人在审校台决定。
- **翻译提示词按渠道渲染**，`build_system_prompt()` 的 `platform` 是必填参数。Instagram 版要求把原文里"去主页看链接"那一句**整句删掉**——它若被照译，会和编辑区独立选定的引导话术叠成两句。
- IG 未绑定人工决定的草稿，源文有 URL 或明确的 bio / profile 引导时均预填独立 CTA。不要只检查 URL：原帖常常只有 `link in bio`。已绑定人工 CTA 原样还原，空值也表示业务决定；不得因源文仍含引导而补回。

⚠️ **正文里残留的主页引导只能给黄色提示，不能做硬闸**（`localization.mentions_profile_link()`）。自然语言判断会误杀，而误杀的代价是拒绝一次已经付过钱的产出。同理，它的正则必须要求出现"链接/去某处"的指向词：德语的 `Bio` 还有"有机"的意思，`Bio-Abfall`、`biologisch abbaubar` 对这个品类是会真出现的词。

## 9. 工作协议

动手前读取本文件、[FUNCTIONALITY.md](FUNCTIONALITY.md) 和相关模块测试。先用离线输入复现，再决定是否需要真实浏览器。任何真实发布前都准备具体内容给用户确认。

验证报告必须写清：运行了什么、使用的是离线夹具还是真实账号、通过日期、哪些外部依赖仍未联调。不得用测试名称或 mock 回执替代真实证据。

### 注释与说明

协作入口统一为 [AGENTS.md](../AGENTS.md)。`docs/` 之外只保留当前位置必需的信息：

- 源码注释解释不明显的约束或原因，通常一行；不复述代码，不保留实施过程、旧方案、验收编号和测试成绩。
- 配置保留参数值、单位、取值范围及必要使用提示；完整操作步骤写入 MANUAL_STEPS。README 保留用途、启动和文档导航。
- 同一规则只维护一处，其余引用。历史数字、运行状态和证据不写进源码；没有后续用途的过程记录直接删除，无需搬入文档。
- 本目录保留业务、验收和恢复需要的细节；有效证据注明日期、代码范围、输入及限制，不要求逐次记录工具操作。
- 简化不得修改配置值、接口、业务逻辑或模型提示词含义；按实际改动验证。

## 10. 文档与验证入口

项目业务文档只维护四份：本文件保存红线和证据边界，[FUNCTIONALITY.md](FUNCTIONALITY.md) 保存功能与附录 A 术语表，[REQUIREMENTS.md](REQUIREMENTS.md) 保存需求和第 10 节验收状态，[MANUAL_STEPS.md](MANUAL_STEPS.md) 保存需要人执行的操作。审校台接口说明位于 [web/DESIGN.md](../web/DESIGN.md)，使用与构建说明位于 [web/README.md](../web/README.md)，界面约束与决策位于 [web/ui/DESIGN.md](../web/ui/DESIGN.md) 和 [web/ui/DECISION_LOG.md](../web/ui/DECISION_LOG.md)。
