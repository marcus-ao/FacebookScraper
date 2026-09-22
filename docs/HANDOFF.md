# 项目交接

**现场同步至 2026-09-22。** 业务功能以 [FUNCTIONALITY.md](FUNCTIONALITY.md) 为准，每个验收单元的状态以 [REQUIREMENTS §10](REQUIREMENTS.md#10-五阶段验收状态) 为准；**本文件管的是边界与证据**——哪些是红线、真实 UI 长什么样、踩过什么坑、哪份证据能证明到哪一步。按主题组织，不记实施过程。

## 1. 当前工作区事实

**源码交付（2026-09-22 用户决定）。** 用户确认服务机直接运行源码，沿用 [MANUAL_STEPS §13](MANUAL_STEPS.md#13-更新并启动审校台) 的拉取、构建和重启流程。Actions 检查与打包工作流移除；自动制品交付及后续联调标为明确延期，保留打包、控制器、兼容协议与既有测试。开发收尾以 [AGENTS 第四节](../AGENTS.md#四按影响选测试默认不跑全量) 的定向验证为准。本次配置与文档调整无需重启服务机，不迁移业务数据。下文 CI 修复记录保留历史证据和仍适用的约束，不是恢复工作流的待办。

本项文档与工作流检查为**离线通过**：核对工作流入口、改动范围、新增链接、既有章节锚点、UTF-8/LF 和 diff；未运行应用回归或操作服务机。远端生效须以移除提交合入 main 后的仓库与运行记录回读为准，不由本地检查推定。

**2026-09-19。开发机只做代码与离线回归，服务机是唯一真实运行业务的机器，两台各一份 `archive/state`。** 2026-09-20 试运营上线。[REQUIREMENTS §10.1–§10.4](REQUIREMENTS.md#10-五阶段验收状态) 已按业务决定标记 `真实通过*`；⛔ **那个星号表示"按决定标记、当时没有可复核证据"**，判据与限制见 [REQUIREMENTS §0](REQUIREMENTS.md#0-状态词)，不要把它读成普通的真实通过。

**开发机（本仓库）。** 没有 `config.local.toml`，直接按 `config.toml` 的 `[paths]` 读同目录的 `archive/` 与 `state/`。归档 0 篇；`state/` 保留离线证据及本次服务机 G1 录制，没有任何业务账本——`published.jsonl`、`paid_requests.jsonl`、`pipeline_state.json`、`feishu_outbox.json` 都不存在。激活边界未激活，四个计划任务未注册，G1 录制及共通信号已通过；专用渠道/月历能力和 G8 仍按各自证据判断，见 §1.21。⚠️ 副本工作区可能用忽略入库的 `config.local.toml` 指回别的数据，所以「在副本里跑」不等于「跑在空数据上」——接手前先看那个文件指向哪里。

**审校台。** 只有 `web/ui/` 一套 React + TypeScript 应用，`config.toml` 的 `[paths].web_dist` 指向 `web/ui/dist/`；构建产物不入版本库。⛔ **源码检出重启 Web 必须走 `scripts/run_web.bat`（局域网用复用它的 `scripts/run_web_lan.bat`）**——它每次按锁文件装依赖再构建。直接跑 Uvicorn 会继续提供旧产物，这个坑真实发生过：服务机 `git pull` 后重启，页面仍是修复前的 CSS。带 `release.json` 的运行包用包内前端，不需要 Node。

**单篇排期。** 审校台冻结后选时刻、再由发布 Chrome 创建定时任务，用的是本次确认的渠道、账号和 `[publish].asset_id` / `business_id`。历史录证和流水线激活不再作为这次提交的许可证；批量批准和 CLI `--submit` 仍走原来的严格条件。`scheduled` 仍可以在远端图片未核验时写下，这条记录不能激活流水线，也不能当作 G8。本段只说明闸的位置，没有新的真实排期证据。

**浏览器会话。** 三个 Chrome profile 在 `~/.fbscraper-*`（家目录，不在仓库内）。进程启动不代表会话有效，要人在对应 profile 核对。

**外部依赖。** `[feishu].enabled = true`，未设置显式网络策略的非受管进程读取 `[feishu].base_url`，由改址脚本与网络 JSON 同步；设置 `FBSCRAPER_NETWORK_CONFIG` 时读取该 JSON。服务机第一次加载这份配置之前先数积压（§1.1）。`[heartbeat].enabled = true`，真正发出 POST 还要服务机 `.env` 的 `HEARTBEAT_URL`。⛔ **云盘镜像明确延期**（[REQUIREMENTS §9](REQUIREMENTS.md#9-明确延期与固定边界)）：`[mirror].enabled = false` 是决定不是缺口，代码和恢复路径都已离线验过，不要去补实现。**它留下的敞口是本轮没有异地备份**，而 `state/published.jsonl` 不可重建——按 [MANUAL_STEPS §1](MANUAL_STEPS.md#1-接续运行数据前先备份和核验) 由人定期外拷，**没有任何代码会替你做这件事**。日历刷新、标签热度仍关闭；`ui_constraints_verified` 已按 G1 验收设为 true，绑定 2026-09-20 录制。

**接手前先看 `git status`。** 只提交自己范围，不覆盖别人的改动。

**源码服务机改址。** 仓库网络入口以 `ops/service-machine.network.json` 为准，非受管飞书入口同步至 `[feishu].base_url`。`scripts/update_service_address.bat` 支持 IP/前缀或明确 CIDR，保留未指定端口；只输入 IP 时不猜新子网。局域网入口 `scripts/run_web_lan.bat` 从同一 JSON 读取监听和端口，再调用原前端构建流程。防火墙及客户端操作见 [MANUAL_STEPS §13.1](MANUAL_STEPS.md#131-源码服务机一键改址)。受管 `control/host.json` 优先且不由此工具修改，历史文档中的地址不是新的现场确认。

### 1.1 服务机现状与上线前的未完项

服务机已部署并跑过真实抓取和真实模型调用。下表是用户提供的服务机输出（2026-09-18）；本机没有那份业务目录，**不能替它下结论，开发机跑出来的结果也不是它的结果**。

| 项目 | 现状 |
|---|---|
| IG `neakasa.global` | 归档 21 篇。本轮采集 12 篇完整；7 篇历史记录来源完整性未确认（3 篇图片 `saved`、4 篇视频 `metadata_only`），由自动核验在访问平台前处理 |
| FB `neakasaofficial` | 图文帖先被作者身份误拒（16 篇），后因 Comet 附件结构落成 `media=[]` 空媒体。两处解析都已修复，⛔ **但代码不会把图片找回来**——原图要人在 9222 按同一日期窗口重新回填 |
| 真实翻译 | 调用过，失败：旧模型名 `deepseek-v4-flash` 已于 2026-09-10 退役。已改 `deepseek-flash`，**至今没有一次成功的真实产出** |
| 真实出图 | 调用过，失败：`/v1/models` 返回合法空列表。已加精确详情补查，**至今没有一次成功的真实产出** |
| 审校台 | 用过。减少动画偏好下三点菜单跑到 `y=-7296`，已修；当时服务机在跑修复前的 CSS，启动脚本已补构建步骤 |
| 仍未解决 | `/api/refinements/task/...` 返回 500。本机 FB/IG 隔离样例都是 200，未复现；需要服务机 Python 日志里该请求的 Traceback 末尾、异常类型和错误说明，遮去密钥 |

上线前按顺序做：服务机先只读数抓取积压 → 拉含这份配置的 `main` 并正常停旧 Web 后重启（源码局域网走 `scripts/run_web_lan.bat`）→ 复验上表六项 → 人工重新回填 FB 原图 → 四机器人在**服务机**自检（2026-09-15 那次在开发机上做的，不算）→ 写入 `HEARTBEAT_URL` → 72 小时试运行且不带 `--process`。打开 `--process` 的四条硬前置见 [MANUAL_STEPS §14.1](MANUAL_STEPS.md#141-打开内容处理抓到就翻译和出图)。

⚠️ **`[feishu].enabled` 已经是 `true`。** 抓取事件在落档时就以 `acknowledged=false` 写进 `capture_state.json`，飞书关闭时 `enqueue_capture_results()` 第一行就返回、不会确认它们。服务机第一次加载这份配置（拉代码并重启）之后，下一轮维护会把积压事件按扫描一次性入队。只读数一遍的步骤见 [MANUAL_STEPS §2.1](MANUAL_STEPS.md#21-配置同群四个机器人)。

⚠️ **没有「只在服务机开飞书」这个选项。** `config.local.toml` 被白名单锁定，只能绑 `[paths]` 与 `[runtime]`，两台机器共用同一份 `config.toml`。未设置显式网络策略的非受管进程读取 `[feishu].base_url`；设置 `FBSCRAPER_NETWORK_CONFIG` 时读取源码网络 JSON；设置 `FBSCRAPER_CONTROL_DIR` 时优先读取 `control/host.json` 的 `public_base_url`。⛔ **`[feishu].base_url` 不是监听地址**，`git pull` 也不会改 `host.json`。⚠️ **开发机不要带着生产 `.env` 跑调度或 `--process`**——四个 webhook 是真的，会往业务群发卡片。

### 1.2 已交付修复留下的硬约束

按主题记，不按分支和日期记——**这些是踩过的坑，退回去就会重犯**。逐项验收状态在 [REQUIREMENTS §10](REQUIREMENTS.md#10-五阶段验收状态)。

**Facebook 响应结构。** Comet 的图片 URL 不在附件外层：`attachments[].media` 可能只有类型和 ID，单图实际在 `styles.attachment.media.photo_image`，相册在 `styles.attachment.all_subattachments.nodes`。相册按内部节点顺序逐项取，外层封面不另算一张。⚠️ 只有宽高、没有 `uri` 的 `viewer_image` **不是可下载地址**。缺 `attachments` 字段表示"数量未知"，只有明确的空数组才表示没有附件；已知图片不能被空片段覆盖。作者身份只接受同批响应里的明确关联——同一作者或主页对象的 ID↔URL，或 `ProfileActionMessage.profile_owner.id` 与 `/messages/t/<用户名>/` 的绑定；不从显示名、目标配置或帖子 permalink 推断，未关联的数字 ID 仍然拒绝。

**Instagram 响应结构。** `media_type=1` 的单图允许 `carousel_media` 为 `null` 或空数组，**字段存在本身不是轮播证据**。`media_type=8` 缺子项、未知类型或类型与轮播字段矛盾仍算不完整。视频 `kind=video` 且 `local_path=null` 是设计行为，不是下载失败。

**Meta CDN 的图片地址是签过名的，会过期。** `oe` 参数是**十六进制**的 Unix 秒（`0x6AA7C003` = 2026-09-14T09:36:03Z）。⚠️ 按十进制解会得到四千年后的日期，于是所有过期地址都被判成"还没过期"，归档里下不回来的图就永远说不清原因。签名改一个字符即失效，**续不了期，只能重新取地址**；过期后再请求得到 403 URL signature expired。因此缺图的帖不能靠重下归档里那个 URL 恢复，必须开一次详情换新地址（条件见 [REQUIREMENTS](REQUIREMENTS.md)，只在人工恢复时）。换地址只认逐位对齐的同一张，且比较时要忽略边缘主机与 `oh`/`oe`/`_nc_*`——那几项每次请求都不同，稳定的只有文件名段；但 `stp=` 这类尺寸/裁剪参数**不能**忽略，它变了就是另一张派生图。

**视频地址不能用来判"来源变了"。** 视频按设计只存元数据，归档里**没有任何会变旧的字节**；而 Meta 每次响应都换一批地址——边缘主机、路径里的对象句柄、`efg`/`vs`/`oh`/`oe` 全是临时的。图片有 `sha256` 可比所以躲开了这条，视频没有，判据就落回地址，于是同一条视频每轮扫描都被判成来源变化、每轮都重采一遍。⛔ **视频只认 `source_media_id`**（`content_revision()` 一直是这么认的，`same_source_media()` 与它对齐）。这条在 2026-09-22 的真实扫描里现形：视线内 3 篇视频帖 3 篇全中，8 篇图片帖一篇没中；详见 [§1.28](#128-视频地址轮换被判成来源变化2026-09-22)。

**读原图时的 WinError 32/33 不是坏文件。** 审校台的图片预览会占住原图，此时读取抛 `OSError`。⛔ **不要把读取异常直接判成"原图损坏"**——那会把一张好图连同整篇推进人工队列，而下一次读它又是好的，队列因此永远清不空。`core/store.py` 的读取侧与 `localize/images.py:_change_image_file` 的写入侧一样，只对 32/33 限时重试；读取期间 mtime/ctime 变化同样按瞬时处理，不算损坏。

**夜间首屏与中断。** 普通轮次读内嵌帖子/身份 JSON 并被动等迟到接口，仍不滚动。截止前不启动预计来不及的下一篇：未开始记 `deferred`，已开始失败才是 `manual`。原图复用先核验本地字节；同媒体仅签名参数变化时零下载，路径/变换变化不吞掉。完整同帖来源与本地原图充分匹配才允许离线补日期/完整性，不采用冲突正文、归属或媒体。细节与离线证据见 [§1.20](#120-夜间监测首屏与采集恢复修复2026-09-20)。

**模型调用名。** 正文当前是 `deepseek-flash`（旧 `deepseek-v4-flash` 已退役并暂时路由到新版），另允许 `deepseek-v4-pro`；退役名称在请求前直接拒绝。图片默认 `gpt-image-2`，可选 `gpt-image-2.5-flare` 与 `gpt-image-2.5-sunburst`。⛔ **笼统的 `gpt-image-2.5` 不是调用名**，两个变体不互相映射，费率和用量按完整型号分表；历史账本里的旧占位名不猜成某个变体。请求 Pro 却收到 Flash 仍报错并停止整批。

**模型目录查询。** 带 Key 的 `GET /v1/models` 反映的是该 Key 的配置。合法空列表只能用同一网关、同一凭据补查 `GET /v1/models/{model}`，详情 `id` 必须精确一致；非空但缺型号、结构异常或查询失败都在付费前关闭本批，不靠付费 edits 试探。`model_verification=catalog` 只证明元数据确认，**不证明计费权限或远端实际路由**。

**Windows 文件占用。** 图片预览会占住旧文件，曾阻止跨格式上传；只对共享冲突限时重试，不放宽业务版本检查。部署安装也复现过目录重命名占用，只重试本地重命名最多 3 秒，**仍然拒绝覆盖既有版本**。

**测试 HTTP 宿主的事件循环。** Python 3.12.9 的 Windows Proactor 在连接清理遇到 `WinError 10054` 时，`socket.shutdown()` 抛异常会跳过 `close()` 和 transport detach，让 Uvicorn 的 `wait_closed()` 一直等——CI 里的表现是业务断言全过、宿主退不出来、制品不产出。⛔ **修法是 `tests/http_fixture.py` 用 `asyncio.Runner` 走 Selector 循环，不要改全局事件循环策略**（Playwright 子进程仍需 Windows 默认循环），也不要靠忽略退出错误放行制品。

**减少动画偏好下的弹层定位。** 全局把 `transition-duration` 设成非零的 `0.01ms !important`，会让弹层的同步测量读到 `-1000vh` 的过渡起点，菜单跑到屏幕外（隔离 Chromium 实测 `y=-7300`）。⛔ **过渡时间必须是 `0s`**，动画时长可以留 `0.01ms`；机制见[上游 #618](https://github.com/react-component/trigger/issues/618)。

**采集表的每页条数。** 运行状态「新帖采集状态」在浏览器里对当前记录分页。`pagination.pageSize` 是受控值；写成固定的 `10` 时，条数菜单的选择会在配置合并里被盖回 10。初始条数用 `defaultPageSize`，并显式打开条数选择。离开本页或整页刷新不要求记住条数。离线证据：`tests_browser_workflow.test_13_capture_table_page_size_follows_the_size_changer`，78 条夹具，在 `state/monitor-page-size/`。

**上传绕过刷新等待的测试写法。** 按钮禁用时直接设置隐藏文件输入会绕过页面的刷新等待，得到 409。测试要点击可用按钮后经文件选择器上传，不放宽业务版本检查，也不自动重试。

**开发代理的 Host。** Vite 代理改写 Host 之后与浏览器 Origin 不符，会被来源检查拒绝；开发代理保留原 Host。

**CI 环境的三个坑。** job 级表达式取不到 `runner.temp`，要在 runner 启动后的步骤里写 `GITHUB_ENV`；runner 的账户临时目录是 `RUNNER~1` 短路径，要在 runner 下显式创建临时目录；生产运行标识不能在普通单测之前注入，否则单测会进维护状态。

### 1.3 证据边界

`state/` 不随 Git 提交，本文的相对链接从主检出解析。⛔ **清理任何 worktree 之前，先确认它引用的证据不是只存在于那一个 worktree 里**——证据没了，结论按[第三节](#三证据的说法要准)要跟着降级。

2026-09-19 核对：本文原先引用的 147 条 `state/` 证据里，**4 条在任何工作树中都已找不到**（`stage1-hygiene.log`、`stage1-ui-build.log`、`stage1-ui-tests.log`、`offline-browser-20260915T121321Z-33388/report.json`，都属于原阶段一实施现场）。2026-09-20 清理已合并工作树前，已把其中独有的日志、截图和报告按 SHA-256 拷到主检出 `state/`（可重建的轮子、CI zip、release 包未拷）；清单在 `state/worktree-cleanup-20260920/preservation.json`。要重新引用，先确认主检出 `state/` 同名目录存在并按 SHA-256 核对。

2026-09-22 清理了余下 11 个已并入 main 的工作树。本文引用但当时只存在于其中的证据已拷到主检出同名路径，**本文的相对链接因此无需改写**；清单在 `state/worktree-cleanup-20260922/preservation.json`，拷贝脚本 `state/preserve_worktree_cleanup_evidence.py`（`.jsonl`/`.sqlite`/`.lock` 一律不跨实例搬运，可重建的测试轮次未拷）。`calendar-data-sync-fix-646a9e`、`social-media-image-verification-fix-938aa1`、`story-insights-ci-timeout`、`manual-schedule-readiness` 四个工作树仍在，未纳入这次清理——⚠️ **本文引用的 `state/calendar-data-sync-fix/`、`state/ci-evidence-35693145553/`、`state/offline-validation-20260922T071604Z/` 和 `state/story-insights-ci-timeout/step14-*.log` 仍只存在于那几个工作树里**，从主检出解析不到，清理它们之前照上面一条先保全。

### 1.19 待审核列表按原帖时间降序（2026-09-19）

分支 `codex/review-newest-first`，基点 `f05f8bb`。两个平台的四个子分类原先沿用候选排期升序，无排期时按任务 ID 排列；现统一按原帖发布时间从新到旧展示，筛选、分页和详情前后导航继承此顺序。候选排期仍按旧帖优先分配。

**验证状态：离线通过。** [修前回归](../state/review-newest-first/order-before.log)的 8 个平台／子分类场景均因顺序不符失败；[修后浏览器报告](../state/review-newest-first/browser-after/report.json)及同目录 8 张列表截图可复核。测试用 24 篇临时图文记录覆盖待我审、未就绪、已挂起、已处理，检查分类筛选、接口分页、跨时区原帖时间、前后导航及候选排期分配；[Web 审校 54 项](../state/review-newest-first/tests_web_review.log)、[历史 6 项](../state/review-newest-first/tests_history.log)、[查询索引 13 项](../state/review-newest-first/tests_query_index.log)和[前端构建](../state/review-newest-first/build.log)通过。构建保留已有大 chunk 提示。

工作树 archive/state/.env 独立，测试写入临时归档，浏览器仅连接隔离本地服务，无真实账号、模型、抓取或发布操作。证据在主检出 `state/review-newest-first/`，不随 Git 提交。服务机更新及业务人员复验为 **待真实联调**，见 [MANUAL_STEPS §13](MANUAL_STEPS.md#13-更新并启动审校台)。

### 1.20 夜间监测首屏与采集恢复修复（2026-09-20）

分支 `codex/monitor-capture-recovery`，基点 `984f4c4`。
用户提供服务机 `92e8718` 的持续运行日志与三份完整 capture；本轮修复基于当前主干，保留已有 IG 自动核验。
归档与状态使用工作树默认独立路径，验证进一步使用临时目录，只复用主检出 Python，没有复制凭据。

| 输入 | 原始捕获离线结果 | 能确认的边界 |
|---|---|---|
| `_capture_delta_1789866868.json` | 9 payload、0 帖子节点/目标帖 | 没取得帖子时间线；不能由此选定迟到、HTML 内嵌或页面未加载中的哪一种 |
| `_capture_delta_1789860525.json` | 29 payload、9 目标帖、8 图/1 视频 | 时间线结构可解析；`122127190695379375` 已有完整单图来源及 `2026-09-16T05:43:50Z` 日期 |
| `_capture_delta_1789857599.json` | 20 payload、59 帖、57 目标帖、2 拒绝；其中 35 篇早于捕获时刻前 30 天 | 四屏返回到五月的历史；日志/粘贴状态指向整轮预算取消，不证明某张图片长期卡住 |

三份原始文件保留于用户 `D:\Download`，不入 Git；SHA-256、重放脚本和结果在
[离线重放报告](../state/monitor-capture-recovery/replay.json)及同目录。FB 原响应与包装成页面 JSON 后的
受控字段提取逐帖比较一致；失败样本现在明确提示未取得时间线。首屏读取内嵌帖子/身份 JSON 并被动等待
迟到接口，默认最多从导航起 18 秒，普通轮次仍不滚动，整轮 90/300 秒及失败配额保持。

用户确认使用固定的初始化基线起点 `enabled_at - lookback_days`：更早且未归档的帖只记观察事实，
已有归档仍核对变化。重放使用**捕获时刻初始化的隔离基线**得到 22 篇处理、35 篇仅观察；
服务机须用其真实已有基线，不能直接套这个数量。原图复用先核验本地字节，同媒体仅签名参数变化时
零下载；路径/变换参数变化不吞掉。以原捕获构造签名刷新后的第二次观察为零请求、零旧帖处理，
这不是两份真实连续捕获，不能据此断言用户每次重复处理都由签名引起。

截止前不启动预计来不及的下一篇；未开始记 `deferred`，只接受后续自然扫描新证据，已开始失败及人工
恢复前置失败仍为 `manual`。~~旧人工项不批量改写。~~ ⚠️ **这条已于 2026-09-21 撤销**：本轮修复只改了
以后怎么记，没管已经记下的，于是服务机 37 条人工项里 33 条是这一版留下的、永远不会再被排程的死账。
现在由 `--retire-stale-manual` 显式退役，判据见 [§1.27](#127-原图不可用按原因分类过期地址恢复与陈旧人工项退役2026-09-21)。完整同帖来源与本地原图充分匹配时可离线补日期/完整性，
不采用冲突正文/归属/媒体，不触发新请求。目标 FB 帖用真实来源与合成原图重放为完整、零额外下载，
人工文案字节保持；不能称为服务机原图已恢复。通知只新建当前结果的卡片，旧事件及已冻结发件箱保留。

**验证状态：离线通过。** [新增回归最终 21 项、通知服务及通知兼容共 3/3 脚本](../state/offline-validation-20260920T022743Z/results.json)
与[入口/生命周期/媒体/hygiene 6/6](../state/offline-validation-20260920T022153Z/results.json)
包含真实隔离 Chromium 的内嵌 JSON 与延迟接口两场景，全部请求由本地路由拦截。
[另外 12/12 脚本](../state/offline-validation-20260920T021406Z/results.json)覆盖归档核验、存储、解析、
FB 身份/媒体、访问及调度；[存储/监测等前期 7/7](../state/offline-validation-20260920T021134Z/results.json)另存。
前端运行页 12 项单测及 TypeScript/Vite 构建通过，保留既有大 chunk 提示；命令输出已核对。
首轮新增回归 [8 项预期失败](../state/offline-validation-20260920T020601Z/results.json)、
[人工恢复边界失败](../state/offline-validation-20260920T021306Z/results.json)、
[内嵌身份与过时通知失败](../state/offline-validation-20260920T021603Z/results.json)、
[响应读取收尾超时失败](../state/offline-validation-20260920T022015Z/results.json)、
[同结果跨扫描漏报](../state/offline-validation-20260920T022509Z/results.json)与
[通知与新采集交错漏报](../state/offline-validation-20260920T022708Z/results.json)均保留。
通知兼容首轮旧夹具未登记开始便预期人工失败，已改为真实开始后模拟重启；最终兼容 23 项通过。
[验证汇总](../state/monitor-capture-recovery/validation.json)逐脚本引用最近记录，不把多轮结果写成一次整库全绿。
[独立复审](../state/monitor-capture-recovery/review.md)已关闭原四项及跨轮通知漏报；
其后主代理另以失败/成功回归验证采集进行中不提前确认旧事件。

证据在主检出 `state/`，不随 Git 推送。没有连接真实社媒/业务 Chrome、CDN、模型、飞书或发布。
服务机普通首屏来源/到达时间、晨扫完整执行、原始图片与两篇未覆盖旧异常仍为 **待真实联调**；
按 [MANUAL_STEPS §14 D](MANUAL_STEPS.md#d-核对事实与卡片)更新并在正常节奏复验。

### 1.21 G1 录证迁移与静态控件名称（2026-09-20）

**G1 已关闭，验收范围按用户本次决定调整。** 用户确认发布服务器为北京时间、Instagram 共通
操作与 Facebook 一致，并取消画幅、正文计数、排期边界及输入行为的人工测量前置要求。
`observations={}` 和缺少单独 IG 历史详情不再阻塞 G1；这些决定不改写原录制或声称 IG 已提交。

独立分支 `codex/publish-probe-g1` 使用主检出的
`state/publish_probe_20260920_100921_378853.json` 及同名截图目录。
原始 SHA-256 为 `5403e869bcb9bb12b7fa23b52ac4792e6eb12501fc93dc33b860276d19dd1b2e`。
录制来自服务机 `C:\Users\admin\.fbscraper-publish`、9223，含 232 条交互、204 条快照及有效 final。

| 证据或决定 | 来源 | 当前用途 |
|---|---|---|
| 北京时间 | 用户明确确认 | `timezone` 与 `ui_timezone` 均为 `Asia/Shanghai`，提交时另核对设备偏移 |
| FB 账号 / 提交 / 成功 | 快照 17、交互 205、快照 136 | 已生成当前信号注册 |
| Planner 月份 / 条目 / FB 详情 | 快照 141、180、184 | 五项结构信号及同页因果链全部回查通过 |
| IG 详情结构与 FB 共通 | 用户明确接受 | `instagram_detail_basis=shared_facebook_structure`；当次 IG ID、账号和正文仍须实际回读 |
| IG 10 图 / 30 标签 / 20 分钟至 29 天提示 | 快照 117/118、108，截图 187、124 | 保留历史事实，不据此构造 FB 窗口或要求补齐其余测量 |

严格组装默认不加载 IG 数量/画幅/正文计数硬限制；平台排期提前量不设猜测上限，交实际 UI 校验。
仍检查内容/图片版本、当前月历覆盖、未来时刻及同渠道 90 分钟间隔。显式注入旧实测契约的内部调用
继续核对其来源和值；常规发布不需要这些注入参数。`ui_constraints_verified` 保留兼容名称，
表示接受绑定的控件录制，不再表示完成 18 项人工观察。

迁移校验只读取 dump 旁同名直属截图目录，不回退旧机器绝对路径，JSON 原字节不变。
录制角色按专用 profile 名称与端口核验，实际 Chrome 附着仍使用本机完整隔离路径。
录制器已修复静态输入名称丢失；既有共通控件继续复用，不要求为恢复旧 dump 脱敏掉的名称重复录制。
`--set-note` 必须搭配 `--fill-notes`；观察笔记仅作可选诊断。

[原始证据只读验收](../state/publish-probe-g1/g1_acceptance.json)通过 G1、账号、提交、回读、
FB/IG 窗口和严格 CLI 时区入口，379 张引用截图与原 JSON 哈希均未改变。
代码回归使用隔离文件、假服务及本地 Chromium；没有连接真实 Chrome、登录、发帖、排期或改写业务账本。
本机报告及相关测试日志已按 SHA-256 保全到主检出的 `state/`，清单见
`state/publish-probe-g1/integration-preservation.json`；原录制保留原位，均不随 Git 推送。

[本轮验证汇总](../state/publish-probe-g1/g1-validation.json)：12 个相关脚本的最终结果全部通过，
覆盖发布/v2、录证契约、规划、批准、单渠道、月历读取/回读/API、预检、运行状态与 hygiene。
初轮月历夹具依赖旧美西默认值，固定其历史时区并补北京时间用例后复跑通过；原失败日志保留。
独立复审发现的当前时刻建议和可选约束注入问题均已修复并补回归。文本换行、diff 已核验。与当前主分支上线配置整合后，
[录证、预检、规划、月历 API 与 hygiene 5/5 定向回归](../state/offline-validation-20260920T034413Z/results.json)通过。

G1 的真实录制结构验收不等于整套生产系统已激活：专用渠道/月历文件、实际单渠道回执和远端图片读取
分别由对应能力与 G8 管理。FB 样本的 Story 曾开启，不能据此声明仅 Feed 真实提交通过。
后续若这些能力缺失，按其具体名称报告，不回退 G1 或再要求补测已取消的 UI 边界。

### 1.22 月历时间子节点与详情就绪（2026-09-20）

服务机月历刷新在 `2026-09-30 17:30` 失败，条目快照只有 `5:30\u202fPM`，href/aria 为空。
已有 `publish_probe_20260920_100921_378853.json` 足以定位本轮修复，不需要重录 G1 或重复 calendar probe：
月份快照 142 含 `30 5:30 PM`；同一手工排期的周视图点击 225 记录了时间 link 与上方第 3 层的正文 aria；
快照 180 出现完整正文和 `September 30, 2026, 5:30 PM` 的 link；181–184 的详情从
`Loading preview` 变成账号及正文。原截图 `225_click.png`、`semantic_181_periodic.png`、
`semantic_184_periodic.png` 均在 §1.21 的录制同名截图目录中。窄不换行空格并非此次已确认根因。

旧读取器只取时间节点自己的 text/aria，再复用最初快照，漏掉父级正文及悬浮后出现的完整条目；
详情也曾只等容器/日期字段出现就取值。现在限定在单张卡片内收集父级标签、限时重新读取，
悬浮后的完整日期 link 必须与该卡正文及格子时刻一致；已有正文/链接/时刻变化仍拒绝。
详情等待所需正文、账号、渠道、ID 就绪与短暂稳定，不等待无关统计。最终整月复扫、推荐依据与发布锁不变。

隔离 Chromium 已复现旧异常并验证新路径；缓存/API 回归确认失败保留旧数据及观测时间。
`refresh_diagnostic` 仅保留日期、时刻、条目索引、读取阶段、字段长度/存在性，不保存正文、URL 或原始异常。
**修复代码为离线通过；服务机当前月份仍待真实联调。** 录制能证明上述结构存在，不能替代服务机拉取后的完整刷新。
最短复验见 [MANUAL_STEPS §8.1](MANUAL_STEPS.md#81-月历更新单条取证与后续复验)。

### 1.23 月历内容类型兼容与无正文 Story（2026-09-20）

服务机新失败在 `2026-09-04 18:39` 的 `published_detail`。用户截图明确显示
`Story · Published on: Fri Sep 4, 6:39pm`、`This content has no text` 和可见预览；
这证明存在合法无独立正文的已发布 Story，不能再把所有内容当成普通有正文 Feed。
旧读取固定第一个三级标题、日期节点内的 `strong` 作者和相邻平台图标；这些字段全部就绪才通过。
截图没有 DOM，尚不能断言服务机具体缺哪一个节点。上次 §1.22 离线通过不等于整月真实通过。
[旧实现隔离复现](../state/planner-content-compatibility/baseline-reproduction.json)确认：语义样例中缺日期区作者时详情不就绪，
即使补齐旧结构也会把 `This content has no text` 原样误作正文。这只证明旧契约缺陷，不证明真实页恰好使用该 DOM。
按可见日期/时刻匹配的取证已收到服务机日志；原文及 SHA-256 保全在
[现场取证汇总](../state/planner-content-compatibility/story-detail-20260920/summary.json)。
实际聚合 `content_id=18084155825688886`，初始无正文提示是三级 heading；日期区域只显示 Story/时间，
不能再要求那里具有普通 Feed 作者。三个渠道 tab 的 id、aria-controls 均为空，没有 tabpanel，
生产读取器要求的关联在该页面上不存在。`instagram_story_preview_frame` 内出现 `neakasa.de`；
这能定位 IG 预览，不能据此把聚合 ID 认定为独立 IG 或 FB 内容 ID。

首次切换日志包含 Facebook/Instagram 三阶段样本；
[本地摘录](../state/planner-content-compatibility/story-detail-20260920/followup-observations.json)是从消息人工转录的观察摘要，非原始 DOM 文件。
IG 就绪样本有无正文提示、18:39 元数据及 `neakasa.de` 的 Story 预览；Facebook 标签已选中，但
`Loading preview` 在即时及两个所谓 settled 样本里一直可见。标题长度 10 的文本仍未知，不能猜成加载提示。
工具仅检查标题/标签便宣布就绪是明确缺陷；现在同时检查可见的 `Loading preview`，超时保存最后状态并继续另一渠道，
最终输出 PARTIAL。统计区自身的错误或进度条不阻止预览取证。日志 JSON 用 ASCII 转义，避免 PowerShell 管道损坏中点。

首次切换仅收到 `tofu_metrics_query/TofuErrorQueryResult`（HTTP 200，错误标题/消息长度 38/71）；
它不证明 Facebook 没有发布，也没有提供独立内容 ID。首次内容可能在监听启动前已返回，这是待验证假设。
`--reload` 在监听启动后仅重载已匹配的详情一次，采集首次加载自然返回的响应，以及该页面已有的惰性 JSON 数据；
不执行嵌入脚本，不主动调用接口。`--responses` 单独使用仍只观察切换响应。
两种来源都仅输出白名单对象 ID、类型、时间与账号关系；正文只保留长度，丢弃凭据字段，不访问 cookie、请求头或浏览器存储。
响应到达时的标签名仅是时间上下文，不是渠道归属证明；仍需根据对象关系核验。数量、体积及等待均有界，
嵌入 JSON 超过采集上限会在 EMBEDDED_SUMMARY 报告跳过数量。

**首次加载补采已收到。** [原文及字段摘录](../state/planner-content-compatibility/story-first-load-20260920/summary.json)
保全 250078 字节、53 条 JSON 记录，SHA-256 为 `e8550e119e3a86495b23a0d968783d0b246423182b319d5fcbf7cc56ca64dc93`。
这次 Facebook settled 样本的预览已加载完成，不能继续沿用“FB 仍在加载”的结论。
用户随后人工确认：FB 标题是 `Your Story`；IG 与 Total performance 均是 `This content has no text`。
`Your Story` 只是界面标题，既不是正文，也不足以证明空正文。

`data.tofu_entity.entity_info` 为 `TofuIGPostEntityInfo`，同对象 `ig_media.id=18084155825688886`
严格等于详情的 content_id，且 `ig_media.permalink` 为 `neakasa.de` 的 Stories 链接。
其 cross_posted_entities 包含 FB Story 以及相同 IG 媒体，因此 IG 原生身份和跨平台关系已有直接证据。
Stories 链接末尾的公开 ID 与 ig_media.id 属不同字段，不强求相等。
另外收到的 `BusinessFBStoryContent.id=1068553422207259`、`BusinessIGStoryContent.id=2112279096392067`
是后台内容记录；它们虽与目标账号和 18:39 对应，但缺少与当前 FB 实体的直接连接，不能写入 native remote_ids。
同理，其 creation_time 尚不能替代当前实体的渠道发布时间。统计错误不能证明内容不存在。

IG 读取现在在新详情导航前被动监听自然响应，结合媒体 ID、Story permalink 账号、响应 title、
选中的 IG 标签、IG 平台标记、已就绪 Story 预览及一致的渠道日期/时间核验。
不依赖该页面没有提供的 aria-controls，不拿“选中标签并等待一会儿”单独证明共享标题归属。
允许标签在标题之后加载；用时仍受原读取预算约束。一个渠道未完成时保留已核验变体并追加未知占位与诊断。
FB 的身份补充和正文未知规则见下方单条生产验证；不会借用 IG 的 ID、空正文或另一个后台记录补齐。

**单条生产验证与三个筛选截图已收到。** [原文及摘录](../state/planner-content-compatibility/story-reader-20260920/summary.json)
保全 9094 字节、9 条 JSON，SHA-256 为 `8bf2754178dcaaf9d96693734b482aeada28c994588f585f5efbf59f9d933ef4`。
读取版本 d9196af 的 IG 变体已返回 complete：原生 ID 18084155825688886、账号 neakasa.de、
2026-09-04 18:39、story、caption_status=empty；仅这一单条 IG 路径为真实通过，整次读取仍 partial。
根 IG 对象的 cross_posted_entities 现在明确提供 FB Story entity_id=1781315906229402，
其 owner.entity_id=61578176852811、类型 TofuFBProfileWithBizToolsEntityInfo；另一次 FB 实体响应的 entity_id 相同。
这是直接实体关系；不是此前单独 BusinessContent 响应中的 ID，也不解码 opaque story_id 来猜身份。
同一 FB 对象给出 created_at 整数和 owner.title，但日志只保留类型/长度，不能声称具体秒值或原始标题已从该日志核对。
运行期逐项检查实际字段，并将 FB 自己的时间按 UI 时区核对所选 FB 页头，不复制 IG 的分钟。
⛔ **跨发的 Story 预览里嵌着被分享的原卡片，它自带账号行，所以 owner 永远不是该区域里唯一的账号名**——
"唯一作者"这个判据在真实页面上不可能成立。2026-09-20 服务机 `--verify-reader` 卡在
`facebook_story_preview_owner`，根因就是这条：读取器认的是 `IMG` 作直接子节点、账号在相邻且无嵌套的 `DIV`，
而三张截图里的预览是「头像 + 账号行」外面再套一层被分享卡片。[隔离复现](../state/calendar-data-sync-fix/preview-owner-repro.json)
用五种与截图一致的结构验证：旧判据只在单元夹具那一种形状下通过，其余四种全假。
夹具当初是照着判据写的，所以测试一直绿——**不要再把它改回一行作者**。
现在的判据是：可见的 `#instagram_story_preview_frame` 不存在（IG 视图保留自己的框，这是两个渠道的结构分界），
且区域内存在恰好等于 owner、其子节点都不等于该文本、三层祖先内有图像的节点。
仅预览标题存在或 Loading preview 消失仍不足以证明 FB 预览已切换。
Total performance 与 Facebook 两个页签的预览完全相同，预览本身不能区分这两者，靠的是页签选中与表头平台图标。

[三个原始截图及 SHA-256](../state/planner-content-compatibility/story-reader-20260920/screenshots.json)确认：
Total performance 与 Instagram 标题均为 `This content has no text`；Facebook 为 `Your story`（小写 s）。
三者显示 Sep 4, 6:39pm；Total/FB 预览账号 Neakasa Deutschland，IG 为 neakasa.de，预览均可见。
截图证实用户此前的描述；FB 0 浏览不能证明未发布，预览内嵌原帖文字不能当作 Story 独立正文。
已核验直接 FB 实体、owner、渠道、发布状态和时间时，Your story（兼容大小写）记正文 unknown、空字符串，
允许 complete 占用记录；它不表示正文为空或全文已读。普通 Feed 与 scheduled 全文回读规则保持。
身份、时间或多实体关系不一致仍保留已核验 IG，加未知占位并阻止决策。

现有录制 191–204 还显示聚合 Post 切换渠道后，FB 为 19:17、IG 为 19:18；不能把聚合时间、
正文或 `content_id` 复制成两个渠道记录。合作作者不能替代目标 owner；预览中的分享源同样不能替代。
官方资料及逐类处理矩阵见 [FUNCTIONALITY F5-5.1](FUNCTIONALITY.md#f5-51内容类型与证据覆盖)。

本地实现将位置、媒体形式、发布状态、账号/渠道、关系与读取状态分开；明确无正文提示转换为空正文状态。
媒体形式缺少结构证据时保留 unknown，不从缩略图猜单图/视频/轮播。已发布详情按元数据与独立渠道证据读取，
不等待互动统计。无证据的类型/身份继续形成未核实条目，不计为推荐时段或空档。
部分结果单独保存 `partial_inventory/partial_observed_at`；此前完整数据与观测时间保留。
槽位判断、排期回读和远端删除登记必须使用决策完整的数据。既有同渠道 90 分钟规则不因 Story/Reel 豁免。

**2026-09-21 服务机现场：9 月 4 日两个渠道已真实通过（`complete=true`），整月仍 partial，7 条未核实。**
日期格与条目本身读全了（`2026-08-30`–`2026-10-03`、`grid_complete`、`entries_complete` 均 true），缺口按类型分五类：

| 条目 | code / 缺失 | 现状 |
|---|---|---|
| 09-05 11:48、09-06 02:09 Feed、09-08 23:36 Reel | `unsupported_type` / `channel_identity_adapter` | **已按现场结构实现 `read_media`**，离线通过，服务机待复验 |
| 09-15 19:17 #1 | `identity_unverified` / `instagram_channel_tab` | FB 单渠道 Story 被路由进跨发读取器，死等不存在的 IG 页签。`read_facebook_only_story` **已真实通过**（第三次刷新起不再出现在诊断里） |
| 09-15 19:17 #0 | `identity_unverified` / `channel` | 同格另一条，FB+IG 双图标的聚合 Post；页签迟挂载被提前采样。页签判定已改为按表头徽标等待，**逐渠道适配器 `read_aggregate` 已按现场结构实现**，离线通过，服务机待复验 |
| 09-27 17:00 | `read_failed`，阶段 `item_ready` | ⭐ 图标项是**平台按历史数据推荐的活跃时段占位**，不是真实任务（用户明确）。tooltip 原文其实没变；第三次刷新已被正确跳过，推荐时刻每次会挪位置 |
| 09-30 17:30 | `structure_unknown` / `item_caption` + `recommendation_absent` | 真实 IG 排期项。新诊断把两件事分开了：悬停确实没出 tooltip（**本来就不是推荐，判定正确**），真正缺的是这张卡的完整正文证据。⛔ 仍未解决 |

**2026-09-21 第二次刷新：未核实从 7 条降到 3 条**（09-15 两条 + 09-27 一条），`read_media` 在服务机真实通过。
剩下三条这一轮的根因如下，都不是先前猜的那个：

- **FB 单渠道 Story 不是没有可用字段。** 补上 `entity_id` 脱敏后重跑证明该页把自己的 content_id 写在
  `tofu_object_insights.entity.entity_id` 上，`entity_info.__typename` 为 `TofuFBStoryEntityInfo`。
  真正的缺陷是路由：`read()` 把所有 `Story` 都送进以 IG 为根的 `read_story`，它死等一个不存在的 IG 页签。
- **聚合 Post 缺的不是渠道，是时机。** `read()` 只在表头就绪时数了一次渠道页签，页签晚于表头挂载，
  于是双平台详情走进单渠道分支，`chosen` 为空，30 秒后报 `channel`。⛔ 这个诊断是误导：页面从来不缺渠道。
- **⭐ 占位项的 tooltip 原文没有变。** 用户悬停截图与代码里的常量逐字一致，所以先前"文案改了"的判断是错的。
  同一次读取里 9/24 的 ⭐ 被正确跳过、9/27 的没有，差异还没定位。

**FB 单渠道 Story 的字段契约（2026-09-21 `--reload`）。**

- 身份用 `tofu_object_insights.entity.entity_id`（严格等于 content_id）+ `entity_info.entity_id` 双向确认。
- 发布页只以 `entity_info.lwi_info.page_id` 的形式挂在实体上；页名取**同一份响应里 `id` 等于该 page_id**
  的页节点（`data.page` 或 `owning_page_for_graphql`）。⚠️ `supported_actions[*].entity.entity_info.owner.entity_id`
  是 `61578176852811` 这种 profile 标识，不是发布页，不能拿来取名。
- ⚠️ `tofu_business_content.contents[0]` 里有 `creation_time` 和 `content_owner`，但它带的 `id` 是业务内容 ID
  （`1054899014212712`），整份响应不出现 content_id，**无法与本条绑定**，所以时刻仍按表头与日期格比对，不引用它。
- 该页没有任何渠道页签，只有 `Total`/`Audience` 指标页签；`relationships` 记空，不得写 `cross_platform`。

**IG Feed/Reel 的现场结构（2026-09-21，`--reload`）。** 这几条推翻了先前从截图得到的猜测：

- ⛔ **表头正文没有被截断。** 响应 `title_length` 与 DOM 三级标题长度只差 2（532/530、718/720，空白折叠），
  可见的 `…` 是 CSS 裁剪。**不要因为截图里有省略号就去别处找全文。**
- 身份绑定用 `data.instagram_post.id`（严格等于 content_id）同对象的 `bizlink_instagram_actor.username`。
  ⚠️ 同一份响应里的 `viewer.username` 是登录账号，不是作者，不能拿来补。
- 预览作者有稳定 ID `#caption-author`，两个样本都在；旁边的 `#caption` 是内嵌正文副本，不是归属证据。
- 合作关系就写在 `Published on:` 那个节点里：`Post · Published on: … · neakasa.global in collaboration with neakasa.tech and neakasa.de`
  （长度 39+3+14+23+27=106 闭合，Reel 同样闭合）。据此记 `collaboration`，不从别处推。
- 这两条帖的 owner 是 `neakasa.global`，与配置的 `neakasa.de` 不同。用户决定：**如实记 `neakasa.global` 并占槽**
  （合作内容会出现在合作者主页，确实挤占发布节奏）。`classify_published` 因此只对响应已核验的 owner 放行差异，
  DOM 推出来的 owner 仍须等于配置账号。`occupied_for_channel` 按渠道选卡片、与账号无关，冲突逻辑不用改。

**聚合 Post 的字段契约（2026-09-21 `--reload`，content_id=122187915260939228）。**

- ⛔ **这一类详情没有 `role=tabpanel`。** 选中渠道是把同一个表头就地改写：徽标、正文、时刻全换。
  按 tabpanel 取作用域的既有聚合路径在真实页面上不成立，因此按「有没有 tabpanel」分流，无则走 `read_aggregate`。
- **每个渠道有各自的分钟**：Facebook `7:17pm`，Instagram `7:18pm`；正文长度也不同（336/312，响应 334/309）。
  日期格只对得上 Facebook，所以整条的时刻校验只要求**至少一个**变体匹配，不得把一个渠道的分钟套到另一个上。
- 成员表在根实体上：`tofu_entity.entity_id` 等于 content_id、`entity_info.__typename` 为 `TofuFBStoryEntityInfo`，
  其 `cross_posted_entities` 给出 FB（entity_id 等于 content_id）与 IG（`18129143875786241`，带 `ig_media.permalink`）两个成员及各自 `owner.entity_id`。
- 名字在别的响应里，按**成员自己的两个 ID** 回接：FB 用 `entity_info.story.post_id` 等于 content_id 的那份，
  取 `feedback.owning_profile` 与 `actors[0]` 一致的 `name`；IG 用 `instagram_post.id` 等于成员 entity_id 的那份，取 `bizlink_instagram_actor.username`。
- ⚠️ 同一份 FB 响应里的 `feedback_context…viewer_actor.id`（`61589751128761`）是登录账号，不是作者。
- ⚠️ 这一版 FB 预览的 permalink 是 `permalink.php?id=<profile>`，**不带 `story_fbid`**，既有 Feed DOM 判据在这页取不到 remote_id；
  ID 只从响应取，不从预览链接凑。

**当前边界：9 月 4 日跨发 Story、IG Feed/Reel、FB 单渠道 Story 真实通过；聚合 Post 逐渠道适配器离线通过、服务机待复验；
09-30 17:30 那条真实 IG 排期项的正文证据仍为代码未完成。**
预览判据的修复在分支 `claude/calendar-data-sync-fix-646a9e`、工作树 `.claude/worktrees/calendar-data-sync-fix-646a9e`，基点 `c0e734c`；
**离线通过**，逐脚本结果与限制见该工作树 [state/calendar-data-sync-fix/validation.json](../state/calendar-data-sync-fix/validation.json)，清理前须保全。
[定向验证汇总](../state/planner-content-compatibility/validation.json)记录 11 个 Python 子系统脚本、
4 个前端测试文件（124 条）、生产构建和月历浏览器场景；月份浏览器回归 23 条，独立复审另跑 8 条针对性场景。
首轮两处测试夹具/断言不一致已修正并复跑，原日志保留。所有写入使用隔离临时数据；没有接入真实账号或调用模型。
[页面证据](../state/ui-regression/browser-stage-f.json)核对部分结果警示、旧缓存时间与无正文展示。
`tests_calendar_detail_probe` 的隔离回归覆盖可见元数据/Story 预览、脱敏、歧义拒绝、发布锁、
延迟页签保持原选择、切换期间加载、缺失渠道不报成功、预览加载超时后继续 IG、首次详情重载响应/嵌入 JSON 与 PowerShell 管道转义；
11 条定向测试、hygiene 与独立复审见 [取证工具验证](../state/planner-content-compatibility/story-detail-20260920/initial-load-validation.json)。
这些既有结果只证明离线行为；当前 IG 单条服务机结果另见上方原始日志。

[IG 路径定向验证](../state/planner-content-compatibility/story-first-load-20260920/validation.json)通过 7 个脚本：
Story 读取 7 条、取证 12 条、分类 8 条、月份 23 条、回读 5 条、缓存 15 条及 hygiene；共 70 条测试。
独立复审发现并修复 IG 标签延迟挂载时提前退出的问题，700ms 延迟夹具已覆盖；复审收尾无阻断项。
均为隔离夹具和临时数据，不升级真实验收。

[直接关联 FB Story 定向验证](../state/planner-content-compatibility/story-reader-20260920/validation.json)
通过 7 个脚本：Story 10 条、分类 8 条、取证 12 条、月份 23 条、回读 5 条、缓存 15 条及 hygiene，共 73 条测试。
成功读取分渠道身份、FB 正文 unknown、IG 正文 empty 与独立分钟；错误 owner/渠道/时间/预览、额外关系及无关联实体保持未完成。
独立复审发现的 FB 预览作者缺口已补齐，修前错误账号用例失败、修后通过；[复审记录](../state/planner-content-compatibility/story-reader-20260920/review.json)无剩余阻断项。
日志、截图与测试结果已按 SHA-256 保全到主检出同名 state 目录，均不随 Git 提交；FB 和整月真实结果仍待服务机提供。

按 [MANUAL_STEPS §8.1](MANUAL_STEPS.md#81-月历更新单条取证与后续复验) 使用 `--verify-reader`：
只对当前详情的新建副本运行生产读取入口，输出已保留变体和缺失字段，并补采根实体/跨帖关系的字段结构。
非数字 ID 若为 JSON 或 base64 JSON，只做有界解码再脱敏；此结果仅作诊断，绝不自动当作原生 ID。
不再输出整页重复 DOM，不刷新月份、不重录 G1、不写月历缓存。部分结果不是整月真实通过。
读取结束、副本关闭之前另输出一条 `PREVIEW_STRUCTURE`：预览标题与 IG 框的可见数量、是否仍在 Loading，
以及读取器那段区域搜索每一层看到的紧致文本节点（标签、三层祖先内有无图像、文字长度，账号名按既有白名单才给原文）。
上限 6 层 × 20 个节点。⚠️ **这条是为了让一次真实运行就能定位预览判据，而不是再照截图猜第六个选择器**；
失败时它和 `READER_RESULT` 在同一份日志里，别只回传结论。

⛔ **取证前先确认工具本身没在饿死证据。** 2026-09-21 踩到两处，都会让现场读起来像「页面没有这些东西」：

- **指标页签也是 `role=tab`。** `Total / Audience / Followers` 会被计入，单渠道详情因此被判成「渠道选择不明」而直接 STOP，
  普通模式和 `--reload` 都进不去。现在只统计 `Total performance / Facebook / Instagram` 三个名字。
- **读取器放弃得比响应到达还快。** 不适配的类型在表头出现后约 0.4 秒就抛 `unsupported_type`，副本随即关闭：
  当次只观察到 1 条响应且没有 `entity_info`，`PREVIEW_STRUCTURE` 六层里只剩标题本身。
  这是测量假象，不是「根实体不存在」。诊断观察者现在先被动静默等待（静默 1.5 秒、上限 10 秒）再取结构。
- **`entity_id` 被脱敏丢掉。** `content_fields` 的 `IDS` 原先没有 `entity_id`，标量落不进任何分支就被丢弃，
  于是 FB 单渠道 Story 看起来「没有任何字段回指 content_id」。已补进 `IDS`；`entity_identity_fields`
  本来就按 `_id$` 保留，所以 `--verify-reader` 和 `--reload` 之前给出的结论不一致。

没有渠道页签**不等于**单渠道详情——页签会迟挂载（已有回归覆盖 3 秒延迟）。工具照旧只在开头捕获一次原选择，
捕获不到就保留初始快照并报不完整，不会声称读全，也不会去点页签。

### 1.24 源码服务地址更新（2026-09-20）

**验证状态：离线通过。** `scripts/update_service_address.bat` 同步网络 JSON 与非受管飞书 URL；`scripts/run_web_lan.bat` 读取同一配置启动。初次交付时未替换主检出原地址，`10.66.6.3/24` 当时仅为测试及操作示例；后续实际配置以网络 JSON 为准。分支 `codex/service-address-updater`。

[定向回归 6/6 脚本](../state/offline-validation-20260921T062357Z/results.json)覆盖 Web 访问 14 项、启动批处理 8 项、受管部署 19 项、发布后台维护保护 25 项、更新器初版 9 项及 hygiene；[更新器最终 10 项](../state/offline-validation-20260921T062550Z/results.json)另补实际 `.bat` 的交互/参数模式、带空格目录、异目录启动和解释器绑定。独立只读复审再次执行更新器及启动脚本共 18 项通过，未发现阻塞问题。

测试均用临时配置和隔离数据；启动测试执行真实 Windows 批处理，但 npm 和最终 Web 服务器为夹具，没有连接真实账号、发送飞书、修改防火墙或读写业务账本。服务机拉取、监听、防火墙及同事电脑访问仍为 **待真实联调**，按 [MANUAL_STEPS §13.1](MANUAL_STEPS.md#131-源码服务机一键改址) 操作。日志在主检出 `state/`，不随 Git 提交。

### 1.25 Story/已发布媒体离线回归耗时（2026-09-21）

基点 `bfd846c`，分支 `codex/story-insights-timeout`。
[原版默认入口](../state/offline-validation-20260921T071100Z/results.json)复现 `tests_story_insights` 300.05 秒、退出码 124。
`month.read(timeout=5)` 的五秒是各读取步骤的预算，不是整次月份读取的总时限；负向子测试各自等待身份核验超时，
并在详情前后分别扫描两遍完整月历。逐格 `Locator.evaluate()` 会反复获取、执行、释放元素句柄；
[分段计时](../state/story-insights-timeout/before-profile.log)中七次 inventory 的十四次月历扫描占 65.97 秒，
详情占 36.98 秒，两个测试的 setup 合计 2.32 秒。该诊断与默认入口并行，计时包装也有开销，不能当独立运行基准。

扫描改用浏览器内直接快照，合并加载标记的零数量检查和同格条目/按钮读取；按钮仍由 Playwright 按角色及可见性定位。
仍逐日滚动、等待加载、验证日期/条目/展开控件，
要求两遍一致，并在详情后完整复扫。浏览器逐测试隔离、五秒负向等待和默认 300 秒脚本上限均保持。
媒体身份负向子测试逐项重置 HTML，避免 `preview_author` 污染后续 `viewer_only`/`platform`。
负向断言另核对 `published_detail` 阶段及各自的缺失字段，避免把加载/导航失败误当身份拒绝。
新增隔离 Chromium 场景覆盖滚到日期格才出现的加载器、`progressbar` 转 `aria-busy`、未加载完不能继续滚动、
迟到条目、完整复扫及扫描途中日期格消失；[定向结果](../state/story-insights-timeout/grid-boundaries-final.log)为离线通过。

两条慢测试用保留的基线源码及最终源码串行比较，无并行浏览器测试、无分段计时包装：
[修前](../state/story-insights-timeout/before-single.json)与[修后](../state/story-insights-timeout/after-single.json)记录
媒体身份五个子场景 69.13 → 47.13 秒，Story 身份两个子场景 27.87 → 18.77 秒，均通过。
[原版整文件诊断](../state/story-insights-timeout/before-full.log)15 条通过、375.96 秒；该次部分时间有其它诊断并行，
不能将其与后续串行回归的差额全部归因于代码。运行环境及可复跑的基线源码在同一证据目录。

**最终验证：离线通过。** [全量结果](../state/offline-validation-20260921T073026Z/results.json)104/104 脚本退出码均为 0，
默认 300 秒上限不变；Story 15 条全部通过、整文件 237.80 秒，月份读取 25 条通过、91.25 秒。
同轮覆盖发布/回读、缓存、取证、调度、Web 浏览器及 hygiene；前端生产构建通过，保留既有大 chunk 提示。
[只读复审](../state/story-insights-timeout/review.json)无阻塞项，原有 Story 15 个方法与基线副本的 Git 一致性另经核对。

用户报告的原始 `invalid='platform'` ERROR 尚缺完整堆栈，本轮修前整文件、修前/后单条及最终全量均未复现；
不能将它归因于已确认的 HTML 污染，也不能声称已证明其独立或已彻底修复。收到原日志后在本节续查；
`platform` 实际走 Facebook Feed 适配器，当前明确拒绝原因是 `owner`，不是 IG `media_channel`。
证据已按 SHA-256 保全到主检出同名目录，清单见 [证据保全](../state/story-insights-timeout/preservation.json)。
不涉及真实账号、模型、发布或业务账本，REQUIREMENTS §10 的验收判据及状态不变。

**2026-09-22 续：300 秒是脚本级预算，这一个文件已经装不下四类详情。** 工作树 `.claude/worktrees/story-insights-ci-timeout`，分支 `claude/story-insights-ci-timeout`，基点 `5a3152c`，已并回 `769d8f0`。
Windows release 在 main 上连续八次失败，`fbscraper-windows` 一直没产出，只剩 `fbscraper-test-evidence`——
打包与上传两步都排在离线全量之后，全量一失败就跳过。最近三次（`67daab4`、`5a3152c`、`1d75df6`）
`tests_story_insights` 与 `tests_service_address` 同时失败，两个都修好才会有制品。直接原因是
[run 35590918182 的取证](../state/ci-evidence-35590918182/offline-validation-20260921T105323Z/results.json)：
`tests_story_insights` 300.02 秒、退出码 124，另外 103 个脚本全绿；同目录日志显示它被杀之前跑完了 23 个测试里的 17 个、
没有失败，照这个速度整文件在 CI 上要约 400 秒。

⚠️ **"本机 287.97 秒、只剩十几秒余量"这个前提已经过期。** 那次
[本机全量](../state/story-insights-ci-timeout/local-20260921T103033Z-results.json)跑的是 **20** 个测试，
早于 `a773b51` 加进来的聚合 Post 一类；当前 main 的 23 个测试在同一台机器空闲时要
[356.23 秒](../state/story-insights-ci-timeout/baseline-single-quiet.log)，本机也已经越线。
上一轮把它压到 237.80 秒，这一轮再加三个场景就又满了——症状不是某处变慢，是一个脚本装了四类详情。

**没有调高上限，两个理由。** 其一，要盖住现在的 356 秒得放到 420 秒以上，等于把这条线整轮往后挪；
其二，"CI 机器慢"这个说法不成立：[同一份提交的逐脚本对照](../state/story-insights-ci-timeout/ci-vs-local.json)里
104 个脚本只有 8 个在 CI 上更慢，其余比率多在 0.5–0.8，而更慢的那几个都是靠固定等待的浏览器脚本，也只到 1 附近
——`tests_month_inventory` 1.12、`tests_browser_workflow` 1.01、`tests_publish_v2` 0.99。
这个文件的耗时几乎全是等待：23 个测试各起一次隔离 Chromium，四个带 subTest 的负向用例逐项等身份核验超时，
仅这四个就占整文件 56%（56.88 / 52.26 / 48.82 / 41.95 秒，见
[逐测试计时](../state/story-insights-ci-timeout/baseline-durations.log)）。这些等待压不下去，压下去就是削弱判据。

**按场景拆成四个脚本，判据一条没动。** 隔离浏览器、月历夹具和 `month.read` 收进 `tests/month_detail_fixtures.py`，
四个脚本各留自己的响应与 DOM 夹具：`tests_story_insights`（跨发 Story，10 项）、`tests_published_media`（6 项）、
`tests_story_facebook_only`（4 项）、`tests_aggregate_post`（3 项），合计仍是原来的 23 个测试；
[逐条对照](../state/story-insights-ci-timeout/split-equivalence.json)记录方法体与夹具常量的比对结果，
只另删掉基线里零引用的 `COLLABORATORS`。浏览器逐测试隔离、负向五秒等待、默认 300 秒上限均不变，
新场景以后各自开脚本，不再往同一个文件里叠。

**验证：离线通过。** [全量 107/107](../state/offline-validation-20260922T034246Z/results.json)退出码均为 0，
最长的 `tests_story_insights` 153.39 秒（预算的 51%），另外三个 71.80 / 68.67 / 65.70 秒；
四个合计 359.56 秒，与同机同条件下未拆分文件的 356.23 秒相当，拆分本身没有可观成本。
前端 32 个文件 589 项单测通过，生产构建通过并保留既有大 chunk 提示。
⚠️ 本机计时会被并行会话拖慢，[另一轮全量](../state/offline-validation-20260922T024841Z/results.json)与别的会话并行，
脚本普遍慢到 2–2.4 倍：四个脚本 275.80 / 138.34 / 129.83 / 121.12 秒仍全部通过且留在预算内，
未拆分的文件在同样负载下必然越线。同一轮里 `tests_browser_workflow` 因界面 5 秒预算红了 7 项，
机器空闲后同一脚本 13 项全过（[单跑](../state/story-insights-ci-timeout/browser-workflow-quiet.log) 101.05 秒、
上面那次全量里 95.61 秒）；它与本轮改动无关——production 侧只改了地址更新器，没有任何模块导入它。
并回 `b7e235c`（带监测分页修复与其浏览器用例）后重建前端并
[定向复跑](../state/offline-validation-20260922T055248Z/results.json)：四个脚本加 hygiene、服务地址、
`tests_browser_workflow` 共 7/7 通过，前端 589 项单测通过。本节及 §1.26 引用的证据已按 SHA-256 保全到主检出同名目录，
清单见 [证据保全](../state/story-insights-ci-timeout/preservation.json)；工作树可清理。
同一条发布闸上的 `tests_service_address` 间歇失败另见
[§1.26](#126-服务地址引用与飞书历史链接核查2026-09-21)，不修它照样产不出制品。

**2026-09-22 续二：离线全量放行之后，浏览器步骤露出两条陈旧判据。** 上面两处修好后，
[run 35693145553](../state/ci-evidence-35693145553/) 的第 13 步在 CI 上 107/107 通过——
`tests_story_insights` 168.59 秒（预算的 56%，也是整个 suite 最慢的一个）、`tests_service_address` 2.56 秒——
但第 14 步 `browser_regression --stage D1` 失败，打包与上传照旧跳过。
⚠️ 这一步至少 25 轮没被执行过：之前每轮都死在第 13 步，两条陈旧判据因此一直没人看见。
两条都与本轮改动无关，`b7e235c` 上同样复现（本分支没碰 `web/`、`browser_regression.py`、`browser_lan.py`、`core/maintenance.py`）。

- **D1 挑错了队列。** 它在 `/review` 点一条来自 `/api/tasks`（不分平台）的首个待审行；列表改按原帖时间降序后
  首行是 IG 行，而 `/review` 是 Facebook 队列，那一行永远不会出现。D2 早就带着 `platform=='facebook'`，D1 补上同一条件。
- **`browser_lan` 六轮红三轮，堵在同一处。** 测试用 `clear_session` 模拟操作员清理失联的脏草稿，而
  `clear_session` 把会话连同 `sequence` 水位一起丢弃；关闭途中已经发出的那个状态包随后把会话重新注册成 dirty，
  维护再也静不下来（每次失败的唯一阻塞项都是刚被清掉的那条，原因 `unsaved`）。操作员真遇到会再清一次，
  测试的等待里同样重试，其余客户端的干净与确认判据仍由 `try_quiesce` 把关。
  ⚠️ 维护闸本身没有改：「落后的状态包能把已解决的阻塞项拉回来」要不要在 `core/maintenance.py` 里挡住，
  留给维护闸的负责人判断。

修后本机按 CI 的顺序跑完整个第 14 步：`browser_regression --stage ALL` 13 组通过（444.03 秒）、
`cutover_rehearsal` 8.26 秒、`browser_deployment` 6 项、`browser_lan` 22.47 秒；
`browser_lan` 另单独连跑六轮全过（修前六轮红三轮）。日志见 `state/story-insights-ci-timeout/step14-*.log`。
最终并回 `769d8f0`（视频来源按媒体标识判断）后重建前端再走一遍：
[全量 107/107](../state/offline-validation-20260922T071604Z/results.json)，
第 14 步四条命令 396.84 / 8.33 / 15.69 / 23.50 秒全部通过（`final-step14-*.log`）。
不涉及真实账号、模型、发布或业务账本，REQUIREMENTS §10 的判据与状态不变。

### 1.26 服务地址引用与飞书历史链接核查（2026-09-21）

用户确认旧地址出现在**改址前已发送的历史卡片**。此时两份当前配置均为 `http://10.66.6.3:8765`；新卡片生成链没有遗漏第三份源码入口配置。发现并修复了显式源码网络环境变量的读取差异：Web 原本读取该 JSON，飞书却回退 TOML；现在二者共用显式策略，受管 `host.json` 仍优先。README 的过期办公入口与 heartbeat 注释中的旧 IP 已移除，使用配置引用，避免下次改址再次过期。

[工作区只读扫描清单](../state/service-address-audit/scan.json)记录命中文件、分类和各工作树配置副本，不记录凭据值。472 个命中文件中有 430 个位于历史测试证据/旧运行副本；当前主检出没有真实 `state/feishu_outbox.json` 或 `control/host.json`。扫描及修复未改业务数据、已部署控制目录或其他功能工作树。以下清单按实际消费者决定改址范围，不全仓替换所有 IP：

| 地址消费者 | 来源与同步方式 |
|---|---|
| 源码局域网监听、来源检查、运行页“审校台入口” | `ops/service-machine.network.json` → `tools/source_web.py` / `core/web_access.py` / `web/api/deployment.py` |
| 飞书新卡片：审校、两平台待审列表、采集异常、已存原帖、运行详情 | `core/feishu.py` 统一生成按钮；优先受管策略，其次显式源码策略，否则读取 `[feishu].base_url`；独立调度器启动时保存设置，改址需正常重启 |
| 飞书部署通知、命令行自检、运行状态 | 同用 `FeishuSettings.load()`；部署通知由独立发件箱记录，不存在另一个写死 IP 的通知模板 |
| 页面导航、API、图片、下载 | 前端使用相对 URL，共用当前浏览器入口；不需要替换静态产物中的 IP |
| 普通开发启动、Vite 代理、CDP、健康检查 | 本机回环地址和对应端口有独立含义，保留；不替换为办公网 IP |
| 飞书已发送历史卡片、已冻结发件箱与回执 | 属于远端消息或历史投递事实，不是配置项；当前 webhook 没有远端 message_id，不改写或自动重发 |
| Windows 防火墙、已安装受管实例 | 服务机本地状态；源码防火墙命令读取网络 JSON，受管实例按 §17 独立维护，不随源码 Git 拉取更新 |
| 模型 API、飞书 webhook、心跳、社媒原帖、德国落地页 | 外部服务或业务链接，独立于本项目访问地址，不纳入改址 |
| 其他工作树、测试夹具、旧制品和历史文档示例 | 版本/验证副本，不参与当前源码进程；保留历史数值与证据 |

**验证状态：离线通过。** [修前](../state/offline-validation-20260921T080707Z/results.json)复现显式源码 JSON 未控制飞书入口；[修后](../state/offline-validation-20260921T080811Z/results.json)更新器 12 项与 Web 策略 15 项通过；[相关回归](../state/offline-validation-20260921T081120Z/results.json)飞书、通知路由、webhook、更新器、Web 策略及仓库卫生检查共 6/6 脚本通过。新增集成覆盖实际改址后 11 组按钮生成场景及端口同步，原帖链接保持；隔离发件箱中新事件使用新入口，已发送及结果未知的投递不改写、不重发。没有给真实群发消息，服务机与客户端可达性仍为 **待真实联调**。证据在本功能工作树与主检出各保留一份，校验见 [保全清单](../state/service-address-audit/preservation.json)。

**2026-09-22 续：改址后同进程仍按旧地址生成链接，`tests_service_address` 在 CI 上因此间歇性红。**
main 上 `67daab4`、`5a3152c`、`1d75df6` 三次运行的取证里它都失败（2.47 / 2.27 / 2.53 秒），
中间 `d7b7cd9` 那次却通过，本机怎么跑都绿。
⚠️ 根因是一次"看不出来"的改写：新旧地址等长，`config.toml` 改写后大小不变（185→185），
而 Windows 的最后写入时间取自约 15.6 毫秒一跳的时钟，夹具写入与改写可能落在同一跳。
`cfg()` 正是按 `(mtime_ns, size)` 这一对判断要不要重读，于是本进程继续拿旧的 `[feishu].base_url` 生成全部按钮。
CI 日志里只看到三条断言，是因为 `tools/test_offline` 失败时只回显日志末 3500 字符，前面九条被截掉——
本机复现时 12 条断言全红，与"只有三条不对"看着像两回事。
修复照 `core/operating_settings.py` 的既有做法：`update_address` 写完 `config.toml` 后调用
`core.config.invalidate_cfg_cache()`，回滚路径同样失效缓存。两个通过 `cfg()` 读配置的用例改用
`update_inside_one_timestamp_tick` 显式还原改写前的 mtime，判据不再依赖时钟运气。
[修前](../state/story-insights-ci-timeout/service-address-red.log)12 条失败、
[修后](../state/story-insights-ci-timeout/service-address-green.log)12 项通过。
`scripts/update_service_address.bat` 是独立进程、改完还要重启服务，真实链路没有发出过旧链接；这条修的是同进程写后读。

### 1.27 原图不可用按原因分类、过期地址恢复与陈旧人工项退役（2026-09-21）

分支 `claude/social-media-image-verification-fix-938aa1`，工作树同名，基点 `ad8c5de`。
用户反馈"绝大部分帖子都进了待人工核验"。服务机 `--status`（2026-09-21T04:46Z）显示这其实是
**两个互不相干的问题**，而那句刺眼的原因只对应其中一篇。

**其一：归档完整性的判据太粗。** 每轮监测都把帖子留在"保留待核验"，原因栏永远是同一句
"原图缺失、损坏或与归档校验值不一致"。那句话由一个判断产生（`core/archive_integrity.py`
的原图闸），而它上游的 `media_storage_info()` 把**九种**互不相干的情况压成 `missing`/`corrupt`
两个值：缺路径、缺文件、不可解码、哈希不符、字节数不符、读取中变动、被占用、I/O 错误、路径越界。
运营因此无法区分该重新取源、该核对档案，还是本来就什么都不用做。

**其二（量在这里）：人工队列里 37 条有 33 条是死账。** 它们的原因串
"本轮会话中断或预算耗尽，未处理候选已转人工"在当前 `main` 里**根本不存在**——`23c719d`
（2026-09-19）删掉了它，同时引入"未开始记 `deferred`、已开始失败才是 `manual`"的区分和
基线外历史帖不进候选的护栏。那 33 条是修复之前那次深滚扫描留下的：31 条是 2026-06-06 至
2026-08-18 的帖，全部早于基线截止日 2026-08-19。⛔ **`begin()` 永远跳过 `manual`**
（`core/capture_state.py`），所以代码修好之后它们既不会被重试、也不会自己消失。

四处修改。**一，分类。** `storage_status` 保持四个值不变（SQLite `post_media` 列、展示索引签名
和审校台详情都按它取），细分走并列的 `storage_detail`，核验报告逐张给出原因与
`refetch`/`adjudicate`/`transient`/`local` 四类处置。**二，瞬时不再算损坏。** 读取侧对
WinError 32/33 和读取期间的 mtime/ctime 变动限时重试，与 `localize/images.py` 的写入侧同源；
这类原先会被判成"原图损坏"并连同整篇推进人工队列，而下一轮读它又是好的。**三，过期地址。**
归档存的是 Meta 签名地址，过期后重下只会拿到 403，所以卡住的帖子在系统内无路可走；
现在人显式恢复且地址按 `oe` 判定确已过期时，允许开一次详情换新地址。两处硬约束见 [§1.2](#12-已交付修复留下的硬约束)。
**四，退役死账。** 新增 `--retire-stale-manual`（带 `--expected-revision` 与 `--reason`），
只把「没有 `attempt_started_at` 且不是人工恢复授权」的人工项退回 `deferred`，判据与现在的
`interrupt()` 逐字一致。⛔ **真正尝试过并失败的项一条不动**，事件账本不改写。
该字段在 `68d3b08` 就已存在，所以旧记录同样可判，不会误伤那 4 条真实失败。
退役不产生任何平台请求：基线外的帖在候选阶段被 `outside_baseline` 拦下，
已归档且无变化的帖 `should_append()` 为假。这一步撤销了 [§1.20](#120-夜间监测首屏与采集恢复修复2026-09-20) 的"旧人工项不批量改写"。

⚠️ **自动轮次的边界没有放宽**，仍然"下载失败不触发详情"；`run_kind` 为 `recovery` 才进这条路，
且失效时刻未知（地址里没有 `oe`）时不开详情——宁可白下一次，也不为一个猜测去访问平台。

**验证状态：离线通过。** 合并 `67daab4` 后[全量 104/104 脚本通过](../state/image-verification-classification/offline-validation-20260921T103033Z/results.json)。
新增用例覆盖九种情况逐一判别、限时重试后仍为已核验、持续占用与
读取中变动不算坏字节、`oe` 按十六进制解析、保留项带逐张结论与动作、过期地址经详情换新后落盘完整，
以及按旧写法伪造的未开始项被退役而真实失败项保留、版本冲突与空 reason 拒绝、事件账本条数不变。
`--retire-stale-manual` 另有一次隔离归档的端到端命令验证：缺 `--expected-revision` 退出码 2，
正常执行退役 2 条、保留 1 条并打印逐条依据。
证据已按 SHA-256 保全到主检出 `state/image-verification-classification/`，清单在同目录 `preservation.json`。

离线部分使用工作树默认独立路径，只复用主检出 Python，没有复制凭据。

**服务机实测（2026-09-22，代码 `d7b7cd9`）：分类与退役真实通过，换址仍未被走到。**
逐条输出记于[服务机记录](../state/image-verification-classification/service-machine-20260922.json)。
退役按判据精确命中：37 条人工项退役 33 条（`historical` 30、`source_updated` 3）、保留 4 条真实失败，
无一误伤。那篇 `3970856510175738446` 经 `--recover-post` 回到 `complete`。

⚠️ **但换址那条路没有被走到，而且原因推翻了本节原先的判断。** 那个地址的 `oe` 是
**2026-09-24T06:51:32Z**，执行时尚未过期，`source_url_expired` 为 `false`——
`_images_need_fresh_urls()` 因此返回假，一次详情都没开，直接用归档里的旧地址重下就成功了。
所以卡住它的从来不是地址过期，而是**没人知道该对它执行 `--recover-post`**：它一直是 `manual`、
入口一直可用，只是那句"原图缺失、损坏或与归档校验值不一致"没说该做什么。真正解开它的是分类，不是换址。
⛔ 因此 `signed_url_expiry` 判定过期后开详情、`refresh_signed_media_urls` 采纳真实详情里的新地址、
以及过期地址是否真的返回 403，三项仍是 **待真实联调**；要等一篇 `oe` 确已过期的缺图帖出现才能验。

操作顺序：先 `--status` 读 revision 与逐帖 `image_problems`，再 `--retire-stale-manual` 清死账，
最后对剩下的真实失败逐帖 `--recover-post`。步骤见
[MANUAL_STEPS §4.2](MANUAL_STEPS.md#42-历史-ig-完整性自动核验)与 [§14 E](MANUAL_STEPS.md#e-cas-恢复与证据)。

### 1.28 视频地址轮换被判成来源变化（2026-09-22）

退役之后的第一轮自然扫描（服务机 2026-09-22T05:58Z）把另一个问题顶了出来。
那一轮的 IG 摘要是「新增 1 篇 · **处理已有帖 3 篇**」，而 3 篇全是 0 图/1 视频的帖子，
8 篇图片帖一篇没动。同一批数字里 `archive_incomplete` 为 0、`manual_items` 只剩 3 条 FB 项，
退役的 33 条没有引发任何重采——**"处理已有帖"与退役无关，是一条独立的老毛病**。

判据在 `Archive._source_changed()`：图片有 `sha256` 就按字节比，比不上才退到地址；
视频从不下载、永远没有 `sha256`，于是每次都退到地址。地址那一步原本调
`same_media_locator()`，而它比的是 scheme + **主机** + **路径** + 非签名参数——
主机和路径里的对象句柄恰恰每次响应都换。结果就是同一条视频每轮都被判成"来源变了"。

单篇视频帖不下载任何字节，所以**没有浪费平台请求**；代价是每轮给同一篇写一次
`post.json`、往事件账本追加一条 `post_captured`、推高 `capture_revision`，
并且让"处理已有帖"这个本该用来判健康的数字永远归不了零。混合帖（图 + 视频）更实际：
`reconcile_local()` 用同一个判据核对媒体，视频地址一换就拒绝用本地证据收尾，
把一篇本可零成本关闭的帖推去再采一次——退役回 `deferred` 的那批里就有这种。

修法是给视频一条按身份的判据 `same_source_media()`：图片仍走 `same_media_locator()`
（`stp=` 这类派生参数照旧不能忽略），视频只认 `source_media_id`——
`content_revision()` 从一开始就是这么认的，这次只是让 `_source_changed()` 和
`reconcile_local()` 跟它对齐。两边都没有身份时不据此重采：地址本来就证明不了什么。
⛔ **不要改 `same_media_locator()` 本身**——`reusable_media()` 也在用它，
放宽主机比较会让另一个尺寸派生被当成同一张图复用旧字节。

**为什么离线套件一直是绿的：** 现有夹具里的视频地址是 `https://cdn/video.mp4` 这类常量，
两轮之间根本不变，判据再错也不会响。新用例按真实响应的形状写——主机、对象句柄、
`vs`/`oh`/`oe` 全换一遍，只有 `source_media_id` 不变。

**验证状态：真实通过。**
[全量 104/104 脚本通过](../state/image-verification-classification/offline-validation-20260922T062154Z/results.json)。
三条新用例都先在修复前复现失败：`should_append()` 对轮换地址返回假而指向另一条视频仍返回真、
整轮扫描的"处理已有帖"回到 0、deferred 的混合帖靠本地证据收尾且不发起新请求；
同时补一条图片用例，确保放宽视频没有顺手放宽图片。

服务机同日前后两轮是干净的对照：05:58Z 与 11:22Z 都看到 12 篇（原创 10 · 合作 2，
2026-09-08 ~ 2026-09-21）、都是 3 篇视频在发布范围外，而"处理已有帖"从 **3 篇变成 0 篇**。
输入没变、判据变了，正是这条修复该有的差别。

⚠️ `tests_story_insights` 在空闲机器上要 358.19 秒，超过 `tools/test_offline.py` 的 300 秒默认，
该次本地验证带 `--timeout 600`，当时 CI 使用默认超时；相关历史修复见上文。当前 Actions 停用决定见 §1。

### 1.29 归属哨兵把自家账号当成合作方（2026-09-22）

2026-09-22 那轮扫描报了
`instagram 丢弃的节点里有 1 篇来自**已知合作方**（neakasa.tech）—— 合作帖归属判定可能又漏判了`。
按哨兵的要求离线查过 `_rejected.jsonl`，**结论是没有漏判**：

| | post_id | 正文开头 |
|---|---|---|
| FB `neakasaofficial` | `122128375707379375` | `Only 6 days to go! ⌛  Hello, hello! We` |
| IG `neakasa.global` | `3991125889886213694` | 同一句 |
| IG `neakasa.tech` | `3991518443455200172` | 同一句，**被丢弃** |

三个不同的 post_id、三条不同的 permalink、发布时刻相差半天。合作帖在 IG 的响应里是**一个**
media 对象带 `coauthor_producers`，永远只有一个 pk；这里是三条各自独立的帖，
即同一批文案跨账号各发一次，`owner_mismatch` 判对了。同轮另外 8 条丢弃全是同品类第三方
（`pets_qtr`、`hoopo_design`、`uahpet_official`、`moonlitterbox`、`catloving.club`、
`hholove_global`、`leo_in_ottawa`），一条都没进哨兵——精度本身是好的。

⛔ **但口径错了。** `neakasa.tech` 不是第三方，是本品牌自己的 IG 账号
（`config.toml [publish.trusted_owners].instagram` 列着它，[REQUIREMENTS](REQUIREMENTS.md) 把
`in_neakasa.tech` 记为冻结只读）。而 `known_partners()` 纯粹从归档的 owner/coauthor 归纳，
分不出自家账号和第三方——归档样本里 `.global` 的合作方几乎全是 `.tech`，所以只要 `.tech`
再跨账号发一次，这条告警就再响一次。它是归属红线的哨兵，被训练成噪音之后就没人看了。

`split_suspect_sources()` 按 `[publish.trusted_owners]` 把疑似节点分成「已授权来源」和
「已知合作方」两类，两类各说各的处置；判定逻辑和丢弃行为一个字没改，只改谁该被怎么读。
监测告警、覆盖不足的中止原因、`backfill`、`replay`、`dryrun_delta` 五处口径一致。

⚠️ **顺带一个业务事实：`.tech` 文档里是冻结的，但它 2026-09-22T03:01Z 还在发帖。**
这不是代码问题，但监测口径和"冻结"的实际含义对不上，上线前值得跟业务确认一次。

**验证状态：真实通过。** `tests_integrity` 新增八条断言覆盖两类划分、按平台各读各的名单、
归属未知不算已授权、名单写坏时当空名单、作者名去重排序与超限写"等"；改动涉及的 10 个套件全部通过。
服务机 2026-09-22 11:22Z 实跑打出的是
`丢弃的节点里有 1 篇来自**已授权来源**（neakasa.tech）——多半是本品牌另一个账号把同一批文案各发了一次`，
口径已经生效。

### 1.30 飞书四机器人统一卡片（2026-09-22）

功能工作树 `.worktrees/feishu-card-redesign`，分支 `codex/feishu-card-redesign`。卡片入口仍为 `core/feishu.py`；纯渲染集中在 `core/feishu_cards.py`，采集/审校/排期/部署调用链提供原始业务时间和结构化状态。配置 `[feishu].site_name/timezone` 默认 `Neakasa 德国` / `Asia/Shanghai`；卡片不带时区字样。双列属性、150 字引用摘要、底部导航、批次计数与逐帖状态遵循 [FUNCTIONALITY 的 F4-4](FUNCTIONALITY.md)。

已冻结卡片和未知投递保持原样；旧未分配检测事件缺少分类时使用“监测到帖子变化”，不从旧自由文本猜“新发布”。风险未扫、失效、失败或素材检查有问题时不显示绿色通过。晨报计数与核账恢复建议独立呈现，避免正文摘要限长吃掉关键提示；部署通知保留具体原因。Windows 本地告警和真实群投递边界不变。

**验证状态：离线通过。** [验证清单](../state/feishu-card-redesign/validation-summary.json)记录最终命令及各脚本日志；[用户指定测试](../state/feishu-card-redesign/pytest-feishu.log)为 44 项、30 个子场景通过。相关通知路由、审校、流水线、采集恢复、发布操作、部署宿主、服务地址、Web 入口、本地通知和 hygiene 共 10 个脚本通过。只读代码评审发现的旧分类误报、部署原因丢失及晨报截断三项已用[回归用例](../state/feishu-card-redesign/review-fixes.log)复现并修复。

合入远端监测归属修复后的[通知定向复验](../state/feishu-card-redesign/merge-notifications.log)为 104 项、32 个子场景通过；[归档完整性、采集恢复及 hygiene](../state/offline-validation-20260922T113554Z/results.json)三个脚本通过。两组均使用隔离数据，证据按 SHA-256 保全至主检出，见[合并验证保全清单](../state/feishu-card-redesign/merge-preservation.json)。

[四类卡片 JSON](../state/feishu-card-redesign/cards-preview.json)由 `notifications --self-test --dry-run` 生成；只验证 payload，不证明飞书客户端实际排版。测试使用临时数据、模拟 HTTP 及固定示例，没有真实群消息、业务 Chrome、付费模型或发布操作。新版服务机测试群/手机/桌面排版为 **待真实联调**，步骤见 [MANUAL_STEPS §2.1](MANUAL_STEPS.md#21-配置同群四个机器人)。

证据已按 SHA-256 校验复制到主检出同名 `state/` 路径，[保全清单](../state/feishu-card-redesign/preservation.json)记录来源与摘要；本功能工作树保留独立测试环境和原证据。未接入真实业务数据或复制凭据。证据不随 Git 提交；清理工作树前仍按 §1.3 核对。

## 2. 红线

1. 不自动登录。人在三个专用 Chrome profile 登录，代码只附着。
2. 不自动滚历史。回填由人滚；增量和兜底对账始终受 C7 深度、频率、会话和失败预算约束。
3. 抓取、探测、发布三种身份分离：9222 / 9224 / 9223。不要恢复旧“两浏览器”设计。
4. Facebook 与 Instagram 是独立车道，不配对、不合并、不沿用 composer 默认双选。
5. Business Suite 定位和成功判据保留来源。G1 允许按用户确认复用 FB/IG 共通结构；结构复用不能充当另一渠道实际提交成功。截图见到文字不等于存在可定位元素。
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

逐项状态在 [REQUIREMENTS §10](REQUIREMENTS.md#10-五阶段验收状态)。这里只列真正还缺的东西，不复述已经做完的部分。

**发布侧是最长的一段，而且两头互相咬住。** `publish/` 的远端 scheduled 详情图片读取适配器**尚未实现**，G8 媒体闸因此不会通过——它要求全文相等、`remote_images_verified`、正整数媒体数以及有序 source SHA 与冻结清单一致，只有 `scheduled`、remote ID 或编辑器缩略图都不够。G1 已绑定现有录制并通过（§1.21），不再要求从零录制或补测 UI 边界。⛔ **必须先真实排出一张卡片，才能录到那张详情页的图片控件证据，再补适配器**；顺序见 [MANUAL_STEPS §16](MANUAL_STEPS.md#16-阶段三真实验收冻结排期与自动发布)。

**没有一次成功的真实付费产出。** 真实翻译和真实出图各失败过一次（§1.1），修复后尚未复验。连带两条阈值也标不了：`[image].dhash_max_distance` 与 `change_ratio_warn` 都是 `-1`，必须用首批真实产出分组标定，见 [MANUAL_STEPS §5.2](MANUAL_STEPS.md#52-标定图内文字到底改没改的告警线)。

**联调素材只有半份。** 服务机 IG 有 21 篇归档，FB 那批图文帖还缺原图；开发机归档为空，出不了素材。两篇具体内容的准备见 [MANUAL_STEPS §9](MANUAL_STEPS.md#9-真实发布前的最终确认)。

**没有异地备份。** `[heartbeat].enabled` 已开，还缺服务机 `.env` 的 `HEARTBEAT_URL`；没配时只记 `missing_url`，外部观察者收不到心跳。云盘镜像仍延期，备份只能靠人外拷（§1）。

**一个业务前置没动：** IG 的 bio 聚合页还没建，`[publish].ig_bio_url` 是空的，链接区显示「未配置」。见 [MANUAL_STEPS §6.1](MANUAL_STEPS.md#61-上线前的一个业务前置ig-的-bio-聚合页)。

**一个未复现的线上错误：** `/api/refinements/task/...` 在服务机返回 500，本机取不到 Traceback（§1.1）。

⛔ **下面这些是决定，不是缺口，不要去补实现：** 云盘镜像、法语、德语标签热度推荐、登录/RBAC/真实 actor、视频加工、FB/IG 内容复用、`supervised` 与无人审核发布、发布任务队列、飞书卡片缩略图与远端去重。判据见 [REQUIREMENTS §9](REQUIREMENTS.md#9-明确延期与固定边界)。

**一项留待跟进的性能观察：** 清库前在 1,067 篇归档上，冻结账号详情页首次打开 8.6 秒、刷新 4.1 秒，其余页面 0.4–3.2 秒。数据重建到相当规模后要重新量，别把空库上的"很快"当成结论。历史页首屏缩略图成本另见[第 7 节](#7-审校台缩略图实测)。

⚠️ **`legal_size()` 的 16 像素量化会把正好压线的原图推出 IG 画幅带。** 候选排序原先第一键是宽度取整
误差，画幅偏差只排第二，而它完全不知道发布侧那条 4:5 ~ 1.91:1 的窗口。实测：`1440x1800`（正好 4:5）
量化成 `1440x1808`，比值 0.7965 掉出 4:5；`640x800` 量化成 `720x912`，比值 0.7895 同样掉出，还附带
1.13 倍放大。两者的形变分别只有 0.442% 和 1.316%，都在 `aspect_drift_warn_percent = 2.0`（818 张归档
标定）以下，**告警不会响**——原帖发得出去，德语版发不出去，该风险由图片输出质量和实际发布界面核验，不要求为关闭 G1 重新测量画幅。

修法是排序加一个前置键"源图在带内而候选出带 → 排到最后"，带由 `[image].preferred_aspect_band` 给出。
修后两例分别落到 `1440x1792`（0.8036）与 `736x912`（0.8070），形变 0.446% / 0.877%，仍远低于告警线。
⛔ **它只重排候选，从不拒绝**，也不修正原图本身就在带外的比值（`1536x2048`、`1200x628` 结果不变）——
那是擅自改画面。这条带来自 2026-09-01 历史观察，只作为生成偏好；G1 不要求人工测量或配置发布画幅边界。

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

这一节保留历史界面观察，原 dump 已随 2026-09-14 清库删除，不能声明旧证据仍可复核。
当前 G1 已采用 §1.21 的 2026-09-20 录制与用户接受的共通结构，操作以
[MANUAL_STEPS §8](MANUAL_STEPS.md#8-录制单渠道-business-suite-证据)为准；不因本节旧文件缺失要求重复录制。

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
| IG 正文 | 当时 UI 提示最多 2200 characters；不把该历史提示作为当前发布硬限制或恢复 bytes 限制 |
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

### 当前发布时区

2026-09-20 用户确认发布服务器为北京时间；运营时区和设备时区分别配置，当前都为
`Asia/Shanghai`。月历覆盖与日期回读按该时区解释，不沿用旧美西设备的跨月算例。
柏林当地时刻仍并排展示，德国夏令时由 `ZoneInfo` 换算；受众深夜提示不阻断发布。

### 每份证据能证明到哪

**最容易犯的错是把左边一列读成右边一列。** 下表统一保存当前仍需遵守的证据边界。

| 证据 | 能证明什么 | **不能**证明什么 |
|---|---|---|
| 单渠道控件录证（已删，需重录） | 当前 FB `Neakasa Deutschland`、IG `neakasa.de` 的单渠道控件与账号观察 | 新 Schedule 已提交或已回读 |
| 被动 v2 探查（2026-09-20，G1 已接受） | 当前五项共通结构信号及因果链；IG 共享结构依据另记 | IG 历史排期成功或 G8 真实排期验收 |
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
