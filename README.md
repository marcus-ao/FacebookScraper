# FB / IG 帖子归档器

抓取指定账号的帖子正文 + 配图，落盘为统一格式，翻译成德语，供 DE 站发布消费。

运行环境：**Windows**

文档各管一件事：

| 文件                                                       | 管什么                                         |
| ---------------------------------------------------------- | ---------------------------------------------- |
| 本文件                                                     | 怎么跑、架构为什么长这样                       |
| [docs/MANUAL_STEPS.md](docs/MANUAL_STEPS.md)               | **需要你亲自动手的步骤**，逐步操作指南         |
| [docs/IMPLEMENTATION_PLAN.md](docs/IMPLEMENTATION_PLAN.md) | 进度真相源：全部任务、验收、已完成项的实际偏差 |
| [docs/CODE_REVIEW.md](docs/CODE_REVIEW.md)                 | 已实现代码的二次审查、修复与验证记录           |

## 两条路径，各自承担不同的风险

目标账号**没有管理员权限**，因此官方 API 与官方数据导出都不可用，只能外部抓取。
抓取分成两条路径，这个拆分是整个设计的核心：

| 路径     | 模块                       | 身份                            | 频次   | 封号风险       |
| -------- | -------------------------- | ------------------------------- | ------ | -------------- |
| **回填** | `routes/backfill.py`       | 登录态（专用小号）+**人工滚动** | 跑一次 | 一次性敞口     |
| **增量** | `routes/delta.py` _(待建)_ | **完全登出**，无 cookie         | 每天   | **无账号可封** |

为什么拆：每日定时会把风险从"一次性"变成"累积性"——单次会不会被封，
和 365 次里会不会被封一次，是两个量级的问题。登出增量没有会话可以丢，
最坏情况只是 IP 被临时限流，换个时间重试即可。

回填之所以让人工滚动而不是脚本自动滚，是因为**滚动的确实是人**，
没有可识别的自动化行为特征。代价是你要花 20 分钟手动滚一次。

两条路径共用 `core/store.py` 的归档 schema 和 `core/parse.py` 的解析器。

## 快速开始（Windows）

```
scripts\setup.bat
```

建 venv、装依赖、装 Playwright 驱动、跑全部离线测试。只需一次。

然后编辑 `config.toml` 的 `[targets]`，填入真实账号名。

```
scripts\start_chrome.bat
```

起专用 Chrome 并开调试端口。**首次运行时在打开的窗口里手工登录抓取小号**
（含二次验证）。该窗口要一直开着。

```
scripts\run_backfill.bat facebook
```

在浏览器里手工向下滚到最早的帖子，回终端按 Enter。IG 同理换成 `instagram`。

```
scripts\run_translate.bat --check
scripts\run_translate.bat --limit 3
scripts\run_translate.bat --review
```

翻译德语并生成人工审校清单。**首次用前先按
[docs/MANUAL_STEPS.md](docs/MANUAL_STEPS.md) 第 6 步配置 API 网关与品牌语域。**

## 目录结构

```
FacebookScraper/
  README.md                本文件
  config.toml              全部可调参数，代码里零硬编码
  requirements.txt
  translate.py             德语翻译 + 审校清单（F 组）

  scripts/                 双击入口，**纯 ASCII 壳**，逻辑在 tools/ 里
    setup.bat  start_chrome.bat  run_backfill.bat  run_translate.bat
  tools/                   .bat 的真正实现（中文提示只能待在 Python 里）
    setup.py  start_chrome.py
  docs/
    IMPLEMENTATION_PLAN.md  进度真相源
    MANUAL_STEPS.md         人工操作指南
    HANDOFF.md              会话交接
    CODE_REVIEW.md          已实现代码二次审查记录
  prompts/
    translate_de.md         英译德提示词，可直接编辑，改它不用动 Python
  core/                    库层
    config.py  chrome.py  store.py  parse.py  session.py
    http.py  integrity.py  notify.py
  routes/                  抓取路径
    backfill.py  fb_graph.py  (delta.py 待建)
  tests/                   离线测试，setup.bat 用 glob 全跑
  _deprecated/             已否决路线的存档，不要引用、不要复活

  archive/                 抓取产物（gitignore）
  state/                   运行状态（gitignore）
  .env                     API 密钥（gitignore）
```

**`.bat` 必须是纯 ASCII + CRLF。** cmd.exe 解析含中文的批处理不可靠——
实测行会被从中间劈开、后半段当命令执行，加不加 `chcp 65001` 都会犯。
所有中文提示放在 `tools/*.py`（Python 在 Windows 控制台走 Unicode API，
任何码页下都正确显示）。`.gitattributes` 锁了 CRLF，ASCII 得靠人守。

## 归档格式

```
archive/<platform>_<account>/
  manifest.jsonl          每行一个 Post，抓取产物，**不要手工改**
  translated.jsonl        德语译文，独立文件，重跑抓取不会冲掉它
  review.md               人工审校清单
  media/<post_id>_<n>.jpg
  raw/<post_id>.json      原始响应，schema 变更后可重放
  _capture_*.json         回填时的原始响应转储
```

`manifest.jsonl` 每行：

```json
{
  "post_id": "...",
  "platform": "instagram",
  "account": "nasa",
  "text": "正文",
  "created_at": "2026-08-01T12:00:00Z",
  "permalink": "https://...",
  "source_route": "backfill",
  "media": [
    {
      "url": "https://...",
      "kind": "image",
      "local_path": "media/xxx_0.jpg",
      "ocr_text": null
    }
  ],
  "media_complete": true
}
```

`media[].ocr_text` 预留给下游 OCR 阶段回填，本层不填。

**译文写在独立的 `translated.jsonl`，不动 manifest。** manifest 是重跑抓取
就能重现的事实，译文是花钱买的加工结果，混在一起会导致"重抓一次把译文冲掉"。

## 只允许存在一条登录路径

人工在 `scripts\start_chrome.bat` 起的专用 Chrome 里登录一次，会话留在该 Chrome
的 profile 目录（`%USERPROFILE%\.fbscraper-chrome`），由 CDP 附着复用。

**不要再引入第二条**——不要写自动登录，不要用 Playwright 自己启浏览器存
`storageState`。多一条路径就多一份被 checkpoint 拦截的机会，且两份会话状态
必然漂移。`core/session.py` 已刻意剥离全部登录逻辑，只剩限速器和 UA 常量。

## 四条必须遵守的运行约束

1. **永不自动登录。** 人工在 `scripts\start_chrome.bat` 的窗口里登录一次，
   会话留在专用 Chrome profile 目录，由 CDP 复用。
   自动登录是触发 checkpoint 的最高频动作。
2. **从住宅 IP 跑。** 数据中心 IP 在首次请求就被拦——
   VPS、云函数、GitHub Actions 全部不可用。用家里宽带。
3. **不要用主账号。** 抓取用的是专用小号，独立 Chrome profile，
   不与日常浏览共享指纹。发布用的账号建议再开第三个 profile，
   避免抓取小号被标记时牵连持有 DE 资产的发布账号。
4. **并发恒为 1，不要移除随机间隔。** `core/session.py` 的 `Pacer`
   默认 6–14 秒随机间隔（约 250–600 请求/小时），明显低于观测到的封禁阈值。
   抓几十篇帖子总共几分钟，没有任何值得用速度换的东西。

## 已知失效模式

**IG GraphQL 的 doc_id 每 2–4 周轮换。** 轮换后旧 doc_id 返回 401，
错误文案是 "Please wait a few minutes before you try again" —— 这个提示是
**误导的**，它不是限流，等多久都不会恢复。硬编码 doc_id 的工具
（包括 instaloader 的 `Profile.get_posts()`）会周期性失效。

本项目规避方式：登出增量走不依赖 doc_id 的 `web_profile_info` 端点；
回填让真实浏览器自己带上当周有效的 doc_id，我们只拦响应。**任何代码都不得硬编码 doc_id。**

**mbasic.facebook.com 不要用作地基。** 官宣 2024-12-03 下线，
2026 年仍有解析记录但行为不稳定。依赖它的老教程和库（如 `facebook-scraper`）
应视为不可靠。

**媒体 CDN URL 带签名且有时效**，必须在拿到响应后立刻下载，
不能先存 URL 事后再取。

**默认 PyPI 源在国内网络吞吐近 0。** `scripts\setup.bat` 默认走清华镜像，
境外网络可设 `PYPI_INDEX_URL=` 空值改回官方源。

## 为什么拦截响应而不是解析 DOM

FB/IG 都是 React SPA，页面 class name 是构建期混淆的，每周都可能变；
靠 CSS 选择器的脚本必然持续失修。而接口返回的 JSON 结构由后端决定，
变更频率低一个数量级。

`routes/backfill.py` 因此让真实浏览器去滚动加载——它自己会带上正确的
`doc_id`、`fb_dtsg`、`X-FB-LSD` 等一堆参数，你不需要逆向任何一个——
我们只在 `page.on("response")` 里把 JSON 捞出来，用全树搜索定位帖子节点
（`core/parse.py` 里的 `walk()`），而不是写死字段路径。
