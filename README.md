# FB / IG 帖子归档器

抓取指定账号的帖子正文 + 配图，落盘为统一格式，翻译成德语，供 DE 站发布消费。

运行环境：**Windows**

文档各管一件事：

| 文件                                                       | 管什么                                         |
| ---------------------------------------------------------- | ---------------------------------------------- |
| 本文件                                                     | 怎么跑、架构为什么长这样                       |
| [docs/MANUAL_STEPS.md](docs/MANUAL_STEPS.md)               | **需要你亲自动手的步骤**，逐步操作指南         |
| [docs/HANDOFF.md](docs/HANDOFF.md)                         | 红线、真实 UI 长什么样、踩过的坑（动代码前必读） |
| [docs/REQUIREMENTS.md](docs/REQUIREMENTS.md)               | 为谁做、做到什么程度算够                       |
| [docs/FUNCTIONALITY.md](docs/FUNCTIONALITY.md)             | **五个阶段各要实现什么功能**（业务访谈后的规划）|
| [docs/CONTEXT.md](docs/CONTEXT.md)                         | **术语表**。词有歧义时以它为准                 |
| [docs/OPTIMIAZATION.md](docs/OPTIMIAZATION.md)             | **还差什么、按什么顺序修**                     |
| [web/DESIGN.md](web/DESIGN.md)                             | 审校台原型的设计与接口契约                     |

> ⛔ **进度不写在文档里**，它是算出来的：`scriptsun_pipeline.bat preflight`。

## 当前自动发布状态（2026-09-01）

G6/G6c 与 G9 assisted 的代码和离线测试已经完成，但**尚未激活、尚未执行真实自动提交**。
生产提交仍由证据闸失败闭合：需要用户用新版 `probe_publish.py` 手工完成一次未来排期，
让一份完整 v2 dump 按因果顺序证明正确的 FB Page/IG 账号上下文、提交按钮、成功信号、
Planner 数据就绪，以及同一张排期卡片中相互独立的时刻、完整正文、FB、IG 与图片数语义；
还要录到 Planner 当前可见日期范围，避免把视图外占用误读成空闲。最终快照和遮罩截图
也必须完整。经人工复核后才能把这些证据回填到登记表。
显式 `--submit` 会强制启用严格 UI 约束，不会采用配置中的占位限制。随后用它做 G8；
G8 通过后才运行 `pipeline activate --g8-verified`，因此不会把激活前的历史帖子送入流水线。
FB/IG 渠道沿用 composer 默认全选，代码不点击；提交前核对目标账号上下文，提交后若结构化
回读明确缺少任一渠道则转人工。

提交状态机在任何可能点击前先耐久写入阻塞 intent；进程即使恰在 click 后崩溃也不能
自动重试。点击前还会在独立 Planner 页确认没有同槽/同文案/同素材/同渠道旧卡；提交后
若 success 与卡片都能读到 remote ID，则必须相等。G9 的付费真相源是独立追加式
`state/paid_requests.jsonl`，硬闸拒绝的已计费响应也会进入预算与 status。

## 两条路径，各自承担不同的风险

目标账号**没有管理员权限**，因此官方 API 与官方数据导出都不可用，只能外部抓取。
抓取分成两条路径，这个拆分是整个设计的核心：

| 路径     | 模块                       | 身份                            | 频次   | 封号风险       |
| -------- | -------------------------- | ------------------------------- | ------ | -------------- |
| **回填** | `routes/backfill.py`       | 登录态（专用小号）+**人工滚动** | 跑一次 | 一次性敞口     |
| **增量** | `routes/delta.py` | **登录态**（同一专用小号）+ CDP 附着 | 每天 | ⚠️ **累积性敞口** |

> ⚠️ **2026-08-30 增量方案变更（用户拍板，方案 B）。**
> 原设计是"增量完全登出"——没有账号、没有 session、没有 cookie，
> **因此没有可封的东西**。但实测 + 用户确认，**FB/IG 在没有登录态时直接报错**
> （对公开账号请求 `web_profile_info` 首次即 429），登出这条腿不存在了。
>
> 增量因此改走登录态，复用回填那条 CDP 通道。**代价必须说清楚**：
> 封号风险从「一次性敞口」变成「累积性敞口」，而抓取小号被封是本项目
> **唯一不可恢复的失败模式**——回填与增量会同时断掉。
>
> 所以 **C7「累积风险缓解」是方案的组成部分**（见 docs/HANDOFF.md 第 1 节红线 3），
> 不是锦上添花：随机化触发时刻、抓取深度上限（只滚几屏不滚到底）、
> 滚动节奏拟人、异常即停不重试、频率可降级、失败预算。
> **不要因为"跑得挺好"就把它们优化掉**——这类风险的反馈是延迟的，且只反馈一次。
>
> **两条红线没变**：仍然不得实现自动登录（复用的是人工登录留下的会话）；
> 回填仍然是人工滚动。

为什么仍要拆两条路径：回填是一次性的深度抓取（滚完整个历史），
增量是每天看一眼最新几条。两者的频率、深度、风险特征完全不同，
共用一套代码只会让两边都做不好。

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
Copy-Item .env.example .env
scripts\run_translate.bat --check
scripts\run_translate.bat --estimate
scripts\run_translate.bat --account in_neakasa.tech --limit 3
scripts\run_translate.bat --review
```

翻译德语并生成人工审校清单。**首次用前先按
[docs/MANUAL_STEPS.md](docs/MANUAL_STEPS.md) 第 6 步把 DeepSeek Key 放进 `.env`，
再按第 7 步分别试跑两个账号。** 当前直连 DeepSeek 官方 OpenAI 兼容接口，默认 High
thinking、不发送客户端输出上限，原帖话题标签逐个原样照搬；模型与术语表已配置好。

## 目录结构

```
FacebookScraper/
  README.md                本文件
  config.toml              全部可调参数，代码里零硬编码
  requirements.txt
  translate.py             德语翻译 + 审校清单（F 组）
  localize_images.py       图内英文德语化（K 组，GPT-Image-2）。
                           已实现并完成两张 high 真实技术验收；德语仍需人工审校

  scripts/                 双击入口，**纯 ASCII 壳**，逻辑在 tools/ 里
    setup.bat  start_chrome.bat  run_backfill.bat  run_translate.bat
    run_delta.bat
    start_chrome_publish.bat  发布用的专用 Chrome（端口 9223）
    run_publish.bat           待发帖离线组装预演。零浏览器/零网络/零写盘
    run_publish_post.bat      单帖准备；只有显式 --submit 才提交并回读
    run_pipeline.bat          activate / run / approve / status 流水线入口
  tools/                   .bat 的真正实现（中文提示只能待在 Python 里）
    setup.py  start_chrome.py
    replay.py               用 _capture_*.json 离线重建归档，不重新下载媒体
    layout.py               归档布局：migrate / reindex / index
    dryrun_delta.py         用增量转储离线跑完 delta_once() 的**真实代码路径**，
                            零网络零写盘。改完解析器先跑它，别用真实露面去验
    schedule.py             Windows 计划任务：xml / install / status / remove
    probe_publish.py        Business Suite v2 探查：你手工走一遍，程序记录稳定交互、
                            URL 变化、状态语义与最终日历卡片。**它不驱动页面**
    publish_post.py         G2–G6c 单帖状态机与人工故障结转入口
  docs/
    IMPLEMENTATION_PLAN.md  进度真相源
    MANUAL_STEPS.md         人工操作指南
    HANDOFF.md              会话交接
    CODE_REVIEW.md          已实现代码二次审查记录
    TRANSLATION_PLAN.md     F 组任务书（DeepSeek 德语翻译）
    IMAGE_PLAN.md           K 组任务书（GPT-Image-2 图内英文德语化）
    PUBLISH_PLAN.md         G 组任务书（Business Suite 定时发布）
    PIPELINE_PLAN.md        L 组：**唯一一份跨组文档**。把上面这些段连成一条
                            不用人管的线；自治分级、分流规则、死人开关、成本闸
  pipeline.py               L 组入口；每次从各阶段真相源对账，不建立发布任务队列
  pipeline_assisted.py      激活边界、跨平台对账、预算、needs_human 与批量审批
  prompts/
    translate_de.md         英译德提示词，可直接编辑，改它不用动 Python
    image_de.md             图片德语化提示词（已实现，在 K 分支上），同样可直接编辑
                            IMAGE_PROMPT_VERSION 现在是 2，改模板必须 +1
  core/                    库层
    config.py  chrome.py  store.py  parse.py  session.py
    http.py  integrity.py  notify.py  console.py
  routes/                  抓取路径
    backfill.py  fb_graph.py  delta.py（登录态增量主流程已实现）
  publish/                 发布路径（G 组）。生产提交证据仍故意留空，等新版 G1 dump
    compose.py              组装并跑离线硬闸（不碰浏览器，不被 G1 阻塞）
    business_suite.py       UI 自动化 + 单次提交 + 内容日历回读
    selectors.py            只登记能从 probe dump 回查的定位与成功信号
    journal.py              追加式五态发布留痕；只有 scheduled 算已发布
    workflow.py             G2–G6c 发布状态机
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

**每个新入口都要调 `core.console.force_utf8()`。** 上一条只在**控制台**成立——
本机代码页是 936，Python 的 stdout 一旦被重定向到文件或管道就回落到 GBK，
而 `⚠ ❗ ✅ ❌ ß` 在 GBK 下一个都编码不出来，print 直接把进程带走。
双击 `.bat` 时永远看不到这个故障，它只在计划任务、日志重定向这些
真正需要可靠的场合发作。`.bat` 那侧另设了 `PYTHONIOENCODING=utf-8`。

## 归档格式：每帖一个文件夹

```
archive/<平台前缀>_<账号>/
  index.html                        全账号总览（派生，双击即可看）
  manifest.jsonl                    **派生索引**，可从 posts/ 重建
  _rejected.jsonl                   被丢弃的节点及原因（不得静默丢弃）
  _orphan_media/                    重建后无主的媒体文件（移动不是删除）
  posts/
    2026-08-25_1423_<post_id>/
      post.json                     **真相源**
      text.txt                      正文纯文本（派生）
      text_de.txt                   德语译文副本（派生）
      01.jpg  02.jpg                原图，编号跟的是帖内位置。**只读，从不修改**
      media_de/                     德语版图。**程序产出 + 人工可覆盖**（K 组）
                                    ⚠️ 人工放进去的文件程序不得覆盖 ——
                                    设计同事手工修的那张一定比模型那张对
    undated_<post_id>/              时间解析不出来的进这里，**不猜**
  translated.jsonl                  德语译文的真相源
  images_de.jsonl                   德语图的真相源（K 组）。
                                    已有两张 GPT-Image-2 high 真实产出与 usage；
                                    其余图片不会在未单独授权时自动补跑
  review.md                         人工审校清单。K 组落地后它同时是
                                    **德语图唯一的验收关口**（预扫描已按用户决定取消，
                                    机器读不出"德语对不对"）
  _capture_*.json                   原始响应转储，离线重放的唯一输入
```

**`media_de/` 的语义在 2026-08-31 改过。** 它原本是「设计同事回填」——
计划第 0 节把"图内英文德语替换"排除在本期之外、走人工。
用户当天拍板改为程序用 `gpt-image-2` 自动完成（K 组），
所以现在是**程序产出、人工可覆盖**。优先级是人工 > 程序，不能反过来。

**文件夹名 `<日期>_<时分>_<post_id>`**：日期在前，按名称排序即按时间排序；
post_id 在后，幂等查找不用打开文件。

`post.json` 每篇：

```json
{
  "post_id": "...", "platform": "instagram", "account": "neakasa.tech",
  "owner": "neakasa.tech", "owner_name": "Neakasa",
  "text": "正文", "created_at": "2026-08-01T12:00:00Z",
  "permalink": "https://...", "source_route": "backfill",
  "media": [{"url": "https://...", "kind": "image",
             "local_path": "posts/2026-08-01_1200_x/01.jpg", "ocr_text": null}],
  "media_complete": true
}
```

### 三条必须守住的规则

1. **`post.json` 是真相，`manifest.jsonl` 是派生索引。** 冲突时以文件夹为准，
   跑 `python -m tools.layout reindex <平台>` 重建——**永远不反过来**。
   方向必须单一，否则会退化成"两个都不可信"。
2. **译文的真相源是账号级的 `translated.jsonl`**，不是文件夹里的 `text_de.txt`
   （那是派生副本）。manifest 是重跑抓取就能重现的事实，译文是花钱买的加工结果，
   混在一起会导致"重抓一次把译文冲掉"。
3. **`owner` 与 `account` 必须分开。** `account` 是我们要抓的目标，
   `owner` 是节点自己声明的归属。人工滚动时页面会加载推荐内容和被 @ 的 UGC——
   2026-08-30 实测一次 IG 回填混进了 266 条来自另外 195 个账号的帖子。
   不筛的话下游会翻译并发布他人内容，**这是法务风险**。
   筛掉的必须写进 `_rejected.jsonl`，不得静默丢弃。
4. **判"这篇在不在本账号主页上"用 `parse.on_timeline_of()`，不要写 `owner == account`。**
   Instagram 的**合作帖**由一方发布、**双方主页同时显示**，节点里的
   `user.username` 只记原始发布者，合作关系在 `coauthor_producers[]` 里。
   只比 owner 的话实测丢掉 **263 篇就在本账号主页上的帖子**；
   增量看到的那一屏更极端——**36 篇里 35 篇是合作帖**。
   `owner` 仍然记**真实作者**，不改写成目标账号：谁创作的是事实，
   下游据此区分原创与合作（`index.html` 的绿标、`review.md` 的授权提示）。

`media[].ocr_text` 预留给下游 OCR 阶段回填，本层不填。
`coauthors` 是归一化小写的合作方 username 列表，原创帖为空数组。

### 丢弃不是静默的

`core/integrity.py` 的第四项检查 `check_dropped_partners()` 盯着一件事：
**被丢弃的节点里，作者是不是我们的已知合作方**（名单从归档推导，实测 IG 210 个）。
是的话就报出来——那多半意味着合作帖判定又漏判了。

它在全部真实数据上零误报（210 个合作方与历史上被丢弃的 6 个账号交集为空），
把判定退回旧实现则必响。**这一项存在的理由不是"检查得更全"，
而是 2026-08-30 那 263 篇被静默丢了很久都没人知道。**

## 出问题时：不用重滚

`routes/backfill.py` 在**解析之前**无条件把原始响应转储到 `_capture_*.json`。
解析器再怎么改坏，20 分钟的人工滚动成果都不会丢：

```
python -m tools.replay instagram --dry-run    # 先看重建结果
python -m tools.replay instagram              # 真正重建（媒体不重新下载）
```

**媒体一律不重新下载**——CDN 签名 URL 有时效，重下必然 403。
已在盘上的文件按 URL 重新关联，关联不上的移进 `_orphan_media/`。

2026-08-30 这条兜底第一次兑现价值：解析器发现三个缺陷后，
整个归档（重建时 IG 1019 篇 / FB 46 篇；2026-08-31 增量后为 1020 / 47）完全离线重建，用户一次都没有重滚。
**这条设计不许优化掉。**


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

本项目规避方式：回填让真实浏览器自己带上当周有效的 doc_id，我们只拦响应。
**任何代码都不得硬编码 doc_id。**
（原本还有一条"登出增量走不依赖 doc_id 的 `web_profile_info`"——
该端点 2026-08-30 已确认对登出访客关闭，见本文开头的 ⛔。）

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
