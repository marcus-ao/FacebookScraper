# 项目交接 · 给接手开发的会话

> 直接把本文件内容粘贴进新会话即可。你也可以先让会话进入项目根目录，再说
> "读 docs/HANDOFF.md 然后开始"，效果一样。
>
> **最后更新：2026-09-01 第七轮 · 七次真机 G8 撞出 CR-70~76；已提交成功过一次，回读待验**
>
> ## 🆕 一分钟看懂现状（这一段覆盖本文件后面所有历史轮次）
>
> **操作顺序照 → `docs/MANUAL_STEPS.md` 第 12 步走**，那是唯一一份
> "照着按就能上线"的清单。`GO_LIVE.md` 现在只管**重录探查/重新标定**。
>
> | 环节 | 状态 |
> |---|---|
> | A/B/C/D/E/J 抓取归档 | ✅ 已验收 |
> | F 翻译（DeepSeek） | ✅ 管道通；德语审校仍待人 |
> | K 图内德语化（GPT-Image-2） | ✅ 两张真实产出，第一张用户确认可用 |
> | G1 真实 DOM 探查 | ✅ 首份完整通过 v2 契约的 dump 已录到并判读完 |
> | G6/G6c 生产证据 | ✅ 五件全部机械推导 + 逐条回查通过 |
> | `ui_constraints_verified` | ✅ 用户量完 14 项并改 `true`，**四道闸全开** |
> | 远端测试排期 | ✅ 用户已手工删除，**远端零残留** |
> | 两张业务表 | ✅ `trusted_owners` 加了 `neakasa.global`；`price_map` 46 行**恒等映射**（用户决定美元原样发） |
> | **G8 真机发一篇** | 🟡 **提交成功了，但回读没证明** —— 见下面那一段 |
> | L 流水线 activate / assisted | ⬜ 等 G8 拿到 `scheduled` |
> | 计划任务 | ⬜ 用户显式决定 |
>
> **先跑这条，它是算出来的，上面这张表是手维护的会过期：**
>
> ```
> .venv\Scripts\python.exe pipeline.py preflight
> ```
>
> 它还会告诉你"激活之后每天到底会发生什么"。
> 当前实测：最近 90 天 125 篇里 **36 篇能不问人走完付费阶段（75%）**。
>
> **测试基线：25 套 / 1607 项全绿**（`scripts\setup.bat` 全跑；
> ⚠️ 别把数字当契约，要断言的是"全绿"）。
>
> ### ⚠️ G8 现在到底停在哪：`submitted_unverified`，不是 `scheduled`
>
> 第六次真机跑**走到了提交并且成功**：成功信号看到了，两个渠道的排期时刻
> 也都写对了（`9/10/2026 01:00 AM ／ 9/10/2026 01:00 AM`），
> 用户在 Business Suite 上**亲眼看到了那两条排期**（随后手工删掉）。
>
> **但 G6c 的内容日历回读没能证明它**，于是留痕停在 `submitted_unverified`。
> 根因是 CR-75：`planner_loaded_signal` 推出来的是 `heading 'September'`，
> 它属于页面外壳，**在日历数据还在转圈的时候就已经渲染好了**。
> 已修（等到真的出现能解析出时刻的条目再读），**但还没在真机上验过**。
>
> **所以接手时要做的第一件事**：
>
> 1. 结转那条未闭合留痕（远端已经被用户删了，所以是 **not**-scheduled）：
>    `scripts\run_publish_post.bat --post-id 3975547640610092585 --mark-not-scheduled`
> 2. 重跑一次 G8。这次要看的是 `[7/7]` 能不能读回卡片、留痕能不能落 `scheduled`。
> 3. 拿到 `scheduled` 之后才能 `activate` —— 那道闸会机检这条证据。
>
> ⛔ **不要为了让 `activate` 过去而用 `--mark-scheduled` 造一条 `scheduled`。**
> 远端已经没有那条帖子了，那样写进去的是**假的**，而且会让这个 post_id
> 永久跳过、以后再也发不出去。
>
> ### 这一轮最重要的一件事：**证据契约按真实 UI 整段重写了**
>
> 2026-09-01 用户录到 `publish_probe_20260901_054226_378622.json`
> （56 交互 / 71 快照 / 117 截图，**v2 契约首次完整通过**）。
> 判读之后发现：**此前那套 G6c 契约是照着想象中的 UI 写的，现实完全不长那样。**
>
> - composer 上**从头到尾没有 IG 帐号名**，渠道只有 `img 'Instagram'` 一个图标；
> - Planner 上**没有"一张卡片带全部元数据"这种东西**：FB 与 IG 是
>   **两个独立对象、两个 remote ID、两个详情弹窗**；
> - Planner 侧**零图片数量语义**；可见范围是 `heading 'September'` +
>   `heading '2026'` 两条，不是 "start - end" 串。
>
> **所以缺的东西不是没录到，是那个 UI 上不存在 —— 重录一百遍也不会有。**
> 逐条判读（每条都带快照编号）在 **`docs/PROBE_FINDINGS_20260901.md`**。
> ⛔ **动 `publish/evidence.py` 或 `business_suite.py` 之前必读那份**，
> 否则很容易把已经按现实校准过的判据"改回"想象中的样子。
>
> ### 🎯 如果你是被派来接着干的新会话，先看这五件
>
> 1. **先读 `docs/GO_LIVE.md`**，再读 `docs/PROBE_FINDINGS_20260901.md`。
>    前者是操作顺序，后者是真实 UI 事实。本文件是背景与禁令。
> 2. **单目录单分支**：只有 `main`（+ 安全网 ref `backup/pre-merge-2026-08-31`），
>    远端只有 `origin/main`。当前提交用 `git log --oneline -1` 看
>    ——**这里不写死 hash，写了下一次提交就过期。**
> 3. **生产闸现在是开的**，`scripts\run_publish_post.bat ... --submit`
>    会**真的点提交**。查一眼：`scripts\run_probe_signals.bat --status`。
> 4. **动 `publish/` 或 `core/chrome.py` 之前读 `CODE_REVIEW.md` 第 17 + 18 节**
>    （CR-63 ~ CR-67）。那五条**全部是用户真机跑出来的，没有一条是审查看出来的**，
>    而离线测试当时全绿。共同教训写在 **18.9**：**mock 掉的边界就是没被测到的边界。**
>    这一轮又验证了一次：契约被推翻的那五条，离线夹具全都"验过"。
> 5. **外部动作顺序不能改**：填 14 个 UI 上限 → G8 `--submit` 并取消 →
>    同项复跑 → activate → 切 assisted → 用户显式安装任务。
>    ⛔ 不替用户提交测试帖、取消排期、激活或安装任务。
>
> 📌 **一句话现状：这个项目目前仍然没有任何东西在自动跑。**
> 计划任务至今没有安装。所有的幂等、断点、告警、降级都写好了也测过了，
> **但没有任何东西会在没人的时候运行它们**。
> 见 `docs/PIPELINE_PLAN.md`——那是唯一一份**跨组**文档，
> 回答"这些段怎么连成一条不用人管的线"。
>
> ### 🧰 这一轮新增的工具（记住它们，别重复造）
>
> | 命令 | 干什么 |
> |---|---|
> | **`pipeline.py preflight`** | **⭐ 最该记住的一条。** 还差什么 + **激活后每天会发生什么**（只读，用生产同一套判据在真实归档上预演）。`GO_LIVE`/`HANDOFF` 里的表会过期，它不会 |
| `pipeline.py preflight --days 180` | 顺带把 `price_map` 缺的行按 TOML 打出来，业务照着填 |
| `run_probe_signals.bat --status` | 三道生产闸现在是开是关，关着差哪条 |
> | `run_probe_signals.bat --check <dump>` | 录完 30 秒体检：这份 dump 能不能解锁生产提交 |
> | `run_probe_signals.bat --report <dump>` | **推不出来时看它**：摊开 dump 里真实存在的语义 |
> | `run_probe_signals.bat --emit <dump>` | 推导 → 回查 → 写 `publish/signals_backfilled.py` |
> | `probe_publish.py --fill-notes <dump> --set-note K=V` | 非交互填观察项，不用走 20 问 |
>
> ⛔ `publish/signals_backfilled.py` 是**生成文件**，不要手工编辑
> ——下一次 `--emit` 会整份覆盖。
>
> ---
>
> <details>
> <summary>历史：2026-09-01 第三/四轮（已被上面覆盖，只用于理解当时为什么那么做）</summary>
>
> - 第四轮：`probe_publish.py` 升级 schema v2；G6/G6c 实现单次提交、成功等待、
>   Planner 回读与追加式五态 journal；G9 assisted 实现 activate/run/approve/status。
>   当时旧 dump 证明不了因果链，所以 `SIGNALS` 保持空、`--submit` 在碰浏览器前失败闭合。
>   **那个状态已在第五轮解除。**
> - 第三轮：G1 的 24 条 dump 人工读完并回填 `publish/selectors.py`（12 条 composer 定位）；
>   G2–G5/G7 实现，链路跑到"提交前一步"；UI 时区定案为跟发帖设备本机时间走；
>   渠道勾选确认默认全选。
>
> </details>
>
> ---
>
> **历史（两条分支都已合并并删除，下面这段只用来理解当时为什么这么分）**
>
> **上一轮：用户答复到齐，K/G 两组划成并行分支**
>
> 🔀 **如果你是被派来做 K 组或 G 组的 Agent，只需要读三样**：
> 1. 你那份任务书（`docs/IMAGE_PLAN.md` 或 `docs/PUBLISH_PLAN.md`）**读完**；
> 2. 本文件的「八条→十一条禁止事项」与「三个最可能踩的坑」；
> 3. `IMPLEMENTATION_PLAN.md` 里**你那一组**的任务项。
>
> **两条分支都从 `docs/plan-image-publish` 开**：
> - K 组（图片德语化）→ **`feat/image-de`**
> - G 组（Business Suite 发布）→ **`feat/business-suite-publish`**
>
> **文件所有权表**在两份任务书里各有一份（`IMAGE_PLAN.md` 第 10 节 /
> `PUBLISH_PLAN.md` 第 12 节），内容相同。**动别人的文件之前先看那张表。**
>
> ---
>
> **上一轮：新增 K 组（图片德语化）并按新范围重写 G 组**
>
> 上一轮：Instagram 合作帖修复已用真实抓取端到端验证（当天新抓到的那篇 IG 帖
> 恰好就是合作帖，`owner=neakasa.global` / `coauthors=[neakasa.tech]`，配图也下成功），
> **C2/C4/C5 验收条件齐了并已合回 main**。
>
> **本轮：只写计划，没写代码，没发过任何付费请求。** 用户拍板了四件事，
> 其中一件推翻了第 0 节的既有决定（图内英文改为程序自动处理）。
> 新增两份任务书：**`docs/IMAGE_PLAN.md`（K 组）**、**`docs/PUBLISH_PLAN.md`（G 组）**。
>
> 剩余外部卡点：德语质量人工审校、全量翻译预算拍板、**G1 DOM 探查**、
> **图片 API 密钥与 DE 发布账号**（MANUAL_STEPS 第 10 步）。

---

## 你的任务

接手一个进行中的项目，按 `docs/IMPLEMENTATION_PLAN.md` 里的任务项继续实现、
调试、跑通。**A / B / J 三组已完成，归档数据是干净可用的**；
**C / D / E 三组也已落地**（C3/C6/C7、D3、E1/E2 完成并验收；
C2/C4/C5 与 E3 的注册卡在用户跑一次复测）。

**你的第一个任务取决于用户带来了什么**，见下面「现在卡在哪」。

**运行环境是 Windows**。所有 `.bat`、Chrome 探测、CDP 连接**已在 Windows 实机
跑通**，不再是未验证假设——这与上一版交接文件不同。

---

## 第一步：按这个顺序读

0. **`docs/GO_LIVE.md`** —— **想知道"现在该按哪个键"就只读这一份。**
   从当前状态到"不用人管"的全部步骤，每步带判据和过不了怎么办。
0b. **`docs/PROBE_FINDINGS_20260901.md`** —— 真实 Business Suite 长什么样，
   每条带快照编号。**动 `publish/evidence.py` 或 `business_suite.py` 之前必读**：
   那里记着五条"照直觉写就会错"的 UI 事实，都是被真实 dump 推翻出来的。
1. **`docs/IMPLEMENTATION_PLAN.md`** —— 唯一的进度真相源。背景、约束、
   关键事实速查、全部任务、依赖图、验收清单、故障速查表都在里面。
   **完整读完再动手**，尤其是第 1 节（关键事实）、第 2 节（禁止事项）、
   以及**附录 D 的同步记录**——那里记着每一次"计划说的和现实不一样"。
2. **`README.md`** —— 架构为什么长这样、怎么跑。
3. **`docs/MANUAL_STEPS.md`** —— 需要用户亲自做的步骤。
   **新增任何需要用户操作的功能，都要同步更新它**，否则用户手上的
   指南会和实现漂移。
4. **`core/parse.py` 和 `core/store.py`** —— 已实现的核心，你会频繁改动前者。
5. **`docs/CODE_REVIEW.md`** —— 已实现代码的审查记录（当前至 **CR-67**）。
   **动 K / G 的代码之前先读第 15 + 16 节**：
   第 15 节是 2026-08-31 对两条分支的同步只读审查，**15.6 是处置结果表**
   （CR-47 ~ CR-60 全部已修并带断言；CR-46/49 已随合并完成）；
   第 16 节是合并前的收尾审查——**16.1 那张表列了四处已复核通过的地方，
   不要再审一遍**，16.2 是新发现并修掉的 CR-61（发布侧漏了话题标签硬闸），
   16.4 是合并态在真实数据上的复核，**16.5 的 CR-62 是那个 `git status` 看不见的 `.bat` 裸 LF 坑**。
   其中 **CR-60** 仍然值得记住：`ZoneInfo("Europe/Berlin")` 在本机会直接抛异常，
   靠 `requirements.txt` 里的 `tzdata` 兜住，**不要改成写死 UTC 偏移**。
   **第 17 / 18 节（CR-63 ~ CR-67）全部是用户实跑撞出来的，不是审查看出来的**——
   离线全绿，而那几条路径在真实环境里从来没成功过。
   共同点写在 18.9：**mock 掉的边界，就是没有被测到的边界**。
   最贵的是 CR-64/66：recorder 全程零提示、走完 20 分钟才发现 dump 是空的；
   以及「按 Enter 停止记录」并没有真的停，直接死锁。
   **第 8 节的 CR-19（Instagram 合作帖）是目前最值得读的一节**，
   它一条推翻了三个曾被写进文档的"事实"；
   **接着读第 9 节（CR-20 ~ CR-24）**——那是同一个问题的另一半：
   把"这次修对了"变成"下次坏了能被发现"，并记着复核用的真实数字。
   翻译侧先读 CR-37/38（原 CR-25/26），再读末尾 CR-32~36：前者解释旧 Anthropic 事故，
   后者是当前官方 API / High thinking / 标签规则。**动 `translate.py` 之前必读。**
6. **`docs/TRANSLATION_PLAN.md`** —— DeepSeek 翻译的当前实现、业务操作和验收真相。
7. **`docs/IMAGE_PLAN.md`** —— K 组任务书（图内英文德语化）。
   **动 GPT-Image-2 之前必读第 2 节**：那里有三条会让请求直接 400 的事实，
   其中一条（`input_fidelity`）**与 openai SDK 的签名和 docstring 直接冲突**——
   照着 SDK 写就是错的。
8. **`docs/PUBLISH_PLAN.md`** —— G 组任务书（Business Suite 定时发布）。
   范围已按用户 2026-08-31 的决定收窄，**不要把批量补发做回来**。
9. **`docs/PIPELINE_PLAN.md`** —— L 组：**唯一一份跨组文档**。
   A~K 每组回答"这一段怎么做对"，它回答"**这些段怎么连成一条不用人管的线**"。
   - **第 14 节 = L0 实施细则**，命令、判据、46 种金额串全备好了，**照做即可**；
   - **第 15 节 = 多 Agent 共享工作区的协作纪律**（2026-08-31 实战教训），
     并行开工前必读；
   - **想改自动化程度、想砍掉某道确认闸之前，先读第 4 节和第 12 节**——
     那里写着哪些人工是承重墙、哪些是可以消除的，
     以及把自动化推过头会踩哪几条红线。

不要跳过第 1 步。计划里有大量"看起来可以优化、实际是保命设计"的地方，
不读会被你顺手改掉。

---

## 📜 历史：三个 Agent 曾在同一个工作目录里并行（已归位）

> **想知道现在是什么状态，看上面的「🧭 当前仓库形态」**，那一节是当前事实。
> 本节写的三个 worktree **已经全部归位**，不要照着它去建目录。
> 本节保留是因为它解释了 `wip/parallel-2026-08-31` 为什么长成这样、
> 以及那两处红是哪来的。

**2026-08-31，四条线同时在这一个 `FacebookScraper` 目录里工作**：
代码审查（已结束）、K 组、G 组、本会话（L 组规划）。

> ⚠️ **git 一个工作目录只能有一个 checked-out 分支。**
> 三个 Agent 各自以为在自己的分支上，实际上全都在改同一个工作区——
> **谁先 `git add -A`，谁就把另外两条线一起提交进去。**

**已经归位了，工作区当时是干净的。** 四条线被拆成四次带归属的提交：

| 提交 | 内容 | 状态 |
|---|---|---|
| `c35715d` | **代码审查 CR-41 ~ CR-45** | ✅ 该轮已结束（用户确认） |
| `ee43aca` | **G 组**：G0 完成 · G0b 落地 · G1 recorder 交付 | 🔄 进行中的快照 |
| `29784d2` | **K 组**：`localize_images.py` 主干 + K8 审校接入 | 🔄 进行中的快照 |
| `3047c65` | **三条线共同改到的文件**（`config.toml` / 三份共享文档） | — 无法按线拆分，归属记在提交说明里 |

分支：`wip/parallel-2026-08-31`（**当前 checked-out**）。
`main` 停在 `3736303`（纯文档，绿的），**WIP 没有进 main**——
项目规则「main 永远是最后一个已验收的可用状态，半成品不进 main」。

### ⛔ 当前有两处红，都在 K 那条线上（K 组 Agent 尚未修完）

```
tests_schedule.py   FAIL  scripts/run_images.bat 有裸 LF
tests_translate.py  FAIL  tests_translate.py:676 NameError: name 're' is not defined
```

1. **`scripts/run_images.bat` 是纯 LF（0 个 CRLF）** —— 踩全局红线 7。
   ⚠️ **`.gitattributes` 的 `*.bat text eol=crlf` 只在下次 checkout 时纠正工作副本，
   而测试查的是当前工作副本**，所以必须由写文件的一方直接写 CRLF 字节。
2. `tests/tests_translate.py:676` 少一个 `import re`（K8 的图片并排断言）。

**我没有替它修**：那两个文件 K 组 Agent 正在写，并发改同一文件会丢更新。

#### ✅ 已核实（2026-08-31 晚）：**两处红都已在 `feat/image-de` 上修好，但 `wip` 上仍然红**

不用再猜"很可能已经被修好了"，实测结论是：

| | `wip/parallel-2026-08-31` | `feat/image-de` |
|---|---|---|
| `scripts/run_images.bat` | ⛔ 0 CRLF / 13 裸 LF | ✅ 13 CRLF / 0 裸 LF / 无 BOM / 纯 ASCII |
| `tests_translate.py:676` | ⛔ `NameError: 're'` | ✅ 通过 |
| 全套测试 | 2 红 | ✅ **17 套全绿** |

`feat/business-suite-publish` 同样 **17 套全绿**。

**所以不要去修 `wip` 上这两处**——它们会随 `feat/image-de` 合进来时一起消失。
这也是 ⛔ 那一节说"不要先把 `main` 快进到 `wip`"的又一个理由：
**`wip` 是红的，`main` 永远只收绿的。**

### 接手时遵守（在原有五条基础上加了两条）

1. 先跑 `git status --short`、`git log --oneline -8`、`git diff --check`。
2. ❌ 不要 `git checkout` 切分支、不要 `git reset --hard` / `stash` /
   整份覆盖文件来"清理"工作区。
   需要让 `main` 前进时用 **`git branch -f main <已验证的提交>`**——
   它**不碰工作区**，前提是 `main` 未被 checkout 且是目标提交的祖先。
3. **只 `git add` 自己所有权表里的文件，永远不要 `git add -A` / `git add .`。**
4. 修改与目标重叠的文件前先看完整 diff；只处理明确问题。
5. 收尾必须跑**全部** `tests/tests_*.py`，不能只跑自己改的套件。
6. Windows `.bat` 必须纯 ASCII、CRLF、无 BOM；`tests_schedule.py` 有字节级断言。
7. ⚠️ **别人正在写的文件不要碰**，哪怕只是修一个 import——并发写会丢更新。
   看到它坏了，**记进本文件，不要顺手改**。

### ✅ 更正：`git worktree` 这条路实际走通了（2026-08-31 晚）

上一版这里写的是"走不通"，**理由只对了一半**。原话是：

> `git worktree` + 每个 Agent 一个独立目录 —— **不能靠 junction 共享 `archive/`**：
> `core/store.py::assert_physical_direct_path` 会**主动拒绝**
> symlink / junction / reparse point，`Archive()` 一构造就抛异常。

**"不能靠 junction 共享 `archive/`"这半句仍然成立。**
但结论错了——**不共享 `archive/` 就行**。当天两条线实际的做法是：

```
FacebookScraper           wip/parallel-2026-08-31   （完整 archive/，主工作区）
FacebookScraper-image-de  feat/image-de             （只复制 manifest/translated 做夹具）
FacebookScraper-publish   feat/business-suite-publish（不需要 archive/）
```

离线测试全部用 `tempfile` 夹具，**本来就不读真实 `archive/`**：
两条分支各自 17 套测试全绿，`config.toml` 也自动合干净了。
**所有权表 + 独立 worktree 的组合是有效的**，
上一轮的混线是"三个 Agent 一个工作目录"造成的，不是并行本身的问题。

⚠️ **但真实数据验收仍然必须回主工作区做**：`--estimate`、`--dry-run`、
K9/G8 都要读完整 `archive/`，worktree 里的夹具会给出误导性的 0。

### ⛔ 合并顺序：先落 feature 分支，**不要**先把 `main` 快进到 `wip`

**这一条不看会多出十二个冲突文件。** 详细实测见 `CODE_REVIEW.md` **CR-46**。

两条 feature 分支都从 **`9ea342c`** 开出——那比 `main`（`3736303`）**还早**，
所以下面这两件事都是**预期的，不是"main 被人动过"**：

1. `git merge --ff-only <分支>` **必然失败**。两份任务书里写的 `--ff-only`
   在这里做不到。**不要 `-f`，改用普通 merge。**
2. `wip/parallel-2026-08-31` 里的 `29784d2` / `ee43aca` 是这两条线**更早的快照**
   （同血缘旧版本）。`main` 一旦先快进到 `wip`，那两份旧版就进了主干，
   feature 分支再合就变成整文件 **add/add** 对撞。

`git merge-tree` 实测的冲突数：

`git merge-tree` 实测（**2026-08-31 晚，两条分支修完之后重测**）：

| 合并 | 冲突 |
|---|---|
| `main ← feat/image-de` | **1**（`IMPLEMENTATION_PLAN.md`） |
| `main ← feat/business-suite-publish` | **2**（+ `MANUAL_STEPS.md`） |
| `feat/image-de ← feat/business-suite-publish` | **3**（三份共享文档） |
| `wip ← feat/image-de` | **6**（含 `localize_images.py` add/add） |
| `wip ← feat/business-suite-publish` | **10**（含 `core/chrome.py`、`config.toml`） |

⚠️ **冲突全部是共享文档，代码零冲突**（`config.toml` / `requirements.txt` /
`translate.py` 都自动合干净）。三份文档按项目约定**两段都保留，不许二选一**。
合并态已用一次性 detached worktree 实跑验证：**18 套测试全绿、代码里零冲突标记、
K→G 的 `images_de.jsonl` 交接契约双向成立**。

**推荐路径**：两条 feature 分支先合进 `main`（各只需解一个文档冲突），
然后从 `wip` 上把**只属于主干**的两笔摘过去：

- `c35715d`（CR-41~45）—— 只碰 `core/capture.py` / `core/store.py` /
  `routes/delta.py` 及其测试，**两条 feature 分支一个都没碰**，cherry-pick 干净；
- `3047c65` 的 `config.toml` 部分 —— 它带着 **`[pipeline]` 整段配置**
  与 `[publish]` 段末的 TOML 子表警告注释。
  ⚠️ **实测这两样只存在于 `wip`**，不摘过来 L0b/L0c 一开工就踩空（CR-49）。

### 📋 文件所有权表漏了第四条线，已补

两份任务书里那张表当时只设想 K / G 两条线，把
`routes/delta.py` / `core/store.py` / `core/capture.py` 标为「三组都不许动」。
**但代码审查线的职责恰恰就是修主干**，CR-41~45 改的正是这三个文件。

**这不是越界，是所有权表的疏漏。** 正确的划法是四条线：

| 线 | 分支 | 可以动 |
|---|---|---|
| **主干修复 / 代码审查** | `fix/*` 或直接 `main` | **主干任何文件**（`core/**`、`routes/**`、`translate.py`），但**不碰 `publish/**`、`localize_images.py`** |
| K 组 | `feat/image-de` | `localize_images.py`、`prompts/image_de.md`、`scripts/run_images.bat`、`tests/tests_localize_images.py`、`translate.py` 的 `run_review`、`config.toml` 的 `[image]` |
| G 组 | `feat/business-suite-publish` | `publish/**`、`tools/*publish*`、`scripts/*publish*`、`core/chrome.py`、`core/config.py`、`tests/tests_chrome.py`、`tests/tests_publish.py`、`config.toml` 的 `[publish]` 标量键 |
| L 组 | `feat/pipeline` | `pipeline.py`、`config.toml` 的 `[pipeline]` 与 `[publish]` 的三个子表、`docs/PIPELINE_PLAN.md` |

⚠️ **主干修复线与 K / G 会在 `translate.py`、`config.toml` 上撞车**——
2026-08-31 就撞了（CR-45 与 K8 混在 `translate.py` 里，拆不开）。
**所以更要串行。**

---

## 🧭 当前仓库形态（2026-08-31 收尾之后：**单目录、单分支**）

**曾经的三个 worktree 已经全部归位。** `git worktree list` 现在只有一行：

| 物理目录 | 分支 | 说明 |
|---|---|---|
| `FacebookScraper` | `main` | 唯一的工作目录，与 `origin/main` 同步 |

- **本地分支只剩两条**：`main` 与 `backup/pre-merge-2026-08-31`
  （后者指向合并前的 `wip/parallel-2026-08-31`，是一条不占工作区的安全网 ref；
  确认不再需要之后可以 `git branch -D` 删掉）。
- **远端只剩 `origin/main`。**
- K 组与 G 组的代码**已经全部在 `main` 里**，两条 feature 分支和 `wip` 都已删除。

### 这一轮做完的事（K/G 合并落地）

`main` 从 `3736303` 走到 **`9f8c1bf`**，中间只有五笔是新的：

```
9f8c1bf chore(merge): 补回只存在于 wip 的三份文档与 [pipeline] 配置
e032b0b Merge branch 'feat/business-suite-publish'
86a56f9 Merge branch 'feat/image-de'
f111057 fix(review): 主干二次审查 CR-41 ~ CR-45（cherry-pick 自 c35715d）
0377d1f fix(publish): 发布前补上话题标签硬闸（CR-61）
```

合并顺序按 CR-46 走的：**先落两条 feature 分支，再把 `wip` 上只属于主干的东西摘过来**。
`wip` 上另外四笔（K/G 的旧快照 `29784d2` / `ee43aca`、`3047c65` 对共享文档的旧进展行、
上一版 HANDOFF `cb9e01f`）**全部丢弃**，因为都已被更新的版本取代。

**合并态验证（一条没省）**：18 套 / **1108 项**测试全绿、`compileall -q .` 干净、
`git diff --check` 干净、代码里零冲突标记、`tomllib` 解析通过、
8 个 `.bat` 全部 0 裸 LF / 无 BOM / 纯 ASCII。

### ⚠️ 合并之后在真实数据上复核过的（feature worktree 里做不到，因为它们没有 `archive/`）

这些**不是**离线断言，是拿真实归档跑出来的：

| 复核 | 结果 |
|---|---|
| `localize_images.py --dry-run --post-id ×3` | **11 张待处理**（FB 5 + IG 5 + IG 1），与合并前一致 |
| `localize_images.py --estimate --all-history` | 正常收尾、**零 traceback**（CR-52 的回归点） |
| 三道费用闸 | `--all-history` 真实运行退出码 **2**；`--latest-posts 4` 被拒；`--confirm-all-history-cost` 不能单独用 |
| `tools/compose_publish.py --post-id ×3` | **3 篇全部组装成功、零拦下**（G0b 验收在合并态复现） |
| `tools/compose_publish.py --strict` | 3 篇全部正确失败闭合（G1 未完成） |
| **K→G 所有权契约** | 7 条断言全过：程序产出双向认得；字节被改过两边同判人工；`01.jpg` + `01.png` 并存时 G 选人工版（CR-48 的场景）；两个都非程序产出时 G 失败闭合 |
| **K8 并排审校**（在真实数据的临时副本上跑 `run_review`） | 7 条断言全过：并排表、缺图逐张点名、逐类清单张数与配对数一致、`text_de.txt` 派生副本数一致、上一版已备份 |
| **CR-56 降级** | 把 `[image]` 配置弄坏之后，F 组审校清单**照常生成**，只是不打形变/放大告警 |
| **CR-60 tzdata** | `tzdata 2026.3` 已装；2026 年两个夏令时切换日的偏移四次全对 |
| 浏览器隔离闸 | 真实 config 上 9222/`.fbscraper-chrome` 与 9223/`.fbscraper-publish` 互不相同 |
| `uv pip check` | 24 个包全部兼容 |

**以上全程零 API 调用、零费用、零社媒访问，真实 `archive/` 一个字节都没动过。**

### ⚠️ 一个只在 Windows 上出现、而且 `git status` 看不见的坑（这一轮真的踩了）

**切分支之后，`scripts\*.bat` 的工作区副本可能带着裸 LF，而 `git status` 是干净的。**

- `.gitattributes` 写着 `*.bat text eol=crlf`，所以**仓库里的 blob 是 LF、检出时才转 CRLF**。
- 但 clean filter 会把 CRLF 和 LF **归一成同一个 blob**，
  于是工作区里那份即使是裸 LF，`git status` 也报干净——**git 看不见这个漂移**。
- 裸 LF 的 `.bat` 在 `cmd.exe` 里会整行误解析，表现为
  `'xxx' 不是内部或外部命令`。
- **`tests_schedule.py` 会抓到它**（这一轮就是它报出来的）。
  修法是让 git 重新物化这个文件：

  ```bash
  rm scripts/run_images.bat && git checkout -- scripts/run_images.bat
  ```

  **不要手工改字节，也不要去改测试。** 那条断言正是为这件事写的。

---

## 🎬 G1 已收口（2026-09-01 第五轮）

**`state/` 下现在只留三份 dump，每一份都有明确用途**（其余失败尝试已删）：

| dump | schema | 交互/快照 | 用途 |
|---|---|---:|---|
| `publish_probe_20260901_054226_378622.json` | **v2** | 56 / 71 | ✅ **生产证据来源**。`config.toml` 的 `ui_probe_dump` 指向它，`publish/signals_backfilled.py` 从它推导 |
| `publish_probe_20260831_222850_347719.json` | v1 | 24 / 0 | `selectors.py` 里 **12 条 composer 定位**的来源，`tests_publish.py` 会逐条回查。**别删** |
| `publish_probe_20260831_195237_008047.json` | v1 | 63 / 0 | 旁证：唯一录到过「Meta 的 Photo/video 按钮背后是真的 `<input type=file multiple>`」。**别删** |

⚠️ 那两份 v1 `finished_at`/`sequence` 有硬伤，**过不了严格模式，这是对的**；
它们只用来给 `Locator` 做回查，不参与 G6/G6c 生产证据。
⛔ **不要手工改序号把它们"修好"**——那两条校验存在的意义就是证明记录没丢过。

### 录完之后该跑什么

```bash
scripts\run_probe_signals.bat --check state\publish_probe_<时间戳>.json --caption "正文里的一小句"
```

30 秒告诉你这份录制成不成立。推不出来时用 `--report` 看 dump 里**真实存在**什么
——这一轮就是靠它发现"缺的东西 UI 上根本不存在"，而不是又去重录一遍。

观察项随时可补，不用重录：

```bash
.venv\Scripts\python.exe tools\probe_publish.py --fill-notes state\<dump>.json --set-note KEY=VALUE
```

⚠️ IG 的四类上限与排期窗口**只能人亲眼看 UI 得到，程序不许猜**（全局红线 5）。
不确定的**宁可空着**：空着只是继续挡住，填错是放行一个错的。

### ✅ 2026-09-01 第二轮：回填做完了

`publish/selectors.py` 现在是一张**带证据的登记表**（`Locator` / `Gap`
两个 dataclass），不是一堆裸字符串：

- **12 条 composer 定位**，每条写清「对应哪一步 / 出自这份 dump 的第几条 /
  什么信号说明它失效了」；
- **2 条旧版后台的旁证**，明确标注**不许拿到 composer 上用**
  （`locator_for()` 会直接 `KeyError`）；
- **8 条缺口登记**，每条写清「为什么没有 / 挡住了谁 / 怎么补上」。

**并且回填不再靠自觉**：`publish/evidence.py` 把每条定位**回查**到它自称的
dump（role + 可访问名 + 稳定属性都要对上），`tests_publish.py` 第 [5] 节
把它变成断言，本机 **14/14 全部命中**。⛔ **编出来的定位会当场被打回。**
⚠️ dump 不进版本库，别的机器上是"跳过"不是"通过"——两者分开打印。

**三处"dump 里没有、但不用重录也能做对"的**（详见 `IMPLEMENTATION_PLAN` 附录 D）：
composer 的 file input 走 **Playwright file chooser 通道**（连选择器都不用）、
小时 spinbutton 按**排除法**（个数不是 3 就失败闭合）、
缩略图**不假装验过**（作为提示交回给人）。

---

## 🚦 接下来做什么

**操作顺序在 `docs/GO_LIVE.md`，这里只写"不许越级"那条纪律。**

代码侧 G6/G6c/G9 与证据契约都已完成，**不要再重写状态机、另建发布队列，
也不要把 G6c 的判据"改回"一张卡片带全部元数据的样子**——
那个形状被真实 dump 推翻过一次（`docs/PROBE_FINDINGS_20260901.md`）。

前一步没过不得越级：
填 14 个 UI 上限 → `ui_constraints_verified = true` → G8 `--submit` 并手工取消 →
同项复跑（必须零浏览器）→ `pipeline activate --g8-verified` → 切 `assisted` →
用户显式决定装不装计划任务。

## 🔭 自动定时发布链路：当前边界

```
归档 → 跨平台对账 → 按 ID 翻译/调图 → 离线硬闸 → 待确认
                                                   ↓ approve
Composer 准备 → 单击提交 → 成功信号 → Planner 回读 → scheduled
```

- `manual` 只对账；`assisted` 的 `pipeline run` 最多做到待确认，**不接触发布浏览器**；
- `approve` 才逐篇调用显式 `--submit`，首个浏览器或提交状态不明确即停止整批；
- 30h 配对窗口未闭合时零付费；部分 source ref 已 scheduled 而 exact counterpart
  迟到时写 `late_scheduled_overlap`，人工 approve 只扩展覆盖证据、零浏览器操作；
- 所有真实付费先写 `paid_requests.jsonl` 的 started，响应 usage 先于业务产物落盘；
  output_rejected 仍计费，uncertain/usage_unknown 会停止后续全部付费；
- `prepared`、`submit_ambiguous`、`submitted_unverified`、`failed_pre_submit`
  都不算发布成功，只有自动回读或人工证据结转后的 `scheduled` 才算；
- 德国排期固定 10:00/17:00，UI 使用美西时区。两地 DST 独立换算；若德国
  10:00 落到美西秋季回拨的重复 01:00，单帖命令失败闭合，批量分配跳到当天 17:00。

### 渠道证据的边界（2026-09-01 按真实 UI 校准，**不要改回去**）

- FB/IG 勾选控件**不读也不点**，依赖 composer 默认全选（用户实测确认）；
- **提交前只能证明 Facebook**：composer 上根本没有 IG 帐号名，
  只有 `img 'Instagram'` 一个图标。目标主页由预览抬头那条 `heading h2`
  按**完整值**比较证明（多一个词就是另一个主页）；
- **IG 由提交后回读证明**：Planner 上 FB 与 IG 是两个独立对象、
  两个 remote ID、两个详情弹窗，各自点开读 `ID: <数字>` + 渠道标记 + 账号 token；
- **两个渠道少任一个都不能记 `scheduled`** —— 这条保证没有放松，
  只是确认时机从提交前挪到了提交后；
- **图片数量的硬闸不在 Planner 侧**（那里零数量语义），
  由 composer 上传后数缩略图保证。

---

## 🧨 这一轮踩到的六个坑（CR-63 ~ CR-68，**全是真机跑出来的**）

**一条都不是审查看出来的，而当时离线测试全绿。** 记在这里是因为它们
会以同样的形状再来一次：

| # | 坑 | 一句话教训 |
|---|---|---|
| **CR-63** | profile 归属核对的 `timeout=5`，而本机要 7–9 秒 → **每次都超时**，正确的环境被报成"你开错了浏览器" | **没在目标机器上量过的超时值，和写死的选择器是同一类东西** |
| **CR-64** | 装监听器失败被 `except Exception: continue` 吞掉 → 一条没记、全程零提示，用户走完 20 分钟才发现 dump 是空的 | **`except: continue` 用在"装设备"这步，等于把"没装上"变成"装好了但什么都没发生"** |
| **CR-65** | 监听器还是会不明原因消失，而 scratch 环境**怎么都复现不出来** | 复现不出来就别继续猜原因；每 4 个 0.5 秒 tick 发起回读+自愈，慢页单飞合并，不承诺坏 CDP 的完成时延 |
| **CR-66** | 「按 Enter 停止记录」只停了巡检、没停记录 → 问答和记录抢同一把锁，**死锁** | 「停止」必须是真的停：同步 admission 关闸、硬 deadline、先撤 callback/init-script/binding 再清 DOM listener/mask、detach 全部 session；专用 runner 关环不做无界 gather |
| **CR-67** | 序号用只增不减的计数器，一次失败就**永久空掉一个号** → 内容完好的 dump 被严格校验判死 | **可失败的操作不许先占号**，序号由已落盘条数推 |
| **CR-68** | Playwright 每张截图先耗满 8 秒，CDP 后备虽成功但队列已落后 70 秒；阶段截图被后续 Planner 覆盖，Enter 的 final 也排在队尾 | CDP 走主路径；每页语义采样单飞合并；URL/Boost/Planner 独立截图；截图前在锁内重采语义；Enter 用单一 deadline 收尾 |

⚠️ **另外两条一般性的**：

- **点在页面空白处（`<body>`/`<html>`）和敏感输入框本来就不记录**，那是设计。
  排查"为什么没记上"时**先证明你点到了真东西**——我差点把这个正确的过滤当 bug 修。
- **改文档要用"临时文件 + 原子替换"**。这一轮我用 `write_text` 直接覆盖，
  被一个孤立代理字符崩在半路，把 `CODE_REVIEW.md` 截成了 0 字节（靠 git 恢复）。
  `ProbeRecorder._write()` 一直是先写 `.tmp` 再 `replace` 的，**那个选择是对的**。

---

## 现在卡在哪（读完这段就知道该干什么）

**只剩一件事：重跑 G8，把留痕落成 `scheduled`。** 代码侧与标定侧全通。

| 项目 | 当前状态 | 下一动作 |
|---|---|---|
| G1 v2 | ✅ `publish_probe_20260901_054226_378622.json` 完整通过 v2 契约 | — |
| G6/G6c 证据 | ✅ 五件全部推导 + 回查通过，四道闸全开 | — |
| 20 个观察项 | ✅ **全部填完**（2026-09-01 用户实测 14 项） | — |
| `ui_constraints_verified` | ✅ `true`；`--latest 3 --strict` 实测 2 篇组装成功 | — |
| 两张业务表 | ✅ 都已填（`price_map` 是**恒等映射**，见 `config.toml` 里的说明） | — |
| 远端测试排期 | ⚠️ **2026-09-01 22:39 又排了一条**（演示用，见下方 CR-77） | 人工去 Planner 确认后删除，再结转留痕 |
| **G8** | 🟡 **第八次：提交成功、成功信号不匹配**（`submit_ambiguous`） | 见下方 CR-77；先按事实结转，再重跑 |
| 幂等复跑 | ⬜ | 拿到 `scheduled` 之后，同项原样重跑，必须零浏览器 |
| G9 / 计划任务 | ⬜ 仍 `manual`、未激活、未安装 | 拿到 `scheduled` → activate → assisted → 用户决定装不装 |

### 🧨 G8 真机跑了七次，撞出 CR-70 ~ CR-76（**离线全程 100% 绿**）

前五次连提交都没走到，第六次提交成功但回读没证明。
**六条全部是"假页面比真页面简单太多"**，而且全都撞在**时间**上：

| # | 表面 | 真因 | 夹具为什么测不到 |
|---|---|---|---|
| 70 | 找不到主页 heading | 证据在图片上传**之后**才存在 | 假页面上元素一直都在 |
| 71 | 正文多一个换行 | 逐字符按键招出 `@`/`#` 的 typeahead | 假键盘不区分按键与 `insert_text` |
| 72 | 时刻差一个字符（1→11） | Ctrl+A 在**分段输入**上选不中 | 假 `fill()` 总是生效 |
| 73 | IG 排到了"立刻" | **每个渠道各有一套**排期控件 | 假页面只造了一套 |
| 74 | 排期控件读到 0 个 | `.all()` **不等待** | 假 locator 的命中结果被冻住 |
| 75 | 刚发的帖回读不到 | 就绪信号**早于**数据 | 假页面没有"加载中"这个中间态 |
| 76 | 定时开关点了没开 | **点击返回 ≠ 状态已翻**（React 受控开关异步） | 假开关点一下立刻翻 |

⚠️ **73 是后果最严重的一条**：内容全对、主页全对，而 **Instagram 那一篇
被排到了当天此刻 = 立刻发出去**。截图上 FB 是 `Sep 10`、IG 是 `Sep 1 06:23 PM`。

⚠️ **74 和 75 是我在修 73 的过程中自己引入/暴露的。** 记着这个形状：
**把 `.first` 换成 `.all()` 时，等待语义会默认消失。**

**这一轮真正值钱的改动不是那几行修复，是把假页面补到接近真实 SPA**：
元素会晚出现、控件是分段的、键盘有两种通道、一个渠道一套控件、
locator 是惰性的、页面有"骨架好了但数据没好"的中间态。
每一条都按规矩**先拿掉修复跑一遍确认它真的红**才留下。
详见 `CODE_REVIEW.md` 第 20~25 节。

### 🧨 CR-77：成功信号**至少有两种变体**，G1 只录到了一种（2026-09-01 22:39 第八次真机）

第八次 G8 跑在另一篇帖子上（`3965025107383038890`，IG），
`attempt_id = d9853906-57d9-437c-9f71-4ed72d103a81`，排期 `2026-09-09T10:00+02:00`。
离线闸、G2–G5 回读全过，提交点下去了，然后：

```
submit_ambiguous · 点击后没有等到已录证的成功信号；绝不自动重试：
Locator.wait_for: Timeout 30000ms exceeded.
  - waiting for get_by_role("heading", name="Your post is scheduled").first
```

**但截图证明帖子其实排上了。** `..._submitted.png` 上是另一个弹窗：

| | |
|---|---|
| 代码在等（G1 录到的） | heading `"Your post is scheduled"`（带 Boost 推广模块） |
| 这一次实际弹出 | heading `"Save time by scheduling another post now"`，按钮 `Maybe later` / `Schedule another post` |

同一张截图上：Facebook 与 Instagram 两栏都停在 `Sep 9, 2026 01:00 AM`，
`Schedule` 按钮已置灰。**Meta 的提交后弹窗不止一种**，
`publish_probe_20260901_054226_378622.json` 只录到了其中一种。

⚠️ **不要把这条当成"信号选择器写错了"去修。** 现在的行为是对的 ——
信号不匹配就报 `submit_ambiguous` 并停止，没有谎报成功。
要做的是**补录第二种变体**：按 G1 流程再录一次提交，把这个弹窗
录进 dump，然后让 `probe_signals.py` 从 dump 机械推导出"多选一"的成功信号。
⛔ 在补录进 dump 之前，不许手写第二个选择器 —— 那就绕过了证据闸。

⚠️ 这条同时说明 **`submit_ambiguous` 的设计是对的**：
它不是"失败"，是"我不知道"。远端此刻**很可能已经排上**，
所以既不能自动重试（会排两遍），也不能记成功（可能没排上）。
只能人工去 Planner 看一眼，然后
`--mark-scheduled` 或 `--mark-not-scheduled` 结转。

### ⚠️ 定时上限不是"多少天"，是"本月最后一天"（2026-09-01 实测）

composer 的日期选择器**不允许跨月**。所以：

- `schedule_max_ahead_seconds` 里那个 31 天只是**绝对天花板**，
  ⛔ **不要当成"可以排 31 天"**；
- 真正的判据是 `publish/compose.py::_validate_schedule_month`，
  外加 `pipeline_assisted.next_slots()` 到月末会**返回少于 count 个槽**
  （调用方必须自己判，approve 会整批不提交）；
- **月份在 UI 时区里判**，不是柏林、不是 UTC。柏林 10-01 06:00 在美西还是
  09-30 —— 写死柏林两个方向都会判错，两边都有断言。

⛔ 不替用户提交/取消测试排期，不激活，不安装任务。

### 📽 工作区里多了一个 `Demo/`（2026-09-01，客户现场演示用）

`D:\VSCodeWorkspace\Facebook\Demo\` —— **在本仓库之外**，不在 git 里，
不参与本仓库的任何测试。它只做一件事：**按顺序调用生产命令**，
一行生产逻辑都没有复制过去。`git status` 不会因为它变脏。

```
demo.bat <步骤>     00 系统现状 / 01 抓取 / 02 翻译 / 03 起图片任务 /
                    04 硬闸 / 05 图片揭晓 / 06 组装 / 07 定时发帖 / 08 留痕
demo.bat board      本地看板（只读归档字节，客户那块屏用）
demo.bat clear|99   清演示视图 / 连产物一起退回演示前
```

对本仓库有影响的只有两点，接手时要知道：

1. **第 07 步会真的排帖子**，用的就是 `run_publish_post.bat --submit` 那条路径。
   上面 CR-77 就是它跑出来的。
2. **演示会在归档里留下真实产物**：`translated.jsonl` / `images_de.jsonl` /
   `posts/<帖>/text_de.txt` / `media_de/` 各多几条。
   `demo.bat 99` 会把演示帖的这些退回去（先备份），
   但**它不碰 `state/published.jsonl`** —— 留痕只能按事实结转，不能删。

⚠️ **生产闸现在是开的**：`run_publish_post.bat ... --submit` 会**真的点提交**。
`scripts\run_probe_signals.bat --status` 一眼看清。

### 历史第三轮快照（以下内容被上面的第四轮状态覆盖）

**A / B / J / C / D / E 六组已落地并合回 `main`。**
**K 组与 G 组的代码也已经全部在 `main` 里**，**L 组的 L0b/L0c 也落地了**
（`d97b413`，19 套 1244 项全绿），形态见上面「当前仓库形态」。

**抓取侧已经没有不依赖真实访问的活了。有业务价值的是 F → K → G 这条线。**
**2026-09-01（第三轮）：这条线第一次整段打通到"提交前一步"。**

- **F** 不再是瓶颈（作用域参数补上之后，要发的帖按需翻即可）；
- **K** 真跑过两张；第一张用户确认完全可用，第二张待复核；
- **G** 的 `selectors.py` 已按真实 dump 回填，G2–G5/G7 已实现，
  `scripts\run_publish_post.bat` 能把一篇德语帖填进 Business Suite 并停在提交前。

**唯一还断着的一环是「提交那一下」**，缺的是提交按钮与成功信号——
补法与整条自动链路的剩余任务写在
**「🔭 自动定时发布链路：还差什么」**（本文件下半部分）。

卡在用户手上的三件：**跑一次真实准备**、**装计划任务**、
**重录探查走到提交**。⛔ **这三件不要替用户按。**

两份任务书分别是 `docs/IMAGE_PLAN.md` 与 `docs/PUBLISH_PLAN.md`，
任务项在 `IMPLEMENTATION_PLAN.md` 的 K 组 / G 组。
**动手前先读任务书**，里面记着若干条「照直觉写就会错」的事实。

| 卡点         | 状态                                                                     | 阻塞了谁            |
| ------------ | ------------------------------------------------------------------------ | ------------------- |
| ~~A / B / J 组~~ | **全部完成**：回填跑通、解析器修好、归档重建、每帖一个文件夹          | —                  |
| ~~C3 / C6 / C7~~ | **完成并已实机验收**（FB 那半边跑通了）                              | —                  |
| ~~D3 / E1 / E2~~ | **完成**                                                             | —                  |
| ~~D4~~       | **完成**：第四项检查「丢弃了已知合作方的帖子」，离线双向验证过         | —                  |
| ~~C2 / C4 / C5~~ | **完成并已实机验收**（2026-08-31）：真实增量各新增 1 篇，**媒体下载第一次被真正触发**（FB 5 图 / IG 1 图，全部非空）| —                  |
| ~~用户复测~~ | **已完成**：`MANUAL_STEPS.md` 第 8 步全部跑通，含离线四步与真实一次 | — |
| **E3 注册**（= L0a） | 工具与离线验收完成，**故意没注册**——装上就开始每天真实访问。⚠️ **2026-09-01 实测三个任务（含新的 `FBScraperAlive`）全部未注册：整个项目现在没有任何东西在自动跑** | **全部自动化** |
| **L 组**（已在 `main`） | ✅ **L0b `pipeline status`**（只读对账，`scripts
un_pipeline.bat`）与 ✅ **L0c 死人开关**（独立计划任务 `FBScraperAlive`）已完成。⬅️ **L0a 装计划任务轮到用户**（第 9 步，已解锁）。L0d 价格表排在 G1 之后（`PIPELINE_PLAN` 10.1）。⚠️ `[pipeline]` 里 `autonomy` 与两个 budget 键**目前仍没有代码读**，`status` 会如实打印"还没接上" | 自动化程度 |
| **F 组**     | 新接口已跑通。**2026-08-31 补了作用域参数**（`--post-id` / `--latest-posts`，CR-47）——待译队列是最老优先的，此前 `--limit` 根本够不到最新几篇，K9 与 G8 都因此卡死。已用它真实翻译点名的三篇（成功 3 / 失败 0，**实际费用上界 US$0.1799**；⚠️ 离线外推给的是 US$0.038，**实际高 4.7 倍**，差在 44 327 reasoning tok——CR-40 那条教训又验证了一次）。仍待**懂德语的人审校**；全量预算已不在关键路径（不补发历史，按需翻即可） | — |
| **K 组**（已在 `main`） | 🟢 **2026-09-01：GPT-Image-2 已完成两张 high 真实产出。** `3975547640610092585[0]`（dHash=9）已由用户确认完全可用；较新的文字图 `3973012230169803390[0]`（dHash=6）已生成并进入 `review.md`，待用户复核。两张 `images_de.jsonl` 均有完整 usage。其余 9 张未跑，无文字图按最新决定人工跳过。单图必须用 `--post-id + --media-index`；`--limit 1` 不是稳定选择器 | 用户复核第二张；后续仅按需选择文字图 |
| **G0 / G0b / G0c**（已在 `main`） | **三项全部完成并验收**（合并态已复现：3 篇全部组装、零拦下）。G0 实机验过 9222/9223 并存且会话隔离；**G0b 的「最新 3 篇组装成功」现已通过**——三篇全部组装、零拦下，金额硬闸在真实正文的 `$219.99` / `$100` 上通过。新增 `tools/compose_publish.py` + `scripts\run_publish.bat`（CR-58），这条验收终于有命令可以复跑 | — |
| **G1**       | 🟡 **dump 已录到，`selectors.py` 已回填，观察项与提交那一段仍缺**。2026-09-01 第二轮：把那份 24 条 dump 人工读完，回填出 **12 条 composer 定位**（+2 条旁证、8 条缺口登记），并新增 `publish/evidence.py` **逐条回查 dump**（本机 14/14 命中）——红线 5 从此是可执行的检查。⚠️ 那份 24 条 `finished_at=null` 且序号不连续，**仍然过不了 `--strict`**，⛔ 不许手工改序号 | 只剩 G6 与「同时发」 |
| **G2–G5 / G7** | ✅ **已实现**，链路跑到**提交前一步**：`scripts\run_publish_post.bat --post-id <id> --at <ISO>`。⛔ **一项都没勾选**——只在仿真 page 上验过，**真实 Business Suite 上一次都没跑过**。挡住真跑的是一个值：`[publish].ui_timezone`（留空即失败闭合，不拿 `[publish].timezone` 顶上）| 用户填时区 |
| **G6**       | ⛔ **故意没实现**：dump 里既没有提交按钮也没有成功信号。`submit()` 在碰 `page` 前抛 `ProbeRequired` 并逐条打出缺口。**最后那一下由人点**，点完用 `--mark-scheduled` 把幂等闭上。**补法见「🔭 自动定时发布链路：还差什么」** | 用户重录探查 |
| ~~渠道勾选~~ | ✅ **已消解**：用户 2026-09-01 确认，**进 composer 时 FB 与 IG 两个渠道默认全勾选**，本来就不需要点。缺的只剩「程序读不出勾了哪几个」，已降级为提交前人眼扫一下 | — |
| **G6b**      | ✅ `publish/journal.py` + `state/published.jsonl`。关键设计：**`prepared` 不算已排期**——它意味着可能留着草稿，重跑前先让人去看（退出码 3）；只有 `scheduled` 才幂等跳过 | — |
| ~~DE 账号~~  | **已提供**：`facebook_page_name = "Neakasa Deutschland"`（⚠️ **显示名，不是 URL 段**）、`instagram_account = "neakasa.de"`。`facebook_page_slug` 留空不阻塞 | — |

### ✅ Instagram 合作帖：2026-08-31 真实验收通过，这一条已经结束

**别再把它当成待办。** 用户按 `MANUAL_STEPS.md` 第 8 步全部跑完了，
包括最后那次真实抓取。**决定性的一条**：当天真实抓到的那篇新 IG 帖

```
3975547640610092585   owner=neakasa.global   coauthors=['neakasa.tech']
```

**本身就是一篇合作帖**——原作者是兄弟账号，本账号只是 coauthor。
**修复前它会被直接丢掉。** 它被正常留下、写进归档、配图也下载成功（323 KB）。
Facebook 同一次新增 1 篇、下了 5 张图。

**媒体下载至此第一次被真正触发并成功**，C2 / C4 / C5 的验收条件到这里才齐。

下面这些离线命令**保留**，它们的用途从"验证这次修复"变成了
**"以后改完解析器先自查，不要用真实露面去验"**：

**想自己再验一遍？跑这个，零网络零写盘：**

```bash
.venv\Scripts\python.exe -m tools.dryrun_delta instagram
.venv\Scripts\python.exe -m tools.dryrun_delta instagram --break-coauthors
```

第一条把最近一份 `_capture_delta_*.json` 喂给假页面、让 `delta_once()`
**原样跑完**（只有浏览器是假的）；第二条把合作帖判定退回 CR-19 修复前，
用来确认哨兵与闸还响不响。**改完 `core/parse.py` 先跑这两条，
不要用一次真实露面去验解析。**

**2026-08-30 晚的离线复核结果（不用重跑就能引用）**：

| capture | 候选 | 保留 | 原创 | 合作 | 丢弃 |
|---|---:|---:|---:|---:|---:|
| IG 回填 | 1022 | 1019 | 756 | **263** | 3（真·推荐位） |
| **IG 增量** | 39 | **36** | **1** | **35** | 3 |
| FB 回填 | 47 | 46 | 46 | 0 | 1 |

**增量那一屏 36 篇里 35 篇是合作帖**——这个账号最近三个月的时间线
几乎全靠合作帖判定撑着。记住这个比例，下面的判读全靠它。

让用户跑（`MANUAL_STEPS.md` 第 8 步，约 5 分钟）：

```bash
.venv\Scripts\python.exe -m routes.delta --dry-run --no-jitter
```

**看输出里 IG 那行的「本账号 N 篇（原创 a · 合作 b）」**：

| 结果 | 含义 | 你要做什么 |
|---|---|---|
| **合计 30+ 且合作是大头** | 与离线复核一致 | 收尾（见下） |
| **合计 30+ 但合作 ≈ 0** | 合作判定漂移了，但被别的东西补上了 | 反常，**离线查**转储 |
| **本账号 1–3 篇** | 合作判定失效 | `_capture_delta_*.json` 里有原始响应，**离线查**：节点里还有没有 `coauthor_producers`、`partition_by_owner` 有没有走 `on_timeline_of()` |
| **`只看到 N 篇 … 大概率没有拿到时间线`** | 闸拦住了 | 设计行为。**看它后半句**：带"其中 N 篇来自已知合作方"就是判定失效，不带才去怀疑被拦 |
| **`丢弃的 N 篇里有 M 篇来自已知合作方`** | 哨兵响了 | 归属判定漏判了。离线查那几篇为什么没带 coauthor 信息，**不要直接放行** |

**❌ 不要让用户反复跑。** 每跑一次就是一次真实露面，而增量的所有原始响应
都转储在 `_capture_delta_*.json` 里，**一次跑够，剩下的离线查**——
这是 B 组那次血的教训（用户滚了 40 分钟，靠转储才没白滚）。

#### 复测通过后的收尾清单　✅ **1 和 2 已完成，别再做一遍**

1. ~~`IMPLEMENTATION_PLAN.md` 里 C2 / C4 / C5 勾上~~ ✅ **已勾**
2. ~~合回 main~~ ✅ **已合。** 2026-08-31 核实：`feat/delta-logged-in` 与
   `feat/integrity-alerts` 都是 `main` 的祖先，`main` 在 `a259582`，
   工作区干净。**不需要再跑那三条 git 命令。**
3. ⬜ 让用户走 `MANUAL_STEPS.md` **第 9 步**装计划任务（`tools.schedule install`）
4. ⬜ E3 勾上，写下实际的触发器行为

（3 和 4 仍然待办——那是一个需要用户知情同意的开关，装上就开始每天真实访问。）

### ⚠️ 两条被推翻的"事实"，别再照旧文引用

**1. "IG 一个多月没发新帖"——假象。** 真相是 Instagram 的**合作帖**
（一方发布、双方主页同时显示）被归属判定丢掉了，实测丢了 **263 篇**。
修完之后 IG 归档 756 → **1019 篇**，最新一帖是 **2026-08-27** 而不是 07-15。
**两个账号都是日更**（近一年间隔中位 FB 1.00 天、IG 0.99 天）。
根因与修法见 `CODE_REVIEW.md` 的 **CR-19**——那是这个项目目前最值得读的一节。

**2. CR-16「IG 首屏时间线不走 XHR」——我的误判。** 时间线一直在 XHR 里。
`harvest_embedded` 因此默认关闭（保留为兜底开关）。CR-17 同样未能证实。
两条都在 `CODE_REVIEW.md` 里带更正段，**别把它们当成已确证的缺陷去引用**。

**3.（2026-08-30 晚补）CR-19 说"两个字段在真实响应里都存在"——
准确说是「键」存在。** `invited_coauthor_producers` 在 1022 个节点上
**值全是空数组**，所以"只收已接受、不收被邀请"这条选择**至今没有真实反例
可验证**。选择保持不变（它在保守方向上），但别把它当成已被数据证实的结论。

### 阈值已经标定过两次，别再自己想一个

`[integrity].alert_after_quiet_days = { facebook = 7, instagram = 10 }`、
`gap_flag_days = { facebook = 5, instagram = 6 }`、
`[delta].quiet_days_before_slowdown = { facebook = 10, instagram = 10 }`。

用户拍板的是"**按平台分开**"这件事；具体数值是按归档实测标的，
**IG 那个在同一天从 21 改到 10**——因为第一版是按"IG 已停更"算的，
而那个前提被合作帖修复推翻了。要再改，先重新统计发帖间隔，别拍脑袋。

### ⚠️ 一条用户已拍板、不要重新讨论的事

那 263 篇合作帖里有 **229 篇的原作者是第三方创作者**（宠物 UGC 账号）。
我提示过"内容著作权在创作者手里，二次发布到 DE Page 有授权问题"，
**用户确认合作协议已覆盖，决定全部进翻译与发布流水线**。
这是用户的决定。`review.md` 每篇仍会标出原作者与授权提示，
`index.html` 有绿色「合作 · @原作者」标签——提示保留，但不要再拿它去劝阻。

### ⚠️ 增量方案是"登录态"，不是"登出"

**用户 2026-08-30 确认：Facebook / Instagram 在没有登录态时直接报错**
（真实探针印证：对公开账号跑 `web_profile_info`，首次请求即 429）。
计划第 1 节"登出可用端点"那条关键事实**不再成立**，
而整个架构的第二条腿（"增量完全登出，因此没有可封的东西"）就架在它上面。

**用户在 A/B/C 三个方案里拍板走 B：增量改走登录态**，复用回填那条 CDP 通道。
（我当时建议 A，用户选了 B。**这是用户的决定，不要再重新讨论。**）

**你必须清醒地知道这个方案换来了什么**：

> 封号风险从「一次性敞口」变成「累积性敞口」。
> 抓取小号被封是本项目**唯一不可恢复的失败模式**——回填与增量会同时断掉。

**所以 C7「累积风险缓解」是方案的组成部分，不是锦上添花**：
随机化触发时刻、抓取深度上限（只滚几屏不滚到底）、滚动节奏拟人、
异常即停不重试、频率可降级、失败预算、抓取与发布 profile 隔离。
**不要因为"跑得挺好"就把它们优化掉**——这类风险的反馈是延迟的，且只反馈一次。

**两条红线没变，别顺手一起改了**：

1. ❌ **仍然不得实现自动登录。** B 说的是"复用人工登录留下的会话"，
   不是"让程序去登录"。全项目仍然只有一条登录路径。
2. ❌ **回填仍然是人工滚动。**

C 组已经按方案 B 实现完了（`routes/delta.py` 下半部分）。
里面那七条缓解措施**都已经落地并且被测试钉住**——
`tests/tests_delta_logged_in.py` 有专门断言。**改动它们之前先看那些断言**，
它们不是防回归，是防"跑得挺好就顺手优化掉"。

`routes/delta.py` 上半部分的登出实现**保留不删**——端点若重开，
切回去是风险更低的路径，而那份代码是对的（55 项离线断言全过）。
`--logged-out-probe` 仍可跑它。

### 归档现在长什么样（2026-08-30 两轮修复后）

```
archive/in_neakasa.tech/
  index.html          ← 双击就能看完整个账号的历史（1020 篇，按时间倒序）
  manifest.jsonl      ← **派生索引**，可用 tools.layout reindex 重建
  _rejected.jsonl     ← 被丢弃的 3 条推荐位，留痕不静默丢
  _orphan_media/      ← 无主的 442 个媒体文件（移动不是删除）
  posts/
    2026-08-27_0912_3941857863710867472/
      post.json  text.txt  01.jpg 02.jpg 03.jpg
  _capture_*.json       ← **别删**，离线重放的唯一输入
  _capture_delta_*.json ← 增量的转储，只留最近 7 份、自动裁剪
```

**`post.json` 是真相，`manifest.jsonl` 是派生索引。冲突时以文件夹为准，
跑 `python -m tools.layout reindex <平台>` 重建——永远不反过来。**

真实数字（2026-08-31 增量之后）：**Instagram 1020 篇**（756 原创 + **264 合作帖**，1011 篇有正文）、
**Facebook 47 篇**（46 篇有正文，合作帖 0）。**最新一帖 IG 2026-08-31 / FB 2026-08-27**。
⚠️ 注意增量新增的那一篇加在**合作**那一栏（263 → 264）——它本身就是合作帖，这正是这次验收的关键证据。
图片 IG 742 张 / FB 70 张，全部 1080px 上下。

### 这次修复留下的五条经验（比修好的代码更值钱）

0. **用户对自己业务的观察，比你对数据的推理更可靠。** 2026-08-30 的 P0
   （合作帖，CR-19）是**用户问出来的**——"很多帖子是两个账号共同发的，
   会不会我们抓的这个只是转发角色？"。而我在此之前刚给出一个错误诊断，
   并据此写了一整条新代码路径。
   **看到数字不对时，先穷尽"这些数据到底是什么"，再去猜"数据从哪来"。**

1. **"离线测试全绿"和"能处理真实数据"是两件事。** 282 项断言全过的同时，
   归档里混着 267 条他人帖、478 条轮播子项。测试覆盖的是我们想到的形态。
   `tests_parse.py` 现在有一整段**照抄真实响应结构**的断言。
2. **`walk()` 全树搜索是一对权衡。** 抗字段路径漂移是真的，代价是任何
   "看起来像帖子"的节点都会被捞进来。补救是加归属校验，不是放弃它。
3. **判定要判值，不要判键。** `"code" in d` 与 `d.get("code")` 差一个字符，
   差 478 条脏数据——轮播子项里那个键在，值是 `None`。
4. **"解析前无条件转储"第一次兑现价值。** 没有它这次就是白滚 40 分钟。
   **这条设计不许优化掉。**

**别为了"有产出"去做依赖图下游的事。** 计划的依赖图是真的。
现在真正有业务价值的是 **F（翻译）→ K（调图）→ G（发布）** 这条线，三段都卡在用户：

- **F**：管道已完全跑通，2026-08-31 又补了作用域参数（`--post-id` /
  `--latest-posts`，CR-47）并真实翻译了要发的三篇。只差**懂德语的人审校**；
  **全量预算已不在关键路径**（不补发历史，按需翻即可）。
- **K**：**代码已完成、17 套 972 项全绿**（`feat/image-de` @ `67612ae`）。
  **K 硬依赖 F**——图内德语要用该帖已有的 `text_de` 做参照，没有当前版本译文就不处理；
  那三篇的译文现在有了，队列排出 **11 张**。
  ⚠️ 仍然成立的一条：**F 的德语审校不通过，K 跑出来的图也是错的**，
  所以 K9 的验收要等 F 那边有可信译文。当前唯一卡点是**图片费用确认**。
- **G**：**G0 / G0b / G0c 已完成并验收**（`feat/business-suite-publish` @ `a620d4f`，
  17 套 946 项全绿）。**G1 ~ G9 全部卡在 G1 的真实 DOM 探查**。

- **L（新）**：把上面三段连起来。**L0 四项不依赖任何组，现在就能做**，
  其中 **L0a（装计划任务）是整个项目投入产出比最高的一件事**——
  在它装上之前自动化程度是 0。见 `docs/PIPELINE_PLAN.md`。

⚠️ **一个会误导工期估算的数**：`1051 条待译正文` 是**翻译**口径。
**发布**口径是 **470 篇**（FB 27 / IG 443）——项目约定"视频只记元数据不下载"，
**585 篇纯视频帖没有可上传素材**。用户已定不补发历史，所以暂时不影响进度，
但别拿 1051 去估发布侧的工作量。

⚠️ **两条待办已被 2026-08-31 的全局复盘消解，别再当阻塞项**：

1. **全量翻译的 US$24 预算拍板已不在关键路径上。** 既然不补发历史，
   **只有要发的帖才需要译文**——稳态是 **5.2 篇/周**，不是 1051 篇。
   它现在是可选项（跑了得到一份德语语料，当前没有自动化环节消费它）。
   **建议先不跑全量**，让对账器按需翻译。
2. **"229 篇第三方创作者合作帖的授权风险"在发布口径下基本蒸发。**
   那些几乎全是纯视频 Reels，**本来就发不了**。图文可发的合作帖只有 39 篇，
   近 90 天的原作者**全是自家兄弟账号**（`neakasa.global` 21 / `neakasa.de` 3）。
   `review.md` 与 `index.html` 的提示保留，但它不再是发布的实际阻碍。

⚠️ **翻译账单的主导项是 thinking，不是译文。** 实测同一篇帖子：
`reasoning_effort=high` 时 reasoning 9899 tok / 可见译文 420 tok；
调到 `low` 时 reasoning 373 tok，**可见译文一样长**，费用 1/4。
high 的德语排版细节确实更好（„…" 引号成对、句中不误大写）。
**这是业务取舍，不要替用户改**；`--estimate` 会用真实 usage 把两种情况都算给他看。

---

## 项目背景（压缩版，细节在计划第 0 节）

公司有两个社媒账号：**US 站**（Facebook Page + Instagram，已发布数十篇英文
图文帖和产品视频）和 **DE 站**（空）。目标是把 US 的内容抓取归档、翻译成德语、
发布到 DE 站并创建定时发布任务。

**为什么是爬取而不是官方 API**：US 账号**拿不到管理员权限**。
官方 Graph API 和官方数据导出都需要目标账号的角色或凭据，两条都走不通。
这一点已经反复确认过，**不要再提议走 API 抓取 US 账号**。

（DE 站的发布端另说：用户有 DE 账号权限但当前拿不到 API Token，
因此发布走 Business Suite 界面自动化，API 只作为只读验证通道保留。）

---

## 架构：两条路径，这个拆分是整个设计的核心

| 路径     | 模块                       | 身份                            | 频次   | 风险           |
| -------- | -------------------------- | ------------------------------- | ------ | -------------- |
| **回填** | `routes/backfill.py`       | 登录态（专用小号）+**人工滚动** | 跑一次 | 一次性敞口     |
| **增量** | `routes/delta.py`（方案 B 已实现） | **登录态**（同一小号）+ CDP 附着 | 每天 | ⚠️ **累积性敞口** |

**为什么拆**：每日定时会把封号风险从"一次性"变成"累积性"——
单次会不会被封，和 365 次里会不会被封一次，是两个量级的问题。
登出增量没有会话可以丢，最坏情况只是 IP 被临时限流，换个时间重试即可。

⚠️ **以上是原设计。2026-08-30 确认 FB/IG 无登录态直接报错，登出这条腿不存在了；
用户拍板走方案 B（增量改走登录态）。** 拆两条路径的意义因此变了——
不再是"两种身份"，而是"一次性深抓 vs 每天浅看"，
两者的频率、深度、风险特征仍然完全不同，共用一套代码只会两边都做不好。
**累积性风险由 C7 的七条措施来控，而不是靠身份来免疫。**

**回填为什么让人工滚动而不是脚本自动滚**：因为滚动的确实是人，
没有任何可识别的自动化行为特征。代价是要花 20 分钟手动滚一次，值得。

**如果你想"优化"掉这两条中的任何一条设计，先回来看这一节。**

---

## 工作区结构

```
FacebookScraper/
  README.md                架构 + 怎么跑
  config.toml              全部可调参数，代码里零硬编码
  requirements.txt         playwright / httpx / openai / **Pillow** / **tzdata**
                           ⚠️ tzdata 是 G 组 2026-08-31 加的（CR-60）：Windows 不自带
                           IANA 时区库，没有它 ZoneInfo('Europe/Berlin') 直接抛异常
  translate.py             DeepSeek 德语翻译 + 审校清单（含 --check/--estimate）
  localize_images.py       图内英文德语化（K 组，GPT-Image-2）。与 translate.py 平级
                           ⚠️ 在 `feat/image-de` 上，尚未进 main

  scripts/                 双击入口。**纯 ASCII + CRLF，不得有中文**
    setup.bat  start_chrome.bat  run_backfill.bat  run_translate.bat
    run_delta.bat          增量调度入口。**字节级约定现在有测试守**（tests_schedule）
  tools/                   .bat 的真正实现，中文提示的容身处
    setup.py  start_chrome.py
    replay.py              用 capture 离线重建归档（B7），不重新下载媒体
    dryrun_delta.py        **用增量转储离线跑完 delta_once() 的真实代码路径**。
                           零网络零写盘。改完解析器先跑它，别用真实露面去验。
                           `--break-coauthors` 自检哨兵还响不响
    layout.py              归档布局：migrate / reindex / index（J 组）
    schedule.py            计划任务：xml / install / status / remove（E3）
  docs/
    IMPLEMENTATION_PLAN.md ← 进度真相源，你要持续更新它
    MANUAL_STEPS.md        ← 人工操作指南，需要用户操作时同步更新
    HANDOFF.md             ← 本文件（抓取侧的任务书）
    CODE_REVIEW.md         ← 审查记录 CR-01~**CR-60**。**CR-19 是最值得读的一节**；
                           **第 15 节是 K/G 两条分支的同步审查 + 处置结果表**
    TRANSLATION_PLAN.md    ← DeepSeek 实现、操作与验收真相（F 组）
    IMAGE_PLAN.md          ← **K 组任务书。动 GPT-Image-2 前必读第 2 节**
    PUBLISH_PLAN.md        ← **G 组任务书。范围已收窄，别把批量补发做回来**
  prompts/
    translate_de.md        英译德提示词，可直接编辑，改它不用动 Python
    image_de.md            图片德语化提示词（K 组），同样可直接编辑
                           IMAGE_PROMPT_VERSION 现在是 2，改模板必须 +1
  core/
    config.py              配置读取 + Chrome 路径探测
    chrome.py              CDP 附着 + launch()（增量与 start_chrome 共用）
    capture.py             响应捕获 / 媒体下载 / 转储裁剪。**回填与增量共用**
    store.py               归档层（含"残缺→补全"升级语义）
    parse.py               三种 JSON 形态的解析 + walk() 全树搜索
    session.py             仅 Pacer 限速 + SAFARI_UA 常量
    http.py                登出 HTTP 客户端。**方案 B 下用不上，保留备用，不要删**
    integrity.py           连续性 / 长期零新增 / 媒体不全 三项检查
    notify.py              Windows 通知（toast → msg → alerts.log 三级降级）
    console.py             stdout/stderr 强制 UTF-8（本机代码页 936，重定向即崩）
  routes/
    backfill.py            登录态回填
    delta.py               每日增量。上半＝登出实现（保留备用）；下半＝登录态（C2–C7）
    fb_graph.py            API 只读通道，保留但未接入（缺 Token）
  pipeline.py              L 组：对账 / activate / run / approve / status
  pipeline_assisted.py     L 组：assisted 的真正实现（跨平台对账、预算、排槽、
                           待确认清单、approve 逐篇提交）
  publish/                 G 组
    compose.py             组装「译文 + 德语图 + 排期时刻」并跑完离线硬闸。
                           **不碰浏览器**。G0b 已验收（最新 3 篇真实组装通过）
    business_suite.py      UI 自动化（G2–G6c、G7）。**submit() 现在会真的点**，
                           前提是三道证据闸全开（见 probe_signals.py --status）
    workflow.py            G6/G6c 浏览器状态机：五态 journal + 提交前 Planner 基线
                           + 单击提交 + 成功等待 + 回读。**click 前先耐久写 intent**
    selectors.py           **带证据的登记表**：12 条 composer 定位（来自 24 条 v1
                           dump）+ 2 条旁证 + 缺口登记。末尾会装载 signals_backfilled
    signals_backfilled.py  ⛔ **生成文件**，由 `probe_signals.py --emit` 从 v2 dump
                           推导 + 回查后写出。**不要手工编辑**，下次 --emit 会覆盖
    evidence.py            把每条定位/信号回查到它自称的 dump；`verify_publish_chain`
                           验同页因果顺序。dump 不在时"跳过"，**跳过不等于通过**
    journal.py             state/published.jsonl 的留痕与幂等（G6b）。
                           **prepared 不算已排期**——它意味着可能留着草稿
  tools/（G 组新增）
    probe_publish.py       G1 探查：只记录人工交互 + 被动语义快照，**不驱动页面**。
                           `--fill-notes ... --set-note K=V` 可非交互补观察项
    probe_signals.py       ← **证据推导**：--status / --check / --report / --emit。
                           把「录完 dump → 生产闸打开」从一次开发会话变成一条命令
    start_chrome_publish.py  起发布专用 Chrome（9223 / .fbscraper-publish）
    compose_publish.py     ← 组装预演入口（CR-58）。零浏览器/零网络/零写盘
    publish_post.py        ← **驱动入口**：默认停在提交前，`--submit` 才真的提交
  tests/                   **23 套**（实测 1543 项全绿）。
                           ⚠️ 别把断言数当契约，以 setup.bat 实际跑出来的为准
  _deprecated/             已否决路线的存档

  archive/  state/  .env   产物与密钥（全部 gitignore）
```

**`_deprecated/` 是留档不是留后路。** 三个模块都与已定架构冲突，
它们的有效信息（端点、Header 常量）已完整收录进计划第 1 节，不用回去翻代码。

---

## 已完成 vs 待建

### 已完成并在 Windows 实机验证过

| 项           | 状态                                                       |
| ------------ | ---------------------------------------------------------- |
| **A1 / A2**  | 环境搭建、离线测试基线 ——`scripts\setup.bat` 全程无报错    |
| **C1**       | `core/http.py` 登出客户端，17 项断言                       |
| **D1**       | `core/integrity.py` 完整性检查，54 项断言                  |
| **D2**       | `core/notify.py` 通知降级，16 项断言 + 真实 toast 弹出验证 |
| **F1/F2/F3** | 官方 OpenAI 接口、High thinking、无输出上限、标签硬校验已离线完成；新版真实试译/人工德语验收待执行 |
| **输出编码** | `core/console.py`：本机代码页 936，输出一旦被重定向就崩在 `⚠/❗/ß` 上。**这会直接炸掉 E1**（它要求把增量输出追加进 `state/delta.log`）。已修并验证 |
| **旧 C2**    | 登出增量 Instagram，55 项断言 ——端点已对登出关闭，**代码保留备用**，见下 |
| **B 组全部** | 解析器缺陷修复 + 归档离线重建 + 滚动进度显示，全部实测验收 |
| **J 组全部** | 归档改为每帖一个文件夹 + `index.html` 总览，迁移完成且可回退 |
| **C3 / C6**  | 登录态增量的 FB 半边 + 主入口，**已实机验收通过** |
| **C7**       | 累积风险缓解七条 + 第八条（`DeltaBlocked.hard`），四条 mock 验收全过 |
| **D3**       | 完整性检查接入增量；核心是**只报新出现的问题**，否则告警通道会被用废 |
| **E1 / E2**  | `run_delta.bat`（编码实测通过）+ `--if-stale` 补跑判定 |
| **C2/C4/C5** | 代码 + 离线断言完成，**未勾选**——解析半边已用真实增量响应离线验证，**媒体下载没被触发过** |
| **E3**       | 计划任务工具 + 48 项离线断言，**故意没注册**，见上 |
| **D4**       | 计划外新增的第四项检查（丢弃了已知合作方的帖子）。正常零误报、退回旧实现必响，冷启动也能响 |
| **CR-20~24** | 合作帖判定的三处加固（并集合并 / 条目形态 / target 归一化）+ 摘要拆成「原创 · 合作」 |

**当前离线测试基线：16 套 825 项检查，全绿**（2026-08-30 第四轮测得；在 stdout 被重定向的
条件下也全绿，这是编码修复后新增的验证条件）。`scripts\setup.bat` 用
`glob("tests/tests_*.py")` 全跑，新增测试自动纳入。

⚠️ **别把这个数字当契约。** 三条线在并行加测试，它一小时之内就从 578 涨到 788。
**要断言的是"全绿"，不是某个具体数**——以 `scripts\setup.bat` 实际跑出来的为准。

### `routes/delta.py` 里那份登出实现该怎么对待

它是按原方案（完全登出）写的，代码本身是对的——55 项离线断言全过，
含归属过滤、登录墙三形态、429 退避上限。但 `web_profile_info` 已对登出访客关闭，
**它现在跑不出任何结果**。

**保留，不要删。** 端点若重新开放，切回去是风险更低的路径。
C2 的新实现（登录态）**并行新增**，不要覆盖它。
`core/http.py` 同理——方案 B 下用不上（媒体下载走浏览器请求栈），
但保留；`ruff` 报"未使用"是预期的。

### F 组为什么代码完成但业务验收仍没勾选

真实文案已经有 1055 条。当前官方 `/chat/completions`、Bearer 鉴权、Pro 模型、
High thinking、无 `max_tokens` 请求体、提示词/术语表、正文与提示词版本断点、单实例锁、
金额/标签硬校验和审校清单均已离线验证。正文或提示词版本变化时，旧译文会自动过期，
旧 `text_de.txt` 派生副本也会移除。2026-08-30 晚旧 Anthropic 路径曾用真实 Key 跑通
`--check` 与 FB 3 + IG 3（US$0.026），但它们裁掉了标签且关闭 thinking，现仅作历史证据。
**当前仍不勾选**：新版真实调用未跑，且没有懂德语的人看过新版译文。

### 尚未实现 / 尚未完成外部验收

⚠️ **本节说的「尚未实现」是相对 `main` 而言的。**
**K 组与 G 组的代码都已经写完并通过全套离线测试**，只是还在各自的 feature
分支上、没有合进 `main` —— 详见上面「🧭 三个 worktree 的现状」。

- **K 组**：K0/K2/K4/K5/K6/K10 已验收；**K1/K7/K8/K9 全卡在图片费用确认**
  （GPT-Image-2 一次都没调过，`images_de.jsonl` 还不存在）。
- **G 组**（2026-09-01 第二轮更新）：G0/G0b/G0c 已验收；
  **G1 已回填、G2–G5/G7 已实现，链路跑到提交前一步**；
  `selectors.py` 不再是空的，但 `business_suite.submit()` **仍然故意抛
  `ProbeRequired`** —— dump 里没有提交按钮也没有成功信号（红线 5）。
  ⛔ **G 组一项都没勾选**：全部只在仿真 page 上验过，真实 UI 上没跑过。
- **H 组**官方 API 验证缺 Token、不阻塞主路径。
- **E 组**代码已实现并通过离线测试，但计划任务**刻意没有注册**（= L0a，仍未装）。
- **L 组**一行代码都还没写；L0 四项不依赖任何组，随时可开工。

⚠️ **C7 的七条缓解措施已经落地并被测试钉住，不许"顺手优化掉"。**
参数全在 `config.toml` 的 `[delta]`（已从注释改成真值，代码真的在读）。
调大任何一个之前先读 C7 的完成行——那里写着每个值为什么是那个值。

⚠️ **维护 E1 时先看 `core/console.py` 的模块注释。** E1 会把增量输出追加进
`state/delta.log`，那就是重定向——不调 `force_utf8()` 的话第一次打 `⚠` 就崩，
而且崩在网络请求已发出、归档尚未写入之际。

### ⚠️ 二次审查留下的两个 API 约定（写 C 组前必读）

审查修掉的 CR-03 / CR-07 各自留下一个**必须沿用**的调用约定。
不沿用不会报错，只会静默地把已修的 bug 重新引进来。

**1. 判断"这条帖子要不要写"，用 `Archive.should_append(post)`，不要用 `has(post_id)`。**

```python
# ❌ 会让登出增量留下的残缺轮播帖永远补不全（这就是 CR-03）
if arc.has(post.post_id):
    continue

# ✅ 已存但媒体不全、而新的一份更全时，仍然应该下载并写入
if not arc.should_append(post):
    continue
```

下载媒体**之前**就要做这个判断，否则幂等重跑会重复请求 CDN。
`append()` 内部也会再判一次，所以两者语义永远一致。

**2. 判断"调试端口能不能用"，用 `core.chrome.cdp_ready(port)`，不要用 `port_open(port)`。**

`port_open` 只回答"这个端口上有没有东西在监听"；随便一个别的程序占了 9222
就会被误判成专用 Chrome 已就绪（CR-07）。`cdp_ready` 会去 `GET /json/version`
确认对端真的是 Chrome DevTools。两个函数都保留是有用的——
先 `cdp_ready` 判可用，再用 `port_open` 区分"没人监听"和"被别的程序占了"，
这两种情况给用户的处理办法完全不同。

### 诚实的未知数

- ~~**`core/parse.py` 的解析器从未与真实响应比对过。**~~
  **2026-08-30 已比对，结果是不匹配**（跨账号污染、轮播子项、FB 视频帖）。
  兜底生效了：capture 完整保留，离线重放可重建，零数据损失。
  **教训值得记住：282 项离线断言全绿，仍然挡不住真实数据——
  测试覆盖的是我们想到的形态，不是真实的形态。**
  新增断言必须用真实结构构造（B2/B4 已写死这条要求）。
- **`walk()` 全树搜索是一对权衡，之前只记了好的一半。** 它对字段路径漂移的抗性
  是真的（FB 的正文、时间、图片路径一次就抽对了），但它也会把任何"看起来像帖子"
  的节点捞进来。补救不是放弃它，而是加一道归属校验。
- ~~**C2/C3（登出增量）可能不成立。**~~ **已作废**：登出端点确认关闭，
  方案改为 B（登录态）。C2/C3 现在是登录态实现，走的是回填那条已验证的 CDP 通道。
- ~~**登录态增量的解析路径还没和真实响应对过。**~~
  **2026-08-30 晚已用 `_capture_delta_*.json` 离线对过，结果是一致的**：
  增量首屏的节点形态与回填完全相同（39 个节点全是 `iphone_struct`，
  `coauthor_producers` 键 39/39 都在），解析出 36 篇本账号帖子
  （1 原创 + 35 合作）、丢弃 3 篇真·推荐位。
  **所以第 8 步要验的不再是解析，而是媒体下载**——那条路径历次都因为
  0 新增而没被触发过。
  兜底不变：解析出 0 篇会**当次中止并明确报"解析器可能已失效"**，
  原始响应转储在 `_capture_delta_*.json`，可以离线改——**不要让用户反复跑**。
- **C7 的七条措施"写对了"已被测试证明，"管用"没有。** 这类风险的反馈是
  延迟的、而且只反馈一次。不要把"跑了两周挺好"当成可以放宽参数的证据。
- **G 组全部依赖 G1 的真实 DOM 探查结果**，在 G1 完成前无法估算工作量。
- **新版翻译质量未经真实验证。** 请求形态和金额/标签硬约束已有离线测试；
  但 High thinking 下的德语自然度、耗时与 reasoning 费用必须靠新版 3+3 回答。

---

## 十一条禁止事项（违反会导致封号、烧钱或大面积返工）

1. ❌ **不得实现自动登录。** 全项目只允许一条登录路径：人工在
   `scripts\start_chrome.bat` 起的专用 Chrome 里登录一次，会话留在该 profile
   目录，由 CDP 附着复用。`core/session.py` 已刻意剥离全部登录逻辑，**不要加回去**。
   ⚠️ 方案 B 让**增量也带上了登录态**，但那是**复用**人工登录留下的会话，
   不是让程序去登录。**这条禁令没有松动**，会话过期时的正解是通知用户去登，
   不是替他登。
2. ❌ **不得硬编码 Instagram `doc_id`。** 该值每 2–4 周轮换，轮换后返回 401
   且错误文案是 "Please wait a few minutes before you try again" ——
   **这个提示是误导的，它不是限流，等多久都不会恢复**。
3. ❌ **不得提高并发（恒为 1）或移除随机间隔。**
4. ❌ **不得依赖 `mbasic.facebook.com`。** 官宣 2024-12-03 下线。
5. ❌ **不得凭猜测编写 Business Suite 的选择器。** 必须先做 G1 的真实 DOM 探查。
6. ❌ **不得部署到 VPS / 云函数 / GitHub Actions。** 必须住宅 IP。
7. ❌ **不得在 `.bat` 里写任何非 ASCII 字符，也不得用 LF 换行。**
   实测 cmd.exe 会把含中文的行从中间劈开、后半段当命令执行，
   加不加 `chcp 65001` 都会犯，加 BOM 更糟。中文提示一律放 Python。
8. ❌ **不得把 API 密钥写进 `config.toml`。** 那个文件进版本库。
   密钥走环境变量或项目内 `.env`（已 gitignore）。现在有两个：
   `DEEPSEEK_API_KEY`（翻译）与 `IMAGE_API_KEY`（图片）。
9. ❌ **调 gpt-image-2 时不得发送 `input_fidelity`。** 该模型强制高保真，
   已取消这个参数，**传了直接 400**。
   ⚠️ **本机 `openai` 3.6.0 的 `images.edit()` 签名里有它，docstring 还写着
   "1.5 及之后的模型支持"——那句话对 gpt-image-2 是错的。**
   写这条是因为：看到签名会觉得"保真度这么关键怎么能不传"，
   **加回去的那一刻整组请求全灭**。详见 `IMAGE_PLAN.md` 第 2 节。
10. ❌ **不得拿原图宽高直接当 `size`。** 边长必须是 16 的倍数，
    而归档里最常见的两档（1080×1080 共 318 张、1080×1350 共 100 张）都不是，
    720×720 那档总像素还低于下限。**一半以上的图会 400。** 一律走 `legal_size()`。
11. ❌ **不得覆盖 `media_de/` 里人工放置的文件，也不得修改原图。**
    人工放的是设计同事手工修的版本，**比模型那张对**；
    `01.jpg` 是抓取产物，只读。

---

## 工作协议

**每完成一项任务**：

1. 把 `docs/IMPLEMENTATION_PLAN.md` 里该项的 `- [ ]` 改成 `- [x]`
2. 在该项下方追加一行：`> 完成：<日期> · <实际做法与偏差>`
3. **未通过该项的【验收】不得勾选**——代码写完不等于验收通过。
   验收要真实数据而暂时拿不到的，写 `> 进行中：...` 并说明缺什么
4. 该项若需要用户操作，同步更新 `docs/MANUAL_STEPS.md`

**偏差必须记录，不要默默改代码。** 尤其是这几类：

- 真实的字段路径与计划里"当前假设"不同
- Business Suite 的真实选择器
- 端点的真实行为（返回什么、什么时候 401、登出能看到几条）
- 平台相关的实际情况（Chrome 路径、Task Scheduler 行为）

后续任务依赖这些事实。计划与现实冲突时，**以现实为准并记录**。

---

## 三个最可能踩的坑

**1. `scripts\start_chrome.bat` 跑了但端口没开。**
若目标 `user-data-dir` 已被另一个 Chrome 实例占用，Chrome 会**静默复用
已有实例并忽略 `--remote-debugging-port`**，没有任何报错。脚本已内置轮询检测，
遇到时先在任务管理器确认没有残留的 `chrome.exe`。
（日常用的 Chrome 开着不影响——profile 目录不同，互不干扰。）

**2. 回填解析出 0 篇。**
这大概率会发生——解析器没和真实响应比对过。
**不要重滚**。`backfill.py` 会在解析之前**无条件转储**原始响应到
`archive/<账号>/_capture_<时间戳>.json`，用它离线校准解析器即可，数据不会丢。

**3. 媒体下载 403。**
CDN URL 带签名且有时效，必须在拿到响应的**同一次运行内**下载完，
不能先存 URL 事后再取。

更多症状与处理见计划的「附录 C · 遇到这些情况该怎么办」。

---

## 版本库

- **远端**：`https://github.com/marcus-ao/FacebookScraper.git`（**私有仓库**，
  2026-08-29 经匿名访问返回 404 确认）　**主干**：`main`
- **不进版本库**（`.gitignore`）：`archive/`（抓取产物）、`state/`（运行状态）、
  `.env`（API 密钥）、`.venv/`、`__pycache__/`、`.history/`。
  Chrome profile 在 `%USERPROFILE%\.fbscraper-chrome`，本来就在仓库外。
- `.gitattributes` 锁 `*.bat` 为 CRLF。**ASCII 得靠人守，git 管不了。**

⚠️ **`config.toml` 进版本库，且已经填了真实账号名**
（`neakasaofficial` / `neakasa.tech`）。仓库是私有的，风险可控；
但**如果哪天要转公开，先想清楚这一点**。密钥永远走 `.env`，不要动这条线。

### git 工作流（2026-08-30 约定，**请照做**）

> 写在这里是因为：**约定不写进文档就会丢。** 这个项目已经反复吃过这个亏——
> 附录 D 里记着好几次"上一轮定的事下一轮没人知道"。
> 你是新会话，除了这些文档你什么都看不到。

**一个任务组一个分支**，命名 `<type>/<简短描述>`：

**当前分支清单（2026-08-31 第七轮，清理之后）**：

| 分支 | HEAD | 说明 |
|---|---|---|
| `main` | `9f8c1bf` | 与 `origin/main` 同步，唯一 checked-out 的分支 |
| `backup/pre-merge-2026-08-31` | `fd3cb02` | 合并前 `wip` 的安全网 ref，不占工作区；确认不需要了可 `git branch -D` |
| **远端** `origin/main` | `9f8c1bf` | 唯一的远端分支 |

上一轮那 8 条分支**已全部删除**：`feat/image-de` / `feat/business-suite-publish`
（内容已并入 `main`，用 `-d` 安全删）、`docs/pipeline-plan` / `docs/plan-image-publish` /
`feat/delta-logged-in` / `feat/integrity-alerts`（都是 `main` 的祖先）、
`wip/parallel-2026-08-31` 与远端的 `fix/parser-ownership-and-archive-layout` /
`wip/parallel-2026-08-31`。

> ⚠️ **`git branch -d` 是按「当前 HEAD」判的，不是按 `main`。**
> 这一轮踩过：在 checked-out 到 `wip` 的目录里删 `feat/image-de`，
> 即使它**确实**已经并进 `main`，`-d` 也会拒绝并说 "not fully merged"。
> **那不是没合干净，是站错了地方。** 换到 `main` 上再删就通过了——
> ⛔ **不要因此改用 `-D`**，那正好把这道安全网关掉。

| 尚未开工的任务组 | 分支名 | 从哪开 | 何时合回 main |
|---|---|---|---|
| E 组 | `feat/scheduler` | `main` | E3 验收通过时 |
| **L 组**（流水线编排） | **`feat/pipeline`** | `main` | 分阶段：L0 各项验收后即可合 |

**K / G / L 三组 2026-08-31 起并行开发，可分别委托给不同 Agent。**
`docs/plan-image-publish` **已由用户快进合进 `main`**，所以三条分支
**都从 `main` 开**（`main` 里已有全部任务书、`[image]`/`[publish]`/`[pipeline]`
三段配置、以及预置好的 Pillow）。

```bash
git checkout -b feat/image-de main
git checkout -b feat/business-suite-publish main
git checkout -b feat/pipeline main
```

**文件所有权表**在 `IMAGE_PLAN.md` 第 10 节、`PUBLISH_PLAN.md` 第 12 节
（内容相同、两边各留一份，因为并行开发时没人会去读另一组的任务书），
以及 `PIPELINE_PLAN.md` 第 13 节。要点：

- `core/chrome.py`、`core/config.py`、`publish/**`、`tools/*publish*` 归 G 组；
- `localize_images.py`、`prompts/image_de.md`、`translate.py` 的 `run_review` 归 K 组；
- `pipeline.py`、`docs/PIPELINE_PLAN.md`、`config.toml` 的 `[pipeline]` 段归 L 组。
  **L 组不改 K / G 的任何实现文件**，只调用它们的入口、读它们的产物——
  这既是架构约束，也顺带让三条分支互不冲突；
- **`core/store.py` / `core/parse.py` / `core/capture.py` / `routes/**` 三组都不许动**；
- `requirements.txt` **三组都不用改**（Pillow 已预置，就是为了避开这个冲突点）；
- ⚠️ `config.toml` 的 `[publish]` 段：**L 组会往末尾加三个子表**
  （`price_map` / `trusted_owners` / `schedule_rule`），TOML 要求子表在段末，
  所以 **G 组新增标量键必须加在子表之前**（该段已就地留了警告注释）；
- 共享文档**只改自己那一节**，附录 D 各自追加、标题带分支名，
  **合并冲突时各段都保留，不许二选一**。

⚠️ **K 组现在没有任何外部卡点，可以直接开工。**
G 组能立刻做的是 G0（独立 profile 参数化）与 G0b（`compose.py` 的离线硬闸），
**但 `publish/selectors.py` 在 G1 之前必须是空的**——
提交一个"看起来合理"的选择器，比不提交更糟：下一个人会以为它验证过。
**L 组的 L0 四项也不依赖任何人，现在就能做。**

**规则**：

1. **`main` 永远是"最后一个已验收的可用状态"。** 半成品不进 main。
2. **合回 main 优先用 `--ff-only`**，保持历史线性：
   ```
   git checkout main && git merge --ff-only <分支名>
   ```
   合不上（非快进）**先查清楚原因再动手，永远不要 `-f`**。
   ⚠️ **2026-08-31 补正：这条规则对当前的 `feat/image-de` /
   `feat/business-suite-publish` 不适用。** 它们的基点 `9ea342c` 比 `main`
   （`3736303`）**还早**，所以 `--ff-only` **必然失败，而那不是「main 被人动过」**。
   这两条要用普通 merge，步骤见上面「🎯 下一个会话的三步」第 2 步。
   **以后新开分支记得从当前 `main` 开**，这条规则才继续成立。
3. **该组的【验收】没过就不要合。** 这与工作协议里"未通过验收不得勾选"是同一条：
   计划里打了 `[x]`、代码进了 main，两件事应当同时发生。
4. **提交信息用 Conventional Commits**（`feat:` / `fix:` / `docs:` / `refactor:`），
   破坏性变更加 `!` 与 `BREAKING CHANGE:` 脚注。
   已有两个例子可参考：`76fc1d4`（带 BREAKING CHANGE）与本约定的 `docs:` 提交。
5. **每次会话结束前提交一次**，并同步更新本文件的"现在卡在哪"。
   下一个会话是从文档开工的，**文档和分支状态不一致比没有分支更糟**。

⚠️ **不要在 `main` 上直接改 C 组的东西。** C 组直接碰封号风险面，
是这个项目里唯一可能"整组推翻重来"的部分——万一方案 B 实测走不通，
你要能整块丢掉，而不是从 main 里一点点往外挑。

## 代码质量工具

`ruff check` 会报 **14 条审查前就存在的样式项**（测试文件的单行写法、
未使用 import、无插值 f-string）。**这些是已知的、被刻意保留的**——
二次审查的边界是"只修可行动的功能缺陷，不做样式扩散"。
看到它们不用惊讶，也不要顺手"修"了当成本次改动。

ruff 没有装进 `.venv`，也不在 `requirements.txt` 里——它是临时用的工具，
不是项目依赖。

## 两条已经踩过、别再踩的

**PyPI 官方源在本网络吞吐近 0。** playwright（36 MB）跑 8 分钟零进展，
换清华镜像 45 秒装完。`tools/setup.py` 已默认走镜像，
境外网络设 `PYPI_INDEX_URL=` 空值改回官方源。

**含反斜杠的批量替换不要走 shell heredoc。** `"scripts\\run_x.bat"` 的 `\\`
会被 shell 吃成 `\`，Python 再把 `\r` 解释成回车写进源文件；
用 `read_text()` 修补时通用换行转换又会把裸 CR 变成真换行，
把字符串从中间断开。**写成独立 `.py` 文件跑，改完先 `ast.parse` 验证。**
（这个坑在 2026-08-29 的目录重组里踩过，详见计划附录 D。）
