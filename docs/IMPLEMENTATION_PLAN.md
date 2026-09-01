# 实施计划 · Facebook / Instagram 内容归档与本地化流水线

> **给实施 Agent 的说明**
>
> 1. 本文件是唯一的进度真相源。每完成一项，把该项的 `- [ ]` 改成 `- [x]`，
>    并在该项下方追加一行 `> 完成：<日期> · <实际做法与偏差>`。
> 2. **偏差必须记录。** 如果实现与本计划不同（尤其是选择器、字段名、端点行为），
>    把真实情况写进"完成"行，不要默默改代码。后续任务依赖这些事实。
> 3. 遇到本计划与现实冲突时，**以现实为准并记录**，不要强行让现实符合计划。
> 4. 每个任务都有【验收】，未通过验收不得勾选完成。
> 5. 本项目最后的执行环境是 **Windows**，不需要兼容 macOS。

---

## 0. 项目背景（已固化的决策，不需要再讨论）

**目标**：把 US 站 Facebook / Instagram 账号已发布的图文帖（正文 + 配图）抓取归档，
翻译为德语，最终发布到 DE 站账号。

**已确定的约束**（这些是讨论结论，不是可选项）：

| 维度     | 结论                                             | 原因                                             |
| -------- | ------------------------------------------------ | ------------------------------------------------ |
| 获取方式 | 外部爬取                                         | US 账号无管理员权限，官方 API 路径不可用         |
| 抓取身份 | 专用小号                                         | 被封损失可控；不用主账号                         |
| 内容范围 | **仅图文帖**。视频只记元数据不下载         | 视频体积大、耗时长、下游本就要重新编码           |
| 架构     | **拆两条路径**                             | 见下                                             |
| 回填     | 登录态 + CDP 附着真实 Chrome +**人工滚动** | 滚动的是人，无自动化行为特征                     |
| 增量     | ~~完全登出~~ → **登录态 + CDP 附着，每天一次**（方案 B） | 登出端点已关闭，见下 |
| 运行环境 | Windows 笔记本，有管理员权限，住宅 IP            | 数据中心 IP 首次请求即被拦                       |
| 发布     | Business Suite UI 自动化                         | 用户当前拿不到 API Token；API 通道保留为只读验证 |

### ⚠️ 增量方案变更：登出 → 登录态（用户 2026-08-30 拍板，方案 B）

**原设计**：增量完全登出，没有账号、没有 session、没有 cookie，
**因此没有可封的东西**，最坏情况只是 IP 被临时限流。

**为什么变**：2026-08-30 实测 + 用户确认，**Facebook / Instagram 在没有登录态时
直接报错**（对公开账号请求 `web_profile_info` 首次即 429）。登出这条腿不存在了。

**用户在三个方案里选了 B：增量改走登录态**，复用回填那条 CDP 通道。
（另两个是 A「降级为定期人工触发回填」和 C「混合」。我当时建议 A，
用户选了 B，这是用户的决定，**不要再重新讨论**。）

**必须清醒记住这个方案换来了什么**：

> 封号风险从「**一次性敞口**」变成「**累积性敞口**」。
> 单次会不会被封、和 365 次里会不会被封一次，是两个量级的问题。
> **这是本项目现在最大的、且不可恢复的失败模式**——抓取小号一旦被封，
> 回填与增量两条路同时断掉。

**因此 C7「累积风险缓解」不是可选项，是这个方案的组成部分。**
它规定的每一条（随机化时刻、抓取深度上限、异常即停、频率可降级）
都是在为"每天都要来一次"这件事买保险。**不要因为"跑得挺好"就把它们优化掉。**

**没有改变的两件事**（别顺手一起改了）：

1. ❌ **仍然不得实现自动登录。** 方案 B 说的是"复用人工登录留下的会话"，
   不是"让程序去登录"。全项目仍然只有一条登录路径：
   人工在 `scripts\start_chrome.bat` 的专用 Chrome 里登录一次。
2. ❌ **回填仍然是人工滚动。** 增量走登录态不等于回填可以自动滚。

**本期不做**（明确排除，不要自作主张实现）：

- ~~图内英文文字的德语替换（一期走人工处理）~~
  ⛔ **2026-08-31 用户拍板改为程序自动完成**，用第三方网关上的 `gpt-image-2`。
  这一条已被 **K 组**取代，任务书见 `docs/IMAGE_PLAN.md`。
  `posts/<帖子>/media_de/` 的语义随之从"设计同事回填"扩展为
  "**程序产出，人工可覆盖**"——人工放进去的文件优先，程序不得覆盖。
- 视频文件下载与处理
- 完整性的"帖子总数交叉校验"（大概率抓不到总数，降级为只做时间序列连续性）

### ⚠️ 发布范围的定案（用户 2026-08-31 拍板，不要重新讨论）

| 项 | 决定 |
|---|---|
| 发布目标 | **Facebook DE Page + Instagram DE，同时发** |
| 历史存量 | **不补发。** 归档里的历史帖只翻译归档，不进发布 |
| 本期目标 | 用**最新几篇**做端到端验证，把功能跑完整 |
| 上线后 | 跟着每日增量走：US 发新帖 → 翻译 → 调图 → 排期发 DE |

**这个决定把 G 组从"发布队列子系统"缩回成"发一篇 + 一个跟增量的挂钩"。**
不要自作主张把批量补发、优先级队列做回来。

⚠️ **顺带记一个容易被误用的数**：`1051 条待译正文` 是**翻译**口径。
**发布**口径不同——项目约定"视频只记元数据不下载"，纯视频帖没有可上传素材。
真实归档实测（2026-08-31）：**图文可发只有 470 篇**
（Facebook 27 / Instagram 443），另有 585 篇纯视频帖发不了。
本期不补发所以不影响工期，但哪天有人决定补发，别按 1051 去估。

---

## 1. 关键事实速查（实施时会用到，不要重新推导）

### Instagram

> ⛔ **2026-08-30 更新：下表"登出可用端点"一行已不成立。**
> 实测对公开账号 `nasa` 请求 `web_profile_info`，**首次请求即 429**；
> 用户确认 FB/IG 在没有登录态时直接报错。该端点对登出访客已关闭。
> 端点与 Header 的记录保留（代码已实现且正确，端点若重开可直接复用），
> 但**不要再把"登出可用"当作可依赖的前提**——C 组整组因此进入架构岔路口，
> 见 C 组开头的 ⛔ 段落。

| 项           | 值                                                                                                          |
| ------------ | ----------------------------------------------------------------------------------------------------------- |
| ~~登出可用端点~~ **登出已不可用** | `GET https://www.instagram.com/api/v1/users/web_profile_info/?username=<name>`（登出访问返回 429） |
| 必需 Header  | `X-IG-App-ID: 936619743392459`                                                                            |
| 辅助 Header  | `X-ASBD-ID: 198387`、`X-Requested-With: XMLHttpRequest`、`Referer: https://www.instagram.com/<name>/` |
| User-Agent   | Safari 17 UA（见`core/session.py: SAFARI_UA`）。该端点对 UA 敏感                                          |
| 返回结构     | `data.user.edge_owner_to_timeline_media.edges[].node`（GraphQL 形态）                                     |
| 登录态端点   | `GET /api/v1/feed/user/<user_id>/?count=12&max_id=<next_max_id>`（iphone_struct 形态）                    |

**已知陷阱 — `doc_id` 轮换**：Instagram 的 web GraphQL 端点要求硬编码 `doc_id`，
该值约每 2–4 周轮换一次。轮换后旧值返回 **401**，错误文案是
`"Please wait a few minutes before you try again"` —— **这个提示是误导的**，
它不是限流，等多久都不会恢复。
**因此：本项目任何代码都不得硬编码 `doc_id`。** 登出走 `web_profile_info`，
登录态走浏览器拦截（浏览器自己会带当周有效的 `doc_id`）。

**已知限制**：`web_profile_info` 的 timeline media **不含轮播子项**，只有封面
（`display_url`）。轮播帖必须标 `media_complete=False`。

### Facebook

| 项           | 值                                                                              |
| ------------ | ------------------------------------------------------------------------------- |
| 登出可见范围 | ~~Page 落地视图：头像、封面、最近 2–3 条帖子~~ **2026-08-30 用户确认：没有登录态直接报错** |
| 登录态获取   | CDP 附着真实 Chrome + 拦截`/api/graphql` 响应                                 |
| 不可用       | `mbasic.facebook.com`（官宣 2024-12-03 下线，行为不稳定，**不得依赖**） |

### 通用

- **媒体 CDN URL 带签名且有时效**，必须在拿到响应后**立刻下载**，不能存 URL 事后再取。
- **数据中心 IP 首次请求即被拦**。不得部署到 VPS / 云函数 / GitHub Actions。

### API 只读验证通道（保留，非当前主路径）

| 项                            | 值                                                                                   |
| ----------------------------- | ------------------------------------------------------------------------------------ |
| 定时窗口`/{page-id}/feed`   | 10 分钟 –**75 天**                                                            |
| 定时窗口`/{page-id}/videos` | 10 分钟 –**6 个月**                                                           |
| 未发布图片留存                | **约 24 小时**（超时 photo ID 失效）                                           |
| 发布后校验字段                | `GET /{post-id}?fields=is_published,is_hidden,scheduled_publish_time,created_time` |
| 已知故障                      | Graph API 定时发布约**1%** 静默失败，Meta 已关闭相关 bug 报告不修              |

---

## 2. 全局约定

**目录结构**（现状 + 待建，`*` 为待建）

```
FacebookScraper/
  README.md                怎么跑 + 架构为什么长这样
  config.toml              全部参数集中，代码零硬编码
  requirements.txt
  .gitattributes           锁 *.bat 为 CRLF（LF 会让 cmd 误解析整行）
  .env                     API 密钥（gitignore；密钥不得进 config.toml）
  translate.py             德语翻译 + 审核清单（F1/F2/F3 三合一，子命令区分）
  scripts/                 双击入口。**全部纯 ASCII + CRLF**，逻辑在 tools/
    scripts\setup.bat              → tools/setup.py
    scripts\start_chrome.bat       → tools/start_chrome.py
    scripts\run_backfill.bat       → routes.backfill
    scripts\run_translate.bat      → translate.py
    scripts\run_delta.bat          增量调度入口（纯 ASCII，输出追加进 state\delta.log）
  tools/                   .bat 的真正实现。**中文只能待在这里，不能进 .bat**
    setup.py               一次性环境搭建（venv + 依赖 + 自检 + 离线测试）
    start_chrome.py        起专用 Chrome + 端口就绪轮询（参数全读 config.toml）
    replay.py              用 _capture_*.json 离线重建归档（B7），不重新下载媒体
    layout.py              归档布局：migrate / reindex / index（J 组）
    schedule.py            计划任务：xml / install / status / remove（E3）
  docs/
    IMPLEMENTATION_PLAN.md 本文件（进度真相源）
    MANUAL_STEPS.md        **人工操作指南**，需要用户亲自做的步骤全在这里
    HANDOFF.md             会话交接（抓取侧任务书）
    CODE_REVIEW.md         审查记录 CR-01~CR-40
    TRANSLATION_PLAN.md    F 组任务书（DeepSeek 德语翻译）
    IMAGE_PLAN.md          K 组任务书（GPT-Image-2 图内英文德语化）
    PUBLISH_PLAN.md        G 组任务书（Business Suite 定时发布）
    PIPELINE_PLAN.md       **L 组：唯一一份跨组文档**。把上面这些段
                           连成一条不用人管的线；自治分级、分流规则、
                           死人开关、成本闸、人工分钟数的量化目标
  prompts/
    translate_de.md        英译德提示词本体。可直接编辑，改它不用动 Python。
                           `{{占位符}}` 由 config.toml 注入；改完 --show-prompt 看效果
    image_de.md *          图内英文德语化的提示词本体（K 组）。同上，可直接编辑
  core/
    config.py              配置读取 + Chrome 路径探测
    chrome.py              CDP 附着 + 拉起专用 Chrome（launch，增量与 start_chrome 共用）
    capture.py             响应捕获 + 媒体下载 + 转储裁剪（回填与增量共用的那一半）
    store.py               归档层
    parse.py               三形态解析 + walk() 全树搜索
    session.py             仅 Pacer 限速 + SAFARI_UA 常量（已剥离全部登录逻辑）
    http.py                登出 HTTP 客户端（拒收一切 cookie）
    notify.py              Windows 通知（toast → msg → alerts.log 三级降级）
    integrity.py           连续性 / 长期零新增 / 媒体不全 三项检查
    console.py             stdout/stderr 强制 UTF-8（本机代码页 936，重定向即崩）
  routes/
    backfill.py            登录态回填（CDP + 人工滚动 + 响应拦截）
    delta.py               每日增量。上半部分＝登出实现（保留备用，端点已关）；
                           下半部分＝登录态实现（方案 B，C2–C7）
    fb_graph.py            API 只读（保留，未接入，缺 Token）
  localize_images.py *     图内英文德语化（K 组，GPT-Image-2）。与 translate.py 平级
  publish/ *
    compose.py *           把「译文 + 德语图 + 排期时刻」组装成一篇 DePost，
                           并在碰浏览器之前跑完全部离线硬闸
    business_suite.py *    UI 自动化发布
    selectors.py *         选择器集中定义（**G1 探查之后才填**，之前不写）
  translate.py             德语翻译 + 审核清单（F1/F2/F3 三合一，子命令区分）
  tests/                   离线测试。scripts\setup.bat 用 glob 全跑，新增即自动纳入基线
    tests_backfill.py        回填收尾、媒体补全、归属拦截、滚动进度（40）
    tests_chrome.py          CDP 真实性与端口占用（7）
    tests_delta.py           登出增量：解析、登录墙三形态、429 退避、归属过滤（55）
    tests_delta_logged_in.py 登录态增量：异常即停、深度上限、状态记录、
                             失败预算、抖动顺序、D3 接入、合作帖（132）
    tests_schedule.py        计划任务 XML + .bat 字节级约定（48）
    tests_fb_graph.py        只读 Graph 路线的视频边界与媒体失败（12）
    tests_parse.py           解析层 + **真实响应结构断言**（61）
    tests_replay.py          replay 输入闭环、重复真相源与可恢复隔离（28）
    tests_store.py           归档层 + 每帖文件夹布局与 reindex（75）
    tests_store_boundaries.py 账号根、原子 post.json 与 schema 边界（8）
    tests_store_links.py     symlink/junction/reparse/hardlink 边界（23）
    tests_http.py            登出客户端（17）
    tests_integrity.py       完整性检查（54）
    tests_notify.py          通知降级（16）
    tests_translate.py       翻译管道 + 提示词渲染 + 付费结果完整性（212，零 API 调用）
                             ——合计 15 套 788 项，**在 stdout 被重定向的条件下也全绿**
  _deprecated/             已否决路线的存档，**不要引用、不要复活**
    dyi_import.py            官方数据导出 —— 需 US 账号登录，拿不到
    intercept.py             早期拦截器 —— 已被 backfill.py + parse.py 取代
    ig_v1_feed.py            登录态 v1 feed —— 与"增量必须登出"冲突
  archive/<平台前缀>_<账号>/   产物（已 gitignore）。**2026-08-30 起为每帖一个文件夹**
    index.html             全账号总览（派生，可重建）
    manifest.jsonl         **派生索引**，可用 tools.layout reindex 从 posts/ 重建
    _rejected.jsonl        被丢弃的节点及原因（不得静默丢弃）
    _orphan_media/         B7 重建后无主的媒体文件（移动不是删除）
    posts/
      2026-08-25_1423_<post_id>/
        post.json          **真相源**
        text.txt           正文纯文本（派生）
        text_de.txt        德语译文副本（派生；真相源仍是 translated.jsonl）
        01.jpg 02.jpg      原图，编号跟的是帖内位置
        media_de/          设计同事回填的德文版图
      undated_<post_id>/   时间解析不出来的进这里，**不猜**
    _capture_*.json        原始响应转储（B7 离线重放的输入，别删）
    ⚠️ 旧布局的 media/ 与 raw/ 已废弃；raw/ 存的其实是 post.to_row() 的副本，
       不是原始响应，post.json 落地后成了纯重复
  state/                   运行状态（已 gitignore）
```

**`_deprecated/` 的存在意义是留档，不是留后路。** 里面三个模块都与已定架构冲突：
`dyi_import.py` 和 `ig_v1_feed.py` 需要目标账号凭据（拿不到），
`intercept.py` 的职能已被取代。它们的有效信息（端点、Header 常量）
已完整收录进本文件第 1 节，不需要回去翻代码。

### Windows 命令约定

所有 `python -m ...` 命令都需要：**先 `cd` 到 `FacebookScraper\` 目录**，
并且**已激活 venv**（`.venv\Scripts\activate.bat`）。
`.bat` 入口脚本已内置这两步，直接双击即可；手工敲命令时别忘了。

**编码约定**

- Python 3.11+（用到 `tomllib`）。路径一律 `pathlib`，不拼字符串。
- 所有可调参数进 `config.toml`，**代码里不写死数字**。
- 中文注释，解释「为什么」而非「是什么」。
- 不引入新依赖，除非任务明确要求；引入时更新 `requirements.txt`。

**错误处理约定**

- 网络请求失败**不得静默吞掉**，至少打印一行含 post_id 的错误。
- 解析异常按单条跳过，不中断整批。
- 任何"抓到 0 条"的情况必须显式告警，不能当成正常结束。

**禁止事项**（违反会导致封号或返工）

- ❌ 不得实现自动登录。会话由人工登录一次产生并持久化。
  ⚠️ 方案 B 让**增量也带上了登录态**，但那是**复用**人工登录留下的会话，
  不是让程序去登录。**这条禁令没有松动**——会话过期时的正解是通知用户去登，
  不是替他登。
- ❌ 不得硬编码 Instagram `doc_id`。
- ❌ 不得提高并发（恒为 1）或移除随机间隔。
- ❌ 不得依赖 `mbasic.facebook.com`。
- ❌ **不得凭猜测编写 Business Suite 的选择器**。必须先探查真实 DOM（见 G1）。
- ❌ 不得把 `state/`、`archive/`、Chrome profile 目录提交进版本库。
- ❌ **不得在 `.bat` 里写任何非 ASCII 字符**（A1 实测结论）。cmd.exe 解析含中文的
  批处理不可靠：行会被从中间劈开、后半段当命令执行，加不加 `chcp 65001` 都会犯，
  加 BOM 更糟（cmd 不认 BOM，会把 `@echo off` 一起打坏）。
  `.bat` 只留纯 ASCII 的壳，中文提示一律放进 `tools/*.py` 或 `routes/*.py`。
- ❌ **`.bat` 不得用 LF 换行**，必须 CRLF（已由 `.gitattributes` 锁定）。

---

## 3. 现状盘点（已完成，需校验而非重建）

以下模块已实现并通过离线测试，**实施 Agent 的任务是校验与校准，不是重写**：

| 模块                   | 状态 | 已验证                                    | 未验证                       |
| ---------------------- | ---- | ----------------------------------------- | ---------------------------- |
| `core/config.py`     | 完成 | 配置解析、4 个 Chrome 候选路径            | Windows 上的真实探测         |
| `core/chrome.py`     | 完成 | 语法                                      | **CDP 真实连接**       |
| `core/store.py`      | 完成 | 17 项检查：幂等、续传、补全升级、拒绝降级 | —                           |
| `core/parse.py`      | **有已证实的缺陷** | 19 项离线检查通过 | ~~与真实响应的匹配度~~ **2026-08-30 已验证，结果是不匹配**：跨账号污染、轮播子项被当成帖子、FB 视频帖被当成抓取失败。见 B2/B4 与 CODE_REVIEW 的 CR-12/13/14 |
| `core/session.py`    | 完成 | 已剥离全部登录逻辑，仅剩 `Pacer` + `SAFARI_UA` | —                 |
| `routes/backfill.py` | 完成 | 语法                                      | **全部网络行为**       |
| `scripts\start_chrome.bat` + `tools/start_chrome.py` | 完成 | Windows 实机：config 解析、Chrome 探测、端口探测 | **实际拉起 Chrome（A3）** |
| `scripts\setup.bat` + `tools/setup.py` | 完成 | **Windows 实机全程跑通（A1）** | —              |
| `scripts\run_backfill.bat`   | 完成 | Windows 实机：壳可执行                    | **转调后的网络行为（B1）** |
| `config.toml`        | 完成 | 可解析；Windows 上 Chrome/profile/port 三项均正确解析 | —      |

~~**已知的最大未知数**：`core/parse.py` 的解析器尚未与真实响应比对过。~~

**2026-08-30 更新：这个未知数已经关闭，答案是坏的。** B1/B3 跑完后逐条核对，
解析器在真实响应上以三种方式失效（跨账号污染 266+1 条、轮播子项被当成帖子 478 条、
FB 视频帖被当成抓取失败 20 条）。具体修法写在 B2/B4，根因分析在
`docs/CODE_REVIEW.md` 第 7 节。

**但那个"无条件转储"的设计救了这次**：两份 capture 完整保留，
修完解析器走 B7 离线重放即可重建，**用户不需要重新滚动**。
这是整个项目里那条兜底设计第一次真正兑现价值。

---

## A. 环境搭建

- [x] **A1** 一次性环境搭建

  - 在 `FacebookScraper\` 目录下双击 **`scripts\setup.bat`**。它依次做：校验 Python ≥ 3.11
    （`tomllib` 要求）→ 建 `.venv` → 装依赖 → `playwright install chromium`
    → 依赖导入自检 → Chrome 路径探测 → 跑两套离线测试
  - 若 Python 版本不足，脚本会明确告知并退出；装新版后重跑
  - 若 Chrome 未被自动探测到，把完整路径填入 `config.toml` 的 `[chrome].exe` 后重跑
  - 【验收】scripts\setup.bat 全程无 `[!]` 开头的报错，末尾打印"完成"
  - 【记录】写下脚本打印的 Chrome 实际路径

  > 完成：2026-08-29 · 验收通过（0 个 `[!]`，末尾打印"完成"），但为达成验收
  > 改动了三个 `.bat` 的实现方式。**Chrome 实际路径：
  > `C:\Program Files\Google\Chrome\Application\chrome.exe`**（`CHROME_CANDIDATES`
  > 第 1 个候选命中，无需手工填 `[chrome].exe`）。
  > 实测环境：Python 3.12.9（`D:\Python\Python312`，另装有 3.11）、uv 0.11.22、
  > playwright 1.62.0 + httpx 0.28.1。
  >
  > 四处偏差，均为「在 macOS 上写的、从未在 Windows 跑过」的直接后果：
  >
  > 1. **`.bat` 是 LF 换行** → cmd 误解析整行（`'xxx' 不是内部或外部命令`）。
  >    已全部转为 CRLF，并新增 `.gitattributes` 用 `*.bat text eol=crlf` 锁住，
  >    防止将来被 git 归一化改回去。
  > 2. **`.bat` 里的中文在 cmd 下不可靠**。本机系统 ACP/OEMCP = **936(GBK)**，
  >    而文件是 UTF-8：双击时全部中文提示变乱码。加 `chcp 65001` 能修好乱码，
  >    但会触发 cmd 的另一个缺陷——**中文 REM 注释行被从中间劈开，后半段当命令
  >    执行**（实测 6 处，例：`'录下双击运行。' is not recognized`）；去掉 `chcp`
  >    在 65001 控制台下同样复现。加 UTF-8 BOM 更糟，cmd 不认 BOM，直接把
  >    `@echo off` 打坏。**结论：cmd 无法可靠解析含中文的 .bat。**
  >    改法（已与用户确认）：三个 `.bat` 降级为**纯 ASCII 壳**，只负责定位
  >    解释器并转调 Python；全部中文提示与逻辑搬进 `tools/setup.py` 与
  >    `tools/start_chrome.py`——Python 在 Windows 控制台走 Unicode API
  >    (WriteConsoleW)，任何码页下都正确显示。
  > 3. **默认 PyPI 源在本网络不可用**：`files.pythonhosted.org` TCP 握手 0.4s
  >    但实际吞吐近 0，playwright（36 MB）跑 **8 分钟零进展**；换清华镜像后
  >    **45 秒**装完。已把镜像设为 `tools/setup.py` 的默认值，可用环境变量
  >    `PYPI_INDEX_URL` 覆盖（置空即走官方源）。
  > 4. **原 `scripts\setup.bat` 不检查测试退出码**，测试挂了照样打印"完成"，
  >    会让 A2 的"基线全绿"形同虚设。现在任一套不过即以非 0 退出。
  >
  > 顺带消除的重复：`scripts\start_chrome.bat` 原本自己写了一份 Chrome 路径探测和
  > `PORT=9222`，与 `config.toml` / `core/config.py` 是两个真相源（计划附录 C
  > 专门警告过要手工同步）。`tools/start_chrome.py` 改为一律从 `config.toml` 读，
  > 已无第二处可改。
  >
  > 用 uv 建环境是用户指定的（`uv venv --python ">=3.11"`，自动选中 3.12.9）；
  > `tools/setup.py` 保留了 uv 缺失时回退到 `python -m venv` + pip 的路径。
  > 另设 `UV_LINK_MODE=copy`：uv 缓存在 C 盘、项目在 D 盘，跨盘硬链接必失败刷告警。
- [x] **A2** 确认离线测试基线为全绿

  - scripts\setup.bat 末尾已跑过一次，确认两套都通过
  - 若有失败，**先修到全绿再往下走**。这两套测试是 B 组校准的基线，
    基线本身是坏的，就分不清"我改坏了"和"本来就坏"
  - 【验收】`python tests_parse.py` 输出「全部通过」（17 项断言）；
    `python tests_store.py` 输出「全部通过」（13 项断言）

  > 完成：2026-08-29 · 两套在 Windows 实机全绿，无需修改任何测试或被测代码。
  > `tests_parse.py` 17 项、`tests_store.py` 13 项，共 30 项断言全部 OK。
  > B 组校准的基线成立——后续解析器一旦改出问题，能与"本来就坏"区分开。
- [x] **A3** 起专用 Chrome 并确认调试端口

  > 完成：2026-08-30 · 由 B1/B3 的成功执行反证——回填必须先经 CDP 附着到
  > 该端口上的专用 Chrome 才能挂响应监听，两个平台都跑通并产出了 capture，
  > 说明端口就绪与 CDP 附着均成立。

  - 双击 `scripts\start_chrome.bat`。脚本会自行轮询等待端口就绪并打印 `[ok]`
  - 若 Chrome 路径探测失败，按 A1 记下的路径手工编辑 bat 顶部的 `CHROME` 变量
  - ⚠️ **已知坑**：若该 `user-data-dir` 已被另一个 Chrome 实例占用，
    Chrome 会静默复用已有实例并**忽略 `--remote-debugging-port` 参数**，
    表现为"脚本明明跑了但端口没开"。脚本已内置检测与提示；
    遇到时先在任务管理器确认没有残留的 `chrome.exe` 进程
  - 【验收】脚本打印 `[ok] 调试端口 9222 已就绪`；且
    `python -c "import sys; sys.path.insert(0,'.'); from core.chrome import port_open; print(port_open(9222))"`
    输出 `True`
- [x] **A4** 小号登录并确认会话持久化

  > 完成：2026-08-30 · 同样由 B1/B3 反证：登出状态下 FB 只给落地视图的 2–3 条、
  > IG 拿不到 6 年时间线。实际抓到 FB 47 条、IG 1500 条（跨 2020–2026），
  > 只有登录态才可能。会话持久化亦成立——两次回填分属不同进程、
  > 中间重启过脚本，均未要求重新登录。

  - 在 A3 打开的窗口里登录抓取用的小号（**手工登录，含二次验证**）
  - 关闭该窗口，重新双击 `scripts\start_chrome.bat`
  - 【验收】重新打开后仍是登录状态（说明 profile 目录生效）
  - ⚠️ 该 profile 目录含已登录会话，确认已在 `.gitignore` 中
- [x] **A5** 填写抓取目标

  - 编辑 `config.toml` 的 `[targets]`，填入真实的 FB Page 用户名和 IG handle
  - 【验收】`https://www.facebook.com/<填的值>` 和 `https://www.instagram.com/<填的值>` 在浏览器里能打开正确页面

  > 完成：2026-08-29 · 用户提供了两条可打开的主页地址，取其中的账号名段填入：
  > `facebook = "neakasaofficial"`、`instagram = "neakasa.tech"`。
  > 验收成立的依据是用户给的就是活的 URL；另在代码侧核过一遍拼接结果与之逐字符相同：
  >
  > | 用途 | 值 |
  > |---|---|
  > | 回填打开（FB） | `https://www.facebook.com/neakasaofficial/` |
  > | 回填打开（IG） | `https://www.instagram.com/neakasa.tech/` |
  > | C2 端点 | `.../web_profile_info/?username=neakasa.tech` |
  > | 归档目录 | `archive/fa_neakasaofficial/`、`archive/in_neakasa.tech/` |
  >
  > **两个都是自定义用户名，没踩 `profile.php?id=` 那个坑**（MANUAL_STEPS 第 3 步
  > 预警过的那个），URL 构造逻辑不需要改。
  > IG handle 带一个点（`neakasa.tech`），归档目录名因此也带点——Windows 下合法，
  > 但后续若有按扩展名判断的逻辑要注意别把 `.tech` 当后缀。
  >
  > **品牌背景对下游有影响**：Neakasa 是宠物用品消费品牌。这印证了
  > `[translate].address_form = "du"` 那个决策（消费品牌在德语社媒上的主流选择）；
  > 另外产品型号名与品类词（具体有哪些**等 B 组抓到文案后再看**，现在不要猜）
  > 要进 `[translate.glossary]`，否则同一个词会在不同篇里被翻成不同写法。

---

## B. 回填校准（依赖 A）

> 本组的目的不是"写代码"，而是**用真实数据校准已有的解析器**。
> 顺序很重要：先跑一次拿到真实响应，再改解析器，不要反过来。

- [x] **B1** 首次回填运行 — Facebook

  - 确保 A3 的 Chrome 窗口开着且小号已登录
  - 双击 `scripts\run_backfill.bat facebook`（或激活 venv 后 `python -m routes.backfill facebook`）
  - 按提示在浏览器里**手工向下滚动**，直到看见最早的帖子。慢滚，等图片加载出来再继续
  - 滚完回终端按 Enter
  - 【验收】`archive/fa_<账号>/_capture_<时间戳>.json` 存在且大于 100 KB
  - 【记录】记下：捕获了多少段 JSON、解析出多少篇帖子
  - ⚠️ 若解析出 0 篇，**不要重滚**，直接进 B2

  > 完成：2026-08-30 · 用户实机跑通。**转储成功，且解析器没有解析出 0 篇**——
  > 这一点比预期好，说明 `is_fb_story` 的判定方向是对的。
  >
  > | 项 | 值 |
  > |---|---|
  > | capture | `archive/fa_neakasaofficial/_capture_1788072462.json`（7.7 MB，**220 段 JSON**） |
  > | manifest | 47 行，post_id 全部唯一 |
  > | 图片已下载 | 74 张，无 0 字节 |
  > | 时间范围 | `2026-06-29T06:18:21Z` → `2026-08-25T14:23:20Z` |
  >
  > ⚠️ **但产出的 47 篇是不可信的**，逐条核对后发现三类问题，见 B2 与
  > `docs/CODE_REVIEW.md` 的 CR-12/CR-14/CR-15。真实值是 **46 篇本账号帖
  > （其中 20 篇是视频帖）**。
  >
  > ⚠️ **时间范围只有约 2 个月**，而 Instagram 是 6 年。差距太大，
  > **无法排除"没滚到底"**——当时终端只显示"已捕获 N 个响应"，
  > 操作者无从判断进度（这正是 CR-15）。B8 修好进度显示后需要用户确认，
  > 必要时补滚一次。**在确认之前不要把 46 篇当作 FB 的全部历史。**
- [x] **B2** 校准 Facebook 解析器

  - 用 `_capture_*.json` 分析真实响应结构：
    ```python
    import json
    d = json.load(open("archive/fa_XXX/_capture_XXXX.json", encoding="utf-8"))
    # 找出帖子节点的真实字段名
    ```
  - 对照 `core/parse.py` 的 `is_fb_story()` 和 `from_fb_story()`，修正：
    - 帖子节点的判定条件（当前假设：含 `post_id` 且含 `message` 或 `attachments`）
    - 正文字段路径（当前假设：`message.text` 或 `message`）
    - 图片字段路径（当前假设：attachments 树里含 `uri`/`width`/`height` 的节点）
    - 时间字段（当前假设：`creation_time` 或 `created_time`，Unix 秒）
    - permalink 字段（当前假设：`url` 或 `permalink_url`）
  - 在 `tests_parse.py` 里**新增一组用真实结构构造的断言**（脱敏后）
  - 【验收】`python tests_parse.py` 全通过；且用 `_capture_*.json` 重放能解析出与人工计数一致的帖子数
  - 【记录】写下真实的字段路径，替换本计划"当前假设"的描述

  > **2026-08-30 · 真实数据已核对完毕，下面是要修的具体内容（不是待调查项）。**
  > 完整根因分析见 `docs/CODE_REVIEW.md` 的 CR-12 / CR-14。
  >
  > **已证实为正确的假设**（不要动）：节点判定方向、`message` 正文路径、
  > `uri`/`width`/`height` 的图片抽取、`creation_time` 时间戳、permalink 字段。
  > 47 条里 46 条正文、时间、图片都抽对了。
  >
  > **必须修的两项：**
  >
  > **① 跨账号污染（CR-12，P0）** —— `from_fb_story` 把调用方传入的 `account`
  > 直接写进 `Post.account`，从不看节点自身归属。实测 47 条里有 1 条 owner 是
  > `The Garden State Cat Club`（`post_id=1514468247386817`，长度 16 而非本账号的
  > 18，且无 `created_at`）。FB 这边只漏了 1 条，但 IG 那边同一个根因漏了 266 条。
  >
  > 归属字段在 `node.actors[0].name`（本账号是 `"Neakasa Official"`）。
  > ⚠️ **它是显示名，不是 URL 里的 `neakasaofficial`，两者不相等**，
  > 不能拿 `config.toml` 里的账号名直接字符串比对。做法：回填时从响应里确定
  > 本账号的 page id 与显示名，记录下来再比对。
  >
  > **② 视频帖被记成"图片抓取失败"（CR-14，P1）** —— 47 条里 **20 条**
  > `media == []` 且 `media_complete=False`。核对 capture 确认它们是**视频帖**
  > （attachments 里主导 `__typename` 是 `Video` / `VideoAttachmentStyleInfo` /
  > `FbShortsVideoAttachmentStyleInfo`；有图帖子那边主导的是 `Photo`）。
  > `from_fb_story` 完全不识别视频，于是把视频帖当成抓取失败。
  >
  > 后果：这 20 条会永久停在 `Archive.needs_media()` 的待补清单里，
  > 每次回填都被 `should_append()` 判为可升级而重复尝试，且完整性检查会
  > 把它们当媒体缺失。修法参考 `from_iphone_struct` 对 `video_versions` 的既有处理：
  > 记 `Media(kind="video")` 但**不下载**，`media_complete` 改判为
  > "已知媒体都已处理"而不是"有图片"。
  >
  > 【新增验收】修完用 B7 的离线重放跑 capture，应得到 **46 篇本账号帖、
  > 其中 20 篇 `kind=="video"`、0 篇 owner 非本账号**；
  > `_rejected.jsonl` 里应有那 1 条 Garden State Cat Club 的记录。

  > 完成：2026-08-30 · 两项都修了，用真实 capture 验证。
  >
  > **归属**：`_fb_actor()` 从 `actors[0]` 取，`_fb_slug()` 把
  > `url` 归一化成账号名段（`https://www.facebook.com/neakasaofficial`
  > → `neakasaofficial`），与 `config.toml` 的值直接可比。展示名另存
  > `owner_name`，只给人看、不参与判等。没有自定义用户名的主页
  > （`profile.php?id=NNN`）退回 `id:NNN`。
  >
  > **视频**：新增 `_fb_videos()`，从 `attachments[i].media` 取
  > `__typename == "Video"` 的节点，并递归 `all_subattachments.nodes`。
  > ⚠️ **没有用 walk() 全树搜索**：实测有 1 篇图文帖的 attachments 深处
  > 埋着 Video 节点（推荐位之类），walk 会把它算成这篇帖子的视频。
  >
  > ⚠️ **我们捕获的这版响应里 `media` 只给 `{__typename, id}`，没有
  > `progressive_url`**（它挂在更深的、没有 `__typename` 的渲染器节点上，
  > 路径不稳定）。所以视频 URL 是**用 id 拼出来的 watch 链接**，不是响应给的。
  > 视频从不下载，拼错的成本仅限于这条链接点不开。
  >
  > **`media_complete` 的语义改了**：从 `bool(media)` 改为解析层恒为 True。
  > 旧写法把**纯文字帖**和**视频帖**都判成不完整，而它们本来就没有图可下。
  > 真正的 False 由下载环节在失败时写入（CR-04 已确立的职责划分）。
  >
  > **实测结果与预期的三处出入，都已查清不是问题**：
  >
  > | 预期 | 实际 | 原因 |
  > |---|---|---|
  > | 20 篇视频帖 | **18** 篇含视频 | 旧的 20 篇"0 图"里，17 篇是纯视频帖，另 3 篇确实没有可下载媒体；另有 1 篇原本有图的相册帖也含视频 |
  > | 图片 74 张 | **70** 张 | 差的 4 张全属于被丢弃的 Garden State Cat Club 那条 |
  > | 无媒体 0 篇 | **3** 篇 | 2 篇是纯文字的抽奖公告；1 篇是**头像/封面更新**（`StoryAttachmentProfileMediaStyleRenderer`，Photo 节点只有 id 没有 uri，响应里本来就没有图片 URL）|
- [x] **B3** 首次回填运行 — Instagram

  - 同 B1，命令为 `scripts\run_backfill.bat instagram`
  - 【验收】同 B1
  - 【记录】同 B1

  > 完成：2026-08-30 · 用户实机跑通。
  >
  > | 项 | 值 |
  > |---|---|
  > | capture | `archive/in_neakasa.tech/_capture_1788073013.json`（17.2 MB，**99 段 JSON**） |
  > | manifest | 1500 行，post_id 全部唯一 |
  > | 图片已下载 | 1184 张（媒体总数 1833 = 1184 图 + 649 视频，视频未下载，符合边界） |
  > | 时间范围 | `2020-09-03T10:02:53Z` → `2026-08-30T03:46:39Z`（约 6 年） |
  >
  > ⚠️ **1500 这个数字是错的。** 逐条核对后拆解为：
  >
  > | 类别 | 条数 | 说明 |
  > |---|---:|---|
  > | 本账号真实帖子 | **756** | owner == `neakasa.tech` |
  > | 轮播子项被当成独立帖 | **478** | `product_type == "carousel_item"`，全部无正文 |
  > | 其它账号的帖子 | **266** | 来自另外 **195 个**账号 |
  >
  > 其它账号里数量靠前的是 `neakasa.global`（30 条）、`neakasa.de`（4 条），
  > 其余为宠物 UGC 账号（`ruka.bsh`、`daria.and.zoe`、`cheeeto.the.cat` 等各 2–3 条），
  > 应为人工滚动时页面加载的推荐内容与被 @ 的帖子。
  > manifest 里 487 条空正文记录，绝大多数就是那 478 条轮播子项。
  > 详见 B4 与 `docs/CODE_REVIEW.md` 的 CR-12 / CR-13。
- [x] **B4** 校准 Instagram 解析器

  - 对照 `core/parse.py` 的 `is_iphone_struct()` / `from_iphone_struct()`
  - 重点确认：
    - `image_versions2.candidates[0]` 是否确为最大尺寸（打印前 3 个候选的宽高验证）
    - `carousel_media` 是否存在于轮播帖
    - 视频帖是否有 `video_versions`
  - 【验收】同 B2
  - 【记录】同 B2

  > **2026-08-30 · 真实数据已核对完毕，下面是要修的具体内容（不是待调查项）。**
  > 完整根因分析见 `docs/CODE_REVIEW.md` 的 CR-12 / CR-13。
  >
  > **已证实为正确的假设**（不要动）：`carousel_media` 确实存在于轮播帖
  > （145 篇）、视频帖确有 `video_versions`（649 个视频媒体被正确标 `kind="video"`
  > 且未下载）、`image_versions2.candidates[0]` 取到的图能正常打开。
  >
  > **必须修的两项：**
  >
  > **① 轮播子项被当成独立帖子（CR-13，P0）** —— 这是本项目目前最脏的一处数据。
  >
  > 根因是一行判定写错了语义：
  >
  > ```python
  > # ❌ 现状：判断的是「键存在」
  > def is_iphone_struct(d): return "pk" in d and "code" in d and "taken_at" in d
  > ```
  >
  > `carousel_media` 子项里 `code` 这个**键确实存在，但值是 `None`**，
  > `pk` 和 `taken_at` 也都在，于是每个子项都命中判定，
  > 被 `from_iphone_struct` 造成一篇独立帖子。实测命中 **478 条**。
  >
  > 修法：判定改为**值非空**（`d.get("code")`），并显式排除
  > `product_type == "carousel_item"`。两条都要，前者是根因，
  > 后者是针对这个已知形态的第二道闸。
  >
  > **清洗无损性已验证**：145 个轮播父帖的 `media` 数量**全部 ≥ 其子项数**
  > （不足的 0 个），说明父帖已收全所有子图，
  > **删掉这 478 条不丢任何一张图片**。可以放心重建。
  >
  > **② 跨账号污染（CR-12，P0）** —— 同 B2 的根因，但 IG 这边严重得多：
  > 266 条来自另外 195 个账号。归属字段在 `node.user.username`
  > 或 `node.owner.username`（本账号是 `neakasa.tech`，与 `config.toml`
  > 里的值**直接相等**，比 FB 那边好处理）。
  > ⚠️ 注意大小写，以及别误用 `full_name`。
  >
  > 另需注意：那 478 条子项**没有 owner 字段**（实测 478/478 全部无 owner）。
  > 所以过滤顺序必须是**先排除子项、再按 owner 过滤**，
  > 否则子项会因为"owner 不匹配"被算进"其它账号的帖子"，统计口径就错了。
  >
  > 【新增验收】修完用 B7 的离线重放跑 capture，应得到 **756 篇本账号帖、
  > 0 篇 `product_type=="carousel_item"`、0 篇 owner 非本账号**；
  > 空正文的帖子数应从 487 大幅下降（剩下的才是真的没写文案的帖子）；
  > `_rejected.jsonl` 里应有 478 + 266 = **744 条**记录，且丢弃原因分两类可区分。

  > 完成：2026-08-30 · 两项都修了，实测数字与预演**逐个吻合**。
  >
  > | 指标 | 修复前 | 修复后 |
  > |---|---:|---:|
  > | 解析出的候选 | 1500 | **1022** |
  > | 本账号保留 | —（全都当成本账号） | **756** |
  > | 丢弃（他人帖） | 0 | **266** |
  > | 正文为空 | 487 | **9** |
  >
  > `is_iphone_struct` 改成判值（`d.get("code")`）并排除
  > `product_type == "carousel_item"`。`is_graphql_node` 一并改成判值——
  > 那边没有真实数据证实会误命中，但同一类判定不该在项目里留两种写法。
  >
  > **归属**取自 `user.username`（实测 1022/1022 全走这个字段，
  > `owner.username` 一个没有；两个都收着，成本为零）。
  >
  > **⚠️ 计划里写的 `_rejected.jsonl` 应有 744 条，实际只写 266 条，这是有意的偏差。**
  > 那 478 条轮播子项**根本不是帖子，是帖子的一部分**——判定修好之后解析器
  > 压根不会把它们当候选，"丢弃"这个词对它们不成立。`_rejected.jsonl` 回答的是
  > 「解析器看见了什么、又扔掉了什么」，把零件写进去只会稀释真正要看的那 266 条。
  > 478 这个数字改为由 B7 的重建报告体现（旧 1500 → 新 756，差额 744 拆成
  > 478 子项 + 266 他人帖），审计链条不断。
  >
  > **轮播父帖仍收全子图**（`from_iphone_struct` 的 `carousel_media` 遍历没动）：
  > 重建后 128 篇帖子的媒体数 > 1，最多一篇 10 项。
- [x] **B5** 媒体下载验证

  - 检查 `archive/*/media/` 下的图片文件
  - 【验收】
    - 文件数与 manifest 中 `kind=="image"` 的媒体数一致
    - 随机抽 3 张能正常打开，且分辨率与 manifest 记录的 `width`/`height` 一致
    - **无 0 字节文件**
    - 视频**没有**被下载（范围外），但 manifest 里有记录
  - 【记录】记下图片的典型分辨率。若普遍低于 1000px，在完成行标注
    "下游图像处理输入质量受限"
- [x] **B6** 回填结果盘点

  - 统计：总帖数、图文帖数、视频帖数、`media_complete=False` 的帖数
  - 【验收】总数与你在浏览器里目测的帖子数量大致相符（差异 >20% 需排查）
  - 【记录】写下四个数字

  > ~~B5 / B6 被 B7 阻塞~~ **阻塞已解除**，B7 于 2026-08-30 重建完成。
  >
  > **B5 完成：2026-08-30 · 四条验收全过。**
  >
  > | 验收项 | Facebook | Instagram |
  > |---|---|---|
  > | 文件数 == manifest 中 image 媒体数 | 70 == 70 ✅ | 648 == 648 ✅ |
  > | 0 字节文件 | 0 ✅ | 0 ✅ |
  > | 抽 3 张：能打开且分辨率与 manifest 一致 | 3/3 ✅ | 3/3 ✅ |
  > | 视频未下载但有记录 | 18 条记录 / 0 本地文件 ✅ | 377 条记录 / 0 本地文件 ✅ |
  >
  > 【记录】典型分辨率：**IG 以 1080×1080 为主**（296 张），另有 1440×1440（62 张）、
  > 1080×1350（56 张）；**FB 以 1080×1350 为主**（23 张）。
  > 绝大多数达到或超过 1080px，**下游图像处理的输入质量没有受限**。
  > 唯一偏小的一档是 FB 的 960×1200（13 张），短边略低于 1000px，可以接受。
  >
  > **B6 完成：2026-08-30 · 盘点如下（重建后的数字）。**
  >
  > | 指标 | Facebook | Instagram |
  > |---|---:|---:|
  > | 总帖数 | **46** | **756** |
  > | 含图片的帖子 | 26 | 404 |
  > | 含视频的帖子 | 18 | 369 |
  > | 完全无媒体 | 3 | 0 |
  > | `media_complete=False` | 0 | 0 |
  > | **有正文（可进翻译流水线）** | **45** | **747** |
  >
  > （含图 + 含视频 之和大于总数，是因为有帖子图和视频都有：FB 1 篇、IG 17 篇。）
  >
  > 【验收】"总数与目测相符"——**用户已确认 FB 主页的内容数与抓到的一致**，
  > 所以 46 篇就是 FB 的完整历史，B1 那条"可能没滚到底"的疑虑**已排除**。
  > IG 的 756 篇跨 2020-09 至 2026-07，用户未逐一核对，但数量级与 6 年周更相符。
  >
  > ⚠️ **重建后 IG 的最新一帖是 2026-07-15，而重建前 manifest 里最新是 2026-08-30**——
  > 那条 8 月 30 日的帖子不属于 neakasa.tech，是滚动时混进来的他人内容。
  > 换句话说，**这个账号最近一个多月没有发新帖**。这个事实会影响 D1 的
  > `check_quiet` 阈值判断（计划假设的是"日均约 1 帖"），C/D 组落地时要重新校准。

- [x] **B7** 离线重放重建归档（修完 B2/B4 之后）

  > **不需要用户重新滚动。** 两份 `_capture_*.json` 完整保留，
  > 重建完全走离线重放。这也正是 `backfill.py` 在解析前无条件转储的意义所在。

  - 新增一个入口（建议 `python -m routes.backfill --replay <capture 路径>`，
    或独立的 `tools/replay.py`），做三件事：
    1. 读 capture → 用修好的解析器重新 `extract()`
    2. 重建 `manifest.jsonl`（**先备份旧的为 `manifest.jsonl.bak`，不要直接覆盖**）
    3. 把被丢弃的节点写进 `_rejected.jsonl`（post_id / owner / 丢弃原因）
  - **媒体不重新下载。** CDN 签名 URL 早已过期，重下必然 403。
    做法是把已在盘上的文件与新记录重新关联：现有文件名是
    `<post_id>_<n>.<ext>`，重建时按同样规则查找即可命中。
    - 轮播子项行删除后，它们对应的媒体文件成为孤儿（父帖已另有完整副本）。
      **移动到 `_orphan_media/` 而不是删除**，确认无误后再由人清理。
    - 非本账号帖子的媒体同样移入 `_orphan_media/`。
  - 【验收】
    - FB 重建出 **46 篇**，其中 `kind=="video"` 的 20 篇，owner 非本账号 0 篇
    - IG 重建出 **756 篇**，`carousel_item` 0 篇，owner 非本账号 0 篇
    - `_rejected.jsonl`：FB 1 条、IG 744 条（478 子项 + 266 他人帖），原因可区分
    - 重建前后**图片文件一张都没少**（孤儿只是移走，没有删除）
    - `manifest.jsonl.bak` 存在，可随时回退
  - 【记录】重建后的实际数字；与上面预期不符的地方要查清楚再往下走

  > 完成：2026-08-30 · 实现为 **`tools/replay.py`**（独立入口，没有塞进
  > `backfill.py` —— 那个模块的职责是"挂监听等人滚"，重放是另一件事）。
  >
  > ```
  > python -m tools.replay facebook              # 取该账号最新的 capture
  > python -m tools.replay instagram --dry-run   # 只看结果不写盘
  > python -m tools.replay facebook --capture <路径>
  > ```
  >
  > **重建结果，两个平台全部达标**：
  >
  > | 验收项 | Facebook | Instagram |
  > |---|---|---|
  > | 本账号帖子 | 46（预期 46）✅ | 756（预期 756）✅ |
  > | owner 全部为本账号 | ✅ | ✅ |
  > | `_rejected.jsonl` | 1 条 ✅ | 266 条 ✅ |
  > | **图片一张没少** | 70 挂上 + 4 孤儿 = 74 ✅ | 648 挂上 + 536 孤儿 = 1184 ✅ |
  > | `local_path` 指向的文件全部存在 | ✅ | ✅ |
  > | `manifest.jsonl.bak` | ✅ | ✅ |
  >
  > **一处比计划更稳的实现**：媒体重新关联**按 URL 而不是按下标**。
  > 计划原文写的是"按 `<post_id>_<n>.<ext>` 同样规则查找即可命中"，
  > 但修复后媒体的排列顺序变了（FB 现在会在图片后面追加视频），
  > 下标关联会张冠李戴。URL 来自同一份 capture，两次解析必然逐字符相同，
  > 是可靠的键。实测 718 个文件全部正确关联，0 个失配。
  >
  > **孤儿媒体是移动不是删除**（`_orphan_media/`）。IG 那 536 个是轮播子项与
  > 他人帖子留下的重复文件；FB 那 4 个属于被丢弃的 Garden State Cat Club 帖。
  > 确认无误后可由人清理，本工具不代劳。
  >
  > `raw/` 下留下了 745 个不再对应任何帖子的文件（FB 1 + IG 744），**未动**。
  > 它们只是 `to_row()` 的副本、不含独有信息，J 组重构布局时一并处理。

- [x] **B8** 回填进度显示（对应 CR-15）

  - 现状：终端只打印"已捕获 N 个响应 / M 段 JSON"。**这两个数字对操作者没有意义**，
    他无法判断还要滚多久、有没有滚到最早一篇
  - 改为对人有意义的量，边滚边刷新：
    - 已解析出的**本账号**帖子数（不是响应数）
    - 目前**最早一篇的日期**
    - 连续 N 秒无新响应时提示"页面似乎不再加载，可以按 Enter 收尾"
  - ❌ **不得因此引入任何驱动页面的动作。** 人工滚动的全部价值在于滚动的确实是人。
    这一项只改打印，不碰页面
  - 【验收】重跑一次回填（任一平台），终端能实时显示帖子数与最早日期
  - 【业务动因】FB 只回填到约 2 个月（46 篇），IG 却有 6 年。
    差距如此之大，**无法排除 FB 根本没滚到底**——而当时操作者没有任何判断依据。
    这一项修好后需要用户按 B8 的显示重跑一次 FB 确认

  > 完成：2026-08-30 · 实现为 `routes/backfill.py::ScrollProgress`。
  > 滚动时的那一行现在长这样：
  >
  > ```
  >   已抓到 128 篇（neakasaofficial）· 最早 2024-03-12  ·  24 秒没有新内容了，可以按 Enter 收尾
  > ```
  >
  > 三个数都是操作者真正需要的：**篇数**（不是响应数）、**最早日期**
  > （不再往前走就是到底了）、**静默时长**（页面滚到底后不再发请求，
  > 但操作者看不见网络活动，只能靠猜）。阈值 `STALL_HINT_SECONDS = 20`。
  >
  > 实现上的两个取舍：
  >
  > 1. **增量解析**：每轮只解析新到的那几段 JSON，不是每 2 秒把全部 payload
  >    重跑一遍（IG 那次滚出了 99 段、17 MB，全量重跑会把滚动的人卡住）。
  >    代价是显示用的统计允许有一点点偏差——收尾时会对全部 payload
  >    重新做一次权威解析，归档数据不受影响。
  > 2. **解析异常一律吞掉**：显示是辅助功能，坏了不能把正在进行的抓取带崩。
  >
  > ❌ **没有引入任何驱动页面的动作**，只改打印（禁止事项：人工滚动的全部价值
  > 在于滚动的确实是人）。
  >
  > **⚠️ 这一项的原始业务动因已经消失**：用户 2026-08-30 确认
  > **FB 主页的内容数与抓到的一致**，即 46 篇就是全部，当时并没有漏滚。
  > 但这一项仍然做了、也仍然值得——下次回填（无论是补抓还是 FB 增量降级为
  > 人工触发）操作者依然需要这个判断依据。**不需要用户重跑 FB 了。**

---

## J. 归档布局重构：每帖一个文件夹

> **J 排在这里而不是按字母序排在最后，是因为它必须先于 C/D/F 落地**——
> 它改的正是那三组要读写的布局。依赖 B7（先把数据修干净，再谈怎么组织；
> 否则只是把 744 条脏数据整整齐齐地排列一遍）。
>
> **业务动因（用户 2026-08-30 提出）**：现在图片在扁平的 `media/` 里堆着，
> 文件名是 `<post_id>_<n>.jpg`，正文在人读不了的 JSONL 里。
> 帖子一多就分不清哪些图和哪段文字是一篇、哪天发的。
>
> **决定性的理由不是"好看"，是下游有人要动这些文件**：计划固化了
> 「图内英文文字本期走人工处理」，设计同事会把替换好德文的图交回来。
> 扁平目录下这个流程无法进行。**存在人工编辑环节的数据，
> 就该按人能操作的粒度组织。**

- [x] **J1** 设计并落地新布局

  ```
  archive/<平台前缀>_<账号>/
    index.html                      全账号总览（派生，可重建）
    manifest.jsonl                  派生索引，可从 posts/ 重建
    manifest.jsonl.bak              B7 留下的旧版，确认无误后可删
    _rejected.jsonl                 被丢弃的节点及原因
    _orphan_media/                  重建后无主的媒体文件
    posts/
      2026-08-25_1423_3712345678901234567/
        post.json                   **真相源**：post_id / owner / created_at /
                                    permalink / text / media[] / media_complete /
                                    source_route
        text.txt                    正文纯文本（派生，给人和设计同事看）
        text_de.txt                 德语译文副本（派生）
        01.jpg  02.jpg  03.jpg      原图，按帖内顺序
        media_de/                   设计同事回填的德文版图
      undated_1514468247386817/     时间解析不出来的进这里，不要猜时间
    _capture_*.json
  ```

  - 文件夹名 `<YYYY-MM-DD>_<HHMM>_<post_id>`：日期在前 → 名称排序即时间排序；
    post_id 在后 → 幂等查找不用打开文件
  - **三条必须写死的规则**：
    1. **`post.json` 是真相，`manifest.jsonl` 是派生索引。冲突时以文件夹为准，
       重建索引——永远不反过来。** 方向单一才不会退化成"两个都不可信"
    2. **译文的真相源仍是账号级的 `translated.jsonl`，不变**（守住既有决策：
       重跑抓取不得冲掉花钱买来的译文）。文件夹里的 `text_de.txt` 是派生副本
    3. **被丢弃的节点必须留痕**，写 `_rejected.jsonl`，不得静默丢弃
  - `Archive` 的对外接口（`rows()` / `append()` / `should_append()` /
    `needs_media()` / `media_path()`）**保持不变**，
    这样 `integrity.py`、`delta.py`、`fb_graph.py` 不用改
  - 【验收】
    - 从现有归档迁移后，`posts/` 下文件夹数 == 本账号帖子数（FB 46 / IG 756）
    - 随机抽 3 个文件夹，图文时间三者对得上
    - `python -m core.store --reindex`（或等价命令）能从 `posts/` 重建出
      与迁移前**逐字段一致**的 `manifest.jsonl`
    - 迁移是**可回退的**：保留迁移前的目录快照或提供反向命令

  > 完成：2026-08-30 · 布局改在 `core/store.py`，迁移与重建工具是
  > **`tools/layout.py`**（`migrate` / `reindex` / `index` 三个子命令，都支持 `--dry-run`）。
  >
  > **迁移结果，两个平台全部达标**：
  >
  > | 验收项 | Facebook | Instagram |
  > |---|---|---|
  > | `posts/` 文件夹数 == 帖子数 | 46 == 46 ✅ | 756 == 756 ✅ |
  > | 每个文件夹都有 `post.json` / `text.txt` | ✅ | ✅ |
  > | `local_path` 全部指向真实文件 | ✅ | ✅ |
  > | 图片记录数 == 文件夹里的图片文件数 | 70 == 70 ✅ | 648 == 648 ✅ |
  > | `reindex()` 能从 posts/ 重建出一致的索引 | ✅ | ✅ |
  > | 可回退（`manifest.jsonl.premigrate`） | ✅ | ✅ |
  >
  > **三处实现上的决定，都与计划略有出入，记在这里**：
  >
  > 1. **`media_path()` 的签名从 `(post_id, idx, ct)` 改成 `(post, idx, ct)`。**
  >    文件夹名要用发布时间，光有 id 拼不出来。调用方只有两处下载环节。
  > 2. **媒体编号跟的是「帖内位置」而不是「第几张图」**：一篇 `[视频, 图, 图]`
  >    会得到 `02.jpg`、`03.jpg`，没有 `01`。看着像缺号，但保住的是
  >    这张图在轮播里的真实位置——那个信息比连号值钱。
  > 3. **删掉了 `raw/` 与 `Archive.save_raw()`。** 那个目录的 docstring 声称存的是
  >    "原始响应，便于 schema 变更后重放"，**但代码实际写进去的是
  >    `post.to_row()`**，与 manifest 逐字段相同。真正的原始响应一直在
  >    `_capture_*.json` 里（B7 的重放就是用它）。`post.json` 落地后它成了
  >    纯重复，留着只会误导人。
  >    ⚠️ 迁移**没有自动删除**已存在的 `raw/` 目录（FB 47 个 / IG 1500 个文件），
  >    只打印提示，由人确认后清理。
  >
  > **`undated_<post_id>` 这条分支是有真实数据的**：FB 那条被丢弃的
  > Garden State Cat Club 帖就没有 `created_at`。命名规则里刻意**不猜时间**——
  > 猜出来的日期看起来是真的，比没有日期更难查。

- [x] **J2** 生成 `index.html` 总览

  - 按时间倒序，一帖一张卡片：日期、正文、配图内嵌、原帖链接、
    视频标记、`media_complete=False` 标记
  - 目的是让业务同事双击就能看完整个账号的历史，不需要懂 JSON
  - 【验收】浏览器打开，图片正常显示，能一眼看出某天发了什么

  > 完成：2026-08-30 · `python -m tools.layout index <平台>`，
  > 产出 `archive/<账号>/index.html`（FB 66 KB / IG 548 KB，单文件、无外部依赖）。
  > 抬头写着「46 篇 · 2026-06-29 ~ 2026-08-25 · 45 篇有正文 · 按时间倒序」，
  > 每帖一张卡片：日期、post_id、视频/媒体不全/无配图标签、原帖链接、
  > 正文（保留换行）、配图缩略图。图片用 `loading="lazy"`，756 篇也不卡。
  >
  > 【验收】索引里 **FB 70 个、IG 648 个 `<img>`，指向不存在文件的 0 个**，
  > 与 manifest 的图片记录数逐个对上。
  > ⚠️ 在预览面板里图显示不出来是因为它按 `data:` 快照渲染、解析不了相对路径；
  > **双击文件用真正的浏览器打开是正常的**。

  > 顺带发现（不属于本项任务，记一笔免得下游踩坑）：
  > **FB 有 1 篇帖子的正文里混着德语**（2026-08-24 那篇 IFA 展会的，
  > 英文正文 + 一句德语 P.S.）。F 组的提示词是按"英译德"写的，
  > 遇到已经是德语的段落会怎么处理没有验证过。只有 1 篇，人工处理即可，
  > 但翻译试跑时要留意这一篇。IG 那 756 篇里没有发现同类情况。

- [x] **J3** 同步下游对新布局的引用

  - `translate.py` 的 `review.md` 图片相对路径需跟着改
    （`review.md` 与 `posts/` 同级，引用变成 `posts/<文件夹>/01.jpg`）
  - `tests_store.py` / `tests_translate.py` / `tests_backfill.py` 里
    涉及归档布局的断言需更新
  - 【验收】全部离线测试仍全绿；`review.md` 在 VS Code 预览里图片能显示

  > 完成：2026-08-30 · 离线测试基线 **10 套 400 项，全绿**（改动前 382）。
  >
  > **`review.md` 的图片引用一行都没改。** 它用的是
  > `m["local_path"]` 且自身就写在 `arc_base`——`local_path` 从
  > `media/x_0.jpg` 变成 `posts/<文件夹>/01.jpg` 之后，相对引用天然还是对的。
  > 这属于运气，但也说明当初"local_path 一律相对 arc_base"那个约定选对了。
  >
  > **新增 `translate.sync_text_de()`**：把译文同步一份 `text_de.txt` 到每帖文件夹。
  > ⚠️ **`translated.jsonl` 仍是译文的唯一真相源**，文件夹里那份是派生副本。
  > 这条边界是刻意保留的——把真相源挪进文件夹会更自洽，但会动到
  > "译文写独立文件、重跑抓取不能冲掉花钱买来的结果"这个已拍板的决策，
  > 不值得为整洁去动它。副本删了也没关系，跑一次 `--review` 就回来。
  >
  > 测试侧的改动值得记一笔：`tests_backfill.py` 与 `tests_delta.py` 原来的
  > fixture **不带 owner**，加上归属过滤后立刻红了——**这正是拦截生效的证据**。
  > 补齐 fixture 的同时各加了一组新断言（主流程拦住他人帖并写 `_rejected.jsonl`、
  > 登出增量拦住他人帖、归属未知一律丢弃）。

---

## C. 每日增量：登录态 + CDP 附着（方案 B，依赖 B 组）

> ### ⚠️ 这一组在 2026-08-30 被整体重写，动手前先读完
>
> **原设计是"完全登出"**，因为登出没有账号、没有 session、没有 cookie，
> **没有可封的东西**。该前提已被推翻：实测 + 用户确认，FB/IG 在没有登录态时
> 直接报错。
>
> **用户拍板走方案 B：增量改走登录态**，复用回填那条 CDP 通道
> （详见第 0 节的方案变更说明）。
>
> **这个方案把封号风险从「一次性敞口」变成「累积性敞口」**——
> 而抓取小号被封是本项目唯一不可恢复的失败模式，回填与增量会同时断掉。
> **C7 的每一条缓解措施都是这个方案的组成部分，不是锦上添花。**
>
> **两条没有改变的红线**：
> 1. ❌ 仍然不得实现自动登录。复用的是人工登录留下的会话，不是让程序去登。
> 2. ❌ 回填仍然是人工滚动。
>
> **C1（登出 HTTP 客户端）现在用不上了**，但保留——见该项下的说明。

- [x] **C1** 实现登出 HTTP 客户端 `core/http.py`

  - 新建文件，实现一个函数 `logged_out_client() -> httpx.Client`
  - 要求：
    - **不携带任何 cookie**（这是本路径零风险的前提）
    - 默认 Header 含 Safari UA（复用 `core.session.SAFARI_UA`）
    - `timeout=30`，`follow_redirects=True`
  - 另实现 `ig_headers(username: str) -> dict`，返回第 1 节表格里的 IG 必需 Header
  - 【验收】
    - `logged_out_client().cookies` 为空
    - 单元测试：构造 client 后断言无 cookie、UA 正确

  > 完成：2026-08-29 · `tests_http.py` 17 项断言全绿。无新增依赖。
  >
  > **一处比计划更严的实现**：验收只要求"构造后 cookies 为空"，但那个条件
  > 太容易满足了——`httpx.Client()` 默认自带 cookie jar，它会**接受响应里的
  > Set-Cookie 并在后续请求带回去**。跑几次之后这条路径就悄悄地不再是"登出"的，
  > 而且没有任何迹象，"没有可封的东西"这个前提会无声失效。
  > 因此显式装了一个 `DefaultCookiePolicy(allowed_domains=[])` 的 jar，
  > 对所有域名拒收。（注意 `allowed_domains=[]` 是空列表才表示"一个都不允许"，
  > 写成 `None` 是"不限制"，正好相反。）
  > 测试用 `httpx.MockTransport` 构造下发两个 Set-Cookie 的响应，断言
  > jar 仍为空、且**下一次请求不含 Cookie 头**——这条才是真正的验收。
  >
  > **⚠️ 2026-08-30 补记：方案 B 之下这个模块用不上了，但保留，不要删。**
  >
  > 它是给"完全登出"那条路径写的。而实测 + 用户确认，FB/IG 在没有登录态时
  > 直接报错（对公开账号请求 `web_profile_info` **首次即 429**），
  > 登出路径不存在了。方案 B 的增量走登录态 CDP，媒体下载也走浏览器请求栈
  > （复用它的 cookie 与 TLS 指纹），两处都不需要这个客户端。
  >
  > **保留的理由有两个**：① 端点若重新对登出开放，切回去是风险更低的路径，
  > 而这份实现是对的（17 项断言全过，含 Set-Cookie 攻防）；
  > ② `IG_APP_ID` / `ig_headers()` 这些常量本身仍是有效知识。
  > **它现在是死代码，`ruff` 之类工具报"未使用"是预期的，不要顺手删。**
  >
  > 其它：`IG_APP_ID` / `IG_ASBD_ID` 作为模块常量写死。这两个是公开且长期稳定的
  > 值，与每 2–4 周轮换的 `doc_id` 是两回事，不违反禁止事项 2（测试里有一条
  > 断言 header 中不含 `doc_id`）。`logged_out_client(**kwargs)` 支持传
  > `transport=` 以便离线测试，也支持 `headers=` 合并自定义头而不冲掉默认 UA。
- [x] **C2** 实现 Instagram 登录态增量

  > 完成：2026-08-31 · **真实验收通过。** 用户实跑一次增量，Instagram 解析出
  > 本账号 36 篇（**原创 1 · 合作 35**）、丢弃 3 条推荐位、新增 1 篇。
  > 新增的那篇是 `3975547640610092585`（2026-08-31），
  > `owner=neakasa.global`、`coauthors=['neakasa.tech']` —— **它本身就是合作帖**，
  > 修复前会被直接丢掉。这同时证明了两件事：登录态增量能拿到时间线，
  > 以及合作帖判定在**当天的真实响应**上仍然成立（不只是在保存的转储上）。
  >
  > 验收前先用 `python -m tools.dryrun_delta instagram` 离线跑完了同一条代码路径，
  > 结果与真实抓取完全一致 —— **这条工具因此可以替代大部分"再跑一次看看"**。

  > 进行中：2026-08-30 · **代码与离线测试完成，验收未过——三条验收全都要对真实
  > 账号跑一次抓取，用户已定"全部写完后一次性实测"（见附录 D）。** 不勾选。
  >
  > 实现是 `routes/delta.py` 下半部分的 `delta_once()`（**两个平台共用一份**，
  > 不是 C2/C3 各写一份：两边的差别只有一个 URL 和一套解析器，
  > 而解析器差异 `core/parse.py` 已经吸收掉了。分开写只会让 C7 的七条缓解措施
  > 各存在两份、然后慢慢漂移）。
  >
  > 骨架照抄 `routes/backfill.py`，改掉三处：不等人按 Enter、按 `max_scrolls`
  > 只滚有限几屏、每一步都受 C7 的参数约束。用 `should_append()` 而非 `has()`
  > （残缺帖可升级，离线测试里有专门一条断言）。
  >
  > **旧的登出实现原样保留在同一文件上半部分**，`--logged-out-probe` 仍可跑它。
  >
  > 【离线已验证】`tests/tests_delta_logged_in.py` 95 项：登录墙四种长相、
  > 深度上限、幂等、归属过滤、残缺帖升级、dry-run 不写盘、四类异常即停。

  > **2026-08-30 首次真实实测：Instagram 没通过，暴露两个缺陷。** 详见
  > `docs/CODE_REVIEW.md` 的 CR-16 / CR-17。用户跑了 1 次 dry-run + 2 次实跑，
  > 输出全是"新增 0 篇"、没有任何报错——**而真相是它根本没看到 IG 的时间线**。
  >
  > 离线复盘 `_capture_delta_*.json` 得到的实际情况：
  >
  > | | 候选 | 本账号 | 丢弃 | 看到的日期范围 | 归档最新 |
  > |---|---:|---:|---:|---|---|
  > | Facebook | 6 | **6** | 0 | 2026-08-16 ~ **2026-08-25** | 2026-08-25 |
  > | Instagram | 39 | **1** | 38 | 2026-06-06 ~ **2026-06-06** | 2026-07-15 |
  >
  > IG 那 38 条丢弃的是推荐位（`neakasa.global` 29 条 + 宠物 UGC），
  > **本账号只有孤零零 1 篇，而且比归档里最新的还旧**。
  >
  > **根因（CR-16）：IG 主页时间线的首屏是随 HTML 下发的，不走 XHR。**
  > 只拦响应就永远看不到最新那十几篇——而增量要的恰恰是最新的。
  > ⚠️ **"多滚几屏"解决不了**：往下滚只会拿到更旧的分页。
  > 修法：`core/capture.py::harvest_embedded_json()`，把页面里
  > `<script type="application/json">` 的内容也一起喂给解析器
  > （纯读已加载的 DOM，不发额外请求；`walk()` + 归属过滤本来就能处理这种输入）。
  >
  > **同时修掉的第二个缺陷（CR-17）：滚动很可能一直没生效。**
  > `page.mouse.wheel` 在**当前鼠标位置**派发事件，默认位置 (0,0) 是导航栏。
  > 现在先 `mouse.move` 到视口中间，并**回读 `window.scrollY` 确认页面真的动了**，
  > 没动就打警告。
  >
  > **⛔ 当晚查清：上面那条 CR-16 的诊断是错的，真正的原因是合作帖（CR-19）。**
  >
  > 用户提出"这个账号很多帖子是两个账号共同发的"。查证属实：IG 的**合作帖**
  > 由一方发布、双方主页同时显示，而 `user.username` 只记原始发布者。
  > 那 38 条"推荐位"里 **35 条是合作帖**——**时间线一直在 XHR 里，
  > 是归属判定把它丢掉了**。
  >
  > 把合作帖算进来之后，那次增量实际看到的是
  > **36 篇本账号主页上的帖子，日期跨 2026-06-06 ~ 2026-08-27**。
  > 也就是说 C2 的抓取链路本来就是通的，坏的是下游的归属判定。
  >
  > 修复见 `core/parse.py::on_timeline_of()` 与 CODE_REVIEW 的 CR-19。
  > `harvest_embedded` 默认改为 false（它修的是不存在的问题，但保留为兜底开关）。
  >
  > 【真实验收待办】**合作帖修复后还没有实机跑过**。再跑一次即可收尾：
  > IG 应看到约 36 篇本账号主页上的帖子（原创 + 合作）、
  > 最新一篇不早于 2026-08-27、连续两次第二次 0 新增。
  > 旧实现（`web_profile_info` + `core/http.py`）**保留在 `routes/delta.py` 里
  > 不要删**：代码本身是对的（55 项离线断言全过，含归属过滤），
  > 端点若重新开放可直接切回去，那是风险更低的路径。

  - 在 `routes/delta.py` 中实现 `fetch_instagram_logged_in() -> list[Post]`
  - 流程（**照抄 `routes/backfill.py` 的骨架，但不要人工介入**）：
    1. `core.chrome.attach()` 附着到专用 Chrome（**不要用 Playwright 自启浏览器**，
       那会让 `navigator.webdriver` 为真、指纹变成自动化浏览器）
    2. 新开 page，挂 `page.on("response")` 收 `INTEREST` 里的接口响应
    3. `page.goto(主页 URL)`，等首屏加载
    4. **只向下滚有限的几屏**（见 C7 的深度上限），不要滚到底——
       增量只需要看到最新的几条，滚到底既慢又是明显的机器行为
    5. `extract()` → `partition_by_owner()` → `should_append()` → 下载 → `append()`
  - 复用已有的 `Archive.should_append()`（**不要用 `has()`**，
    否则残缺帖永远补不全，见 HANDOFF 的两个 API 约定）
  - 错误处理：
    - 页面出现登录墙 / 被重定向到登录页 → **立即停止，不重试**，
      调 `core.notify.notify()` 告警"会话可能已失效，需要人工重新登录"
    - 连不上调试端口 → 明确提示"专用 Chrome 没开"，退出码非 0
  - 【验收】
    - 对 `neakasa.tech` 运行，能返回本账号的最新若干篇，`owner` 全部正确
    - **连续运行两次，第二次新增 0 篇**（幂等）
    - `--dry-run` 不写盘
- [x] **C3** 实现 Facebook 登录态增量

  - 同 C2，目标 `neakasaofficial`
  - FB 的响应形态与回填时相同（`is_fb_story`），解析器已在 B2 校准过，不需重做
  - ⚠️ **FB 首屏可能包含推荐内容与被 @ 的 UGC**（回填时实测混进 1 条）。
    `partition_by_owner()` 必须照常调用，不能因为"增量只看几条"就省掉
  - 【验收】同 C2

  > 完成：2026-08-30 · **三条验收在真实账号上全过。** 与 C2 共用
  > `delta_once()`，平台差异只在 `profile_url()` 与 `core/parse.py` 已有的
  > 解析器分派。
  >
  > | 验收项 | 实测结果 |
  > |---|---|
  > | 返回本账号最新若干篇，owner 全部正确 | 6 篇，**丢弃 0 篇**，owner 100% 正确 ✅ |
  > | 连续运行两次，第二次新增 0 篇 | 跑了 3 次，每次都 0 新增，归档无重复 ✅ |
  > | `--dry-run` 不写盘 | 无转储、manifest 未变 ✅ |
  >
  > **"新增 0 篇"这次是真的**：页面上最新一篇是 2026-08-25，与归档里最新的
  > 逐日相同。我原本预判"归档缺了 8-25 之后几篇"，**这个预判错了**——
  > FB 那阵日更是一段时间的节奏，8-25 之后确实停了。
  > 判定依据是离线复盘 capture 得到的日期范围，不是"没报错就算过"。
  >
  > ⚠️ **验收证据采集于修 CR-16/CR-17 之前。** 那两处修改也会走 FB 这条路
  > （滚动定位、内嵌 JSON、`min_own_posts` 闸）。FB 侧不受影响的判断依据是：
  > 它本来就从 XHR 拿到 6 篇（≥ 闸门 3），内嵌 JSON 只是多一路来源。
  > **下次 IG 复测时顺带复验 FB。**
- [x] **C4** 增量的媒体下载

  > 完成：2026-08-31 · **验收通过，而且这是媒体下载第一次被真正触发**——
  > 此前几次实测都是"新增 0 篇"，下载那段代码从来没跑过。
  > 本次 Facebook 新增 1 篇下了 **5 张图**、Instagram 新增 1 篇下了 **1 张图**，
  > 落在 `posts/<日期_时分_id>/01.jpg…` 下，**全部非 0 字节**
  > （FB 91–100 KB/张，IG 323 KB）。`media_complete` 均为 True。

  - **走浏览器自己的请求栈**（`ctx.request.get()`，同 `backfill._download`），
    不要用 `core/http.py` —— 那是给登出路径写的，方案 B 下不适用；
    而且用浏览器请求栈能复用它的 cookie 与 TLS 指纹
  - 视频跳过（只记元数据），图片必须在**同一次运行内**下完
  - ⚠️ 媒体 URL 带签名且有时效，不能先存 URL 事后再取
  - 媒体落在该帖自己的文件夹里（`Archive.media_path(post, idx, ct)`，J 组布局）
  - 下载失败必须留下 `media_complete=False`（CR-04 已确立），下次可重试
  - 【验收】新抓到的帖子在 `posts/<文件夹>/` 下有对应图片，无 0 字节

  > 进行中：2026-08-30 · **没有新写一份下载逻辑，而是把回填那份抽进
  > `core/capture.py::download_media()`，两条路径共用。**
  >
  > 理由值得记：抄一份过去的代价不是重复代码，是**语义漂移**——CR-04 确立的
  > 「下载失败必须留下 `media_complete=False`」一旦只在一处生效，
  > 另一条路径就会安静地把失败记成完整，而且要几周后才会被人发现。
  > 同时抽走的还有 `Collector` 与 `INTEREST`（`routes/backfill.py` 改为 import，
  > 对外名字不变，现有测试不受影响）。
  >
  > `Collector` 顺带加了一个回填不需要、增量必需的能力：**记下非 200 的接口
  > 状态码**（`blocked_status()`）。回填有人盯着，增量没有——401/403/429
  > 时页面壳照常渲染、URL 完全正常，只看 URL 会把"被拦"读成"这号没发帖"。
  >
  > 【真实验收】2026-08-31 通过：FB 5 张 / IG 1 张，全部落进各自文件夹、无 0 字节。
- [x] **C5** 运行状态记录

  > 完成：2026-08-31 · 验收通过。两个平台的 `state/delta_state.json` 都写出了
  > `first_success` / `last_success` / `last_new_at` / `last_new_count=1` /
  > `consecutive_quiet_days=0` / `consecutive_failures=0`。
  > 同一次运行里 D3 的告警也第一次真实触发（见附录 D 的 2026-08-31 条）。

  - 在 `state/` 下维护 `delta_state.json`：
    ```json
    {"facebook": {"last_success": "2026-08-30T10:00:00Z", "last_new_count": 0,
                  "consecutive_quiet_days": 3, "last_error": null},
     "instagram": {...}}
    ```
  - 每次增量运行后更新。**失败也要写**（`last_error`），
    否则"连续三天失败"和"连续三天没新帖"在状态文件里长得一样
  - 【验收】连续运行两次，`last_success` 被正确刷新；人为制造一次失败，
    `last_error` 有内容且 `last_success` 不被刷新

  > 进行中：2026-08-30 · `state/delta_state.json`，读写全是纯函数
  > （`record_success` / `record_failure` / `quiet_days` / `budget_exhausted`），
  > 离线测试直接测这些函数。等真实的"连续运行两次"，不勾选。
  >
  > **比计划多两个字段，各有必须存在的理由**：
  >
  > | 字段 | 为什么必须有 |
  > |---|---|
  > | `first_success` | 从没抓到过新帖时，"零新增几天了"要有个起点。没有它，一个从来没成功过的平台永远显示 0 天，正好是最该告警的情况 |
  > | `consecutive_failures` | C7 的失败预算要它。`last_error` 只回答"上次错了吗"，回答不了"连续错了几次" |
  >
  > **`consecutive_quiet_days` 按天算而不是按次算**（计划没写死，实现时定的）：
  > 按次算的话 `runs_per_day` 一改这个数的含义就变了（跑两次 = 一天算两天），
  > 而它下游喂给的是"连续 N **天**零新增就告警/降频"。
  >
  > **失败不刷新 `last_success`** —— 刷新了 `--if-stale` 就会以为跑过了，
  > 于是"每天失败"会表现成"每天正常"。离线测试有专门一条钉住它。
  >
  > **2026-08-30 实测：验收的前一半过了。** 两个平台连跑两次，
  > `last_success` 从 `10:35:53` 正确刷新到 `10:39:26`，`first_success` 保留，
  > `consecutive_failures` 为 0。**后一半（失败时不刷新 `last_success`）
  > 只有离线断言**，真实的失败路径要等 IG 复测时自然触发。
  >
  > ⚠️ **`consecutive_quiet_days` 从"我们开始看"算起，不是从账号最后一次发帖算起。**
  > 实测 IG 已经 45 天没发新帖，但状态里是 0 天。这是**刻意的**：
  > 从归档最新日期起算的话，IG 第一天跑就会报"连续 45 天零新增"——
  > 那不是增量坏了，是监控开始之前就存在的状态。第一天就报一个
  > 用户无法处理的警报，只会训练他忽略警报。
- [x] **C6** 增量主入口

  - `routes/delta.py` 的 `__main__`：依次跑 FB 和 IG，写归档，更新状态
  - 支持 `--platform facebook|instagram|all`（默认 all）
  - 支持 `--dry-run`（只抓不写盘）
  - 支持 `--if-stale`（E2 会用，见 E 组）
  - **必须先检查专用 Chrome 是否就绪**（`core.chrome.cdp_ready(port)`，
    **不是 `port_open`** —— 见 HANDOFF 的两个 API 约定）。
    没就绪时：尝试拉起（复用 `tools/start_chrome.py` 的逻辑）或明确报错，
    ❌ **不得静默跳过**——静默跳过会让"每天都在跑"变成"一年没跑过"而无人察觉
  - 【验收】`python -m routes.delta --dry-run` 能完整跑完并打印将要新增的帖子

  > 完成：2026-08-30 · **验收通过**：用户实机跑
  > `python -m routes.delta --dry-run --no-jitter`，两个平台完整跑完
  > （CDP 附着成功、页面打开、响应捕获、解析、状态落盘），零报错。
  > 参数：`--platform` / `--dry-run` / `--if-stale` / `--no-jitter` /
  > `--reset-failures` / `--status` / `--logged-out-probe`。
  >
  > ⚠️ **但那次的输出是"新增 0 篇"四个字，什么都看不出来**——IG 其实
  > 已经坏了（CR-16），而输出与"真的没新帖"完全一样。
  > 事后加了 `ScanResult`：现在这一行是
  > `新增 0 篇 · 本账号 6 篇（2026-08-16 ~ 2026-08-25）· 丢弃 0 · 归档最新 2026-08-25`。
  > **入口跑通了不等于入口说清楚了**，这一条是这次实测最值钱的收获。
  >
  > **Chrome 没就绪时自动拉起**（用户 2026-08-30 拍板；另一个选项是报错退出）。
  > 拉起逻辑抽进 `core/chrome.py::launch()`，`tools/start_chrome.py` 改为共用——
  > 增量在 Chrome 没开时要做的事和那个脚本逐字相同，两处各写一份必然漂移。
  > ⚠️ **这不是自动登录**：会话是人留在 profile 目录里的，程序只是把那个
  > 浏览器重新用起来。红线 1 没有松动。
  > 拉不起来时：记失败 + 告警 + 非 0 退出，❌ **不静默跳过**。
  >
  > **计划外新增 `--status`**：不抓取，只打印两个平台的上次成功/零新增天数/
  > 连续失败次数。理由是这条路径的失败是**延迟可见**的——用户需要一个
  > 零成本、零风险的方式回答"它这几天到底在不在跑"，而不是只能去翻日志。
  >
  > **计划外新增 `--reset-failures`**：失败预算用尽后必须有一条人工确认的出路，
  > 否则唯一的恢复方式是手改 JSON。
  >
  > **退出码分三档**（计划只说"非 0"）：0 = 正常或不到期；1 = 抓取失败；
  > **2 = 失败预算用尽，需要人工处理**。E3 的计划任务据此区分"今天没到期"
  > 和"已经停摆等人"。
- [x] **C7** 累积风险缓解（**方案 B 的组成部分，不是可选项**）

  > 方案 B 用"每天都来一次"换掉了"没有可封的东西"这个保护。
  > 下面每一条都是在为这个交换买保险。**不要因为"跑得挺好"就删掉它们**——
  > 这类风险的反馈是延迟的、且只反馈一次。

  | 措施 | 怎么做 | 为什么 |
  |---|---|---|
  | **随机化触发时刻** | 计划任务定在某个基准时刻，程序内部再随机延迟 0–N 分钟（N 进 `config.toml`） | 每天 09:00:00 整点发起请求是最容易被识别的模式之一 |
  | **抓取深度上限** | 只滚 `[delta].max_scrolls` 屏（建议 2–3），够看到最新几条即可 | 每天滚到底既无意义又是明显的机器行为 |
  | **滚动节奏拟人** | 每屏之间随机停顿（复用 `core.session.Pacer`），不要连续 `mouse.wheel` | 匀速滚动是行为指纹 |
  | **异常即停** | 出现登录墙 / 非 200 / 解析出 0 篇 → 当次立即停止并告警，**不重试、不换 UA、不绕** | 继续试探是把"可能被注意到"变成"确定被注意到" |
  | **频率可降级** | `[delta].runs_per_day` 已存在；再加一个"连续 N 天零新增就自动降频"的开关 | 该账号实测一个多月没发新帖，天天全速跑没有收益 |
  | **失败预算** | 连续失败达阈值就**停止自动运行**并要求人工确认，不要无限重试 | 会话失效时每天硬撞是最坏的一种行为 |
  | **不与发布账号共用 profile** | 抓取小号与 DE 发布账号必须是不同的 Chrome profile | 小号被标记时不牵连持有 DE 资产的账号 |

  - 参数全部进 `config.toml` 的 `[delta]`，**代码里不写死数字**
  - 【验收】
    - 随机延迟：连续启动 3 次，实际发起请求的时刻不同
    - 深度上限：把 `max_scrolls` 设为 1，确认只滚一屏就收尾
    - 异常即停：mock 一个登录墙响应，确认当次立即返回且发出告警
    - 失败预算：人为把 `last_error` 连续写满阈值，确认下次运行拒绝执行并提示
  - 【记录】写下最终选定的各项默认值，以及选择理由

  > 完成：2026-08-30 · **四条验收本来就是 mock 验收，四条全过**
  > （`tests/tests_delta_logged_in.py` 的 [2] [4] [4b] [7] 段）。
  > 这是 C 组唯一能在没有真实抓取的情况下真正验收完的一项。
  > ⚠️ 但"参数写对了"不等于"参数管用"——它们的真实效果要等长期运行才知道。
  >
  > 【记录】最终默认值与理由（全部在 `config.toml` 的 `[delta]`，代码零硬编码）：
  >
  > | 参数 | 值 | 为什么是这个值 |
  > |---|---|---|
  > | `start_jitter_minutes` | 45 | 每天固定整点发请求是最易识别的模式之一。45 分钟足够把时刻打散，又不至于让"每天一次"漂到第二天 |
  > | `max_scrolls` | **2** | 首屏 + 两次翻页，对日更账号（FB 中位间隔 1.0 天）绰绰有余。计划建议 2–3，取下界 |
  > | `first_screen_seconds` | 6 | `goto` 返回时首屏接口响应通常还在路上 |
  > | `max_session_seconds` | 300 | 页面卡住时宁可放弃本次，也不要在计划任务里挂一个永不退出的进程 |
  > | `failure_budget` | 3 | 连错三天足以说明不是偶发。再多就是每天主动提醒对面注意我们 |
  > | `quiet_days_before_slowdown` | **FB 14 / IG 7** | 见下 |
  > | `slowdown_stale_after_hours` | 72 | 降频后三天一次。**降频是这条路上唯一能直接降低风险的旋钮** |
  > | `autostart_chrome` | true | 用户拍板。否则关一次浏览器就漏一天，且要靠人看通知才知道 |
  > | `keep_captures` | 7 | 留一周的原始响应够排查，又不会一年攒出几个 GB |
  >
  > **`quiet_days_before_slowdown` 按平台分开，这是本项唯一一处偏离计划的地方。**
  > 计划写的是一个数。实测两个账号节奏差一个量级：
  >
  > | | 最新一帖 | 距 2026-08-30 | 发帖间隔中位 | >5 天的间隔 |
  > |---|---|---|---|---|
  > | Facebook | 2026-08-25 | **4.4 天** | **1.00 天** | 1 次 / 46 篇 |
  > | Instagram | 2026-07-15 | 45.4 天 | 1.61 天 | 102 次 / 756 篇 |
  >
  > 一个阈值不可能同时适配这两种：对 FB 合适的值会让 IG 一直全速空跑，
  > 对 IG 合适的值会让 FB 一停更就被降频、然后漏帖。
  > 配置写成**内联表** `{ facebook = 14, instagram = 7 }`，
  > 是为了避开 TOML 子表的排序陷阱（`[translate.glossary]` 那条警告说的就是它）；
  > 写成一个数时两个平台通用，向后兼容。
  >
  > ⚠️ **顺带纠正一处文档里被反复引用的错误事实**：计划与交接文件里多处写着
  > "该账号实测一个多月没发新帖，天天全速跑没有收益"——**这句话只对 Instagram
  > 成立**。Facebook 是日更的，而且 4 天前还在发。
  > 增量对 FB 有真实价值，对 IG 基本是空跑，这也正是上面要分开定值的原因。
  >
  > **计划外新增的一条缓解措施**：`DeltaBlocked.hard`。登录墙 / 401 / 403 / 429
  > 属于"对面在拦我们"，这时**同一次运行不再去敲另一个平台**——两个平台共用
  > 同一个会话、同一份浏览器指纹，IG 刚被限流就转头敲 FB，正是"异常即停"
  > 要防的行为。解析不出来、页面没加载、超时属于我们自己这边的问题，
  > 只停这一个平台。没跑的那个平台**也记一次失败**，否则连续被拦时
  > 失败预算永远攒不满，自动运行就永远停不下来。
  >
  > **最后一条（`不与发布账号共用 profile`）代码管不了**，它是操作约束，
  > 已经写在 `MANUAL_STEPS.md` 第 2 步与 G1 的前置里。

## D. 完整性检查与告警（依赖 C）

> ⚠️ **方案 B 之下这一组更重要了。** 登出增量最坏只是抓不到；
> 登录态增量最坏是**会话失效后每天硬撞、直到账号被处理**。
> D3 的告警是这条路径上唯一能让"悄悄坏掉"变成"看得见地坏掉"的东西。
>
> ⚠️ **`[integrity].alert_after_quiet_days = 4` 必须重设，值已定，D3 落地时改。**
>
> 2026-08-30 按归档统计了真实发帖节奏（这就是本条原来说的"先统计一遍再定值"）：
>
> | | 最新一帖 | 距 2026-08-30 | 间隔中位 | 间隔 p90 | >7 天的间隔 |
> |---|---|---|---|---|---|
> | Facebook | 2026-08-25 | **4.4 天** | **1.00 天** | 2.10 天 | **0 次** / 46 篇 |
> | Instagram | 2026-07-15 | 45.4 天 | 1.61 天 | 5.89 天 | 54 次 / 756 篇 |
>
> **用户拍板：FB 7 天 / IG 21 天，按平台分开。** 依据：FB 历史上最长的正常间隔
> 是 5.9 天，7 天既能容下它又能在停更两天内报出来；IG 六年里 >14 天的正常间隔
> 出现过 19 次（约每年 3 次），阈值低于 21 天就会年年误报。
>
> ⚠️ **顺带纠正**：本节原文"该账号连续一个多月没发新帖"**只对 Instagram 成立**。
> Facebook 是日更的。这条错误事实在计划、交接文件里被引用了好几处，
> 它会让人以为"增量没什么用"——恰恰相反，FB 那半边很有用。
>
> 配置写法与 `[delta].quiet_days_before_slowdown` 一致，用内联表：
> `alert_after_quiet_days = { facebook = 7, instagram = 21 }`。

> 爬取相对 API 最本质的劣势是**没有 ground truth**：
> "滚到这里就没了"和"被限流截断了"在响应上长得一样。
> 这一组的目的是让静默失败变成可见失败。

- [x] **D1** 实现连续性检查 `core/integrity.py`

  - 函数 `check_continuity(rows: list[dict], gap_days: int) -> list[dict]`
    - 按 `created_at` 排序，找出相邻两帖间隔 > `gap_days` 的位置
    - 返回缺口列表：`[{"after": post_id, "before": post_id, "gap_days": 12.4}]`
  - 函数 `check_quiet(state: dict, platform: str, alert_after: int) -> bool`
    - 连续 N 天零新增即返回 True（目标账号日均约 1 帖，长期零新增本身是异常）
  - 函数 `check_incomplete(arc: Archive) -> list[dict]`
    - 直接返回 `arc.needs_media()`（媒体不全的轮播帖）
  - 参数从 `config.toml` 的 `[integrity]` 读，不写死
  - 【验收】单元测试 `tests_integrity.py`：
    - 构造有明显缺口的时间序列 → 能检出
    - 构造均匀序列 → 不误报
    - 构造 `media_complete=False` 的记录 → 进待补清单

  > 完成：2026-08-29 · `tests_integrity.py` 23 项断言全绿，三条验收均通过。
  >
  > 语义明确化（计划里没写死，实现时定的）：`after` 是**较早**那篇的 post_id、
  > `before` 是**较晚**那篇——缺口在 after 之后、before 之前。判定用**严格大于**
  > `gap_days`，正好等于阈值不算缺口（测试里有边界断言）。
  > 输入乱序也正确：manifest 是追加写的，顺序本来就不保证，函数内部先排序。
  >
  > **计划外新增 `check_undated(rows)`**：`created_at` 缺失或解析不了的记录
  > 无法参与连续性比较。如果只在 `check_continuity` 里默默跳过，它们就成了
  > 检查不到的盲区——而"检查不到"正是本模块要消灭的东西，所以单独汇报。
  > 这类记录真实存在：`parse.from_fb_story` 在时间戳不是数字时会原样透传字符串。
  >
  > `params()` 从 `[integrity]` 读阈值并返回 `(gap_flag_days, alert_after_quiet_days)`；
  > 三个检查函数本身收显式参数，便于测试，阈值来源只有这一处。
  > `check_quiet` 对 `state` 缺该平台、值为 None、值为布尔都返回 False 不崩
  > （布尔那条是因为 `isinstance(True, int)` 为真，不排除就会把 True 当 1 天）。
- [x] **D2** 实现 Windows 通知 `core/notify.py`

  - 函数 `notify(title: str, message: str) -> None`
  - Windows 实现：PowerShell toast
    ```powershell
    powershell -NoProfile -Command "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType=WindowsRuntime] | Out-Null; ..."
    ```

    若 toast 在该机器上不可用（旧版 Windows / 权限问题），
    **降级为 `msg` 命令或弹窗**，最终降级为写 `state/alerts.log`
  - **降级必须静默生效，不得因为通知失败而让主流程崩溃**
  - 【验收】`python -c "import sys; sys.path.insert(0,'.'); from core.notify import notify; notify('测试','这是一条测试通知')"`
    在桌面弹出通知，或至少在 `state/alerts.log` 里留下记录

  > 完成：2026-08-29 · `tests_notify.py` 15 项断言全绿。
  > 真实弹窗验收：`python -m core.notify` 走通了 **toast 通道**（PowerShell
  > 通知器返回 0），`state/alerts.log` 留下 `(toast) 测试 | 这是一条测试通知`。
  > ⚠️ 退出码 0 只说明通知器接受了请求；若开了专注助手 / 免打扰，
  > 桌面上可能仍看不到。**这一条需用户目视确认过才算完全落实。**
  >
  > 与计划的一处差异：**`state/alerts.log` 无条件先写，不是"前两级都失败才写"**。
  > 落盘是唯一不依赖桌面会话的通道——计划任务在无人登录时触发、或人根本不在
  > 电脑前时，toast 弹了也等于没弹，日志才是能回溯的那份。
  > 每行还记录了实际走的通道（`toast` / `msg` / `log-only`），
  > 用来排查"为什么我没看到弹窗"。**D3 因此不需要再自己写一遍 alerts.log**，
  > 直接调 `notify()` 即可。
  >
  > 实现细节：PowerShell 脚本用 `-EncodedCommand`（base64 的 UTF-16LE）传，
  > 彻底绕开引号转义——标题正文里带引号、换行、中文、emoji 都不会把命令行拆坏
  > （测试里都覆盖了）。借用 PowerShell 自己已注册的 AppUserModelID，
  > 免得为一条通知去注册开始菜单快捷方式。子进程 20 秒超时，
  > 日常任务里挂死比通知不到更糟。
  > `notify(title, message, popup=False)` 供自动化测试跳过弹窗，
  > 免得跑一次测试往桌面糊一串通知。
- [x] **D3** 把检查接入增量流程

  - 每次 `routes/delta.py` 运行结束后：
    1. 跑 D1 的三项检查
    2. 任一项触发 → 调 D2 通知，并写入 `state/alerts.log`
  - 告警文案要具体，包含平台、检查项、数值。反例："发现问题"；
    正例："instagram 连续 5 天零新增（阈值 4 天），可能已被登录墙拦截"
  - 【验收】人为把 `state/delta_state.json` 的 `consecutive_quiet_days` 改大 → 运行后触发通知

  > 完成：2026-08-30 · 实现为 `core/integrity.py::run_checks()` +
  > `routes/delta.py::run_integrity()`，接在每个平台 `record_success()` 之后、
  > 存盘之前（它要读刚算好的 `consecutive_quiet_days`，它自己的"报过了"标记
  > 也要一起落盘）。
  >
  > **⚠️ 计划里写的验收步骤做不出来，已更正。** 「把 `consecutive_quiet_days`
  > 改大」不会生效——那个字段每次成功都由 `last_new_at` 重算，手改的值当场被
  > 覆盖。**能真正触发的是把 `last_new_at` 往前推**。已在
  > `MANUAL_STEPS.md` 与离线测试里都改成这个做法（走真实的 `_run_due` 路径，
  > 只有浏览器是假的）。
  >
  > **这一组的价值全在"用户还会不会看它"，所以核心设计是「只报新出现的问题」**：
  >
  > | 检查 | 重复抑制 | 不这么做会怎样 |
  > |---|---|---|
  > | 连续性缺口 | 只看最近 60 天，且同一个缺口只报一次（记在 state 的 `alerts.gaps`） | 归档跨 6 年，IG 有上百个历史间隔，会每天重报一遍 |
  > | 媒体不全 / 无日期 | 只在**数量比上次多**时报；变少时静默更新 | 一个补不上的帖子会天天报到天荒地老 |
  > | 长期零新增 | 达阈值报一次，之后每 7 天最多再报一次 | 账号真停更时天天弹 |
  >
  > **误报的代价不是打扰，是让整条告警通道失效**——用户在第三天关掉通知之后，
  > 真正的故障就再也没人知道了。爬取路径没有 ground truth，这条通道是
  > "悄悄坏掉"唯一的出口。
  >
  > 告警走 `core.notify.notify()`，它无条件先写 `state/alerts.log`，
  > 所以 D3 不需要自己再写一遍日志（D2 已确立的职责划分）。
  > 一个平台合成**一条**通知，不是一项一条——四条 toast 连弹的结果是全被划掉。
  >
  > **阈值按平台配置**（`params(platform)`，内联表写法）。⚠️ **值在同一天改过
  > 一次**：第一版 FB 7 / IG 21 是按"IG 已停更一个多月"标的，而那个认知源于
  > 合作帖被误丢（CR-19）。修完重新统计，**两个账号都是日更**
  > （近一年间隔中位 FB 1.00 天、IG 0.99 天），IG 因此从 21 收到 **10**：
  > 21 天意味着真坏了要三周才报出来，而近一年 >10 天的正常间隔只出现过 1 次。
  > 最终值 `{ facebook = 7, instagram = 10 }`，缺口阈值 `{ facebook = 5, instagram = 6 }`。

- [x] **D4**（计划外新增，2026-08-30 晚）**第四项检查：丢弃了已知合作方的帖子**

  - `core/integrity.py::known_partners(rows, account)` —— 从归档推出合作方名单
    （取"合作帖的 owner" ∪ "自家帖的 coauthors"，实测 IG **210 个**）
  - `core/integrity.py::check_dropped_partners(rejected, partners)` ——
    被丢弃的节点里作者是合作方的那些
  - 接进 `routes/delta.py`（`ScanResult.suspect`，`--dry-run` 也打，
    正式跑并进 D3 那条平台级通知）、`routes/backfill.py`、`tools/replay.py`
  - 【验收】离线用真实 capture 双向验证：正常不响、把判定退回旧实现要响

  > 完成：2026-08-30 · **为什么这一项该存在**：CR-19 的根因修好了，但
  > **让它拖那么久才被发现的原因没修**——丢弃完全没有声音，程序丢掉 263 篇，
  > `_rejected.jsonl` 只写不读，输出里一个字都没有。合作机制以后还会变，
  > 修好的是这一次，这一项盯的是下一次。
  >
  > **零误报**：210 个合作方 vs 历史上被丢弃过的 6 个账号，交集为空。
  > 推荐位来自完全陌生的账号，合作方的帖子本来就该留下，两者天然不重叠。
  >
  > **灵敏度**（退回只比 owner 的旧实现）：IG 回填报 263、增量报 35；
  > **归档为空的冷启动**也能报（34 / 29）——名单同时取自本次留下的那批，
  > 本账号自己发的帖子里就记着合作方。
  >
  > ⚠️ **它不做重复抑制**，与上面三项刻意不同：那三项会天天成立
  > （账号真停更时"零新增"每天都真），而这一条在全部真实数据上从未成立过。
  > 这种信号漏报的代价远大于重复提醒。
  >
  > ⚠️ **它是提示不是判定**：响了要去 `_rejected.jsonl` 与 `_capture_*.json`
  > 离线查那几篇为什么没带 coauthor 信息，**不是自动收编**。
  > "宁可漏一篇自家的，不可混进一篇别人的"没有变。

---

## E. 定时调度（依赖 C6、D3）

> 目标机是**笔记本**，会合盖睡眠。固定时刻的 cron 会静默跳过，
> 因此必须有"补跑"机制。
>
> ### ⚠️ 方案 B 给这一组加了三个新前提
>
> 增量改走登录态之后，定时任务不再是"跑个纯 HTTP 脚本"那么简单：
>
> 1. **它需要专用 Chrome 处于运行状态且会话有效。** 任务必须先确认
>    （`core.chrome.cdp_ready(port)`），没起就拉起（复用 `tools/start_chrome.py`）。
>    ❌ **不得因为 Chrome 没开就静默跳过**——那会让"每天都在跑"
>    变成"一年没跑过"而无人察觉。
> 2. **会话会过期，而过期只能由人修。** 一旦检测到登录墙，任务应当
>    **停止自动重试**并通过 `core.notify` 告诉用户去重新登录（见 C7 的失败预算）。
>    每天硬撞一个已失效的会话是最坏的行为。
> 3. **触发时刻必须随机化**（C7）。`schtasks` 只能定固定时刻，
>    所以抖动要在 Python 侧做：入口先随机 sleep 0..N 分钟再干活。
>    ⚠️ 这与 `--if-stale` 的先后顺序要想清楚——**先判 stale 再抖动**，
>    否则每次唤醒都要先睡半小时才发现"其实不用跑"。

- [x] **E1** 编写 `scripts\run_delta.bat`

  - 内容：激活 venv → `cd` 到项目目录 → `python -m routes.delta` → 记录退出码
  - 输出**追加**到 `state/delta.log`（含时间戳），不要覆盖
  - ⚠️ **必须是纯 ASCII + CRLF**（禁止事项 7），中文提示放 Python 里
  - ⚠️ **必须设 `set "PYTHONIOENCODING=utf-8"`**，和另外四个 `.bat` 一样。
    输出重定向进 `state/delta.log` 时，本机 cp936 编不出 `⚠ ❗`，
    第一次打印就会把整个进程带走——**崩在网络请求已发出、归档尚未写入之际**。
    这不是假设，是 2026-08-30 实测过的故障（见 `core/console.py`）
  - 【验收】双击能完整跑完，日志有新增行；**日志里的 `⚠` 等符号正常显示**

  > 完成：2026-08-30 · 纯 ASCII + CRLF + `PYTHONIOENCODING=utf-8`，
  > 输出 `>>` 追加进 `state\delta.log`。
  >
  > **编码验收已通过**：把 `-m routes.delta --status` 重定向进文件，
  > 产出 266 字节**合法 UTF-8**（含 `·` 与中文），能原样解回来。
  > 这正是本机 cp936 下会崩的那条路径。
  >
  > 三处实现上的决定：
  >
  > 1. **运行分隔线由 Python 打，不由 `.bat` 的 `echo %DATE%` 打。**
  >    cmd 按控制台代码页（936）写，会在一份 UTF-8 日志里插进 GBK 字节，
  >    整份日志就不再是任何单一编码。现在每次运行开头是
  >    `===== 2026-08-30T11:00:23Z · --status =====`。
  > 2. **只有不带参数时才 `pause`**（即双击进来的情况）。每日任务显式带
  >    `--platform all`，补跑任务带 `--if-stale`，都不会卡在 pause 上等到超时。
  > 3. 双击时跑完用 PowerShell `Get-Content -Encoding UTF8 -Tail 40` 回显日志尾巴
  >    ——否则黑窗口一闪而过，人什么都看不到。
  >
  > **`.bat` 的字节级约定现在有测试守了**（`tests/tests_schedule.py` 第 [6] 段）：
  > 五个 `.bat` 全部断言纯 ASCII、无裸 LF、无 BOM。
  > 计划原文写着"`.gitattributes` 只保证换行，ASCII 得靠人守"——
  > **靠人守的东西迟早会破**，2026-08-29 的目录重组里就破过一次。
- [x] **E2** 实现补跑判定

  - 在 `routes/delta.py` 入口加 `--if-stale` 参数：
    - 读 `state/delta_state.json` 的 `last_success`
    - 距今 < `config.toml` 的 `[delta].stale_after_hours` → 直接退出（退出码 0）
    - 否则正常执行
  - 【验收】连续跑两次 `--if-stale`，第二次应立即退出并打印"距上次成功不足 N 小时，跳过"

  > 完成：2026-08-30 · 随 C6 一起实现（`stale_enough()` + `effective_stale_hours()`）。
  > 实际打印是 `[i] facebook 跳过：距上次成功 1.0 小时，不足 26 小时`——
  > 带上具体数字，因为"跳过"本身不足以让人判断是对是错。
  >
  > **顺序是先判 stale、再抖动**（E 组头部要求的），离线测试有专门一条钉住它：
  > 不到期时 `time.sleep` 一次都不该被调用。反过来的话，每次唤醒都要先睡
  > 半小时才发现"其实不用跑"。
  >
  > 降频时自动改用 `slowdown_stale_after_hours`（C7 的「频率可降级」），
  > 判定同样按平台取阈值。
- [ ] **E3** 注册 Windows 计划任务

  - 用 `schtasks` 创建任务，**三个触发器**：
    1. 每天固定时刻（时刻从 `config.toml` 读，或直接写在任务里）
    2. 用户登录时
    3. 系统唤醒/解锁时
  - 后两个触发器统一调用 `scripts\run_delta.bat --if-stale`，靠 E2 去重
  - 建议同时勾选"仅在使用交流电时运行"→ **取消**（笔记本常在电池上）
  - ⚠️ 任务需要**专用 Chrome 在跑**（方案 B）。两种做法二选一并记录：
    a) 任务先调 `tools/start_chrome.py` 再调 delta；
    b) 把 start_chrome 挂到"用户登录时"触发器上，delta 只负责确认端口就绪
  - ⚠️ **不要勾"不管用户是否登录都运行"**：那样拉起的 Chrome 没有桌面会话，
    CDP 附着行为不确定。这条路径本来就依赖一个真实的、有人在用的浏览器
  - 参考命令（实际以 Windows 版本为准，允许改用任务计划程序 GUI 导入 XML）：
    ```
    schtasks /Create /TN "FBScraperDelta" /TR "C:\path\to\scripts\run_delta.bat" /SC DAILY /ST 09:00 /F
    ```
  - 【验收】
    - `schtasks /Query /TN "FBScraperDelta"` 能查到
    - 手工 `schtasks /Run /TN "FBScraperDelta"` 能触发并在日志留痕
    - 合盖 → 唤醒 → 确认补跑触发（或至少确认触发器已注册）
  - 【记录】写下实际用的触发器组合，以及电源策略是否影响

  > 进行中：2026-08-30 · **工具写完并通过离线验收，但没有真的注册——这是刻意的。**
  >
  > ⚠️ **2026-08-31 更新：那个『刻意不注册』的理由已经消失。**
  > 当时不装是因为『合作帖修复后还没实机跑过，装一个每天准时失败的东西
  > 比不装更糟』。第 8 步真实验收当天已经通过（C2/C4/C5 全部勾选），
  > **现在装是安全的**。剩下的只是用户跑一次 `MANUAL_STEPS.md` 第 9 步。
  >
  > 实现为 `tools/schedule.py`（`xml` / `install` / `status` / `remove`，
  > `install` 支持 `--dry-run`）。当前 48 项离线断言在 `tests/tests_schedule.py`。
  >
  > **注册成两个任务而不是一个**，因为 Task Scheduler 一个任务只能有一个
  > Action，而三个触发器需要两种参数：
  >
  > | 任务名 | 触发器 | 参数 |
  > |---|---|---|
  > | `FBScraperDelta` | 每天 `[delta].daily_time`（默认 09:30） | `--platform all` |
  > | `FBScraperDeltaCatchup` | 用户登录时（延迟 3 分）+ **解锁时**（延迟 2 分） | `--if-stale` |
  >
  > ⚠️ **每日那个不能带 `--if-stale`**：`stale_after_hours = 26` 而每天同一
  > 时刻的间隔是 24 小时，带上就变成"跑一天、跳一天"。计划原文写的
  > "后两个触发器统一调用 `--if-stale`"是对的，我把它落成了硬约束并写了断言。
  > 每日任务仍显式传 `--platform all`：它不改变 stale 语义，只是避免批处理把
  > “无参数”识别成人工双击并在末尾 `pause`。
  >
  > **走 XML 导入而不是拼 `schtasks` 参数**：`/SC ONLOGON` 有，但
  > **"解锁时触发"（`SessionStateChangeTrigger`）命令行表达不出来**。
  > XML 写成 UTF-16 LE + BOM 存进 `state\`（已 gitignore）。
  >
  > 【记录】关键设置与理由（都有断言）：
  > `DisallowStartIfOnBatteries=false`（笔记本常在电池上，勾着等于大半时间不工作）、
  > `StopIfGoingOnBatteries=false`、`StartWhenAvailable=true`（合盖错过的醒来后补跑）、
  > **`WakeToRun=false`（绝不把机器叫醒来抓取）**、
  > `LogonType=InteractiveToken`（不是 Password/S4U —— 那样拉起的 Chrome 没有
  > 桌面会话，CDP 附着行为不确定）、`MultipleInstancesPolicy=IgnoreNew`、
  > `ExecutionTimeLimit=PT2H`（抖动最多 45 分钟 + 抓取，卡住也不会永远挂着）。
  >
  > **⚠️ 为什么不直接替用户注册**：`schtasks /Create` 一跑，**从那一刻起
  > 每天就会真的去访问一次 Facebook 和 Instagram**——方案 B 的累积敞口从此开始计。
  > 而此刻 C2/C4/C5 的真实验收还没过（合作帖修复后没有再实机跑过一次）。
  > 装上一个每天准时失败的东西，比不装更糟。
  > **验收步骤留给用户**（`MANUAL_STEPS.md` 第 9 步），一条命令的事。

---

## F. 德语翻译（依赖 B6，可与 C/D/E 并行）

> 当前详细操作与验收真相见 `docs/TRANSLATION_PLAN.md`。
> 真实归档：Facebook 46 篇 / 45 条正文，Instagram 1019 篇 / 1010 条正文，
> 共 **1055 条待译正文**。

### F1 · DeepSeek 翻译主流程

- [x] **代码实现完成**
  - 使用 DeepSeek 官方 OpenAI 兼容端点 `https://api.deepseek.com/chat/completions`，
    模型 `deepseek-v4-pro`，不再经过 Anthropic 协议层。
  - 密钥从 `DEEPSEEK_API_KEY` 或项目 `.env` 读取，`.env.example` 提供模板。
  - thinking 显式开启，默认 `reasoning_effort=high`；不发送 `max_tokens`、
    `max_completion_tokens`、`temperature` 或 `top_p`。
  - `--check` 与正式翻译共用请求形态；核对响应 model 与 reasoning 证据，
    阻止参数被忽略或未知模型名静默回退 Flash。
  - `--estimate` 离线估输入与可见译文；High thinking 的 reasoning token 不可离线可靠
    预测，因此只显示未含 reasoning 的基础费用参考，不再冒充总价上界。
  - 只翻非空正文，结果独立追加到 `translated.jsonl`，不改 `manifest.jsonl`。
  - 记录 `post_id/source_text_sha256/text_de/translated_at/model/prompt_version/usage`；
    只有帖子 ID、实际送入模型的正文指纹与当前提示词版本同时匹配才算完成。
  - 支持
    `--limit`、`--account`、`--force`、`--dry-run` 与断点续跑。
  - 付费批次单实例锁；JSONL 追加补齐坏尾换行并 `flush + fsync`；读取按行独立
    解码，截断的 UTF-8 坏行不会遮住后续有效付费结果。
  - API 共享错误首条熔断；单条内容错误保留断点；空输出、截断、拒绝显式失败。
  - 金额必须逐字符原样保留；任何遗漏、新增、换算或格式改写均拒绝写盘。
  - 标签必须逐个原样照搬；数量、内容、大小写或顺序不一致均拒绝写盘。
- [ ] **真实业务验收待执行**
  - Key 已在 `.env`；新版官方接口的 `--check` 必须重新通过。
  - Facebook 与 Instagram 各试译 3 条并由懂德语的人确认，不能只全局跑 3 条。
  - 旧 Anthropic 路径的 3+3 已保留在 `translated.jsonl`，但提示词版本 4 已自动过期；
    人工确认新版 3+3 后才跑 1055 条全量。

### F2 · 品牌风格与提示词

- [x] **代码与初版配置完成**
  - 独立模板 `prompts/translate_de.md`，`PROMPT_VERSION=5`。
  - 品牌决策固定为 `du`、中性性别表达、适度保留英语借词、简洁直接；
    标签原帖有几个就逐个照搬几个，不设数量上限。
  - 13 条高频术语基于真实语料并按 Neakasa 德国站用语收敛。
  - 每账号从长度中位附近选稳定风格参照，只取目标账号自己的正文，
    不把第三方合作方文案提升进 system prompt。
  - 外部正文按不可信数据封装，无法闭合标签、执行提示注入或污染模板占位符。
  - 保持换行、emoji、品牌名、型号、@提及和 URL；不加码情绪。
  - **货币金额不做德国格式本地化，也不换算币种或价格。**
    数字尺码不换算；英制物理量可换算但进入人工核对。
- [ ] **德语质量验收待执行**
  - 需要业务人员检查两个账号各 3 条真实结果，确认术语、称呼、自然度与德国市场口吻。
  - 若改模板，必须增加 `PROMPT_VERSION`；普通运行会自动重译旧版本，
    仅同版本强制重试时使用 `--force`。

### F3 · 人工审校清单

- [x] **代码实现完成**
  - `--review` 为每个账号生成 `review.md`：原文、译文、原帖、配图、
    合作帖原作者、媒体残缺提示、数字警示和人工勾选框。
  - 同步派生每帖 `text_de.txt`；正文变化使译文过期时，旧派生副本会移除，
    `translated.jsonl` 保持唯一译文真相源且保留历史付费记录。
  - 重建前把上一版移动为 `review.previous.md`，避免人工批注静默覆盖。
  - 只收当前 manifest 中正文指纹和提示词版本一致的有效译文，报告缺失、过期与孤儿行；
    Markdown 围栏不受外部正文注入。
- [ ] **真实预览验收待执行**
  - 全量翻译后在两个账号的 Markdown 预览中确认图片可见，并交给德语审校人。
  - 直接编辑 `review.md` 不会回写真相源；审校回写及发布仍属后续 G 组边界。

### F 组离线证据

- `tests/tests_translate.py` 全绿。
- 对真实归档执行 `--dry-run --account in_neakasa.tech --limit 3`，确认零 API 调用。
- 对两个真实账号执行 `--show-prompt`，均成功渲染。
- 对 1055 条真实正文执行 `--estimate`，确认离线预算路径。
- 新版真实试译与人工德语判断完成前，不把 F 组业务验收标成完成。

---

## K. 图内英文德语化（GPT-Image-2，依赖 F）

> **详细任务书见 `docs/IMAGE_PLAN.md`**，本节只放任务项与验收。
> 本组取代第 0 节原本「本期不做」里的那条「图内英文文字的德语替换」，
> 是 2026-08-31 的范围变更，不是新增。

> ⛔ **动手前必读 `IMAGE_PLAN.md` 第 2 节的三条硬事实。** 它们每一条都会让请求直接失败：
> 1. **`input_fidelity` 必须不发送**——gpt-image-2 强制高保真、传了返回 400。
>    ⚠️ 本机 `openai` 3.6.0 的 `images.edit()` 签名里**有**这个参数，
>    docstring 还写着「1.5 及之后的模型支持」。**那句话对 gpt-image-2 是错的。**
> 2. **`quality` 必须显式给 low/medium/high**，用 `auto` 会让计费不可预测。
>    **用户 2026-08-31 拍板 `high`**（我建议过 medium，用户选了 high；
>    业务取舍，代码不替用户改）。
> 3. **不得用原图宽高直接当 `size`**：边长必须是 16 的倍数，
>    而归档里最常见的两档（1080×1080 共 318 张、1080×1350 共 100 张）都不是。
>    另有 720×720 那档总像素低于下限，必须放大。

> **开发分支：`feat/image-de`。** 与 G 组（`feat/business-suite-publish`）并行开发，
> **文件所有权表在 `IMAGE_PLAN.md` 第 10 节**——动别人的文件之前先看那张表。
> 本节（K 组）**只由 `feat/image-de` 编辑**，不要顺手改 G 组那一节。

- [x] **K0** 依赖与配置骨架　（三项已预置，只剩配置审计）

  - ✅ **Pillow 已进 `requirements.txt` 并装进 `.venv`**（实测 12.3.0）。**只有它，没有 numpy**。
    > 违反了"不引入新依赖"的全局约定，理由记在这里：产出图必须验证
    > 「能解码 / 尺寸对 / 不是占位图 / 与原图结构相近」，没有图像库做不了。
    > 结构相似度用纯 Python 的 dHash 汉明距离即可，不值得为它多一个依赖。
    > **提前落进主干，是为了让两条并行分支不必同时改 `requirements.txt`**
    > （G 组的 `compose.py` 也要读图）。
  - ✅ `config.toml` 的 `[image]` 与 `[image.keep_verbatim]` 已写好；
    `.env.example` 已加 `IMAGE_API_KEY`；**用户已把真实密钥填进 `.env`**（已核对可读）。
  - 【验收】**`[image]` 段配置项审计通过**——
    既没有"配置里有、代码不读"的死旋钮，也没有"代码读、配置没写"的静默默认值
    （CR-40 那轮立的规矩）
  > 完成：2026-08-31 · `Settings` 对 `[image]`、费率内联表与
  > `[image.keep_verbatim]` 做双向精确键审计，缺键/多键均在联网前失败。
  > 为落实“不补 818 张历史”，自适应新增并实际消费 `incremental_since`：默认只跟
  > 2026-08-31 起的增量；K9 用显式 `--latest-posts 3`，全历史另有双重费用闸。
- [ ] **K1** `Settings` 与 `--check` 自检

  - 照 `translate.py::Settings` 写，`validate()` 在任何网络请求前给出可操作错误
  - `--check` 发一个最小真实 edits 请求，并断言：网关可达 / 鉴权通过 /
    **响应 model 确实是 `gpt-image-2`（没被静默降级成 `gpt-image-2-free`）** /
    `data[0].b64_json` 能解码成合法图片 / `usage` 四个字段齐全
  - ⚠️ **`--check` 会真的花钱**，输出里必须说清楚（与 F 组口径一致）
  - 【验收】真实 Key 下通过并打印实际 model 与 usage；缺 Key 时给出复制
    `.env.example` 的完整命令且**不联网**
  > 实现：2026-08-31 · 请求体固定为 `model/prompt/image/n/size/quality/output_format`
  > 七项，明确不发送 `input_fidelity`；正式 `quality=high`，响应 model、base64 图片及
  > 四层 usage 缺一即失败，且共享响应契约错误在第一张后熔断整批，避免逐张付费丢弃。
  > 假 SDK 与缺 Key 路径均已通过；**真实 `--check` 尚未执行**，
  > 因它会产生费用，须先向用户报量级并取得单独确认，故本项不勾选。
- [x] **K2** `legal_size()` 尺寸合法化

  - 各边取到 16 的倍数 → 边长夹到 ≤3840 → 总像素低于 655,360 则整体放大过线 →
    高于 8,294,400 则缩小 → 宽高比 >3:1 显式失败（**不裁剪、不填充**，那是改画面）
  - 【验收】用 `IMAGE_PLAN.md` 第 2 节那张实测分布表逐行断言
    （1080×1080→1088×1088、1080×1350→1088×1360、**720×720→816×816**、
    1440×1440 原样），再加模糊测试：任意 (w,h) 的输出必满足四条约束或显式抛错
  > 完成：2026-08-31 · 任务书实测尺寸逐项吻合；另跑 2,000 组确定性模糊输入，
  > 合法输出全部满足 16 倍数、像素上下限、边长与 3:1 四条约束，超 3:1 明确拒绝，
  > 不裁剪、不填充；同时记录宽高比形变百分比供 K8 告警。
- [x] ~~**K3** 预扫描：这张图有没有英文、有哪些~~　⛔ **已取消**

  > 取消：2026-08-31 · **用户决定不加这一步**，直接用 gpt-image-2 自带的视觉能力。
  > 设计保留在 `IMAGE_PLAN.md` 的 K3 一节（未删除），**不要重新讨论**。
  >
  > **两个后果必须记住，免得以后当成 bug 去查**：
  > 1. **没有"这张图有没有文字"的闸**，所有配图一律送进 edits，
  >    无文字的产品图也会被付费处理一次。当前范围（不补发历史）下可接受；
  >    **若哪天决定补发 818 张，应当先把 K3 加回来**。
  > 2. **"德语文字正确"这条验收完全落到人工**——机器不再知道原图上有哪些英文，
  >    无法自动回答"有没有漏改"。**K8 因此从"锦上添花"变成 K 组能否验收的唯一依据。**
  >
  > 勾选为已处理是因为"决定不做"也是一个已完成的决定；它不是一项已实现的功能。
- [x] **K4** 提示词 `prompts/image_de.md`

  - 与 `translate_de.md` 同构：中文指令 + `{{占位符}}` + `IMAGE_PROMPT_VERSION`
  - 占位符：`{{TEXT_DE}}`（该帖德语译文）/ `{{GLOSSARY}}`（**复用
    `[translate.glossary]`，不得另写一份**，否则图里和正文里出现两种说法）/
    `{{KEEP_VERBATIM}}`（由 `[image.keep_verbatim]` 渲染）
  - ⚠️ **没有 `{{SCAN_ITEMS}}`**——K3 已取消，模型自己找图上有哪些英文。
    这意味着提示词要自己承担"找哪些字要改"的职责，
    第 3.1 节那份清单在提示词里的分量因此更重，**要写得能被模型当规则用**
  - ⚠️ 外部正文按不可信数据封装，与 translate 一致
  - 【验收】`--show-prompt` 对任意真实帖渲染成功，零 API 调用
  > 完成：2026-08-31 · 仅支持 `TEXT_DE/GLOSSARY/KEEP_VERBATIM` 三个占位符，
  > 未知或缺失占位符在 API 前失败，K3/`SCAN_ITEMS` 不存在；外部译文用 JSON +
  > untrusted 标签并最后注入。已对真实归档 `in_neakasa.tech` 的帖子
  > `2390713492816847396` 完整渲染，零 API、零费用。真实最新帖预演又发现预置表遗漏
  > `PH5RIKO`、`Riko/RIKO`、`P1 Pro/NeakasaP1Pro`、`IFA2026`，已据归档命中补表，
  > 并把“任何识别出的优惠码都保留、已知表不是穷举”写成规则；提示词版本升到 2。
- [x] **K5** 产出硬校验（写盘前，任一不过即失败并保留断点）

  1. base64 能解码、字节数 > 0
  2. Pillow 能打开，**尺寸等于 `size_requested`**
  3. 不是纯色 / 接近全黑全白（抓"模型返回了占位图"）
  4. **结构相似度闸**：原图与产出图的 dHash 汉明距离 ≤ 阈值。
     这一条抓的是本组最严重的失败模式——模型不是改了几个字，而是**整张重画了**。
     用户那条验收标准「产品外观未被改动」在机器侧就落在这里
  - ⚠️ **阈值现在不定。** 项目已立规矩：阈值在真实数据上标定，不许拍脑袋
    （`[integrity]` 那几个值改过两次）。第一批 20 张跑完后按真实距离分布定，
    **标定完成前配 `-1`（关闭），只记录不拦截**
  - 【验收】构造一张"整张重画"的假产出，闸能拦下来
  > 完成：2026-08-31 · base64、Pillow 格式/尺寸、纯色/近黑白与纯 Pillow dHash
  > 四层硬闸均在写盘前；构造横向重画图在阈值 `0` 时被拦。真实阈值仍严格保持
  > `-1`（只记录），等待首批真实距离与人眼判定，未擅自拍数。
- [x] **K6** 落盘与幂等

  - 产出写 `posts/<帖子>/media_de/<两位序号><扩展名>`
  - ⛔ **目标文件已存在且 `images_de.jsonl` 里没有对应记录 → 跳过并告警。**
    那是设计同事手工修的版本，**比模型那张对，不得覆盖**
  - "已完成"的判据是四者全等：`post_id + media_index` + **原图字节指纹** +
    **参照译文指纹** + `IMAGE_PROMPT_VERSION`
    > 原图指纹必须算进去：归档支持"残缺→补全"升级，同一 post_id 的 `01.jpg`
    > 是可能被换掉的。只认 post_id 会让旧德语图配到新原图上——
    > 和 F 组"旧德文配新英文"是同一个 bug，那边已经踩过
  - 单实例锁 `state/images.lock`；`append_jsonl` 沿用 translate 那份写法
  - 【验收】连跑两次，第二次全部跳过、零 API 调用、零写盘
  > 完成：2026-08-31 · 真相源逐行容错、坏尾补换行并 `flush+fsync`；人工同序号
  > 文件优先。除任务书四项指纹外，增加 `output_sha256` 绑定程序所有权：先 fsync
  > 临时图与记录、再 `os.replace`，模拟“记录已落盘但 replace 失败”后普通重跑可恢复，
  > 不会误认成人工图；JSONL fsync 后、replace 前还会最后复查一次人工同序号文件。
  > 两次幂等、原图/译文变更精准失效、人工竞争窗口及锁冲突测试均通过。
- [ ] **K7** `--estimate` 与费用汇总

  - ⚠️ **必须按真实 usage 外推（中位数，只采当前 prompt_version 的样本）**，
    不许按"图片数 × 猜的单价"。CR-40 的教训：F 组按字符估给出 US$1.08–5.41，
    真实是 **US$23.73**，**低估一个数量级**
  - 一张都没跑过时明确说"还没有真实样本，只是量级参考"，不冒充上界
  - 【验收】跑完第一批后外推值与实际账单同量级
  > 实现：2026-08-31 · 只采当前提示词版本、输出文件与记录哈希一致且 usage 完整的
  > 样本，按真实 token 明细费率和单张中位数外推；无样本时只报量级参考，并完整打印
  > dHash 的 `values/min/median/max`。首批真实账单尚未产生，故本项不勾选。
- [ ] **K8** 接入审校清单　⬆️ **K3 取消后优先级提高，它是 K9 的唯一依据**

  - `review.md` 增加**原图与德语图并排**一节 + 逐条人工勾选框
    （英文项由审校人自己看图列出，**不再有 K3 扫描结果可用**）
  - 勾选项要覆盖 `IMAGE_PLAN.md` 第 3.1 节每一类：优惠码 / 品牌 / 型号 /
    合作方水印 / 金额 / 数值单位 / 认证标识——**一类一个框**，
    比一个笼统的"检查过了"有用得多
  - ⚠️ 这是**唯一**能执行"德语文字正确"这条验收的地方，机器读不出德语对不对
  - 【验收】至少 3 篇真实帖并排预览，Markdown 里图片能显示
  > 实现：2026-08-31 · `translate.py --review` 已按每张图生成原图/德语图并排表、
  > 程序/人工来源、size/dHash/形变信息，并为七类不可改内容、德语正确性和产品外观
  > 分别列框；人工同序号图总是先于当前程序记录展示。三篇物理图片的离线集成测试
  > 确认六个 Markdown 引用可显示。
  > 尚无真实 GPT-Image-2 产出，不能冒充“3 篇真实帖”验收，故本项不勾选。
- [ ] **K9** 端到端真实验收

  - 对**最新 3 篇**帖子的全部配图跑完整流程
  - 【验收】懂德语的人确认：文字正确、`IMAGE_PLAN.md` 第 3.1 节那些项
    （优惠码 / 品牌 / 型号 / 合作方水印 / 金额 / 数值单位 / 认证标识）
    **一个都没被动过**、产品外观没变。**这条不通过不许勾选 K 组**
  - 【记录】单张耗时、单张费用、dhash 距离分布、失败率
  > 待验收：2026-08-31 · 对真实归档只读预演后，当前最新三篇实际为
  > `3975547640610092585`（1 图）、`3973112941198515773`（0 图）、
  > `3973012230169803390`（5 图），合计 **6 图**，不是计划时的约 10 图；按任务书
  > high 上游参考约 **US$1.31**，Inferera 加价未知。三篇均没有当前提示词版本的
  > `text_de`，因此 K 入口正确排队 0 图，必须先补 F 组译文。现有 F 入口的 `pending`
  > 按时间正序排列，实测 `--limit 2/3` 都会选择最老帖子，不能精确补最新帖。中间那篇
  > 没有图片，不创建 K 任务，因此实际只需补有图的 **两篇**，无需为零图片帖多花一次；
  > 按当前提示词版本 3 个真实 usage 样本离线外推，两篇正文保守约 **US$0.038**。
  > 本 K 分支遵守文件所有权，没有改 F 主流程，也不得为此误跑约 1017 篇待译正文。
  > 需用户授权 F 增加精确选帖能力，或提供这两篇已审校的 `text_de`。译文就绪后仍须先
  > 取得图片费用确认，再由懂德语的人逐图勾完 K8；这些步骤均未发生，不勾选。
  >
  > **进展：2026-08-31（同步审查轮）· 上面那个卡点已解除，K9 现在只差费用确认。**
  > 用户授权后给 `translate.py` 加了作用域参数（CR-47，见下面 K10），
  > 并**真实翻译了点名的三篇**（成功 3 篇 / 失败 0 篇，
  > 实际费用上界 **US$0.1799** —— 比离线外推的 US$0.038 高，
  > 因为 `reasoning_effort=high` 在这三篇长正文上产生了 44 327 reasoning tok；
  > **记下这个差值：离线外推仍然会低估 thinking 主导的账单**，与 CR-40 同型）。
  > 三篇的当前版本译文现已在真实归档里，金额与标签硬校验均通过，
  > 其中两篇被标记"含金额/尺码/英制单位需人工确认"（正常 flag，不是失败）。
  >
  > 复核后的真实工作集（只读预演，零 API）：
  >
  > | post_id | 账号 | 图片数 | 合法化 |
  > |---|---|---:|---|
  > | `122123185335379375` | FB | 5 | 1080×1080 → 1088×1088 |
  > | `3973012230169803390` | IG | 5 | 1080×1080 → 1088×1088 |
  > | `3975547640610092585` | IG | 1 | 1350×1687 → 1344×1680 |
  > | **合计** | | **11** | 全部零形变或 ≤0.03% |
  >
  > ⚠️ **K9 必须用 `--post-id`，不能用 `--latest-posts 3`。** 实测原因：
  > 真实归档里第 3 新的帖子是 `3973112941198515773`（纯视频、0 图），
  > 它占掉一个名额，把 FB 那篇挤到第 4 位（两篇 `created_at` 只差 4 秒），
  > 于是 `--latest-posts 3` 只排到 6 张、**完全漏掉 FB**。
  > 而 `MAX_LATEST_POSTS = 3` 的费用闸又不允许写 4。
  > **`--post-id` 三次是唯一能覆盖计划点名那三篇的命令**，已写进 `MANUAL_STEPS.md`。
  >
  > 仍未做（因此仍不勾选）：真实 GPT-Image-2 调用需用户按 **11 张 × high
  > ≈ US$2.4（上游参考，Inferera 加价未知）** 确认费用；之后才有 dHash 分布可标定、
  > 才有图给懂德语的人按 K8 逐类勾选。
- [x] **K10** 第三轮同步审查修复（CR-46 ~ CR-59 中属于 K 组的九条）
  > 完成：2026-08-31 · 只改本组所有权内的文件。逐条：
  > **CR-47** `translate.py` 新增 `--post-id`（可重复）/ `--latest-posts N`
  > 与 `resolve_scope()`；`pending()` 接受 scope，**作用域过滤放在数据契约校验之后**，
  > 不做绕过 `SourceDataError` 的后门。默认行为完全不变（不传参数时 scope 为 None，
  > 有断言钉住）。
  > **CR-51** `[image.keep_verbatim].models` 补回被顺手删掉的 `"S1 Pro"`（语料 10 篇），
  > 并补 `S1Pro`(10) / `P1Pro`(3) 两种连写形态；六个型号串各有断言，
  > 配置里加了"只能加不能减"的警告。
  > **CR-52** 单张素材问题（缺文件 / 坏字节 / 超 3:1）只跳过这一张并点名报出，
  > 计入新的 `RunStats.skipped_bad_source`，不再掀掉整账号；
  > `--estimate` / `--show-prompt` 也套上了异常处理，不再抛裸 traceback。
  > **CR-53** 致命集合从"整个 `openai.APIError`"收窄到
  > 鉴权/权限/端点不存在/模型不匹配/响应契约破裂；瞬时错误与单图 400 计为单张失败，
  > 由新的 `[image].failure_budget = 3` 按**连续**失败兜住。
  > **CR-54** 新增 `scale_factor()` 与 `[image].scale_warn_factor = 1.25`：
  > 等比放大的宽高比形变是 0，所以旧的形变告警抓不到"小图被放大"。
  > **CR-55** `decode_image_payload` 先剥空白与可选 `data:image/...;base64,` 前缀
  > 再 `validate=True` 解码（折行是传输格式；这一步失败时钱已经花掉了）。
  > **CR-56** `translate.py --review` 不再因 `[image]` 配置不合法而整条命令失败。
  > **CR-57** 完成判据加上"产出路径必须就是这次要写的那个"，
  > 顺带消除改 `output_format` 后 `media_de/` 出现两个文件、撞上 G 组多候选闸的路径。
  > **CR-50** 新增 `readonly_archive()`：先判目录存在再构造 `Archive`，
  > 离线命令那句"零写盘"不再靠 `Archive.__init__` 里的 mkdir 兜底。
  > **【验收】** 全部 `tests/tests_*.py` **17 套全绿**；
  > `tests_localize_images.py` 新增 26 项、`tests_translate.py` 新增 10 项断言，
  > 全部指向"这条修复不许被顺手改回去"。`compileall` 与 `git diff --check` 干净。

---

## G. Business Suite UI 自动化发布

> **详细任务书见 `docs/PUBLISH_PLAN.md`**，本节只放任务项与验收。
> **范围已按 2026-08-31 用户决定收窄**：FB DE Page + IG DE 同时发、
> **不补发历史**、只用最新几篇做端到端验证、上线后跟增量走。
> **不要把批量补发/优先级队列做回来。**
>
> **发布目标已提供**：`facebook_page_name = "Neakasa Deutschland"`（**显示名，不是 URL 段**）、
> `instagram_account = "neakasa.de"`。⚠️ 两者形态不同，别混——
> `facebook.com/Neakasa Deutschland` 不是合法地址。CR-12 已经因为
> "显示名 ≠ URL 账号名段"踩过一次。发布链路不需要那个 slug（G2 按显示名认 Page）。

> **开发分支：`feat/business-suite-publish`。** 与 K 组（`feat/image-de`）并行开发，
> **文件所有权表在 `PUBLISH_PLAN.md` 第 12 节**——动别人的文件之前先看那张表。
> 本节（G 组）**只由 `feat/business-suite-publish` 编辑**，不要顺手改 K 组那一节。

> ⚠️ **本组必须先探查再实现。严禁凭猜编写选择器。**
> Business Suite 是 React SPA，class name 是构建期混淆的，
> 任何"看起来合理"的选择器都是错的。

- [x] **G0**（2026-08-31 新增前置）发布走**独立的 Chrome profile**

  > **这是红线不是优化项。** `MANUAL_STEPS.md` 早写了发布账号要用另一个 profile，
  > 与抓取小号分开避免指纹关联。抓取小号被封是本项目唯一不可恢复的失败模式，
  > 把持有 DE 发布权的账号放进同一个指纹里，等于把两件事绑成一件。
  >
  > **但当前代码做不到**：`core/config.py` 只有一个 `profile_dir`/`debug_port`，
  > `core/chrome.py` 的 `launch()`/`attach()` 直接读全局 `cfg()`。

  - `config.toml` 新增 `[publish].profile_dir` 与 `[publish].debug_port`（默认 **9223**）
  - `core/chrome.py` 的 `launch()`/`attach()`/`cdp_ready()` 接受**显式 port 与 profile**，
    不给就退回读 `[chrome]`——**现有 backfill / delta 一行都不用改**
  - 新增 `scripts\start_chrome_publish.bat`（**纯 ASCII + CRLF + 无 BOM**，
    `tests_schedule.py` 的字节级断言要扩过来）→ `tools/start_chrome_publish.py`
  - ⚠️ 两个 Chrome 同时开着（抓取 9222 / 发布 9223）。"profile 被占用会静默复用
    已有实例并忽略 `--remote-debugging-port`"那个坑在这里同样成立，提示要区分是哪一个
  - 【验收】两个 profile 同时跑，`cdp_ready(9222)` 与 `cdp_ready(9223)` 都为 True，
    且**登录态互不可见**（发布 Chrome 里打开 instagram.com 不是抓取小号）
  - **G0 不依赖 G1，现在就能做**——它是整组唯一一件不需要真实 DOM 的事

  > 完成：2026-08-31 · `core.chrome.launch/attach` 支持显式 port/profile，
  > `cdp_ready()` 支持显式 port/profile 且无参仍回退 `[chrome]`；Windows 上会只读
  > 核对监听进程的 `--user-data-dir`，端口对但 profile 错也拒绝。配置把两侧端口
  > 或 profile 误写成相同值时启动前失败闭合；backfill/delta 零改动。
  > 新增发布专用 Python/.bat 入口并做字节级验收。实机 9222/9223 同时为 True；
  > 抓取 profile 有 FB/IG 登录 cookie，新建发布 profile 两边 cookie 为 0
  > （只比较登录 cookie 名、未读取或打印值），会话互不可见。
- [x] **G0b** `publish/compose.py`：发布前的离线硬闸（同样不依赖 G1）

  > UI 自动化最贵的是时间、最险的是半成品。**能在离线阶段拦下的，绝不留到线上拦。**

  - 把「译文 + 德语图 + 排期时刻」组装成一篇 `DePost`：
    - 正文取 `translated.jsonl` 的 `text_de`，**且 `prompt_version` 必须是当前版**，
      过期译文不发
    - 图片**优先 `posts/<帖子>/media_de/`**；缺失时回退原图并**显式告警**
      （那意味着要发一张带英文的图）
  - 离线硬校验，任一不过就不发：
    1. 译文存在、非空、版本当前
    2. **金额逐字符未被改动**——直接复用 `translate.py::money_preserved`，
       **不重写第二份**。改价格是商业事故，这道闸在发布环节要再过一次
    3. 至少 1 张图，每张能被 Pillow 打开且非 0 字节
    4. **IG 平台约束预检**（画幅 / 单帖图片数 / 正文长度 / 标签数）——
       ⚠️ 具体数字**以 G1 实测 UI 的实际拒绝行为为准**，
       不要照抄网上流传的数然后当事实写进注释
    5. **合作帖在输出里点名原作者**（不拦截）。最新那几篇 IG 帖恰好都是合作帖，
       `review.md` 与 `index.html` 已有同款提示，发布这最后一环不能反而没有
  - 【验收】最新 3 篇真实帖组装成功；人为把译文改过期 / 删掉一张图，
    各自被正确拒绝且说清原因

  > 进展：2026-08-31 · 代码与离线测试已完成：复用
  > `translate.money_preserved` / `translation_is_current`，逐图优先 `media_de`、
  > 缺图告警回退原图，Pillow 完整像素解码、缺失完整性标记、残缺/混合/脏媒体、
  > 合作方原作者、aware datetime 均有硬闸；IG/定时数字必须与 config 人工确认的
  > 完整 G1 dump 逐项一致，严格发布会核对 9223/profile/交互/截图/观察，缺值即失败。
  > 真实当前译文中 5 篇图文帖组装成功、1 篇纯视频被正确拒绝；但计划点名的
  > 最新三篇均尚无译文，所以“最新 3 篇成功”未通过，本项按协议不勾选。
  >
  > **完成：2026-08-31（同步审查轮）· 验收条件到这里才齐，现已勾选。**
  > 缺的那一半是 F 侧的选帖能力（CR-47）：用户授权后 `translate.py` 加了
  > `--post-id`/`--latest-posts`，点名的三篇已真实翻译。随后用新增的
  > `tools/compose_publish.py`（CR-58）在真实归档上跑出：
  >
  > ```
  > 可组装 3 篇 / 被硬闸拦下 0 篇
  >   facebook  / 122123185335379375   5 图
  >   instagram / 3973012230169803390  5 图（合作帖，点名原作者 Neakasa Global）
  >   instagram / 3975547640610092585  1 图
  > ```
  >
  > 三篇的金额硬闸都通过（真实正文里的 `$219.99` / `$100` 逐字符保留），
  > 每张图都按"缺德语图 → 回退原图并逐张点名"告警（K 组还没跑，符合预期）。
  > `--strict` 在 G1 未完成时正确失败闭合（"严格发布缺少 G1 实测定时窗口"）。
  > 「人为改过期 / 删图各自被拒」由 `tests_publish.py` 第 [3] 节的夹具覆盖。
- [x] **G0c** 第三轮同步审查修复（CR-46 ~ CR-60 中属于 G 组的四条）
  > 完成：2026-08-31 · 只改本组所有权内的文件（`requirements.txt` 见下方说明）。
  >
  > **CR-48**（用户拍板改 G 侧）· `media_de/` 同序号多候选的语义**与 K 组冲突**：
  > K 组**刻意支持**「程序图 `01.jpg` 与设计同事的 `01.png` 并存、人工优先」，
  > 而 `_choose_images` 原先见到多个候选就 `ComposeError` ——
  > **K 专门为设计同事设计的那个场景会让这篇帖子发不出去**。
  > 现在读 `images_de.jsonl` 判程序所有权（含 `output_sha256` 比对：
  > 程序产出被人改过字节也算人工版本），非程序产出的那个优先，并显式告警
  > 「这一篇被设计同事动过手」。仍然失败闭合的只有"多个都不是程序产出"。
  > ⚠️ **故意不 import `localize_images`**（K 组独占文件），只读它的产物 jsonl ——
  > 与 `PIPELINE_PLAN` 第 2 节「只读产物、不重写别人逻辑」同一条边界。
  >
  > **CR-58** · `compose_post()` 此前唯一的调用方是测试文件，于是本项的【验收】
  > 「最新 3 篇组装成功」**没有任何命令能让用户自己复跑**。
  > 新增 `tools/compose_publish.py` + `scripts/run_publish.bat`（纯 ASCII/CRLF/无 BOM）：
  > 零浏览器、零网络、零写盘，打印译文/逐张图来源/合作帖原作者/全部告警，
  > 支持 `--post-id`、`--latest N`、`--at`、`--strict`、`--json`。
  > 它同时就是 `require_confirmation = true` 那道人工闸要看的那张清单。
  >
  > **CR-59** · `cdp_ready(profile=…)` 每次都 fork 一个 `powershell.exe`
  > （`Get-NetTCPConnection` + `Win32_Process`，超时 5 秒），而 `launch()`
  > 在 15 秒窗口里**每秒**调一次 —— 一次启动最多 16 个 PowerShell。
  > 轮询改用不带 profile 的轻量 CDP 探测，端口起来之后再核对一次归属：
  > **判据一个字没松**，只是从最多 16 次降到 2 次。有断言钉住这个比例。
  >
  > **CR-60（本轮新发现，原先没人知道）** · **`ZoneInfo("Europe/Berlin")`
  > 在这台 Windows 上直接抛 `ZoneInfoNotFoundError`。**
  > 实测 `zoneinfo.TZPATH` 是**空的**：Windows 不自带 IANA 时区数据库，
  > 而 `zoneinfo` 只读系统数据库。这不是小毛病 ——
  > `PUBLISH_PLAN` 第 3.3 节把显式时区转换写成硬要求，还要求**在夏令时切换日
  > 各测一次**，两件事都不可能靠写死 UTC 偏移做对。
  > **所以 G5 一旦按计划实现就会当场失败，而且是在最难发现的那一环。**
  > 处置：`requirements.txt` 加 `tzdata>=2024.1`（纯数据包、无原生代码、
  > 无传递依赖），理由按 Pillow 的先例写进注释；已装进 `.venv`（2026.3）并验证
  > 2026 年两个切换日（03-29 / 10-25）的偏移都正确。
  > ⚠️ 这是本轮唯一一处越出所有权表的改动（表里写着"`requirements.txt` 三组都不用改"）。
  > 那句话的本意是避免 K/G 同时改这一个文件造成冲突；K 组已收工且没碰它，
  > 因此实际零冲突。**这条偏差在这里显式记录，不是默默改的。**
- [ ] **G1** 探查 Business Suite 的真实 DOM 与流程　**← 需要你操作，且它阻塞 G2–G7**

  - 用 **G0 建的发布 profile**（不是 A3 那个抓取 profile）登录**有 DE Page 发布权的账号**
  - 交付 `tools/probe_publish.py`：**记录，不驱动**。附着到 9223，你手工走一遍，
    程序在旁边把每次交互命中元素的**稳定属性**（tag / role / aria-label /
    data-testid / name / placeholder / 可见文本 / 是否 contenteditable）
    dump 成 `state/publish_probe_<时间戳>.json`，每步存一张截图
    > ❌ **不用 `playwright codegen`。** 它产出的是 `div > div:nth-child(3) > span`
    > 这类脆选择器，混淆 class 一变就全废。我们要的是 role + 可访问名这类抗混淆定位。
    > **与回填的人工滚动是同一条设计**：驱动页面的是人，程序只在旁边捞。
  - 要回答的七个点：
    1. 创建帖子的入口（URL 与按钮）
    2. 图片上传控件是 `<input type=file>` 还是拖拽区
    3. 文案框是 `<textarea>` 还是 contenteditable
       （contenteditable 的**换行要逐行 type + Shift+Enter**，直接 fill 会把多段挤成一段）
    4. **FB 与 IG 两个渠道的勾选控件在哪**（用户要同时发，这是本次新增的一路）
    5. 定时开关的位置
    6. **日期/时间选择器能否直接输入；UI 显示的是哪个时区**
    7. 提交按钮 + 提交成功的确认信号（toast / 跳转 / 排期列表出现）
  - 【验收】`publish/selectors.py` 存在，含**至少 7 个带注释的定位常量**，
    每个注释写清「对应哪一步 / 来自哪份 probe dump / 什么信号说明它失效了」
  - 【记录】**定时窗口的 UI 上下限**（最早排多久之后、最晚排多远）与时区行为。
    Graph API 那边是 10 分钟–75 天，**但 UI 不一定一样，必须实测**

  > 进展：2026-08-31 · `tools/probe_publish.py` 已交付并通过离线 recorder 测试：
  > 只监听人工 click/input/change/submit，逐步截图、原子写 JSON，记录命中元素与
  > 语义祖先的稳定属性；合成事件/敏感输入丢弃，Python 侧再做字段白名单、URL 去参、
  > 登录输入截图遮罩，不记录 class/CSS path/cookie/输入值；结束时补记时区、窗口、
  > 四类 IG 实测拒绝、slug 与成功信号。用户尚未实际运行，`selectors.py` 仍只有
  > TODO，故不勾选。
- [ ] **G2** 登录态与**目标 Page**检查

  - `publish/business_suite.py::ensure_logged_in(page) -> bool`
  - 未登录则**打印提示并退出**。❌ **不得实现自动登录**（全局红线 1）
  - ⚠️ 还要判**登录的是不是对的账号 / 选中的是不是对的 Page**——
    登录态在、但上下文是别的 Page，会把德语内容发到错误主页上，
    **这比没登录严重得多**
  - 【验收】未登录时提示清晰；已登录但 Page 不对时**也**要拦住
- [ ] **G3** 图片上传

  - `upload_images(page, paths: list[Path]) -> None`，优先 `set_input_files()`
  - 上传后**等缩略图出现**再继续，不用固定 `sleep`
  - 【验收】1 张与 **5 张**（最新那两篇真实帖就是 5 图）缩略图数量正确
- [ ] **G4** 文案填写

  - `fill_caption(page, text: str) -> None`；contenteditable 按 G1 第 3 点处理换行
  - 填完**回读比对**，不一致抛错
  - 【验收】含换行、空行、emoji、德语变音（ä/ö/ü/ß）、`#标签`、`$金额`
    的文案能正确填入并回读**逐字符**一致
  - ⚠️ 入口必须调 `core.console.force_utf8()`：本机代码页 936，
    输出一旦被重定向就会崩在 `ß`/`⚠` 上（附录 C 有记录）
- [ ] **G5** 定时设置

  - `set_schedule(page, when: datetime) -> None`
  - ⚠️ **时区是整组最容易出错的一步。** Business Suite 显示的时间跟 **Page 的时区设置**走，
    不一定是本机时区，也不一定是 `Europe/Berlin`，三者可能各不相同。
    按 G1 记录显式转换，**不依赖本地时区隐式生效**
  - 设完**回读 UI 上显示的日期时间**比对
  - 【验收】跨日 / 跨月 / **夏令时切换日**各测一次
    （`Europe/Berlin` 每年切两次，那两天最容易错）
- [ ] **G6** 提交与结果确认

  - `submit(page) -> str | None`，必须等**明确的成功信号**，不能提交完就返回
  - 成功后写 `state/published.jsonl`（见 G6b）
  - 【验收】排期列表里能看到该帖
- [ ] **G6b** 幂等与留痕 `state/published.jsonl`

  - 每条记：`post_id` / 平台 / `scheduled_at`（含时区）/ 提交结果 /
    Business Suite 返回的标识（若有）/ 截图路径 / `text_de_sha256` / 用了哪些图
  - **同一 post_id + 平台已有成功记录 → 跳过**，除非显式 `--force`
  - 这是"UI 自动化没有事务性"的**唯一补偿**：出了事靠它回答
    "到底发出去了什么、什么时候、用的哪版文案和哪版图"
  - 【验收】连跑两次，第二次全部跳过、零浏览器操作
- [ ] **G7** 失败处理与残留清理

  - 截图存 `state/publish_failures/<时间戳>.png`；打印当前 URL 与失败步骤；
    **检查草稿残留并提示人工清理（不自动删除）**
  - 【验收】人为把某个定位改错 → 有截图、有明确的失败步骤提示
- [ ] **G8** 端到端发布测试

  - 用 **FB `122123185335379375`（2026-08-27，5 图）** 与
    **IG `3973012230169803390`（同日，5 图）**——同一波内容的两个平台版本
  - 排一个**几天后**的时刻，便于验证后取消
  - 【验收】已排期列表中出现该帖，时间、文案、图片全部正确；
    德语文案与 `review.md` 里那版**逐字符一致**
  - 【记录】从调用到完成的耗时、人工介入次数、UI 实际接受的定时窗口
- [ ] **G9** 与每日增量的挂钩

  - `delta 抓到新帖 → translate → localize_images → 排期发布`
  - ⚠️ **自动发布到品牌主页是不可回滚的对外行为。** 抓错可以重抓、译错可以重译，
    **发出去了只能删帖，而删帖在粉丝侧是可见的**。
    因此最后一棒默认需要人工确认：`[publish].require_confirmation = true`，
    程序把待发清单（原作者 / 译文 / 图 / 排期时刻）打出来，人点头才提交。
    **这是一行配置，想全自动改成 `false` 即可**；建议至少前几周保留——
    理由和 C7 那七条一样：这类风险的反馈是延迟的，而且第一次通常就是最贵的那次
  - 【验收】增量抓到新帖后，链路能一路走到"待确认清单"，且确认后能真的排上

---

## L. 流水线编排与自动化（跨组，2026-08-31 新增）

> **总体规划见 `docs/PIPELINE_PLAN.md`**，本节只放任务项与验收。
> A~K 每组回答"这一段怎么做对"，**L 组回答"这些段怎么连成一条不用人管的线"**。
>
> **开发分支：`feat/pipeline`。L 组不改 K / G 的任何实现文件**——
> 它只**调用**它们的入口、**读**它们的产物。这既是架构约束，
> 也顺带让三条分支互不冲突。所有权见 `PIPELINE_PLAN.md` 第 13 节。

> ⛔ **两条会毁掉这一组的做法**：
> 1. **不要建队列。** 建对账器（reconciler）。四个阶段的"做完了没有"
>    全都是内容寻址的（指纹 + 版本），差值可以每次现算。
>    队列是第二个真相源——这个项目已经为此立过铁律
>    （`post.json` 是真相、`manifest.jsonl` 是派生，方向永远单一）。
> 2. **对账器不许自己干活。** 不解析响应、不拼提示词、不点浏览器。
>    它一旦"顺手做一点"，就成了第五个真相源。

### L0 · 不依赖 K / G，现在就能做

> ⚠️ **执行顺序是 L0b → L0c → L0a →（等 G1）→ L0d**，不是下面的编号顺序。
> 2026-08-31 第七轮改的，两条理由都在 `PIPELINE_PLAN.md` **10.1**：
> ① 死人开关要赶在计划任务**之前**落地，否则会出现一段"自动化在跑、
> 但没人能确认它还活着"的窗口——而**"没跑"和"跑了但没事做"输出上一模一样**，
> 这个项目为同型失效吃过一次亏（静默丢 263 篇）；
> ② `[publish.price_map]` 是 TOML 子表、必须在 `[publish]` 段最后，
> 而 G1 还要往 `[publish]` 写值，先插子表会让之后新加的标量键静默掉进表里。
> 两处延后的代价都是零。

- [ ] **L0a** 装计划任务（就是 E3）　⬅️ **已解锁，轮到用户**（死人开关已就位）

  > ⚠️ **这是整个自动化的第 0 步，也是当前投入产出比最高的一件事。**
  > 在它装上之前，这个项目的自动化程度是 **0**——所有的幂等、断点、
  > 告警、降级都写好了也测过了，**但没有任何东西会在没人的时候运行它们**。
  - 用户操作，`MANUAL_STEPS.md` 第 9 步，一条命令
  - 【验收】`tools.schedule status` 显示两个任务已注册；次日 `delta.log` 有新记录
- [x] **L0b** `pipeline status`：只读对账　✅ **2026-09-01 完成**

  - **零网络、零花钱、零写盘。** 打印各阶段积压、最近一次成功、
    本月花了多少、人工队列里有几篇
  - 沿用 `routes.delta --status` 已被用户接受的形态
  - **先有"看得见"，再谈"自动跑"**
  - ⚠️ **`待发布` 这一列在 G6 落地之前是假的**：判据是 `state/published.jsonl`，
    而那个文件由 G6 写、现在还不存在，所以它会恒等于"可发"。
    **要么显式标注"G6 未落地"，要么先不打印这一列**——
    不要打一个看起来是积压、实际是"这个阶段还没接上"的数字
  - 顺带还掉一笔债：实测**没有任何代码在读 `[pipeline]`**，
    `main` 上那四个键目前是死旋钮（违反 CR-40 立的规矩）。
    `dead_man_days` 由 L0c 接上，两个 budget 键由 L1b 接上
  - 【验收】在当前归档上跑出正确的积压数，且确实一次网络请求都没发
  - **基线（2026-08-31 第七轮实测，可直接用来对答案）**：

    | 账号 | 归档 | 可发 | 待译 | 待调图 |
    |---|---:|---:|---:|---:|
    | `fa_neakasaofficial` | 47 | 27 | 24 | 27 |
    | `in_neakasa.tech` | 1020 | 443 | 438 | 443 |
    | **合计** | **1067** | **470** | **462** | **470** |

    另有 **597 篇**纯视频/无图/无正文，进不了发布口径。
    ⚠️ **"可发"是发布口径（有正文 **且** 有已下载的图），不是翻译口径**，
    这是这个项目最容易搞错的一处数字
- [x] **L0c** 死人开关（**当前最大的可观测性缺口**）　✅ **2026-09-01 完成**

  > ⚠️ **两处偏离任务书，理由写在 `PIPELINE_PLAN.md` 10.2**：
  > ① 没有新建 `pipeline_state.json`，改从 `delta_state.json` 已有的
  > `last_success` 读——那是**运行**标记（抓到 0 篇也更新），
  > 正是第 7 节要区分「没跑」和「跑了没事做」需要的语义；
  > 再记一份就是第二个真相源（第 2 节明令不要）。
  > ② 用**独立**计划任务 `FBScraperAlive`，不挂进增量任务——
  > 挂进去会跟着增量任务一起哑掉，而那正是它要抓的失效。

  > `core/integrity.py` 检查的是「**账号**有没有在发新帖」。
  > **没有任何东西检查「我们的流水线还在不在跑」。**
  > 计划任务被禁用、`.venv` 被删、笔记本连续两周没开机——今天全是静默的，
  > 而**"没跑"和"跑了但没新内容"在输出上长得一模一样**。
  > 这个项目已经吃过同型的亏：合作帖丢了 263 篇，输出里一个字都没有。
  - `pipeline_state.json` 记 `last_successful_run`；
    超过 `[pipeline].dead_man_days`（默认 3）就告警
  - ⚠️ **这个检查不能只写在 `pipeline run` 里**——它自己不跑的时候，
    正是最需要它响的时候。挂进 `FBScraperDeltaCatchup`（登录/解锁触发）那条链路
  - 【验收】人为把 `last_successful_run` 改到 4 天前 → 下次触发时告警弹出
- [ ] **L0d** 价格表 `[publish.price_map]`（**流水线上最密的一道人工**）

  > `prompts/translate_de.md` 第 3 节要求金额逐字符原样保留，
  > 并写明「这些金额会由人工在审校环节替换成德国站定价」——
  > 也就是**每一篇带价格的帖子都要人动一次手**。
  > 实测：**近 90 天 67 篇图文可发帖里 26 篇含金额（39 %）**，每 5 篇就有 2 篇。
  >
  > **解法是把"每篇一次决策"变成"每个价格一次表项"**：
  > 全部图文可发帖里只出现 **46 种**不同金额串，一张 46 行的表覆盖整个归档。
  - 从现有归档生成初稿，交业务填德国站定价
  - **替换发生在发布前，不在翻译时**——译文真相源不该混进商务决策，这条边界不动
  - **硬闸**：出现表里没有的金额串 → 该篇进人工队列，提示补一行映射。
    ⚠️ 这道闸不能省：没有它，缺失的映射要么把美元价发到德国站（商业事故），
    要么静默跳过（更糟）
  - 【验收】46 种金额串全部有映射；构造一个新金额 → 该篇被正确拦下

### L1 · 对账器主干（K 或 G 任一落地后即可接）

- [ ] **L1a** `pipeline run`：按顺序推进所有能推进的阶段，每阶段调各自已有入口
- [ ] **L1b** 预算闸（`[pipeline].monthly_budget_usd` / `daily_budget_usd`）。
      ⚠️ 按**真实 usage** 外推，不许按字符或张数猜（CR-40：低估一个数量级）
- [ ] **L1c** `state/needs_human.jsonl` + 单页 HTML。
      **把"人"变成一个阶段，而不是一次中断**；沿用 `tools/layout.py::index`
      那条已被用户接受的路子（双击就能看）
- [ ] **L1d** `autonomy = "assisted"`：抓取/翻译/调图自动，发布停在待确认

### L2 · 分流（G8 通过之后）

- [ ] **L2a** 七条分流规则（`PIPELINE_PLAN.md` 第 6 节）。
      ⚠️ **不许为了"看起来更自动"去放宽它们**——它们和 C7 那七条一样，
      不是防回归，是防"跑得挺好就顺手优化掉"
- [ ] **L2b** `[publish.trusted_owners]` 白名单。
      实测近 90 天可发的合作帖原作者**全是自家兄弟账号**
      （`neakasa.global` 21 篇 / `neakasa.de` 3 篇），触发率接近 0
- [ ] **L2c** 排期规则（工作日 × 时刻 × 按原发布时间正序），人不用挑时间
- [ ] **L2d** `autonomy = "supervised"`

### L3 · 收敛

- [ ] **L3a** 每周一条摘要通知。⚠️ 频率要克制：
      **误报的代价不是打扰，是让整条告警通道失效**（`[integrity]` 已立过这条）
- [ ] **L3b** 量一次真实的人工分钟数，与 `PIPELINE_PLAN.md` 第 11 节的目标对照
      （今天 ≈ 95 min/周 → 目标 ≈ 8–10 min/周）
- [ ] **L3c** 是否推到 `autonomous`——**这是业务决定，不是技术决定**

---

## H. API 只读验证通道（低优先，保留能力）

> 用户当前拿不到 API Token，因此本组**不阻塞任何其他任务**。
> 一旦拿到 Token 再做。它的价值是：给 UI 自动化提供断言手段——
> 否则你只能靠肉眼确认 UI 到底发对了没有。

- [ ] **H1** 把 `routes/fb_graph.py` 降级为只读验证模块

  - 现状核对：`fb_graph.py` 里只有 `scrape_page()` 和
    `scrape_ig_professional()` 两个函数，**都是读取**（对 Meta 无写入），
    所以"移除发布函数"无事可做。真正要做的是下面这一条
  - 新增 `verify_scheduled_post(post_id: str, token: str) -> dict`：
    `GET /{post-id}?fields=is_published,is_hidden,scheduled_publish_time,created_time`
  - 【验收】拿到 Token 后，对 G8 发布的帖子调用能返回正确的排期时间
- [ ] **H2** 接入静默失败检测

  - 已知 Graph API 定时发布约 1% 静默失败：帖子存在但不在时间线上，
    特征是 `is_published=false` 且 `is_hidden=true`
  - 实现 `repair_post(post_id, original_ts, token)`：
    `POST /{post-id}` body `{is_published: true, backdated_time: <原定时间戳>, timeline_visibility: "normal"}`
  - ⚠️ **省略 `backdated_time` 会让帖子显示为修复时刻发布，而非原定时刻**
  - 【验收】能对一个已发布帖子正确读出状态（修复路径不易构造，能读即可）

---

## I. 联调与交付

- [ ] **I1** 端到端串跑

  - 顺序：回填 → 增量跑一次 → 翻译 → 生成审核清单 → 发布 1 条
  - 【验收】全流程无手工修数据，中间产物齐全
- [ ] **I2** 更新 `README.md`

  - 按实际实现更新：命令、目录结构、已知限制
  - **删除或修正**所有与最终实现不符的描述（当前 README 还残留
    官方 API / DYI 导出路线的内容，那两条已否决）
  - 【验收】一个没参与开发的人照 README 能跑通回填
- [ ] **I3** 汇总遗留问题

  - 在本文件末尾新增"## 遗留问题"一节，列出：
    - 未能实现的项及原因
    - 实现了但不稳定的项
    - 需要定期人工介入的环节
  - 【验收】该节存在且非空（如果真的一个遗留都没有，写明"无"）

---

## 附录 A · 任务依赖图

```
A1 → A2 → A3 → A4 → A5
                      ↓
              ┌───────┴───────┐
              ↓               ↓
        B1 → B2         B3 → B4
              └───────┬───────┘
                      ↓
                     B7  ← 离线重放重建归档（不需重滚）
                      ↓
                     B5 → B6      B8（进度显示，可与 B5/B6 并行）
                      ↓
                     J1 → J2 → J3  ← 归档布局重构，**必须先于 C4/D3/F 落地**
                      ↓
        ┌─────────────┼──────────────────────────┐
        ↓             ↓                          ↓
   C2→C3→C4→C5→C6→C7  F1→F2→F3            G0 · G0b（不依赖 G1，现在就能做）
   （C1 已完成但方案 B 下用不上）  ↓                 ↓
        ↓                    K0→K1→K2→K4→     G1 探查 ← **需要用户操作，阻塞下面全部**
   D1→D2→D3               K5→K6→K7→K8→K9          ↓
                          （K3 已取消）
        ↓                            ↓        G2→G3→G4→G5→G6→G6b→G7
   E1→E2→E3                          └──────────────→ G8 → G9
        │                                             ↓
        │                                   (H1→H2 需 Token，不阻塞)
        └──────────────┬──────────────────────────────┘
                       ↓
   L0（**不依赖任何组，现在就能做**）→ L1 → L2 → L3
   L0a 装计划任务 · L0b status · L0c 死人开关 · L0d 价格表
                       ↓
                   I1 → I2 → I3
```

⚠️ **L0a（装计划任务）是这张图里投入产出比最高的一格。**
在它装上之前，这个项目的自动化程度是 **0**——
所有的幂等、断点、告警、降级都已写好并测过，
**但没有任何东西会在没人的时候运行它们**。

**可并行的支线**：C/D/E（增量与调度）、F→K（翻译→调图）、G（发布）、
**L0（编排的前四项，不依赖任何组）**。

**两条新的硬依赖**（2026-08-31 加入）：

- **K 依赖 F**：图内德语文字要用该帖已有的 `text_de` 做参照
  （用户指定），没有当前版本的译文就不处理这篇的图。**不做降级。**
- **G8 依赖 K**：发布优先用 `media_de/` 的德语图，缺失时回退原图并显式告警。
  但 **G0 / G0b / G1 不依赖 K**，可以并行推进。

**当前 G 组唯一的外部卡点是 G1。** 在它完成前，G 组能推进的就是 G0 与 G0b，
其余只有骨架——**这是设计，不是拖延**（全局红线 5）。

**K 组现在没有任何外部卡点**：密钥已就位、两个待拍板项已结清、Pillow 已预置。

### 并行开发的分支划分（2026-08-31 定）

| 组 | 分支 | 文件所有权表在哪 |
|---|---|---|
| K 组（图片德语化） | **`feat/image-de`** | `docs/IMAGE_PLAN.md` 第 10 节 |
| G 组（Business Suite 发布） | **`feat/business-suite-publish`** | `docs/PUBLISH_PLAN.md` 第 12 节 |
| **L 组（流水线编排）** | **`feat/pipeline`** | `docs/PIPELINE_PLAN.md` 第 13 节 |

⚠️ **L 组与另外两组的边界很干净：它不改 K / G 的任何实现文件**，
只**调用**它们的入口、**读**它们的产物。三条分支因此互不冲突。
唯一要留意的是 `config.toml` 的 `[publish]` 段——
**L 组会往它末尾加三个子表**（`price_map` / `trusted_owners` / `schedule_rule`），
按 TOML 规则子表必须在段末，**G 组新增标量键要加在子表之前**
（该段已就地留了警告注释）。

两条分支都从 **`docs/plan-image-publish`** 开（那里有计划、`[image]`/`[publish]`
配置和 Pillow）。**本文件的 K 组一节只由前者编辑，G 组一节只由后者编辑**；
附录 D 各自追加一段、标题带分支名，合并时**两段都要保留，不许二选一**。

---

## 附录 B · 验收总清单

实施完成后，逐条确认：

- [ ] 回填能拿到 FB 和 IG 的全部历史图文帖，数量与目测相符
- [ ] **归档里没有任何一条属于其它账号的帖子**（2026-08-30 实测漏进 267 条，
      下游会翻译并发布他人内容，是法务风险）
- [ ] **没有轮播子项被当成独立帖子**（2026-08-30 实测漏进 478 条）
- [ ] **FB 视频帖被正确记为视频**，而不是"图片抓取失败"（实测 20 条被误记）
- [ ] 被丢弃的节点都进了 `_rejected.jsonl`，没有静默丢弃
- [ ] 每篇帖子在 `posts/` 下有独立文件夹，文件夹名带发布日期
- [ ] `media/` 下无 0 字节文件，分辨率与 manifest 一致
- [ ] 视频未被下载，但 manifest 中有记录
- [ ] ~~登出增量能发现新帖，且运行时不携带任何 cookie~~
      **方案 B 已改为登录态增量**（登出端点已关闭）。改为验收：
- [ ] 登录态增量能发现新帖，且**没有引入第二条登录路径**（会话仍来自人工登录）
- [ ] 增量**只滚有限几屏**，没有每天滚到底
- [ ] 触发时刻有随机化，不是每天固定整点
- [ ] 遇到登录墙立即停止并告警，没有重试/换 UA/绕过的行为
- [ ] 连续失败达阈值后会停止自动运行，而不是无限重试
- [ ] 增量重复运行幂等，不产生重复记录
- [ ] 计划任务已注册，合盖唤醒后能补跑
- [ ] 连续性检查能检出人为构造的缺口
- [ ] 告警能实际弹出（或降级写日志）
- [ ] 德语译文经人工审阅通过
- [ ] **德语图经懂德语的人确认**：文字正确，且优惠码 / 品牌 / 型号 /
      合作方水印 / 金额 / 数值单位 / 认证标识**一个都没被动过**
- [ ] **德语图的产出没有覆盖任何人工放进 `media_de/` 的文件**
- [ ] **原图从未被修改**（`01.jpg` 是抓取产物，只读）
- [ ] 能通过 UI 成功创建 1 条定时帖，时间与文案正确
- [ ] **发布与抓取用的是两个不同的 Chrome profile**，登录态互不可见
- [ ] **发布幂等**：同一篇不会被排两次（`state/published.jsonl` 生效）
- [ ] **定时时刻的时区经过显式转换并回读校验**，不是靠本地时区隐式生效
- [ ] 发布失败时留下截图与失败步骤，**没有自动删除任何草稿**
- [ ] 全流程无硬编码 `doc_id`
- [ ] 全流程无自动登录
- [ ] **`publish/selectors.py` 里没有任何未经 G1 探查得来的选择器**
- [ ] `state/`、`archive/`、Chrome profile（**两个**）、`.env` 未进版本库

---

## 附录 C · 遇到这些情况该怎么办

| 症状                                 | 判断                                             | 处理                                                                 |
| ------------------------------------ | ------------------------------------------------ | -------------------------------------------------------------------- |
| IG 返回 401，文案说"稍后再试"        | **不是限流**，是 `doc_id` 失效或会话失效 | 不要等、不要重试。检查是否误用了硬编码 doc_id 的路径                 |
| 登出请求返回 HTML 而非 JSON          | 登录墙触发                                       | 降低频率，隔几小时再试。不要立即重试                                 |
| 回填解析出 0 篇但有`_capture` 文件 | 解析器与真实结构不符                             | 用 capture 文件离线校准解析器，**不要重滚**                    |
| 媒体下载 403                         | URL 已过期                                       | 必须在同一次运行内下载。检查是否存了 URL 事后再取                    |
| Chrome 连不上调试端口                | 没跑 scripts\start_chrome.bat，或端口被占                | 先起浏览器；端口冲突则改`config.toml` 的 `debug_port`            |
| 计划任务不触发                       | 笔记本电源策略                                   | 取消"仅在使用交流电时运行"；确认唤醒触发器已加                       |
| Business Suite 选择器失效            | SPA 改版                                         | 重跑 G1 探查，更新`publish/selectors.py`。这是预期内的定期维护     |
| 抓到的图片分辨率很低                 | CDN 压缩                                         | 确认取的是最大尺寸候选。爬取路径拿不到原始上传文件，这是路线固有代价 |
| 登出增量**首次请求**就 429     | **已定论（2026-08-30）：端点对登出访客关闭**     | 不要排查、不要换网络重试。这是既成事实，见 C 组开头的 ⛔。等增量方案拍板 |
| 回填解析出的帖子数比目测多很多 | 混进了推荐内容/被 @ 的 UGC，或轮播子项被当成帖子 | 看 `_rejected.jsonl` 和 `posts/` 的实际条数；`partition_by_owner` 已拦，若仍超标说明 owner 字段路径变了 |
| FB 某帖 `media` 为空          | 可能是纯文字帖、头像更新帖，**不一定是抓取失败** | 头像/封面更新帖的 Photo 节点只有 id 没有 uri，响应里本来就没图。`media_complete` 仍应为 True |
| 手工删了 `posts/` 下的文件夹后索引对不上 | 索引是派生的                          | 跑 `python -m tools.layout reindex <平台>`。**方向永远是 posts→索引，不反过来** |
| 输出重定向到文件后崩在`UnicodeEncodeError` | 本机代码页 936，`⚠ ❗ ✅ ❌ ß` 编不出来 | 入口调`core.console.force_utf8()`；`.bat` 里设 `PYTHONIOENCODING=utf-8`。控制台下看不到这个故障 |
| **图片 edits 请求 400，`error.param` 是 `input_fidelity`** | gpt-image-2 不接受这个参数 | **删掉它。** 高保真是该模型默认行为，不靠参数换。⚠️ 别信 openai SDK 签名与 docstring，那句"1.5 及之后支持"对 gpt-image-2 是错的 |
| **图片 edits 请求 400，`error.param` 是 `size`** | 宽高不是 16 的倍数，或总像素越界 | 走 `legal_size()`，**不要拿原图宽高直接当 size**。归档里一半以上的图不合法（1080 不是 16 的倍数） |
| 图片费用比预期高一个量级 | 用了 `quality="auto"` 或 `high` | `quality` 必须显式给；默认 `medium`。high 贵 4 倍，而输出还要被 Meta 再压一次 |
| 德语图整张被重画了、产品变了样 | 模型没在"改字"而在"重绘" | K5 的 dHash 闸就是拦这个。阈值标定前它是关的，**先看 `images_de.jsonl` 里的 `dhash_distance` 分布** |
| `media_de/` 里的图被程序覆盖了 | K6 的"人工优先"判据写错了 | 判据是「文件存在**且** `images_de.jsonl` 里没有对应记录 → 跳过」。两个条件缺一不可 |
| 发布 Chrome 起了但端口 9223 没开 | 与 9222 同一个坑：profile 被占用会静默复用已有实例 | 任务管理器里找用**发布 profile** 的 `chrome.exe`。⚠️ 别把抓取那个 Chrome 关了 |
| 定时帖发在了错误的时刻 | 时区没显式转换 | Business Suite 跟 **Page 的时区设置**走，不是本机时区也不一定是 `Europe/Berlin`。按 G1 记录显式转换并**回读 UI 比对** |
| 德语内容发到了错误的主页 | 登录态在但选中的 Page 不对 | G2 要判的是"登录了**且** Page 对"，只判登录会漏掉这种——**它比没登录严重得多** |

---

## 附录 D · 计划同步记录

> 代码与计划的每次对齐都记在这里，避免"计划说的和代码里的不一样"。

### 2026-08-29 · 迁移到 Windows 前的一致性整理

审计发现并修掉的问题：

1. **代码里存在三条互相冲突的登录路径**，而"禁止事项"只允许一条。
   已裁剪 `core/session.py`：删除 `_login_flow` / `load_state` /
   `cookies_for` / `state_path` / `STATE_DIR`（含 import 时创建目录的副作用），
   只保留 `Pacer` 与 `SAFARI_UA`（108 行 → 53 行）。
   现在全项目只有一条登录路径：人工在 `scripts\start_chrome.bat` 的 Chrome 里登录。
2. **`routes/ig_v1_feed.py` 与已定架构冲突**（登录态抓取 vs 增量必须登出），
   且是唯一使用 `cookies_for` 的模块。已移入 `_deprecated/`。
   其端点与 Header 常量已收录于第 1 节，无信息损失。
3. **`README.md` 严重过时**：仍在描述四条路线（其中官方导出与官方 API
   两条已否决），且指向已移走的模块。已重写为"两条路径"并全面校正。
4. **`scripts\start_chrome.bat` 有一个会浪费大量调试时间的坑**：若目标
   `user-data-dir` 已被占用，Chrome 会静默复用已有实例并忽略调试端口参数。
   已加入实例占用检测 + 端口就绪轮询（最多 15 秒）+ 三条失败原因提示。
5. **依赖表虚高**：`requests` 与 `python-dotenv` 无任何代码引用，已移除。
6. **`.gitignore` 有重复条目**，且缺 `.venv/`。已去重并补全。
7. **缺少 Windows 入口脚本**。新增 `scripts\setup.bat`（含 Python 版本校验、
   环境搭建、自检、离线测试）与 `scripts\run_backfill.bat`。

相应更新的计划条目：目录结构、Windows 命令约定、现状盘点表、A1–A3、B1/B3、H1。

**未验证项（本次整理无法覆盖）**：本次整理在 macOS 上完成，
所有 `.bat` 脚本、Chrome 路径探测、CDP 连接**均未在 Windows 实机跑过**。
A 组任务的意义正在于此——它是这些假设的第一次真实检验。

### 2026-08-29 · A1/A2 在 Windows 实机的第一次真实检验

上一条预言的「第一次真实检验」结果：**Chrome 路径探测一次命中，三个 `.bat`
全部不可用**。详细记录在 A1 的完成行，这里只留结论与对后续任务的影响。

坏掉的原因全是平台假设，与业务逻辑无关：

| 假设 | 实际 | 影响面 |
|---|---|---|
| `.bat` 用 LF 换行没问题 | cmd 误解析整行 | 三个 `.bat` 全部 |
| `.bat` 里可以写中文 | cmd 把行从中间劈开当命令执行 | 三个 `.bat` 全部 |
| `pip`/`uv` 能从默认源装包 | 官方源吞吐近 0，8 分钟零进展 | 任何装依赖的环节 |

**对后续任务的约束**（已同步进第 2 节禁止事项）：

- E1 要新建的 `scripts\run_delta.bat` 必须同样是**纯 ASCII 壳**，逻辑写进 Python。
- E3 的 `schtasks` 命令里若含中文任务描述，同样注意编码；
  建议任务名用 ASCII（`FBScraperDelta`），描述留空或走 XML 导入。
- 任何新增的 `.bat` 都要过一遍：纯 ASCII + CRLF。`.gitattributes` 只保证换行，
  不保证 ASCII，这一条得靠人。

**被证伪的一处计划描述**：附录 C 的「Chrome 连不上调试端口 → 端口冲突则改
`config.toml` 的 `debug_port`」原本不成立——旧 `scripts\start_chrome.bat` 自己硬编码了
`PORT=9222`，只改 `config.toml` 无效。现在 `tools/start_chrome.py` 一律从
`config.toml` 读，该处理办法才真正成立。

**被证伪的第二处**：上一条同步记录第 2 项称
「`routes/ig_v1_feed.py` … 已移入 `_deprecated/`」——**实际是复制，原件从未删除**。
`routes/ig_v1_feed.py` 与 `_deprecated/ig_v1_feed.py` 字节完全相同，两份并存。
留着它有三个问题：

1. 它 `from core.session import ... cookies_for`，而 `cookies_for` 已在上次整理中
   删除 —— 该模块现在一 import 就 ImportError，是死代码。
2. 它是一条**登录态抓取路径**，却待在活跃的 `routes/` 包里，
   正是"只允许存在一条登录路径"要防的东西。
3. 计划声称它已被移走，读计划的人不会去 `routes/` 找它。

已删除 `routes/ig_v1_feed.py`（2026-08-29）。`_deprecated/` 里的存档件保持不动，
信息零损失。附带说明：`_deprecated/intercept.py` 里有一行
`from routes.ig_v1_feed import _parse_item`，删除后该 import 失效——
但 `intercept.py` 本来就已因 `state_path` 被删而不可运行，且 `_deprecated/`
是留档不是留后路，**不修**。

### 2026-08-29 · C1 / D1 / D2 完成（B 组尚未开始）

这三项在依赖图上画在 B 组之后，但实际都不依赖真实响应或真实账号：
C1 是构造 HTTP 客户端、D1 是纯时间序列计算、D2 是调 PowerShell。
趁 A3/A4/A5 需要人工介入的空档做掉了，各自带离线测试。
**依赖图的箭头对 C2 及以后仍然成立**——那些确实要等 B4 校准完的解析器。

离线测试基线现为 5 套 86 项断言（原 2 套 31 项）：

| 套件 | 断言数 | 覆盖 |
|---|---|---|
| `tests_parse.py` | 17 | 三形态解析、跨形态合并、脏输入 |
| `tests_store.py` | 14 | 幂等、续传、补全升级、拒绝降级 |
| `tests_http.py` | 17 | 拒收 cookie（含 Set-Cookie 攻防）、IG Header |
| `tests_integrity.py` | 23 | 缺口检出、不误报、边界、无日期记录 |
| `tests_notify.py` | 15 | 三级降级、脏输入不崩、日志必留 |

`tools/setup.py` 用 `glob("tests_*.py")` 全跑，新增测试自动纳入基线，
不需要回来改文件名列表（改这里很容易忘，忘了等于那套测试没人跑）。

### 2026-08-29 · F 组代码完成（历史记录：内部兼容端点方案已被 DeepSeek 替代）

> **状态更正（2026-08-30）**：本条保留为决策历史，不再是当前配置说明。
> 当前业务路径是 DeepSeek 官方 Anthropic 兼容端点与 `deepseek-v4-pro`，
> 以 F 组正文和 `TRANSLATION_PLAN.md` 为准。

用户指定：翻译走**公司内部兼容 Anthropic Messages 的第三方 API**，
并要求把需要人工操作的步骤写成指南。两件事都做了。

**F1/F2/F3 三项均未勾选**——它们的验收都要求真实文案 + 真实网关，
现在两样都还没有。代码与离线测试完成（`tests_translate.py` 65 项断言，
零 API 调用），细节记在各项的「进行中」行里。

**依赖变更**：新增 `anthropic>=1.2`，已更新 `requirements.txt`。
这是第 2 节「不引入新依赖，除非任务明确要求」的一次合规引入——
F1 明确要求调 LLM API。⚠️ 该 SDK 基于 **httpx2**，与 `core/http.py` 用的
`httpx` 是两个不同发行包，已实测共存无冲突（httpx 0.28.1 / httpx2 2.12.0）。

**`config.toml` 的 `[translate]` 段大改**，新增连接类参数：
`base_url` / `api_key_env` / `auth_style` / `extra_headers` / `max_tokens` /
`timeout_seconds` / `max_retries` / `request_gap_seconds`，
以及两个默认注释掉的 `effort` / `temperature`。
原有的 `provider` 字段已删除——只支持一种协议形态，留着会误导。

**密钥管理**：从环境变量读，变量名可配；回落到项目内 `.env`（已 gitignore）。
**密钥不得进 `config.toml`**，该文件进版本库。这条已写进 `MANUAL_STEPS.md`。

**新增 `MANUAL_STEPS.md`**（用户要求）：把 A3/A4/A5、B1/B3/B5、F 组配置与试跑、
以及 G1 的预告，全部写成「怎么做 → 成功长什么样 → 不对时怎么办」的三段式，
附命令速查、产物位置、以及「卡住时给我什么」。
**后续任务凡是需要用户操作的，都要同步更新这个文件**，
否则用户手上的指南会和实现漂移。

### 2026-08-29 · 翻译提示词重写（用户提议，评估后采纳）

用户问「是否需要给 LLM 写一份详尽的英译德 Prompt 以规范行为」。评估结论：
**需要，但价值集中在少数几处**——原提示词有 1 个真实错误和 3 个真实空白，
其余部分再加长只会稀释信号。当代模型对过度规定的提示词是**负收益**，
所以本次只补「模型不可能自己知道的决策与事实」，不写「要准确、要通顺」这类话。

**修掉的错误**：原提示词写着「价格写成 19,99 €（逗号小数、欧元符号在后）」。
**源文案是美国站的，金额是美元。** 让模型把 `$49.99` 写成 `49,99 €`
是擅自改定价——那是商务决策，不是翻译决策。这条原本会**稳定产出错误译文**，
而且错得很隐蔽（格式看着完全正确）。
现在的规则：**货币金额与数字尺码一律不换算、原样保留**；物理量（in→cm、
lb→kg、°F→°C）可以换算但不得虚增有效位数。

**补上的三个空白**（都在 `config.toml` 里可配，渲染进提示词）：

| 配置项 | 为什么模型自己定不了 | 默认值与理由 |
|---|---|---|
| `address_form` | 英文 "you" 不含 du/Sie 的信息，不定死就篇篇自己挑，整个 feed 语域是散的 | `du`——**用户已确认**（消费品牌在 IG/FB 上的德语主流） |
| `gender_style` | 德国市场的真实分歧点，是品牌立场不是语言问题 | `neutral`——**用户已确认**（改写避开人称名词，不站队） |
| `[translate.glossary]` | 术语一致性。没有它第 1 篇 `Kapuzenpullover`、第 12 篇 `Hoodie` | 空表，等 B 组抓到真实文案后填 |

> `address_form` 与 `gender_style` 是**用户 2026-08-29 明确拍板的品牌决策**，
> 不是我设的默认值。要改需要用户重新确认，并且改完必须 `--force` 重译全量
> ——否则新旧两种语域会混在同一个 feed 里。

外加 `anglicism_policy`（德语营销对英语借词容忍度高，但过量显廉价）。

**结构变更**：提示词从 Python 搬进 **`prompts/translate_de.md`**，
用 `{{ADDRESS_FORM}}` 等占位符注入配置。理由：改翻译行为不该动 Python，
营销同事要能自己改；而且它已经长到塞进源码会把模块变成一坨字符串。
文件头的 HTML 注释是给人看的使用说明，渲染时会剥掉不发给模型。
渲染后若仍有未替换的占位符，直接报错——静默发出带 `{{...}}` 的提示词
是最难发现的一类故障。

**顺带修掉一个我自己引入的缺陷**：原实现按每篇帖子重算风格示例
（`exclude_id=当前篇`），导致 system prompt **篇篇不同**——既完全拿不到
提示词缓存，也让不同帖子的语域基准出现漂移。改为**每账号只算一次**。
某篇碰巧成为自己的风格参照是无害的（提示词已明确"不要翻译示例"），
这点冗余远比缓存全失效划算。测试里加了断言：同一账号内所有调用的
system prompt 必须完全一致。
同时加了 `[translate].prompt_cache` 开关（默认关，兼容网关未必支持
`cache_control`）。

**新增 `numeric_flags()` + review.md 标注**：提示词刻意不换算金额与尺码，
那些数字会原样留在德语译文里。代码用正则把它们识别出来，
在终端打 `⚠需人工确认`、在 `review.md` 里逐篇标出并多给一个勾选框、
抬头统计总篇数。**不这样做的话，"刻意不换算"就变成了"悄悄留了个坑"**。
⚠️ 正则要同时匹配符号前置（`$50`）和德式后置（`19,99 €`）——
后者正是我们自己要求的格式，第一版漏了，测试抓到了。

**新增 `--show-prompt`**：打印渲染后的完整 system prompt，不调 API。
用途是让用户（和懂德语的同事）在花钱之前先审提示词本身。

`PROMPT_VERSION` 从 1 提到 2。测试从 65 项增至 **120 项**，
新增覆盖：模板渲染、占位符无残留、语域配置真的会改变提示词（换成 Sie
后 du 形式的指令必须消失）、无效配置值直接报错而非静默走默认、
术语表渲染、11 种文本的数字识别（含 3 种不误报）、review.md 标注。

**一处踩到的坑记一下**：模板的「常见错误」表原本举了一个 Sie 形式的
正确示范，结果在 `du` 配置下，提示词里会出现一句用 Sie 的「✅ 正确」——
这会直接干扰模型。已改成不指定具体形式的表述。**示范性内容也必须
跟着配置走，不能写死。**

### 2026-08-29 · 美元金额：从「提示词要求」升级为「代码强制」

用户复述并强调了这条规则（保留原来的美元数值和符号，不得改成德国金额）。
借这次复查发现提示词里**有一处自相矛盾**，并把这条规则做成了机器可验证的。

**发现的矛盾**：第 4 节德语排版表里有一行
「货币符号 → `19,99 €`（符号在后，中间一个空格）」，
与第 3 节「金额原样不动」直接打架。模型完全可以「合规地」把 `$49.99`
写成 `49,99 $` —— 币种没换，但数值写法与符号位置都变了，仍然违反要求。
**已删除该行**，并在排版表下方显式声明「本表不适用于货币金额」，
第 3 节改写为「**逐字符原样复制**」，用一张三行表把
`49,99 €` / `49,99 $` / `$49,99` 三种错法逐一点名。

**新增 `money_preserved(src_en, text_de)`** —— 提示词只是要求，模型可能不听，
所以必须有一道机器检查。做法是把原文里每一处金额 token 抽出来，
要求它在译文里**原样出现**（只忽略空白差异，不做任何数值或格式归一）。
于是下面四种都会被拦下来，而不只是最明显的换币种：

| 原文 | 译文 | 判定 |
|---|---|---|
| `$49.99` | `49,99 €` | ❗ 换了币种 |
| `$49.99` | `49,99 $` | ❗ 币种没换，写法与符号位置全变 |
| `$49.99` | `$49,99` | ❗ 只改了小数点 |
| `$49.99` | （没有） | ❗ 金额整个丢失 |

违规在三处 surface：翻译时逐条打 `❗金额被改动` 并点名具体金额；
跑完打汇总；`review.md` 抬头统计违规篇数、每篇加警示块与一个 **必查** 勾选框。
译文里凭空出现 `€/EUR` 时给的是更具体的判断（「八成是被换算了」），
否则给通用提示，不乱下结论。

**为什么值得做到这个程度**：金额被悄悄换算是本项目里最贵的一类错误——
格式看着完全正确，人工审校几十篇时极易滑过去，而错的是价格。
其它翻译问题是文案问题，这个是商业事故。

正则实现上的一个细节：金额 token 用 `\d+(?:[.,]\d+)*` 而不是 `[\d.,]*`，
后者会把句尾的句号一起吃进来（`$50.`），导致「原样出现」的比对因为
一个标点而误报。测试里有专门一条覆盖它。

`PROMPT_VERSION` 提到 3。`tests_translate.py` 新增两组断言
（金额保留强制 + 排版规范与金额规则之间不得留下矛盾），该套合计 **142 项**；
六套离线测试合计 **228 项**。

### 2026-08-29 · 目录重组（用户要求）

根目录原本平铺 6 个 `tests_*.py` + 4 个 `.bat` + 3 个 `.md`，共 13 个文件。
按类别归入子目录：

| 去处 | 内容 |
|---|---|
| `docs/` | `IMPLEMENTATION_PLAN.md`、`MANUAL_STEPS.md`、`HANDOFF.md` |
| `tests/` | 全部 `tests_*.py` |
| `scripts/` | 全部 `.bat` |

`README.md` 留在根（它是入口文档），`translate.py` 也留在根
（它是顶层流水线阶段，与 `routes/`、`publish/` 平级；搬进 `tools/`
会和 tools/「.bat 的实现」这个定位冲突）。

连带改动：

1. `.bat` 里 `cd /d "%~dp0"` → `cd /d "%~dp0.."`（scripts/ 的上一级才是项目根）
2. 测试的 `sys.path.insert(0, ".")` → 锚定 `Path(__file__).parent.parent`，
   否则从 `tests/` 里跑就 import 不到 `core`
3. `tools/setup.py` 的 glob 从 `ROOT/"tests_*.py"` 改为 `ROOT/"tests/tests_*.py"`
4. 所有面向用户的 `.bat` 提示加 `scripts\` 前缀（共 24 + 28 处）

**这一步里我自己造了三个 bug，记下来免得重犯**：

1. 往 `.bat` 里写了中文注释（21 个非 ASCII 字节）——**违反了自己立的规则**。
   而且 `cd /d "路径" REM 注释` 会让 `cd` 把 REM 当成路径的一部分，
   注释必须单独一行。
2. 批量替换时用了 heredoc，`"scripts\\run_backfill.bat"` 里的 `\\` 被
   shell 吃成 `\`，Python 再把 `\r` 解释成回车，写进了 5 个源文件。
   **再遇到含反斜杠的批量替换，写成独立 .py 文件跑，不要走 heredoc。**
3. 修补脚本用 `read_text()` 读回来，通用换行转换把那个裸 CR 变成了真换行，
   于是字符串在中间断开 → 5 个文件 SyntaxError。
   修补前应先 `ast.parse` 确认破坏范围，而不是假设替换成功了。

**Review 修掉的四项**（用户明确要求只修与功能紧耦合的问题，不做边界扩展）：

| # | 位置 | 症状 | 严重度 |
|---|---|---|---|
| 1 | `translate._FENCES` | `"```de"` 排在 `"```deutsch"` 前面，抢先匹配只切 5 字符，译文变成 `utsch\n...` | **污染正式产出** |
| 2 | `translate.load_translated` / `store._load_rows` | 合法 JSON 但非对象的行（如 `[1,2]`）抛未捕获 `TypeError`，而两处都自称"脏输入不崩" | 中断整批 |
| 3 | `[translate].target_lang` | 读进 `Settings` 后从未使用——提示词模板是德语专用的，这是个改了不生效的假承诺 | 误导 |
| 4 | `--account` 拼错 | 报"先跑回填"，把人引向错误原因 | 排查成本 |

第 1、2 项加了回归测试（围栏四种写法各一条 + manifest 三种坏行）。
第 3 项是**删除**而非新增：留一个不生效的配置项比没有更糟。

**刻意没动的**（报告了但按"精简优先"不改）：
`Settings` 有 20 个字段（配置对象本来就该这样）；
`integrity.check_incomplete` 只是转发 `arc.needs_media()`（Fowler 的 Middle Man，
但**计划原文就是这么规定的**，仓库规范压过通用坏味道）；
`run_review` 里 `money_preserved` / `numeric_flags` 各算两遍（正则开销可忽略）；
`PORT_WAIT_SECONDS` / `CHANNEL_TIMEOUT` 等模块常量没进 config.toml
（它们不是运维要调的旋钮，第 2 节"参数进 config"针对的是可调参数）。

**一个连带发现（不是我造的，是加前缀才暴露的）**：
`"scripts\run_backfill.bat"` 写在普通字符串里，`\r` 是合法转义会静默变成回车；
`"scripts\start_chrome.bat"` 的 `\s` 是非法转义，触发 `SyntaxWarning`
（未来 Python 版本会升级为 `SyntaxError`）。
已把 7 个模块 docstring 转成 raw 字符串、代码里的字符串反斜杠写成两个。
验证方式：`python -W error::SyntaxWarning -c "import ..."` 全部通过。

### 2026-08-29 · 输出编码缺陷（新会话接手后发现），以及 C2 的第一份真实证据

接手会话按交接文件复核基线时，`tests_translate.py` 直接崩在
`UnicodeEncodeError: 'gbk' codec can't encode character '\xdf'`。
不是测试写坏了，是**平台假设又一次落空**——与 A1 那次同源。

**故障机理**：本机 ANSI 代码页是 **936**。Python 在 Windows 上只有当 stdout
连着**真实控制台**时才走 Unicode API (WriteConsoleW)；一旦被重定向到管道或文件，
就回落到 locale 编码。实测 `⚠` `❗` `✅` `❌` `ß` 在 cp936 下**一个都编码不出来**。

这个故障有个很坏的性质：**双击 `.bat` 时永远看不到它**（那是控制台），
只在真正需要它可靠的时候才发作：

| 场景 | 后果 |
|---|---|
| **E1**（任务描述就要求把增量输出**追加到 `state/delta.log`**） | 那就是重定向。第一次打 `⚠`/`❗` 即崩，**崩在网络请求已发出、归档尚未写入之际** |
| **E3**（计划任务无人登录时触发） | 连控制台都没有，同一个失败模式 |
| `translate.py` 的 `❗金额被改动` | 全项目最重要的一条安全信号，而它恰好就是会崩的那一行 |
| 任何"把输出捞回来看"的排查 | 直接失效（接手会话就是这么撞上的） |

**修法（两处都做，各管一条路径）**：

1. 新增 `core/console.py::force_utf8()`，在各入口调用一次
   （`translate.py` / `routes/backfill.py` / `routes/delta.py` / `core/notify.py`
   / `tools/setup.py` / `tools/start_chrome.py` / 全部 `tests_*.py`）。
   用 `errors="replace"`：一个符号打不出来不该让整批抓取失败，宁可显示成 `?`。
2. 四个 `.bat` 加 `set "PYTHONIOENCODING=utf-8"`（纯 ASCII，未违反 .bat 约定）。
   `tools/setup.py` 另外 `os.environ.setdefault` 同一变量，给测试子进程兜底。

**顺带纠正一处旧实现**：`tools/setup.py` 与 `tools/start_chrome.py` 原本只写了
`sys.stdout.reconfigure(errors="replace")`——**只改了错误处理、没改编码**，
于是不崩了但把字符静默换成 `?`，而且完全没管 stderr。这也是为什么
`setup.bat` 一直显示"全绿"却掩盖着这个问题。已统一改成 `force_utf8()`。

**验证**：全部 `.bat` 补丁后仍是纯 ASCII + 无裸 LF（字节级断言）；
10 套离线测试在 **stdout 被管道重定向**的条件下 334 项全绿（补丁前 `tests_translate.py`
在同样条件下必崩）。`python -W error::SyntaxWarning -m compileall` 通过。

补丁脚本写成独立 `.py` 跑、`.bat` 按字节处理、改完 `ast.parse` 验证——
这三条正是上一条同步记录里踩过的坑，这次照着做没有复现。

**同一批完成的还有 C2**，结果记在 C2 的「进行中」行里。一句话摘要：
代码与 52 项离线断言全过，但**真实探针第一次请求就吃到 429**，
所以 C2 不勾选。这份真实证据下调了交接文件里
「Instagram 登出增量把握较大」的判断——那句话写的时候还没有任何真实证据。

### 2026-08-30 · 首次真实回填的结果：解析器在真实响应上以三种方式失效

B1/B3 跑通了（用户实机，两份 capture 都在），但**产出的 manifest 不可信**。
逐条核对后确认三类缺陷，全部有真实数据佐证，不是推测。
根因分析在 `docs/CODE_REVIEW.md` 第 7 节（CR-12 ~ CR-15），
具体修法写在 B2 / B4 / B7 / B8。这里只记结论与它改变了什么。

| 缺陷 | 实测规模 | 根因一句话 |
|---|---|---|
| 跨账号污染 | IG 266 条（来自 195 个账号）、FB 1 条 | 构造函数把传入的 `account` 直接写进 Post，从不看节点自身归属 |
| 轮播子项被当成独立帖 | IG 478 条 | `is_iphone_struct` 判的是「键存在」而非「值非空」，子项的 `code` 键在、值是 `None` |
| FB 视频帖被当成抓取失败 | FB 20 条 | `from_fb_story` 只抽图片，不识别视频 |
| 人工滚动无进度反馈 | FB 可能没滚到底 | 终端只显示响应数，对操作者无意义 |

**真实数字：IG 756 篇、FB 46 篇（其中 20 篇视频）**，不是 manifest 里的 1500 / 47。

**这次事件对项目认知的三处修正**：

1. **"离线测试全绿"和"能处理真实数据"是两件事，而且差得很远。** 282 项离线断言
   全过，但测试构造的是"结构正确的单一账号响应"；真实响应里混着推荐内容、
   被 @ 的 UGC、轮播子项和视频帖。**测试覆盖的是我们想到的形态，不是真实的形态。**
   B2/B4 要求新增的断言必须**用真实结构构造**，原因就在这里。
2. **`walk()` 全树搜索的代价这次显形了。** 它对字段路径漂移的抗性是真的
   （FB 正文、时间、图片路径一次就抽对了），但它也会把任何"看起来像帖子"的
   节点捞进来。**抗漂移和精确性是一对权衡，之前只记了前者。**
   补救不是放弃 `walk()`，而是给它加一道归属校验。
3. **`backfill.py` 那个"解析前无条件转储"的兜底第一次兑现了价值。**
   如果没有它，这次就是用户白滚 40 分钟、且无从排查。
   **这条设计以后不许优化掉。**

**顺带说明为什么 B5/B6 没往下做**：它们的验收是数媒体、盘点总数。
基于当前这份 manifest 去数，只会把错误的数字写进记录。已在 B6 下标注被 B7 阻塞。

### 2026-08-30 · 归档布局重构（新增 J 组，用户提出）

用户提出：能否把一篇帖子的正文、配图、元信息放进一个独立文件夹，
用文件夹名区分不同时间发布的帖子。

**评估结论：采纳，而且它比我先前建议的"manifest 当真相源 + 生成只读视图"更好。**
我先前反对的理由是"文件夹当真相源等于自己发明一个更差的数据库"——
**这个反对在本项目的规模下不成立，已收回**：

- 约 800 个文件夹（IG 756 + FB 46），Explorer 与遍历都毫无压力。
  那个反对意见要到几万篇量级才成立。
- "幂等重跑要和文件夹对账"的成本，与现在读 `manifest.jsonl` 重建状态是同一量级，
  不是新增负担。

**真正让判断反转的是：下游有人要动这些文件。** 计划固化了「图内英文文字本期走
人工处理」，设计同事会把替换好德文的图交回来。扁平的 `media/<post_id>_<n>.jpg`
下这个流程无法进行。**存在人工编辑环节的数据，就该按人能操作的粒度组织。**

布局、命名规则与三条必须写死的规则见 J1。其中两条容易被后来者改掉，
在此重复：

- **`post.json` 是真相，`manifest.jsonl` 是派生索引；冲突时以文件夹为准。**
  方向必须单一，否则会退化成"两个都不可信"。
- **译文的真相源仍是账号级的 `translated.jsonl`。** 考虑过把译文直接放进
  每帖文件夹（那样更自洽），但会动到一个已经拍板的决策
  （重跑抓取不得冲掉花钱买来的译文），**不值得为整洁去动它**。
  文件夹里的 `text_de.txt` 是派生副本。

### 2026-08-30（下午）· 缺陷修复 + 归档重建 + 布局重构，一次做完

按用户指定的顺序执行：B2/B4 修解析器 → B7 离线重放重建 → B8 进度显示 →
J 组布局重构。全部完成并验收。**用户没有重新滚动过一次页面。**

**修复前后的数字对照**（这是这一天最该记住的一张表）：

| | 修复前 | 修复后 | 差在哪 |
|---|---:|---:|---|
| Instagram 帖子 | 1500 | **756** | −478 轮播子项、−266 他人帖 |
| Instagram 空正文 | 487 | **9** | 空正文几乎全是被误当成帖子的轮播子项 |
| Facebook 帖子 | 47 | **46** | −1 他人帖 |
| Facebook 视频帖 | 0（被记成抓取失败） | **18** | `from_fb_story` 现在识别视频 |
| `media_complete=False` | FB 20 | **0** | 视频帖不再被误判为待补 |
| 图片文件 | FB 74 / IG 1184 | FB 70+4 / IG 648+536 | **一张没少**，多出来的进了 `_orphan_media/` |

**新增的三个工具**：

| 文件 | 作用 |
|---|---|
| `tools/replay.py` | 用 `_capture_*.json` 离线重建归档（B7），媒体按 URL 重新关联、不重新下载 |
| `tools/layout.py` | `migrate`（扁平→每帖一个文件夹）/ `reindex`（posts→索引）/ `index`（生成 index.html） |
| `routes/backfill.py::ScrollProgress` | 滚动时显示"已抓到 N 篇 · 最早 YYYY-MM-DD · M 秒没有新内容" |

**四条值得写进项目记忆的结论**：

1. **"离线测试全绿"和"能处理真实数据"是两件事。** 282 项断言全过的同时，
   真实归档里混着 267 条他人帖、478 条轮播子项。测试覆盖的是我们想到的形态。
   `tests_parse.py` 现在有一整段**照抄真实响应结构**的断言，就是为了钉住这一点。
2. **`walk()` 全树搜索是一对权衡，之前只记了好的一半。** 抗字段路径漂移是真的
   （FB 的正文、时间、图片路径一次就抽对了），代价是任何"看起来像帖子"的节点
   都会被捞进来。补救不是放弃它，而是加一道归属校验。
3. **判定要判值，不要判键。** `"code" in d` 与 `d.get("code")` 差一个字符，
   差 478 条脏数据。轮播子项里那个键**在**，值是 `None`。
4. **"解析前无条件转储"第一次兑现价值。** 没有它，这次要么白滚 40 分钟，
   要么带着脏数据往下走。**这条设计不许优化掉。**

**顺带修掉的一个前置问题**（同日上午）：输出编码。本机代码页 936，
Python 的 stdout 一旦被重定向就回落到 GBK，而 `⚠ ❗ ✅ ❌ ß` 一个都编码不出来。
双击 `.bat` 时永远看不到这个故障，只在计划任务、日志重定向这些真正需要可靠的
场合发作——而 **E1 的任务描述本身就要求把增量输出追加进 `state/delta.log`**。
已由 `core/console.py::force_utf8()` + 四个 `.bat` 的 `PYTHONIOENCODING` 修掉。

**同日确立的一条架构级结论**：用户确认 **FB/IG 在没有登录态时直接报错**，
登出增量的前提不成立。C 组因此进入架构岔路口，三条路与我的建议写在 C 组开头的
⛔ 段落，**等用户拍板**。C2 的代码保留（它本身是对的，只是端点关了）。

### 2026-08-30（晚）· 增量方案定案：B（登录态 + CDP 附着）

用户在 A / B / C 三个方案里**选了 B**。我当时建议 A，用户选 B，
**这是用户的决定，不要再重新讨论**。本条记录它改了什么、以及代价是什么。

**为什么需要这次决策**：原设计的"增量完全登出"依赖一条关键事实——
`web_profile_info` 对登出访客可用。2026-08-30 实测首次请求即 429，
用户确认原因是"没有登录态就直接报错"。该事实不成立，方案必须换。

**B 意味着什么**：增量复用回填那条 CDP 通道（附着到人工登录过的专用 Chrome），
每天跑一次。

**代价，写清楚以免将来有人当成免费的**：

> 封号风险从「一次性敞口」变成「累积性敞口」。
> 单次会不会被封、和 365 次里会不会被封一次，是两个量级的问题。
> 抓取小号被封是本项目唯一不可恢复的失败模式——**回填与增量会同时断掉**。

**因此新增了 C7「累积风险缓解」，它是方案的组成部分而不是锦上添花**：
随机化触发时刻、抓取深度上限（只滚几屏，不滚到底）、滚动节奏拟人、
异常即停不重试、频率可降级、失败预算、抓取与发布 profile 隔离。
七条的参数都进 `config.toml` 的 `[delta]`，**当前以注释形式预置**——
留成真值而代码不读，就是项目里已经犯过一次的"改了不生效的假承诺"
（`target_lang` 那次）。

**两条红线没有变，别顺手一起改了**：

1. ❌ **仍然不得实现自动登录。** B 说的是"复用人工登录留下的会话"，
   不是"让程序去登录"。全项目仍然只有一条登录路径。
2. ❌ **回填仍然是人工滚动。** 增量走登录态不等于回填可以自动滚。

**连带修改的条目**：第 0 节决策表与方案变更说明、C 组整组重写
（C2/C3 从"登出"改为"登录态"，新增 C7）、C1 标记为"方案 B 下用不上但保留"、
D 组头部（告警在 B 之下更重要 + 静默天数阈值必须重设）、
E 组头部（三个新前提：Chrome 必须在跑、会话过期只能人修、抖动放 Python 侧）、
E1（`.bat` 必须设 `PYTHONIOENCODING`）、E3（Chrome 拉起方式、不要勾"不管用户是否登录"）、
附录 A 依赖图、附录 B 验收总清单、`config.toml` 的 `[delta]` 段。

**留给下一个会话的第一件事**：C2。骨架照抄 `routes/backfill.py`，
但不要人工介入、不要滚到底。`routes/delta.py` 里现有的登出实现**保留不删**——
端点若重开，切回去是风险更低的路径。

### 2026-08-30（夜）· C 组落地：代码与离线测试完成，真实验收待跑

上一条留的"第一件事"做完了。C2–C7 全部实现，**C7 勾选（它的四条验收本来就是
mock 验收），C2–C6 标进行中**——它们的验收都要对真实账号跑一次抓取，
而用户拍板"全部写完 + 离线测试全绿后，一次性实测"，把真实露面次数压到最少。

**三个由用户拍板的决定**（写在这里是因为：约定不写进文档就会丢）：

| 问题 | 用户的选择 | 影响到哪 |
|---|---|---|
| 专用 Chrome 没在跑时怎么办 | **自动拉起** | `[delta].autostart_chrome = true`；`core/chrome.py::launch()` |
| 「长期零新增」告警阈值 | **按平台分开：FB 7 天 / IG 21 天** | D3 落地时改 `[integrity]`，写法见 D 组头部 |
| 真实验收的时机 | **全部写完后一次性实测** | C2–C6 暂不勾选；实测清单见 `MANUAL_STEPS.md` |

**一个被纠正的"事实"**：计划与交接文件里多处写着"该账号实测一个多月没发新帖，
天天全速跑没有收益"。按归档逐条统计，**这句话只对 Instagram 成立**：

| | 最新一帖 | 距 2026-08-30 | 间隔中位 | 间隔 p90 |
|---|---|---|---|---|
| Facebook | 2026-08-25 | **4.4 天** | **1.00 天** | 2.10 天 |
| Instagram | 2026-07-15 | 45.4 天 | 1.61 天 | 5.89 天 |

**Facebook 是日更的。** 这条错误事实原本会推出两个错误结论：
"增量没什么用"（对 FB 完全不成立）和"一个阈值就够"（对两边都不成立）。
它同时是 C7 降频阈值与 D3 告警阈值都改成**按平台配置**的直接原因。
另外：归档里 FB 最新是 8-25，**大概率已经缺了之后的几篇**——
这正好是首次实测的天然靶子，抓不到就是有问题，不是"没新帖"。

**结构上的三处改动**：

1. **新增 `core/capture.py`**：`INTEREST` / `Collector` / `download_media()` /
   `prune_captures()` 从 `routes/backfill.py` 抽出，两条路径共用。
   动机不是消除重复代码，是**防语义漂移**——CR-04 那条
   "下载失败必须留下 `media_complete=False`" 只在一处生效的话，
   另一条路径会安静地把失败记成完整。
   `Collector` 顺带新增 `blocked_status()`：401/403/429 时页面壳照常渲染、
   URL 完全正常，只看 URL 会把"被拦"读成"这号没发帖"。
2. **`core/chrome.py` 新增 `launch()`**，`tools/start_chrome.py` 改为共用。
   同上：增量拉起 Chrome 与那个脚本做的是同一件事。
   ⚠️ 这不是自动登录，会话仍然只由人产生一次（红线 1 未松动）。
3. **`[delta].runs_per_day` 删除**。它没有任何代码读取，而增量的实际频率由
   `stale_after_hours` + 计划任务的触发器决定。留一个改了不生效的旋钮
   比没有更糟——`target_lang` 那次已经付过这个学费。

**C 组落地时新想到、计划里没有的一条缓解措施**（已记进 C7）：
`DeltaBlocked.hard` —— 登录墙/401/403/429 时**同一次运行不再敲另一个平台**。
两个平台共用同一会话、同一指纹，IG 刚被限流就转头敲 FB，
正是"异常即停"要防的行为。没跑的那个平台也记一次失败，
否则连续被拦时失败预算永远攒不满，自动运行永远停不下来。

**离线基线：11 套 482 项，全绿**（stdout 被重定向的条件下同样全绿）。
新增 `tests/tests_delta_logged_in.py`（82）。`ruff` 在本次改动的文件上零告警
（仓库总数从 14 涨到 30 是 ruff 版本差异，不是本次引入——全部落在
`translate.py`、旧测试与 `_deprecated/`）。

### 2026-08-30（深夜）· 首次实测 → 一个误判 → 用户线索揪出真正的 P0 → D3/E 组落地

这一段发生的事比它的篇幅重要：**用户的一句怀疑，推翻了我当天写下的三个结论。**

#### 1. 用户跑了实测，四次输出全是"新增 0 篇"、零报错

| | 候选 | 本账号 | 丢弃 | 看到的日期范围 | 归档最新 |
|---|---:|---:|---:|---|---|
| Facebook | 6 | **6** | 0 | 2026-08-16 ~ **2026-08-25** | 2026-08-25 |
| Instagram | 39 | **1** | 38 | 2026-06-06 ~ **2026-06-06** | 2026-07-15 |

FB 是对的（"新增 0 篇"是真的没新帖），**C3 与 C6 的验收因此通过并勾选**。
IG 明显不对，但**从输出里完全看不出来**——这本身就是缺陷（CR-18）。

#### 2. 我据此下了一个错误诊断

我判断"IG 首屏时间线随 HTML 下发、不走 XHR"（CR-16），并加了
`harvest_embedded_json()`；又判断"滚动可能没生效"（CR-17）。

**两条都不成立。** 我犯的错误是：看到"本账号帖子只有 1 篇"就去猜数据从哪来，
**而没有先问"那 38 条到底是什么"**——答案就在同一份 capture 里。

#### 3. 用户的线索：「很多帖子是两个账号共同发的，会不会我们抓的这个只是转发角色？」

查证属实。IG 的**合作帖**由一方发布、双方主页同时显示，
合作关系在 `coauthor_producers[]` 里，而 `partition_by_owner` 从来不看它。

| 类别 | 篇数 | 旧判定 |
|---|---:|---|
| `user.username == neakasa.tech` | 756 | 保留 |
| **coauthor 含 neakasa.tech** | **263** | **丢弃（错）** |
| 真正无关 | 3 | 丢弃（对） |

**一条缺陷解释掉了三个结论**：CR-16 那 38 条里 35 条是合作帖（时间线一直在
XHR 里）；"IG 一个多月没发新帖"是假象（合作帖最新 2026-08-27）；
"回填混进 266 条他人帖"里 263 条就在我们自己主页上。

归档重建：**756 → 1019 篇**，图片 742 张全部关联、94 张从 `_orphan_media/`
捞回、**0 张丢失**，用户没有重滚。可进翻译的从 747 涨到 **1010**。
详见 CODE_REVIEW 的 CR-19。

#### 4. 连锁修正：告警阈值当天改了两次

第一版 FB 7 / IG 21 是按"IG 已停更"标的。修完合作帖重新统计，
**两个账号都是日更**（近一年间隔中位 FB 1.00 / IG 0.99），
于是 IG 从 21 收到 **10**。⚠️ 这个值用户批准过第一版，
**基础数据变了所以我改了，并已明确告知**——不是悄悄改。

#### 5. D3 与 E 组落地

- **D3 完成**。核心不是"检查得全"，而是**只报新出现的问题**
  （缺口只看最近 60 天且只报一次、媒体不全只在变多时报、零新增每 7 天最多一次）。
  ⚠️ 计划里的验收步骤（改 `consecutive_quiet_days`）**做不出来**，
  那个字段每次都会被重算；正确做法是改 `last_new_at`，已更正。
- **E1 / E2 完成**。`run_delta.bat` 的编码验收实测通过；
  **五个 `.bat` 的"纯 ASCII + CRLF + 无 BOM"现在有测试守了**——
  计划原文说"靠人守"，而靠人守的东西 2026-08-29 就破过一次。
- **E3 工具完成但没有注册**，这是刻意的：注册的那一刻起每天就会真的去访问
  一次 FB/IG，而合作帖修复后还没实机跑过。装一个每天准时失败的东西比不装更糟。

#### 这一轮最值得记住的三条

1. **"多看一个字段"和"猜数据从哪来"的差距。** `coauthor_producers`
   在**每一个**节点上都存在，翻一眼就能看到；我却先写了一整条新路径。
2. **"离线测试全绿"第三次没挡住真实数据**（前两次是 CR-12/13、CR-18）。
   三次的共同点：**测试构造的输入里没有那个字段**。
3. **用户对自己业务的观察，比我对数据的推理更可靠。**
   这次的 P0 是用户发现的，我只是去验证了它。

**离线基线：12 套 578 项，全绿**（stdout 重定向条件下同样全绿）。
新增 `tests/tests_schedule.py`（47）、`tests_parse.py` 的「真实结构 5」（7）、
`tests_integrity.py` 的 `run_checks` 段、`tests_delta_logged_in.py` 的
[3b]/[4b]/[4c] 三段。

### 2026-08-30（晚）· 合作帖判定的复核与加固：把"这次修对了"变成"下次坏了能被发现"

**用户要求**："合理解决当前 Instagram 上存在爬取的当前账号不是原帖发帖者
而是合作转发发布者这种情况导致逻辑误判漏掉这种帖子的问题。"

CR-19 已经修掉了根因（`partition_by_owner` 不看 `coauthor_producers`）。
本轮做的是另一半，**全程零真实访问**，用已有的四份 capture 离线做。

#### 1. 先复核，不假设上一轮说的都对

| capture | 候选 | 保留 | 原创 | **合作** | 丢弃 |
|---|---:|---:|---:|---:|---:|
| IG 回填 | 1022 | 1019 | 756 | **263** | 3 |
| IG 增量 | 39 | 36 | **1** | **35** | 3 |
| FB 回填 | 47 | 46 | 46 | 0 | 1 |

判定成立。**增量那份比回填更极端**：36 篇里 35 篇是合作帖，
本账号自己发的只有 1 篇——这解释了 CR-19 之前 IG 增量为什么退化成"只看到 1 篇"。

三个"还有没有别的漏网形态"的问题也一并用真实数据查掉了：

- 被丢弃的节点里 **没有任何一个**在 `usertags` 等字段里提到本账号；
- `invited_coauthor_producers` 的**键**在 1022 个节点全有，**值全是空数组**
  ——CR-19 原文说"两个字段都存在"，准确说是**键**存在，
  "不收被邀请的"这条选择至今没有真实反例可验证。选择不变，注释补了限定；
- **Facebook 上不存在这种形态**：139 个 story 节点的 `actors` 长度全是 1，
  `attached_story` 只出现 1 次且 actor 为空。不需要对称实现。

#### 2. 三个残留缺口（CR-20 / CR-21 / CR-22）

都在 `core/parse.py`，都是"按形态推出来的"而非观测到的，所以记 P2：

- `_merge_post` 合并 `coauthors` 用"空了才补"，两份响应各给一个子集时
  会把目标账号挡在外面 → 改成**取并集**；
- `ig_coauthors` 只认 `{"username": ...}` 条目，IG 若改成裸字符串数组
  会**一次性退回 CR-19** → 两种形态都认；
- `on_timeline_of` 依赖调用方先转小写，直接调用并传入带大写的账号名
  会**静默丢光全部帖子** → 函数自己归一化。

#### 3. 真正的那一半：丢弃从此不再是静默的（CR-23 / CR-24）

**让 CR-19 拖那么久才被发现的，不是修不好，而是丢弃完全没有声音。**
程序丢掉 263 篇，`_rejected.jsonl` 只写不读，输出里一个字都没有。

- **新增第四项完整性检查**：`known_partners()` + `check_dropped_partners()`
  ——被丢弃的节点里，作者是**已知合作方**的那些。
  名单同时取自"合作帖的 owner"与"自家帖的 coauthors"，实测 IG 有 **210 个**。
- **零误报**：210 个合作方 vs 历史上被丢弃过的 6 个账号，**交集为空**。
- **灵敏**：把 `on_timeline_of` 退回旧实现后，回填那份报 **263**、
  增量那份报 **35**；**归档为空的冷启动**也能报（34 / 29），
  因为名单同时取自本次留下的那批。
- **摘要拆分**：`新增 0 篇 · 本账号 36 篇（原创 1 · 合作 35，…）· 丢弃 3 · …`。
  只报合计的话，"合作判定失效"和"今天真的只发了一篇"长得一模一样——
  这正是 CR-18 修过的那类二义性换了个位置重新出现。
- **中止理由带指向**：`min_own_posts` 那道闸触发时会写明
  "其中 N 篇来自已知合作方 —— 优先怀疑合作帖判定失效，而不是被拦"。
  2026-08-30 那次就是因为"只看到 1 篇"没有指向性，才先去猜数据来源
  并写了一整条新路径（CR-16）。

#### 4. 这一轮的边界（没做什么，为什么）

- **没有放宽任何"宁可漏一篇自家的，不可混进一篇别人的"的判定。**
  新增的是提示，不是自动收编。哨兵响了要人去离线查，不会自动放行。
- **没有碰 C7 的七条缓解措施**，也没有改任何阈值。
- **没有替用户跑真实抓取。** 增量的解析路径在真实响应上是否成立，
  已经用 `_capture_delta_*.json` 离线验证过了；
  但**媒体下载至今没有被真正触发过**（历次都是 0 新增），
  那一条只能靠用户跑第 8 步，见 `MANUAL_STEPS.md`。

**离线基线：全绿；2026-08-30 晚测得 15 套 788 项（三条线并行加测试，数字仍在涨，以实际跑出来的为准）**（stdout 重定向条件下同样全绿）。
本轮抓取侧新增 28 项（parse +4 / integrity +10 / delta_logged_in +14）；
其余增量来自并行进行的翻译侧与一轮健壮性加固。

### 2026-08-30（晚，第三轮）· 增量离线自检工具 + DeepSeek 翻译的首次真实验收

用户两个要求：**"Instagram 合作帖这块到底解决了吗，核查并检测"**，
以及 **"DeepSeek Key 已配好，把英译德也核验一遍"**。

#### 1. 合作帖：结论是**已解决**，而且现在可以随时自证

上一轮验的是两个解析函数。这一轮新增 `tools/dryrun_delta.py`，
把 `_capture_delta_*.json` 喂给假页面，让 **`delta_once()` 原样跑完**——
只有浏览器是假的，**零网络、零写盘**。真实转储上的结果：

| | 摘要 |
|---|---|
| Instagram | `本账号 36 篇（原创 1 · 合作 35，2026-06-06 ~ 2026-08-27）· 丢弃 3` |
| Facebook | `本账号 6 篇（原创 6 · 合作 0，2026-08-16 ~ 2026-08-25）· 丢弃 0` |
| IG `--break-coauthors` | **中止**，且理由直接点名"其中 35 篇来自已知合作方 —— 优先怀疑合作帖判定失效" |

**这个工具的意义不是它今天说了什么，而是"改完解析器对不对"从此不需要
用一次真实露面去回答。** 2026-08-30 那次正是靠一次实测才发现异常、
然后又猜错了原因（CR-16）。

⚠️ 它**验证不到媒体下载**——那是增量里唯一还要真实跑一次的东西。

#### 2. 翻译：查出一个 P0，真实文案 100% 失败（CR-37，原 CR-25）

第一次拿真 Key 试译，**FB 3 篇全部失败**，报"译文被 max_tokens=4096 截断"。
单次诊断请求看到的真相：**响应里只有一个 15444 字的 `thinking` 块，
`text` 块一个都没有**。原因是关 thinking 用的
`extra_body={"reasoning": {"effort": "none"}}` **这个端点根本不认**——
不报错、也不生效。785 字的帖子换来 15000 字思考，4096 个输出额度全烧光。

三处修复（都实测过）：

1. 改用 **Anthropic 标准的顶层 `thinking={"type": "disabled"}`**
   （同一份提示词：输出 4096 → **30** token）。其余档位在这个端点上
   没有可验证的映射，**不再发那个被忽略的字段**——
   项目已经因为"改了不生效的旋钮"吃过两次亏。
2. `translate()` 把两种截断分开：有 `text` 块 = 译文太长（调大 max_tokens）；
   **只有 `thinking` 块 = thinking 没关（明确写"不要调大 max_tokens"）**。
   原来的提示是有害的：照做只会让它想得更久、账单更高。
3. `--check` 检查响应里有没有 `thinking` 块。
   以前它发过假绿灯——自检那句话短到思考着也能答出来。

修复后：`--check` 通过、**FB 3 篇 + IG 3 篇全部成功**（US$0.026）。

**F1 / F2 仍不勾选**：验收要求是"人工检查 3 篇输出"，
而**没有懂德语的人看过**。管道跑通 ≠ 译文可用。

#### 3. 留给用户拍板的一件事

IG 译文比原文短 40–55%，**全部来自话题标签被从 22 个砍到 3 个**
（`tone` 规则；正文本身德语反而略长）。这是**流量决策不是翻译问题**，
会作用在全部 1010 篇上，**本轮没有改**，已写进 `MANUAL_STEPS.md` 第 7.1c 节。

**离线基线：16 套 811 项，全绿**（stdout 重定向条件下同样全绿）。
新增 `tests/tests_dryrun_delta.py`（13 项）、`tests_translate.py` 的
thinking 截断分流与**合作帖授权提示**两段（后者此前零覆盖，见 CR-38，原 CR-26）。

### 2026-08-30（第四轮）· DeepSeek 官方 API / High thinking / 标签全量照搬

> 本节是当前有效决定；上一节保留旧 Anthropic 事故与真实试跑历史，但其中“关闭
> thinking”“配置 `max_tokens`”“标签裁到 3 个”均已被本节取代。

用户明确要求四项：直接用 DeepSeek 官方接口，不套 Anthropic 协议；不发送
`max_tokens` 等客户端输出限制；thinking 默认 High；原帖多少个标签就全部照搬。

已实现：

1. `anthropic` 依赖与 `custom_anthropic` 运行分支退出当前主干，改用 `openai` SDK
   直连 `https://api.deepseek.com/chat/completions`，Bearer 鉴权。
2. 请求显式包含 `thinking={"type":"enabled"}` 与 `reasoning_effort="high"`；
   不发送 `max_tokens`、`max_completion_tokens`、temperature 或 top_p。线级 MockTransport
   测试检查最终 URL、header 与 JSON body，而不是只检查业务层 kwargs。
3. `reasoning_content` 与可见 `message.content` 分开解析；usage 从 OpenAI 格式的
   `prompt_tokens/completion_tokens/completion_tokens_details` 归一为现有落盘字段。
4. 提示词删除所有“最多 3 个”与裁剪示范，改为数量、内容、大小写、顺序完全一致；
   新增 Unicode-aware 标签扫描和写盘前硬校验，少贴、多贴、翻译、改大小写或调序均失败。
5. `PROMPT_VERSION` 升至 5，并把版本纳入“当前有效译文”判断。因此旧 3+3 付费记录
   仍保留，但普通运行会自动重译，不需要靠人记住 `--force`。
6. High thinking 的 reasoning token 无法离线可靠估计；`--estimate` 只显示未含 reasoning
   的基础费用参考并明确要求用新版 3 条真实 usage 外推，不再把旧低思考费用叫“上界”。

验证边界：本轮只做离线/模拟传输验证，没有使用现有真实 Key 发付费请求。下一步由用户
先执行新版 `--check`，再分别试译 Facebook 3 条与 Instagram 3 条；德语人工确认和
reasoning 实际成本通过后，才允许全量。

离线基线：**16 套 815 项全部 exit 0**；另通过 `compileall`、真实归档 `--estimate`
与 `--dry-run --limit 2`（零 API）。

### 2026-08-31 · C2/C4/C5 真实验收通过；两条被真实运行顺带照出来的缺陷

#### 1. 验收结果：合作帖修复在**当天的真实响应**上成立

用户按 `MANUAL_STEPS.md` 第 8 步跑完全流程（离线四步 + 真实一次）：

| | 结果 |
|---|---|
| Facebook | 新增 1 篇，**下了 5 张图**（91–100 KB，无 0 字节） |
| Instagram | 新增 1 篇，**下了 1 张图**（323 KB） |
| **IG 那篇新帖** | `owner=neakasa.global`、`coauthors=['neakasa.tech']` —— **本身就是合作帖** |

最后一行是收口：**当天真实抓到的新帖，恰好就是修复前会被丢掉的那一类。**
判定因此不只是"在保存的转储上成立"，而是在**今天的线上响应**上仍然成立。

**媒体下载第一次被真正触发**（此前几次都是 0 新增，那段代码从没跑过），
**C2 / C4 / C5 三项验收条件到此齐了，已勾选。**

离线的 `tools.dryrun_delta` 事先给出的数字与真实抓取完全一致 ——
**这条工具因此可以替代大部分"再跑一次看看"**，这是本轮最有复用价值的产出。

#### 2. CR-39：连续性检查看不见跨越窗口边界的缺口

那次运行 FB 报了一处 5.9 天缺口（真实、不可行动：FB 那 6 天确实没发帖），
而 **IG 一条都没报**——但归档里有一处 `2026-07-01 → 07-09` 的 **8.4 天**缺口
（IG 阈值 6 天），**一次都没被报过**，只因为起点比 60 天前早了一天。

旧写法先剔窗口外的帖子再算相邻间隔，于是跨边界的缺口整个消失。
**而"一段历史根本没抓到"这类失败，洞越大起点越早，就越容易被这样剔掉**——
检查在最需要它的场景下最不灵。改成按"较晚那篇是否在窗口内"筛选。

#### 3. CR-40：离线预算漏掉账单的主导项，低估一个数量级

`--estimate` 按字符换算给出 US$1.08–5.41，而真实 3+3 篇实测显示
**输出费用的 97–99% 花在 reasoning 上**（FB 每篇中位 25 400 tok，IG 4 351 tok）。
每篇 usage 本来就写进了 `translated.jsonl`，改为**优先按真实 usage 外推**
（中位数，且只采当前 `PROMPT_VERSION` 的样本）。
全量预算从"US$1.08–5.41（不含 reasoning）"变成 **US$23.73**。

#### 4. 留给业务拍板的一个量化取舍

同一篇帖子、只改 `reasoning_effort`：`low` reasoning 373 tok，
`high` **9899 tok**，**可见译文一样长**，费用差 4 倍（全量 ≈ US$7 vs US$24）。
但 `high` 的德语细节确实更好（`low` 那版出现句中误大写与引号不成对）。
**代码不替用户定**，已写进 `config.toml` 注释与 `MANUAL_STEPS.md` 第 7 步。

#### 5. 翻译线的依赖与配置复核

- `requirements.txt` 已改成 `openai>=3.6,<4`，但 **venv 里当时并没有装**
  （`.venv` 是 uv 建的、不含 pip，得用 `uv pip install --python .venv/...`）。
  已装上 3.6.0，新版 `--check` 与 3+3 实跑通过。
- **配置项审计：7 个段全部一一对应**，既没有"配置里有、代码不读"的死旋钮，
  也没有"代码读、配置没写"的静默默认值。
- 修掉一条会误导的注释：费率表标的是 V4 **Pro** 的价，而 `model` 已是 flash。
  **故意不换成 flash 的价**——这张表只用来算最坏上界，用更贵的费率估更便宜的
  模型方向是安全的。注释已写明。

**离线基线：16 套 825 项，全绿**（stdout 重定向条件下同样全绿）。

### 2026-08-31（第二轮）· 新增 K 组（图片德语化）；G 组按新范围重写

本轮**只写计划，没有写代码，也没有发过任何付费请求**。

#### 1. 用户的四项决定

| 问 | 用户的决定 |
|---|---|
| 图片"调整"是什么 | **图内英文换德语，版式/构图/配色/产品不动**。品牌文字、Logo、费用符号、单位数值等**不得翻译，须与原图样式一致**；细分类由我按常识制定 |
| 第三方 API | **`https://api.inferera.com`**，`gpt-image-2`，文档同 AIHubMix 的 GPT-Image 页 |
| 发布目标 | **Facebook DE Page + Instagram DE，同时发** |
| 发布节奏 | **不补发历史**，只用最新几篇做端到端验证；上线后跟增量走 |

第一项**推翻了第 0 节「本期不做」里那条「图内英文文字的德语替换（一期走人工处理）」**，
已在该节标注取代关系。第四项把 G 组从"队列子系统"缩回成"发一篇 + 一个挂钩"。

#### 2. 三条核实过的接口事实（**不是推导，是查证 + 实测**）

1. **`input_fidelity` 对 gpt-image-2 必须不发送**，传了返回 400——
   该模型强制高保真处理参考图，这个旋钮已被取消。
   ⚠️ **本机 `openai` 3.6.0 的 `images.edit()` 签名里有它**，
   docstring 还写着「supported for gpt-image-1.5 and later models」。
   **那句话对 gpt-image-2 是错的。** 记进计划是因为：
   下一个会话看 SDK 签名会觉得"保真度这么关键怎么能不传"，然后加回去，
   **加回去的那一刻整组请求全部 400**。
2. **`size` 的四条约束**（边 ≤3840 / **宽高都要是 16 的倍数** / 宽高比 ≤3:1 /
   总像素 655,360–8,294,400）与我们的归档**大面积冲突**。
   对 818 个图片文件实测：最常见的 1080×1080（318 张）与 1080×1350（100 张）
   **都不是 16 的倍数**，720×720（12 张）**总像素低于下限**。
   直接 `size=f"{w}x{h}"` 会在一半以上的图上 400。
3. **网关形态已实测**：`GET https://api.inferera.com/v1/models` 返回 200 且为
   OpenAI 形态，列表里有 `gpt-image-2` 与 `gpt-image-2-free`。
   所以客户端可复用 `openai` SDK，与 DeepSeek 那条线同构。

#### 3. 一个被顺带查出来、会误导工期估算的数

`1051 条待译正文` 是**翻译**口径。**发布**口径是 **470 篇**
（Facebook 27 / Instagram 443）——项目约定"视频只记元数据不下载"，
**585 篇纯视频帖没有可上传素材**。本期不补发所以不影响进度，
但已写进第 0 节，防止将来有人按 1051 估工期和预算。

另：最新那几篇 IG 帖（也就是要拿来做端到端测试的）**大多是合作帖**，
原作者是 `neakasa.global` 等。用户已拍板全部进流水线，
但发布环节必须在输出里点名原作者——`review.md` 与 `index.html` 都有这个提示，
**不能到最后一环反而没有**。

#### 4. 计划里新增的两条硬依赖

- **K 依赖 F**：图内德语要用该帖已有的 `text_de` 做参照（用户指定），
  没有当前版本译文就不处理这篇的图，不做降级。
- **G8 依赖 K**：发布优先用 `media_de/`，缺失回退原图并显式告警。
  但 **G0 / G0b / G1 不依赖 K**。

#### 5. 新增前置 G0：发布必须用独立 Chrome profile

`MANUAL_STEPS.md` 早写了这条，但**当前代码做不到**——
`core/config.py` 只有一个 `profile_dir`/`debug_port`，
`core/chrome.py` 直接读全局 `cfg()`。
已把参数化单列为 **G0**，并明确它**不依赖 G1、现在就能做**。

#### 6. 明确引入的一个新依赖

**Pillow**（只它，不含 numpy）。违反了"不引入新依赖"的全局约定，
理由写在 K0：产出图必须验证"能解码 / 尺寸对 / 不是占位图 / 与原图结构相近"，
没有图像库做不了；结构相似度用纯 Python 的 dHash 汉明距离即可。

#### 7. 两个刻意留空、等真实数据再定的数

- **K5 的 dHash 阈值**：第一批 20 张跑完后按真实距离分布标定，
  **标定前配 `-1`（关闭），只记录不拦截**。
  这条遵循 `[integrity]` 那几个阈值立下的规矩：阈值在真实数据上标，不许拍脑袋。
- **G0b 的 IG 平台约束数字**（画幅 / 图片数 / 正文长度 / 标签数）：
  以 G1 实测 UI 的实际拒绝行为为准，**不照抄网上流传的数字当成事实**。

#### 8. 本轮同步更新的文件

`docs/IMAGE_PLAN.md`（新增）、`docs/PUBLISH_PLAN.md`（新增）、
本文件（第 0 节 / 目录结构 / K 组 / G 组 / 附录 A / B / C）、
`config.toml`（新增 `[image]`、`[image.keep_verbatim]`、`[publish]`）、
`.env.example`、`docs/MANUAL_STEPS.md`、`docs/HANDOFF.md`。

### 2026-08-31（第三轮）· 用户答复四项；确定并行开发的分支划分

分支：**`docs/plan-image-publish`**（本轮全部改动都在这里，之后两条 feature 分支从它开）。

#### 1. 用户的四项答复

| 问 | 答复 | 落地 |
|---|---|---|
| 图片 API 密钥 | **已填进 `.env`** | 已核对可读（长度 51，未打印内容） |
| DE 发布目标 | **`Neakasa Deutschland`** / **`neakasa.de`** | 见下方 ⚠️ |
| 要不要 K3 预扫描 | **不要**，直接用 gpt-image-2 自带视觉能力 | K3 标为已取消（保留设计与后果） |
| `quality` 档位 | **`high`**（我建议过 medium） | `config.toml` 已改；月度成本约 US$33 |

#### 2. ⚠️ FB 那个值是**显示名**，不是 URL 账号名段

用户给的 `Neakasa Deutschland` **含空格**，`facebook.com/Neakasa Deutschland`
不是合法地址。**CR-12 已经因为"显示名 ≠ URL 账号名段"踩过一次**
（FB 的 `actors[0].name` 是 `"Neakasa Official"`，URL 里却是 `neakasaofficial`，
当时误用显示名做判等直接造成跨账号污染）。

因此 `[publish]` 拆成**两个键，形态不同不许混用**：

- `facebook_page_name = "Neakasa Deutschland"` —— 显示名。**G2 用它在
  Business Suite 的主页切换器里认 Page**，整条发布链路不拼 URL。
- `facebook_page_slug = ""` —— URL 账号名段，**当前留空不阻塞**，
  G1 探查时从地址栏抄一下补上。
- `instagram_account = "neakasa.de"` —— IG 给的就是 handle，可直接拼 URL。

#### 3. 取消 K3 的两个后果（已写进 K3 与 `config.toml` 注释）

1. **没有"这张图有没有文字"的闸**，所有配图一律送进 edits，
   无文字的产品图也会被付费处理一次。当前范围下只值几美元；
   **若哪天决定补发 818 张，应当先把 K3 加回来**（否则 $179 是全额支出）。
2. **"德语文字正确"这条验收完全落到人工。** 机器不再知道原图上有哪些英文，
   无法自动回答"有没有漏改"。**K8 因此从"锦上添花"升级为 K 组能否验收的唯一依据**，
   优先级已跟着提上来，勾选框改为按第 3.1 节分类逐项列。

#### 4. 为并行开发做的三件预置

用户要把 K / G 两组**分别委托给不同 Agent 并行实现**，因此本轮把
**会被两条分支同时改到的东西提前落进主干**，减少合并冲突：

1. **`Pillow>=11,<13` 写进 `requirements.txt` 并装进 `.venv`**（实测 12.3.0）。
   K 组要用它验产出图，G 组的 `compose.py` 也要用它验待发图——
   **留给任一方去加，另一方就必然要改同一个文件**。
2. `config.toml` 的 `[image]` / `[image.keep_verbatim]` / `[publish]` 三段全部写好，
   两条分支只在**各自那一段**内改值。
3. 两份任务书里各写了一份**内容相同的「分支与文件所有权表」**
   （`IMAGE_PLAN.md` 第 10 节 / `PUBLISH_PLAN.md` 第 12 节）。
   两边都留一份是有意的：**并行开发时没人会去读另一组的任务书。**

分支划分：

| 组 | 分支 | 从哪开 | 何时合回 main |
|---|---|---|---|
| K | `feat/image-de` | `docs/plan-image-publish` | K9 真实验收通过 |
| G | `feat/business-suite-publish` | `docs/plan-image-publish` | G8 验收通过 |

**本文件的 K 组一节只由前者编辑、G 组一节只由后者编辑**；
附录 D 各自追加一段并在标题里带分支名，
**合并冲突时两段都保留，不许二选一**——那是项目的历史记录。

#### 5. 验证

离线基线 **16 套全绿**（`config.toml` 改动未破坏任何既有断言），
`git diff --check` 与 `compileall` 干净，`.bat` 仍是纯 ASCII + CRLF。
**本轮仍未写任何功能代码，也未发过任何付费请求。**

### 2026-08-31（第四轮）· 全局复盘：新增 L 组（流水线编排）

分支：**`docs/pipeline-plan`**。用户要求以全局视角规划"如何把所有组件串成
一条高度自动化的流水线"，产出 `docs/PIPELINE_PLAN.md` 与本文件的 L 组。

#### 1. 全局复盘照出来的第一件事

**这个项目现在没有任何东西在自动跑。** 计划任务至今没有安装
（实测 `schtasks /Query /TN FBScraperDelta` → 找不到）。
所有的幂等、断点、告警、降级都写好了也测过了，
**但没有任何东西会在没人的时候运行它们**。
因此 L0a（装计划任务，就是 E3）被标为整张依赖图里投入产出比最高的一格。

#### 2. 架构决定：**对账器，不是队列**

四个阶段（增量 / 翻译 / 调图 / 发布）的"做完了没有"**全都是内容寻址的**
——不是"跑过了"，而是"这份内容 + 这个版本的产物存在"。
这是在没人打算做编排的时候自然长出来的，因为每组都独立遵守了
「真相在文件里、索引是派生的」这条纪律。

**所以不需要队列**：每次运行现算"每篇卡在哪一阶段"即可。
队列会成为第二个真相源——这个项目已经为此立过铁律
（`manifest.jsonl` 那次演示过代价）。

#### 3. 复盘量到的四个数，每一个都改变了判断

| 数 | 值 | 它改变了什么 |
|---|---|---|
| 近 90 天可发节奏 | **5.2 篇/周、11.9 图/周** | 稳态量很小，不需要队列/并发/批处理 |
| 含金额的可发帖 | **近 90 天 39 %**（每 5 篇有 2 篇） | **流水线上最密的一道人工**，见下 |
| 不同金额串总数 | 全档只有 **46 种** | 46 行表覆盖整个归档 → 人工可从"每篇"降到"每个新价格" |
| 可发合作帖的原作者 | 近 90 天**只有 2 个，全是自家账号** | 授权确认这道闸的实际触发率 ≈ 0 |

#### 4. 两条待办被复盘直接消解

- **全量翻译的 US$24 预算拍板，已不在关键路径上。**
  用户已定不补发历史，而**只有要发的帖才需要译文**——稳态 5.2 篇/周，不是 1051 篇。
  它现在是可选项（跑了得到一份德语语料，当前没有自动化环节消费它）。
  **建议先不跑全量**，让对账器按需翻译。
- **"229 篇第三方创作者合作帖的授权风险"在发布口径下基本蒸发。**
  那些几乎全是纯视频 Reels，**本来就发不了**。
  图文可发的合作帖只有 39 篇，近 90 天的原作者全是 `neakasa.global` / `neakasa.de`。

#### 5. 找到的最大缺口：**没有死人开关**

`core/integrity.py` 检查的是「**账号**有没有在发新帖」，
**没有任何东西检查「我们的流水线还在不在跑」**。
计划任务被禁用、`.venv` 被删、笔记本两周没开机——今天全是静默的，
而**"没跑"和"跑了但没新内容"在输出上长得一模一样**。

这与合作帖那次是**同一个故障模式**：丢弃是完全静默的，
"程序丢掉 263 篇，输出里一个字都没有"。已列为 L0c。

#### 6. 自治分级，而不是开关

新增 `[pipeline].autonomy`：`manual` → `assisted` → `supervised` → `autonomous`。
理由：这个项目的风险**不对称**——抓取被封不可恢复、发布不可回滚，
而翻译和调图错了只是重跑。所以按阶段分别放开，
每一档都真实运行一段时间，把没想到的失败模式暴露在还便宜的时候。

**核心主张**：「人为干预尽可能少」的正解**不是砍掉闸门，
是让闸门按"类"触发而不是按"篇"触发**。
价格表、授权白名单、排期规则三样加起来，
把稳态人工从 **≈ 95 min/周压到 ≈ 8–10 min/周**；
剩下的压不动，因为它们落在三条承重墙上（登录、选择器维护、德语质量的最终责任）。

#### 7. 本轮改动

新增 `docs/PIPELINE_PLAN.md`；本文件新增 L 组、附录 A 的 L 支线与分支表；
`config.toml` 新增 `[pipeline]` 段（**目前还没有代码读它，是 L 组的配置骨架**，
落地时必须做配置项审计）并在 `[publish]` 末尾留了 TOML 子表顺序的警告注释；
同步 `HANDOFF.md`、`MANUAL_STEPS.md`、`README.md`。

**本轮同样未写任何功能代码，未发过任何付费请求。**
离线基线 16 套全绿。

### 2026-08-31（第四轮）· `feat/image-de` 离线实现完成，付费验收留在费用闸前

分支：**`feat/image-de`**，从规定基线 `docs/plan-image-publish` 的 `9ea342c` 创建。

#### 1. 实际落地

- 新增 `localize_images.py`、`prompts/image_de.md`、`scripts/run_images.bat` 与
  `tests/tests_localize_images.py`；K8 只改 `translate.py::run_review` 及对应断言。
- GPT-Image-2 edits 正式请求只含七个确认字段，**从不发送 `input_fidelity`**，
  `quality=high` 显式发送；响应模型、base64 图片、尺寸/格式与完整 usage 均失败闭合。
- `legal_size()` 覆盖任务书分布并用 2,000 组模糊输入验证四条尺寸约束。
- 取消 K3 后，提示词自行找应译英文；所有不可改类别、术语表和不可信 `text_de`
  都进入模板。真实归档一篇已用 `--show-prompt` 完整渲染，零请求。
- K5 以 Pillow 做解码/格式/尺寸/占位图硬闸，以纯 Python dHash 记录结构距离；
  阈值继续为 `-1`，没有根据假图自行定数。
- K6 复用真相源、指纹、坏尾修复、fsync 与单实例锁，并增加 `output_sha256`。
  落盘顺序为临时图 fsync → 所有权记录 fsync → 原子替换；模拟最后一步失败时，
  普通重跑可以补齐，不会把缺图误判为人工版本或已完成。
- K7 按当前提示词版本的完整真实 token usage 中位数外推，并打印完整 dHash 分布；
  K8 为每张图生成原图/德语图并排表及逐风险类别勾选框，人工文件仍优先。

#### 2. 与原计划相比的自适应调整

“不补 818 张历史”若只靠操作约定，入口第一次普通运行仍可能误触 US$179。
因此在 `[image]` 增加并严格消费 `incremental_since`，默认只跟 2026-08-31 后增量；
K9 明确用 `--latest-posts 3`，全历史还需 `--all-history` 与
`--confirm-all-history-cost` 双重显式确认；`--latest-posts` 只接受 1..3，不能用大数
绕过该闸。该键纳入 K0 双向配置审计，不是死旋钮。

另一个实现偏差是 `images_de.jsonl` 增加 `output_sha256`。任务书原 schema 只绑定输入
指纹，无法区分“旧程序图被人工改过”与“仍是程序原件”，也会在进程硬终止时留下
图片/记录先后顺序窗口；输出哈希同时解决所有权、审校配对与恢复判定。

#### 3. 并行工作区事实

共享目录在实现期间被另一任务切到 `docs/pipeline-plan` 并出现 K 组禁止触碰的改动。
没有回滚、暂存或提交它们；改在相邻独立 worktree
`D:\VSCodeWorkspace\Facebook\FacebookScraper-image-de` 继续，目标分支仍是
`feat/image-de`。工具实际提供的是 Windows PowerShell，`/bin/bash` 不存在；代码和
批处理仍按项目 Windows 契约验证，`run_images.bat` 为纯 ASCII、CRLF、无 BOM。

#### 4. 验证与诚实边界

- 全部 `tests/tests_*.py`：**17 套全绿**（原基线 16 套 + K 组 1 套）。
- `git diff --check` 与 `.venv/Scripts/python.exe -m compileall -q .` 干净；
  `run_images.bat` 字节级复核为纯 ASCII、CRLF、无 BOM，且新入口先调用 `force_utf8()`。
- K8 额外用三篇带物理原图/德语图的离线集成样本验证六个 Markdown 链接可显示；
  这不是 GPT-Image-2 真实产出，故 K8 未勾选。
- 首轮只读代码审查无 Critical；指出的五个 Important 已全部修复并加回归：
  usage 首张熔断、latest 范围上限、付费/离线模式互斥、K8 人工优先、replace 前末检。
  修复后二次只读复核确认无残留 Critical/Important。
- 用户已授权复用现有 `.env` 中的 `IMAGE_API_KEY`；独立 worktree 已通过“只向子进程
  注入该环境变量”的零 API 配置实例化验证，密钥未回显，也未复制 `.env` 或写进配置。
- 真实最新帖还暴露出保护表落后于语料：`Riko/RIKO` 已有 204 次命中，另有
  `PH5RIKO`、`P1 Pro/NeakasaP1Pro`、`IFA2026`。这些项已回填 `[image.keep_verbatim]`，
  优惠码类别改为非穷举规则，`IMAGE_PROMPT_VERSION` 从 1 升到 2；没有沿用过期表硬跑。
- **本轮没有发送任何真实图片 API 请求，也没有产生图片费用。** K1 的 low
  `--check`、K7 的真实账单、K8 三篇真实审校仍未发生。提交后对真实归档做零 API
  预演，发现最新三篇是 1/0/5 共 6 图（上游参考约 US$1.31），且均无当前 `text_de`；
  其中零图片帖不创建 K 任务，实际只需补有图的两篇，按当前真实 usage 离线外推约
  US$0.038。现有 F `pending` 又是时间正序，`--limit 2/3` 实测只会选最老帖子，不能
  精确补目标。本 K 分支未越过文件所有权改 F 主流程，也未误跑约 1017 篇待译正文；
  需用户另行授权精确选帖或提供这两篇已审校译文，再经过图片费用确认与德语逐图确认，
  故这些任务保持未勾选。

### 2026-08-31 · `feat/business-suite-publish` · G0/G0b/G1 阶段记录

- **G0 已完成并真实验收。** 发布侧固定走 `.fbscraper-publish` / 9223，抓取侧
  无参调用仍走 `.fbscraper-chrome` / 9222；Windows 还会核对监听 Chrome 的
  `--user-data-dir`。两端实机同时可附着，且只核对 cookie 名得到抓取侧已登录、
  新发布侧未登录，证明会话没有串用。
- **G0b 只完成代码与离线验收，未完成任务书的真实验收。** `compose_post()` 已把
  当前版译文、金额不变、媒体完整性与完整解码、混合/脏媒体、合作帖来源、aware 排期
  以及与 config 审核 dump 逐项一致的 UI 约束做成失败闭合；当前库里 5 篇图文实帖
  可组装、1 篇纯视频被拒绝，
  但计划点名的最新三篇都没有当前版译文，故 G0b 保持未勾选。
- **G1 只交付记录器。** `tools/probe_publish.py` 附着 9223 后仅监听人工交互、记录
  白名单稳定语义属性并对敏感输入截图遮罩，不导航、不点击、不填表、不上传、不提交；
  用户尚未手工
  跑完流程，因此真实选择器、FB slug、定时窗口和 Page 时区仍为空，G1 保持未勾选，
  `publish/selectors.py` 只有 TODO；G2–G6 只有失败闭合函数，G7 仍只有任务书契约。

#### 独立代码审查修正（同分支）

首轮审查无 Critical，发现 5 个 Important，已全部补反例后修正：截断 JPEG 必须
完整加载像素；缺失 `media_complete=True`、混合视频或脏媒体行不得被静默过滤；
严格模式不能用任意字符串冒充 probe；记录器过滤合成/敏感事件与任意 payload；
无参 `launch/attach` 恢复旧 CDP-only 行为，只有显式 profile 才核对进程归属。
修正后原 16 套加新增发布套件共 **17 套全绿**；`ruff`、`compileall -q .` 与
`git diff --check` 均通过，发布 `.bat` 的 ASCII/CRLF/无 BOM 断言包含在全量测试中。

### 2026-08-31（第七轮）· K/G 合并进 `main` 并推送；仓库回到单目录单分支

分支：合并在一个一次性 worktree 里做，做完即删；结果推到 **`origin/main` = `9f8c1bf`**。

#### 1. 合并顺序按 CR-46 走，没有走那条"最自然"的错路

两条 feature 分支都从 `9ea342c` 开出，**比 `main`（`3736303`）还早**，
所以 `git merge --ff-only` 必然失败——**那不是 `main` 被人动过**。
正确顺序是**先落两条 feature 分支，再把 `wip` 上只属于主干的东西摘过来**；
反过来（先把 `main` 快进到 `wip`）实测会把冲突从 1~2 个文档放大到 6/10 个、
其中含整文件 add/add，因为 `wip` 里带着两条线更早的快照。

实际冲突与 CR-46 的预测一致，且**全部在共享文档里，代码零冲突**
（`config.toml` / `requirements.txt` / `translate.py` / `core/chrome.py` 全部自动合）：

| 合并 | 冲突文件 |
|---|---|
| `← feat/image-de` | `IMPLEMENTATION_PLAN.md` |
| `← feat/business-suite-publish` | `IMPLEMENTATION_PLAN.md`、`MANUAL_STEPS.md`、`HANDOFF.md` |

解冲突按既定约定**两段都保留**：附录 D 的三段（L 组复盘 / K 组第四轮 /
G 组 G0-G0b-G1）各自独立全部保留；`MANUAL_STEPS` 的第 10b 步（K）与
第 10c 步（G）各自独立全部保留；摘要表按**"每组自己那一行以自己分支为准"**取并集。

#### 2. 抢救回来的三样（CR-49）

`c35715d` 的主干修复（cherry-pick，实测零冲突）、`config.toml` 的 `[pipeline]` 整段、
`[publish]` 段末那条 TOML 子表顺序警告。后两样只存在于 `wip`，
按合并路径本来会丢，而 `PIPELINE_PLAN` 第 13/14 节直接引用它们。

#### 3. 本轮唯一的代码改动：CR-61

`publish/compose.py::_load_current_translation` 此前只再判 `money_preserved`，
不判 `hashtags_preserved`——而这两条在 `translate.py` 里本来就是同一类
「不可改内容规则」，写盘时一起判。**写盘侧判过不等于发布侧不用判**：
`translated.jsonl` 是真相源，人工审校的修正就是直接改它，不经过写盘闸。
已复用 `hashtags_preserved`（不重写第二份）+ 三条断言 + `PUBLISH_PLAN` §5.2b。
上线前在真实归档 9 条当前有效译文上验过：金额违规 0 / 标签违规 0，**新闸不改变任何现有可组装帖**。

#### 4. 验证与诚实边界

- 合并态 **18 套 / 1108 项全绿**；`compileall -q .`、`git diff --check`、
  零冲突标记、`tomllib` 解析、`uv pip check`（24 包）、8 个 `.bat` 字节约定全过。
- **在真实 `archive/` 上复核了合并态**（feature worktree 里做不到，它们没有归档）：
  11 张待处理与合并前一致、三道费用闸都响、3 篇真实帖组装成功且 `--strict` 正确失败闭合、
  K→G 所有权契约 7 条、K8 并排审校 7 条、CR-56 降级、CR-60 tzdata 四个偏移。
  **全程零 API 调用、零费用、零社媒访问，真实 `archive/` 一个字节都没动过**
  （`run_review` 是在临时副本上跑的）。
- **发现并修掉 CR-62**：切分支之后 `.bat` 的工作区副本会带裸 LF，
  而 `git status` 结构性地看不见（clean filter 把 CRLF/LF 归一成同一 blob）。
  是 `tests_schedule.py` 抓到的。详见 `CODE_REVIEW.md` 16.5。
- **`publish/selectors.py` 仍然是空的，`business_suite.py` 五个 `ProbeRequired` 原样保留**——
  红线 5，本轮一个字都没动。
- 冗余分支已清：本地 6 条 + 远端 2 条，`-d` 全程用安全删（`wip` 用 `-D`，
  删前确认过三样东西都已进 `main`，并留下 `backup/pre-merge-2026-08-31`）。
- **仍然卡在用户手上的三件一件都没动**：K9 图片费用确认、G1 真实 DOM 探查、
  L0a 装计划任务。

### 2026-09-01 · L 组 · L0b（`pipeline status`）与 L0c（死人开关）落地

分支：`feat/pipeline`，从 `main` 开。

#### 1. 交付

- 新增 `pipeline.py`（两个子命令：`status` / `check-alive`）、
  `scripts/run_pipeline.bat`、`tests/tests_pipeline.py`（**61 项断言**）；
- `tools/schedule.py` 多注册一个 **`FBScraperAlive`**（第三个任务），
  `_settings()` 参数化出 `network` / `time_limit` 两项；
- **全量 19 套 / 1183 项全绿**（新增 1 套 61 项，`tests_schedule` 从 57 增至 71）。

#### 2. 两处偏离任务书（细节在 `PIPELINE_PLAN.md` 10.2）

① **没建 `state/pipeline_state.json`**，改从 `delta_state.json` 已有的
`last_success` 读。它是**运行**标记（抓到 0 篇也更新），
正是第 7 节要区分「没跑」和「跑了没事做」所需要的语义；
另建一份则是第 2 节明令不要的第二个真相源。读取位置已为 L1a 预留。

② **死人开关是独立计划任务**，不挂进增量任务：挂进去会跟增量任务一起哑掉，
而那正是它要抓的失效。`RunOnlyIfNetworkAvailable=false`——
这个检查不联网，"断网"不能成为警报不响的理由。

#### 3. 实测发现，都写进了输出而不只是注释

- 实现前 `grep -rn '"pipeline"' --include=*.py` 为空：`[pipeline]` 那四个键
  **一个都没被代码读过**（CR-40 明令不许有的死旋钮，是合并带来的债）。
  现在 `dead_man_days` 真的被消费，其余三个 `status` 会**显式打印"还没接上"**。
- 「待发布」列打 `—` 不打 0：`published.jsonl` 由 G6 写、G6 被 G1 卡着。
  打 0 会被读成"没有积压"，实际含义是"这阶段还没接上"——与 CR-19 同源。
- 「可发」实测 **470**（FB 27 / IG 443），归档 1067，差的 597 篇是纯视频/无图/无正文。
  **这是发布口径，比 `compose_post` 的完整硬闸宽**，脚注里写明了。

#### 4. 诚实边界

- 本轮**零 API 调用、零费用、零社媒访问**，真实 `archive/` 一个字节没动。
- **计划任务仍未安装**——`FBScraperAlive` 只是"装的时候会一起装上"，
  按 E3 的一贯做法**没有替用户按**。L0a 现在解锁了，轮到用户。
- L0d（价格表）**按 10.1 排在 G1 之后**，本轮没做。L1a 等发布链路通了再做。

### 2026-09-01 · G1 实跑撞出 CR-63：归属核对的超时从来没被标定过

用户按第 11 步跑 `start_chrome_publish.bat`，**浏览器正常打开**，
脚本却报"端口未就绪"，`probe_publish.py` 再报"不属于目标 profile，
先关闭占错端口的 Chrome"。**两条消息都是错的，环境一直是对的。**

根因：`_cdp_profile_matches` 的 PowerShell 探测（`Get-NetTCPConnection`
＋ `Get-CimInstance`）本机实测要 **7.4–9.4 秒**，而超时写死 **5 秒**——
每次都 `TimeoutExpired`，被吃掉后返回 `None`，按"核对不了"失败闭合。

**这是 CR-59 的尾巴**：那轮正确地把这个调用从每秒一次降到 2 次，
**却没有人量过它到底多贵**，5 秒原样留着。而离线测试全部 mock 掉了这个函数，
所以 17 套断言一条都没响。

> **教训：`timeout=` 是一个关于目标机器的经验断言。
> 没在目标机器上量过的超时值，和写死的选择器是同一类东西。**

处置（细节见 `CODE_REVIEW.md` 第 17 节）：端口→PID 改走 `netstat`
（0.12s vs 5.7s），整条降到 ~3.5s；`PROFILE_PROBE_TIMEOUT = 20.0` 抽成常量
并把实测表写在旁边；**三态分诊**——`False`（确实是别的 profile，去关掉它）
与 `None`（核对不了，不是说它错了）必须给不同的话，
后者附两条只读自查命令。`start_chrome_publish.py` 在 `launch()` 失败后重新分诊。

真实验证：用户那个 Chrome 的核对从 `None` → **`True`**（4.02s），
`attach()` 成功（6.38s），窗口未受影响；反向传抓取 profile → `False`、
传空端口 → `None`。新增 17 条断言（`tests_chrome` 33 → 50），**19 套 / 1199 项全绿**。

### 2026-09-01 · G1 recorder 静默记录不到（CR-64）——同一天的第二个真机 bug

用户走完**整个** Business Suite 发帖流程，结束后发现 dump 里
`interactions` 只有 1 条（NTP 上那次点击），"点进网址之后全部丢失"。
20 分钟人工操作作废，而**过程中一句提示都没有**。

根因：`connect_over_cdp` 附着到**连接前就已打开**的页面时，Playwright 的
page 对象可能永远拿不到帧树——实测 `page.url=''`、`page.evaluate('1+1')`
**TimeoutError**、`expose_binding` 后 `typeof window[binding]` 是 `undefined`；
而**同一 target 的原始 CDP 一切正常**。旧的 `_install_existing` 用
`except Exception: continue` 把这个 TimeoutError 整个吞掉，
于是监听器一个都没装上、一条没记、零提示。

> 教训：**`except Exception: continue` 用在"装设备"这一步，
> 等于把"设备没装上"变成"设备装好了但什么都没发生"**——
> 这两件事在输出上一模一样，和 L0c 死人开关要区分的
> 「没跑」vs「跑了没事做」是同一类失效。

修复中还撞出第二个：改走 CDP 后**不 `Runtime.enable`/`Page.enable` 就跨不过导航**，
症状和原 bug 完全一样（首页正常、一导航全丢）。没有跨导航回归测试的话，
这个修复会带着同一个症状上线。

处置见 `CODE_REVIEW.md` 第 18 节：CDP 安装 + **回读校验**、通路统一成
JSON 字符串、装不上大声报、**逐条回显**（屏幕不跳数字就是没记上）、
空 dump 收尾告警、3 秒巡检用 `/json/list` 核对漏页、
截图显式超时 + CDP 回退（**回退路径同样遮罩敏感输入，插不进遮罩就不截**）。

验证：scratch Chrome 真·可信点击，跨**两次导航**均记录、密码框仍不记录、
截图无错误；用户那个原本静默失败的页面 `install_on_page -> True | ok`，
页面内回读 `handlers=object | binding=function`。
新增 20 条断言（`tests_publish` 92 → 112），**19 套 / 1219 项全绿**。

#### 同日追加（CR-65）：光"装得上"不够，还得"掉了会自己回来"

用户复跑仍然丢：新开空标签页 → 点 shortcut 跳到 `business.facebook.com`
→ 之后全部没记录。**而 scratch 环境把能想到的路径全试了一遍，每一条都正常**
（NTP→本地站、Playwright 之外新建标签页、跨站换渲染进程、装完立刻导航、
甚至在用户自己的浏览器里开中立标签页做可信点击）——**复现不出来**。

复现不出来就不该继续猜原因。改成**对所有原因都成立**的做法：
① 每 2 秒回读监听器标记，掉了就地补装；② 主帧一导航立刻补注入一次；
③ 已装表用 page 对象做键（`id()` 会在回收后重用，会让新页面被误跳过）。

验证：跨站后**人为把监听器整个删掉** → 点击不记录（0）→ 巡检发现并补装
→ 再点击记录成功（1）。**这条路径不依赖"我们猜对了原因"。**

⚠️ 中途差点误判一次：跨站后点击"没记录"，查下来是点在了空白处，
`event.target` 是 `<body>`，而 `_safe_element` **本来就故意丢弃 body/html**。
**先证明点到了东西，再谈丢事件。**

新增 6 条断言（`tests_publish` 112 → 118），**19 套 / 1225 项全绿**。
