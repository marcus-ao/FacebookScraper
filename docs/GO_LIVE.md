# 重录探查与重新标定 · 操作清单

> ## ⚠️ 2026-09-01 起，这份文档**不再是"现在该按哪个键"的答案**
>
> 上线操作顺序已经整理进 **[`MANUAL_STEPS.md` 第 12 步](MANUAL_STEPS.md)**
> （12.0 ~ 12.10，从开发布 Chrome 到装计划任务）。**照那一份走。**
>
> 本文件保留下来只管一件事：**Meta 改版之后要重录探查、重新标定 UI 上限时，
> 照第 1 ~ 3b 步走。** 第 4 步之后的内容与 `MANUAL_STEPS` 第 12 步重复，
> 已经不再更新 —— 两份都维护必然漂移，而漂移的那一份会把人带沟里。
>
> 状态永远以这条为准（它是算出来的）：
>
> ```
> .venv\Scripts\python.exe pipeline.py preflight
> ```

---

## 历史标题：上线跑通清单 · 从现在到「不用人管」

> 建立于 2026-09-01。这份文档只回答一件事：**照什么顺序按，这条线就能自己跑起来。**
> 背景、为什么这么设计、踩过什么坑，都在别的文档里，这里一句不重复。
>
> 每一步都有**判据**（怎么算过了）和**过不了怎么办**。前一步没过不要往下走。

---

## 现状一句话

> **2026-09-01 更新：第 1 步已完成，但结论变了 —— 详见
> [`PROBE_FINDINGS_20260901.md`](PROBE_FINDINGS_20260901.md)。**
>
> 用户录到了第一份**完整通过 v2 契约**的 dump
> （`publish_probe_20260901_054226_378622.json`，56 交互 / 71 快照 / 117 截图），
> 整条「账号 → 提交 → 成功 → Planner → final」的因果链都在里面。
>
> `--check` 当时报"推不出账号上下文/排期卡片"，追进去看，**缺的东西不是没录到，
> 是那个 UI 上根本不存在**：composer 上从头到尾没有 IG 账号名；
> Planner 上没有"一张卡片带全部元数据"这种东西，
> FB 与 IG 是**两个独立对象、两个 remote ID、两个详情弹窗**。
>
> **证据契约已按现实改完**（同日）。五件生产证据全部从那份 dump 机械推导 +
> 逐条回查通过，`publish/signals_backfilled.py` 已落地，
> `[publish].ui_probe_dump` 已签字，**G6/G6c 三道闸现在是开的**：
>
> ```
> [开] 账号上下文        [开] 提交按钮 + 成功信号        [开] Planner 回读
> ```
>
> **2026-09-01 晚：第 3b 与 3c 都已完成。**
> 14 个 UI 上限用户已在真实 composer 上量完并回填，
> `ui_constraints_verified` 已改 `true`；那条 2026-09-15 的测试排期也已取消。
>
> **2026-09-01 更晚：G8 真机跑了六次。** 前五次撞出 CR-70~74，第六次提交成功了但 G6c 回读没证明（CR-75，已修待验）。当前状态与下一步以 `MANUAL_STEPS.md` 第 12 步为准。

> ## 先跑这一条（2026-09-01 新增）
>
> ```bash
> scripts\run_pipeline.bat preflight
> ```
>
> 它把下面这张手维护的表**算出来**：三道闸开没开、14 个观察项还差哪几个、
> G8 有没有真机证据、激活边界、autonomy、计划任务装没装。
> 更重要的是最后一段 —— **「若此刻激活，最近 90 天的 N 篇会怎么走」**，
> 用生产同一套判据在真实归档上预演一遍，告诉你上线之后每天到底会发生什么。
>
> ⚠️ **这条命令是这份文档现在唯一不会过期的部分。** 表格会随手改漏，它不会。

**代码全写完了，链路全接通了，闸也开了大半。**

`--submit` 之前一共四道闸：账号上下文 / 提交证据 / Planner 回读证据 /
`ui_constraints_verified` —— ✅ **四道现在全开**，
`run_publish_post.bat ... --submit` 会真的点提交。

下面第 1 步的操作清单保留 —— 以后 Meta 改版要重录时照它走即可。

---

## 第 1 步 · 录一份 v2 探查（只有你能做，约 15 分钟）

程序不会替你提交测试帖，也不会替你取消。这一步必须是真人操作。

**开一个发布专用的 Chrome（和日常用的、和抓取用的都互不干扰）：**

```bash
scripts\start_chrome_publish.bat
```

在里面登录 DE 发布账号（`Neakasa Deutschland` / `neakasa.de`），登录一次就够。

**开始录制：**

```bash
.venv\Scripts\python.exe tools\probe_publish.py
```

然后在浏览器里**完整走一遍**，顺序不能乱（证据链是按先后顺序验的）：

1. 进内容日历 → **Create post** → 进 composer；
2. 传 5 张图、填一段正文（记住其中一句，后面要用）；
3. **在 composer 里把「发到哪个 Page / 哪个 IG 帐号」那一块露出来**，
   让两个账号名同时在屏幕上停一两秒
   ——⚠️ 这一步最容易漏，漏了整条链就断在起点；
4. 打开定时开关，排一个**几天后**的时刻（不要排今天）；
5. **用鼠标点提交**（不要用开发者工具触发，那不是可信事件）；
6. 提交后的成功提示**让它自己停两秒**，不要马上点走；
7. 进内容日历，**等日历数据真的渲染出来**，把刚排的那张卡片滚进视口，
   确认卡片上同时看得见：时刻、正文、Facebook、Instagram、图片数量；
8. **保持这一页不动**，回终端按 Enter 停止录制，等它打完收尾再关窗口。

> ⚠️ 第 8 步的「等它打完收尾」不是客套。`finished_at` 没写进去的 dump
> 直接判死，前面 15 分钟白走 —— 这个项目已经这样白走过一次。

**判据**：`state/` 下多出 `publish_probe_<时间戳>.json` 和同名 `_screenshots/` 目录。

---

## 第 2 步 · 30 秒确认这次录制成不成立（先跑这个，别急着往下）

```bash
scripts\run_probe_signals.bat --check state\publish_probe_<时间戳>.json --caption "正文里的一小句"
```

它会先验 v2 契约，再逐条告诉你五件证据能不能推出来：

```
[OK]  composer_account_context      第 3 条快照 · 容器 role=group name='...'
[OK]  composer_success_signal       第 7 条快照 · status/'...'
[OK]  composer_submit_button        第 9 条交互 · button/'...'
[OK]  planner_scheduled_card        第 12 条快照 · 卡片 role=article name='...'
[OK]  planner_loaded_signal         第 12 条快照 · heading/'...'
[OK]  同页因果链                     账号 → 提交 → 成功 → Planner 就绪 → 卡片 → final
```

**判据**：六行全是 `[OK]`。

**过不了怎么办**：每条 `[缺]` 后面直接印着「补录：要做什么」。
最常见的三种：

| 报的是             | 说明第 1 步漏了哪一下                            |
| ------------------ | ------------------------------------------------ |
| 账号上下文推不出来 | 漏了第 3 小步 —— composer 上两个账号没同时露过面 |
| 成功信号推不出来   | 第 6 小步太快，提示还没被采样就跳走了            |
| 排期卡片推不出来   | 第 7 小步日历数据没等到，或卡片上少了某一格      |

补录就是把第 1 步重走一遍（旧 dump 留着不碍事）。
⚠️ 多个成功提示候选时它会都列出来，用 `--success-name 关键词` 指定一个。

标着 `⚠ 脆弱` 的行**不拦你**，但值得看一眼：那是"这一格上只有会变的文字"，
意思是过一段时间可能失配。失配时 G6c 会**转人工**，不会误判成已排期。

---

## 第 3b 步 · 填那 14 个只能人量的 UI 限制　✅ **2026-09-01 已完成**

> ✅ **2026-09-01 用户量完并回填，`ui_constraints_verified` 已是 `true`。**
> 实测值：**图片 ≤ 10 · 画幅 4:5 ~ 1.91:1 · 正文 ≤ 2200 · 标签 ≤ 30 ·
> 定时下限 = 当前时刻 · 定时上限 = 本月最后一天（不能跨月）**。
> 逐条判读与两条"照直觉写就会错"的坑见
> [`PROBE_FINDINGS_20260901.md`](PROBE_FINDINGS_20260901.md) 第九节。
>
> ⚠️ **换 dump 就要重量一次。** 那一位签的是"上面那份 dump 里的观察项有人
> 亲眼看过"，不是"这个项目量过一次"。下面的操作步骤为此保留。

`[publish].ui_constraints_verified` 管的是「IG 的四类上限有没有人亲眼量过」。
20 个观察项里 4 项按 dump 证据就能填（入口 URL、成功信号、日期时间输入方式、
时区），剩下 **14 项 dump 里没有，也不该由程序猜**（全局红线 5）。

### 怎么量：一次 composer 会话，按这个顺序做完（约 15 分钟）

**开 `scripts\start_chrome_publish.bat`，进 composer，全程不点提交。**
量完直接关窗口 —— 有草稿提示就选丢弃，别点 `Finish later`。

| # | 做什么 | 记下什么 | 对应观察项 |
|---|---|---|---|
| 1 | 打开定时开关，把日期往**前**调到今天/最近，看它从哪一刻起不让选 | 最早能排多久之后 | `schedule_min_ahead` + `_seconds` |
| 2 | 把日期一直往**后**翻，看日历在哪个月之后全灰 | 最晚能排多远 | `schedule_max_ahead` + `_seconds` |
| 3 | 传一张**极窄**的长条图（例如 600×2000）和一张**极宽**的（2000×600） | 哪个比例被拒、报什么话 | `instagram_min_aspect_ratio` / `_max_` / `_aspect_ratio_rejection` |
| 4 | 一次性选 **11 张**图上传 | 是只收前 N 张、还是整批弹错 | `instagram_max_images` / `_image_count_rejection` |
| 5 | 贴一段**明显超长**的正文（先贴 3000 字符） | 从第几个字符起打不进去 / 计数器停在几 | `instagram_max_caption_length` / `_caption_length_rejection` |
| 6 | 正文里堆 **35 个** `#tag` | 第几个开始被拒 | `instagram_max_hashtags` / `_hashtag_rejection` |
| 7 | 正文粘一串 emoji（`👨‍👩‍👧‍👦🇩🇪`）看计数器加了几 | 加 1 = `codepoints`，加 2/4 = `utf16_units`，更多 = `utf8_bytes` | `instagram_caption_length_mode` |

⚠️ **第 3～6 步一定要看 IG 那一侧的报错**（FB 的上限比 IG 松，
composer 会先过 FB 再过 IG）。四个 `*_rejection` 是**自由文本**，
照你看到的写一句人话即可（例如「弹红字：This image is too tall for Instagram」），
不用抠字眼 —— 它们是给以后排障的人看的，不参与数值判断。

⚠️ **不确定的宁可空着。** 空着只是继续挡住；填错是放行一个错的。

然后**一条命令填完**：

```bash
scripts\run_probe_signals.bat --status
```

（先确认三道闸是开的，再填下面这条。把 `<>` 里的换成你量到的值。）

```bash
.venv\Scripts\python.exe tools\probe_publish.py --fill-notes state\publish_probe_20260901_054226_378622.json --set-note "schedule_min_ahead=<最早能排多久之后，例如 10 分钟>" --set-note "schedule_min_ahead_seconds=<换算成秒，例如 600>" --set-note "schedule_max_ahead=<最晚能排多远，例如 75 天>" --set-note "schedule_max_ahead_seconds=<换算成秒，例如 6480000>" --set-note "instagram_min_aspect_ratio=<最小宽高比，例如 0.8>" --set-note "instagram_max_aspect_ratio=<最大宽高比，例如 1.91>" --set-note "instagram_max_images=<单帖图片数上限，例如 10>" --set-note "instagram_max_caption_length=<正文长度上限，例如 2200>" --set-note "instagram_caption_length_mode=<codepoints 或 utf16_units 或 utf8_bytes>" --set-note "instagram_max_hashtags=<标签数上限，例如 30>" --set-note "instagram_aspect_ratio_rejection=<超画幅时 UI 实际怎么拒>" --set-note "instagram_image_count_rejection=<超图片数时 UI 实际怎么拒>" --set-note "instagram_caption_length_rejection=<超正文长度时 UI 实际怎么拒>" --set-note "instagram_hashtag_rejection=<超标签数时 UI 实际怎么拒>"
```

⛔ **不要照抄网上流传的数字。** 这几个值存在的意义就是"在碰浏览器之前
拦住会被 IG 拒的帖"，抄错了等于把闸拆了。不确定的那几项**宁可空着** ——
空着只是继续挡住，填错是放行一个错的。

填完把 `config.toml` 的 `ui_constraints_verified` 改成 `true`，然后：

```bash
scripts\run_publish.bat --latest 3 --strict
```

**判据**：不再报 `ui_constraints_verified 仍为 false`，至少一篇能组装成功。
✅ 2026-09-01 实测：**3 篇里 2 篇组装成功**（第 3 篇缺译文，那是数据不是闸）。

---

## 第 3 步 · 把证据落盘（一条命令）

```bash
scripts\run_probe_signals.bat --emit state\publish_probe_<时间戳>.json --caption "正文里的一小句"
```

推导 → 逐条回查 → 写 `publish/signals_backfilled.py`。任一条回查不过就整体拒绝落盘。

**然后手工填一个值**（程序不替你填，这是你审核这份证据的签字栏）：

```toml
# config.toml 的 [publish] 段
ui_probe_dump = "publish_probe_<时间戳>.json"
```

**判据**：

```bash
scripts\run_probe_signals.bat --status
```

打出三个 `[开]`。打出 `[关]` 就照它说的差什么补什么。

---

## 第 4 步 · 真机发一篇（G8）

先看一眼要发的那篇长什么样（零浏览器、零网络、零写盘）：

```bash
scripts\run_publish.bat --latest 3
```

挑一个能组装成功的 `post_id`，然后真的发：

```bash
scripts\run_publish_post.bat --post-id <id> --at 2026-09-10T10:00 --submit
```

⚠️ **`--at` 必须落在「当月」** —— composer 的日期选择器不允许跨月
（2026-09-01 实测）。而且月份是按 **UI 时区**（`America/Los_Angeles`）判的，
不是柏林：柏林 10-01 06:00 在美西还是 09-30，那一篇是可以排的。
排到下个月会在碰浏览器之前被离线硬闸拦下并说清原因。

它会：核对账号 → 提交前查远端没有同槽卡片 → 填图/正文/时刻 →
**点一次提交** → 等成功信号 → 回内容日历回读卡片 → 记 `scheduled`。

**判据**：退出码 0，`state/published.jsonl` 最后一条 `status=scheduled`，
并且你在 Business Suite 上**亲眼看到**那条排期。

**过不了怎么办**：看退出码。`4`= 提交结果不明确，`5`= 提交了但回读不到 ——
这两种都**禁止自动重试**，去 Business Suite 人工确认远端到底排上没有。

确认无误后**手工把这条测试排期取消掉**。

---

## 第 5 步 · 幂等复跑（证明它不会重复发）

同一条命令**原样再跑一次**：

```bash
scripts\run_publish_post.bat --post-id <同一个 id> --at 2026-09-10T10:00 --submit
```

**判据**：它在锁内查到 `scheduled` 就直接停，**一次浏览器操作都没有**。
（这一步跳不得 —— 定时任务每天跑，重复发布是唯一会被用户看见的错误。）

---

## 第 5b 步 · 填两张业务表（**不填，上线之后每天什么都不会产出**）

这两张表和那 14 个 UI 上限一样是承重的，区别只是它们不在闸上，
所以**不会报错，只会让每一篇都停在人工队列**。跑一次 `preflight` 就能看到实数。

**① `[publish.trusted_owners]`** —— 作者不在表里的帖子逐篇进人工队列。
实测最近 90 天有 **12 篇**的作者是 `neakasa.global`（自家兄弟账号）而它不在表里。
确认二次使用授权后加进对应平台数组即可：

```toml
[publish.trusted_owners]
instagram = ["neakasa.tech", "neakasa.global"]
```

⚠️ **第三方创作者（宠物 UGC 账号）不要一并加进去。** 那类每篇都该有人看一眼，
这道闸就是为它们留的。

**② `[publish.price_map]`** —— 正文里出现表外金额的帖子逐篇进人工队列。
`preflight` 会直接把缺的行按 TOML 格式打出来，左边是归档里的真实金额串，
**右边由业务填德国站定价**：

```bash
scripts\run_pipeline.bat preflight --days 180
```

⛔ **程序不做汇率换算，也不许照抄网上的价。** 发错价格是商业事故。

---

## 第 6 步 · 打开流水线

```bash
.venv\Scripts\python.exe pipeline.py activate --g8-verified
```

⚠️ **`--g8-verified` 现在是会被核对的**（2026-09-01 起）：`activate` 会检查
`[publish].ui_constraints_verified` 是不是 `true`，以及
`state\published.jsonl` 里有没有一条真的 `status=scheduled`。
两条缺任一就拒绝激活并打印缺什么。
理由是钱：`assisted` 的 `run` 会**先付费翻译、再付费调图**，
最后才逐篇卡在离线硬闸上 —— 早激活一天就是白花一天的钱。

然后把 `config.toml` 的 `[pipeline].autonomy` 从 `manual` 改成 `assisted`。

**从此以后**：

```
pipeline run       抓增量 → 对账 → 按需翻译/调图 → 排出待确认清单（不碰发布浏览器）
pipeline approve   你点头之后，逐篇真的提交
```

`assisted` 下 `run` **永远不会自己发帖**，最多走到"待确认"。
待确认清单在 `state/needs_human.html`，双击就能看。

**判据**：

```bash
scripts\run_pipeline.bat --status
```

「激活边界」不再是"未激活"，autonomy 显示 `assisted`。

---

## 第 7 步 · 装计划任务（装上才叫"不用人管"）

⚠️ **这一步之前，整个项目没有任何东西在自动跑。**

```bash
.venv\Scripts\python.exe -m tools.schedule install
.venv\Scripts\python.exe -m tools.schedule status
```

装的是两个任务：每日 `pipeline run`，以及独立的死人开关 `FBScraperAlive`
（主任务连着几天没成功跑过就提醒你）。

**判据**：`status` 能查到任务，第二天早上 `state/pipeline.log` 有新记录。

---

## 稳态是什么样

- 每天：计划任务自己抓增量、自己翻译、自己调图，排出待确认清单；
- 你要做的：看一眼 `state/needs_human.html`，`pipeline approve --item-id …`；
- 德语排期固定柏林 10:00 / 17:00，UI 用美西时区，两地夏令时各自换算；
- 每月预算 US$60，超了会停；
- 出问题时它**停下来转人工**，不会硬着头皮发。

---

## 一张表：现在到底差什么

| 步                               | 谁做              | 时间    | 状态            |
| -------------------------------- | ----------------- | ------- | --------------- |
| 1 · 录 v2 探查                   | **你**            | 15 分钟 | ✅ **2026-09-01 完成**，v2 契约首次通过 |
| 1b · 判读 dump                   | 命令 + 判断       | —       | ✅ 见 `PROBE_FINDINGS_20260901.md` |
| 1c · 按现实改证据契约            | 开发              | —       | ✅ 已完成，23 套测试全绿 |
| 2 ·`--check`                     | 命令              | —       | ✅ 六行全 `[OK]`，含同页因果链 |
| 3 ·`--emit` + 填 `ui_probe_dump` | 命令 + 一行配置   | —       | ✅ 三道闸已开 |
| **3b · 填 14 个 IG/排期 UI 上限** | **你**           | ~15 分钟 | ✅ **2026-09-01 完成**，`ui_constraints_verified = true` |
| 3c · 取消远端测试排期            | **你**            | 1 分钟  | ✅ **已取消**（FB `1887083152480681` / IG `4378984725697354`） |
| 4 · G8 真机发一篇                | **你**            | 5 分钟  | ⬜ **当前唯一阻塞项** |
| 5 · 幂等复跑                     | 命令              | 1 分钟  | ⬜              |
| **5b · 两张业务表**              | **你（业务）**    | ~20 分钟 | ⬜ 不填的话上线后每天零产出 |
| 6 · activate + assisted          | 命令 + 改一行配置 | 1 分钟  | ⬜              |
| 7 · 装计划任务                   | **你**            | 1 分钟  | ⬜              |

**这张表会过期，`scripts\run_pipeline.bat preflight` 不会 —— 以它为准。**

**不用重录。** 现有那份 dump 已经包含了新契约要的每一条证据。

想自己看一眼那份 dump 里到底有什么：

```bash
scripts\run_probe_signals.bat --report state\publish_probe_20260901_054226_378622.json
```

---

## 附：这一轮补上的是哪一块

在此之前，第 2 和第 3 步是不存在的 —— 录完 dump 之后要**再开一次开发会话**，
让人（或 Agent）把 dump 读一遍、手写五条 dataclass 塞进 `selectors.py`。
那是这条链上唯一一处"还需要一个懂代码的人在场"的地方。

`tools/probe_signals.py` 把它变成了两条命令，而且**没有放松任何一道闸**：

- 每个字段的值都是从 dump 里抄的，regex 是拿候选表逐个试到能解析为止；
- 推完原路丢回 `publish/evidence.py` 回查 —— 和 `--submit` 上生产闸是同一套代码；
- 定位名会把随帖子/日期变化的部分挖掉，只留固定标签
  （不然「Scheduled for 09/08/2026 10:00 AM」下个月一张卡片都找不到）；
- `[publish].ui_probe_dump` 仍然必须人工填 —— 那道人工签字栏没动。

离线验收在 `tests/tests_probe_signals.py`：完整 dump 五件全推出来、
回查全命中、装载后三道闸真的打开；同时六种残缺 dump（少 IG、少图片数、
没有可信点击、成功提示提交前就在、final 截图缺失、顺序被打乱）**必须全部推不出来**。
后面那半比前面那半重要 —— 一个"看起来验过"的假绿灯，比没有更糟。
