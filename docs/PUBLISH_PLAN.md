# G 组实施计划 · Business Suite 定时发布（Playwright + 登录态 CDP）

> 对应 `IMPLEMENTATION_PLAN.md` 的 G 组。本文件是 G 组的任务书。
>
> **建立日期：2026-08-31。状态：G0 已完成并实机验收；G0b 代码与离线验收完成，
> 但计划点名的最新 3 篇尚无当前译文，真实验收未勾；G1 记录工具已就绪、需你操作；
> G2–G8 仍被 G1 阻塞。**
>
> **开发分支：`feat/business-suite-publish`**（详见第 12 节的分支与文件所有权约定）。
> 本组与 K 组（`feat/image-de`）**并行开发**，
> 第 12 节列了两条分支各自能碰哪些文件——**动别人的文件之前先看那张表**。

---

## 0. 用户拍板的范围（2026-08-31，不要重新讨论）

| 项 | 决定 |
|---|---|
| 发布目标 | **Facebook DE Page + Instagram DE，同时发** |
| 历史存量 | **不补发。** 1051 篇译文只归档，不进发布 |
| 本期目标 | **用最新几篇做端到端验证，把功能跑完整** |
| 上线后 | **跟着每日增量走**：US 站发新帖 → 翻译 → 调图 → 排期发 DE |

**这个决定把 G 组从"发布队列子系统"缩回成"发一篇的函数 + 一个跟增量的挂钩"，
工作量小一个量级。** 不要自作主张把队列/优先级/批量补发做回来。

---

## 1. 三个必须先摆在台面上的事实

### 1.1 能发的帖子比想象的少得多：**470 篇，不是 1051 篇**

`1051 条待译正文` 是**翻译**的口径。发布的口径不一样——
**项目约定「视频只记元数据、不下载」**，所以纯视频帖没有可上传的素材。

对真实归档实测（2026-08-31）：

| | 总数 | 有正文 | **图文可发** | 纯视频（发不了） | 无媒体 |
|---|---:|---:|---:|---:|---:|
| Facebook | 47 | 46 | **27** | 17 | 2 |
| Instagram | 1020 | 1011 | **443** | 568 | 0 |
| **合计** | 1067 | 1057 | **470** | 585 | 2 |

本期不补发，所以这个数暂时不影响工作量；
**但它必须写下来**，否则哪天有人决定补发，会按 1051 去估工期和预算。

### 1.2 最新那几篇（也就是要拿来做端到端测试的）**大多是合作帖**

```
IG 3975547640610092585  2026-08-31  1 图  owner=neakasa.global   （本账号是 coauthor）
IG 3973012230169803390  2026-08-27  5 图  合作帖
FB  122123185335379375  2026-08-27  5 图  原创
```

用户已拍板「合作协议已覆盖，全部进流水线」（见 `HANDOFF.md`），
**这里不重新劝阻**。但发布环节必须做一件事：**在输出里点名原作者**，
让操作者在按下"排期"之前看得见自己在发谁的内容。
`review.md` 和 `index.html` 已有同款提示，发布侧要接上，不能到最后一环反而没了。

**FB 与 IG 在 2026-08-27 各有一篇 5 图帖**，是同一波内容的两个平台版本——
**这两篇是端到端测试的首选**（G8）。

### 1.3 UI 自动化没有事务性，这决定了整组的设计取向

翻译失败了重跑一次就行；**发布失败可能留下一个半成品草稿，或者一条已经排上的帖**。
所以：

- 每一步都要**回读校验**（填完读回来比对），不是"点了就算成功"；
- 失败必须**截图 + 打印当前 URL + 说清停在哪一步 + 提示检查草稿残留**，
  **不自动删除任何东西**；
- `state/published.jsonl` 是**幂等与留痕**的唯一依据，跑两次不能发两次。

---

## 2. G0（前置改造）· 发布必须用**独立的 Chrome profile**

**这是红线，不是优化项。** `MANUAL_STEPS.md` 早就写了：
发布账号要用**另一个专用 profile**，与抓取小号分开，避免指纹关联。
抓取小号被封是本项目唯一不可恢复的失败模式；
把持有 DE 发布权的账号和它放进同一个浏览器指纹里，等于把两件事绑成一件。

**但当前代码做不到**：`core/config.py` 只有一个 `profile_dir` / `debug_port`，
`core/chrome.py` 的 `launch()` / `attach()` 直接读全局 `cfg()`。

改造内容（**不改变任何现有调用方的行为**）：

- `config.toml` 新增 `[publish].profile_dir` 与 `[publish].debug_port`（默认 **9223**）。
- `core/chrome.py` 的 `launch()` / `attach()` / `cdp_ready()` 接受**显式的 port 与 profile**，
  不给就退回读 `[chrome]`——现有 backfill / delta 一行都不用改。
- 新增 `scripts\start_chrome_publish.bat`（**纯 ASCII + CRLF + 无 BOM**，
  `tests_schedule.py` 那套字节级断言要扩过来）→ `tools/start_chrome_publish.py`。
- ⚠️ **两个 Chrome 会同时开着**（抓取的 9222、发布的 9223）。
  `tools/start_chrome.py` 里那条"profile 被占用会静默复用已有实例"的坑
  在这里同样成立，提示文案要区分是哪一个。

**【验收】** 两个 profile 同时跑，`cdp_ready(9222)` 与 `cdp_ready(9223)` 都为 True，
且各自的登录态互不可见（在发布 Chrome 里打开 instagram.com 不是抓取小号）。

> **G0 不依赖 G1，现在就能做。** 它是整组唯一一件不需要真实 DOM 的事。
>
> **完成：2026-08-31。** `launch()` / `attach()` 接受显式 port/profile，
> `cdp_ready()` 不给 port 时保持读 `[chrome]`，显式给 profile 时还会在 Windows
> 只读核对监听进程的 `--user-data-dir`；端口是 CDP 但 profile 不对也失败闭合。
> 配置把两侧端口或 profile 误写成相同值时同样拒绝启动。现有 backfill/delta 零改动。
> 实机同时拉起 9222 与 9223 后两者均为 True：抓取 profile 有 FB/IG 登录 cookie，
> 新建的发布 profile 两边 cookie 都为 0（只比较 cookie 名，不读取/打印值），
> 登录态互不可见。入口为 `scripts\start_chrome_publish.bat`。

---

## 3. G1 · 真实 DOM 探查（**需要你操作，且它阻塞 G2–G7**）

> ⛔ **全局红线 5：不得凭猜测编写 Business Suite 的选择器。**
> Business Suite 是 React SPA，class name 是构建期混淆的，
> 任何"看起来合理"的选择器都是错的。**这一步之前，一行选择器都不写。**

### 3.1 交付物：`tools/probe_publish.py`（已实现，运行需要你）

**不用 `playwright codegen`。** 它产出的是脆的 CSS 选择器
（`div > div:nth-child(3) > span`），混淆 class 一变就全废。
我们要的是**抗混淆的定位**：`role` + 可访问名（aria-label / 文本）+ `data-testid`。

探查工具做的事是**记录，不是驱动**：

1. 附着到发布 profile 的 Chrome（9223）；
2. 你手工走一遍完整的定时发帖流程；
3. 程序在旁边监听点击/输入事件，把每次交互命中的元素的**稳定属性**
   （tag / role / aria-label / data-testid / name / placeholder / 可见文本 / 是否 contenteditable）
   dump 成 `state/publish_probe_<时间戳>.json`；
4. 同时在每一步存一张截图，便于事后对照"这一步到底点的是哪个控件"。

实际入口：

```bat
.venv\Scripts\python.exe tools\probe_publish.py
```

工具对 click / input（700ms 去抖）/ change / submit 逐条原子刷新 JSON，
点击命中内层图标或 `<span>` 时还会记录最多 8 层语义祖先；不记录 class/CSS path、
cookie 或密码输入值。结束时会让操作者原样补记入口 URL、FB slug、UI 时区、
定时上下限与成功信号。不知道的项留空，绝不猜。

> **当前状态：工具与离线 recorder 测试已完成，但用户尚未实际跑 G1。**
> 因此 `publish/selectors.py` 仍只有 TODO，没有任何定位常量；G1 不得勾选。

**与回填的人工滚动是同一条设计**：驱动页面的是人，程序只在旁边捞。

### 3.2 你要走一遍并让程序记下来的七个点

| # | 要回答的 | 为什么它非问不可 |
|---|---|---|
| 1 | 创建帖子的入口（URL + 按钮） | SPA 的 URL 可能不变，得知道靠什么进 |
| 2 | 图片上传控件是 `<input type=file>` 还是拖拽区 | 决定用 `set_input_files()`（稳）还是模拟拖拽（脆） |
| 3 | 文案框是 `<textarea>` 还是 contenteditable | contenteditable 的**换行要逐行 type + Shift+Enter**，直接 fill 会把多段挤成一段 |
| 4 | **FB 与 IG 两个渠道的勾选控件在哪** | 用户要**同时发**，这是本次范围里新增的一路 |
| 5 | 定时开关的位置 | — |
| 6 | **日期/时间选择器：能不能直接输入，还是必须点选；以及 UI 显示的是哪个时区** | ⚠️ **这是整组最容易出错的一步**，见 3.3 |
| 7 | 提交按钮 + 提交成功的确认信号（toast / 跳转 / 排期列表出现） | 没有明确成功信号就只能"点完就当成功"，那等于没有验收 |

**还要记两个数**：定时窗口的 UI 下限（最早能排多久之后）与上限（最晚能排多远）。
Graph API 那边是 10 分钟 – 75 天，**但 UI 的限制不一定一样，必须实测**。

### 3.3 ⚠️ 时区：这一步错了，帖子会在错误的时间发出去，而且没人会立刻发现

Business Suite 显示的时间**跟 Page 的时区设置走，不一定是你电脑的本地时区**，
也不一定是 DE 站该用的 `Europe/Berlin`。三者可能各不相同。

处理方式（不许省）：

1. G1 探查时**原样记录 UI 上显示的时区字符串**；
2. 代码里**显式做时区转换**，不依赖本地时区隐式生效；
3. 设置完成后**回读 UI 上显示的日期时间**并与目标值比对，不一致就失败；
4. 跨日、跨月、**以及夏令时切换日**各测一次
   （`Europe/Berlin` 每年切两次，那两天是最容易出错的）。

### 3.4 【验收】

`publish/selectors.py` 存在，含**至少 7 个带注释的定位常量**，
每个注释写清「对应哪一步、是从哪份 probe dump 得来的、什么信号说明它失效了」。
外加定时窗口上下限与时区行为的实测记录。

---

## 4. G2–G7 · 实现（**被 G1 阻塞**）

G1 之前，这几项只写**函数签名 + 契约 + 测试骨架**，不写选择器。

### G2 · `ensure_logged_in(page) -> bool`
- 打开 Business Suite，判断是否已登录**且**当前上下文是那个 DE Page。
- 未登录：**打印提示并退出**。❌ **不得实现自动登录**（全局红线 1）。
- ⚠️ 还要判"登录的是不是对的账号"——登录态在、但选中的是别的 Page，
  会把德语内容发到错误的主页上，这比没登录严重得多。
- **认 Page 用 `[publish].facebook_page_name`（显示名 `Neakasa Deutschland`）**，
  它就是主页切换器里显示的字符串。**不要用 `facebook_page_slug`**——
  那个当前是空的，而且发布链路全程不拼 URL（见第 10 节）。
  IG 那侧用 `instagram_account`（`neakasa.de`）。
- 【验收】未登录时提示清晰；登录了但 Page 不对时**也**要拦住。

### G3 · `upload_images(page, paths) -> None`
- 优先 `set_input_files()`。
- 上传后**等缩略图出现**再继续，不用固定 `sleep`。
- 【验收】1 张与 5 张（真实归档里最新那两篇就是 5 图）缩略图数量正确。

### G4 · `fill_caption(page, text) -> None`
- contenteditable 时按 3.2 第 3 点处理换行。
- **填完回读比对**，不一致抛错。
- 【验收】含换行、空行、emoji、德语变音（ä/ö/ü/ß）、`#标签`、`$金额`
  的文案能正确填入并回读逐字符一致。
  > `ß` 和 `⚠` 这类字符在本机（代码页 936）曾直接炸掉输出，
  > 入口必须调 `core.console.force_utf8()`（见附录 C）。

### G5 · `set_schedule(page, when) -> None`
- 按 3.3 处理时区，**设完回读比对**。
- 【验收】跨日 / 跨月 / **夏令时切换日**各一次，UI 回读一致。

### G6 · `submit(page) -> str | None`
- 必须等**明确的成功信号**，不能提交完就返回。
- 成功后写 `state/published.jsonl`。
- 【验收】排期列表里能看到该帖。

### G7 · 失败处理与残留清理
- 截图存 `state/publish_failures/<时间戳>.png`；
- 打印当前 URL + 停在哪一步 + **提示人工检查草稿残留**（不自动删）；
- 【验收】人为把某个定位改错 → 有截图、有明确的失败步骤。

---

## 5. `publish/compose.py` · 发布前的硬闸（可以先于 G1 写）

把「一篇 DE 帖」从归档里组装出来，并在**碰浏览器之前**全部校验完。
UI 自动化最贵的是时间，最险的是半成品——能在离线阶段拦下的，绝不留到线上拦。

一篇 `DePost` = 译文 + 图 + 排期时刻：

| 来源 | 规则 |
|---|---|
| 正文 | `translated.jsonl` 里 `text_de`，**且 `prompt_version` 必须是当前版**。过期译文不发 |
| 图片 | 优先 `posts/<帖子>/media_de/`；**缺失时回退原图并显式告警**（发的是带英文的图） |
| 排期 | 由调用方给，必须落在 G1 实测的 UI 窗口内 |

**离线硬校验（任一不过就不发）**：

1. 译文存在、非空、版本当前；
2. **金额逐字符未被改动**——直接复用 `translate.py::money_preserved`，
   不重写第二份。改价格是商业事故，这道闸在发布环节要再过一次；
3. 至少 1 张图，且每张能被 Pillow 打开、非 0 字节；
4. **IG 平台约束预检**（画幅、单帖图片数、正文长度、标签数）——
   ⚠️ 这几个数**以 G1 实测 UI 的实际拒绝行为为准**，
   不要照抄网上流传的数字然后当成事实写进代码注释；
5. **合作帖：在输出里点名原作者**（第 1.2 节），不拦截。

【验收】对最新 3 篇真实帖组装成功；人为把译文改过期 / 删掉一张图，
各自被正确拒绝且说清原因。

> **实现状态（2026-08-31，未勾验收）：** `publish/compose.py` 已完成当前译文与
> 源正文指纹、`translate.money_preserved`、Pillow 解码/非零字节、残缺轮播、
> `media_de` 逐图优先/原图逐图告警回退、合作帖原作者提示、显式时区 datetime 等硬闸。
> IG 四类限制与定时窗口使用“必须带 probe dump 来源”的可注入契约；G1 前严格发布模式
> 会失败闭合，绝不消费 `10 分钟/75 天` 的 API 占位值。
>
> 真实归档复核：现有当前版本译文中 5 篇图文帖可组装，1 篇纯视频被正确拦下；
> 但计划点名的最新三篇 `3975547640610092585` / `3973012230169803390` /
> `122123185335379375` 在各自 `translated.jsonl` 中都没有译文，均被正确拦下。
> 所以“最新 3 篇成功”尚未通过，按协议不把 G0b 勾成完成。

---

## 6. 幂等与留痕：`state/published.jsonl`

每条记：`post_id` / 平台 / `scheduled_at`（含时区）/ 提交结果 /
Business Suite 返回的标识（若有）/ 截图路径 / `text_de_sha256` / 用了哪些图。

- **同一 post_id + 平台已有成功记录 → 跳过**，除非显式 `--force`。
- 这是"UI 自动化没有事务性"的唯一补偿：出了事，靠它回答
  "到底发出去了什么、什么时候、用的哪版文案和哪版图"。

【验收】连跑两次，第二次全部跳过、零浏览器操作。

---

## 7. G8 · 端到端验收

- 用 **FB `122123185335379375`（2026-08-27，5 图）** 与
  **IG `3973012230169803390`（同日，5 图）** ——同一波内容的两个平台版本。
- 排一个**几天后**的时刻，验证后取消。
- 【验收】Business Suite 已排期列表里出现该帖，**时间、文案、图片全部正确**；
  德语文案与 `review.md` 里那版逐字符一致。
- 【记录】从调用到完成的耗时、中途人工介入次数、UI 实际接受的定时窗口。

---

## 8. 与每日增量的挂钩（上线后的形态）

```
routes/delta.py 抓到新帖
   → translate.py            德语译文
   → localize_images.py      德语图（K 组）
   → publish/…               排期发布
```

### ⚠️ 我要提一次的顾虑（提完就按你的决定做）

**自动发布到品牌主页是不可回滚的对外行为。** 抓取错了可以重抓，
翻译错了可以重译，**发出去了只能删帖**——而删帖本身在粉丝侧是可见的。

所以我把这条链路的最后一棒**默认设成需要人工确认**：
`config.toml` 的 `[publish].require_confirmation = true`，
程序把待发清单打出来（原作者、译文、图、排期时刻），你点头才提交。

**这是一行配置，你想全自动就改成 `false`。**
建议至少前几周保留——理由和 C7 那七条一样：这类风险的反馈是延迟的，
而且第一次反馈通常就是最贵的那次。

---

## 9. 顺序与阻塞

```
G0（独立 profile）   ← 现在就能做，不依赖任何人
compose.py 硬闸     ← 现在就能做（不碰浏览器）
        ↓
G1 探查（**需要你**：另起一个 profile，登录 DE 发布账号，手工走一遍）
        ↓
G2 → G3 → G4 → G5 → G6 → G7
        ↓
G8 端到端（需要 F 的译文 + K 的德语图）
```

**当前 UI 实现的唯一外部卡点是 G1。** G0 已完成；compose.py 已完成当前可离线
验证的部分，但最新三篇还需要 F 组当前译文，IG/定时数字还需要 G1 dump。
G2–G7 只有会在接触 page 前明确报 `ProbeRequired` 的函数签名——**这是设计，不是拖延**。

---

## 10. 待你提供 / 拍板的项　（2026-08-31 结清两项，剩一项）

### ✅ 已提供：DE 站的发布目标

```toml
facebook_page_name = "Neakasa Deutschland"   # ← 显示名
facebook_page_slug = ""                      # ← 待 G1 顺手补
instagram_account  = "neakasa.de"            # ← handle
```

> ⚠️ **注意这两个值的形态不一样，别混用。**
> 用户给的 FB 值是**主页显示名**（含空格），
> **不是 URL 里那一段**——`facebook.com/Neakasa Deutschland` 不是合法地址。
>
> 这个项目已经因为"显示名 ≠ URL 账号名段"踩过一次：CR-12 里
> FB 的 `actors[0].name` 是 `"Neakasa Official"`，而 URL 里是 `neakasaofficial`，
> 当时误用显示名做判等直接导致跨账号污染。
>
> **好消息是发布链路不需要那个 slug**：G2 在 Business Suite 的主页切换器里
> **按显示名**认 Page，全程不拼 URL。所以 `facebook_page_slug` 留空不阻塞，
> **G1 探查时从地址栏抄一下填上即可**（只填账号名那一段）。

### ✅ 已就绪：账号与密钥

- 发布账号必须与抓取小号**不同**、有 DE Page 发布权限，
  登录在新 profile `%USERPROFILE%\.fbscraper-publish`（G0 建）。
- `IMAGE_API_KEY` 与 `DEEPSEEK_API_KEY` 都已在 `.env` 里（已核对可读）。

### ⬜ 仍待你确认：`require_confirmation`

默认 `true`（第 8 节）。**这一项不阻塞开发**——先按 true 实现，
你随时可以改成 `false`。

---

## 11. 五条不许违反的

1. ❌ **不得实现自动登录**（全局红线 1）。会话过期的正解是通知你去登。
2. ❌ **不得凭猜测编写选择器**（全局红线 5）。G1 之前不写。
3. ❌ **不得与抓取小号共用 Chrome profile**（G0 存在的全部理由）。
4. ❌ **不得在失败时自动删除草稿**。半完成状态必须可见（G7）。
5. ❌ **`.bat` 不得含非 ASCII 字符，不得用 LF**（全局红线 7，已有测试守）。

---

## 12. 分支与文件所有权（K 组与 G 组**并行开发**的约定）

> 这一节在 `PUBLISH_PLAN.md` 与 `IMAGE_PLAN.md` 里**内容相同**，
> 两边都留一份是有意的：并行开发时没人会去读另一组的任务书。
> **改这一节要两边一起改。**

### 12.1 两条分支

| 组 | 分支名 | 从哪里开 | 何时合回 `main` |
|---|---|---|---|
| **K 组**（图片德语化） | **`feat/image-de`** | `docs/plan-image-publish` | **K9 真实验收通过**（懂德语的人确认过图） |
| **G 组**（Business Suite 发布） | **`feat/business-suite-publish`** | `docs/plan-image-publish` | **G8 验收通过** |

```bash
git checkout -b feat/business-suite-publish docs/plan-image-publish
```

> `docs/plan-image-publish` 是两份计划、`config.toml` 的 `[image]`/`[publish]` 段、
> `requirements.txt` 的 Pillow 所在的分支。**必须从它开**，
> 从 `main` 开会拿不到计划和配置。
> （若用户已把它快进合进 `main`，从 `main` 开等价。）

**合回 `main` 用 `--ff-only`，保持历史线性**——这是项目既定约定，
合不上说明 `main` 在你之外动过，**先查清楚再动手，不要 `-f`**。

### 12.2 文件所有权：**动别人的文件之前先看这张表**

| 文件 / 目录 | `feat/image-de` | `feat/business-suite-publish` |
|---|---|---|
| `localize_images.py` | ✅ 独占 | ⛔ |
| `prompts/image_de.md` | ✅ 独占 | ⛔ |
| `scripts/run_images.bat` | ✅ 独占 | ⛔ |
| `tests/tests_localize_images.py` | ✅ 独占 | ⛔ |
| `translate.py`（仅 `run_review` / `sync_text_de`，为 K8） | ✅ 独占 | ⛔ |
| `tests/tests_translate.py`（仅 K8 相关断言） | ✅ 独占 | ⛔ |
| `publish/**` | ⛔ | ✅ 独占 |
| `tools/probe_publish.py`、`tools/start_chrome_publish.py` | ⛔ | ✅ 独占 |
| `scripts/start_chrome_publish.bat`、`scripts/run_publish.bat` | ⛔ | ✅ 独占 |
| `tests/tests_publish.py` | ⛔ | ✅ 独占 |
| **`core/chrome.py`**（G0 的 port/profile 参数化） | ⛔ | ✅ 独占 |
| `core/config.py`（如需 `[publish]` 派生路径） | ⛔ | ✅ 独占 |
| `tests/tests_chrome.py`（G0 扩断言） | ⛔ | ✅ 独占 |
| `config.toml` 的 `[image]` / `[image.keep_verbatim]` 段 | ✅ | ⛔ |
| `config.toml` 的 `[publish]` 段 | ⛔ | ✅ |
| `requirements.txt` | ⛔ **Pillow 已预置，不用动** | ⛔ **同左** |
| `docs/IMAGE_PLAN.md` | ✅ | ⛔ |
| `docs/PUBLISH_PLAN.md` | ⛔ | ✅ |
| `docs/IMPLEMENTATION_PLAN.md` | ⚠️ **只改 K 组那一节**，附录 D 追加自己的一段 | ⚠️ **只改 G 组那一节**，附录 D 追加自己的一段 |
| `docs/MANUAL_STEPS.md` | ⚠️ 只改与自己相关的步骤 | ⚠️ 只改与自己相关的步骤 |
| `docs/HANDOFF.md` | ⚠️ **只改「现在卡在哪」里自己那一行** | ⚠️ 同左 |
| `core/store.py`、`core/parse.py`、`core/capture.py`、`routes/**` | ⛔ **都不许动** | ⛔ **都不许动** |

### 12.3 三条降低冲突的规矩

1. **只在自己那一节里编辑共享文档。** 不要重排其它节、不要"顺手"改格式、
   不要动行尾空白。`config.toml` 的 `[image]` 与 `[publish]` 相邻，
   `IMPLEMENTATION_PLAN.md` 的 K 组与 G 组也相邻——
   **各自只碰自己那一段，git 就能自动合**。
2. **附录 D 各自追加一节，标题里带上分支名。** 两段都要保留，
   合并冲突时**不许二选一**——那是项目的历史记录。
3. **先完成的先合。** 第二个合之前先
   `git fetch && git rebase main`（或 `docs/plan-image-publish`），
   **在自己分支上解冲突**，别把冲突带进 `main`。

### 12.4 收尾时两条分支都必须做的

- 跑**全部** `tests/tests_*.py`，不能只跑自己新增的那套（项目既定要求）。
- `git diff --check` 与 `python -m compileall -q .` 干净。
- `.bat` 保持**纯 ASCII + CRLF + 无 BOM**（`tests_schedule.py` 有字节级断言）。
- 新入口调 `core.console.force_utf8()`——本机代码页 936，
  输出一旦被重定向就会崩在 `ß`/`⚠` 上。
- 按项目工作协议更新 `IMPLEMENTATION_PLAN.md`：勾选完成项、
  在下面追加 `> 完成：<日期> · <实际做法与偏差>`。
  **未通过【验收】的不得勾选。**

### 12.5 G 组的一条额外提醒

**`publish/selectors.py` 在 G1 完成之前必须是空的（或只有带 TODO 的占位）。**
提交一个"看起来合理"的选择器比不提交更糟——
下一个人会以为它被验证过。这是全局红线 5 的直接推论。
