# 人工操作指南

**2026-09-20 试运营上线。服务机已装好，这一轮的动作是：先只读数抓取积压（[§2.1](#21-配置同群四个机器人)）→ 拉取含 `[feishu].enabled = true` 的 `main` 并重启 → 复验 [HANDOFF §1.1](HANDOFF.md#11-服务机现状与上线前的未完项) 那张表 → 人工重新回填 FB 原图（[第 4 节](#4-为当前目标建立归档)）→ 四机器人在服务机自检（[§2.1](#21-配置同群四个机器人)）→ 写入外部看门狗 `HEARTBEAT_URL`（[§2.2](#22-外部心跳)，不是审校台网址）→ 72 小时试运行（[第 14 节](#14-阶段一真实验收监测与原帖抓取)，不带 `--process`）。** ⛔ **源码局域网按 [§13.1](#131-源码服务机一键改址) 改址与启动；受管实例的 `control/host.json` 不随拉取更新。** 打开内容处理另有四条硬前置（[§14.1](#141-打开内容处理抓到就翻译和出图)），真实排期还要先录发布证据（[第 8 节](#8-录制单渠道-business-suite-证据)、[第 16 节](#16-阶段三真实验收冻结排期与自动发布)）。日历、云盘镜像、标签热度本轮仍关着。

1–13 节是分主题的长期参考，第 14–17 节是排成顺序的现场流程。业务规则看 [FUNCTIONALITY.md](FUNCTIONALITY.md)，每个验收单元的状态看 [REQUIREMENTS §10](REQUIREMENTS.md#10-五阶段验收状态)，证据边界看 [HANDOFF.md](HANDOFF.md)。

⚠️ **两台机器不要混。** 服务机的 `shared/archive`、`shared/state` 是实际业务数据，审校写入会落到真实账本；开发机归档为空，只做代码与离线回归。在开发机上跑出来的结果不是服务机的结论。

## 1. 接续运行数据前先备份和核验

主干直接按 `config.toml` 的 `[paths]` 读同目录的 archive/state；副本的绑定以 `config.local.toml` 和运行状态为准，⚠️ **接手任何副本前先看那个文件指向哪里**，不能假定副本一定是隔离的。

⛔ 2026-09-14 曾按业务决定把开发机的 archive/state 整库清空，且**没有备份**。那是一次性决定；**以后再接续或恢复，仍然必须先备份再动**，下面这套核验步骤照做。服务机的业务数据现在是真的，更要照做。

以后再次接续或恢复时，先停止旧调度器与写入进程，备份完整 archive/state，另包含运行 `config.toml`、存在时的 `config.local.toml`、本机 `.env` 的受控副本和解释器位置/版本记录。备份凭据单独控制访问，不放公共云盘或证据报告；不要只复制 manifest/SQLite。

至少核验：

1. 原目录与备份的文件数/总大小、逐文件哈希一致，压缩包可读；配置与数据属于同一运行边界；
2. `state/published.jsonl`、`state/paid_requests.jsonl` 和激活状态存在且非空时完整复制；
3. 每个账号的 `review_items.jsonl`、`translated_human.jsonl`、`localization.jsonl` 都包含在内；
4. 旧 IG 目录 `in_neakasa.tech` 保持只读，不把它改名冒充 `.global`；
5. 新 `.global` 使用新的账号目录和增量游标；
6. 遇到坏账本先保留现场；严格读取应报具体文件/行，在副本检查，不删行或置空“修好”；
7. 在副本实际比较 SQLite 与源文件的 ID 集合、数量和展示字段，识别缺失/多余/内容不符；重建派生后人工/审校/付费/发布账本哈希不变；
8. 原图按每张实际字节哈希、数量、顺序核对，文件名/大小相同不能代表媒体未变；
9. Web、CLI、批处理和调度读取同一 archive/state/环境/解释器及共享锁，再恢复服务。

原工作区的 FB 47 / `.tech` 1020 是历史基线，不是 `.global` 的验收样本。本轮原 1,067 条与 SQLite 实际记录/字段已核对一致，第 1/2 页各 20 条无重复，冻结详情已真实只读访问；后续恢复仍须重新核对恢复出的副本。

一次 ZIP 不替代周期备份。周期保存不可重建账本及主配置，记录时间/文件数/哈希，并在副本恢复验证；大型 probe、截图、冻结媒体按独立 hash 版本留存，在账本中保存引用。该组合已有离线验证。本机密钥/运行覆盖配置另作受控备份，不因云盘镜像启用而公开。云盘只单向接收，本地恢复不能自动用远端覆盖本地事实。

⛔ **云盘本轮延期（[REQUIREMENTS §9](REQUIREMENTS.md#9-明确延期与固定边界)），所以这一节是本轮唯一的备份手段，不是可选项。** `state/published.jsonl` 不可重建——丢了会让已发过的帖子被再发一遍，而 2026-09-14 那次清库就是没有备份的。上线后按固定节奏把整个 `archive/` 与 `state/` 拷到本机之外的另一块盘，每次记下时间、文件数、字节数和哈希。**没有任何代码会替你做这件事。**

## 2. 配置本机凭据

已有本机绑定时编辑绑定指向的环境文件，保留已存在值；首次独立安装才从 `.env.example` 建本机 `.env`。模型/飞书凭据和访问令牌不写入可提交配置、终端截图、probe dump 或日志。云盘 folder token 是目标资源标识，不能代替应用凭据和目录授权。

**飞书消息使用同一业务群的四个机器人**。四个地址必填，启用签名校验时再填各自密钥（第 2.1 节）：

| 变量 | 是什么 |
|---|---|
| `FEISHU_WEBHOOK_DETECT` | 新帖检测推送机器人：检测到新帖 |
| `FEISHU_WEBHOOK_CAPTURE` | 新帖爬取推送机器人：抓取完成及逐篇落档结果 |
| `FEISHU_WEBHOOK_PUBLISH` | 新帖发布推送机器人：待审、排期成功 |
| `FEISHU_WEBHOOK_ALERT` | 状态告警推送机器人：积压、排期失败、晨间摘要、系统异常 |
| 上述变量各加 `_SECRET` | 对应机器人的签名密钥。不填就必须在该机器人配置关键词 |
| `HEARTBEAT_URL` | 外部缺席告警接收地址，必须 HTTPS；不写进 `config.toml` |

⛔ **地址本身带 token，等于密钥。** 不要贴进 config、截图、日志或版本库。


**运营地址仍须现场验收**——受管服务机使用持久的 `public_base_url`，按第 17 节从业务电脑实际打开飞书链接；消息通道可达不能替代这个检查。企业管理员那条路（应用 AppSecret、应用授权、云盘根目录权限）随云盘一起延期，见 [REQUIREMENTS §9](REQUIREMENTS.md#9-明确延期与固定边界)。

### 2.1 配置同群四个机器人

用户已建好四个机器人。逐个核对现有机器人即可，不重复创建。`config.toml` 里 `[feishu].enabled` 已经是 `true`。

⛔ **服务机第一次加载这份配置之前，先只读数积压，不要重启。** 飞书关闭期间抓取事件不会被确认；重启后下一轮维护会按扫描把 `acknowledged=false` 的事件一次性入队。在服务机业务目录执行：

```powershell
scripts\run_python.bat -c "import json; from pathlib import Path; from core.config import cfg; p = cfg().state_dir / 'capture_state.json'; data = json.loads(p.read_text(encoding='utf-8')) if p.exists() else {}; events = [e for e in (data.get('events') or {}).values() if isinstance(e, dict) and e.get('acknowledged') is False]; scans = {e.get('scan_id') for e in events}; print('unacknowledged_events', len(events)); print('scans', len(scans))"
```

记下事件数和扫描数。文件不存在则积压为 0。这是只读，不要改 `capture_state.json`。数完再拉代码、停旧 Web；源码局域网用 `scripts\run_web_lan.bat` 重启（§13.1）。

1. 进入同一业务群 → 群设置 → 群机器人，分别打开四个自定义机器人；
2. 核对名称为「新帖检测推送机器人」「新帖爬取推送机器人」「新帖发布推送机器人」「状态告警推送机器人」，与上表对应；
3. 安全设置里勾**签名校验**，把密钥和 webhook 地址一起抄下来。两种校验的区别：
   - **签名校验**（推荐）：和卡片内容无关，改文案不会失效；
   - **关键词**：卡片里必须出现该词。要用就设成 `Neakasa`（当前卡片标题里有），但请记住这个约束只写在群设置里，**以后改标题会静默失效**；
4. 四个地址及各自签名密钥填进**服务机**实际运行绑定的 `.env`。`FEISHU_WEBHOOK_OPS/TECH` 已停用，不能把一个旧地址复制到多个新角色。然后在**服务机**人工自检——这一步会**由四个机器人各发一条消息**：

```powershell
scripts\run_python.bat -m pipeline notifications --self-test
```

同一个群应收到四张使用正式检测、抓取、待审、告警模板的示例卡，标题均带「通道自检」及角色名称。示例时间和帖子为固定夹具，没有业务含义；逐张核对消息的真实发送者与角色一致。同群正常；四个角色复用相同 webhook 地址会在发送前拒绝。运行页 `bots` 显示各自是否配置和格式是否有效，`duplicate_bot_targets` 显示重复地址；这些字段不含地址或密钥，也不证明实际可投递。

开发机只检查结构时使用离线预览，不需要 webhook 或签名密钥：

```powershell
scripts\run_python.bat -m pipeline notifications --self-test --dry-run
```

输出为四个 `{role, card}` 对象组成的 JSON 数组；不创建发件箱、不发送请求。卡片站点与显示时区取 `[feishu].site_name/timezone`，当前为 `Neakasa 德国` / `Asia/Shanghai`；只显示时间数字，不附时区文字。服务机测试群另核对双列属性、引用摘要、底部按钮、合并卡序号，以及手机/桌面的实际显示和链接可达性。dry-run 与模拟 HTTP 不能替代这项真实验收。

日常只读投递状态、以及核对不确定投递：

```powershell
scripts\run_python.bat -m pipeline notifications
```

⚠️ **群机器人不返回飞书消息 ID。** 所以登记"已送达"时填的是你自己写的核对说明（例如「业务群 21:07 已收到」），不是平台 ID；审校台运行页上的输入框同理。填不出 ID 不是你操作错了。

**未知结果不自动重发。** 超时、5xx、响应无法解析或进程中断会留在 `uncertain`。在群里核对机器人、扫描时刻和内容，再按 delivery ID 与当前 version 登记；已送达填核对说明，确认未送达才恢复。明确限流或拒绝的请求会退避 15 分钟。不要删除发件箱或更改角色来“重试”。

旧发件箱会在写入投递流程时升级为版本 2：已送达记录保留旧角色且不重发；从未尝试的记录转当前阶段；旧未知结果先核对。确认旧通道未送达后，有效内容才转当前机器人，原卡与旧 ID 保留；部分过期卡只补仍有效的内容。当前主工作区在本轮整合前没有 `feishu_outbox.json`，升级行为由隔离夹具验证。

⚠️ **开发机不要带着生产 `.env` 跑调度或 `--process`。** 飞书开关是共享的，四个 webhook 是真的，会往业务群发卡片。

### 2.2 外部心跳

`[heartbeat].enabled` 已经是 `true`。调度进程每隔 `interval_minutes` 对 `HEARTBEAT_URL` 发一次空 HTTPS POST；缺席由外部服务告警，本机飞书在关机时帮不上忙。

1. 在外部心跳服务建一个检查，过期阈值按 `stale_after_minutes`（默认 45）设；
2. 把 HTTPS 地址写入**服务机** `.env` 的 `HEARTBEAT_URL`。地址可能带 token，不要贴进 config、截图、日志或版本库；
3. 没配时进程不崩，状态记 `missing_url`，外部观察者收不到心跳。配错成 HTTP 或带用户名密码时记 `invalid_url`。

## 3. 登录三个专用 Chrome

三个角色必须使用不同 profile 和端口：

| 角色 | 启动入口 | 默认端口 | 登录身份 |
|---|---|---:|---|
| 回填 | `scripts\start_chrome.bat` | 9222 | 抓取专用小号 |
| 发布 | `scripts\start_chrome_publish.bat` | 9223 | 持有德国站资产权限的发布账号 |
| 探测 | `scripts\start_chrome_detect.bat` | 9224 | 与回填隔离的探测专用小号 |

在各自 Chrome 人工登录或完成二次验证，核对 profile 路径、端口和身份。进程已启动不表示会话有效；实际平台返回登录、checkpoint、challenge、401/403/429 时按共享访问控制立即停机并保留证据。

任何 checkpoint 或会话异常都停下来。不要连续重试，不要增加自动登录脚本，也不要把发布账号拿去抓取。

## 4. 为当前目标建立归档

Facebook 目标是 `neakasaofficial`，Instagram 目标是 `neakasa.global`。确认配置后，回填由人在 9222 浏览器里打开目标主页并手工滚动；程序只拦截响应和保存内容。

```powershell
scripts\run_backfill.bat facebook --days 30
scripts\run_backfill.bat instagram --days 30
```

`--days N` 只归档最近 N 天，并打印窗口外跳过数；算不出日期的仍保留。不带该参数是全量。
它收紧的是归档范围，不改人工滚动——仍要自己滚到看见窗口边界为止。

不要自动滚到底，不绕过 C7 会话/深度限制。完成后检查目标目录名、帖子数量、合作帖、拒绝记录和媒体完整性。`.global` 合作帖无论合作方是谁都应归档；`owner` 仍保存真实作者。

`.global` 覆盖、会话与当前安全闸未满足时不启动其自动处理/常驻任务。已授权且不依赖回填的代码/离线验收继续做；冻结 `.tech` 不因历史可查询而重译或发布。

### 4.1 IG 单图被误标不完整时的离线修复

适用于来源明确 `media_type=1`、`carousel_media=null` 或空数组，现有原图已保存，但旧代码把来源完整性记为 false 的帖子。它不是模型、IP 或端口配置问题。先拉取包含修复的代码，停止 Web 和调度进程，保留现有 `archive/`、`state/` 与 `config.local.toml`；不要重新回填或运行全账号 replay。

以下命令针对 `3984612646028833441`，只读配置绑定的 IG 账号归档与指定 capture，不连接平台或模型。`--capture` 只给文件名时从该账号归档根目录查找，也可传完整路径；不自动选择“最新”响应。

```powershell
scripts\run_python.bat -m tools.repair_ig_completeness --post-id 3984612646028833441 --capture _capture_1789697596.json
```

默认只预览，输出 `apply: false`、`changed`、预计完整性、已校验图片数及 capture/图片 SHA。确认 post ID、账号和 `verified_images: 1` 后执行：

```powershell
scripts\run_python.bat -m tools.repair_ig_completeness --post-id 3984612646028833441 --capture _capture_1789697596.json --apply
```

命令逐份核对同帖响应的类型、帖子链接、作者/合作关系、媒体 URL/ID 和本地原图解码/大小/SHA。来源不一致、真实轮播缺项或原图损坏会拒绝。成功时先在帖子目录保存 `post.json.before-completeness-<唯一标识>.bak` 原字节和配套 `.evidence.json`，仅把 `post.json` 的 `source_media_complete`、`source_media_count`、`media_complete` 改为 `true`、`1`、`true`，追加该帖 manifest 并重建两个 SQLite 展示索引。原文、分类、目录、原图及人工/审校/付费/发布记录保留。

若中断或提示索引重建失败，保留输出与备份，核对原因后重跑同一命令；已修好的真相不会再次改写，manifest 和索引可继续修复。命令不改采集历史、访问停机状态或旧通知；这些历史记录仍反映当时结果。

成功后执行 `scripts\run_web.bat`（源码启动会重建前端），重新打开详情，再只读核验：

```powershell
$postDetail = Invoke-RestMethod -Uri "http://127.0.0.1:8765/api/tasks/in_neakasa.global/3984612646028833441"
$postDetail.storage.local | Format-List
```

本帖应为 `status: complete`，`saved_images / expected_images / verified_images` 均为 `1`，`source_media_complete / media_complete` 均为 `True`，`source_media_count: 1`。这只证明素材闸恢复；实际点击生成仍须满足原有许可、预算、审校版本等条件。后端拒绝原因现在直接显示在页面上，刷新成功会清除旧提交提示且保留优化要求，不重新提交生成。

服务机 capture 和业务目录未在开发机实际修复；开发机验证使用同字段形态的构造响应、合成原图与隔离账本，不能替代本步骤的真实回读。

### 4.2 历史 IG 完整性自动核验

更新代码后，普通监测和晨间对账自动检查当前 IG 账号归档中标记不完整的历史帖子，在访问平台及清理过期增量 capture 之前执行。不需要逐篇输入 ID、寻找 capture、重新初始化或重新回填。`scripts\run_scheduler.bat --run --once` 在调度到 Instagram 时调用同一入口；没有到访问时刻则调度器不会派发，该轮返回不表示修复已执行。

自动核验读取账号目录下全部现存 `_capture*.json`，不只选最新文件。只修复证据明确且一致的单图/单视频；每篇核对身份、媒体 URL/ID，图片需解码及校验。成功先保存 `post.json.before-completeness-*.bak` 原字节和配套 `.evidence.json`（含捕获摘要、媒体类型与匹配依据），再修完整性及 manifest。正文、原图、分类、人工记录、业务账本、访问配额和采集历史不改写。视频 `metadata_only` 是正常存储形式，但仍需完整来源证据才可修复。

日志单独输出“历史归档本地核验”的检查、修复、保留数量和逐帖结果。“处理已有帖”是本轮平台扫描处理数量，不是修复成功数量。显示“首次检查发现”表示第一次保存告警数量；后续数量与上次记录比较，均不能解释为本轮下载损坏了多少篇。

只读查看当前归档缺口无需粘贴临时 Python：

```powershell
scripts\run_python.bat -m routes.delta --status --platform instagram
```

检查 `archive_incomplete`、`source_unconfirmed`、`images_unavailable`、`metadata_only_videos`、`index_mismatch` 和 `incomplete_items`。计数可以重叠，视频数是待核验帖内的视频项数。`not_in_capture_state` 表示历史归档尚未登记为监测采集项，不能使用 `--recover-post`。没有 capture、文件损坏、同帖证据矛盾或实际图片缺失时，程序保留原记录并输出原因；重复运行不会绕过证据要求，也不会触发额外详情访问。`--status` 和 `--dry-run` 不执行修复。

#### 保留待核验的帖子该怎么处理

“原图不可用”按原因分开报，因为三类的处置完全不同。`image_problem_counts` 是全账号汇总，`incomplete_items[].media_files[].detail` 和保留项的 `image_problems` 是逐张结论，`actions` 是这一篇需要哪几类动作。

| `detail` | `action` | 你要做的事 |
|---|---|---|
| `never_downloaded` / `file_absent` / `undecodable` | `refetch` | 原图得重新取，走下面的人工恢复。`source_url_expired` 为 `false` 时直接用归档里的地址重下即可；为 `true` 时那个地址已过期、单独重下只会拿到 403，恢复会自动先开一次详情换新地址 |
| `digest_mismatch` / `size_mismatch` | `adjudicate` | 文件是好图，但和归档记的哈希/字节数对不上。**先别动**：比对 `post.json.before-*.bak` 与当前文件，确认是谁改的再决定 |
| `changed_while_reading` / `locked` | `transient` | 什么都不用做。`locked` 通常是审校台正在预览这张图占住了文件，关掉那一页下一轮自己就好 |
| `read_error` / `path_rejected` | `local` | 本地磁盘、权限或归档目录的问题，先修好再核验 |

只有 `refetch` 这一类需要占用平台访问配额，且必须由人显式发起：

```powershell
scripts\run_python.bat -m routes.delta --recover-post instagram:<账号>:<post_id> --expected-revision <N> --reason "补回过期地址的原图"
```

⚠️ 它只对已登记为采集项（`--status` 里 `capture_status` 不是 `not_in_capture_state`）且状态为 `manual` 的帖有效。地址确已过期时这一次会开详情换新地址；没有 `oe` 参数、失效时刻未知的按仍可用处理，不开详情。

⚠️ **地址显示未过期、但恢复仍然报下载失败**，说明 Meta 提前作废了这个签名（`oe` 到点之前被服务端失效是已知行为）。此时**不要反复重试**：每次都是一次真实平台请求。把该轮输出留下，按 §4.1 用明确 capture 定点处理，或等下一轮自然扫描带回新地址。

修复后的列表/详情从真相读取，SQLite 在展示入口按现有刷新机制更新（历史页可能保留短时缓存）。即使真相已写入而 manifest 中断，下一次普通监测也能继续同步；原图与备份无需移动或删除。单帖工具 §4.1 仍可用于明确指定证据的定点操作，并支持 `media_type=2` 的明确单视频。

本机只用隔离归档及构造 capture 验证自动处理，实际修复数量以服务机输出为准。2026-09-22 服务机实测：归档 29 篇中 1 篇缺图，判为 `never_downloaded`／`refetch`，人工恢复后回到 `complete`；人工队列 37 条退役 33 条、保留 4 条真实失败。

## 5. 付费模型操作

先检查预算、来源许可和不确定请求：

```powershell
scripts\run_pipeline.bat preflight
scripts\run_pipeline.bat preflight --json
scripts\run_python.bat -m core.paid_requests --status
scripts\run_translate.bat --estimate
```

只有看到明确账号、篇数、预计费用和许可后再执行付费命令。第三方来源没有当前许可时不要运行。每张图最多受理三次优化，**失败的那次也算一次**（在受理入口计数）。生成过的版本在审校台图片页可以比较后换回去，换版不调用模型也不计费。

新来源指纹忽略约定的图片下载签名刷新；原图、作者、顺序或派生参数变化仍需重新确认。旧授权会核对原始身份与摘要，不修改历史文件；旧快照按其原版本核验，签名变化导致旧快照失配时重新审校冻结，不能手改 `snapshot.json`。计费不确定时仍先核账。

`scripts\run_translate.bat --check` 会发起真实付费请求，只能在上述检查与费用确认之后运行；
已有选定单篇的模型修复复验可直接按 §5.4 执行，不必额外付费自检。

### 5.1 切换图片模型前先自检

`[image].model` 默认是 `gpt-image-2`，另外可选两个精确型号：

| 型号 | 供应商说明 | 文本输入 / 图片输入 / 图片输出（USD / 百万 token） |
|---|---|---|
| `gpt-image-2.5-flare` | [Flare 官方页](https://aihubmix.com/model/gpt-image-2.5-flare)：侧重速度与日常高质量出图 | 5 / 8 / 30 |
| `gpt-image-2.5-sunburst` | [Sunburst 官方页](https://aihubmix.com/model/gpt-image-2.5-sunburst)：侧重高精度图片编辑 | 5 / 8 / 30 |

2026-09-18 核对上述公开费率，配置分别按完整型号保存。`gpt-image-2.5` 不是这里的可选调用名，
不能省略后缀，也不会自动映射成其中之一。若旧配置仍有这个名称或费率键，恢复仓库的三型号
完整费率表，并将 `model` 设为明确选定的完整型号；历史业务记录保持原样，未知旧型号不套用新型号费率。
改模型之前做两件事，顺序不能反：

1. **重新核对 `[image].cost_rates_usd_per_million` 中所选型号的三个费率与当前网关价目表一致。** 这是 token 估算，实际费用以该 Key 的网关账单为准。
2. 在本节开头的预算、许可及费用确认之后，跑一次付费自检，确认网关能接受所选型号、返回尺寸与请求一致：

```powershell
scripts\run_python.bat -m localize.images --check
```

⚠️ **`SIZE_STEP`、像素上下限、最大边长和 3:1 这组尺寸契约是按 gpt-image-2 标定的，Flare 与 Sunburst 均没有实测。** 自检只验证 816×816 可解码、返回尺寸及 usage 结构；一个型号通过不证明另一个型号或其它尺寸边界也兼容。自检不过就不要继续批量出图，通过后再用经确认的业务样本核对实际请求尺寸和费用。

### 5.2 标定「图内文字到底改没改」的告警线

每张德语图都会记录 `changed_pixel_ratio`（改动像素占比）。它忽略最大通道差不超过 24 的像素，因此 0 只表示“未检测到明显像素变化”，不证明一个像素都没改，也不证明未翻译。没有英文、模型未按要求处理、低对比度文字改动均可能得到 0。这里仅提示；先放大核对文字，再决定是否花费优化次数。

`[image].change_ratio_warn` 是"改得太少"的告警线，当前是 `-1`（关闭）。⚠️ **不要凭感觉填。** 合成样本上实测过：JPEG q60 重编码的噪声占比 0.079%，而一次真实改写文字只有 0.062%——两者会重叠，阈值只能用真实产出标定。

首批真实出图之后这样取数：

```powershell
scripts\run_python.bat -c "import json,pathlib;print(sorted(json.loads(l)['changed_pixel_ratio'] for l in pathlib.Path(r'<账号目录>/images_de.jsonl').read_text(encoding='utf-8').splitlines() if l.strip() and 'changed_pixel_ratio' in l))"
```

把"确实译过的图"和"人眼确认没译的图"两组分开看，取两组之间的空档；两组重叠就继续保持 `-1`，不要挑一个中间值。

### 5.3 业务自己换图

原图没有需要本地化的内容时，在图片页逐张点“确认使用原图”；最终图片显示“已确认使用原图”，后续加工跳过这一张。点“撤销原图确认”恢复默认选择。确认绑定当前图片字节、序号与媒体身份，源图变化后需重新确认。上传新图或采用历史生成版本表示改选相应素材。冻结后先解冻才能换图；图片正在生成时等待任务结束。确认不撤销已经发生的费用，历史图片仍保留。

备份保留 `review_items.jsonl` 中的图片选择事件。回退涉及该格式或来源指纹 v2 时，先停止加工和提交入口，只退到能识别 `image_selected` 与 `source_fingerprint_version=2` 的版本；不要删除人工选择、原图、许可/费用/发布账本或冻结字节来兼容旧代码。现有发布兼容声明已提升为 `truth_contract=2`，旧受管控制器不能自动接入；自动部署仍延期，源码更新沿用 §13。

业务在审校台图片页点击“下载本篇素材”，在外部编辑后上传替换某一张（JPEG / PNG / WebP，最大 32 MB）。替换之后：

- 这一张按**人工图优先**使用，程序不会再重生成覆盖它，该张的模型优化入口也会停用并写明原因；
- 这篇记为 **edited**，继续留在系统里走排期发布——上传是换素材，不是转交人工；
- 被替换掉的旧图保留在该帖 `media_de/superseded/`，付费产出不销毁；写入失败时当前图保持可用；
- 人工图标记、替换时间、画幅偏差提示会显示在当前图旁，连续上传后应看到新的图片；
- `media_de/manual_uploads.jsonl` 保存明确的人工选择，备份/迁移时连同图片保存，不要单独删除；否则上传历史生成图的人工决定会丢失；
- 历史版本先预览后采用，免费换版不改变日/月费用统计。已换人工图时历史版本只供查看，不允许报告成功却未生效的切换；
- 要"下载素材但不转交"时用下载按钮；"转交人工、系统不再代发"是另一个按钮，它会把这篇推进 `handed_off` 终态。

若任务长时间 pending/running，在审校台查看同一 job 的拥有进程、更新时间、request ID 和恢复分类。用恢复入口核对当前 job 版本；它本身不重发模型请求。仍执行的不能抢占；未请求/已落盘/可能计费分别处理，可能计费先人工查供应商。源文/原图/提示词变化后旧候选不能继续应用，保留人工文案/图。不可重复点击或删账本绕过三次受理上限。

中断的自动批次还要核对运行状态中的 batch_id、state_revision、operation_id、paid request 与总费用。确认落盘结果和供应商状态后，使用当前版本进行“已核对结果”恢复；CLI 对应 `recover-processing --batch-id <id> --version <state_revision> --outputs-reviewed`。版本冲突则重读，恢复不自动重新发起模型请求。

翻译提示词当前为版本 7，优化建议提示词为版本 2。`stale=false` 只说明原文没变；还需看 `machine_current`
和机器/当前提示词版本，同时单独核对每张图。旧机器译文或建议的版本失效不授权自动付费重做；
历史冻结账号的素材不能替代新账号素材，也不能从提示词版本差异推断已有合格德语图。

风险需区分未扫、失败、成功零风险，真实扫描绑定源文/提示词，发生在翻译前。标签需分别核对 Trends DE 同英文标签候选组/同时间公开 CSV、IG 全球累计与德语同类账号周更；空名单跳过、失败/过期可手选。当前 429 下不继续 IG 或 Trends 采样。

Trends 人工确认访问恢复后，先取只读版本，再按实际核对理由恢复；`--version` 必填，恢复本身不导出：

```powershell
scripts\run_python.bat -m tools.hashtag_sampling trends-status
scripts\run_python.bat -m tools.hashtag_sampling trends-reset --reason "已人工核对访问恢复" --version <revision>
```

`<revision>` 替换为刚读取的 revision，版本已变就重新核对，不删除 `trends_export_state.json`。随后用被动记录的真实 CSV 按钮生成本次 proof，核对同一英文标签的德语候选组、`geo=DE` 和起止日，再运行导出。Sign in 或任意链接不能作 CSV 控件证据；保留原始 CSV 字节、三个摘要和源文上下文，不用编辑器改换行后重新冒充原下载。当前没有可宣称通过的真实 CSV 导出。

### 5.4 DeepSeek Flash 模型名升级后的单篇复验

[DeepSeek 官方更新说明](https://api-docs.deepseek.com/zh-cn/updates/)确认：2026-09-10 起
V4.1 Flash 的调用名为 `deepseek-flash`，旧 `deepseek-v4-flash` 已退役且暂时兼容路由到新版。
当前 `[translate].model` 默认使用 `deepseek-flash`；另允许 `deepseek-v4-pro`。旧 Flash 名称在
请求前直接拒绝并提示迁移。请求 Pro 却收到 Flash 仍报错并停止整批，不能删除模型校验。

模型不匹配发生在读取 usage 之后、保存译文之前：有完整 usage 的失败会保留费用和
`output_rejected`，没有可用译文；费用未知的请求会阻止后续付费。复验顺序：

1. 服务机更新到含修复的版本，核对实际运行目录的 `[translate].model`。
2. 运行本节开头的 `core.paid_requests --status`，核对未闭合请求和被拒预算，再与供应商后台核账。
   只有查明真实结果才能结转不确定请求；`--resolve` 不能解除已闭合请求的拒绝次数限制。
3. 确认该帖来源许可、统一预算与本次单篇费用后，执行：

   ```powershell
   scripts\run_translate.bat --account in_neakasa.global --post-id 3987885751693561790 --limit 1
   ```

4. 预期单篇 `OK`、成功 1 篇/失败 0 篇；核对保存的德语正文、响应模型及新付费记录。

同一任务仍最多允许两次产出拒绝，任务标识不因模型名改变而重置。只有一次旧拒绝时可在上述
核账后重试；若已达到上限，保留账本及 request ID，先人工核对拒绝原因和后续重试方案。
本次修复没有提供额外付费授权或清零入口；不要删账本、伪造 accepted 或只改 PROMPT_VERSION 绕过限制。
原保守估算费率保持，`reasoning` 已包含在输出 tokens 中；实际费用以供应商账单为准。

### 5.5 图片模型目录为空时的核验与单图复验

供应商[模型管理文档](https://docs.aihubmix.com/cn/api/Models-API)说明，带 Authorization 的
`GET /v1/models` 查询该 Key 的 token 配置列表，并提供 `GET /v1/models/{model}` 精确详情。
HTTP 200 且有效模型 ID 数为 0 不能单独证明模型下架，也不能证明图片 Key 已有出图权限；
非空但缺少有效 `id` 的列表属于结构异常，不能按空列表放行。

含本修复的版本在合法空列表后自动补查详情，详情 `id` 精确匹配配置型号才继续。
原有翻译无需重做，保持 `gpt-image-2`，不用切换模型或修改账本。
先在服务机同一运行目录执行以下免费核验；只读取模型元数据，不上传图片、不写付费账本：

```powershell
@'
from localize.images import Settings, ImageEditor, build_client, safe_error_summary

settings = Settings()
print("base_url:", settings.base_url)
print("configured_model:", settings.model)
try:
    with build_client(settings).with_options(timeout=30.0, max_retries=0) as client:
        ImageEditor(settings, client=client).verify_model_available()
    print("model_metadata_verified: True (not a paid image test)")
except Exception as exc:
    print(safe_error_summary(exc))
    raise SystemExit(1)
'@ | scripts\run_python.bat -B -
```

若仍失败，提示会区分列表结构、精确型号和详情查询的 HTTP 错误；401/403 核对图片 Key/权限，
404 或详情型号不一致向供应商核对该 Key 的模型配置。保留安全错误摘要即可，不发送密钥或原始响应体。
不得用公开产品页替代当前 Key 的查询，也不要反复运行付费 `--check`。

元数据通过后，按本节开头核对来源许可、统一预算和未闭合请求，再对用户指定的单图执行：

```powershell
scripts\run_images.bat --account in_neakasa.global --post-id 3987885751693561790 --media-index 0 --limit 1
```

这一步才会产生真实图片费用。核对生成图的德语、图片尺寸、用量与供应商账单；
元数据通过不代表这些项目已验收。若 edits 结果不确定，先核账，不能自动重放。

## 6. 审校台人工检查

开发机 API 跑在 [127.0.0.1:8765](http://127.0.0.1:8765)，绑定开发机自己的 archive/state（当前为空，所以列表也是空的）。⚠️ **本机页面能打开不证明运营机器能访问**，运营入口按[第 17 节](#17-办公局域网接入)从业务电脑验收。下列构建/启动步骤供停止或更新服务时使用，已有实例运行时不重复占用端口。

开发机构建唯一 React 前端（见第 13 节）：

```powershell
npm.cmd --prefix web/ui ci
npm.cmd --prefix web/ui test
npm.cmd --prefix web/ui run build
```

启动 API：

```powershell
scripts\run_python.bat -m uvicorn web.api.app:app --host 127.0.0.1 --port 8765
```

**审校台按平台分了两个入口**：Facebook 待审、Instagram 待审。两边的链接、标签和长度上限规则不同，
一次只面对一套。历史归档仍然跨平台检索。

逐篇检查英文/德文、图片、标签、链接、作者/合作方和来源版本。保存、挂起、不发、资源下载和人工接管会写真实追加式账本。

正文区的「复制发布文案」给的是最终成品——正文加链接或引导话术加标签，就是要贴进 Business Suite 的那一份。
校验还没回来时按钮是灰的：那会儿只有近似值，复制出一份和实际发布不一致的文案比没有这个按钮更糟。

「优化建议」是可选的一步，模型逐条指出可以改的地方，**采纳与否你定**；它不会动任何译文，
改完仍要自己点保存。译文改过之后旧建议会标失效并停用「采用」，需要就重新生成。

生成建议审的是点击时编辑区中的正文，尚未保存的修改也会带上；请求不代为保存。
后台等待期间继续改正文会使返回的旧建议失效；采用一条后也要重新核对剩余建议。
忽略只作用于这一轮，重新生成后相同片段的建议仍会展示。复制受浏览器限制时会打开完整文案供手动复制。

⚠️ **本轮标签没有热度推荐**（[REQUIREMENTS §9](REQUIREMENTS.md#9-明确延期与固定边界)），标签区只做人工选取与编辑。

Facebook 在链接区确定德国落地页后，可把对应 `{{linkN}}` 插入正文当前光标处；最终预览/计数会换成 URL，未插入的有效链接仍追加末尾。无效编号、缺目标或损坏占位符先修正，Instagram 使用 bio 话术。编辑中的“约”是临时估算，等待 `/check` 返回当前完整草稿的最终计数，包含标签、实际链接或 CTA；不以占位符短长度判断是否可发。

风险夹具只供明确演示模式，不能据此批准真实内容；正式页面读取实际风险状态和来源。采样降级时仍可人工决定，不把缺数据写成热度为零或已扫描安全。

从历史入口检查服务端分页、平台/月/tag/状态筛选（各含显式「全部」选项，月份跟随行内日期）、总数及 90 天外详情，冻结 `.tech` 只读；查询不会启动翻译。默认排期时刻与挂起工作日数不在界面上设置（2026-09-23 起设置页移除），由维护者改配置，见第 15.3 节。五阶段运行状态显示真实激活、进程/处理、429、飞书凭据、镜像和月历状态，查看状态不触发外部操作。源发帖至首次就绪和批次处理时效分别显示，前者包含发现等待；没有历史就绪事实时不期待补出统计数字。

遇到 409 版本冲突时刷新后重做决定。遇到账本解析错误时停下来保留文件；不要删掉坏行让界面继续。

### 6.1 上线前的一个业务前置：IG 的 bio 聚合页

⛔ **这是业务要先建的东西，不是系统功能**，`[publish].ig_bio_url` 现在是空的，链接区显示"未配置"。

Instagram 文案里的链接点不动，运营靠一句"详见主页"把人引到账号主页。所以主页那个位置
**必须是一个稳定的聚合页**（Linktree，或 `de.neakasa.com` 上一个入口页），不能是某一篇帖子的落地页。

理由是它和定时发帖硬矛盾：帖子排到 9/15 10:00 自动发出，文案写着"Link in Bio"——
**谁在那个时刻去把 bio 改成这一篇对应的落地页？** 要让每篇都对，就得有人守在每个发布时刻改 bio，
那等于把定时发布的价值抵消掉。

建好之后把地址填进 `config.toml` 的 `[publish].ig_bio_url`。系统只在链接区**只读显示**它，
不会自动改 bio，也不会提醒你去改。

## 7. 飞书群与云盘真实联调

卡片/聚合/恢复代码已有离线验证。第 2.1 节需人工核对四张自检卡的真实发送者；HTTP 成功只证明接口接受请求，不能独自证明机器人对应、业务内容或运营能否打开链接。逐条验证：

0. 恢复 9224 探测会话后跑一轮真实探测：检测机器人先发「监测到新帖」，爬取机器人后发「原帖抓取完成」，明细与本地目录、图片数一致；在受控演练中停止探测 Chrome，由状态告警机器人提示系统异常；
1. 同一群内按四机器人核对全部八类：检测、抓取各一类；发布接待审/排期成功；状态告警接积压/排期失败/晨间摘要/系统异常。`scheduled` 仍只表示远端排期回读确认；
2. 待审排队后修改文案/首图，核对卡片给的是当前有效人工/机器摘要与检查结果；首图状态写成文字且注明「未随卡片投递」——**卡片里没有缩略图是当前形态，不是故障**；重读失败时卡片应明说内容可能不是最新；
3. 同帖发现+处理+重启按帖只计一次；挂起/不发/接管/排期项从待审卡片退出；
4. 离岗三篇在次日 08:00 合并，含兜底补抓、分类跳过（四类都应是中文）、延迟/失败与最早等待；无事不推空日报；
5. 受控未知投递重启后保持暂停；先在群里核对，再按当前 delivery 版本登记已送达或确认未送达。当前角色的有效原卡保持 ID/内容；旧角色转交则保留旧记录并创建当前阶段投递。不能凭网络异常断言未送达；
6. ~~多收件人部分失效~~：每个角色现在只有一个群，这条在生产里不再触发（[REQUIREMENTS §10.4](REQUIREMENTS.md#104-阶段四飞书主入口与运行可见性) 已标明确延期）。加第二个群之后再验；
7. 镜像 v1 后生成 v2，确认 v1 不覆盖；
8. 在受控恢复演练目录移除一个已备份测试版后，用冻结 v1 补送，确认没有误用当前 v2；
9. 验证两个周期的配置/账本包、大型证据独立版本和副本恢复；确认远端修改不会回写本地。

在运营机器点击真实卡片到对应帖子详情，记录群、机器人名称、日期、任务与云盘 file ID，不记录地址或密钥。机器人没有 message ID；证据是群里实际消息、人工核对记录与本地 delivery ID。关机/断网/停调度的缺席告警由外部心跳服务验证。

outbox 的终态在线保留期默认 30 天，过期完整关联组件归档后原卡片、UUID 与回执仍可读，事件去重索引留在主状态。未决、待重试、待发和离岗待发不归档；不要为缩小文件手工删除这些记录。归档异常时保留原文件与 hash 记录核对。

### 7.1 原帖云盘接入与恢复

⛔ **本节本轮不执行（2026-09-15 业务决定，见 [REQUIREMENTS §9](REQUIREMENTS.md#9-明确延期与固定边界)）。** 下面的步骤留着，拿到应用凭据后照做；在那之前不要因为 `mirror_status` 显示 `disabled` 就来补实现或改配置。

**目标已确定为[原帖归档文件夹](https://genhigh.feishu.cn/drive/folder/TsQef8msClS2i6dlzpfcpfn6nAc)。** 代码配置保留该 folder token，`mirror.enabled=false`。目标内容、权限、容量与运营账号可见性均未实测；本轮 worktree 环境文件没有应用凭据。

**管理员准备：**

1. 在飞书开放平台创建企业自建应用，启用机器人；按下表申请接口权限，由管理员批准并发布可用版本。当前文档提供的细分权限为：建目录 `space:folder:create`、上传 `drive:file:upload`、目录列表 `space:document:retrieve`、移动 `space:document:move`；[异步任务查询](https://open.feishu.cn/document/uAjLw4CM/ukTMukTMukTM/reference/drive-v1/file/task_check)另需 `drive:drive.metadata:readonly`。各接口也接受范围较大的 `drive:drive`；是否授予统一权限由企业管理员按实际调用范围决定。
2. 按[应用获得目录访问权限的官方步骤](https://open.feishu.cn/document/uAjLw4CM/ugTN1YjL4UTN24CO1UjN/trouble-shooting/how-to-add-permissions-to-app)，将已发布机器人加入授权群，并把目标文件夹分享给该群。应用权限和资源权限是两道条件；开发者本人能打开目录不能证明应用有权限。运营读者配置为只读。
3. 在实际运行绑定的 `.env` 中填写 `FEISHU_APP_ID`、`FEISHU_APP_SECRET`，不放进 Git。用审校台运行状态检查是否存在凭据；该状态不会打印值。
4. 在目标下人工创建独立验收子目录，记下其 folder token。验收使用独立 archive/state/.env 绑定，在该运行配置的 `[mirror]` 设 `enabled=true`、验收子目录 token、`mirror_state=false`，先只验原帖。已有镜像队列不能换根 token 后重置；不同验收根应使用独立 state，保留旧队列和冻结字节。

**接口限制与实现依据：**

| 操作 | 核对入口和实现约束 |
|---|---|
| 建目录 | [create_folder](https://open.feishu.cn/document/server-docs/docs/drive-v1/folder/create_folder)，目录名按 UTF-8 计最多 256 字节；分类名与本地相同。 |
| 元数据分页 | [list](https://open.feishu.cn/document/server-docs/docs/drive-v1/folder/list)，逐页处理 `has_more/next_page_token`，单页至多 200 项；同名文件不是相同内容的证据。 |
| 普通上传 | [upload_all](https://open.feishu.cn/document/server-docs/docs/drive-v1/upload/upload_all)，上限 20,971,520 字节；普通 Drive 文件使用 `explorer` 父节点。空正文由清单表达。 |
| 大文件 | [分片上传](https://open.feishu.cn/document/uAjLw4CM/ukTMukTMukTM/reference/drive-v1/file/multipart-upload-file-/introduction)，保存 upload ID、分片及完成进度；超过 24 小时有效期停止，完成请求结果未知先核对。 |
| 移动 | [move](https://open.feishu.cn/document/uAjLw4CM/ukTMukTMukTM/reference/drive-v1/file/move)，采用 20 次/分钟的较严限制，真实回读确认之前保持待处理。 |

**操作命令**（在已核对绑定的 worktree 根目录运行）：

```powershell
scripts\run_python.bat -m tools.mirror status
scripts\run_python.bat -m tools.mirror preflight
scripts\run_python.bat -m tools.mirror run --account in_neakasa.global
```

无参数和 `status` 都是本地只读预览。`preflight` 会认证并读取目录元数据，返回 `root_visible` 和 `write_permission=unverified`：可见不等于可上传或移动。`run` 才会冻结、上传和处理移动；兼容旧 `--run`。常驻服务独立执行镜像，原帖入档即可排队，不等待本地化完成。

**遇到 `uncertain` 或 `blocked`：**

1. 保留 `mirror_queue.json`、`mirror_spool/` 及操作 ID、错误码、请求标识。先运行 `status`，再用 `preflight` 读取分页目录和任务元数据。不要删 token、队列或冻结文件来“重试”。
2. 唯一同名目录只是候选，需要核对父目录与意图；多个候选或权限不足继续保留不确定。文件由人工核对远端文件、时间、名称和内容；必要时下载到**隔离目录**，用 `Get-FileHash -Algorithm SHA256` 比较冻结摘要。代码不会从云端下载正文或原图。
3. 明确成功后绑定原操作 ID 和远端 token，文件操作必须提供对应冻结 SHA-256；未创建则填写相同操作的未创建确认及依据：

```powershell
scripts\run_python.bat -m tools.mirror resolve --operation-id OPERATION_ID --sha256 FROZEN_SHA256 --remote-token VERIFIED_TOKEN --note "已核对远端文件与冻结摘要，记录见验收表"
scripts\run_python.bat -m tools.mirror resolve --operation-id OPERATION_ID --sha256 FROZEN_SHA256 --not-created --note "已检查原父目录和操作记录，确认未创建"
```

这两条是二选一的模板，替换为实际值后执行。目录操作不要求文件 SHA；移动成功使用原目录 token。`resolve` 只记本地人工结论，不执行上传，不能和 `--run` 组合。明确未创建后，另行运行 `run` 恢复。计数限流先退避；权限、容量或每日配额先解除阻断；请求结果未知不得直接重发。

**本地与数据库恢复：**

```powershell
scripts\run_python.bat -m tools.layout recover-tags instagram --dry-run
scripts\run_python.bat -m tools.layout recover-tags instagram
scripts\run_python.bat -m tools.layout reindex-db
```

分类移动恢复只处理已记录的意图；损坏或内容冲突保留现场，在副本核对。普通查询不会触发恢复写入。数据库重建只写派生 SQLite；历史入口独立使用 `index-history.sqlite`，首次查询会按当前源文件重建。详情显示媒体保存数及上次数据库核验时间。

**真实验收清单：**

- 人工确认 9222/9224 登录、账号可用后取得至少一篇 Facebook、一篇 Instagram 真实图文，至少一篇多图；不自动登录或滚历史。
- 比较原文、来源平台、目标账号/真实作者/合作方、图片数量和顺序。新帖目录用北京时间，原 ISO 保留；型号别名归一，人工清空仍有效。
- 查询 SQLite 三表，核对帖子身份、分类顺序、媒体路径、尺寸、字节数及 SHA。对已知缺图应明确显示未完整。
- 在验收子目录核对 `月份/产品/单帖/01_原帖[_vN]`、file/folder ID、运营只读可见性；人工下载隔离副本逐项比较 SHA。
- 再次同步不重复；只改分类移动目录且不重传；原文或原图修订产生新版本；缺图补齐和重启补送不冒充已完成，旧版本仍可核查。
- 保存 UTC 测试时间、样本 ID、文件清单/哈希、远端回执与人工核验结论。只有这些企业证据可复核后，才将相应单元从“待真实联调”改为“真实通过”。

## 8. 录制单渠道 Business Suite 证据

**G1 已按 2026-09-20 用户确认的范围关闭，无需重复录制。** 当前使用
`publish_probe_20260920_100921_378853.json`，`ui_probe_dump` 已绑定此文件，
`ui_constraints_verified=true` 表示接受这份控件录制，不表示人工测量过所有 UI 边界。

本次采用的业务决定：

- 发布服务器和 Business Suite 设备时区均为北京时间，`ui_timezone=Asia/Shanghai`；
- Instagram 共通详情结构沿用 Facebook，缺少单独的 IG 历史详情不阻塞适配器；
- `observations` 可以为空；不要求测量或配置画幅、正文长度/计数方式、排期提前量、输入失焦和非法值提示；
- 排期窗口以当次 Facebook/Instagram 界面的实际接受结果为准。代码仍检查未来时刻、当前月历覆盖、同渠道间隔，以及填写后的日期时间回读。

部署时保留原 JSON 和同名 `_screenshots` 目录，置于实际配置的 `state/` 下；不要修改 JSON 中的旧机器路径。
录制可跨机器复制，校验专用 profile 名称和 9223 端口；实际附着继续检查完整 profile 路径与三浏览器隔离。
不复制登录凭据，不把历史 FB remote ID 登记成 IG 排期。

以下只读命令可复核现有证据，不会打开浏览器：

```powershell
scripts\run_probe_signals.bat --check state\publish_probe_20260920_100921_378853.json --caption "This is a manual test."
scripts\run_pipeline.bat preflight
```

预期 G1 的五项结构信号和同页因果链通过。`planner_loaded_signal` 是月份定位依据，
运行时仍须读取实际日期格和条目，不能凭月份标题判断日历为空。

G1 和真实提交验收分别记录。`channel_controls.json`、`planner_controls.json` 的部署状态、
真实单渠道提交及远端图片读取属于其余发布能力；若 preflight 报告这些项目，按项目名处理，
不能再归因为 G1 缺画幅/正文计数/IG 重复录制。远端图片适配器由开发侧完成，
新排期仍须按第 9 节展示具体内容并确认。现有 FB 任务 `2059528092104126` 不为补证重复提交。

发布浏览器可能仍保留旧技术测试草稿；先识别其内容，不直接提交。编辑器缩略图检查不替代远端排期图片核验。

未来真实 UI 改动导致运行时定位失效时再定位具体变化；无需定期重录或重新填写 18 项观察。
录制工具及 `--fill-notes` 保留作可选诊断用途；本次无须执行。

### 8.1 月历更新、单条取证与后续复验

服务机单条日志已验证 IG Story，并提供直接关联的 FB Story 实体；适配已加入 FB owner、预览作者、标题与自身发布时间核对。
三个截图确认 IG/Total performance 显示 `This content has no text`，Facebook 显示 `Your story`（小写 s）。
先用生产读取器复验这条详情；完整返回两个独立渠道后，再进行一次整月刷新。FB 正文保持 unknown，不要求把界面标题证明为空正文。
保留 G1 文件、北京时间配置、既有月历 probe、缓存及业务数据，不重录 G1。

1. 确认没有发布、排期或月历刷新正在运行；在旧 Web 的 PowerShell 按 `Ctrl+C` 正常停止服务。旧进程仍运行旧代码，可能打印调用栈或询问是否终止批处理；更新源码并用新入口重启后，后续 Ctrl+C 才会显示简短的停止提示。
   保持发布 Chrome 9223 打开。另开 PowerShell 检查源码状态：

   ```powershell
   Set-Location 'D:\Code\FacebookScraper'
   git status --short
   git branch --show-current
   ```

   若有源码修改，先保留并处理其归属，不运行 `reset --hard`、`clean` 或覆盖文件。
   `.env`、本机路径配置、`archive/` 和 `state/` 保持原样。

2. 工作区干净时逐条更新；任一步报错就停止后续步骤：

   ```powershell
   git fetch origin
   git switch main
   git pull --ff-only origin main
   git log -1 --oneline
   scripts\run_python.bat tools\probe_calendar_detail.py --help
   ```

   帮助中应包含 `--verify-reader`。此模式已自行监听新详情的首次响应，不与 `--reload` 或 `--responses` 混用。

3. 在发布专用 Chrome（9223）中保留唯一的 9 月 4 日 18:39 Story 详情，核对
   `Story · Published on: Fri Sep 4, 6:39pm`。已打开正确详情时不用重新进入月历。
   可保持 Total performance；工具不会改变这个原页面的渠道选择。不能换用 9222 或 9224。

4. 在 PowerShell 完整执行以下代码块一次，只复制代码，不带终端提示符：

   ```powershell
   Set-Location 'D:\Code\FacebookScraper'
   $DetailLog = Join-Path $env:TEMP ('planner-reader-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '.log')
   scripts\run_python.bat -u tools\probe_calendar_detail.py --date 2026-09-04 --time 18:39 --kind Story --verify-reader 2>&1 | Tee-Object -FilePath $DetailLog
   Write-Host "诊断日志：$DetailLog"
   ```

   工具持发布锁、核验 profile，只为当前详情打开一个临时副本，调用生产读取入口，结束后关闭副本并回到原页。
   不刷新整月、不写月历缓存、不登录、不发布、不排期、不调用模型。
   只被动观察页面自然返回的响应；输出根实体/跨帖关系的字段结构、白名单 ID/类型/账号及文字长度。
   非数字 ID 若为 JSON 或 base64 JSON，只做有界解码后再次脱敏；不执行内容，不输出任意字符串原值或凭据。
   该解码结果只供诊断；生产 remote_ids 使用明确关联的数字实体 ID。

5. 重点看 `READER_RESULT` 的 `complete=true` 和两个独立 variants。本条 IG 的预期结果为：
   `remote_ids.instagram=18084155825688886`、`accounts.instagram=neakasa.de`、`placement=story`、
   `caption_status=empty`、`caption_length=0`、`ui_at=2026-09-04T18:39:00`。
   FB 应为 `remote_ids.facebook=1781315906229402`、`accounts.facebook=Neakasa Deutschland`、
   `placement=story`、`caption_status=unknown`、`caption_length=0`、`read_status=complete`，本条截图时间同为 18:39。
   FB 正文 unknown 是预期展示，表示 `Your story` 未被当成正文；不表示身份或占用未知。
   若末尾为 `PARTIAL`、complete=false 或退出码 2，保留整份日志发回，先不刷新整月，不连续重跑。
   日志里还有一条 `PREVIEW_STRUCTURE`，记录读取器在 Feed preview 区域每一层看到的节点结构；
   预览类字段（如 `facebook_story_preview_owner`）失败时，定位就靠它，务必一并回传。
   尤其 `time_mismatch` 需核对同一个 FB 对象的 created_at，不能拿 IG 或后台 BusinessContent 时间补齐。
   `STOP: matching detail pages: 0` 仅表示没匹配当前打开的详情；2 或更多表示不唯一。
   发布锁忙时等待正在运行的操作结束，不删除锁文件。

6. 单条复验后重新启动 Web，使用会重建前端的入口：

   ```powershell
   scripts\run_web.bat
   ```

   等启动完成后打开 `http://127.0.0.1:8765` 并按 `Ctrl+F5`，保持该终端运行。
   另一个 PowerShell 可以只查询缓存：

   ```powershell
   Invoke-RestMethod 'http://127.0.0.1:8765/api/calendar' |
       Select-Object status, refresh_status, cached_at, error, coverage, refresh_diagnostic |
       ConvertTo-Json -Depth 6
   ```

   旧失败缓存及 `cached_at=null` 可能仍在：单条验证不写缓存，重启也不会产生成功读取。
   第 5 步单条完整通过后执行下方整月复验；若单条未完成，只查看缓存并保留诊断。

**单条完整通过后的整月复验。** 保持发布 Chrome 登录，
页面点一次「刷新月历」，完成后用第 6 步 GET 查询，不重复触发刷新。
整月通过要求 `status=ready`、`refresh_status=refreshed`、`error=null`、`refresh_diagnostic=null`，
`coverage.matches_current_month/grid_complete/entries_complete/classification_complete/channels_complete/occupancy_complete/decision_complete` 均为 true，
`unresolved_count=0`，且 `cached_at` 更新为此次完成时间。
`status=ready` 同时 `unresolved_count>0` 只说明网格已经同步、未读卡片仍显示在月历上；这不能确认空档，也不是整月通过。
旧缓存若没有 `time_verified`，按时刻未核实，不能用来排除占用。
仍在 2026 年 9 月时，日期格范围应为 `2026-08-30` 至 `2026-10-03`；核对 9 月 4 日 Story、
9 月 30 日 17:30 手工帖子和其它实际类型，逐渠道对照账号、时间与 ID，不预设两渠道相同。
同渠道且时刻已核实的内容继续遵循 90 分钟规则。渠道未知落在目标前后 90 分钟内，或时刻没有独立证据时，不能判为空档。
保留旧数据或 `decision_complete=false` 均不算本次完整读取通过。
全程不得新增、修改、删除或重新提交真实内容；开发机隔离测试不能代替服务机结果。

## 9. 真实发布前的最终确认

在可能提交 Business Suite 的动作之前，先准备两篇实际联调内容（FB-only 一篇、IG-only 一篇），每篇给用户审查：

- 来源帖子和来源版本；
- 最终德语正文、hashtags、链接/bio 话术；
- 每张最终图片及哈希；
- 唯一渠道和目标账号；
- 北京时刻（2026-09-23 起界面不再并排显示德国受众当地时刻，联调包仍须自行核对受众侧钟点）；
- Planner 当前覆盖与同渠道前后 90 分钟冲突结果；
- 本次冻结快照位置和预算状态。

两个联调包都不存在：清库把归档和此前准备的 FB 待制作包一起删了。要重新走到这一步，得先回填出有正文和图片的新帖，再逐篇准备。

用户确认后才执行一次提交。提交前后都不要编辑冻结目录。只有 Planner 回读确认目标渠道、时刻和 remote ID 后才能写 `scheduled`。`scheduled` 不代表到时已经公开。

两篇合起来须分别覆盖长正文完整回读、多图字节/数量/顺序、FB 德国链接、IG CTA；人工选时若同渠道 90 分钟内冲突，拒绝并给建议，不自动顺延。提交前再读完整远端 Planner，不能用未加载完的月历判空档。公开状态仅只读观察，未证实写 unknown。

月历显示 partial 时仍保留异常和旧缓存时间；是否可以排期由当次实时读取按目标渠道、相关时段判断。已核实在范围外的详情异常不阻塞该时段，可能影响的未知条目仍会拒绝。最后一次读取完成后系统再次只读核对表单；等待期间不要修改正文、图片或时刻，发现变化会停止，请重新打开当前审校结果确认。远端删除登记仍须完整读取证明目标确实消失。

当前已实现目标图片诊断采集、冻结字节比较和原 attempt 只读补验；FB/IG 完整有序媒体布局尚缺真实结构依据，不能通过 G8。优先按 §16.4 对原 scheduled 对象取证，再补布局适配；没有合格样本才具体确认后排期。编辑器图片、成功 dialog 或仅正文/ID 回读不能替代远端完整媒体证明，历史 scheduled 继续防重。

最终由运营走完真实飞书卡片 → 当前帖审校/修改/检查 → 北京选期/确认 → 单渠道回读 → 飞书回执，记录实际任务与消息。两次独立脚本提交不能代替此流程。

点击后进程退出、回读失败或 remote ID 不一致时，不要重试。查 `published.jsonl`、Planner 和冻结快照，按不确定提交处理。

⛔ **开发机的 `published.jsonl` 已随 2026-09-14 清库删除，那条旧双渠道不确定账目连同防重记录一起没了。** 所以「本地查不到这一篇」不能当作「没提交过」——历史上可能有已提交但本地无记录的尝试。服务机建立自己的发布账本之后，以它为准；两边账本不合并。

## 10. 激活边界

激活只影响边界之后的新内容，旧归档不得自动补发。接续旧状态时先运行只读 `preflight/status`，核对激活时间、已发布引用和不确定记录。不要为了“重新开始”删除状态。

本轮只读 preflight 的 exit 0 只表示查询完成，实际仍显示探测 blocked、内容 not_observed、飞书 disabled，以及两渠道 acceptance.verified=false。不能仅按退出码启动调度或提交。

旧激活边界已经存在，接续时保留，不能再 activate 覆盖成今天。在 `.global` 回填、关键代码缺口、企业联调和受控真实验收前置未满足时，不启用常驻自动运行；当前持久停机标记保留到人工核对后。

## 11. 安装当前调度器

本节用于未接入部署控制器的独立检出。受管服务机按[第 15 节](#15-服务机部署与日常更新)安装唯一控制器任务，通过 `deployment mode` 管理调度，不并行安装这里的旧任务。

⚠️ **Web 进程在跑不代表常驻监测任务已安装**，两个进程的状态不能混用。开发机当前四个计划任务都是未注册。

`tools.schedule install` 是旧每日组合。常驻 scheduler 当前已有下列入口，先做只读预览与安装演练：

```powershell
scripts\run_python.bat -m tools.schedule scheduler-xml
scripts\run_python.bat -m tools.schedule scheduler-install --dry-run
scripts\run_python.bat -m tools.schedule scheduler-status
```

检查 XML 的工作目录、`scripts\run_scheduler.bat`、运行账户、登录条件、日志位置与旧每日任务冲突。实际安装前已有状态仍应备份，并核对：

```powershell
scripts\run_scheduler.bat --preview
scripts\run_pipeline.bat preflight
```

只有真实依赖验收完成才用 `scheduler-install` 安装并允许 `--run`。管理入口 `scheduler-disable` 会停用定义并结束常驻实例；`scheduler-enable` 检查旧任务冲突后恢复并启动。它们会改变实际计划任务，按维护窗口执行，不为演示而运行。安装后验证查询、停用、恢复、重启单实例、不补跑睡眠全部轮次与三 profile 隔离；进程中断付费任务仍先核账，不自动重放。

提交报告前，按 [AGENTS 第四节](../AGENTS.md#四按影响选测试默认不跑全量) 选取与改动相关的验证，保存命令、版本与结果。Python 定向隔离示例：

```powershell
scripts\run_python.bat -m tools.test_offline --only tests_parse
```

`--only` 可重复，按改动选择脚本；省略时运行全量，仅在影响范围确有需要时使用。`tools/test_offline.py` 给每个脚本独立的 archive/state/环境和日志——⛔ **它不会让测试结果自动变成真实外部系统结果。** 前端改动运行相关单测与构建；构建保留已有的大 chunk 提示。

脚本失败时终端显示日志开头和末尾，并给出该脚本完整日志路径；中段只在文件中保留。出现 `Event loop is closed` 或 `closed pipe` 时先查看开头的首个异常，不能仅凭退出清理信息判定业务浏览器崩溃。

需要完整前端验证时，再选用以下入口，不逐次全部执行：

```powershell
scripts\run_python.bat tests/tests_browser_workflow.py -v
scripts\run_python.bat tests/browser_regression.py --stage ALL
scripts\run_python.bat tests/cutover_rehearsal.py
scripts\run_python.bat tests/review_probe.py
scripts\run_python.bat tests/history_thumbnail_cost.py
```


⛔ **不要对绑定真实数据的目录直接运行会写入的测试脚本。**

部署形状另有静态路由测试；浏览器夹具自带的 SPA 回落不能替代它：

```powershell
scripts\run_python.bat tests/tests_spa_static.py
scripts\run_python.bat tests/cutover_rehearsal.py
```

## 12. 卡住时保留什么

不要先清理现场。提供：

- 完整命令和时间；
- `git rev-parse --short HEAD` 与 `git status --short`；
- 相关日志最后一段，先检查没有凭据；
- 对应 archive/state 文件名和哈希；
- 若涉及 Business Suite，probe dump 名、交互序号和遮罩截图；
- 若涉及付费，request/job ID、账本状态和供应商侧是否计费；
- 若涉及飞书/云盘，非敏感的 message/file ID 和远端错误码。

不要提供 cookie、token、`.env`、完整用户数据截图或未遮罩的浏览器 dump。

## 13. 更新并启动审校台

**当前服务机采用源码更新，不依赖 GitHub Actions 或云端运行包。** 开发机完成本次改动的定向验证后交付，服务机继续使用已有源码目录、解释器、凭据和数据绑定。仅删除工作流或修改说明无需重启服务机。

Facebook、Instagram 待审核入口的四个子分类均按原帖发布时间从新到旧排列。更新后各选取不同发布时间的帖子，核对列表、分类／月份筛选及详情“上一篇／下一篇”顺序；表格中的“时刻 · 柏林”仍表示候选或已有排期，不作为列表排序依据。

“话题标签与链接”页的两个分区均可直接勾选确认，无须先点“编辑德语”；等待“确认已保存”后，刷新应仍保持勾选。Facebook 先打开检查各落地页，再确认整个链接区；Instagram 核对引导语，也可确认“无链接／不添加引导语”。编辑标签、落地页或引导语会取消相应分区确认，随草稿保存。旧记录升级后，有链接或引导语的帖子须补一次分区确认；保存失败不算确认成功，版本冲突时载入最新内容后重新核对；正文版本已过期时，先进入“编辑德语”复核并保存正文。

项目只维护 `web/ui/` 下的 React + TypeScript 前端。构建输出为 `web/ui/dist/`，`config.toml` 的 `[paths].web_dist` 显式指向该目录；`config.local.toml` 只接续 archive/state，不选择前端。部署契约由 `tests/tests_spa_static.py` 与 `tests/cutover_rehearsal.py` 守住。

源码检出的前端产物不随 `git pull` 更新，服务机需要 Git、Python 和 Node.js/npm。日常更新按以下顺序执行：

1. 在现有源码目录记录 `git rev-parse HEAD`，检查 `git status --short --branch`。确认当前为 main、工作区干净；有本地改动时先核对和保全。
2. 保存运营草稿，等待抓取、模型、上传和发布工作结束，在调度器空闲后正常退出需要更新的 Web 与调度进程，不中断正在进行的业务。
3. 拉取源码；出现冲突或分支分叉时保留现场，不继续启动：

```powershell
git pull --ff-only
if ($LASTEXITCODE -ne 0) { throw 'Pull failed; stop before restart.' }
```

4. Python 依赖发生变化时，使用当前绑定解释器更新依赖；无变化时复用已有环境。下面命令失败时停止更新并保留错误，不重跑包含全量测试的 `setup.bat`：

```powershell
scripts\run_python.bat -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed; stop before restart.' }
```

5. 局域网服务机运行 `scripts\run_web_lan.bat`（网络配置见 §13.1）；本机开发运行 `scripts\run_web.bat`。脚本每次安装锁定前端依赖并构建，成功后才启动 Web。失败时保留 npm 错误，修正后重试，不提供旧页面。
6. 按原有命令和已授权模式恢复调度器，核对页面、运行状态和本次修改涉及的功能。凭据、路径绑定、`archive/state` 及处理权限保持原状。

在服务机本机回环地址查询 `/api/health`，按实际端口访问；预期 HTTP 200 且 `error=null`。`managed=false`、`deployment_ready=false` 是源码模式的正常值；运行版本在实际进程的源码目录用 `git rev-parse HEAD` 核对，不要求制品 SHA 或运行指纹字段。只看 HTTP 200 不能证明所有业务依赖可用。

旧受管实例另见 [第 15 节](#15-服务机部署与日常更新)，不要在 `releases` 内执行源码更新。直接运行 Uvicorn 不经过前端构建入口，不能作为本节重启步骤。

以下清单按本次改动选择；涉及的检查失败时停止更新并保留报告。代码回退通过正常 Git 反向提交恢复已知可运行版本，依赖与前端按该版本重新准备；账本、人工稿和原图保留当前数据，不恢复旧数据副本。

### 集成前检查

- [ ] 按改动范围选定检查并记录结果；纯文档或工作流删除检查内容、引用、换行及 diff
- [ ] Python 改动运行直接相关的隔离测试；跨模块改动先说明影响，再选择子系统或全量回归
- [ ] 前端改动运行相关单测与构建；浏览器交互选择对应场景
- [ ] 静态路由或部署形状改动运行 `tests_spa_static.py` 与 `cutover_rehearsal.py`
- [ ] 涉及配置键、启动入口或模块依赖时补相关测试及必要的 hygiene 检查

### 合并与启动

- [ ] 按仓库约定确认推送并将开发分支合入 main
- [ ] 服务机按上述顺序拉取、构建并启动；源码局域网使用 `scripts\run_web_lan.bat`
- [ ] 核对 `[paths].web_dist = "web/ui/dist"`、构建产物存在及本次修改的功能
- [ ] 核对入口：独立开发为 `127.0.0.1:8765`；源码局域网读取网络 JSON（§13.1），从实际业务地址检查访问

`DIST` 的目录在 `web/api/app.py` import 时确定，但每次请求仍读取其中的文件。
旧版启动脚本只启动 Python，不会将更新后的源码变成新构建；当前脚本已补上构建步骤。

### 只读检查

涉及路由或页面改动时，从下面选择相关入口，直接输入地址覆盖服务端深链接回落：

- [ ] `/review`
- [ ] `/history`
- [ ] 一篇活账号详情并在该页刷新
- [ ] 一篇冻结账号详情：确认 `read_only` 且没有写入入口
- [ ] `/calendar`
- [ ] `/runtime`
- [ ] 旧链接 `/?task=<真实账号>/<真实帖子>` 跳到 `/review/...`
- [ ] 旧链接 `/?view=history` 跳到 `/history?page=1&limit=50`
- [ ] 浏览器 console 无报错
- [ ] Network 没有异常 404/422
- [ ] 北京时刻和四个队列计数与实际数据一致

菜单定位复验：在系统或 DevTools Rendering 中启用 `prefers-reduced-motion: reduce`，
分别在常规窗口及约 827×730 的窄窗口打开列表、FB/IG 详情的“三点”。核对菜单位于按钮附近，
“待我审／已修改／未就绪”可进入“稍后再审／这篇不发”对话框，“已挂起”可进入“恢复审校”；
只点取消，核对焦点回到三点按钮，再次展开仍正常。恢复原动画偏好后再核对一次。
若仍出现在屏幕外，记录实际前端资源 hash、动画偏好、菜单坐标与计算后的过渡时长。
拉取含启动修复的源码后，正常停止旧 Web 进程并重新执行 `scripts\run_web.bat`，确认 npm 构建成功，
再刷新页面核对菜单；旧受管实例的恢复边界见第 15 节。
本机离线定向命令为 `scripts\run_python.bat tests/browser_regression.py --stage REVIEW_MENU`，运行前先构建。

若优化能力查询 `/api/refinements/task/...` 仍返回 500，另取服务机 Python 日志中该请求的
Traceback 末尾、异常类型和错误说明，遮去密钥；`Internal Server Error` 或 React 调用栈
不足以定位后端原因，菜单定位修复不等于该接口通过。

### 低风险写入检查

由你或授权运营手工执行，每步之后回列表核对状态：

- [ ] 编辑德语正文并保存，返回列表后再打开仍正确
- [ ] 修改产品分类并保存
- [ ] 挂起一篇并恢复

### 可选外部只读检查

- [ ] 只有需要刷新 Planner 时才执行一次 calendar refresh；它会打开发布浏览器读取后台，通常需要数十秒

### 经确认的外部写入检查

先核对当前会话是否已明确授权这篇具体帖子、最终文图、目标账号、唯一渠道和北京时刻。已有这份具体授权就继续，不重复申请；缺少任一项时停在提交前补齐确认。

- [ ] 选一篇明确允许用来测试的帖子和北京时刻
- [ ] 执行真实 approve
- [ ] 在 Business Suite 确认排期存在
- [ ] 审校台回读为「已排期」
- [ ] 回执必须是 `ok === true && status === 'scheduled'`，不能只因为 HTTP 200 判成功

### 第一个工作日观察

- [ ] 历史归档页缩略图补齐速度；原归档估算首屏约 12–13 秒，见 [HANDOFF §7](HANDOFF.md#7-审校台缩略图实测)，太慢先改成每页 20 条
- [ ] `/runtime` 状态
- [ ] 队列计数与列表局部更新是否一致
- [ ] 记录 409 恢复和运营不清楚下一步的情况

### 出现问题时停止更新并留证

- [ ] 停止当前服务，保存命令、版本、浏览器错误和测试报告
- [ ] 记录 `git rev-parse --short HEAD`、实际 build hash 与 `config.toml` 的 `web_dist`
- [ ] 保存失败页面、console、Network 和对应测试报告
- [ ] 核对 `/api` 404 仍是 JSON、缺失静态资源仍是 404、深链接刷新仍返回应用
- [ ] 保留 archive/state 原样；前端整理没有改变数据格式或写入契约

### 13.1 源码服务机一键改址

在开发机的功能工作树中双击 [scripts/update_service_address.bat](../scripts/update_service_address.bat)，或从仓库运行：

```powershell
scripts\update_service_address.bat
```

按提示输入服务机新 IPv4。同一允许网段内保留原来源范围与端口；跨网段时必须补充允许来源 CIDR，或直接输入现场确认过前缀的 `IP/前缀`。例如 **仅当服务机实际前缀为 24 且准备允许该网段时**，可输入 `10.66.6.3/24`。IP 不能用于推断掩码；在服务机用 `Get-NetIPAddress -InterfaceAlias WLAN -AddressFamily IPv4` 核对，网卡名按实际修改。

也可以传参数，先预览再写入：

```powershell
scripts\update_service_address.bat 10.66.6.3/24 --dry-run
scripts\update_service_address.bat 10.66.6.3/24
# 多个获准客户端网段用重复参数；不要与 IP/前缀混用。
scripts\update_service_address.bat 10.66.6.3 --allow-client-subnet 10.66.6.0/24 --allow-client-subnet 10.66.4.0/24
# 需要改端口时追加 --port 9876；默认保留原端口。
```

脚本校验完两份配置后更新 `ops/service-machine.network.json` 的监听、入口、端口、来源，以及 `config.toml` 中 `[feishu].base_url`；保留其他模型服务入口、注释、凭据和业务数据。相同值重复执行不写文件，配置损坏或输入错误时返回非零。普通写入失败会恢复本次已写的文件；这是开发机的配置修改，不是跨文件断电事务，仍需检查 diff 后再提交。脚本不自动提交、推送、修改 Windows IP、防火墙或受管 `host.json`。

检查 `git diff -- config.toml ops/service-machine.network.json`，提交并合并到 `main`，再让服务机更新。仅推送功能分支不会进入服务机拉取的 `main`。服务机按 [§13](#13-更新并启动审校台) 记录版本、保全本地改动，保存草稿并在业务空闲后正常退出 Web 与调度器；确认当前为 main 且工作区干净后执行：

```powershell
git status --short --branch
git pull --ff-only origin main
if ($LASTEXITCODE -ne 0) { throw 'Pull failed; stop before restart.' }
scripts\run_web_lan.bat
```

`run_web_lan.bat` 在当前源码目录读取网络 JSON，自动采用其中的端口；它继续调用 `run_web.bat` 安装并构建前端。无效 JSON、受管终端或运行包会停止启动。需要本机开发时使用普通 `run_web.bat`；局域网环境变量仅在子进程内生效。飞书入口的读取顺序为受管 `control/host.json` → 显式 `FBSCRAPER_NETWORK_CONFIG` → `[feishu].base_url`。调度器按改址前相同命令恢复，以载入新的入口；常驻 Runtime 保存启动时的设置，只重启 Web 不会刷新旧调度器。

**先区分新卡片与历史消息。** 新生成的审校、待审列表、采集异常、历史归档和运行详情按钮使用新入口。改址前已发出的飞书卡片，其 URL 已保存在飞书；本地配置、Git 拉取或修改发件箱副本均不能改写那条远端消息。当前群 webhook 没有可用于更新消息的 `message_id`，`bot-accepted:` 只是本地发送凭证。飞书的[更新卡片接口](https://open.feishu.cn/document/server-docs/im-v1/message-card/patch)需要真实消息 ID，不能将本地凭证传入。

需要打开旧卡片对应内容时，复制链接，将开头的旧 `http://IP:端口` 换成网络 JSON 中的 `public_base_url`，保留后面的路径、查询参数和帖子 ID；也可以从新入口进入历史或审校列表查找。已发送、已尝试或已冻结的投递记录继续保留，不清空发件箱、不将结果未知的通知自动重发。若重启后新生成的卡片仍带旧地址，核对进程实际代码目录、上述配置读取顺序与两份配置的值；不要根据历史卡片断言新配置未生效。

**防火墙是服务机本地状态，Git 拉取不能更新它。** 原规则若绑定旧 IP/端口/来源，首次启用或改址后，在服务机管理员 PowerShell 中核对公司认可的 Domain/Private 网络，更新专用规则。以下代码从同一 JSON 读取地址，不再手写三处 IP：

```powershell
# 在服务机仓库根目录执行；网卡名称按实际修改。
$sourceInterface = 'WLAN'
Get-NetIPAddress -InterfaceAlias $sourceInterface -AddressFamily IPv4
Get-NetConnectionProfile -InterfaceAlias $sourceInterface
# 核对 JSON 入口 IP 属于此网卡、前缀正确，网络为 DomainAuthenticated 或 Private 后继续。
$sourcePolicy = Get-Content -LiteralPath 'ops\service-machine.network.json' -Raw | ConvertFrom-Json
$sourceAddress = ([uri]$sourcePolicy.public_base_url).Host
$sourceRuleName = 'FBScraper-Source-LAN-Web'
$sourceRuleSettings = @{
    PolicyStore = 'PersistentStore'
    Direction = 'Inbound'
    Action = 'Allow'
    Enabled = 'True'
    Protocol = 'TCP'
    LocalPort = $sourcePolicy.web_port
    LocalAddress = $sourceAddress
    RemoteAddress = @($sourcePolicy.allowed_client_cidrs)
    InterfaceAlias = $sourceInterface
    Profile = @('Domain', 'Private')
}
$sourceRules = @(Get-NetFirewallRule -PolicyStore PersistentStore -ErrorAction Stop |
    Where-Object { $_.Name -eq $sourceRuleName })
if ($sourceRules.Count -gt 0) {
    Set-NetFirewallRule -Name $sourceRuleName @sourceRuleSettings
} else {
    New-NetFirewallRule -Name $sourceRuleName -DisplayName 'FBScraper Source LAN Web' @sourceRuleSettings
}
Get-NetTCPConnection -LocalPort $sourcePolicy.web_port -State Listen
```

监听应为 `0.0.0.0` 与配置端口。若网卡是 Public，按公司网络政策处理，不扩大规则到所有配置文件。已有更宽规则可能影响最终有效范围，按 §17.2 人工核对；这里只更新源码专用规则。受管安装继续使用 §17 的流程。

从获准客户端对配置入口 IP/端口执行 `Test-NetConnection`，然后打开 JSON 的 `public_base_url`，验证首页、历史、详情刷新、图片显示与下载、已授权样本保存后持久化，以及新生成飞书链接。TCP 不通检查实际地址、监听、防火墙和客户端网络；403 检查来源网段、Host 和 Origin；503 `access_config_invalid` 检查网络 JSON，不关闭来源校验。跨网段放行仍需公司路由支持。这些服务机及同事电脑验收均为 **待真实联调**。

## 14. 阶段一真实验收：监测与原帖抓取

本节是阶段一唯一的真实启用顺序。全程不加 `--process`，不触发模型、标签、审校唤醒、月历、云盘镜像或发布。不得自动登录或自动滚历史。

**整段在服务机上做**（装机顺序见[第 15 节](#15-服务机部署与日常更新)）。验收证据只对产生它的那台机器成立，开发机上跑过的不算数。

### A. 准备与访问初始化

⛔ **全程不要加 `--process`。** 它需要先过激活边界，而激活边界要阶段五的 G8 真机验收证据。阶段一的范围止于监测与抓取，翻译与图片是后面几轮的事。

1. 人工登录 9222（回填）、9224（探测）、9223（发布）三个独立 profile，核对身份。阶段一只让 9222/9224 访问源站。
2. 配置同群 detect、capture、publish、alert 四个机器人及运营要使用的 `[feishu].base_url`。base_url 只校验 URL 格式。
   → 2026-09-15 在**开发机**上收到过四个机器人的响应，只证明通道可达。这一轮改在服务机上运行，**这一条要在服务机上重做**，那次不算数。
3. 离线执行 `scripts\run_pipeline.bat preflight --json`、`scripts\run_python.bat -m routes.delta --status`、`scripts\run_scheduler.bat --preview`。`preflight --json` 与 `GET /api/runtime` 应显示 monitoring state/status/pause/quota/next_due/capture_revision/items/baselines；读取不访问平台或发消息。
4. 人工核对 9224 身份后初始化：
```powershell
scripts\run_python.bat -m routes.delta --initialize-access --reason "人工核对 profile 身份，允许阶段一受控探测"
```

缺初始化或坏状态时真实导航必须失败闭合。

### B. 人工回填与独立基线
人在 9222 分别打开 FB `neakasaofficial`、IG `neakasa.global`，手工滚到 30 天前：

```powershell
scripts\run_backfill.bat facebook --days 30
scripts\run_backfill.bat instagram --days 30
```

核对独立数量、最早/最新时间、实际 owner/coauthors、媒体顺序与完整性。视频、混合、无媒体、无正文也归档计数。historical 只汇总，不逐帖推群。

⚠️ **IG 的合作帖判据是 `parse.on_timeline_of()`，不是 `owner == account`。** 只比 owner 会把 `.global` 的帖子整批漏掉——这是 2026-09-11 真实发生过的，263 篇合作帖静默丢失。

确认覆盖后另行执行：
```powershell
scripts\run_python.bat -m routes.delta --initialize-baseline --reason "人工核对 FB/IG 最近 30 天回填完整"
```

访问初始化和基线初始化是两步；基线写 `capture_state.json.baselines` 与 monitoring enabled_at，不创建或改写发布激活边界。

### C. 预览、扫描与 72 小时试运行

预览核对每天含周末：08–19 点间隔 45–75 分钟，19–08 点 135–225 分钟；晨间 06:30–07:30 随机 2–4 屏；08:00 首轮在 08:00–08:15 且距上次主页至少 45 分钟。预览离线。随后先单轮核对，再启动常驻试运行：

```powershell
scripts\run_scheduler.bat --run --once
scripts\run_scheduler.bat --run
```

常驻命令保持运行至少 72 小时，不加 `--process`。

普通轮次只开主页首屏、不滚动，每平台最多 300 秒。所有 GraphQL/feed 候选先持久化再下载。仅来源媒体不完整者可打开一次匹配详情，不滚动、不翻轮播、不重放 GraphQL。真实浏览器 dry-run 也写停机和配额。

连续观察至少 72 小时，覆盖 08:00、19:00、晨间和周末；不足则延长。记录平台、计划/实际时刻、skip reason、主页/详情配额、pause/hard-stop、候选/归档/卡片。核对 scheduler、CLI、`--if-stale` 共用 `next_due`；19:00 不重抽；重启不追补；普通/晨间不足 45 分钟合并；锁忙（退出码 75）不推进；统计不改变间隔。

主页不超过 24 次/平台/滚动 24 小时；详情不超过 1 次/帖/扫描、3 次/平台/扫描、12 次/平台/滚动 24 小时。401/403/429 或登录/checkpoint/challenge 立即全 profile 停机且不再导航、滚动、下载；CDN 过期 403 仅单项失败，CDN 429 全局停机；三次普通失败暂停平台。已开始采集后失败、超额、超时待人工，普通扫描不重试；尚未开始项仅延期，见下节。

### D. 核对事实与卡片

**夜间监测故障修复后的复验。** 在服务机停止旧调度器，先核对 `git status --short --branch`、
`git rev-parse --short HEAD` 与 `tools.runtime status` 的实际归档/状态绑定，再更新到含本次修复的版本并重启。
保留已有基线、访问配额、采集账本与原始 capture；不重新初始化基线或批量改写人工项。
按持久 `next_due` 执行上节单轮命令，普通轮次仍只读首屏；晨间轮次等原定 06:30–07:30 窗口，不强制补跑。

单轮后执行 `scripts\run_python.bat -m routes.delta --status`，保留输出及对应 `_capture_delta_*.json`：

- Facebook：`last_scan_diagnostics` 中比较 `initial_response_payloads`、`embedded_payloads`、
  `response_payloads`、`timeline_seen` 与 `page_seconds`，确认普通首屏取得目标帖子。
  若仍只有辅助响应，准确记录“未取得目标主页帖子时间线数据”；不要增加滚动深度或反复手动访问。
- 晨扫：核对 `prepare_seconds`、`capture_seconds`、`capture_started/finished`、`deferred` 与
  `outside_baseline`。整轮仍为 90 秒；基线起点来自服务机已有 `enabled_at - lookback_days`，
  不使用开发机重放示例的“22/35”作为服务机固定预期。
- `deferred_items` 是尚未开始，等待下次自然响应，不是图片下载失败。`manual_items` 是需核验的已尝试项；
  旧记录不会批量重置。完整同帖证据与本地原图匹配时可离线补齐，否则沿用下节一次人工恢复。
- Facebook `122127190695379375` 可在后续自然响应再次提供完整来源时核验日期/完整性；
  图片仍须与本地实际字节匹配。另两篇旧缺日期帖没有本次成功样本证据，不据此声称已恢复。

开发机的真实 capture 重放与隔离浏览器均属于离线验证；服务机首屏数据到达路径、正常晨扫耗时及持续运行仍待真实联调。

核对类别 new/historical/source_updated/recovered/time_unknown；原文、元数据、owner/coauthors、`items[key].source.media` 和媒体线索齐全。`source_media_complete` 与 `media_complete` 分开；后者要求每张静态图全图解码、SHA 和原子落盘。IG 重复封面/视频缩略图不计图片；每媒体一个尺寸、顺序不变；未知总数明确 unknown。revision 只由正文与有序实际媒体 SHA 改变。

detect 每平台/扫描至多一张摘要，零新增不发；分类计数区分新发布、历史补获、更新和恢复，多篇展示最新发帖及对应监测时间。capture 对每个合格候选恰好一张卡，包含完成/部分完成/失败、平台账号、合作归属、发帖与抓取时间、英文前 150 字符及超长省略号、实际验证图片数/已知总数、异常原因和下一步。卡片时间按配置格式化且不附时区字样。主按钮按归档状态去 `/history/{account_dir}/{post_id}` 或 `/runtime?capture={key}`，次按钮到源帖。卡片无缩略图，不声称进入本地化队列。

消息异步入 durable outbox，不阻塞下载；重启补入已持久候选漏掉的意图。未知结果人工核对，不自动重发。FB 多图顺序、IG carousel 顺序、四机器人真实发送者/链接、自然新帖分别留真实证据；无自然新帖时平台捕获仍待真实联调。

### E. CAS 恢复与证据

先读取 revision，人工核对后执行：

```powershell
scripts\run_python.bat -m routes.delta --recover-access --expected-revision <N> --reason "具体核对原因"
scripts\run_python.bat -m routes.delta --recover-post PLATFORM:ACCOUNT:POST_ID --expected-revision <N> --reason "具体失败原因"
```

访问恢复不清配额/历史；单帖恢复只尝试一次并服从同一护栏。Web `POST /api/runtime/capture/recover` 含 key/version/reason。`monitor/recover` 含 version/reason 与可选 platform，且不访问平台。版本冲突先重读。

**人工队列里混着 `23c719d` 之前留下的陈旧项。** 那一版的 `interrupt()` 把当轮**尚未开始**的候选也打成 `manual`，而 `begin()` 永远跳过 `manual`，于是它们既不会被自动重试、也不会自己消失——服务机 2026-09-21 的 37 条里有 33 条是这一类。现在的代码已经把未开始的记成 `deferred`（会自愈），只差把旧账清掉：

```powershell
scripts\run_python.bat -m routes.delta --retire-stale-manual --expected-revision <N> --reason "23c719d 之前的漏判"
```

只退役「没有 `attempt_started_at` 且不是人工恢复授权」的项，判据与现在的 `interrupt()` 完全一致；⛔ **真正尝试过并失败的项一条都不动**，投递事件账本也不改写。退役后状态变 `deferred`，命令会同时列出退役清单和保留的真实失败项。

⚠️ 这一步**不产生任何平台请求**：基线外的旧帖在候选阶段就被 `outside_baseline` 拦下，已归档且无变化的帖 `should_append()` 为假。真要重新采集，仍然只能逐帖走上面的 `--recover-post`。

本地落点也要核（云盘本轮不做，没有可比对的远端）：

- [ ] `archive/` 下出现对应帖子文件夹，落在正确的月份和 tag 下
- [ ] 文件夹名是四段：`<北京日期_时分>_<IG|FB>_<产品>_<摘要>`，没有看不懂的数字尾巴。
      没匹配上型号表的帖子没有产品段，落在 `未分类/` 下；没正文的帖子没有摘要段
- [ ] 名字里的日期时分是**原帖发布的北京时间**，和卡片上那一行对得上

报告保存命令、版本、真实/夹具来源、平台/账号、访问与基线 revision、计划/实际时刻、配额、类别、三个时间、owner/coauthors、两层 completeness、图片 SHA/顺序、delivery 状态和未联调项。离线夹具、无自然新帖、HTTP 接受或已删除的旧 429 记录不能写成真实通过。

### 这一轮能证明什么、不能证明什么

| 做完之后成立 | **仍然不成立** |
|---|---|
| 监测能发现真实新帖并在同一轮抓取归档 | 翻译、图片德语化、审校、排期任何一段 |
| 本地三层布局与业务可读的文件夹名 | 云盘镜像——本轮明确延期，没有任何远端证据 |
| 共享访问预算、硬停机与配额在真实往返里成立 | 长期稳定性——封号风险的反馈是延迟的，且只反馈一次 |
| 同群四个机器人分工正确、逐帖卡链接可点达 | 运营完整流程（她从卡片进去审完再排期） |
| 详情补齐与媒体校验在真实响应上成立 | 晨间轮次真的补到过漏帖（要等一次真实漏帖） |

验收报告里写清楚：跑了什么、哪几条是真实账号的结果、哪些外部依赖仍未联调。离线测试通过不能替代上面任何一项。

### Facebook 误拒记录的核验与恢复

出现 `owner=id:<数字>`、`expected_owner=neakasaofficial` 的 `owner_mismatch` 时，先保留本次
`_capture_*.json`、`_rejected.jsonl` 和全部既有归档。捕获文件开头常是界面配置或主页资料，
不等于帖子正文；核验单帖需找到其 `post_id` 对应的 story，连同 `actors`、`attachments`、
`creation_time`、`url/permalink_url` 及账号身份对象一起检查。

更新到包含身份修复的版本后，可在服务机已绑定正确数据的运行目录执行以下只读预览，替换捕获路径：

```powershell
scripts\run_python.bat -m tools.replay facebook --capture "D:\capture-copy\_capture_TIMESTAMP.json" --dry-run
```

保留 `--dry-run`。预览会使用新的作者解析规则，显示保留/拒绝与图片/视频数；
它不下载图片，也不证明图片已落盘。非 dry-run 的 replay 会重建索引并移动未关联目录，
不能作为这次误拒的一键补入命令。

核对数字 ID 与用户名是否由同一作者/主页对象或 `ProfileActionMessage` 明确关联，确认没有
`owner_conflict`，再检查目标日期范围内的逐帖图片数量、顺序与 URL。若需要从完整 capture 补入，
先在完整数据副本制定仅补缺失帖/原图的清单，保留现有人工稿、审校和发布账本；当前没有自动追加恢复命令。
若原始响应不足或图片 URL 失效，由人在 9222 按本节 B 的同一日期窗口重新回填；修复后的回填使用已有
`should_append`/归档保护补入帖子，不删除拒绝历史，不自动重建监测或发布激活边界。

最终以 `post.json` 的规范化 `owner`、`owner_evidence`、媒体图序、实际文件及 SHA 核验恢复结果。

若作者已经正确、目录仍只有 `post.json` 与 `text.txt`，先看 `media`：空数组表示下载器没有收到媒体项，
不是图片下载失败；有图片项但 `local_path` 为空才继续核对该帖的下载错误。
从同次捕获中保留目标 `post_id` 的全部 story 片段及其 `attachments`、`comet_sections`，不要用主页照片栏替代。
缺附件字段应解析为来源不完整、数量未知；已知图片不应被空片段覆盖。
修复版本可在重新人工回填取得图片后补入旧空归档，无须先删目录；只更新代码不会产生缺失的原图。

已确认的 Comet 缺图结构：附件外层 `media` 只有 Photo ID，实际单图位于
`styles.attachment.media.photo_image`，相册位于 `styles.attachment.all_subattachments.nodes`。
预览应显示相册原顺序及真实项数，不能把外层封面重复计入；仅有宽高、没有 `uri` 的 `viewer_image`
不是可下载地址。2026-09-18 用户捕获文件含 21 帖、23 图片、5 视频；按该次捕获时刻的 30 天窗口为
12 帖、13 图片，其余帖子在窗口外。这些数量仅用于核对该文件，不是每次回填的固定期望值。
视频的 `kind=video` 且 `local_path=null` 属于设计行为；真实原帖含视频但 `media=[]` 才需另查媒体结构。
完整响应、原图补入与服务机核验未完成前，身份修复只记“离线通过”。

## 14.1 打开内容处理：抓到就翻译和出图

阶段一固定不带 `--process`。这一节是把它打开的顺序。**它是本轮第一次让非开发者触发付费调用**，
所以前面每一条都是硬前置，缺一条就停在那里。

### A. 四条硬前置

| 前置 | 怎么确认 | 缺了会怎样 |
|---|---|---|
| 阶段一真实验收已通过 | 第 14 节走完，归档里有真实新帖 | 归档是空的，没有东西可翻 |
| 激活边界已设 | [第 10 节](#10-激活边界) | `Runtime` 直接抛「流水线尚未激活」 |
| 本机 `.env` 有 `DEEPSEEK_API_KEY` 与 `IMAGE_API_KEY` | [第 2 节](#2-配置本机凭据) | 失败闭合，不会制造"已向远端发出"的假账 |
| `[pipeline]` 的日/月预算是业务认可的数 | 当前 `monthly_budget_usd = 60`、`daily_budget_usd = 5` | 超预算停止，中途停在一半 |

### B. 先看清楚这一轮要花什么

三条都是**只读**的：零网络、零费用、零写盘。业务点下去之前能看到的就是这些。

```powershell
scripts\run_pipeline.bat processing-preview
```

逐帖列出平台、账号、`source_ref`、文案动作、待生成图片索引/张数、排除或阻塞理由及美元参考。
复用自动处理的激活边界、来源许可、人工稿、审校/发布状态和图片规划；没有译文的新帖也会列出翻译成功后要生成的图片。
已有人工图保留；已有程序图因尚未生成的新译文而可能失效时，显示数量区间并单列待核对索引，不当作必然重做。
可加 `--account fa_neakasaofficial` 或 `--account in_neakasa.global` 限定平台账号，预算仍核算全账号；`--json` 可保存预览快照供核对。

```powershell
scripts\run_pipeline.bat status
```

逐账号显示归档与各阶段积压。**积压不是本次处理作用域**：历史帖、未获许可的合作帖或人工保留项都可能在积压中；确认付费范围以上面的 `processing-preview` 为准。

```powershell
scripts\run_scheduler.bat --preview
```

只看下次到期时刻，不访问浏览器。处理预览只读取已经落地的归档，不能预知下次真实扫描新发现的帖子；新帖落地后应重看处理预览。

美元参考小计包括文案基础估值与图片历史估值。文案按当前提示词/正文粗估 token、德语可见输出为英文的 1.25 倍、缓存全未命中计算，**不含 thinking 的 reasoning 输出**；英文风险预扫是额外付费请求，费用未知，单列次数且不计入小计。
`gpt-image-2` 图片沿用 2026-09-11 的 US$0.211/张历史参考（当时约 2.5 分钟/张），**不是当前保证、完整账单或预算上界**。
切换到 `gpt-image-2.5-flare` 或 `gpt-image-2.5-sunburst` 时预览不沿用这个单价，待出图费用与参考小计显示未知；模型真实费率仍按 §5.1 核验。
日/月预算及已有费用另行展示，实际请求逐次核账，预算或失败可使批次提前停止。
预览不验证凭据、模型、浏览器或外部服务；真实跑完之后用当前账单和耗时补充真实联调证据。

### C. 打开

```powershell
scripts\run_scheduler.bat --run --process --once
```

`--process` 必须与 `--run` 一起给。`--once` 只扫描当前到期的一轮，并等待已启动的翻译/出图批次完成，
再汇总待审或失败通知、等待当轮投递结束后退出；不会为等出图再扫描一轮。离岗静默规则仍生效，
应静默的卡片保留到下一次在岗投递。首次验收先用单轮模式，确认结果后再常驻。

扫描发现内容后，同一轮里依次做：英文风险预扫描 → 翻译 → 逐张出图 → 进审校队列 → 发布机器人推待审卡。
每个付费请求后按刚落盘的 usage 重算预算；算不出费用就停。

已完整入库的图文按逐帖采集结果进入处理队列：同轮其它帖抓取失败不阻止它，人工恢复成功后也会在下一次
维护时入队。只有启用 `--process`、激活之后且属于当前账号的来源进入此路径；不完整素材仍交人处理。
入队回执与批次原子保存，飞书确认、重复维护或进程重启不会重新受理同一结果；不确定付费批次仍须先核账。
恢复操作只结转旧批次，不隐含启动付费任务。中断期间已入队的后继结果保留，等待新的处理请求；
重复读取同一采集结果不算新请求。

### D. 通过判据

| 做完之后成立 | **仍然不成立** |
|---|---|
| 真实新帖在同一轮里产出德语文案与德语图 | 文案质量——那要业务看过"出片"效果才知道 |
| 付费账本有 request ID、用量和费用，且对得上供应商账单 | 长期费用曲线——一轮不代表一个月 |
| 处理失败时已生成的内容保留，不重复扣费 | 中断恢复——要真的中断过一次才算验过 |

⛔ **付费调用不可撤销。** 第一次真实跑之前先用 B 段的三条确认作用域；不确定就先别跑。

## 15. 服务机部署与日常更新

**当前服务机直接运行源码，日常操作见 [§13](#13-更新并启动审校台)，局域网改址见 [§13.1](#131-源码服务机一键改址)。** 开发机负责代码与按影响选择的隔离验证，服务机继续使用已有数据和已授权模式。

**Actions 自动制品交付明确延期（2026-09-22 用户决定）。** 工作流移除后不再生成新运行包；§15.2–§15.10 保留原受管安装、维护与恢复说明，供已有实例维护或以后恢复该能力时参考，不是当前源码部署的操作清单。不寻找新的 Actions 制品、不注册部署控制器任务；既有实例需要继续运行旧版时用 §15.8 的 `pause` 暂停发现更新。原离线证据保留，见 [REQUIREMENTS §10.7](REQUIREMENTS.md#107-服务机自动部署)。

### 15.1 服务机与账户

源码服务机需要 Windows x64、Python、系统 Chrome、Git 和 Node.js/npm；沿用已验证的 Python **3.12.9**、Node **24.12.0** 工具链，不因停用 CI 更换版本。源码拉取需要访问 GitHub，前端依赖安装需要访问所配置的 npm 源；Python 依赖变化时按 §13 更新。机器保持接电、不休眠及足够磁盘空间。旧运行包自带前端及 wheel，因此其恢复过程不需要 Node 或在线安装依赖；这个条件不适用于源码启动。

所有业务进程和三个 Chrome profile 都由**运营账户**运行。当前源码调度沿用原有启动方式和已授权模式，管理入口见 §11；不额外安装第二套任务。旧控制器任务使用 `InteractiveToken`、最小权限和登录触发；Windows 重启后尚无人登录时不保证业务运行。技术账户不能替代运营账户的 Chrome 会话。

### 15.2 首次安装已验证制品

本节首次安装随自动制品交付明确延期，当前服务机不执行。旧受管实例恢复使用已保留且可核对来源的历史包：仓库 `marcus-ao/FacebookScraper`、原提交 SHA、制品身份和哈希须一致。历史 `fbscraper-windows` 包解压后的根目录直接包含 `release.json`；不要从来源不明的 ZIP 启动安装程序，也不要把旧包标成当前 main 的新版本。

以下路径仅为示例，在运营账户的 PowerShell 中执行：

```powershell
Set-Location D:\Downloads\fbscraper-windows
py -3.12 -m deployment install --root D:\FacebookScraperService --release D:\Downloads\fbscraper-windows
```

安装器检查 Python 版本、包哈希、前后端指纹和旧计划任务冲突，在**最终目录**创建两个独立虚拟环境，并从包内 wheel 安装。临时业务数据上的 Web 验证成功才完成安装。目标必须为空；安装器不会覆盖已有业务目录。失败时保留现场，查明原因后处理安装目录，不能把未完成安装误当成可运行实例。

```text
D:\FacebookScraperService\
  controller\              固定控制器与独立 .venv
  releases\<sha>\          程序、dist、.venv、config.local.toml
  shared\archive\          原帖、原图、人工稿与人工图片
  shared\state\            发布/付费/审校及运行状态
  shared\.env              业务凭据
  shared\instance.json     业务实例标识
  control\                 部署记录、维护会话和 GitHub 凭据
  logs\                    每个受管进程的日志
```

各版本由安装器生成绝对路径绑定；Web、调度器和受管 CLI 使用同一 `shared`。缺失绑定或实例标识不一致时拒绝启动，不回落到版本内的空数据。不得手改标识掩盖目录错误。

### 15.3 配置凭据与初始数据

按 `.env.example` 填写 `shared\.env`。GitHub 的仓库范围细粒度凭据只需 `Actions: read`、`Contents: read`，写入 `control\github.token` 一行；不放进业务 `.env`、Git、日志或聊天。安装器为 token 文件设置运营账户 ACL；按组织政策保管和轮换。认证失败时保留当前服务。

默认新建空业务实例，调度与付费处理均关闭。开发机和服务机各自的测试数据不能混入生产账本。若要接续已经存在的真实业务数据，先停止其写入，按[第 1 节](#1-接续运行数据前先备份和核验)完整备份核验，再由技术人员在新实例首次启动前接续完整 `archive/state` 与必要凭据；核对全部路径及实例归属。不得只搬部分 JSONL，不能覆盖已有运行中的 `shared`，也不自动合并两份账本。

网络参数保存在 `control\host.json`：`web_host`、`web_port`、`public_base_url`、`allowed_client_cidrs`。受管通知的审校台地址以这里为准；独立开发仍读取 `[feishu].base_url`。首次局域网安装见第 17 节。

两个运营偏好：默认排期时刻（业务时区，当前是北京）与挂起工作日数。2026-09-23 起界面不再提供设置页；维护者改默认值时，开发检出直接改 `config.toml`（`publish.schedule_rule.times`、`review.snooze_default_days`），服务机改 `shared\state\operator_preferences.json`（只含这两个键的 JSON，例如 `{"default_times": ["16:00", "23:00"], "snooze_default_days": 3}`），或经 `PUT /api/settings`（带版本 CAS）。其余配置、提示词和业务规则由已验证版本交付；不要在服务机直接改 `releases\<sha>\config.toml`。

### 15.4 注册控制器与首次启动

先处理旧 `FBScraperScheduler`、`FBScraperDelta`、`FBScraperDeltaCatchup` 任务。确认旧业务空闲并正常退出后，人工停用旧启动机制；不使用 `schtasks /End` 中断长任务。安装和启动控制器遇到启用的旧任务会拒绝继续。

```powershell
Set-Location D:\FacebookScraperService\controller
.venv\Scripts\python.exe -m deployment install-task --root D:\FacebookScraperService --dry-run
.venv\Scripts\python.exe -m deployment install-task --root D:\FacebookScraperService
.venv\Scripts\python.exe -m deployment supervise --root D:\FacebookScraperService
```

核对 XML 的运营账户、`InteractiveToken`、`LeastPrivilege`、固定 controller 工作目录、单实例规则。最后一条可用于前台观察；计划任务与手动重复启动受到控制器锁保护。首次成功启动只开放 Web，默认不开启真实调度或 `--process`。

访问本机审校台及 `/api/health`，核对 `deployment_ready=true`、运行 SHA、前后端指纹和实例 ID。控制器自身及 CLI 的入口始终在 `controller`，不要跳到某个旧版本目录运行。

### 15.5 受管业务命令与人工登录

既有手册中的 Python 命令通过控制器 `exec` 执行，例如：

```powershell
.venv\Scripts\python.exe -m deployment exec --root D:\FacebookScraperService -- -m pipeline preflight --json
.venv\Scripts\python.exe -m deployment exec --root D:\FacebookScraperService -- -m tools.start_chrome_detect
.venv\Scripts\python.exe -m deployment exec --root D:\FacebookScraperService -- -m tools.start_chrome
.venv\Scripts\python.exe -m deployment exec --root D:\FacebookScraperService -- -m tools.start_chrome_publish
```

`exec` 使用实际当前版本、同一业务绑定，并将完整命令生命周期登记为在途工作。三个 Chrome 仍须人工登录，不搬开发机 profile、不混用 9222/9223/9224。Chrome 不属于更新时退出的进程树。`preflight` 返回 0 只表示状态查询完成，仍须逐项看业务前置。

飞书自检、访问初始化、真实采集、模型和发布照各节的人工授权要求进行。例如 `exec ... -- -m pipeline notifications --self-test` 会真实发送消息，不属于部署健康检查。

### 15.6 授权调度与内容处理

完成[阶段一验收](#14-阶段一真实验收监测与原帖抓取)后，显式设置模式：

```powershell
.venv\Scripts\python.exe -m deployment mode --root D:\FacebookScraperService --scheduler on
```

只有另行满足[第 14.1 节](#141-打开内容处理抓到就翻译和出图)的付费许可、预算和激活要求后，才执行：

```powershell
.venv\Scripts\python.exe -m deployment mode --root D:\FacebookScraperService --scheduler on --process on
```

模式变化同样等待空闲并经过维护切换；控制器不会随代码更新自动加上 `--process`。关闭处理可用 `--process off`；关闭调度同时指定 `--scheduler off --process off`。保留模式指令与实际运行模式分别展示，失败模式须核对后显式重试。

### 15.7 日常自动更新

本项明确延期。以下是保留的原控制器行为：每 60 秒核对当前 main 的成功 push 工作流与制品身份；删除工作流后不会产生新候选。旧实例按 §15.8 暂停发现更新，源码服务机按 §13 手动更新。

原流程的下载、哈希校验、依赖安装和隔离预检发生在旧版本运行期间。候选准备好后等待后台工作及编辑结束，页面预告 60 秒；运营可点击“延后 30 分钟”。持续等待超过 30 分钟通知一次。

未保存草稿、上传和正在执行或排队的任务都会阻止切换。失联的脏会话不会因超时自动忽略；先恢复原标签页保存或明确放弃内容。确认原编辑确已不存在后，技术人员才能用 `clear-session` 解除其阻塞。

维护期间旧进程自行退出，新版本使用同一数据启动；部署验证最长默认 90 秒，检查通过才恢复业务。正常切换约 1–2 分钟是目标，须在服务机实测；长模型请求没有强杀截止。旧页面的写请求会收到明确版本冲突并保留草稿。

只改文档/测试且运行指纹相同的提交记录为已检查，无需重启；“实际运行 SHA”与“已检查 SHA”不能混写。网络错误、CI 失败、过期包或摘要不符保留当前服务；失败候选不循环安装，须新版本或显式 `retry`。

开发变更若涉及真相源格式，必须同步改变发布包兼容声明，交由技术维护进行备份、迁移与验收；不能保留旧 `truth_contract` 来让迁移混入自动更新。固定控制器及其依赖代码指纹变化也会拒绝自动切换。单纯兼容的派生索引变化仍需离线重建验证。

### 15.8 状态、暂停、重试与回退

```powershell
.venv\Scripts\python.exe -m deployment status --root D:\FacebookScraperService
.venv\Scripts\python.exe -m deployment pause --root D:\FacebookScraperService
.venv\Scripts\python.exe -m deployment resume --root D:\FacebookScraperService
.venv\Scripts\python.exe -m deployment retry --root D:\FacebookScraperService
.venv\Scripts\python.exe -m deployment rollback --root D:\FacebookScraperService --sha <已保留的完整成功SHA>
.venv\Scripts\python.exe -m deployment clear-session --root D:\FacebookScraperService --session <已核对的会话ID>
```

`pause` 暂停发现和安装自动更新，保留业务进程；退出前台控制器也不承诺 Web 已退出。旧实例停机先保存或放弃草稿、关闭标签页，再由技术人员通过维护 Gate 关闭接单、向已登记进程请求退出并核对 PID/创建时间，不靠删除目录解除阻塞。`rollback` 仍经过空闲协调；不能直接杀掉可能正在提交的业务。旧代码、环境和版本配置保留在本机，恢复不依赖联网。

自动回退**不恢复旧业务数据**：发布防重、付费记录、人工稿、审校决定、激活边界和访问额度始终使用最新共享数据。真相源格式变化或需要新控制器的版本交技术维护。控制器切换中断时读取持久记录核对进程；无法确认身份时保持阻塞，不能按窗口标题结束进程。

已启用的新版本若进程退出，恢复旧版后仍会阻止故障 SHA 自动重装；不会在“新版故障—回退—再次安装”之间循环。普通 Windows/控制器重启则恢复最后已确认的版本和模式，不擅自退到更早版本。

### 15.9 故障与通知

运行页展示实际版本、候选、阶段、阻塞与最近结果；详细进程日志在 `logs`，部署事务在 `control\deployment.json`，维护与会话在 `control\maintenance.json`。不要删除这些记录来“解锁”。端口被无关进程占用时不会结束它。

飞书 `alert` 机器人通知完成、失败、回退及长期等待，并链接审校台。部署通知使用独立发件箱，发送失败或结果不确定不改变已经提交的部署结果，也不自动重放不确定通知。受管卡片链接使用控制目录里的 `public_base_url`，与运行页显示的“审校台入口”一致；手机不在已允许办公网络中时不属于本轮可达性承诺。

GitHub 构建成功不代表服务机已更新。缺少 Chrome 登录、业务未激活或历史费用待核对属于业务状态；部署检查不自动抓取、付费、自检飞书或排期。

### 15.10 受控验收与持续运行

以下受管部署验收明确延期，不是当前交付条件；旧证据及演练工具保留。

开发机可先执行版本化本机演练（Python 3.12.9、现有前端依赖及已下载的锁定 wheel）：

```powershell
scripts\run_python.bat tests\windows_deployment_rehearsal.py --wheelhouse state\release-wheelhouse --out state\deployment-implementation\windows-rehearsal
```

`--out` 必须是新目录。演练复制源码构建两个明确标为夹具的版本，在最终路径创建虚拟环境，启动真实本机 Web 进程，验证更新、健康失败回退和控制器重建后的恢复；最后按进程身份协作退出并保留日志、文件哈希与报告。远端版本、通知与时间推进为替身，调度和付费始终关闭，不注册计划任务、不使用 Chrome 业务 profile。这不能代替下一步的 GitHub 和服务机验收。

先用另一套**隔离实例、空凭据与假外部服务**演练：真实 GitHub 制品获取、成功切换、错误版本/绑定拒绝、候选启动失败回退、控制器在停止/启动/验证阶段中断后恢复、端口冲突、失联草稿、多标签页及旧页面提交。逐项记录实际 SHA、实例 ID、进程创建时间、切换耗时、前后数据哈希和通知状态。

再在业务服务机核对已有人工登录、各阶段验收、共享数据、已授权模式及运营体验。部署演练只能升级部署能力证据；不能升级抓取、模型账单或 Business Suite 发布证据。

每日核对审校台 `/runtime`、部署状态与调度实际心跳；机器无人登录时不报“业务正常”。本地告警不能发现本机断电，既有外部心跳仍按配置单独验收。

### 15.11 备份与旧手工流程

当前源码服务机按 §13 手动更新；`setup.bat` 用于安装，不是日常更新的固定步骤。首次接续、数据迁移前完整备份核验；日常按第 1 节外拷实际绑定的 archive/state 及受保护凭据，备份覆盖人工图片与设置。旧受管实例对应 `shared/archive`、`shared/state`。普通兼容更新不逐次复制整套图库，保留旧代码只提供代码回退能力。云盘镜像本轮延期；首次真实发布后每天异机备份，不能把制品保留期当作数据备份。

上述源码流程不能直接用于旧受管实例：其版本目录由控制器管理，不 stash、不原地 pull、不复制旧虚拟环境，不同时安装第二套调度计划任务；维护与代码回退使用 §15.8。

## 16. 阶段三真实验收：冻结、排期与自动发布

2026-09-16 完成的是**代码侧**：冻结与提交拆成两步、业务时区改北京、提交过程可轮询、
月历叠加本地图层、到点核实、撤销登记。全部只有离线证据。要真的排出一条帖子，下面四步
按顺序做，**顺序不能颠倒**。

### 16.1 检查现有发布能力

G1 已接受现有 2026-09-20 录制，按[第 8 节](#8-录制单渠道-business-suite-证据)保全文件即可，
无需再测 UI 边界或补录 IG 共通详情。运行只读预检：

```powershell
scripts\run_pipeline.bat preflight
```

分别查看 `submission`、`readback`、渠道控件、`calendar_coverage` 和 G8 真机回执。
预检仍覆盖双渠道；单渠道执行只核对公共能力和对应渠道。图片数量提示为“由实际发布界面校验”时无需为数量上限补录证。合作方单篇处理请在审校台确认该帖的来源与付费加工许可，不必先扩大全局可信作者名单。
后面几项不由 G1 的结构回查代替；缺项按实际能力处理，不能再要求重做已接受的录制。
这些预检结果不再挡住审校台的单篇排期。内容合格就可以冻结并选择时刻。提交时沿用第 8 节已有渠道记录中的资产绑定，并核对当前配置的目标账号与发布 Chrome；无需另填资产 ID。只有绑定缺失或与当前账号、发布浏览器不符时，才按第 8 节核对对应渠道记录。缺绑定仍可冻结和选时刻，提交会给出原因。首次测试不要求另一渠道已经成功，也不要求流水线已经激活。G8 和远端图片核验仍按原标准另验。

**更新无需增加配置或迁移渠道记录。** 单篇提交、确认预览和人工撤销核验共用既有资产绑定；普通月历刷新继续使用原录证入口。按 [§13](#13-更新并启动审校台) 的启动脚本构建并使用同版本前端。旧任务和快照按原指纹版本读取，不批量重写账本；受管实例与回退的版本约束见 [§5.3](#53-业务自己换图)。

### 16.2 回填出一篇能发的稿

开发机归档为空，服务机以其实际绑定目录为准，优先检查已有稿件。缺少合格来源时才按[第 4 节](#4-为当前目标建立归档)和
[第 14 节](#14-阶段一真实验收监测与原帖抓取)回填，直到至少有一篇**有正文、媒体全是静态图、
每张都有有效德语图或已明确确认使用当前原图**的帖子。未确认原图且缺德语图时不能冻结，
界面会写明缺第几张；确认原图的操作见 [§5.3](#53-业务自己换图)。

### 16.3 走一遍完整的人工流程

按[第 9 节](#9-真实发布前的最终确认)准备 FB-only 和 IG-only 各一篇，逐项给业务确认。
确认之后在审校台上依次做：

1. 点「编辑确认无误」，确认正文与图片被锁住、编辑入口都关掉了；
2. 选北京时刻，核对联调确认材料另行列出的柏林当地时刻；当前界面不再并排显示柏林时刻；
3. 点「确认发布时间并排期」，在当前页弹出的确认窗口核对账号、渠道、时间、完整文案及每张图片。多图可点缩略图或左右箭头切换；需要返回时点「继续核对」，已选时间会保留。确认无误后点「确认并创建排期」，**盯着进度走完七步**——提交已被接受后可以关页面再回来看；
4. 回读成功之后核对月历上远端卡片出现、飞书 publish 机器人收到回执。

记下：实际耗时、进度停在哪一步最久、远端 remote ID、飞书卡片的实际发送者。

### 16.3.1 发布 Chrome 窗口消失或 9223 断开

先看该任务的发布回执。`failed_pre_submit` 且没有提交意图时，本次未点击提交；`submit_ambiguous`、`submitted_unverified` 或 `scheduled` 都不能靠再次创建排期来验证浏览器。保留截图和时刻，不删除发布账本或 profile，不同时重复点击创建。

1. 按 §13 更新源码并重启 Web。本次 Python 修复不新增配置或依赖，既有账号、渠道资产和快照沿用。可用 `scripts\run_python.bat -m tools.test_offline --only tests_publish_browser_lifecycle --only tests_final_form --only tests_manual_schedule` 做隔离检查；浏览器测试使用已安装的 Chrome 程序创建无登录临时会话，不连接业务 9223，不依赖 Playwright 额外下载的浏览器。
2. 在服务机执行 `Get-NetTCPConnection -LocalPort 9223 -State Listen -ErrorAction SilentlyContinue | Select-Object LocalPort,OwningProcess`。无输出时运行 `scripts\start_chrome_publish.bat`；有输出时复用该窗口，人工核对 DE 发布身份。旧失败留下的编辑器可人工检查后关闭，至少保留一个普通标签页；程序不清理已有人工标签页。
3. 本次记录明确提交前失败后，回审校台点「编辑确认无误」，检查当前冻结正文、图片、唯一渠道、账号和选定时间。确认弹窗中的实际内容无误后只创建一次。单篇核对应只展开相关日期；独立月历同步仍会逐条读整月。提交结果不确定时核对原远端记录，不再提交。
4. 若窗口仍消失，先保存当前操作记录和错误截图，再查下面的只读信息。记录是整个窗口消失还是某标签页报错、发生时刻、是否手工重新启动过 Chrome。没有事件不等于没有发生退出；事后空闲内存不能证明故障瞬间的内存情况。

旧版 `tests_final_form` 若全部在 `asyncSetUp` 报 `BrowserType.launch: Executable doesn't exist ... chromium_headless_shell`，更新后重跑上述三项即可。这是测试误依赖下载浏览器及失败清理遗漏，尚未进入表单业务断言；后续 `Event loop is closed` 不要求修改 Windows 事件循环、重装 Python 或重建发布 profile。本项仅改测试与日志展示，不需要为它重启 Web。

```powershell
Set-Location 'D:\Code\FacebookScraper'
git rev-parse --short HEAD
.\scripts\run_python.bat -c "import importlib.metadata as m; from core.config import cfg; print('playwright=' + m.version('playwright')); print('chrome=' + cfg().chrome_exe)"
Get-NetTCPConnection -LocalPort 9223 -State Listen -ErrorAction SilentlyContinue | Select-Object LocalPort,OwningProcess
Get-CimInstance Win32_OperatingSystem | Select-Object TotalVisibleMemorySize,FreePhysicalMemory,TotalVirtualMemorySize,FreeVirtualMemory
$publishEventSince = (Get-Date).AddHours(-4)
Get-WinEvent -FilterHashtable @{LogName='Application'; Id=1000,1001; StartTime=$publishEventSince} -ErrorAction SilentlyContinue | Where-Object { $_.Message -match 'chrome.exe' } | Select-Object -First 6 TimeCreated,Id,Message | Format-List
Get-WinEvent -FilterHashtable @{LogName='System'; Id=2004; StartTime=$publishEventSince} -ErrorAction SilentlyContinue | Select-Object -First 3 TimeCreated,Id,Message | Format-List
```

代码按控件就绪状态等待，已公开详情释放后增加 1 秒间隔并限制响应读取并发；这些措施用于减少资源占用，不能保证平台不会触发账号检查。无直接证据时不先关闭 GPU、重建登录 profile 或更改浏览器安全参数。

### 16.4 排期详情的图片控件证据

**FB/IG 完整有序图片适配为代码未完成；目标诊断采集、比较和只读补验为离线通过。** 当前采集器只保存目标 dialog 内的有界图片候选、加载状态、脱敏层级和导航标签，无法确认媒体容器、总数及轮播关系，始终不声明完整，不点击翻页。`layout_unverified` 是真实缺口，不能手填成功值解除 G8。

查询 `scheduled_attempts` 为空时，先核对实际账本是否存在、每个 attempt 的最新状态，以及 Business Suite 是否有原排期。这个筛选结果不代表没有 `submit_ambiguous` / `submitted_unverified`，也不代表远端为空。没有合格原 attempt 时暂不运行下面的补验命令，不手填 ID 或改账本为 scheduled；先处理未决记录或核对历史对象。确实没有可用原样本后，才按 §16.3 准备具体冻结内容并确认新排期。

若点击确认后提示“月历里的渠道无法识别”，先查该任务的 `publish_operation` 与月历的 `refresh_diagnostic/coverage`。这个提示也包含独立时刻未核验；此时换日期仍可能被拒绝，不能直接把旧日期格视作无关卡片。保留冻结内容，按 §8.1 对当前未核实项运行一次只读详情探针，先区分同格的 Post 与 Story；不要用反复提交代替诊断。刷新成功但 `unresolved_count>0` 仍需核对具体证据，见 [HANDOFF §1.34](HANDOFF.md#134-g8-远端图片与原排期补验2026-09-23) 的首次确认现场。

09-15 19:17 的聚合 Post 更新后，保持 9223 中该 Post 详情打开，用 `scripts\run_python.bat -m tools.probe_calendar_detail --date 2026-09-15 --time 19:17 --kind Post --verify-reader` 复验一次。结果须完整列出各自的 FB/IG 身份和时刻，不能用同刻的单渠道 Story 替代；`PARTIAL` 时保留含 `DETAIL_STRUCTURE`、`ENTITY_IDENTITY`、`READER_RESULT` 的整份日志。单条完整通过后才刷新月历并检查未核实项，再回到原冻结稿件确认排期；不将探针成功当作已创建排期或 G8 通过。

**若随后提示冻结快照不一致，按失败字段处理。** v1 冻结来源指纹包含图片链接的签名，新链接可能让旧快照失配；不能仅凭这句报错断言原图已变。2026-09-24 的已确认实例及只读诊断见 [HANDOFF §1.34](HANDOFF.md#134-g8-远端图片与原排期补验2026-09-23)。更新后的代码会在访问月历前指出失配项，但不会自动迁移或解除旧冻结。

1. 保留诊断和旧快照，核对本帖账本无 scheduled 或未决提交、失败发生在提交前；已绑定时刻或存在回执时先核对原尝试，不套用下面步骤。
2. 打开对应稿件，在“审核与排期”点“解除冻结”。这只作废原冻结记录并恢复编辑，不删原文件，也不提交排期。
3. 逐项复核最终正文、链接、标签和所选图片；测试文字如非正式内容，应在这一步人工编辑并保存。仅 URL 签名变化不需要重译或重做图片。
4. 点“编辑确认无误”，重新冻结。新快照采用 v2，只忽略图片已知签名参数；正文、作者关系、原图字节、数量、顺序、派生参数及未知 URL 参数仍受校验。旧快照保留，不能手改 `snapshot.json`。
5. 重新选择时刻，打开“确认发布时间并排期”，复核新冻结预览中的图文、目标账号及唯一渠道，并核对业务时刻与柏林时刻。确认这一份内容后只点击一次创建排期。
6. 成功时保留原 attempt/remote ID，需补充图片证据时再按 §16.4 只读补验；结果不明确时保留现场，先查原回执，不反复提交。

**旧版本提示“编辑器缩略图尚未成为可核验的 Meta 图片”时，先更新代码。** 该限制已从普通发布流程移除，不需要为满足缩略图地址或相似度检查重新制作图片。图片里画着加载动画或文字，不代表平台正在上传。系统现在检查附件数量、平台的上传提示及提交按钮状态，并保留账号、正文、时刻和防重检查。

本帖账本若明确为 `failed_pre_submit`，且没有同来源的 scheduled 或未决提交，可回到审校台沿用已有最终图片和正文，重新点“编辑确认无误”并确认具体排期。该失败恢复为待审核是现有流程；无需重跑加工任务、删除旧快照或清账本。旧失败编辑器不用于手动补点 Publish。若为 `submit_ambiguous` / `submitted_unverified`，先核对原回执。新版本仍报告少图、多图、上传未完成或按钮禁用时，提供本次操作与页面提示，不反复提交；G8 图片证据不足本身不阻断单篇排期。

正常提交后确认月历中的账号、渠道、完整正文、时刻和远端编号，即可得到“已排期”；不会自动为图片证据重开详情、下载或比较图片。看到“图片未做额外远端核验”无需再创建排期。下面的图片只读复验用于补充 G8 证据，可按需另行执行。

**旧版本提示“提交前日期已变化；未提交”时，先更新并重启服务。** 已确认一例：填写回执为 `9/30/2026`，后续截图显示 `Sep 30, 2026`，实际日期相同。新版按年月日比较，并在错误中同时列出期望日期、UI 时区及实际值；时间字段的补零和显示空白也按含义比较。无需为了该显示变化修改 Windows 时区、排期配置或重做图文。回执明确为提交前失败时，沿用上面的重新冻结与单次确认步骤；未知结果仍先核对原回执。新版本仍拒绝时，提供这条包含期望值和实际值的完整提示及排期区截图。

**若 G5 报期望和 UI 同为 `11 : 00 PM`，但三个字段均为空，更新并重启 Web 后再确认原稿件。** 旧代码要求 input 值和容器显示同时成立，会误拒绝这类分段控件；新版在三个字段全空且同一容器全文为唯一完整时刻时读取显示值，仍核对选定时刻，填写与最终复核共用。无需修改时区、改选其他时间或重做素材。离线三脚本通过只代表对应测试，不代表真实排期完成；先检查原回执是否明确提交前失败，再展示当前冻结内容并确认一次，未决记录不得重提。只读控件探针无结果时不为补证重复提交。

若 FB 已完整而 IG 缺 `aggregate_identity`，同时旧输出为 `RESPONSE_SUMMARY: 40/40`，先更新并重启 Web，再对同一 Post 只读复验。新版摘要列出 `by_view`、`dropped_by_view` 和每视图 40 条/整页最多 120 条限制；同一轮保留 `instagram_post` 的媒体与账号关联以及 `during_view`。达到初始额度不再关闭后续渠道采集，重复切换也不会增加额度。仍缺身份时返回完整日志，不能用已选中 IG 或预览中的账号名字代替原生绑定，也不要用再次创建排期取证。

1. 在服务机核对已有 `published.jsonl`，选当前最新行为 `scheduled` 的原 `attempt_id`。它必须有单一渠道、确切 `remote_id`、原 `snapshot_id`、最终正文/图片摘要、来源指纹版本、绑定时刻和 UI 时区。保留原冻结目录及已有 `channel_controls.json`，不新增资产 ID 配置，不把开发机空目录覆盖过去。
2. 快照中的 `publish_target` 必须与当前配置账号、既有资产绑定和发布 Chrome 一致；缺失或变化时入口在访问浏览器前拒绝。旧 v1 来源指纹仍按 v1 校验，但缺历史目标绑定不能自动补造。没有合格原对象时，才按 §16.3 展示并确认具体冻结正文、逐张图片及 SHA、账号、唯一渠道、北京/柏林时刻、UI 时区与占用结果，再创建一次受控样本。
3. 在对应服务机源码目录，用人已登录的发布 Chrome 9223 执行只读补验。将下面的 `ATTEMPT_ID` 替换为原记录的值；没有 `--submit` 参数。

```powershell
scripts\run_python.bat -m tools.reverify_scheduled_media --attempt-id ATTEMPT_ID --timeout 30
```

入口持现有发布锁重读账本与快照；未决提交、已撤销记录、坏快照或缺 ID 直接拒绝。它不填写、上传、提交、删除，也不伪造提交前基线。结果沿原 attempt 追加，旧行、身份、时刻与指纹版本保留，重复执行复用原投影幂等位。原 `recover` 只补本地投影，不会访问远端验图。

输出 `remote_images_verified=false` 或退出码 2 表示本次尚未核验全部图片，**不表示原排期被取消或允许重提**。`target_found=true` 表示本次正文/账号/唯一渠道/时刻/ID 已匹配；图片问题另看 `readback_diagnostics.remote_media.error`，如 `layout_unverified`、`image_not_loaded`、`image_download_failed`、`image_decode_failed`、`media_list_changed`。`target_found=false` 时保留历史 scheduled 和防重，按诊断人工核对原对象。拒绝结果不追加；已经开始远端重读的失败会追加诊断。完整证据成立才返回退出码 0，当前诊断布局不会产生这种结果。

每次成功保全的观察位于 `state/publish_attempts/remote_media/<观察 ID>/`：`media.json` 记录绑定、期望数、独立观测数、方法、结构和文件摘要，`detail.png` 为目标截图，`image-000.bin` 等为实际候选图片字节。候选序号不等于已验证的帖子媒体顺序。若报告 `evidence_write_failed`，先处理本地存储问题，不能据此确认图片。原快照、journal 追加行、观察目录须一起保全，并逐份核对 SHA-256；不覆盖同名异字节文件，不外传 Cookie、令牌或带凭据请求头。

供开发补布局的材料应按 FB-only、IG-only 分开，优先原有多图且容易区分的对象。除上述文件，还需同一详情的媒体容器与账号/渠道/ID 归属、独立总数或明确末项、每一位置及顺序、主图与缩略图对应关系、轮播/懒加载过程和实际加载状态。当前候选诊断没有证明读全；需由人补充缺失的只读观察后实现并验证对应布局。单图只覆盖单图行为；G1 通用快照不能代替这份材料。

⛔ 在拿到那份证据之前不要动 G8 的判据。编辑器里两张图核对通过、成功 dialog、
只回读到正文和 remote ID，都**不能**替代远端排期详情里的图片数量与顺序。

### 16.5 要撤掉一条已排期的帖子时

系统不会替你删远端卡片。先在 Business Suite 里删掉，再回审校台点「我已在后台删除这条排期」
并填写说明；系统会持发布锁重读整月核实那条 remote ID 确实不在了，核实不过不改状态。
`published.jsonl` 里原来那条记录保留不删。
操作前按 §16.1 核对发布账号与已有资产绑定；目标时刻回读或日历刷新成功都不能替代这里的整月核实。

## 17. 办公局域网接入

当前源码部署用 [§13.1](#131-源码服务机一键改址)，拉取后的调试见 §17.6。§17.1–§17.5 保留旧受管实例的安装、维护和网络验收说明；新的制品安装明确延期。以下具体 IP、网段和网卡属于 2026-09-17 的受管操作示例，改址后不得照抄旧数值；源码配置读取 `ops/service-machine.network.json`，旧受管实例以其 `control/host.json` 为准。

首版面向 2–5 人同权、受控办公局域网 HTTP。用户确认暂不登录和记录个人身份，`actor: null`；获准进入入口的电脑具有相同业务能力，HTTP 不加密。业务写入仍经过已有预算、来源许可、冻结确认和版本冲突检查。本节只解决访问，不授予抓取、模型或发布权限。

### 17.1 固定入口和首次安装

2026-09-17 用户确认的历史样例为 IPv4 **10.66.4.9**、网卡 **WLAN**、掩码 **255.255.255.0**、网关 **10.66.4.254**，当时入口为 **http://10.66.4.9:8765**，仅允许 **10.66.4.0/24**，开发机 `10.66.4.12` 在该范围内。DHCP 地址保留未确认。网关不是业务入口，已断开的以太网和虚拟网卡不用于放行。

安装配置已写入 [ops/service-machine.network.json](../ops/service-machine.network.json)，仅含四个网络字段，通过 `--network-config` 读取并校验后写入 `control/host.json`。该文件只在版本库里，不随运行制品发布，不改变开发默认、不保存凭据、不覆盖业务运行模式。配置文件与逐项网络参数不可混用。

每次改址均重新现场核对 IP/前缀、DHCP 地址保留、Domain/Private 网络类型、客户端网段及 Wi-Fi 客户端隔离。应用配置不会切换 Wi-Fi，也不会修改 Windows 的 IP、掩码、网关或网卡网络类型。

新的受管安装随自动制品交付明确延期；以下仅保留历史包的安装参数示例，包来源与恢复边界见第 15 节。当前源码调试按第 17.6 节。网络配置取服务机上已核对过的仓库副本，下面历史包、仓库和安装目录按现场修改：

```powershell
Set-Location D:\Downloads\fbscraper-windows
py -3.12 -m deployment install --root D:\FacebookScraperService --release D:\Downloads\fbscraper-windows --network-config D:\VSCodeWorkspace\Facebook\FacebookScraper\ops\service-machine.network.json
```

服务机上没有仓库副本时，改用等价的逐项参数 `--web-host 0.0.0.0 --public-base-url http://10.66.4.9:8765 --allow-client-subnet 10.66.4.0/24`，不要另行传递这份文件。

`--allow-client-subnet` 可重复；只接受已核定的内网 IPv4 CIDR，公网网段与 `0.0.0.0/0`、`128.0.0.0/1` 这类全网放行一律拒绝。默认端口为 8765，显式改变端口时 `--web-port` 与 URL 端口须一致。省略全部网络参数会安装为回环模式；局域网参数不完整或非法时，安装器在创建目标目录前拒绝。

首次安装直接使用包含局域网能力的完整制品。控制器指纹已经包含网络策略代码，旧控制器不会自动升级来接受本次新基线。若现场实际已存在安装，先核对其版本和实例，按第 17.4.1 节改址，不覆盖安装目录或共享数据。

按第 15.4 节建立唯一控制器任务、启动默认仅 Web 的模式。本机检查：

```powershell
Invoke-RestMethod http://127.0.0.1:8765/api/health
Get-NetTCPConnection -LocalPort 8765 -State Listen
```

核对 `deployment_ready`、前后端指纹、实例、实际 `web_host`/`web_port` 与标准入口。健康信息来自匹配当前进程的心跳；配置文件和进程监听不一致会拒绝 readiness。候选预检继续绑定临时回环端口，不出现在办公入口。

### 17.2 手动设置 Windows 防火墙

在具备权限的技术账户 PowerShell 中使用已安装控制器的 `scripts\configure_lan_firewall.ps1`。先预览，再应用；本机使用 `WLAN`，执行前通过 `Get-NetIPConfiguration` 复核地址没有改变：

```powershell
D:\FacebookScraperService\controller\scripts\configure_lan_firewall.ps1 -Root D:\FacebookScraperService -LocalAddress 10.66.4.9 -InterfaceAlias 'WLAN' -WhatIf
D:\FacebookScraperService\controller\scripts\configure_lan_firewall.ps1 -Root D:\FacebookScraperService -LocalAddress 10.66.4.9 -InterfaceAlias 'WLAN'
```

脚本读取并校验本实例的持久网络配置，仅管理名称 `FBScraper-LAN-Web`、分组 `FBScraper Managed Access` 的专用规则：TCP Web 端口、指定本机 IPv4 与办公网卡、配置的办公来源网段、Domain/Private 配置文件。重复执行更新同一条规则，不按版本目录绑定 Python 路径，不关闭整个防火墙、不启用 Public 配置文件。业务控制器不自动调用此脚本，也不因此增加运行权限。

人工检查现有 Python 或端口放行规则和生效的组策略，确认没有更宽规则绕过范围；脚本不会擅自删除其他软件规则。Chrome 9222/9223/9224 保留本机回环，Vite 5174 不作为业务入口。

### 17.3 从业务电脑验收

至少两台实际办公电脑分别运行 TCP 连通检查，然后用桌面 Chrome/Edge 打开标准入口：

```powershell
Test-NetConnection 10.66.4.9 -Port 8765
```

- 打开首页、深层任务链接并刷新，查看图片及下载素材；从运行页复制标准入口。
- 在隔离业务数据上，两人修改同一篇并先后保存：后一个旧版本保存收到冲突，输入仍保留。再验证 5 个独立会话操作不同内容。
- 保留一个未保存页面或在途上传，触发已准备的测试更新：其他电脑可见等待，不能越过该页面切换；所有页面完成后才能更新。核对暂缓 30 分钟、断网恢复、失联脏会话及旧页面写入冲突。
- 对部署健康、本地 CLI 与未允许来源做拒绝检查；失败记录 TCP、HTTP 状态及所用地址，不粘贴业务凭据。
- 按已有人工通知步骤取得验收消息，从业务电脑的飞书打开任务或运行链接；只看卡片正文不算链接验收。

访问被拒提示时先核对标准 URL、实际来源网段、办公网卡/配置文件、规则及公司网络策略；无需更改浏览器安全设置或放宽所有来源。临时断线时保留原标签页和输入，连接恢复后先确认版本再继续，不重复提交结果不确定的付费或发布请求。

### 17.4 跨夜、重启和日常变更

机器持续供电联网，禁用会中断任务的自动睡眠，运营账户保持登录；可锁屏，不以注销代替锁屏。记录一次锁屏期间及跨夜的局域网访问、版本与业务模式。控制器任务仍是 `InteractiveToken`：重启后无人登录时不承诺业务恢复；运营登录后验证只启动一套正确实例。Chrome 三个 profile 仍按原流程人工登录和独立验收。

普通代码更新/回退不覆盖 `control\host.json`，飞书与页面入口保持一致。需要更换 IP/网卡/网段时，由技术人员按下面的维护顺序改址；不能只改文件后把它当作进程已经切换监听。

#### 17.4.1 已有实例改为当前办公网

仅修改仓库的 `ops/service-machine.network.json`、拉取源码或更新程序都不会迁移已安装实例。先核对实际安装根目录：第 17.6 节测试实例为 `C:\FacebookScraperServiceLanTest`，第 15 节正式实例示例为 `D:\FacebookScraperService`；全部操作必须指向同一实例，不重新安装或清空目录。

1. 在服务机本地记录实例 ID、当前 SHA、实际及保留的调度/处理模式、自动更新暂停状态和控制器启动方式。执行第 15.8 节的 `pause`，确认没有正在切换的部署事务、尚未处理的 retry/rollback 请求或模式变更。所有使用者保存或明确放弃草稿，等上传、采集、模型和发布任务结束；换网造成的失联草稿仍需核对，不删除维护记录解除阻塞。
2. 技术人员先防止控制器再次启动，再核验控制器正常退出：有 `FBScraperService` 计划任务时先停用其后续启动/重启机制；前台控制器按一次 `Ctrl+C` 退出并核对进程身份。更新后的前台控制器会显示「已停止。」；旧进程仍按其原版本输出。`pause` 只暂停更新，控制器仍会重启已退出业务进程或重开维护闸，不能让它与手动维护并行；退出控制器也不代表 Web/调度进程已退出。
3. 控制器退出后，技术人员通过现有维护 Gate 预告，等预告期结束且 operations 和 blockers 清空，确认 `try_quiesce()` 成功；再由 `LocalBackend.stop()` 向登记的准确业务进程请求协作退出，用 `LocalBackend.exited()` 确认 worker 和 launcher 均已退出，核验 PID、创建时间及端口。维护调用不要经过 `deployment exec`，它登记的 manual_cli 本身会阻止进入维护。不可使用 `schtasks /End`、强杀业务进程或删账本来结束任务；只关闭前台窗口不足以完成这一步。
4. 备份该实例 `control\host.json`，仅将 `public_base_url` 改为 `http://10.66.4.9:8765`、`allowed_client_cidrs` 改为 `["10.66.4.0/24"]`。核对原监听仍为 `0.0.0.0:8765`；保留 instance_id、shared、版本、调度/处理模式等其余字段，不用四字段安装配置替换整个 host 文件，不更改 `releases` 中的配置或 `shared` 数据。使用现有网络策略校验确认 URL、端口和 CIDR 一致。
5. 核对 WLAN 当前为 `10.66.4.9/24`、Domain/Private 网络，并完成 DHCP 地址保留。按第 17.2 节使用该实例的脚本先预览再应用防火墙：本地地址 `10.66.4.9`，来源仅 `10.66.4.0/24`，TCP 8765。核对旧来源已不在本系统专用规则中，其他放行规则仍按第 17.2 节人工审查。
6. 保持维护闸关闭，通过原控制器入口启动同一实例，由控制器完成 readiness 后自行开放接单，不手动 `reopen()`。恢复原来已有的控制器启动机制，自动更新保持暂停。核对本机 `/api/health` 的 readiness、新标准入口、实例、SHA 与前后端指纹，以及实际调度/处理模式；再由开发机 `10.66.4.12` 和另一台同网段办公电脑按第 17.3 节检查页面、刷新和访问限制。记录新网络结果，更新书签；历史飞书卡片不会改写，后续卡片链接使用新入口。自动制品交付延期期间继续暂停发现更新，业务运行模式保持原状。

网络切换与业务启用分别验收，改址不打开调度、付费处理或发布。新网段的两台客户端、跨夜及登录恢复尚未验收时，状态继续为「待真实联调」。

### 17.5 撤回入口与证据

撤回远程入口先撤销本系统专用放行，核对实际可达性；已有任务依然按维护协议处理，不强杀业务进程：

```powershell
D:\FacebookScraperService\controller\scripts\configure_lan_firewall.ps1 -Root D:\FacebookScraperService -Remove -WhatIf
D:\FacebookScraperService\controller\scripts\configure_lan_firewall.ps1 -Root D:\FacebookScraperService -Remove
```

应用回退使用第 15.8 节的控制器 CLI，仅回退已验证且兼容网络配置的版本，继续使用同一份共享数据。回退不能恢复旧发布/付费账本或人工稿。

现场证据记录安装 SHA、控制器及应用指纹、实例、固定地址、规则范围、两台客户端、并发与更新结果、锁屏/跨夜/重启登录时间。TCP 通、完整页面可用、真实模型/发布分别验收；开发机上的假服务及 HTTP 浏览器回归只记离线证据。

开发机的局域网专项离线复核命令如下；先按第 15.10 节准备前端及锁定 wheel，`--out` 使用新目录：

```powershell
scripts\run_python.bat tests\browser_lan.py
scripts\run_python.bat tests\windows_deployment_rehearsal.py --lan --wheelhouse state\release-wheelhouse --out state\windows-lan-rehearsal
```

浏览器测试通过隔离域名映射验证非 localhost HTTP，断言 `isSecureContext === false`，不降低浏览器安全选项。Windows 演练使用临时端口、测试域名和文档保留网段，核对 `0.0.0.0` 监听及更新/回退前后的网络配置，隔离候选仍只绑定回环；不设置本机防火墙或注册业务任务。两者均使用隔离数据及假外部服务，不能代替实际办公网验收。

### 17.6 拉取主分支后在服务机调试

当前服务机直接运行源码。本节沿用现有目录、数据与启动方式，不创建新的受管实例，也不下载 Actions 制品。旧受管实例的维护、暂停和协作退出见 [§15.8](#158-状态暂停重试与回退)；不要在其 releases 目录套用本节操作。

1. 按 [§13](#13-更新并启动审校台) 记录当前 SHA、保全本地改动，在业务空闲后正常停进程，再拉取 main。依赖有变化时使用原绑定解释器更新；启动前保留实际数据与凭据路径。
2. 使用 `scripts\run_web_lan.bat` 安装锁定前端依赖、构建并启动。失败时保留控制台输出，先处理构建或启动错误，不另起 Uvicorn 提供旧页面。源码运行需要 Node.js/npm。
3. 另开服务机本机 PowerShell，在同一源码目录核对版本、网络配置及健康响应：

   ```powershell
   git rev-parse HEAD
   $sourcePolicy = Get-Content -LiteralPath 'ops\service-machine.network.json' -Raw | ConvertFrom-Json
   $sourcePolicy
   Invoke-RestMethod -Uri ('http://127.0.0.1:{0}/api/health' -f $sourcePolicy.web_port)
   ```

   `error` 应为空、`managed=false`；`deployment_ready=false` 是源码模式正常值。SHA 通过实际运行目录的 Git 核对，不使用制品字段判断源码版本。页面通过配置中的 `public_base_url` 访问；健康接口只在本机检查，远程返回 403 符合访问策略。

4. 核对本次修改涉及的页面、接口及 `/runtime`，记录命令、源码 SHA 和结果。真实业务异常按 [§12](#12-卡住时保留什么) 留取日志；本机响应正常不代表抓取、模型或排期已验收。写入测试使用隔离样本，不在真实账本中造夹具，不为部署检查触发抓取、付费、飞书自检或发布。
5. 首次接入或网络策略变更时，按 [§13.1](#131-源码服务机一键改址) 核对当前 IP、来源网段、防火墙和至少两台办公电脑的可达性；日常无网络变更时不重复整套局域网验收。按原命令及已授权模式恢复调度器，确认实际心跳。

更新失败时停止后续操作并保留现场；代码按 §13 回退，账本、人工稿、原图和凭据不随代码回退。仅移除工作流或修改文档无需为此重启服务机。
