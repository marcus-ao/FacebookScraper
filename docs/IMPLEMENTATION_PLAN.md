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
| 增量     | **完全登出**，每天一次                     | 无账号可封；风险不随时间累积                     |
| 运行环境 | Windows 笔记本，有管理员权限，住宅 IP            | 数据中心 IP 首次请求即被拦                       |
| 发布     | Business Suite UI 自动化                         | 用户当前拿不到 API Token；API 通道保留为只读验证 |

**为什么拆两条路径**：每日定时把封号风险从"一次性"变成"累积性"——
单次会不会被封，和 365 次里会不会被封一次，是两个量级的问题。
登出增量没有账号、没有 session、没有 cookie，**因此没有可封的东西**，
最坏情况只是 IP 被临时限流，换个时间重试即可。

**本期不做**（明确排除，不要自作主张实现）：

- 图内英文文字的德语替换（一期走人工处理）
- 视频文件下载与处理
- 完整性的"帖子总数交叉校验"（大概率抓不到总数，降级为只做时间序列连续性）

---

## 1. 关键事实速查（实施时会用到，不要重新推导）

### Instagram

| 项           | 值                                                                                                          |
| ------------ | ----------------------------------------------------------------------------------------------------------- |
| 登出可用端点 | `GET https://www.instagram.com/api/v1/users/web_profile_info/?username=<name>`                            |
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
| 登出可见范围 | Page 落地视图：头像、封面、**最近 2–3 条帖子**，之后是硬登录墙           |
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
    chrome.py              CDP 附着
    store.py               归档层
    parse.py               三形态解析 + walk() 全树搜索
    session.py             仅 Pacer 限速 + SAFARI_UA 常量（已剥离全部登录逻辑）
    http.py                登出 HTTP 客户端（拒收一切 cookie）
    notify.py              Windows 通知（toast → msg → alerts.log 三级降级）
    integrity.py           连续性 / 长期零新增 / 媒体不全 三项检查
  routes/
    backfill.py            登录态回填（CDP + 人工滚动 + 响应拦截）
    delta.py *             登出增量
    fb_graph.py            API 只读（保留，未接入，缺 Token）
  publish/ *
    business_suite.py *    UI 自动化发布
    selectors.py *         选择器集中定义
  translate.py             德语翻译 + 审核清单（F1/F2/F3 三合一，子命令区分）
  tests/                   离线测试。scripts\setup.bat 用 glob 全跑，新增即自动纳入基线
    tests_backfill.py        回填收尾、超时与媒体补全（15）
    tests_chrome.py          CDP 真实性与端口占用（7）
    tests_fb_graph.py        只读 Graph 路线的视频边界与媒体失败（7）
    tests_parse.py           解析层（19）
    tests_store.py           归档层（17）
    tests_http.py            登出客户端（17）
    tests_integrity.py       完整性检查（23）
    tests_notify.py          通知降级（15）
    tests_translate.py       翻译管道 + 提示词渲染 + 金额保留强制（162，零 API 调用）
  _deprecated/             已否决路线的存档，**不要引用、不要复活**
    dyi_import.py            官方数据导出 —— 需 US 账号登录，拿不到
    intercept.py             早期拦截器 —— 已被 backfill.py + parse.py 取代
    ig_v1_feed.py            登录态 v1 feed —— 与"增量必须登出"冲突
  archive/<平台>_<账号>/    产物（已 gitignore）
    manifest.jsonl
    media/
    raw/
    _capture_*.json        原始响应转储
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
| `core/parse.py`      | 完成 | 19 项检查：三形态、跨形态合并、脏输入     | **与真实响应的匹配度** |
| `core/session.py`    | 完成 | 已剥离全部登录逻辑，仅剩 `Pacer` + `SAFARI_UA` | —                 |
| `routes/backfill.py` | 完成 | 语法                                      | **全部网络行为**       |
| `scripts\start_chrome.bat` + `tools/start_chrome.py` | 完成 | Windows 实机：config 解析、Chrome 探测、端口探测 | **实际拉起 Chrome（A3）** |
| `scripts\setup.bat` + `tools/setup.py` | 完成 | **Windows 实机全程跑通（A1）** | —              |
| `scripts\run_backfill.bat`   | 完成 | Windows 实机：壳可执行                    | **转调后的网络行为（B1）** |
| `config.toml`        | 完成 | 可解析；Windows 上 Chrome/profile/port 三项均正确解析 | —      |

**已知的最大未知数**：`core/parse.py` 的解析器是按已知响应结构编写的，
**尚未与真实响应比对过**。B 组任务专门处理这件事。
`backfill.py` 已内置无条件原始转储（解析之前执行），因此即使解析全部失败，
数据也不会丢，无需重滚。

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
- [ ] **A3** 起专用 Chrome 并确认调试端口

  - 双击 `scripts\start_chrome.bat`。脚本会自行轮询等待端口就绪并打印 `[ok]`
  - 若 Chrome 路径探测失败，按 A1 记下的路径手工编辑 bat 顶部的 `CHROME` 变量
  - ⚠️ **已知坑**：若该 `user-data-dir` 已被另一个 Chrome 实例占用，
    Chrome 会静默复用已有实例并**忽略 `--remote-debugging-port` 参数**，
    表现为"脚本明明跑了但端口没开"。脚本已内置检测与提示；
    遇到时先在任务管理器确认没有残留的 `chrome.exe` 进程
  - 【验收】脚本打印 `[ok] 调试端口 9222 已就绪`；且
    `python -c "import sys; sys.path.insert(0,'.'); from core.chrome import port_open; print(port_open(9222))"`
    输出 `True`
- [ ] **A4** 小号登录并确认会话持久化

  - 在 A3 打开的窗口里登录抓取用的小号（**手工登录，含二次验证**）
  - 关闭该窗口，重新双击 `scripts\start_chrome.bat`
  - 【验收】重新打开后仍是登录状态（说明 profile 目录生效）
  - ⚠️ 该 profile 目录含已登录会话，确认已在 `.gitignore` 中
- [ ] **A5** 填写抓取目标

  - 编辑 `config.toml` 的 `[targets]`，填入真实的 FB Page 用户名和 IG handle
  - 【验收】`https://www.facebook.com/<填的值>` 和 `https://www.instagram.com/<填的值>` 在浏览器里能打开正确页面

---

## B. 回填校准（依赖 A）

> 本组的目的不是"写代码"，而是**用真实数据校准已有的解析器**。
> 顺序很重要：先跑一次拿到真实响应，再改解析器，不要反过来。

- [ ] **B1** 首次回填运行 — Facebook

  - 确保 A3 的 Chrome 窗口开着且小号已登录
  - 双击 `scripts\run_backfill.bat facebook`（或激活 venv 后 `python -m routes.backfill facebook`）
  - 按提示在浏览器里**手工向下滚动**，直到看见最早的帖子。慢滚，等图片加载出来再继续
  - 滚完回终端按 Enter
  - 【验收】`archive/fa_<账号>/_capture_<时间戳>.json` 存在且大于 100 KB
  - 【记录】记下：捕获了多少段 JSON、解析出多少篇帖子
  - ⚠️ 若解析出 0 篇，**不要重滚**，直接进 B2
- [ ] **B2** 校准 Facebook 解析器

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
- [ ] **B3** 首次回填运行 — Instagram

  - 同 B1，命令为 `scripts\run_backfill.bat instagram`
  - 【验收】同 B1
  - 【记录】同 B1
- [ ] **B4** 校准 Instagram 解析器

  - 对照 `core/parse.py` 的 `is_iphone_struct()` / `from_iphone_struct()`
  - 重点确认：
    - `image_versions2.candidates[0]` 是否确为最大尺寸（打印前 3 个候选的宽高验证）
    - `carousel_media` 是否存在于轮播帖
    - 视频帖是否有 `video_versions`
  - 【验收】同 B2
  - 【记录】同 B2
- [ ] **B5** 媒体下载验证

  - 检查 `archive/*/media/` 下的图片文件
  - 【验收】
    - 文件数与 manifest 中 `kind=="image"` 的媒体数一致
    - 随机抽 3 张能正常打开，且分辨率与 manifest 记录的 `width`/`height` 一致
    - **无 0 字节文件**
    - 视频**没有**被下载（范围外），但 manifest 里有记录
  - 【记录】记下图片的典型分辨率。若普遍低于 1000px，在完成行标注
    "下游图像处理输入质量受限"
- [ ] **B6** 回填结果盘点

  - 统计：总帖数、图文帖数、视频帖数、`media_complete=False` 的帖数
  - 【验收】总数与你在浏览器里目测的帖子数量大致相符（差异 >20% 需排查）
  - 【记录】写下四个数字

---

## C. 登出增量（依赖 B4，共用校准后的解析器）

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
  > 其它：`IG_APP_ID` / `IG_ASBD_ID` 作为模块常量写死。这两个是公开且长期稳定的
  > 值，与每 2–4 周轮换的 `doc_id` 是两回事，不违反禁止事项 2（测试里有一条
  > 断言 header 中不含 `doc_id`）。`logged_out_client(**kwargs)` 支持传
  > `transport=` 以便离线测试，也支持 `headers=` 合并自定义头而不冲掉默认 UA。
- [ ] **C2** 实现 Instagram 登出增量

  - 在 `routes/delta.py` 中实现 `fetch_instagram(account: str) -> list[Post]`
  - 流程：
    1. `GET https://www.instagram.com/api/v1/users/web_profile_info/?username=<account>`，带 C1 的 header
    2. 从 `data.user.edge_owner_to_timeline_media.edges[].node` 取节点
    3. 交给 `core.parse.extract(...)`（GraphQL 形态分支），`route="delta"`
  - 错误处理：
    - 响应含 `"login"` 重定向或返回 HTML 而非 JSON → 判定为**登录墙触发**，
      打印明确提示并返回空列表，**不要重试**（重试会加重触发）
    - HTTP 429 → 按 `Pacer.backoff()` 退避，最多 2 次
  - 【验收】对一个已知的公开账号运行，能返回 ≥1 篇 Post 且 `text` 非空
- [ ] **C3** 实现 Facebook 登出增量

  - 在 `routes/delta.py` 中实现 `fetch_facebook(account: str) -> list[Post]`
  - ⚠️ **这一项难度高于 C2，且可能不成立。** FB 对登出访客只给落地视图，
    且返回的多为服务端渲染 HTML 而非 JSON
  - 实施步骤：
    1. **先探查**：用无痕窗口访问 `https://www.facebook.com/<account>`，
       在 DevTools Network 里确认登出状态下有没有可用的 JSON 响应
    2. 若**有** JSON → 按 C2 的模式实现
    3. 若**只有 HTML** → 从 HTML 里提取内嵌的 JSON（FB 常把数据放在
       `<script type="application/json">` 里），或退化为解析 DOM 提取
       最近 2–3 条帖子的正文与图片 URL
    4. 若**完全被墙** → 记录该结论，FB 的增量降级为"定期人工触发一次回填"
  - 【验收】三种结果中任一种，只要**结论明确并记录**即算完成
  - 【记录】必须写清楚走的是哪条分支、以及登出到底能看到几条帖子
- [ ] **C4** 增量的媒体下载

  - 复用 C1 的 client 下载图片；视频跳过
  - ⚠️ 媒体 URL 有时效，必须在同一次运行内立刻下载
  - 【验收】新抓到的帖子在 `media/` 下有对应文件，无 0 字节
- [ ] **C5** 运行状态记录

  - 在 `state/` 下维护 `delta_state.json`：
    ```json
    {"facebook": {"last_success": "2026-08-29T10:00:00Z", "last_new_count": 0,
                  "consecutive_quiet_days": 3},
     "instagram": {...}}
    ```
  - 每次增量运行后更新
  - 【验收】连续运行两次，`last_success` 被正确刷新
- [ ] **C6** 增量主入口

  - `routes/delta.py` 的 `__main__`：依次跑 FB 和 IG，写归档，更新状态
  - 支持 `--platform facebook|instagram|all`（默认 all）
  - 支持 `--dry-run`（只抓不写盘，用于调试）
  - 【验收】`python -m routes.delta --dry-run` 能完整跑完并打印将要新增的帖子

---

## D. 完整性检查与告警（依赖 C）

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

- [ ] **E1** 编写 `scripts\run_delta.bat`

  - 内容：激活 venv → `cd` 到项目目录 → `python -m routes.delta` → 记录退出码
  - 输出**追加**到 `state/delta.log`（含时间戳），不要覆盖
  - 【验收】双击能完整跑完，日志有新增行
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
                     B5 → B6
                      ↓
        ┌─────────────┼─────────────┐
        ↓             ↓             ↓
   C1→C2→C3→C4→C5→C6  F1→F2→F3    G1→G2→G3→G4→G5→G6→G7→G8
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
- [ ] `media/` 下无 0 字节文件，分辨率与 manifest 一致
- [ ] 视频未被下载，但 manifest 中有记录
- [ ] 登出增量能发现新帖，且**运行时不携带任何 cookie**
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
