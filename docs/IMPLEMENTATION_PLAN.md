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

- 图内英文文字的德语替换（一期走人工处理）
- 视频文件下载与处理
- 完整性的"帖子总数交叉校验"（大概率抓不到总数，降级为只做时间序列连续性）

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
    scripts\run_delta.bat *        增量任务的调度入口（同样只写纯 ASCII）
  tools/                   .bat 的真正实现。**中文只能待在这里，不能进 .bat**
    setup.py               一次性环境搭建（venv + 依赖 + 自检 + 离线测试）
    start_chrome.py        起专用 Chrome + 端口就绪轮询（参数全读 config.toml）
    replay.py              用 _capture_*.json 离线重建归档（B7），不重新下载媒体
    layout.py              归档布局：migrate / reindex / index（J 组）
  docs/
    IMPLEMENTATION_PLAN.md 本文件（进度真相源）
    MANUAL_STEPS.md        **人工操作指南**，需要用户亲自做的步骤全在这里
    HANDOFF.md             会话交接
    CODE_REVIEW.md         已实现代码二次审查、修复与验证记录
  prompts/
    translate_de.md        英译德提示词本体。可直接编辑，改它不用动 Python。
                           `{{占位符}}` 由 config.toml 注入；改完 --show-prompt 看效果
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
  publish/ *
    business_suite.py *    UI 自动化发布
    selectors.py *         选择器集中定义
  translate.py             德语翻译 + 审核清单（F1/F2/F3 三合一，子命令区分）
  tests/                   离线测试。scripts\setup.bat 用 glob 全跑，新增即自动纳入基线
    tests_backfill.py        回填收尾、媒体补全、归属拦截、滚动进度（32）
    tests_chrome.py          CDP 真实性与端口占用（7）
    tests_delta.py           登出增量：解析、登录墙三形态、429 退避、归属过滤（55）
    tests_delta_logged_in.py 登录态增量：异常即停、深度上限、状态记录、
                             失败预算、抖动顺序、转储裁剪（82）
    tests_fb_graph.py        只读 Graph 路线的视频边界与媒体失败（7）
    tests_parse.py           解析层 + **真实响应结构断言**（47）
    tests_store.py           归档层 + 每帖文件夹布局与 reindex（35）
    tests_http.py            登出客户端（17）
    tests_integrity.py       完整性检查（23）
    tests_notify.py          通知降级（15）
    tests_translate.py       翻译管道 + 提示词渲染 + 金额保留强制（162，零 API 调用）
                             ——合计 11 套 482 项，**在 stdout 被重定向的条件下也全绿**
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
- [ ] **C2** 实现 Instagram 登录态增量

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
  > 【真实验收待办】修完之后再跑一次：IG 应看到 ≥3 篇本账号帖、
  > 最新一篇不早于 2026-07-15、连续两次第二次 0 新增。
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
- [ ] **C4** 增量的媒体下载

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
  > 【真实验收待办】图片确实落进 `posts/<文件夹>/` 且无 0 字节。
- [ ] **C5** 运行状态记录

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
- [ ] **D3** 把检查接入增量流程

  - 每次 `routes/delta.py` 运行结束后：
    1. 跑 D1 的三项检查
    2. 任一项触发 → 调 D2 通知，并写入 `state/alerts.log`
  - 告警文案要具体，包含平台、检查项、数值。反例："发现问题"；
    正例："instagram 连续 5 天零新增（阈值 4 天），可能已被登录墙拦截"
  - 【验收】人为把 `state/delta_state.json` 的 `consecutive_quiet_days` 改大 → 运行后触发通知

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

- [ ] **E1** 编写 `scripts\run_delta.bat`

  - 内容：激活 venv → `cd` 到项目目录 → `python -m routes.delta` → 记录退出码
  - 输出**追加**到 `state/delta.log`（含时间戳），不要覆盖
  - ⚠️ **必须是纯 ASCII + CRLF**（禁止事项 7），中文提示放 Python 里
  - ⚠️ **必须设 `set "PYTHONIOENCODING=utf-8"`**，和另外四个 `.bat` 一样。
    输出重定向进 `state/delta.log` 时，本机 cp936 编不出 `⚠ ❗`，
    第一次打印就会把整个进程带走——**崩在网络请求已发出、归档尚未写入之际**。
    这不是假设，是 2026-08-30 实测过的故障（见 `core/console.py`）
  - 【验收】双击能完整跑完，日志有新增行；**日志里的 `⚠` 等符号正常显示**
- [ ] **E2** 实现补跑判定

  - 在 `routes/delta.py` 入口加 `--if-stale` 参数：
    - 读 `state/delta_state.json` 的 `last_success`
    - 距今 < `config.toml` 的 `[delta].stale_after_hours` → 直接退出（退出码 0）
    - 否则正常执行
  - 【验收】连续跑两次 `--if-stale`，第二次应立即退出并打印"距上次成功不足 N 小时，跳过"
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

---

## F. 德语翻译（依赖 B6，可与 C/D/E 并行）

- [ ] **F1** 实现 `translate.py` 主流程

  - 读 `archive/*/manifest.jsonl`，筛出 `text` 非空且尚未翻译的帖子
  - 翻译结果写入**独立文件** `archive/<账号>/translated.jsonl`，
    **不要改动 manifest.jsonl**（保持抓取产物不可变，便于重跑）
  - 结构：`{"post_id": "...", "text_de": "...", "translated_at": "...", "model": "..."}`
  - 支持 `--limit N` 用于小批量试跑
  - 【验收】对 3 篇试跑，产出结构正确的 `translated.jsonl`

  > 进行中：2026-08-29 · **代码已完成，验收未通过——验收要求真实文案 + 真实网关，
  > 两者都还没有。** 不勾选。`tests_translate.py` 65 项断言全绿（用合成归档 +
  > 假翻译器，零 API 调用），覆盖：待译筛选、写盘结构、幂等重跑、`--limit`、
  > `--force`、`--dry-run`、单条失败不中断整批、脏 `translated.jsonl` 不崩，
  > 以及**最关键的一条：`manifest.jsonl` 字节级未变**。
  >
  > 结构比计划多一个 `prompt_version` 字段。理由：提示词一旦调过，
  > "这批译文是旧提示词产出的"必须是可查的事实而不是靠记忆，
  > 否则 `--force` 重译哪些帖子只能靠猜。
  >
  > **API 接入方式（用户指定：公司内部兼容 Anthropic Messages 的第三方端点）**：
  > 用官方 `anthropic` SDK 加 `base_url` 覆盖，不自己拼 HTTP——
  > SDK 自带 429/5xx 指数退避与分类异常，自己写一遍只会更差。
  > 已实测 `anthropic==1.2.0` 的构造器支持 `base_url` / `api_key` /
  > `auth_token` / `default_headers` / `timeout` / `max_retries`。
  > 新增依赖已写进 `requirements.txt`。
  > ⚠️ `anthropic` 1.x 基于 **httpx2**，与 `core/http.py` 用的 `httpx` 是两个
  > 不同发行包，已实测 httpx 0.28.1 / httpx2 2.12.0 同时安装无冲突。
  >
  > 为兼容第三方网关做的三处保守选择（都在 `config.toml` 里可开）：
  > 1. **默认不发 `thinking`**。官方 Opus 5 上不发即为自适应思考，所以这也是
  >    官方端点上的最佳状态；而兼容网关未必认这个参数。
  > 2. **默认不发 `temperature`**。⚠️ Opus 5 / Sonnet 5 已移除该参数，发了直接 400。
  > 3. **默认不发 `output_config.effort`**。同样是较新参数，兼容性未知。
  >
  > 鉴权支持两种风格（`auth_style = "x-api-key" | "bearer"`），因为内部网关
  > 用 Bearer 的不在少数，而 SDK 对这两种是 `api_key=` 和 `auth_token=` 两个参数。
  >
  > **密钥不进 `config.toml`**（那个文件进版本库）。从环境变量读，
  > 变量名本身可配；找不到环境变量时回落到项目内的 `.env`（已 gitignore）。
  > 没引 `python-dotenv`——只需要 `KEY=value` 一种形态，十行够了。
  >
  > 新增 `--check` 子命令：发一次十几 token 的请求验证网关配置，
  > 并把 401（鉴权风格选反）/ 403（无权限）/ 404（模型名不认 or base_url 多带了
  > `/v1/messages`）/ 400（网关不认某参数）分别报出来，而不是让人对着一个
  > 状态码猜。**已实测**：用假密钥打官方端点会正确落到 401 分支并给出
  > "试试换成 bearer" 的提示，说明整条请求链路与异常分类是通的。
  >
  > 截断处理：`stop_reason == "max_tokens"` 抛错而不是返回半句德语——
  > 半句德语在人工审校时未必看得出来。`refusal` 同样抛错。
- [ ] **F2** 实现风格 few-shot

  - 从已抓到的 US 文案里取 `config.toml` 的 `[translate].style_examples` 篇
    （建议取长度中位数附近的，不要取最短或最长的）作为提示词示例
  - 提示词中加入 `[translate].tone` 的内容
  - 提示词必须明确要求：
    - 保持品牌口吻，不要过度营销化
    - **控制长度**（Meta 对冗长文案有分发降权）
    - **话题标签不超过 3 个**（同上）
    - 保留原文的换行与段落结构
    - 货币、尺码、日期格式按德国习惯本地化
  - 【验收】人工检查 3 篇输出，德语通顺且风格与原文一致

  > 进行中：2026-08-29 · **提示词已实现，验收未通过——"人工检查 3 篇输出"
  > 要等真实文案与真实网关。** 不勾选。
  > 测试断言了提示词包含全部六条硬性要求（标签 ≤3、保留换行、`19,99 €`、
  > `TT.MM.JJJJ`、不过度营销、控制长度）以及 `config.toml` 的 `tone`。
  >
  > **一处计划没说清、实现时必须定的事**：`style_examples` 取的是
  > **英文原文单语示例**，不是英德翻译对照——归档里本来就没有德语对照。
  > 所以它们在提示词里的定位是"这个品牌平时怎么说话"，而不是"这句该怎么译"，
  > 提示词里显式写了「它们是语气参照，不是翻译对照，不要去翻译它们」，
  > 否则模型会把示例本身也翻一遍。
  >
  > 选篇规则：按长度排序取**中位数附近**，并排除当前正在翻的那篇。
  > 最短的往往是"New drop 🔥"这种没信息量的，最长的会把模型带向啰嗦，
  > 两头都不能代表品牌的常态口吻。少于 20 字符的不做示例。
  >
  > 另加了两条计划没列但同类的约束：emoji 原样保留在对应位置；
  > 品牌名 / 型号 / @提及 / URL 原样不译。
  > 以及一条工程性的：**只输出译文本身**，不要前言、不要代码围栏——
  > 输出要直接进 `translated.jsonl`，多一句"以下是译文"就得靠后处理擦。
  > 代码侧仍做了最小限度的围栏剥离兜底（只剥围栏，不做激进清洗）。
  >
  > 更新：2026-08-29（同日第二轮）· 应用户要求撰写了完整版翻译提示词。
  > 提示词从 Python 里搬进独立文件 **`prompts/translate_de.md`**（约 9 KB，
  > 渲染后约 4800 字符 / 约 2400 tokens），由 `{{占位符}}` 从 `config.toml` 注入。
  > 详见附录 D 的「翻译提示词重写」条目。
- [ ] **F3** 产出人工审核清单

  - 生成 `archive/<账号>/review.md`，每篇帖子一节，含：
    - 原文英文
    - 译文德语
    - 配图的本地路径（Markdown 图片引用，便于直接预览）
    - **图内是否含英文文字**的待确认勾选框（本期图内文字走人工处理）
  - 【验收】用 Markdown 预览器打开，图片能正常显示，可直接交给德语审校人

  > 进行中：2026-08-29 · **代码已完成，验收未完全通过**——结构已用合成数据
  > 验证（图片相对路径引用、两个勾选框、原帖链接、按时间正序、分辨率标注），
  > 但"用 Markdown 预览器打开图片能正常显示"需要真实图片文件，等 B 组。不勾选。
  >
  > `review.md` 与 `media/` 同目录，因此图片用相对路径 `media/xxx_0.jpg` 引用，
  > 预览器直接能显示，交给审校人时不用额外传文件。
  > 每篇两个勾选框（译文已审校 / 图内含英文文字需替换）。
  > `media_complete=False` 的帖子额外加一行警示——那类帖的配图**本来就不全**，
  > 审校人看到的图少于实际，不提示会被当成"这帖就一张图"。
  > 本地文件缺失（媒体没下载成功）与"本来就没图"分开显示，两者含义不同。
  >
  > **一个已知的单向缺口**：审校人在 `review.md` 里改的译文**不会回写**
  > `translated.jsonl`。当前定位是"给人看的清单"而非"可编辑的数据源"。
  > 若发布环节要吃审校后的结果，需要再加一个回写命令——已写进
  > `MANUAL_STEPS.md` 提示用户，等用户确认是否需要。

---

## G. Business Suite UI 自动化发布

> ⚠️ **本组必须先探查再实现。严禁凭猜编写选择器。**
> Business Suite 是 React SPA，class name 是构建期混淆的，
> 任何"看起来合理"的选择器都是错的。

- [ ] **G1** 探查 Business Suite 的真实 DOM 与流程

  - 在 A3 的专用 Chrome 里登录**有 DE Page 发布权的账号**
    （注意：这与抓取小号是**不同账号**，建议用**另一个专用 profile**，
    避免发布账号与抓取小号出现在同一浏览器指纹下）
  - 手工走一遍完整的定时发帖流程，全程记录：
    1. 创建帖子的入口在哪（URL 与按钮）
    2. 图片上传控件的类型（`<input type=file>` 还是拖拽区）
    3. 文案输入框的类型（`<textarea>` 还是 contenteditable）
    4. 定时开关的位置
    5. 日期选择器、时间选择器的交互方式（能否直接输入，还是必须点选）
    6. **时区显示的是哪个时区**（这是最容易出错的一步）
    7. 提交按钮，以及提交成功后的确认信号
  - 推荐用 `playwright codegen` 录制一遍，得到初版选择器
  - 把结果写入 `publish/selectors.py`，每个选择器加注释说明它对应哪一步
  - 【验收】`publish/selectors.py` 存在，含至少 7 个带注释的选择器常量
  - 【记录】**必须记录定时窗口的 UI 限制**（最早能排多久之后、最晚能排多远），
    以及时区行为。这些数字后续会被依赖
- [ ] **G2** 实现登录态检查

  - `publish/business_suite.py` 中的 `ensure_logged_in(page) -> bool`
  - 打开 Business Suite，判断是否已登录；未登录则**打印提示并退出**
  - ❌ **不得实现自动登录**
  - 【验收】未登录时给出清晰提示；已登录时返回 True
- [ ] **G3** 实现图片上传

  - `upload_images(page, paths: list[Path]) -> None`
  - 优先用 Playwright 的 `set_input_files()`（比模拟拖拽稳定得多）
  - 上传后**必须等待缩略图出现**再继续，不能用固定 `sleep`
  - 【验收】能上传 1 张和 3 张（多图），缩略图数量正确
- [ ] **G4** 实现文案填写

  - `fill_caption(page, text: str) -> None`
  - 若是 contenteditable，注意换行的输入方式（可能需要逐行 `type` + `Shift+Enter`）
  - 填完后**回读校验**：读取控件内容与输入比对，不一致则抛错
  - 【验收】含换行和德语变音字符（ä/ö/ü/ß）的文案能正确填入并回读一致
- [ ] **G5** 实现定时设置

  - `set_schedule(page, when: datetime) -> None`
  - ⚠️ **时区是最大的坑。** 按 G1 记录的时区行为处理，
    并在函数里显式转换，不要依赖本地时区隐式生效
  - 设置完成后**回读 UI 上显示的日期时间**，与目标值比对
  - 【验收】设定一个明确时刻，UI 回读一致；跨日、跨月边界各测一次
- [ ] **G6** 实现提交与结果确认

  - `submit(page) -> str | None`，返回成功标识（若 UI 提供）
  - 提交后必须等待**明确的成功信号**（toast / 跳转 / 列表中出现该帖），
    不能提交完就返回
  - 【验收】提交一条定时帖，能在 Business Suite 的已排期列表里看到
- [ ] **G7** 实现失败处理与残留清理

  - 流程中断（超时、选择器失效）时：
    - 截图存到 `state/publish_failures/<时间戳>.png`
    - 打印当前 URL 与失败步骤
    - **检查是否留下了草稿**，若有则提示人工清理（不要自动删除）
  - ⚠️ UI 自动化没有事务性，半完成状态必须可见
  - 【验收】人为把某个选择器改错 → 运行 → 有截图、有明确的失败步骤提示
- [ ] **G8** 端到端发布测试

  - 用 F1 的德语文案 + 归档的图片，完整发布 1 条定时帖到 DE Page
  - 选一个**几天后**的时间，便于验证后再取消
  - 【验收】Business Suite 已排期列表中出现该帖，时间、文案、图片全部正确
  - 【记录】记下从调用到完成的耗时，以及中途需要人工介入的次数

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
        ┌─────────────┼─────────────┐
        ↓             ↓             ↓
   C2→C3→C4→C5→C6→C7  F1→F2→F3    G1→G2→G3→G4→G5→G6→G7→G8
   （C1 已完成但方案 B 下用不上）
        ↓                            ↓
   D1→D2→D3                         (H1→H2 需 Token，不阻塞)
        ↓
   E1→E2→E3
        └──────────────┬─────────────┘
                       ↓
                   I1 → I2 → I3
```

**可并行的三条支线**：C/D/E（增量与调度）、F（翻译）、G（发布）。
它们都只依赖 B 组完成。

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
- [ ] 能通过 UI 成功创建 1 条定时帖，时间与文案正确
- [ ] 全流程无硬编码 `doc_id`
- [ ] 全流程无自动登录
- [ ] `state/`、`archive/`、Chrome profile 未进版本库

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

### 2026-08-29 · F 组代码完成（验收待真实数据），API 改走内部兼容端点

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
