# 代码审查交接（2026-09-12 历史记录）

## 2026-09-13 处理结果

下面原文保存当时的审查事实和用户决定。“未完成”及测试数量均指 2026-09-12 的现场，不再作为当前待办。当前五阶段状态见 [实施与验收清单](../../OPTIMIAZATION.md)，真实观察及尚缺材料见 [本轮集成记录](../../INTEGRATION_2026-09-12.md)。

| 原发现 | 当前结果与验收入口 |
|---|---|
| 1. 同名源文摘要不一致 | 写入和比较统一 `source_text_sha256`；风险扫描另存精确 `scan_text_sha256` 保护高亮位置，最终发布文案仍按原始字节计算独立摘要。hygiene 的 AST 守卫覆盖含 Web 的直接字段计算，publication_recovery/risk_scan 覆盖空白与版本行为。AST 不宣称能证明任意变量的数据流。 |
| 2. 兜底排队过期丢普通探测、漏提醒 | `afb6784` 仅在真正执行补扫后合并普通探测，维护回调收到本轮结果；service 记录跳过事实并按目标早班日期向开发者告警、汇入晨报。scheduler_recovery/monitoring/review_notifications 验证跨平台排队与跨日归属。 |
| 3. 卡住的付费任务没有放弃入口 | `829cbd2` 已实现进程身份、中断识别、关联请求和带版本核对的 Web 恢复入口。未发请求可关闭；已发请求先核产物与费用，结果不确定保持阻塞，活跃执行器不能抢占。沿用每图次数，不采用仅等待 15 分钟就覆盖活跃任务的建议；恢复不会触发再次付费。 |
| 4. 飞书发件箱只增不减 | `923091f` 新增 30 天保留期，完整关联的已完成组件耐久归档后才裁剪主文件，主文件保留去重索引；原卡、UUID、回执可读。结果不确定、retry、pending 和未投递积压保留。此处优先遵循本轮“不确定结果必须先核对”的恢复规则，不把旧建议中的 uncertain 当作可删除终态。 |
| 5. FB 链接只能落在末尾 | 已接入正文光标插入 `{{linkN}}`、最终渲染替换、无效占位符阻止通过，以及服务端最终字符数；未插入的链接按原顺序追加。IG 正文和自定义 CTA 都不接受占位符。localization/web_review 与第 7 条离线浏览器回归验证保存、刷新和计数。 |
| 6. 重复兼容别名 | `_norm_money = normalize_money_token` 保留一份。 |

原六处既有修订与独立账号索引修订已经保留并纳入 `829cbd2`；不是重新创建空状态。卫生检查的 Web 语料盲点和延迟导入说明也已修复。文档之后经 `81d94b2` 恢复详细需求并继续同步，原业务功能编号仍保留；下文第五节的“继续简版”记录不再描述当前文档形态。

独立复审的风险摘要和 IG CTA 两项发现已修复并定点复验。最终完整离线运行与针对失败的重跑记录位于集成文档；以上实现关闭不代表 `.global`、飞书、真实模型或新单渠道排期已完成外部验收。远端排期详情图片读取适配器仍按当前实施清单单独列为代码缺口。

---

## 原始审查交接

来源：2026-09-12 对 `c67c56a`（`701b1f8` + `c67c56a` 两个提交、约 12,200 行）做的一次独立代码审查，
校准依据是当时的 `docs/FUNCTIONALITY.md`（1513 行那版，现完整保存在原工作区 `docs/functionality-planning`
分支 `0eeb099`）。审查期间你正在并行实施 `2026-09-12-workflow-completion.md`，
所以**代码侧我只落地了第一项，其余六项写在这里交给你**，避免两边同时写同一批文件。

先记公道的：47 份离线脚本全绿、hygiene 当时六条全绿、Ruff 在这批改过的生产文件上干净
（base 里 `translate.py` 10 条、`tools/layout.py` 5 条、`core/store.py` / `capture.py` / `pipeline/cli.py`
各 2 条都清掉了），该停在证据闸前的地方也真的停住了（`publish/channels.py` 无条件抛
`ProbeRequired`，没有靠改 `target_channels` 假装渠道已选）。

---

## 一、已经落在工作区、不要重做的六处

| 文件 | 改了什么 |
|---|---|
| `core/mirror.py` | `queue_state` 的范围从整棵 `state/` 收成账本，加 32 MB 上限 |
| `web/api/query_index.py` | 干净路径不再白跑第二遍 `_source_signature` |
| `config.toml` | 补上 `[review].snooze_default_days`（三处代码在读，段落却不存在） |
| `core/store.py` + `core/translated.py` | 源文哈希收进 `source_text_digest` 一处定义 |
| `tests/tests_hygiene.py` | 检查 [1] 的语料扩到 `web/` |
| `core/index_db.py` · `query_index.py` · `reader.py` · `tools/layout.py` · `tests_query_index.py` | 展示索引与审校队列收窄到活跃账号 |

两处实测，供你写验收证据时引用：

**`state/` 镜像**。F2-4 给「`state/` 一起镜像」的理由只有一条：`published.jsonl`（112 KB）不可重建，
原话「成本可忽略」。但快照是按小时重跑的，而真实 `state/` 里 **166.2 MB 中有 163.8 MB 是 228 张 PNG**
（probe 截图 + 提交/失败证据）。PNG 压不动，zip 后仍是 166.2 MB，base64 进 spool 是 221.7 MB/次
≈ **5.32 GB/天**，而 spool 和 `snapshots` 都没有回收口，云盘每月还会多出约 720 个 `快照_vN`。
收成「能校验完整性的账本」之后是 **0.121 MB/次、3.9 MB/天**，内容是
`published.jsonl` / `paid_requests.jsonl` / `delta_state.json` / `pipeline_state.json` /
`alerts.log` / `pipeline.log` 六个文件。probe dump 另有独立理由：HANDOFF 明写换机器必须重录，
镜像它既贵又没用。

**展示索引**。实测一次冷路径 `GET /api/tasks` **7.24 秒而列表是空的**：
`review.history` 被调 **1067 次**（1.75 s，`_display_rows` 每篇重读一遍整个审校账本），
`_source_signature` 走遍 1067 篇 `post.json` 两趟（各约 1.66 s）。其中 **1020 篇是冻结的
`in_neakasa.tech`**，审校队列永远显示不到它。`display_account_dirs` 就是为这件事加的 ——
你已经把它当成「当前队列看活跃账号 / 历史页 `include_frozen=True` 看全部」的接缝，方向一致。

---

## 二、还没做的六项

每项标了对应你计划里的哪个 Task，以及用户已经拍过的板。

### 1 · `source_text_sha256` 统一到一个口径 + 机器检查 → 你的 Task 2 / Task 8

**用户已决定：统一，并加 AST 机器检查。**

同一个字段名有两套算法：`core.translated.source_text_sha256` 去首尾空白，
`publish.journal.text_sha256` 不去。最危险的一处在 `needs_human.jsonl` ——
`ready_to_publish`（不 strip，`engine.py:1046`）和 `human_translation_stale`（strip，`engine.py:673`）
用**同一个键名**落在**同一个账本**里。今天两条链各自闭合所以对得上，
哪天有人写一句跨 kind 读 `details['source_text_sha256']` 的代码就会误判。

**实测 0/1067 篇归档正文带首尾空白**，所以现在统一是零行为变化：既有记录的值一个都不变，
不会产生假的「原文发生变化」告警，也不会静默丢掉待审推送。

5 处写入 + 3 处比对切到 `translated.source_text_sha256`：

| 文件 | 行 |
|---|---|
| `publish/workflow.py` | 53（`DePost.source_text` 就是 `source["text"]` 原文，见 `compose.py:1020`） |
| `pipeline/approval.py` | 79（`snapshot.json`） |
| `pipeline/engine.py` | 1046（`ready_to_publish` details） |
| `pipeline/initial_translation.py` | 141（同文件第 82 行已是 strip 口径，本行不是） |
| `pipeline/service.py` | 123 / 221 / 227 / 274（一处写入、三处比对） |

⛔ **不要动** `text_de_sha256` / `original_text_sha256` / `final_text_sha256` /
`_publish_fingerprint` / `business_suite.py:1627` —— 那些绑的是**提交出去的那一份字节**，
strip 会让绑定失真。`engine.py:320` 的 `sources` 摘要也不动（键名不同，见第四节）。

机器检查（`tests_hygiene.py` 新增一条，扫描范围要含 `web/`）：字面量键
`"source_text_sha256"` 或同名 keyword 参数，取值是 `ast.Call` 时被调函数名必须是
`source_text_sha256`；`ast.Compare` 一侧是 `Call`、另一侧是带该键字面量的 `Subscript` /
`.get(...)` 时同样要求（这条抓 `service.py` 那三处比对）。透传已校验令牌的 `Name` /
`Attribute` 形态放过。

### 2 · 兜底深扫会静默丢失 → 你的 Task 3

`_reconcile_expired` 的阈值是上海 **07:32**（08:00 − `processing_budget_min` 25 −
`2 × reconcile_max_session_seconds/60` 3），而两个平台的 reconcile 各自随机落在 **06:30–07:30**。

`scheduler.tick()` 在**轮次开头**就按「reconcile 未过期」把同平台的 delta 合并掉，
但 `expired` 会在该 job **真正开始时**重算。第一个平台跑完已过 07:32 时，
第二个平台这一轮**既没深扫也没探测**，下次探测 45–75 分钟后。

而跳过只写进 `scheduler.json` 的 `last_error`，不经过 `Runtime.scan`，**没有飞书告警** ——
F1-3 说这是她上班前最后一道防线，CONTEXT 里「跳过必须被计数并报出」是硬规矩。

既有 `test_expired_reconcile_does_not_swallow_evening_probe` 覆盖的是「轮次开头就已过期」那种，
和这条不是一回事。

两个改法合起来才闭合：

1. 不再预先算 `reconciled_platforms` 删 delta；同平台按 reconcile 先于 delta 执行，
   记下**真正执行过** reconcile 的平台，之后遇到该平台的 delta 才合并。
   「先落盘下一次时刻，再调回调」的顺序不能破（崩溃后不密集补跑压在它上面）。
2. `tick()` 把本轮 results 传给维护回调（`self.maintenance(self.clock(), results)`），
   `Runtime.maintenance(self, now, results=())` 对带 `skipped` 的 result 发一条 system 告警，
   `event_id` 含日期与平台（天然按天去重）。默认参数让
   `tests_pipeline_service.py` 现有三处 `runtime.maintenance(self.now)` 不用改。

### 3 · 卡住的付费任务没有放弃入口 → 你的 Task 2 / Task 5（你正在改这批文件）

**用户已决定：审校台按钮 + 显式勾选**（不是只做 CLI）。

Web 进程中途重启会在 `state/refinements.jsonl` 里留下 `running`，
`refinement.submit` 与 `initial_translation.capabilities` 都据此永久拒绝这篇
（「重启遗留任务需先核对付费账本」）。不自动重放是对的，但既没有 CLI 也没有按钮，
唯一出路是手改一份紧挨付费账本的文件 —— 而项目自己的原则是「硬拦人工是反模式」。
两类任务共用 `refinements.jsonl`，要一起解决。

建议形状：

- `refinement.abandon(job_id, *, acknowledged: bool, now=None)`；
  `acknowledged is not True` → `ReviewValidationError('请先核对 state/paid_requests.jsonl…')`；
  已终态 → `ReviewConflict`；`recorded_at` 距今不足 15 分钟 → `ReviewConflict`
  （单张图实测约 2.5 分钟，留 6 倍余量。这是为了诚实不是为了安全 —— 并发已由
  `_executor(max_workers=1)`、`ImageRunLock` / `TranslationRunLock` 与 `RequestController` 守着）。
- 锁内追加 `status='abandoned'`，带 `acknowledged_paid_ledger: True`；对
  `text` / `image` / `initial` 三类一视同仁。
- **不重置次数**：`capabilities().image_attempts` 数的是全部 job 行，abandoned 仍计入
  3 次/张上限，与「失败也计入次数」同口径。
- 若那条任务其实还在跑，它结束时照常追加终态事件，`latest()` 后写胜出 → 真相覆盖
  abandoned。这是对的，写进 docstring。
- ⚠️ **`refinement.execute` 缺一道 claim 检查**：它进入时无条件 `_append(status='running')`。
  `initial_translation.execute:102-104` 已经有（`status != 'pending'` 就不接）。
  引入 abandoned 之后这个缺口会把一条已放弃的任务标回 running。
- 端点 `POST /api/refinements/jobs/{job_id}/abandon`，body `{acknowledged_paid_ledger: true}`；
  `RefinementPanel.vue` 与 `InitialTranslationPanel.vue` 在任务卡住超过 15 分钟时展开一个
  `<details>`，内含必选勾选「我已核对 `state/paid_requests.jsonl`」。两个面板已经持有当前 job
  （`capabilities().jobs` / `.job`），不需要新取数。

### 4 · 飞书发件箱只增不减 → 你的 Task 6

`core/feishu.py` 的 `events` / `deliveries` 没有回收口，而 `dispatch` 每发一条就整文件重写一次 JSON。

加 `[feishu].keep_delivered_days = 30`，`dispatch` 末尾（仍持锁）回收。**顺序要紧**：

- 一个 event 的**全部** delivery 都是终态（`sent` / `uncertain` / `cancelled`）
  且最新一条 `created_at` 早于窗口 → 连同那些 delivery 一起删；
- 或该 event 带 `cancelled_at` 且早于窗口（被 `retain_ready` 撤下的）→ 同上；
- ⛔ **没有任何 delivery、也没有 `cancelled_at` 的 event 永不因年龄删除** ——
  那是离岗窗的积压，删掉等于夜里那批待审静默消失；
- 只删 delivery 不删 event 会让该 event 退出 `assigned`，**下一轮重新发一遍**。

### 5 · FB 正文里的链接只能落在末尾 → 你的 Task 7

**用户已决定：正文支持占位符**（不是保持现状）。

`localization.validate` 对正文里的 URL 报 `body_urls`，而 `render()` 只把 `target_url`
拼在文案末尾 —— 她无法把短链放在句中，而英文原帖里短链常在句中。

- 语法 `{{link1}}`…，1-based，对应 `draft["links"]` 顺序。
- `render()` facebook 分支：正文里出现的 `{{linkN}}` 替换成 `links[N-1].target_url`；
  **没有**出现在正文里的链接仍拼到末尾 → 既有记录渲染结果逐字节不变。
- `validate()` 加两条 **issue**（不是 warning，否则字面量 `{{link3}}` 会被发出去）：
  `unknown_link_placeholder`（序号越界 / 拼错 / 该链接 `target_url` 为空）、
  `placeholder_not_supported`（IG 分支会摘掉全部 URL，占位符会原样发出去）。
- `extract_urls` / `body_urls` 不用改：`{{link1}}` 不含 scheme 也不是 `www.`。
  `counts["char_count"] = len(render(draft))` 自动跟着对。
- ⚠️ `TaskDetail.vue::captionLength`（`:61-64`）**本来就是 `localization.render()` 的第二份实现**，
  而 `web/ui` 没有 JS 测试、机器守不住。建议顺手让 `POST /api/tasks/{id}/check` 追加返回
  `caption_length` / `hashtag_count` / `warnings`（接受可选的完整 localization 草稿，
  不传就维持旧契约），JS 那份只作 `/check` 回来之前的即时估算。

### 6 · 一处存量清理

`core/translated.py` 里 `_norm_money = normalize_money_token` 连同上面那行注释**整段重复了两遍**
（`c67c56a` 之前就有，行为无影响）。

---

## 三、两条 hygiene 红，都是你此刻的 in-flight 代码

2026-09-12 03:20 跑 `tests/tests_hygiene.py`：

```
[2] FAIL 零引用定义 1 个：query_page（core/index_db.py:175）
[6] FAIL 函数体内导入无「延迟导入：」说明 1 处：tools/publish_post.py:181
```

`query_page` **只被 `web/api/query_index.py:101` 调用**，所以它不是真的零引用 ——
这是检查 [2] 的 `web/` 盲点。作者自己在 `SYMBOL_EXEMPT` 里已经为 `capabilities` 踩过一次
（注释原话「此扫描器不遍历 web」）。

⛔ **不要再往 `SYMBOL_EXEMPT` 加一条。** 正确的修法和我已经对检查 [1] 做的那条一样：
把 `references` 计数器的语料也扩到 `web/**/*.py`。检查 [1] 现在用的是
`all_python = sources.values() + WEB_SOURCES`，检查 [2] 的 `references` 还只读 `sources.values()`。
一行的事，而且能让这个盲点彻底关掉 —— 否则每次 web 独占调用一个生产函数都要加一条豁免，
而豁免表越长，这条检查越接近失效。

（另有 `ensure`（`publish/snapshots.py:79`）在一次早些的运行里也被报成零引用，
重跑已自行消失 —— 那是抓到你写到一半的瞬间，`publish/workflow.py:361` 的调用方还没落盘。）

---

## 四、只记录、不要求动的四条观察

1. **`list_tasks` 每篇重复读账本。** 收窄账号范围之后，主项变成它自己：每篇约 6 次
   译文/审校账本全量读 + 2 次 `journal.load`（`published.jsonl` 112 KB），
   来自 `reviewable` / `_review_state` / `_hard_alerts` / 任务循环各自调一遍
   `_effective_translation_of`，以及 `scheduled_at` 在 `already` 与 `allocate_slots` 里各一遍。
   真要治就在 `_Context` 上加一层 per-request 记忆化（生命周期就是一个请求，无过期风险），
   并给 `review.state_for` 加可选参数 `events`。建议先量，不到 1 秒就别动。
2. **`review.history` 的坏行会让状态静默回退。** 它对解析不过的行 `continue`，
   于是 `latest()` 取到的是更早一条事件 —— 「已挂起」会表现成「待审」。
   你的计划里「坏审校记录回退 pending_review」就是这条，方向（失败闭合）是对的。
3. **回拨那一小时，同一张 Planner 卡片会在月历上出现两次。**
   `read_remote_slot_inventory` 为撞槽判定把 fold=0 / fold=1 两个绝对时刻都记为占用，
   两条 `RemotePlannerCard` 共用同一份 `rendered` 与 `card_sha256`。一年一次，只影响显示，
   保守方向是对的。
4. **`engine.py:320` 的 `sources` 摘要仍用不 strip 的口径。** 键名不同（`sources` 而非
   `source_text_sha256`），第二节那条机器检查不覆盖它；它是内部身份摘要，改了会动 item_id，
   所以刻意留着。

---

## 五、文档去向（已与用户确认）

`2ec1fae` 把八份文档从 4,116 行削到 910 行。**不回滚。** 分工是：

- **权威版本**在原工作区 `docs/functionality-planning` 分支（`0eeb099`）：
  `FUNCTIONALITY.md` 1513 行、`REQUIREMENTS.md` 496、`CONTEXT.md` 145、`HANDOFF.md` 518、
  `OPTIMIAZATION.md` 453、`MANUAL_STEPS.md` 469、`web/DESIGN.md` 462、`README.md` 342。
  已核实该分支工作区干净、§0.2 / §0.5 / §0.6 / §1.3 / §6 / §8 / §9 七个小节全在。
  那里存着业务访谈原话、13 条被推翻的旧决定、配置键的「安全 / 运营 / 业务决策」三分、
  47 篇发帖时刻直方图（83% 落在离岗窗，这是「在岗窗 / 离岗窗」命名的全部依据）、
  S0 三条硬前置与依赖图、刻意接受的代价、§8 待拍板五项。
- **worktree 里的简版**（`FUNCTIONALITY.md` 278 行 + `OPTIMIAZATION.md` 111 行的五阶段状态表）
  当开发基线，继续用。

两边各得其所。**但有一条要留意**：新版 `FUNCTIONALITY.md` §0.1 的固定前提表里，
原 §0.5 那条 ⚠️ 不在了 —— 「`[feishu].quiet_hours` 与 `[delta].on_duty_window` 表达的是同一件事，
实现时让后者引用前者，不要各写一份，否则她改作息时另一处会静默不同步」。
代码**确实**照做了（`Outbox` 用 `MonitorSchedule.is_on_duty`），只是依据不再有记录，
下一个人有可能把它拆回两份。值得在简版里留一行。
