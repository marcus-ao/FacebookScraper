# 人工操作指南

本文件**只讲需要人亲自动手的步骤**。每一步都写了：**怎么做 → 成功长什么样 →
不对时怎么办**。

⛔ **它不记进度。** 进度是算出来的，随时跑这条（只读、零网络、零费用、零写盘）：

```
scripts\run_pipeline.bat preflight
```

它会告诉你还差什么，以及**激活之后每天会发生什么**。
手维护的状态表一定会过期，它不会。

> 下文的"项目根目录"指包含 `README.md`、`config.toml`、`core/` 的目录
> （当前是 `FacebookScraper`）。除非另有说明，命令都从这个目录跑。

| 文件 | 管什么 |
|---|---|
| 本文件 | **需要人亲自动手的步骤** |
| [HANDOFF.md](HANDOFF.md) | 动代码之前必须知道的（红线、架构、真实 UI、踩过的坑） |
| [REQUIREMENTS.md](REQUIREMENTS.md) | 为谁做、做到什么程度算够 |
| [FUNCTIONALITY.md](FUNCTIONALITY.md) | 五个阶段各要实现什么功能（业务访谈后的规划） |
| [CONTEXT.md](CONTEXT.md) | 术语表。词有歧义时以它为准 |
| [OPTIMIAZATION.md](OPTIMIAZATION.md) | 还差什么、先修哪个 |

---

## 0. 稳态：每天实际会发生什么

装上计划任务之后（§6）：

- **每天**：计划任务自己抓增量、自己翻译、自己调图，排出待确认清单；
- **你要做的**：看一眼 `state\needs_human.html`，对满意的执行 `approve`；
- 排期固定**柏林 10:00 / 17:00**，Business Suite UI 用美西时区，两地夏令时各自换算；
- 每月预算 **US$60**，超了会停；
- 出问题它**停下来转人工**，不会硬着头皮发。

⚠️ **排期只能排到当月最后一天。** 到月底 `run` 会说"本月已无可排槽位" ——
那是 UI 的事实，不是故障，进入下个月自动恢复。

⚠️ **计划任务目前还没装**（`preflight` 第 [6] 项会告诉你）。
在 [OPTIMIAZATION.md](OPTIMIAZATION.md) 的 S0 全部清掉之前**不要装** ——
自动化会把每一条 S0 从"偶发一次"变成"每天发生"。

---

## 1. 两个 Chrome（会话过期时重做）

**这两个必须是两个 Chrome、两个 profile、两个端口。**
把持有 DE 发布权的账号和抓取小号放进同一个浏览器指纹里，等于把两件事绑成一件，
而**抓取小号被封是本项目唯一不可恢复的失败模式**。

| | 抓取 | 发布 |
|---|---|---|
| 启动 | `scripts\start_chrome.bat` | `scripts\start_chrome_publish.bat` |
| 端口 | 9222 | 9223 |
| profile | `%USERPROFILE%\.fbscraper-chrome` | `%USERPROFILE%\.fbscraper-publish` |
| 登录谁 | 抓取专用小号 | `Neakasa Deutschland` / `neakasa.de` |

### 成功长什么样

```
使用 Chrome: C:\Program Files\Google\Chrome\Application\chrome.exe
专用 profile: C:\Users\<你>\.fbscraper-chrome
调试端口:     9222

等待调试端口就绪....
[ok] 调试端口 9222 已就绪。
```

同时弹出一个**全新的、什么都没登录的 Chrome 窗口**。

**你日常用的 Chrome 可以照常开着，不冲突** —— Chrome 的"同一实例"限制是按
profile 目录算的。

### 登录（只做这一次，只在这里做）

1. 在**那个窗口**里访问 `https://www.facebook.com/`，用小号登录，走完二次验证；
2. 同一窗口访问 `https://www.instagram.com/`，同样登录；
3. **验证会话持久化**：完全关掉窗口 → 重新双击 `.bat` → 打开 facebook.com，
   确认**仍是登录态**。

### ⚠️ 三条必须遵守的

1. **绝对不要用主账号或持有 DE 资产的账号做抓取。**
2. **不要在抓取那个窗口里做日常浏览。** 它每天都要代表你访问一次 FB 和 IG，
   混进日常浏览会让指纹变脏。**而且它得一直开着**，关了每日增量就跑不了。
3. **全项目只有这一条登录路径。** 代码里没有任何自动登录，**也不要加**。

### 不对时怎么办

| 现象 | 原因 | 处理 |
|---|---|---|
| `[i] 端口 9222 已在监听` | 已经在跑了 | 正常，直接下一步 |
| 等 15 秒仍未监听 | profile 目录被残留进程占着 | 任务管理器结束掉用这个 profile 的 `chrome.exe`，重跑 |
| `[!] .venv not found` | 环境没建 | 先跑 `scripts\setup.bat` |
| 找不到 Chrome | 装在非标准路径 | 把完整路径填进 `config.toml` 的 `[chrome].exe` |
| 重开后又要求登录 | profile 目录没写成功 | 确认目录存在且非空；检查安全软件是否拦了写入 |
| 登录时出现 checkpoint | —— | **换个时间再试，不要连续重试。** 连续失败的登录尝试本身就是风险信号 |

---

## 2. 人工滚动回填（换目标账号时才做）

两个平台各约 **20 分钟**。

**为什么要人滚**：滚的确实是人，没有任何可识别的自动化行为特征。
脚本全程不驱动页面，只在旁边把浏览器自己发出的接口响应捞下来。
**这是拿 20 分钟换掉一整类封号风险。**

### 怎么做

**前置**：§1 的抓取 Chrome 开着，小号已登录。

```
scripts\run_backfill.bat facebook
```

脚本会在那个 Chrome 里打开目标主页，然后等你滚。**切到 Chrome 窗口开始往下滚**：

- **慢滚。** 每屏停一下，等图片真的显示出来再继续。
  **图片没加载 = 那批数据没请求 = 没捞到。**
- **用鼠标滚轮或方向键，不要按 End。** 一次跳到底会跳过中间的加载。
- 时不时瞄一眼终端里的计数器，数字在涨说明在捞到东西。
- 一直滚到**看见最早的一条帖子**（页面不再加载新内容）为止。

滚完切回终端**按 Enter**。然后 `scripts\run_backfill.bat instagram` 重复一遍。

### 成功长什么样

```
原始响应已转储 → D:\...\archive\fa_xxx\_capture_1756....json  (2048 KB)
解析出 37 篇帖子
  + 1234567890  3图/0视频  Summer sale starts now
新增 37 篇
```

📌 **原始响应会被完整保存**，所以解析器以后修好了可以用 `tools.replay` 重建归档，
**不用让你重滚**（历史上已经这样救过两次）。

---

## 3. 重录探查（Meta 改版之后做）

`tools/_scaffolding/` 一年最多用一两次，就是为了这个。
Meta 改版不是"如果"是"什么时候"。

### 3.1 录一份 v2 探查（约 15 分钟，只有人能做）

程序不会替你提交测试帖，也不会替你取消。**这一步必须是真人操作。**

```
scripts\start_chrome_publish.bat
.venv\Scripts\python.exe tools\_scaffolding\probe_publish.py
```

然后在浏览器里**完整走一遍**，顺序不能乱（证据链是按先后顺序验的）：

1. 进内容日历 → **Create post** → 进 composer；
2. 传 5 张图、填一段正文（**记住其中一句**，下一步要用）；
3. **让"发到哪个 Page / 哪个 IG 帐号"那一块露出来**，两个账号名同时在屏幕上停一两秒
   —— ⚠️ **这一步最容易漏，漏了整条链就断在起点**；
4. 打开定时开关，排一个**几天后**的时刻（不要排今天）；
   ⚠️ **两个渠道各有一套日期/时间控件，两套都要设**（CR-73）；
5. **用鼠标点提交**（不要用开发者工具触发，那不是可信事件）；
6. 成功提示**让它自己停两秒**，不要马上点走；
7. 进内容日历，**等日历数据真的渲染出来**，把刚排的卡片滚进视口；
8. **保持这一页不动**，回终端按 Enter 停止录制，**等它打完收尾再关窗口**。

> ⚠️ 第 8 步的"等它打完收尾"不是客套。`finished_at` 没写进去的 dump **直接判死**，
> 前面 15 分钟白走 —— 这个项目已经这样白走过一次（CR-64 / CR-66）。

**判据**：`state\` 下多出 `publish_probe_<时间戳>.json` 和同名 `_screenshots\` 目录。

### 3.2 30 秒确认这次录制成不成立（先跑这个，别急着往下）

```
scripts\run_probe_signals.bat --check state\publish_probe_<时间戳>.json --caption "正文里的一小句"
```

**判据**：六行全是 `[OK]`（五件证据 + 同页因果链）。

**过不了怎么办**：每条 `[缺]` 后面直接印着"补录：要做什么"。最常见三种：

| 报的是 | 说明 §3.1 漏了哪一下 |
|---|---|
| 账号上下文推不出来 | 漏了第 3 小步 —— composer 上两个账号没同时露过面 |
| 成功信号推不出来 | 第 6 小步太快，提示还没被采样就跳走了 |
| 排期卡片推不出来 | 第 7 小步日历数据没等到，或卡片上少了某一格 |

补录就是把 §3.1 重走一遍（旧 dump 留着不碍事）。
多个成功提示候选时用 `--success-name 关键词` 指定一个。

标着 `⚠ 脆弱` 的行**不拦你**，但值得看一眼：那是"这一格上只有会变的文字"。
失配时 G6c 会**转人工**，不会误判成已排期。

### 3.3 落盘

```
scripts\run_probe_signals.bat --report <dump>    推不出来时看它：摊开 dump 里真实存在的语义
scripts\run_probe_signals.bat --emit <dump>      推导 → 回查 → 写 publish/signals_backfilled.py
```

⛔ `publish/signals_backfilled.py` 是**生成文件**，不要手工编辑 ——
下一次 `--emit` 会整份覆盖。

然后把新 dump 文件名填进 `config.toml` 的 `[publish].ui_probe_dump`。

### 3.4 重新量那 14 个 UI 上限

改版后 UI 上限可能变。当前实测值与两条"照直觉写就会错"的坑见
[HANDOFF.md §5.4](HANDOFF.md)。量完把 `[publish].ui_constraints_verified` 改回 `true`。

📌 也可以非交互补填：
`tools\_scaffolding\probe_publish.py --fill-notes <dump> --set-note K=V`

---

## 4. 发一篇（单帖 / G8 验收）

### 4.1 先看清楚要发什么（零风险，随时可跑）

```
scripts\run_publish.bat --latest 3
```

零浏览器、零网络、零写盘、零费用。看德语正文、每张图**是德语图还是回退的原图**、
合作帖原作者、全部告警。

⚠️ **重点看 `image_sources`。** 出现 `original` 意味着那张图**图内可能仍是英文** ——
这是 [OPTIMIAZATION.md](OPTIMIAZATION.md) S0-1 那条，目前没有硬闸拦它。

### 4.2 真发

```
scripts\run_publish_post.bat --post-id <id> --at 2026-09-10T10:00 --submit
```

流程：核对是不是 `Neakasa Deutschland` → 查远端没有同槽 → 传图 → **再核对一次主页**
→ 填正文 → **两个渠道各设一次时刻** → 点**一次**提交 → 等成功提示 → 回内容日历回读
→ 记 `scheduled`。

中途会停下来让你确认（`require_confirmation = true`）。**看清楚正文和图片再回 `yes`。**

⚠️ **`--at` 只能填当月内的日期** —— composer 的日期选择器不允许跨月，
排到下个月会在碰浏览器之前被离线硬闸拦下。

**退出码怎么读：**

| 码 | 含义 | 你要做什么 |
|---|---|---|
| 0 | 成功 | 往下走 |
| 2 | 离线硬闸拦下，**没碰浏览器** | 看它打印的原因 |
| 3 | 上次留了未结转状态 | 见 §4.4 |
| 4 | **提交结果不明确** | ⛔ 禁止重跑，见下 |
| 5 | 提交了但回读不到 | ⛔ 禁止重跑，见下 |
| 6 | 证据门禁关着 | 回 §3 |

⛔ **4 和 5 绝对不要自动重试** —— 那是唯一会导致重复发帖的路径。
去 Business Suite **人工确认远端到底排上没有**，然后用 §4.4 结转。

失败时截图在 `state\publish_failures\`，程序**不会**替你删任何草稿。

### 4.3 亲眼确认 + 幂等复跑

去内容日历确认那条**同时存在 Facebook 和 Instagram 两条**
（它们是两个独立对象、两个 remote ID）。

然后**同一条命令原样再跑一次**。判据：立刻说"已经排过了，永久跳过"，
**一次浏览器操作都没有**。

> 定时任务每天跑，**重复发帖是唯一会被粉丝看见的错误**，所以必须亲手验一次。

### 4.4 结转未闭合的留痕

`state\published.jsonl` 里有 `failed_pre_submit` / `submit_ambiguous` /
`submitted_unverified` 记录时，下一次同 `post_id` 会直接退 3 停住，`--force` 也绕不过去。
**这是有意的**：那三种状态意味着"远端可能已经排上了"。

先去 Business Suite 看一眼，然后：

```
scripts\run_publish_post.bat --post-id <id> --mark-scheduled       远端确实排上了
scripts\run_publish_post.bat --post-id <id> --mark-not-scheduled   远端确实没有
```

⛔ **不要为了让 `activate` 过去而用 `--mark-scheduled` 造一条假的。**
远端没有那条帖子的话，那样写进去的是假的，**而且会让这个 post_id 永久跳过、
以后再也发不出去。**

---

## 5. 激活与批准

### 5.1 激活（只做一次）

```
scripts\run_pipeline.bat activate --g8-verified
```

`--g8-verified` **会被核对**：检查 `[publish].ui_constraints_verified` 是 `true`，
且 `published.jsonl` 里真有一条 `status=scheduled`。缺任一就拒绝。

> 为什么要拦：激活早了不是顺序不好看，是**真花钱** —— assisted 的 run 会先付费翻译、
> 再付费调图，最后才逐篇卡在离线硬闸上，而且**每天一次**。

然后把 `config.toml` 的 `[pipeline].autonomy` 改成 `"assisted"`。

### 5.2 跑一次

```
.venv\Scripts\python.exe -m pipeline run
```

⚠️ **联调阶段用这条，不要用 `scripts\run_pipeline.bat run`** ——
那个 `.bat` 会把输出重定向进 `state\pipeline.log`，屏幕上什么都看不到
（那是给无人值守的定时任务用的）。

**刚 activate 完第一次大概率打印"激活边界之后还没有新帖" —— 那是正常的**，
流水线不补发历史，要等下一次增量抓到新帖才有事做。它会明说这句话。

### 5.3 批准

双击打开 `state\needs_human.html`。页面上能看到每篇的德语正文、图片张数、
是不是合作帖，下面是可以整行复制的命令：

```
.venv\Scripts\python.exe -m pipeline approve --item-id ready-to-publish-xxxxxxxx
```

⚠️ 页面上标着"**不能**用 approve 结转"的那些是硬闸 ——
改配置或补素材后重跑 `run` 才会消失，对它们敲 approve 会被顶回来。

---

## 6. 计划任务

```
.venv\Scripts\python.exe -m tools.schedule install     # 先跑 --dry-run 看看
.venv\Scripts\python.exe -m tools.schedule status
.venv\Scripts\python.exe -m tools.schedule remove
```

装三个：每日 `pipeline run`、登录/解锁补跑、以及死人开关 `FBScraperAlive`
（主任务连着几天没成功跑过就提醒）。

**判据**：第二天早上 `state\pipeline.log` 里有新记录。

⛔ **装之前先清掉 [OPTIMIAZATION.md](OPTIMIAZATION.md) 的 S0。**

---

## 7. 付费账本卡住时

任何一次付费请求没有正常闭合（进程被杀、断电、崩溃），
账本会留下一条未闭合记录，**并阻断全部后续付费**。这是设计，不是故障 ——
它把未知超额限制在一次请求。

```
.venv\Scripts\python.exe -m core.paid_requests --status
.venv\Scripts\python.exe -m core.paid_requests --resolve <ID> --as accepted|rejected
```

`accepted` = 产物确实落盘了；`rejected` = 没落盘。
**先确认真实结果再结转**，这条命令会写进费用真相源。

---

## 8. 命令速查

> 全部在 2026-09-09 核对过确实存在。

### 双击类（项目根目录）

| 命令 | 作用 |
|---|---|
| `scripts\setup.bat` | 建环境、装依赖、跑全部 22 个离线测试（只需跑一次） |
| `scripts\start_chrome.bat` | 起**抓取**用 Chrome（9222） |
| `scripts\start_chrome_publish.bat` | 起**发布**用 Chrome（9223） |
| `scripts\run_backfill.bat facebook\|instagram` | 回填（需人工滚动） |
| `scripts\run_pipeline.bat` | 一张表看完四阶段积压、激活边界、待确认项、费用（零风险） |
| `scripts\run_pipeline.bat preflight` | **⭐ 还差什么 + 激活后每天会发生什么**（只读） |
| `scripts\run_pipeline.bat activate --g8-verified` | 只在 G8 通过后原子激活；重复运行不移动边界 |
| `scripts\run_pipeline.bat run` | manual 只对账；assisted 自动增量/翻译/调图，**不碰发布浏览器** |
| `scripts\run_pipeline.bat approve --item-id <ID>` | 批量确认；重做离线硬闸、分配空槽、逐篇提交 |
| `scripts\run_pipeline.bat check-alive` | 死人开关手查一次 |
| `scripts\run_publish.bat --latest 3` | 离线看"要发出去的到底长什么样"（零风险） |
| `scripts\run_publish_post.bat --post-id <id> --at <ISO>` | 填进 composer 并**停在提交前** |
| `scripts\run_publish_post.bat ... --submit` | 显式提交一次并回读；缺证据时在碰浏览器前失败闭合 |
| `scripts\run_publish_post.bat --post-id <id> --mark-scheduled` | 人工确认已排期，结转 |
| `scripts\run_publish_post.bat --post-id <id> --mark-not-scheduled` | 人工确认没排期，结转 |
| `scripts\run_probe_signals.bat --status` | 三道生产闸现在是开是关 |
| `scripts\run_probe_signals.bat --check\|--report\|--emit <dump>` | 见 §3.2 / §3.3 |

### 翻译（`scripts\run_translate.bat`）

`--check`（极小请求验配置）· `--show-prompt`（不调 API）· `--estimate`（不调 API）·
`--dry-run` · `--limit N` · `--account <名>` · `--post-id <id>` · `--latest-posts N` ·
`--force` · `--review`（生成审校清单）

### 调图（`scripts\run_images.bat`）

`--check` · `--show-prompt` · `--estimate` · `--dry-run` · `--account` · `--limit` ·
`--post-id` · **`--media-index N`**（`N=0` 对应 `01.jpg`，一次付费调用只处理一张）·
`--latest-posts` · `--force` · `--all-history --confirm-all-history-cost`

### 命令行类（前面加 `.venv\Scripts\python.exe -m`）

| 命令 | 作用 |
|---|---|
| `pipeline status\|preflight\|check-alive\|activate\|run\|approve` | 同上，但不重定向输出 |
| `routes.delta --status` | 看增量这几天在不在跑（**不联网、零风险**） |
| `routes.delta --dry-run --no-jitter` | 空跑一次增量，只看不写 |
| `routes.delta --no-jitter` | 真跑一次增量 |
| `routes.delta --reset-failures` | 失败预算用尽、已处理完，清零 |
| `tools.schedule xml\|install\|status\|remove` | 计划任务 |
| `tools.layout index\|reindex facebook\|instagram` | 重新生成 / 修复 `index.html` 总览 |
| `tools.replay instagram --dry-run` | 用保存的原始响应重建归档 |
| `core.notify` | 发一条测试通知 |
| `core.paid_requests --status\|--resolve` | 见 §7 |

> 上面大部分支持 `--dry-run`，先看结果再决定要不要真跑。

---

## 9. 产物都在哪

```
FacebookScraper\
  config.toml                     全部可调参数（含术语表、价格表、白名单）
  prompts\translate_de.md         翻译提示词本体，可直接编辑，不用改 Python
  prompts\image_de.md             图片提示词本体
  scripts\                        ← 要双击的入口都在这里
  docs\                           ← 四份文档
  archive\<平台前缀>_<账号>\
    manifest.jsonl                派生索引（真相是每帖文件夹）
    translated.jsonl              译文真相源（花钱买的，重抓不会冲掉）
    images_de.jsonl               德语图真相源
    review.md                     人工审校清单（--review 生成）
    index.html                    双击看总览
    _capture_*.json               原始响应，解析器修好后可 replay 重建
    posts\<日期>_<时刻>_<post_id>\
      post.json                   ← 真相源
      text.txt / text_de.txt      英文原文 / 德语译文（派生副本）
      01.jpg 02.jpg …             原图（只读，抓取产物）
      media_de\01.jpg …           德语图（**人工放的程序不得覆盖**）
  state\
    published.jsonl               发布留痕与幂等（**不可重建**）
    paid_requests.jsonl           付费账本
    needs_human.html / .jsonl     待确认项
    pipeline.log / delta.log      无人值守时的运行记录
    alerts.log                    告警
    publish_probe_*.json          证据 dump（**不进版本库**）
    publish_failures\             失败截图
```

---

## 10. 卡住时给我什么

1. **你跑的完整命令**（原样复制）；
2. **终端输出的最后 30 行**；
3. `scripts\run_pipeline.bat preflight` 的输出；
4. 涉及发布的话：`state\publish_failures\` 里最新那张截图，以及
   `state\published.jsonl` 的最后一条；
5. 涉及付费的话：`.venv\Scripts\python.exe -m core.paid_requests --status`。

⛔ **不要发 `.env` 或任何密钥。**
