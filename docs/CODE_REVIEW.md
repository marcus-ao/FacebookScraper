# 已实现代码二次审查记录

> 审查日期：2026-08-29  
> 审查方式：缺陷优先；只修复已实现主干中的可复现问题，不扩展未实现功能。  
> 最终验证：9 套离线测试、282 项检查全部通过；未调用真实社媒或翻译 API。

## 1. 审查边界

本次以 `docs/IMPLEMENTATION_PLAN.md` 的当前状态为边界，审查并验证了以下已实现代码：

- 配置、Chrome/CDP 附着、归档、解析、限速：`core/`
- 登录态人工回填与保留的只读 Graph 抓取：`routes/backfill.py`、`routes/fb_graph.py`
- 登出 HTTP 客户端、完整性检查、Windows 通知：`core/http.py`、
  `core/integrity.py`、`core/notify.py`
- 德语翻译、提示词渲染、金额保护、审校清单：`translate.py`
- Windows 启动与环境入口：`tools/`、`scripts/`
- 对应离线测试、配置、依赖与调用点

以下内容按项目边界明确没有实现或扩展：

- C2–C6 登出增量、D3 检查接入、E 组计划任务
- G 组 Business Suite 上传/定时发布、H1/H2 API 验证增量
- 图内文字自动处理、视频下载
- `_deprecated/` 中的废弃路线（仅确认活跃代码没有重新引用它们）

仓库在审查开始时没有可用的已提交基线，项目文件全部处于未跟踪状态；因此本次不是
基于 Git diff，而是按当前实现、文档约束、测试和调用链进行完整的已实现部分审查。

## 2. 已确认并修复的问题

### CR-01 · P1 · Bearer 鉴权同时夹带占位 API Key

- **位置**：`translate.py::build_client`
- **问题**：Bearer 模式同时传入 `auth_token=<真实密钥>` 与
  `api_key="unused-when-auth-token-is-set"`。当前 SDK 会实际发送两个头：
  `Authorization: Bearer <真实密钥>` 和 `X-Api-Key: unused-...`。严格网关可能拒绝、
  错选或审计到错误凭据，导致文档声明支持的 Bearer 路径无法工作。
- **修复**：Bearer 模式只传 `auth_token`；`x-api-key` 模式只传 `api_key`；
  未知 `auth_style` 在发请求前明确失败。
- **回归验证**：离线检查两种客户端的最终 `auth_headers`，保证二者互斥。

### CR-02 · P1 · 金额保护存在多种可稳定绕过的假阴性

- **位置**：`translate.py::money_preserved`、`_MONEY_TOKEN_RE`
- **问题**：原实现用字符串子串判断，导致 `$5` 会错误命中 `$50`；同一金额在原文
  出现两次、译文只保留一次也会放行；后置符号 `50 €` 因正则词边界无法识别；
  模型保留 `$49.99` 同时新增 `45 €` 换算价也会通过。这些都绕过了项目最重要的
  商业安全检查。
- **修复**：改为金额 token 的精确多重集比对；只忽略空白，严格比较数值、分隔符、
  币种、符号位置和出现次数；同时拒绝译文新增的金额；修正后置货币符号正则。
- **回归验证**：新增前缀冲突、重复金额、后置符号、追加换算价、凭空新增金额等用例。

### CR-03 · P1 · 回填会跳过本应补全的残缺帖子

- **位置**：`routes/backfill.py::run`、`core/store.py::Archive.should_append`
- **问题**：归档层已经支持“残缺增量记录 → 完整回填记录”的升级，但回填入口先执行
  `arc.has(post_id)` 并直接跳过，导致该升级路径永远到不了 `Archive.append()`。
- **修复**：新增与 `append()` 同语义的下载前判定 `should_append()`；完整重复帖仍跳过，
  残缺帖允许进入下载并追加升级，避免重复 CDN 请求的同时恢复补全能力。
- **回归验证**：主流程伪造一个已归档的残缺封面帖，确认回填后升级为完整两图记录。

### CR-04 · P1 · 媒体下载失败被永久记成“完整”

- **位置**：`routes/backfill.py::_download`
- **问题**：403、异常或空响应只打印错误，`post.media_complete` 仍保持 `True`；随后记录
  被视为完整，幂等重跑会跳过，丢失图片既不进入完整性清单，也没有自动重试机会。
- **修复**：任一已知图片请求失败或返回空体时将 `media_complete=False`；成功文件必须
  非空才写入 `local_path`。下一次回填可通过 CR-03 的升级判定重新尝试。视频仍不下载。
- **回归验证**：一张成功、一张空响应、一段视频的组合会保留成功文件、跳过视频并把
  整帖标成可重试的媒体不完整状态。

### CR-05 · P2 · 用户按 Enter 时最后几个响应可能尚未进入 capture

- **位置**：`routes/backfill.py::Collector`
- **问题**：响应回调用 `asyncio.create_task()` 后不保存任务。用户完成滚动按 Enter 时，
  代码会立即转储 `payloads`；仍在 `await response.text()` 的最后几个任务可能被漏掉，
  随后页面关闭又会取消它们，形成无提示的数据缺口。
- **修复**：Collector 登记所有响应任务；停止监听新响应后统一 `drain()`，确认已到达的
  响应体全部读取、异常全部消费，再写 `_capture_*.json`。
- **回归验证**：用受控异步响应证明转储前尚为空、`drain()` 后最后一段 JSON 已存在。

### CR-06 · P2 · 最长会话超时可能仍被 stdin 永久拖住

- **位置**：`routes/backfill.py::_stdin_waiter`
- **问题**：原实现通过默认 executor 执行阻塞的 `sys.stdin.readline()`。达到
  `max_session_seconds` 后主协程虽然跳出，但 `asyncio.run()` 关闭默认 executor 时会
  等待该读取线程，实际进程仍可能卡到用户按 Enter，“自动收尾”名存实亡。
- **修复**：改用守护线程和 `threading.Event`；超时后守护线程不会参与进程退出等待，
  正常按 Enter 仍能即时通知异步主循环。
- **回归验证**：受控阻塞输入确认线程为 daemon、不会误报完成，并能在输入结束后通知。

### CR-07 · P2 · 任意端口监听器会被误报成专用 Chrome

- **位置**：`core/chrome.py::cdp_ready`、`tools/start_chrome.py::main`
- **问题**：旧逻辑只做 TCP connect。只要其它程序占用 9222，就会提示“专用 Chrome
  已运行”并返回成功，后续 CDP 附着才失败；启动脚本中“端口被其它程序占用”的提示
  实际到不了。
- **修复**：请求 Chrome 标准 `/json/version`，仅在返回合法
  `webSocketDebuggerUrl` 时视为就绪；普通监听器会给出明确的端口占用错误。CDP 连接
  失败或无上下文时同时清理 Playwright 资源。
- **回归验证**：本地 HTTP 服务分别返回普通 JSON、合法 CDP JSON、脏响应；只有合法
  CDP 响应通过，且启动器不会在被占用端口继续拉起 Chrome。

### CR-08 · P2 · 同帖多响应媒体数相同时会丢正文

- **位置**：`core/parse.py::_merge_post`
- **问题**：旧逻辑只有“新响应媒体更多”才替换。同一帖子同时出现在 GraphQL 与
  `iphone_struct` 中、两份媒体数相同但先到响应正文为空时，后到的正文不会补入，
  下游会把该帖当成无文案并跳过翻译。媒体更多但正文为空的响应也会覆盖正文信息。
- **修复**：按媒体数量、媒体完整度和非空字段选主记录，并从另一响应补齐正文、时间、
  permalink；仍以媒体更全为首要优先级。
- **回归验证**：覆盖“同媒体数后到正文”和“更多媒体但正文为空”两种交叉形态。

### CR-09 · P2 · `--limit N` 会对每个账号各执行 N 次

- **位置**：`translate.py::main`
- **问题**：CLI 文案和人工指南把 `--limit 3` 定义为三篇试跑，但主循环向每个账号
  都传入 3；FB 与 IG 两个归档同时存在时会调用 6 次 API，突破试跑成本边界。
- **修复**：在账号循环外维护总剩余额度，成功与失败的实际尝试都消耗额度；负数直接
  由 argparse 拒绝。
- **回归验证**：两个账号下 `--limit 1` 只进入一次处理调用。

### CR-10 · P2 · 新增或拼错的提示词占位符会原样发给模型

- **位置**：`translate.py::build_system_prompt`
- **问题**：旧校验只查代码已知的占位符是否残留。营销同事在模板中新增或拼错
  `{{PLACEHOLDER}}` 时，它不在已知列表中，反而不会被发现，会原样进入正式请求。
- **修复**：替换前扫描模板全部双花括号 token，未知项立即失败；扫描发生在风格示例
  注入前，避免把真实文案中的 `{{...}}` 误判为模板错误。
- **回归验证**：拼错占位符被拒绝，风格示例中的字面双花括号正常通过。

### CR-11 · P2 · 只读 Graph 路线会下载视频，并把图片失败记成完整

- **位置**：`routes/fb_graph.py::_download_images`、`scrape_page`、
  `scrape_ig_professional`
- **问题**：两个已经存在的只读抓取函数遍历全部 `post.media`，其中 Instagram
  `kind="video"` 的 `media_url` 是实际视频，会被下载，违反“视频只记元数据”的固化
  边界；图片 HTTP 失败又被静默跳过并按完整帖归档，后续因 `arc.has()` 永久跳过。
- **修复**：集中为只下载图片的 helper；视频不发请求，图片空体/HTTP 失败明确打印并
  标记 `media_complete=False`；调用方复用 `Archive.should_append()`，允许以后补全但拒绝
  完整帖被重复下载。没有新增 H1/H2、Token 或写入 Meta 的行为。
- **回归验证**：图片成功、空体、403 与视频混合输入中，只有图片会请求；成功文件非空，
  两种失败均保留可重试状态，视频只有 URL/kind 元数据。

## 3. 修改文件

- 生产代码：`core/chrome.py`、`core/parse.py`、`core/store.py`、
  `routes/backfill.py`、`routes/fb_graph.py`、`tools/start_chrome.py`、`translate.py`
- 新增测试：`tests/tests_backfill.py`、`tests/tests_chrome.py`、
  `tests/tests_fb_graph.py`
- 扩充测试：`tests/tests_parse.py`、`tests/tests_store.py`、
  `tests/tests_translate.py`
- 文档：本文件，以及当前测试基线数字的同步更新

## 4. 验证结果

### 4.1 项目自带测试发现机制

通过 `tools/setup.py::run_tests()` 运行，确认新测试会被现有 glob 自动纳入：

| 测试文件 | 检查数 | 结果 |
| --- | ---: | --- |
| `tests_backfill.py` | 15 | PASS |
| `tests_chrome.py` | 7 | PASS |
| `tests_fb_graph.py` | 7 | PASS |
| `tests_http.py` | 17 | PASS |
| `tests_integrity.py` | 23 | PASS |
| `tests_notify.py` | 15 | PASS |
| `tests_parse.py` | 19 | PASS |
| `tests_store.py` | 17 | PASS |
| `tests_translate.py` | 162 | PASS |
| **合计** | **282** | **全部通过** |

### 4.2 其它检查

- `python -W error::SyntaxWarning -m compileall -q core routes tools translate.py tests`：通过
- `.bat` 文件：仍为纯 ASCII、仅 CRLF；本次没有修改批处理文件
- 禁止项搜索：没有新增自动登录、硬编码 Instagram `doc_id`、`mbasic` 依赖、
  `_deprecated` 活跃引用、视频下载或 Business Suite 选择器
- `ruff check`：仍报告 14 条审查前就存在的非功能性样式项（测试文件的单行写法、
  未使用 import、无插值 f-string）；按本次“只修可行动功能缺陷、不做样式扩散”的边界
  没有机械清理，也没有把它们作为功能验收门槛

## 5. 未验证与残余风险

这些不是本次代码修复可以替代的验收，仍应按原计划执行：

1. `core/parse.py` 尚未用真实 Facebook/Instagram capture 校准；B1–B4 仍是最大运行时风险。
2. 真实 Chrome 启动、CDP 附着、人工滚动与 CDN 下载仍需 A3/B1/B3/B5 实机验收；
   本次只验证了控制流和本地假响应。
3. 真实 Anthropic 兼容网关、模型名、鉴权策略及德语输出质量仍需 `--check` 和 F1/F2
   的三篇真实试跑；本次没有读取密钥，也没有发出网络调用。
4. CDN 签名 URL 在长时间人工滚动后的实际有效期仍未知；若 B5 出现 403，应按现有
   capture 离线排查下载时机，不应在没有真实证据前重构抓取路线。
5. C2–C6、D3、E、G、H 尚未实现的功能没有被本次“修复”伪装成完成，计划勾选状态
   保持不变。

## 6. 项目目录重命名复核（2026-08-29）

项目目录从 `scraper` 政名为 `FacebookScraper` 后，针对已实现部分补做了路径专项复核：

- 活动生产代码、`config.toml`、批处理脚本和测试中，旧项目路径匹配数为 0。
- Python 入口均通过 `Path(__file__).resolve()` 推导根目录，四个 `.bat` 均通过
  `%~dp0..` 回到根目录；两类入口都不依赖项目文件夹名称。
- `[paths]` 下的 `archive`、`state` 仍正确解析到新根目录；提示词、依赖文件和
  `config.toml` 的实际目标均存在。
- `.venv` 的解释器、激活脚本、入口程序和 `pyvenv.cfg` 均指向新目录；系统计划任务、
  `VIRTUAL_ENV`、`PYTHONPATH` 和进程 `PATH` 中未发现旧项目路径。
- 改名遗留的 Python/Ruff 生成缓存已重建或清理。人工操作说明改用“项目根目录”表述，
  不再把本机工作区绝对路径复制进命令。

复核后再次运行 9 套离线测试，282 项检查全部通过；根目录、归档目录、状态目录、
提示词路径和 venv 解释器的专项断言也全部通过。未调用 Chrome、社媒或翻译 API。

## 7. 真实数据暴露的缺陷（2026-08-30）

B1/B3 的首次真实回填跑完后，对两份 `_capture_*.json` 与产出的 `manifest.jsonl`
做了逐条核对。**解析器在真实响应上失效的方式，和离线测试里假设的完全不同**——
测试构造的是"结构正确的单一账号响应"，真实响应里混着推荐内容、被 @ 的 UGC、
轮播子项和视频帖。以下四项都**已用真实数据证实**，不是推测。

原始证据（可复现）：

| 平台 | capture | 段数 | manifest 行数 |
| --- | --- | ---: | ---: |
| Facebook | `archive/fa_neakasaofficial/_capture_1788072462.json`（7.7 MB） | 220 | 47 |
| Instagram | `archive/in_neakasa.tech/_capture_1788073013.json`（17.2 MB） | 99 | 1500 |

### CR-12 · P0 · 归档写入了其它账号的帖子，且被标成本账号

- **位置**：`core/parse.py::from_iphone_struct` / `from_graphql_node` / `from_fb_story`
- **问题**：三个构造函数都把**调用方传入的** `account` 直接写进 `Post.account`，
  **从不检查节点自身的归属**。而 `extract()` 走的是 `walk()` 全树搜索，
  人工滚动时页面加载的推荐内容、被 @ 的 UGC、关联账号帖子全在同一批响应里。
- **实测**：Instagram 1500 条去重记录中，仅 **756 条**的 owner 是 `neakasa.tech`；
  **266 条来自另外 195 个账号**（`neakasa.global` 30 条、`neakasa.de` 4 条，
  其余为宠物 UGC 账号，如 `ruka.bsh`、`daria.and.zoe` 等各 2–3 条）。
  Facebook 47 条中 1 条 owner 是 `The Garden State Cat Club`
  （`post_id=1514468247386817`，长度 16 而非本账号的 18，且无 `created_at`）。
- **业务后果**：这不只是数据脏。下游 `translate.py` 会翻译他人文案，
  G 组发布会把第三方 UGC 当作自家内容发到 DE Page —— **法务风险**。
- **修复方向**：`Post` 增加 `owner` 字段，取自节点自身
  （IG：`node.user.username` 或 `node.owner.username`；FB：`node.actors[0].name`/`id`）。
  `extract()` 增加目标账号过滤：owner 与目标不一致的丢弃，
  但**必须写进 `_rejected.jsonl`**（post_id + owner + 原因），不得静默丢弃。
  ⚠️ IG 的 owner 匹配要注意大小写与 `username` vs `full_name` 的区别；
  FB 的 `actors[0].name` 是**显示名**（"Neakasa Official"）而非 URL 里的
  `neakasaofficial`，两者不相等，不能直接字符串比对——需要在首次回填时
  从响应里确定本账号的 page id / 显示名并记录下来。

### CR-13 · P0 · 轮播子项被当成独立帖子写入

- **位置**：`core/parse.py::is_iphone_struct`
- **问题**：判定写的是 `"pk" in d and "code" in d and "taken_at" in d` ——
  **判断的是键存在，不是值非空**。Instagram 的 `carousel_media` 子项里
  `code` 这个键确实存在，但值是 `None`；`pk` 和 `taken_at` 也都在。
  于是每个子项都命中判定，被 `from_iphone_struct` 构造成一篇独立帖子。
- **实测**：Instagram 1500 条中 **478 条是 `product_type == "carousel_item"` 的子项**
  （全部无 owner 字段、`code` 为 `None`）。它们没有 caption，
  因此 manifest 里 487 条空正文记录绝大多数来自这里。
  轮播父帖共 145 篇。
- **清洗无损性已验证**：145 个父帖的 `media` 数量**全部 ≥ 其子项数**
  （不足的 0 个），说明父帖已收全所有子图，删除这 478 条子项行不丢任何图片。
- **修复方向**：判定改为值非空（`d.get("code")` 而非 `"code" in d`），
  并显式排除 `product_type == "carousel_item"`。
  ⚠️ 改完要同时在 `tests_parse.py` 里加一条**用真实结构构造**的断言：
  给一个带 `carousel_media` 的父节点，断言 `extract()` 只产出 1 篇。

### CR-14 · P1 · Facebook 视频帖被记成"图片抓取失败"

- **位置**：`core/parse.py::from_fb_story`
- **问题**：只抽取 `walk()` 里同时含 `uri`/`width`/`height` 的图片节点，
  **完全不识别视频**。视频帖因此得到 `media == []`，
  再经 `media_complete=bool(media)` 得到 `False`。
- **实测**：Facebook 47 条中 **20 条 `media` 为空且 `media_complete=False`**。
  核对 capture 后确认这 20 条的 attachments 里主导 `__typename` 是
  `Video` / `VideoAttachmentStyleInfo` / `FbShortsVideoAttachmentStyleInfo`
  （对比：有图帖子的 attachments 主导 `__typename` 是 `Photo`），
  即**它们是视频帖，不是抓取失败**。
- **后果**：与计划固化的"视频只记元数据不下载"边界相违背。这 20 条会永久停留在
  `Archive.needs_media()` 的待补清单里、每次回填都被 `should_append()` 判为可升级
  从而重复尝试，且完整性检查会把它们当成媒体缺失。
- **修复方向**：`from_fb_story` 识别视频附件并记 `Media(kind="video")`（只记 URL
  与元数据，**不下载**），`media_complete` 的判定改为"已知媒体都已处理"，
  而不是"有图片"。参考 `from_iphone_struct` 里对 `video_versions` 的既有处理。

### CR-15 · P2 · 人工滚动时无法判断是否已滚到底

- **位置**：`routes/backfill.py::run` 的进度打印
- **问题**：终端只显示"已捕获 N 个响应 / M 段 JSON"。这两个数字对操作者**没有意义**——
  他无法据此判断还要滚多久、是否已经到了最早一篇。
- **可能已造成的实际损失**：Facebook 归档的时间范围只有
  `2026-06-29 → 2026-08-25`（约 2 个月，46 篇），而 Instagram 是
  `2020-09-03 → 2026-08-30`（约 6 年）。两者差距如此之大，
  **无法排除"FB 根本没滚到底"**。在补上进度显示之前，这个问题无法自证。
- **修复方向**：把计数改为对操作者有意义的量——**已解析出的本账号帖子数**
  与**目前最早一篇的日期**，边滚边刷新。同时在连续 N 秒无新响应时提示
  "页面似乎不再加载，可以按 Enter 收尾"。
  ⚠️ 这只是改打印，**不得因此引入任何驱动页面的动作**（禁止事项：人工滚动的
  全部价值在于滚动的确实是人）。

### 这四项对既有结论的影响

- 计划第 3 节"现状盘点"里 `core/parse.py` 的"未验证：与真实响应的匹配度"
  **现已验证，且结果是不匹配**。
- B5「媒体下载验证」与 B6「回填结果盘点」的验收**不能基于当前 manifest 进行**——
  现在的数字（1500 / 47）是错的，真实值是 **756 / 46**（其中 FB 20 篇为视频）。
  必须先修 CR-12/13/14 并离线重放重建归档，再做 B5/B6。
- **不需要重新人工滚动。** 两份 capture 完整保留，重建走离线重放即可。

### CR-12 ~ CR-15 的修复与验证（2026-08-30 当日完成）

四项全部修复并用**真实 capture 验证**，归档已重建。

| 编号 | 修复位置 | 验证结果 |
| --- | --- | --- |
| CR-12 | `core/parse.py`：`Post.owner`/`owner_name` 新字段、`_fb_actor()`、`_fb_slug()`、`partition_by_owner()` | IG 丢弃 266 条（195 个账号）、FB 丢弃 1 条；保留的帖子 owner 100% 为目标账号 |
| CR-13 | `core/parse.py::is_iphone_struct` 改为判值 + 排除 `product_type == "carousel_item"` | 候选从 1500 降到 1022，**正好剔除 478 条子项**；轮播父帖仍收全子图（128 篇媒体数 > 1） |
| CR-14 | `core/parse.py::_fb_videos()`，`media_complete` 语义改为解析层恒 True | FB 18 篇含视频；`media_complete=False` 从 20 降到 0 |
| CR-15 | `routes/backfill.py::ScrollProgress` | 滚动时显示篇数 / 最早日期 / 静默秒数；未引入任何驱动页面的动作 |

**归属判等的两个平台不对称，实现时才发现，记在这里**：

- Instagram：`user.username` 与 `config.toml` 的值**直接相等**（`neakasa.tech`）。
- Facebook：`actors[0].name` 是**展示名**（`"Neakasa Official"`），
  与 URL 里的 `neakasaofficial` **不相等**。必须从 `actors[0].url` 取 slug 才能判等。
  没有自定义用户名的主页（`profile.php?id=NNN`）退回 `id:NNN`。

**归属未知一律丢弃**（`reason: "owner_unknown"`，与 `owner_mismatch` 分开记）。
宁可漏一篇自家的，不可混进一篇别人的：漏的下次回填还能补，发出去的收不回来。

**`_rejected.jsonl` 只记 266 条而不是计划里写的 744 条，这是有意的偏差**：
那 478 条轮播子项**根本不是帖子，是帖子的一部分**，判定修好之后解析器压根不会把
它们当候选，"丢弃"这个词对它们不成立。478 这个数字由 `tools/replay.py` 的重建
报告体现（旧 1500 → 新 756，差额 744 拆成 478 子项 + 266 他人帖），审计链条不断。

### 归档重建与布局重构（B7 / J 组）

- `tools/replay.py`：用 `_capture_*.json` 离线重建，**媒体不重新下载**
  （CDN 签名 URL 早已过期）。已在盘上的文件**按 URL 而不是按下标**重新关联——
  修复后媒体排列顺序变了（FB 会在图片后追加视频），下标关联会张冠李戴。
  实测 718 个文件全部正确关联、0 个失配。孤儿文件**移入 `_orphan_media/`，
  不删除**。
- `tools/layout.py`：`migrate` / `reindex` / `index`。归档改为每帖一个文件夹
  （`posts/<日期>_<时分>_<post_id>/`），`post.json` 为真相源、
  `manifest.jsonl` 降为派生索引，冲突时以文件夹为准。
- 删除了 `Archive.save_raw()` 与 `raw/` 目录：docstring 声称存原始响应，
  **代码实际写的是 `post.to_row()`**，与 manifest 逐字段相同。真正的原始响应
  一直在 `_capture_*.json`。`post.json` 落地后它是纯重复。
  ⚠️ 已存在的 `raw/`（FB 47 / IG 1500 个文件）**没有自动删除**，只打印提示。

## 8. 登录态增量首次实测暴露的缺陷（2026-08-30 夜）

C 组代码写完、95 项离线断言全绿之后，用户跑了第一次真实增量
（1 次 `--dry-run` + 2 次实跑）。**四次输出全是"新增 0 篇"，零报错。**

看起来完美，实际上 Instagram 那半边根本没工作。离线复盘
`_capture_delta_*.json` 才看出来：

| | 候选 | 本账号 | 丢弃 | 看到的日期范围 | 归档最新 |
| --- | ---: | ---: | ---: | --- | --- |
| Facebook | 6 | **6** | 0 | 2026-08-16 ~ **2026-08-25** | 2026-08-25 |
| Instagram | 39 | **1** | 38 | 2026-06-06 ~ **2026-06-06** | 2026-07-15 |

Facebook 是对的（"新增 0 篇"是真的没新帖）。Instagram 那 38 条丢弃的全是推荐位
（`neakasa.global` 29 条 + 宠物 UGC 账号），**本账号只有孤零零 1 篇，
而且比归档里最新的一篇还旧 39 天**。

**这次事件与 2026-08-30 上午那次（CR-12~15）是同一个教训的第二次出现**：
95 项离线断言覆盖的是我们想到的形态。这次连"能跑通"都不足以说明问题——
它跑通了，只是跑的不是我们以为的那件事。

> ## ⛔ 本节的 CR-16 结论在当晚被推翻，先读 CR-19
>
> 用户随后提出「很多帖子是两个账号共同发的，会不会我们抓的这个只是转发角色」。
> 查证结果：**是 Instagram 的合作帖（collab）**，而这一条同时解释掉了上面那张
> 表里 Instagram 的全部异常——时间线一直都在 XHR 里，是我们把它丢掉了。
>
> **CR-16 是误判**（详见其下的更正段），**CR-17 无法证实**，
> 只有 CR-18 是真问题。真正的 P0 是 CR-19。

### CR-16 · ~~P0~~ **误判** · Instagram 主页时间线首屏不走 XHR，增量永远看不到最新帖

- **位置**：`routes/delta.py::scan_page`（原实现只收 `page.on("response")`）
- **问题**：Instagram 主页的首屏时间线是**随 HTML 文档一起下发的**
  （`<script type="application/json">` 数据块），不经过任何 XHR。
  增量只拦响应，于是拦到的全是推荐位和相关账号内容。
- **实测**：17 段响应里有帖子的 4 段全部来自推荐接口；本账号的帖子只有 1 篇，
  且是 2026-06-06 的——它出现在一个混着 `neakasa.global` 的推荐位里，
  不是时间线。
- **为什么回填没暴露这个**：回填是人从头滚到底，触发了几十次分页 XHR，
  最新那十几篇在往回滚的过程中被重新请求过。增量只滚两屏，触发不到。
- **⚠️ "多滚几屏"不是解法**：往下滚只会拿到**更旧**的分页，
  而增量要的恰恰是最新的。加大 `max_scrolls` 只会提高风险、不解决问题。
- **修复**：`core/capture.py::harvest_embedded_json()` —— 页面加载并滚动完之后，
  把 DOM 里 `<script type="application/json">` 的内容也解析出来，
  与拦到的响应合并后一起交给 `extract()`。
  这是**纯读已经加载好的 DOM，不发任何额外请求**，与"被动拦截浏览器自己的
  流量"是同一性质的动作。解析层的 `walk()` 全树搜索 + `partition_by_owner()`
  归属过滤本来就能处理这种"结构未知、混着别人内容"的输入，不需要写第二套解析。
  开关是 `[delta].harvest_embedded`，总量上限 4 MB（它会原样进转储）。
- **回归验证**：离线构造"XHR 里只有别人的帖子、本账号的在内嵌 JSON 里"，
  断言仍能抓到 1 篇；关掉开关则抓不到。

> **⛔ 更正（同日晚）：上面这条诊断是错的。**
>
> 那 38 条"推荐位"里有 **35 条是合作帖**（本账号是 coauthor，帖子就在
> 本账号主页上）。把它们算进来之后，那次增量看到的是
> **36 篇本账号主页上的帖子、日期跨 2026-06-06 ~ 2026-08-27**——
> **时间线一直在 XHR 里，只是被归属判定丢掉了。**
>
> 我犯的错误是：看到"本账号帖子只有 1 篇"就去猜数据从哪来，
> 而没有先问"这 38 条到底是什么"。**答案就在同一份 capture 里，
> 多看一个字段就能看到。**
>
> 处置：`harvest_embedded_json()` 与它的测试**保留**（它是一条有效的兜底
> 来源，哪天 IG 真的不走 XHR 了可以直接开），但 `[delta].harvest_embedded`
> **默认改为 false** —— 不能让一个建立在错误诊断上的路径每天白跑并往
> 转储里塞几 MB。

### CR-17 · ~~P1~~ **未能证实** · 滚动很可能一直没有生效，而且完全看不出来

- **位置**：`routes/delta.py::human_scroll`
- **问题**：`page.mouse.wheel(0, dy)` 在**当前鼠标位置**派发滚轮事件，
  而 Playwright 的默认鼠标位置是 **(0, 0)**——视口左上角通常是导航栏或侧边栏，
  滚轮打在那里对主内容区没有任何作用。
- **为什么严重**：程序会打印"滚了 2 屏"，页面却一动没动，
  **日志里看不出任何异常**。FB 那边靠首屏 XHR 照样拿到了 6 篇，
  所以这个缺陷被完全掩盖了。
- **修复**：滚之前先 `mouse.move` 到视口中心；滚完**回读 `window.scrollY`
  与滚前比对**，返回实际位移；位移为 0 时明确打警告。
- **教训**：驱动页面的动作必须有"它真的生效了吗"的回读。
  这与 CR-15（人工滚动时看不出到没到底）是同一类问题的两种形态。

> **⛔ 更正（同日晚）：这条也没有证据。**
>
> 那次增量捞到了 4 段带帖子的响应、共 39 个节点、覆盖 3 个月——
> 看起来正是"首屏 + 两次翻页"，**说明滚动其实生效了**
> （Chromium 的滚轮事件会冒泡到文档，(0,0) 落在导航栏上照样滚得动）。
>
> 修改本身**保留**：把鼠标移到视口中心更接近真人行为，
> 而回读 `window.scrollY` 让"滚了没动"这件事从此可验证——
> **这正是当时无法证实它的原因**。但把它记成"已证实的缺陷"是不诚实的，
> 这里改成"加固"。

### CR-18 · P1 · "新增 0 篇"同时表示两件相反的事

- **位置**：`routes/delta.py::_run_due` 的结果打印
- **问题**：`新增 0 篇` 既可能是"今天确实没发新帖"（FB 的真实情况），
  也可能是"我们根本没看到时间线"（IG 的真实情况）。
  **两者在输出、在状态文件、在退出码里全都一模一样。**
  这正是本项目反复强调要消灭的静默失败，却在增量的主输出上重现了。
- **修复**：
  1. 新增 `ScanResult`，把候选数、本账号篇数、丢弃数、看到的日期跨度、
     归档最新日期一并打出来。现在那一行是
     `新增 0 篇 · 本账号 6 篇（2026-08-16 ~ 2026-08-25）· 丢弃 0 · 归档最新 2026-08-25`。
  2. 新增 `[delta].min_own_posts`（默认 3）：一次抓取看到的本账号帖子
     少于这个数，**判定为"没拿到时间线"并当次中止告警**，而不是当成没新帖。
     阈值取 `min(配置值, 归档已有篇数)`，新账号不会误报。
  3. `ScanResult.stale_view()`：看到的最新一篇比归档还旧时明确提示。
     **只提示不判失败**——账号删掉最新一帖时也会这样，
     天天误报会把失败预算耗光，而删帖是会真实发生的。
- **判定用篇数而不是用日期倒退**，理由同上：前者没有已知的误报场景，
  后者有。

### CR-19 · P0 · Instagram 合作帖（collab）被当成他人帖丢弃

**由用户提出线索**："这个账号很多帖子是两个账号共同发的，会不会我们抓的
这个只是转发角色？" —— 查证属实，而且它一条就解释掉了前面几乎所有异常。

- **位置**：`core/parse.py::partition_by_owner`（只比 `owner == target`）
- **问题**：Instagram 的**合作帖**由一方发布、双方主页同时显示，
  但节点里的 `user.username` **只记原始发布者**。合作关系在
  `coauthor_producers[]` 里，而 `partition_by_owner` 从来不看它。
  于是"发生在本账号主页上、但不是本账号发起"的帖子，全部被当成他人帖丢弃。
- **实测**（`_capture_1788073013.json`，1022 个去重节点）：

  | 类别 | 篇数 | 旧判定 |
  | --- | ---: | --- |
  | `user.username == neakasa.tech` | 756 | 保留 |
  | **coauthor 含 neakasa.tech** | **263** | **丢弃（错）** |
  | 真正无关（推荐位） | 3 | 丢弃（对） |

  被丢的 263 篇**全部有正文**，跨 2022-06-10 ~ **2026-08-27**；
  按原作者拆：自家品牌账号 34 篇（`neakasa.global` 30、`neakasa.de` 4），
  第三方创作者 229 篇（190 个宠物 UGC 账号）。
- **它推翻的三个结论**：
  1. ~~"IG 一个多月没发新帖"~~ —— 本账号原创停在 2026-07-15，
     但合作帖最新是 **2026-08-27**，2026-08 一个月就有 21 篇。
     **这个账号一直在更，只是主要以合作帖的形式**。
     C7 的降频阈值、D3 的告警阈值当初都是按"IG 已停更"的认知定的。
  2. ~~CR-16「增量没看到时间线」~~ —— 见上面的更正。
  3. ~~"回填混进 266 条他人帖"（CR-12 的一部分）~~ ——
     其中 263 条就在我们账号自己的主页上。
- **业务后果**：DE 站会缺掉该账号最近一年的主要内容形式。
  可进翻译流水线的帖子从 747 篇涨到 **1010 篇**。
- **修复**：`Post.coauthors` 新字段（取 `coauthor_producers[].username`，
  归一化小写）；新增 `core.parse.on_timeline_of()`：
  `owner == target or target in coauthors`；`partition_by_owner` 改用它。
  ⚠️ **只取 `coauthor_producers`，不取 `invited_coauthor_producers`**
  ——后者是"邀请了但没接受"，那种帖子不会出现在被邀请方主页上。
  两个键在真实响应里都存在（1022 个节点全有），很容易顺手一起收。
  ⚠️ **`owner` 仍然记真实作者，没有被改写成目标账号**：谁创作的是事实，
  不能因为"它在我们主页上"就抹掉。下游据此区分原创与合作。
- **归档重建**：`tools/replay.py` 离线重放，**用户没有重新滚动**。
  756 → **1019 篇**（756 原创 + 263 合作），丢弃 3 篇；
  图片 742 张全部关联成功，其中 **94 张从 `_orphan_media/` 捞回**，
  0 张丢失；`_orphan_media/` 从 536 降到 442（**只是移动，从未删除**）。
- **可见性**：`index.html` 每张合作帖卡片带 `合作 · @原作者` 绿色标签，
  抬头统计合作帖篇数；`review.md` 每篇合作帖加一行
  「原作者是 @xxx，发布前确认二次使用授权」。
- **回归验证**：`tests_parse.py` 新增「真实结构 5」一段（7 项），
  照抄真实响应结构构造，覆盖：自家原创 / 自家兄弟账号合作 /
  第三方创作者合作 / **只被邀请没接受** / 纯推荐位 / coauthor 大小写 /
  跨响应合并时补齐 coauthors。

**⚠️ 一个必须记住的决策（用户 2026-08-30 拍板）**：那 229 篇第三方创作者的
合作帖**全部进翻译与发布流水线**。我提示了内容著作权在创作者手里、
二次发布到 DE Page 存在授权问题；用户确认合作协议已覆盖，决定全量处理。
**这是用户的决定，不要再重新讨论**，但 `review.md` 的提示保留，
让审校人在每一篇上仍能看到这个事实。

### 这次事件本身的教训

**"多看一个字段"和"猜数据从哪来"的差距。** 看到"本账号帖子只有 1 篇"时，
我去猜了数据来源（HTML vs XHR）并据此写了一整条新路径，
而正确的第一步是问"**那 38 条到底是什么**"——答案就在同一份 capture 里。
`coauthor_producers` 这个字段在**每一个**节点上都存在，翻一眼就能看到。

**这也是"离线测试全绿"第三次没能挡住真实数据。**
前两次是 CR-12/13（跨账号污染、轮播子项）和 CR-18（0 篇的二义性）。
三次的共同点都是：**测试构造的输入里没有那个字段**。

### 本轮之后的测试基线

**12 套 578 项，全绿**（2026-08-30 深夜；当天开始时是 9 套 282 项）。
新增 `tests_delta.py`（55）、`tests_parse.py` 的真实结构断言段、
`tests_store.py` 的布局与 reindex 段、`tests_backfill.py` 的归属拦截与进度显示段。

⚠️ 新增了一条以前没有的验证条件：**全套测试在 stdout 被管道重定向的情况下也必须全绿**。
本机代码页是 936，修复前 `tests_translate.py` 在这个条件下必崩
（`UnicodeEncodeError: 'gbk' codec can't encode character '\xdf'`）。

---

## 9. 合作帖判定的复核与加固（2026-08-30 晚）

> **起点是用户的一句要求**："合理解决当前 Instagram 上存在爬取的当前账号
> 不是原帖发帖者而是合作转发发布者这种情况导致逻辑误判漏掉这种帖子的问题。"
>
> CR-19 已经修掉了根因。本节做的是**另一半**：把"这次修对了"变成
> "下次坏了能被发现"，并把三个残留缺口补上。**全程零真实访问**，
> 用的是已有的四份 capture（回填 IG / FB 各一份，增量 IG 两份、FB 三份）。

### 9.1 先复核：修复在真实数据上到底成不成立

| capture | 候选 | 保留 | 原创 | **合作** | 丢弃 | 丢弃的是谁 |
|---|---:|---:|---:|---:|---:|---|
| IG 回填 `_capture_1788073013` | 1022 | 1019 | 756 | **263** | 3 | chicagofire / shaq / cars_luxury_accessories |
| IG 增量 `_capture_delta_1788086330` | 39 | 36 | **1** | **35** | 3 | erykatravel / diycraftsofficial1 / craftypanda |
| FB 回填 `_capture_1788072462` | 47 | 46 | 46 | 0 | 1 | thegardenstatecatclub |

**判定成立，且增量那份比回填更极端**：增量看到的那一屏里
**36 篇有 35 篇是合作帖，本账号自己发的只有 1 篇**。
这也顺带说明 CR-19 之前 IG 增量为什么会退化成"只看到 1 篇"。

同时排掉了三种"还有没有别的漏网形态"的可能，都用真实数据查的：

- **`usertags` / 其它字段里提到本账号**：被丢弃的节点里
  **一个都没有**（`mentions_target_somewhere = 0`）。被丢的确实是陌生账号。
- **`invited_coauthor_producers`**：键在 1022 个节点上全有，
  **值全是空数组**。所以"只收已接受、不收被邀请"这条选择至今**没有真实反例
  可验证**——CR-19 原文写"两个字段在真实响应里都存在"，准确说是**键**存在。
  选择保持不变（保守方向），但注释里补了这个限定。
- **Facebook 有没有同类形态**：`actors` 数组**长度全是 1**（139 个 story 节点），
  没有第二作者；`attached_story` 只在 1 篇上出现且其 actor 为空。
  **FB 上不存在这个问题**，不需要对称实现。

### CR-20 · P2 · `_merge_post` 用"空了才补"合并 coauthors，可能丢掉真帖子

- **位置**：`core/parse.py::_merge_post`
- **问题**：同一帖出现在多份响应里时，`coauthors` 走的是"winner 为空才从
  other 补"。两份响应各给出**不同子集**时（合作方超过一个的帖子实测有 21 篇，
  最多 4 个），winner 的非空短列表会挡住 other 里的目标账号 ——
  结果又是一篇自家帖子被判成他人帖。
- **实测**：当前四份 capture 里**同一 post_id 从未出现过两次**，
  所以这条在现有数据上不会触发。**它是按形态推的，不是观测到的**，
  记 P2 而不是 P0。
- **修复**：改成取**并集**。语义上也更对——coauthor 关系是帖子的属性，
  不是某次响应的属性。

### CR-21 · P2 · 认不出 coauthor 条目形态 = 整篇帖子被丢

- **位置**：`core/parse.py::ig_coauthors`
- **问题**：只认 `{"username": ...}`，非 dict 条目直接 `continue`。
  IG 若把该字段改成裸字符串数组，**全部合作帖会一次性退回 CR-19 的状态**，
  而失败形态是静默的。
- **修复**：dict 与裸字符串两种条目都认，空值/None 跳过。
  代价两行，而认错的代价是几百篇。

### CR-22 · P2 · `on_timeline_of` 依赖调用方先把账号名转小写

- **位置**：`core/parse.py::on_timeline_of`
- **问题**：函数是公开判定入口，但 `target` 的归一化在调用方
  （`partition_by_owner`）里做。将来任何一处直接调用它并传入
  `config.toml` 里带大写的账号名，结果是**静默丢光全部帖子**。
- **修复**：函数自己 `strip().lower()`。

### CR-23 · P1 · 丢弃是完全静默的 —— 这才是 CR-19 拖了那么久的原因

- **位置**：`core/integrity.py`（新增第四项检查）、`routes/delta.py`、
  `routes/backfill.py`、`tools/replay.py`
- **问题**：CR-19 的根因修了，但**让它一直没被发现的那个原因没修**。
  程序丢掉 263 篇，`_rejected.jsonl` 只写不读，输出里一个字都没有。
  合作机制会变（换字段名、换形态、出新的联合发布方式），
  修好的是这一次，没有任何东西盯着下一次。
- **修复**：新增 `known_partners(rows, account)` 与
  `check_dropped_partners(rejected, partners)` ——
  **被丢弃的节点里，作者是已知合作方的那些**。合作方名单同时取自
  "合作帖的 owner"和"自家帖的 coauthors"，实测 IG 有 **210 个**。
- **为什么这个信号可用**：它在全部真实数据上**零误报**。
  210 个合作方 vs 历史上被丢弃过的 6 个账号，**交集为空**——
  推荐位来自完全陌生的账号，而合作方的帖子本来就该留下，两者天然不重叠。
- **灵敏度实测**（把 `on_timeline_of` 退回只比 owner 的旧实现）：

  | 场景 | 正常 | 退回旧实现 |
  |---|---:|---:|
  | IG 回填（归档已建） | 0 | **263 命中，闸响** |
  | IG 增量（归档已建） | 0 | **35 命中，闸响** |
  | IG 回填（**归档为空的冷启动**） | 0 | **34 命中，闸响** |
  | IG 增量（**冷启动**） | 0 | **29 命中，闸响** |

  冷启动也能响，是因为名单同时取自**本次留下的那批**：
  本账号自己发的帖子里就记着合作方。
- **接线**：增量把它写进 `ScanResult.suspect`，`--dry-run` 也打；
  正式跑时并进 D3 的那条平台级通知。**这一项不做去重节流**——
  其它检查会天天成立（账号真停更时"零新增"每天都真），
  而这一条在全部真实数据上从未成立过，漏报的代价远大于重复提醒。
- **⚠️ 它是提示不是判定**：真响了，正确动作是去 `_rejected.jsonl` 和
  `_capture_*.json` 里离线查那几篇为什么没带上 coauthor 信息，
  **不是把它们无条件收进来**。"宁可漏一篇自家的，不可混进一篇别人的"没有变。

### CR-24 · P1 · "本账号 N 篇"这一个数字看不出它是怎么来的

- **位置**：`routes/delta.py::ScanResult.summary`、`routes/backfill.py`
- **问题**：CR-18 把"新增 0 篇"的二义性修掉了，但换成 `own` 之后又埋了一层：
  实测 IG 那 36 篇里 **35 篇靠合作帖判定撑着**。判定一旦部分漂移，
  `own` 会从 36 掉到 1，而那和"今天真的只发了一篇"在输出里完全一样。
  这是同一类缺陷换了个位置。
- **修复**：摘要改成
  `新增 0 篇 · 本账号 36 篇（原创 1 · 合作 35，2026-06-06 ~ 2026-08-27）· 丢弃 3 · 归档最新 2026-08-27`。
  回填同样拆开报。`min_own_posts` 那道闸触发时，中止理由里会直接写
  "其中 N 篇来自**已知合作方** —— 优先怀疑合作帖判定失效，而不是被拦"。
- **为什么这句话重要**：2026-08-30 那次，正是因为"只看到 1 篇"没有指向性，
  才先去猜了"是不是没拿到时间线"并写了一整条新代码路径（CR-16）。

### 9.2 本轮之后的测试基线

**全绿；2026-08-30 晚测得 15 套 788 项（三条线并行加测试，数字仍在涨，以实际跑出来的为准）**（stdout 被重定向的条件下同样全绿）。
本轮新增 28 项：`tests_parse.py` +4（并集合并、裸字符串条目、target 归一化）、
`tests_integrity.py` +10（第四项检查的名单构建与命中/不命中）、
`tests_delta_logged_in.py` +14（摘要拆分、哨兵、中止理由指向合作帖判定）。

⚠️ 新增断言全部**按真实形态构造**：`ig_payload()` 现在无条件带上
`coauthor_producers` 与 `invited_coauthor_producers` 两个键——
CR-19 的教训是"测试构造的输入里没有那个字段"，
让测试样本比真实数据更干净，就是在给下一次留同样的坑。

---

## 10. 已实现主干的第二轮全量审查与修复（2026-08-30）

### 10.1 范围与边界

- 审查基线：提交 `92e7018` 到当前工作树的全部已实现改动，包括未跟踪的新测试。
- 覆盖：回填、登录态增量、只读 Graph 路线、归档/replay/layout、完整性通知、
  Windows 调度、DeepSeek 翻译与人工审校清单。
- 明确未扩展：G 组自动发布、审校回写、自动登录、视频下载和新的抓取路线。
- 没有真实 `.env` 或 `DEEPSEEK_API_KEY`，因此本轮**没有发起付费请求**，也没有
  生成或伪造真实德语译文。

### 10.2 项目目录改名专项核对

项目根目录已从旧名称改为 `FacebookScraper`。核对结果是**活动配置没有因改名失效**：

- `git rev-parse --show-toplevel` 为
  `D:/VSCodeWorkspace/Facebook/FacebookScraper`；`cfg().archive_dir/state_dir` 均落在新根目录。
- Python 入口基于 `Path(__file__)`，批处理基于 `%~dp0..`，没有依赖旧项目绝对路径。
- `tools.schedule xml` 生成的 `Command`、`WorkingDirectory` 均是新目录；每日任务参数为
  `--platform all`，补跑任务为 `--if-stale`。
- 活动仓库中旧 `.../Facebook/scraper` 引用为 0；系统中
  `FBScraperDelta` / `FBScraperDeltaCatchup` 均未注册，所以不存在需要迁移的旧 Action。
- `%USERPROFILE%\.fbscraper-chrome` 是刻意放在仓库外的浏览器 profile，不随项目改名。

因此本项不需要硬编码新绝对路径；保留相对定位才是对后续再次移动目录也有效的修复。

### CR-25 · P1 · 归档/replay 可覆盖、复活或越界写入真相源

- **位置**：`core/store.py`、`tools/replay.py`、`tools/layout.py`
- **问题**：外部 `post_id` 可构造路径/Windows 保留名；同 ID 的 dated/undated truth dir
  可同时存在并被 reindex 选回旧记录；空/错形 capture 仍可能开始重建；目录或叶子
  symlink/junction/reparse/hardlink 可把写入导向账号外；`post.json` 升级时直接截断写，
  中断会损坏唯一真相源；replay 的 kept `post.json/text.txt` 叶原先直到备份/隔离后
  才校验，且已有普通媒体目标会被 `shutil.move` 静默覆盖。
- **修复**：异常 ID 使用稳定 SHA-256 目录分量但 JSON 保留原 ID；统一验证 root、account、
  posts、帖子目录及叶子的真实物理直属关系；拒绝 link/reparse/hardlink；replay 在任何写盘前
  验证输入并 fail closed；重复 truth dir 可恢复地移入 `_orphan_posts`；reindex 按
  `完整 > 残缺、残缺时媒体更多` 去重；`post.json` 改为同目录临时文件
  `flush + fsync + replace`，失败保留旧 JSON/text/manifest；replay 在任何备份/移动前
  无条件预检 truth 叶与全部媒体目标，普通文件冲突和重复目标均整体失败闭合。
- **验证**：新增 `tests_replay.py`、`tests_store_boundaries.py`、`tests_store_links.py`，
  覆盖 Windows junction/hardlink、账号根越界、错误 capture、重复真相源和原子替换失败。

### CR-26 · P1 · 增量失败可能未记状态，或部分停摆却返回成功

- **位置**：`routes/delta.py`、`core/notify.py`
- **问题**：CDP attach/launch 的 `SystemExit`、未预期异常、禁止自动拉起但端口未就绪等路径
  可能直接穿出，未累计各平台失败预算也不通知；一个平台预算耗尽而另一个成功时可能返回 0；
  state 直接覆盖写会在中断时丢失 `last_success` 与失败预算；通知读取配置抛 `SystemExit` 时
  连降级日志也到不了；严格路径检查后，平台 `Archive(...)` 构造失败仍位于旧异常闭环之外。
- **修复**：所有共享启动/附着和平台异常统一记录、原子保存并通知；硬阻断给所有到期平台
  记失败；部分停摆保持非零退出码；state 使用同目录临时文件、`fsync`、原子替换与清理；
  通知状态目录在配置失败时退回安全默认路径；账号读取、Archive 构造与 `delta_once` 统一
  置于平台级 try，权限/路径错误会落状态并通知，但作为本地错误不阻断另一个平台。
- **验证**：`tests_delta_logged_in.py` 覆盖 attach/launch/generic exception、部分预算耗尽、
  state 替换失败和通知；`tests_notify.py` 覆盖配置 `SystemExit`。

### CR-27 · P1 · 200 响应可把登录页/SVG 当图片，原始 capture 可被中断截断

- **位置**：`core/capture.py`、`routes/backfill.py`、`routes/delta.py`、`routes/fb_graph.py`
- **问题**：只看 HTTP 200 或宽泛 `image/*` 会把伪 JPEG HTML、SVG、MIME/字节错配写进归档，
  还会把 `media_complete` 错记为 true；回填几十 MB 的唯一原始响应直接写目标文件，
  中断后最新 capture 会成为截断 JSON；系统 MIME 注册差异还会把 WebP 命名成 `.jpg`。
- **修复**：两条下载路线共用 JPEG/PNG/WebP/GIF/AVIF 静态光栅白名单并校验文件签名与
  MIME 一致；显式稳定扩展名；拒绝 SVG、HTML、空体与错配并保留可重试状态；回填/增量
  capture 共用同目录 JSON 临时文件、`flush + fsync + replace`。
- **验证**：`tests_backfill.py`、`tests_fb_graph.py` 覆盖四种常见合法格式、伪 JPEG、SVG、
  MIME 错配、空体与 capture 中断；登录态增量使用真实 JPEG magic 夹具。

### CR-28 · P1 · 译文只按 post_id 断点会把旧德文错配给新英文

- **位置**：`translate.py`
- **问题**：同 ID 的英文正文经解析修复或人工纠正后，旧实现仍把旧译文当完成；
  `review.md` 会展示新英文 + 旧德文，帖子目录还残留旧 `text_de.txt`。另外，
  `translated.jsonl` 若截在 UTF-8 多字节字符中，文本迭代会让后续已付费有效行全部不可见；
  非字符串 ID/text 还可能触发无效付费请求。
- **修复**：每条结果写 `source_text_sha256`（对实际发送的 strip 后 UTF-8 正文）；
  `pending` 与 review 只有 ID+指纹同时匹配才算完成；旧/缺指纹记录明确列为过期并只重译
  受影响帖子；过期 `text_de.txt` 派生副本安全移除但历史 JSONL 记录保留；JSONL 改为
  二进制逐行独立解码，坏行不遮住后续结果；无效源 schema 在 API 前失败闭合。
- **同时核对的付费边界**：DeepSeek 官方 Anthropic URL、`deepseek-v4-pro`、
  `reasoning.effort=none`、实际响应 model 防 Flash 静默回退、单实例锁、共享错误首条熔断、
  金额逐字符保留、坏尾追加 `fsync` 和 usage/费用记录均有线级或离线回归。
- **验证**：`tests_translate.py` 212 项，含正文 A→B 只重译一帖、review 不错配、旧副本移除、
  invalid UTF-8 后有效付费结果可读、无效源零调用以及 SDK MockTransport 线级请求。

### CR-29 · P1 · 单个坏轮播子项会丢整帖，视频缩略图可被伪装成完整图片

- **位置**：`core/parse.py`
- **问题**：一个 malformed carousel child 的异常会向上冒泡并丢掉整篇父帖及其它合法子项；
  节点已有 `video_versions` 却无可用视频 URL 时，旧逻辑会退回缩略图并标成普通完整图片。
- **修复**：逐子项容错，保留合法 sibling 并把父帖标为 `media_complete=False`；
  已知为视频但 URL 缺失时不把 thumbnail 当完整图片。
- **验证**：`tests_parse.py` 覆盖坏子项、合法 sibling 与缺视频 URL 的不完整语义。

### CR-30 · P1 · 迁移布局可通过 local_path 或预置目标链接移动账号外文件

- **位置**：`tools/layout.py`
- **问题**：manifest 中的外部 `media.local_path` 可使用 `../..` 或链接逃出账号目录；
  即使链接仍指向账号内，迁移也可能搬走另一文件；预置的目标帖子目录/metadata/media
  链接可把写入导向其它帖子或账号外 sentinel，已有普通媒体还会被静默覆盖。
- **修复**：源路径要求 resolve 后仍在账号物理根内且拒绝链接；所有目标在备份前预检，
  执行前再次验证真实直属关系、搬移后复核；symlink/junction/reparse/hardlink 源一律拒绝；
  普通媒体或重复目标冲突使整次迁移在备份前失败闭合，不覆盖人工整理结果。
- **验证**：`tests_store.py` 与 `tests_store_links.py` 覆盖 traversal、source/target junction、
  叶链接、dry-run 及账号外 sentinel 不变。

### CR-31 · P1 · 计划任务默认参数会暂停或跳过应跑的一天

- **位置**：`tools/schedule.py`、`scripts/run_delta.bat`
- **问题**：计划任务无参数调用批处理会被识别成人工双击并 `pause`；每日任务若误用
  `--if-stale`，26 小时阈值与 24 小时间隔组合会变成隔天运行。
- **修复**：每日 Action 显式 `--platform all`，登录/解锁补跑 Action 使用 `--if-stale`；
  batch 只在人真正无参数双击时暂停，调度 XML 保持新项目根目录。
- **验证**：`tests_schedule.py` 48 项，含 XML 参数、工作目录和 batch 字节级约定。

### 10.3 最终验证基线

| 检查 | 结果 |
|---|---|
| 全套 `tests/tests_*.py` | **15 套 788 项，全部 exit 0** |
| `python -m compileall -q core routes tools translate.py tests` | 通过 |
| `git diff --check` | 通过（仅现有 CRLF/LF 转换提示） |
| 真实归档离线 `--estimate` | 45 + 1010 = 1055 条；约 US$1.08–5.39 |
| 缺 Key 的 `translate.py --check` | 明确给出 `.env` 操作，exit 1，零网络请求 |
| 旧项目绝对路径搜索 | 活动文件 0 命中 |
| 已注册计划任务 | 两个任务均不存在，未产生外部定时访问 |

真实 DeepSeek 连通、两个账号各 3 条试译、德语人工质量确认、全量翻译与真实带图审校
仍是外部业务验收，不应被离线测试冒充完成。

### 10.4 独立最终复审

按 `requesting-code-review` 流程，由一名未参与实现的独立 reviewer 对基线
`92e7018` 到当前工作树做了最终审查。首次复审发现 3 个 P1：replay truth 叶预检
发生得太晚、replay/layout 可覆盖既有媒体或接受账号内链接、增量 Archive 构造仍在
平台异常闭环之外。三项均补修并加入真实失败形态回归；同一 reviewer 复核后结论为：
**无剩余 Critical / Important finding，补丁未引入新的 P0/P1。**

---

## 11. 增量离线自检工具 + DeepSeek 翻译的首次真实验收（2026-08-30 晚，第三轮）

> ⚠️ **编号说明**：本节两条原编为 CR-25 / CR-26，与第 10 节的第二轮审查撞号，
> 2026-08-31 改为 CR-37 / CR-38。第 10 节那两条保持不动——它们已被
> `TRANSLATION_PLAN.md` 等文件引用，改它们的波及面更大。

> 用户要求两件事：**"Instagram 合作帖这块到底解决了吗，核查并检测"**，
> 以及 **"DeepSeek Key 已配好，把英译德也核验一遍"**。
> 前者的结论是"已解决，并且现在可以随时自证"；后者查出一个 P0。

### 10.1 合作帖：从"离线验过解析函数"升级到"离线跑完真实代码路径"

上一轮验的是 `extract()` + `partition_by_owner()` 两个函数。这一轮补了
`tools/dryrun_delta.py`：把 `_capture_delta_*.json` 喂给一个假页面，
让 **`routes.delta.delta_once()` 原样跑完**——响应收集、登录墙判定、归属过滤、
合作方哨兵、`min_own_posts` 闸、`ScanResult` 摘要、`should_append` 幂等判断
全是真实实现，只有浏览器是假的。**零网络、零写盘**（内部固定 `dry_run=True`）。

真实转储上的结果：

| | 结果 |
|---|---|
| Instagram | `新增 0 篇 · 本账号 36 篇（原创 1 · 合作 35，2026-06-06 ~ 2026-08-27）· 丢弃 3 · 归档最新 2026-08-27` |
| Facebook | `新增 0 篇 · 本账号 6 篇（原创 6 · 合作 0，2026-08-16 ~ 2026-08-25）· 丢弃 0 · 归档最新 2026-08-25` |
| IG `--break-coauthors` | **当次中止**：`只看到 1 篇…；其中 35 篇来自**已知合作方**（irina.catmom、neakasa.global、…）—— 优先怀疑合作帖判定失效，而不是被拦` |

**这个工具的价值不在于它今天说了什么，而在于它让"改完解析器对不对"
不再需要用一次真实露面去回答。** 2026-08-30 那次正是靠一次实测才发现异常、
然后又猜错了原因（CR-16）。

`--break-coauthors` 把判定退回 CR-19 修复前的形态，用来回答
**"哨兵真的会响吗"**——它只改内存里的函数引用，不碰任何文件。

⚠️ **它验证不到媒体下载。** 那是增量里唯一还需要真实跑一次的东西
（历次实测都是 0 新增，下载环节从没被触发过）。

### CR-37（原 CR-25）· P0 · ~~DeepSeek 的 thinking 一直没关掉，真实文案 100% 失败~~

> ⛔ **本条的修复已被后续决策取代，保留作事故记录。**
> 2026-08-31 用户拍板把翻译主干换成 DeepSeek 原生 OpenAI 兼容接口，
> 并**刻意开启 thinking**（见 CR-32 / CR-33）：不再发 `max_tokens`，
> 因此『思考吃光输出预算』这种失败形态在新主干上不成立。
>
> **但它仍然值得读**，因为记着一个与 SDK 无关的通用教训：
> *自检那句话短到即使参数没生效也能答对，于是自检发绿灯、真实文案全灭。*
> 新主干的 `--check` 保留了从这里长出来的那道检查（回读响应里的
> reasoning 用量，确认 thinking 的实际状态与配置一致）。

- **位置**：`translate.py::Translator.request_kwargs`
- **问题**：原实现用
  `extra_body={"reasoning": {"effort": "none"}}` 关 thinking。
  **DeepSeek 的 Anthropic 兼容端点根本不认这个字段**——不报错、也不生效。
- **真实后果**（第一次拿真 Key 试译，FB 3 篇）：

  ```
  [1/3] 122098529409379375  失败：RuntimeError: 译文被 max_tokens=4096 截断…
  [2/3] 122099269947379375  失败：…
  [3/3] 122099685669379375  失败：…
  完成：成功 0 篇，失败 3 篇。   输出 12288 tok（= 3 × 4096，全部顶满）
  ```

  单次诊断请求的响应形态：**只有一个 `thinking` 块，15444 字符，
  `text` 块一个都没有**。785 字符的帖子换来 15000 字的思考。
- **为什么自检没拦住**：`--check` 那句话（"Reply with the single word: OK"）
  短到即使 thinking 开着也能顺利产出 text。**自检绿灯、真实文案全灭。**
  这是"测试覆盖的是我们想到的形态"在翻译侧的又一次重演。
- **为什么错误提示是有害的**：当时报的是"请调大 max_tokens 后重跑"。
  **对这种失败照做只会让它想得更久、账单更高，译文一篇也拿不到。**
- **修复**（三处，都实测过）：
  1. `reasoning_effort == "none"` → 发 **Anthropic 标准的顶层
     `thinking={"type": "disabled"}`**。实测：同一份提示词，
     输出从 4096 tok 降到 30 tok，德语干净。
     其余档位（low/high/max）在这个端点上没有可验证的映射，
     **不再发那个被忽略的 `reasoning` 字段**——项目已经因为
     "改了不生效的旋钮"吃过两次亏（`target_lang`、`runs_per_day`）。
  2. `translate()` 区分两种截断：有 `text` 块 = 译文太长（调大 max_tokens）；
     **只有 `thinking` 块 = thinking 没关掉（明确写"不要调大 max_tokens"）**。
  3. `--check` 记录响应的内容块类型，**发现 `thinking` 块就判失败**并说明原因。
     连通 ≠ 能用，自检不能再发假绿灯。
- **修复后的真实验收**：

  | | 结果 | 输出 tok | 费用上界 |
  |---|---|---:|---:|
  | `--check` | 通过，且"已确认关闭" | 1 | ~0 |
  | FB 3 篇 | **3 成功 0 失败** | 1128 | US$0.019 |
  | IG 3 篇 | **3 成功 0 失败** | 168 | US$0.0065 |

### CR-38（原 CR-26）· P2 · 合作帖的授权提示没有任何测试覆盖

- **位置**：`translate.py::run_review` 的合作帖提示块
- **问题**：用户拍板"229 篇第三方创作者的合作帖全部进流水线"时，
  **配套条件是 `review.md` 每篇仍标出原作者与授权提示**。
  这条提示此前一个断言都没有——静默失效的话，
  审校人再也看不到"这篇的著作权在别人手里"，而没人会发现少了一句话。
- **修复**：`tests_translate.py` 新增 `[19b-2]` 段（3 项）：
  合作帖标出原作者、写明确认授权、**自己原创的帖子不加这条提示**
  （篇篇都有 = 等于没有）。

### 10.2 译文质量的第一手观察（3+3 篇，人工看过）

德语正文本身没问题，`【…】`、分隔点、emoji、品牌名、型号都保留了。
**Instagram 译文比原文短 40–55%，全部来自话题标签被从 22 个砍到 3 个**
（`config.toml` 的 `tone` 规则）——正文本身德语反而略长，符合预期。

⚠️ **"IG 话题标签砍到 3 个"是一个业务决策，不是翻译问题**：
IG 的话题标签直接影响自然流量，而这条规则会作用在全部 1010 篇上。
本轮**没有改它**，留给用户拍板。

### 10.3 本轮之后的测试基线

**16 套 811 项，全绿**（stdout 被重定向的条件下同样全绿）。
新增 `tests/tests_dryrun_delta.py`（13 项，含"一次网络请求都不发"的断言）、
`tests_translate.py` 的 thinking 截断分流与合作帖提示两段。

---

## 12. DeepSeek 官方 API 与标签契约复审（2026-08-30，第四轮）

> 本节记录当前有效实现。第 10 节 CR-25 的 Anthropic 关闭-thinking 修复保留为
> 历史事故记录，但已经被用户的新决定取代；不能再据此恢复旧协议或旧参数。

### CR-32 · P1 · DeepSeek 主干经过 Anthropic 协议层，配置与响应语义都绑定错接口

- **位置**：`translate.py::build_client`、`Translator.translate`、`config.toml`、
  `requirements.txt`
- **问题**：主干使用 `anthropic.Anthropic`、`/anthropic/v1/messages`、`x-api-key` 和
  内容块响应；这不是用户要求的 DeepSeek 官方原生/OpenAI 兼容调用，也导致 thinking、
  finish reason 与 usage 字段必须绕一层协议映射。
- **修复**：改用 OpenAI SDK 直连 `https://api.deepseek.com/chat/completions`，Bearer
  鉴权；响应从 `choices[0].message.content/reasoning_content` 读取；移除
  `custom_anthropic`、`auth_style`、`extra_headers`、`prompt_cache` 等非主干旋钮。
- **验证**：MockTransport 在线级断言最终 URL、Authorization header、messages 和
  OpenAI 格式响应解析；不是只检查调用前的 Python 字典。

### CR-33 · P1 · 客户端输出上限与默认关闭 thinking 同时违背当前翻译契约

- **位置**：`Settings`、`Translator.request_kwargs`、`run_check`
- **问题**：旧请求固定 `max_tokens=4096` 且默认 `reasoning_effort=none`。前者会人为
  截断模型，后者与用户要求的 High thinking 相反；旧错误提示还会建议继续调大该上限。
- **修复**：thinking 显式 `enabled`，默认 `reasoning_effort="high"`；只接受官方
  `low/high/max` 档位；不配置、不发送 `max_tokens`、`max_completion_tokens`、
  temperature 或 top_p。服务端 `finish_reason=length` 单独报错，并明确客户端未设上限。
  `--check` 除了核对 model，还要求响应提供 reasoning content/token 证据，避免假绿灯。
- **验证**：业务层 kwargs 与 SDK 最终 JSON body 各有独立“无输出上限”断言；
  `length` 回归确保提示不会让业务人员重新添加客户端限制。

### CR-34 · P1 · “最多 3 个标签”会静默删除原帖发布内容

- **位置**：`prompts/translate_de.md`、`config.toml`、`translate.py::run_translate`
- **问题**：提示词、tone、示范和自检清单同时要求最多 3 个标签，真实 IG 试译已出现
  22→3。用户现已明确要求原帖多少个就照搬多少个；只改一句提示词仍可能被模型违反。
- **修复**：删除所有数量上限与裁剪示范，改为数量、内容、大小写、顺序完全一致；
  新增 Unicode-aware `extract_hashtags` / `hashtags_preserved`，在付费结果写盘前硬校验。
  少贴、多贴、翻译、改大小写或调序都拒绝写盘，失败项留在普通重跑队列。
- **验证**：覆盖拉丁重音、CJK 标签、句尾标点，以及删/增/翻译/大小写/调序；
  另有主流程回归确认违规结果不会进入 `translated.jsonl`。

### CR-35 · P1 · 已保存译文不校验提示词版本，规则升级后仍会被当成完成

- **位置**：`translate.py::translation_is_current`
- **问题**：记录虽然保存 `prompt_version`，但“是否当前有效”只比较正文 SHA-256。
  标签策略从裁 3 个改为全量照搬后，旧 3+3 的正文指纹仍匹配，普通运行会跳过，
  使新规则实际上不生效，除非业务人员刚好记得加 `--force`。
- **修复**：有效性同时要求正文指纹与 `PROMPT_VERSION`；版本从 4 升至 5。旧付费行
  保留作历史，不删除；普通运行自动重译，`--review` 不再展示旧版结果或旧派生副本。
- **验证**：旧版本同正文进入待译队列；当前版本重跑仍保持幂等。

### CR-36 · P2 · High thinking 后旧离线费用仍被描述为“保守上界”

- **位置**：`translate.py::run_estimate`、翻译操作文档
- **问题**：旧估算只按可见德语长度估输出。High thinking 开启后，reasoning token
  由模型按内容决定；继续把 US$1.08–5.39 写成“保守费用区间”会系统性低估预算。
  迁移时还发现缓存费用调用仍传旧 `cache_read_input_tokens`，与新归一字段不一致。
- **修复**：缓存计费改用 `prompt_cache_hit_tokens`；输出明确标成“可见译文”，费用标成
  “未含 reasoning 的基础参考”，并要求用新版每账号 3 条真实 usage 外推全量。
- **验证**：`usage` 回归覆盖 `prompt_tokens`、`completion_tokens`、缓存命中和嵌套
  reasoning token；真实付费请求未在本轮擅自执行。

### 11.1 验证状态

- `tests/tests_translate.py`：通过，覆盖官方接口线级契约、High thinking、无输出上限、
  标签硬校验与提示词版本自动过期。
- 当前两份真实 manifest 的扫描结果：1065 条记录中识别到 **7652 个标签**，与独立
  Unicode 类别扫描的总数一致；单帖最多 32 个，不存在“最多 3 个”的隐含假设。
- 全项目 `tests/tests_*.py`：**16 套 815 项，全部 exit 0**。
- 新版真实 `--check` 与 Facebook/Instagram 各 3 条仍待用户执行；旧 Anthropic 试跑
  只作历史证据，不能作为当前实现的真实验收。

---

## 13. 真实验收之后暴露的两条（2026-08-31）

> 用户 2026-08-31 按 `MANUAL_STEPS.md` 第 8 步完整跑了一遍（含最后那次真实抓取），
> **C2 / C4 / C5 验收通过**。这一节记的是那次运行**顺带暴露出来的东西**——
> 真实运行的价值往往不在它验证了什么，而在它顺手照出了什么。

### 13.1 那次真实验收本身

| | 结果 |
|---|---|
| Facebook | 新增 1 篇，**下了 5 张图**（91–100 KB，无 0 字节） |
| Instagram | 新增 1 篇，**下了 1 张图**（323 KB） |
| **IG 那篇新帖** | `owner=neakasa.global`、`coauthors=['neakasa.tech']` —— **它本身就是合作帖** |

最后一行是整件事的收口：**当天真实抓到的那篇新帖，恰好就是修复前会被丢掉的那一类**。
合作帖判定因此不只是"在保存的转储上成立"，而是**在当天的真实响应上仍然成立**。

**媒体下载至此第一次被真正触发**——此前几次实测都是"新增 0 篇"，那段代码从没跑过。

### CR-39 · P1 · 连续性检查看不见跨越窗口边界的缺口

- **位置**：`core/integrity.py::run_checks`
- **问题**：旧写法先把 60 天窗口外的帖子**整条剔掉**，再算相邻间隔。
  于是**起点在窗口外、终点在窗口内的缺口会整个消失**：窗口内最早那篇
  没有前邻居了，缺口无从产生。
- **实测**（2026-08-31 那次运行）：Facebook 报出了 5.9 天的缺口，
  而 **Instagram 一条都没报**——但归档里明明有一处
  `2026-07-01 → 2026-07-09` 的 **8.4 天**缺口（IG 阈值 6 天）。
  它一次都没被报过，**只因为起点比 60 天前早了一天**。
- **为什么这个盲区正好落在最坏的地方**：本项检查要抓的是"一段历史根本没抓到"。
  **洞越大，它的起点越早，就越容易被这样剔掉**——检查在最需要它的场景下最不灵。
- **修复**：改成先算全量相邻间隔，再按**较晚那篇是否落在窗口内**筛选。
  "不重报陈年缺口"的初衷不变（两端都在窗口外的仍然不报），盲区消失。
- **回归**：`tests_integrity.py` 新增跨边界与两端皆在窗口外两组断言。

### CR-40 · P1 · 离线预算漏掉了账单的主导项，低估一个数量级

- **位置**：`translate.py::run_estimate`
- **问题**：`--estimate` 按正文字符数换算 token，给出 **US$1.08–5.41**。
  但 thinking 开着时，**输出费用的 97–99% 花在看不见的 reasoning 上**，
  而字符换算完全不含这部分。它自己也承认估不了，让人"试跑 3 篇后自行外推"——
  **把一个能自动做的事留给了人**，而这种事人不会做。
- **实测**（2026-08-31，真实 3+3 篇）：

  | | 每篇输出 tok（中位） | 其中 reasoning | 占比 |
  |---|---:|---:|---:|
  | Facebook | 25 662 | 25 400 | **99%** |
  | Instagram | 4 464 | 4 351 | **97%** |

- **修复**：每篇的 usage 本来就写进了 `translated.jsonl`。
  `--estimate` 改为**优先按已译帖子的真实 usage 外推**（取中位数，不取均值——
  单篇思考量长尾很重），只在没有任何当前提示词版本的记录时才退回字符换算，
  并明确标注那个数不含 reasoning。**只采当前 `PROMPT_VERSION` 的样本**：
  换了提示词，思考量不可比。
- **修复后**：全量 1051 篇的预算从"US$1.08–5.41（不含 reasoning）"
  变成 **US$23.73（按真实 usage 外推）**。

### 13.2 一个留给业务拍板的量化结果

同一篇 1261 字符的帖子，其余条件完全相同，只改 `reasoning_effort`：

| effort | reasoning tok | 可见译文 tok | 单篇费用上界 |
|---|---:|---:|---:|
| `low` | **373** | 419 | US$0.0101 |
| `high` | **9 899** | 420 | US$0.0412 |

**可见译文一样长，思考量差 26 倍。** 但 `high` 的德语细节确实更好：
`low` 那版出现了 `Schluss mit Schlechtem Gewissen`（句中误大写）
和 `„…"` 引号不成对；`high` 两处都正确。

全量口径：`high` ≈ **US$24**，`low` ≈ US$6–7。
**这是业务取舍，代码不替用户定**，已写进 `config.toml` 的注释与
`MANUAL_STEPS.md` 第 7 步。

### 13.3 本轮之后的测试基线

**16 套 825 项，全绿**（stdout 被重定向的条件下同样全绿）。
新增 `tests_integrity.py` 的跨窗口缺口两项、`tests_translate.py` 的
`--estimate` 实测外推段（7 项）。

⚠️ 顺带修掉一条**写死配置值的断言**：`check(s.model == "deepseek-v4-pro")`
在业务把模型改成 flash 时当场变红，而实现完全正常。
**用哪个模型是 config.toml 里的业务取舍，不是代码契约**——
断言改成跟着 `Settings.validate()` 的白名单走。

---

## 14. 已实现主干二次审查（2026-08-31）

### 14.1 审查边界与取证方式

- 本轮开始时的代码基线为 `a259582`。按 `HANDOFF.md` / `IMPLEMENTATION_PLAN.md`
  当时的完成状态，只审 A/B/J/C/D/E/F 已实现主干；K（图片德语化）与 G（发布）
  当时仍是任务书/配置脚手架，不把未实现计划当缺陷，也没有补写那些功能。
- 审查过程中工作区被并行切到 `9ea342c`，并出现尚未提交的 K/G 实现文件。
  这些在本轮开始后才出现的在途改动没有被回退、覆盖或纳入本轮缺陷结论；全量测试
  只把它们当作兼容性验证。本文以下修改仅落在既有抓取、归档和翻译主干。
- 修改前先跑 16 套既有测试，**825 项全部通过**；这说明下面的问题都是原测试未覆盖的
  语义盲区，不能用“原来测试是绿的”否定。
- 需要外部契约的两处均查了第一手资料：Windows Task Scheduler 的实例策略与
  DeepSeek 的模型字段；其余结论均由本地最小复现、真实 archive/capture 离线扫描得到。
- 本轮没有发起 Facebook/Instagram 抓取、没有调用付费翻译 API、没有安装计划任务。
  两个 Windows 任务在审查时均为“未注册”；真实 capture 的验证全走离线重放。

### CR-41 · P1 · 两个计划任务可并发进入同一增量主干

- **位置**：`tools/schedule.py`、`routes/delta.py::main`
- **问题**：每日任务 `FBScraperDelta` 与登录/解锁补跑任务
  `FBScraperDeltaCatchup` 各自设置了 `MultipleInstancesPolicy=IgnoreNew`，但它只约束
  **同一个任务**已有实例。笔记本醒机时，错过的每日触发会因 `StartWhenAvailable`
  补跑，解锁触发器也会启动另一个任务；两者可同时读取同一份 `delta_state.json`、
  附着同一个 Chrome、写同一账号的 truth/manifest/capture。除状态丢失外，这还直接
  破坏 C7 “并发恒为 1”的风险边界。
- **外部证据**：Microsoft 的
  [Task Scheduler Settings schema](https://learn.microsoft.com/en-us/windows/win32/taskschd/taskschedulerschema-settings-tasktype-element)
  明确 `StartWhenAvailable` 会补跑错过的时刻；
  [`TASK_INSTANCES_POLICY`](https://learn.microsoft.com/en-us/windows/win32/api/taskschd/ne-taskschd-task_instances_policy)
  把策略对象表述为该 task 的既有实例，而不是跨任务全局互斥。
- **修复**：新增 OS 级 `DeltaRunLock`，使用 `state/delta.lock` 在 Windows 上做
  `msvcrt` 非阻塞字节锁、其它平台做 `flock`。锁在读取 state、判断 stale 和随机抖动
  **之前**取得，并一直覆盖 Chrome、归档和状态提交；`--reset-failures` 也走同一把锁。
  撞锁的计划任务明确打印原因并以 0 退出，不附着浏览器、不写状态。
- **验证**：同进程双实例回归通过；另用独立 Python 子进程竞争同一路径，子进程得到
  `child-blocked` / rc=7，证明不是只挡住测试进程内的第二个对象。

### CR-42 · P1 · 空归档会把“0 篇自家内容”判成成功

- **位置**：`routes/delta.py::delta_once`
- **问题**：旧门槛为 `min(config.min_own_posts, len(known))`。空归档时结果必为 0；
  页面即使只给推荐位/UGC，归属过滤后 `own=0` 仍满足 `0 < 0 == False`，随后刷新
  `last_success`、清空失败预算。错误账号、目标改名或冷启动没拿到时间线都会留下一个
  看似健康的“新增 0 篇”。
- **修复**：继续允许小归档按已有规模降低门槛，但只要配置没有显式设为 0，冷启动
  至少必须看到 1 篇属于目标账号的帖子；已有 1 篇的小账号看到 1 篇仍不会误报。
- **验证**：最小复现由“空归档 + 1 篇他人推荐位 => SUCCESS”变为
  `DeltaBlocked(...期望至少 1 篇)`；原“小归档仅 1 篇”的反例仍通过。

### CR-43 · P1 · 媒体只补回一部分时，恢复进展不会进入真相源

- **位置**：`core/capture.py::download_media`、
  `core/store.py::_post_quality_rank/Archive.reusable_media_path`
- **问题**：下载前，新解析记录是 `media_complete=True`，所以能通过 `should_append()`；
  如果两张失败图本次只补回一张，下载后又变成 `media_complete=False`。旧、新两条记录
  的媒体项数相同，旧质量等级判成平级，`Archive.append()` 拒绝新条目。图片文件虽然
  可能已写到磁盘，`post.json` / manifest 却不记录其 `local_path`；下一次又从头请求
  全部 CDN 图片，完整性检查也一直看到旧状态。
- **修复**：残缺记录改为“已知媒体数、已落盘数均不得倒退”的单调升级；同媒体项数下
  有新恢复路径可以追加，而“发现更多项但本轮全下载失败”不能反向抹掉旧路径。下载前
  按 `(post_id, media URL)` 找回旧路径，只复用当前预期
  truth dir 内、通过 symlink/junction/reparse/hardlink 防护且文件签名仍是允许图片的
  普通文件。已成功图片不再重复请求，只补失败项；若发布时间纠正导致 truth dir 变化，
  则不跨目录借用，避免旧目录隔离后产生悬空引用。
- **验证**：两图均失败 → 补回一图的记录现在会写盘且仍保持待补；同样进展重跑幂等；
  新增媒体但丢失旧路径的倒退被拒绝，保住旧路径后再新增媒体则允许升级。另有下载链路
  回归确认同 URL 的第二轮只请求第二张，复用第一张后可正常升级为完整记录。

### CR-44 · P2 · 修复旧帖被计成社媒新增，静默推迟零新增告警

- **位置**：`routes/delta.py::ScanResult/delta_once/_run_due`
- **问题**：旧逻辑只要 `Archive.append()` 成功就执行 `res.new += 1`，包括已知
  `post_id` 的媒体补全。`record_success(..., res.new)` 因而把 `last_new_at` 刷成今天，
  把“修好一篇旧归档”误写成“账号今天发了新帖”，长期零新增告警与降频时钟最多被
  推迟一个完整阈值周期。
- **修复**：写入前记录该 ID 是否已存在；真正的新帖子计入 `new`，已知残缺帖升级
  计入新设的 `upgraded` 诊断字段。摘要现在分别显示“新增 N / 修复旧帖 M”；状态机仍
  只用 `new` 更新 `last_new_at`。
- **验证**：真实 `_run_due` 路径完成一篇旧帖媒体升级后，归档变完整且本轮成功，
  但 `last_new_count=0`、原 `last_new_at` 保持不变。

### CR-45 · P1 · 模型名双向子串会放行未请求的短模型

- **位置**：`translate.py::_model_matches`
- **问题**：旧实现接受 `requested in actual` **或** `actual in requested`。
  因此请求 `deepseek-v4-pro`、实际响应 `deepseek-v4` 会返回匹配，绕过本来用于防止
  静默回退的 `ModelMismatchError`；整批付费结果可能在错误模型、成本和质量假设下落盘。
- **外部证据**：DeepSeek 官方
  [Chat Completions API](https://api-docs.deepseek.com/api/create-chat-completion/)
  将请求模型列为明确 ID，并定义响应 `model` 为“用于该 completion 的模型”；当前
  [Models & Pricing](https://api-docs.deepseek.com/quick_start/pricing)
  分别列出 `deepseek-v4-flash` 与 `deepseek-v4-pro`，不是可互相包含的别名族。
- **修复**：只做首尾空白与大小写归一化，然后要求精确相等；未知短前缀、后缀和
  Flash/Pro 互换均失败闭合。
- **验证**：同名不同大小写通过；`deepseek-v4`、`deepseek-v4-pro-unknown`、
  `deepseek-v4-flash` 均触发不匹配。OpenAI SDK MockTransport 线级请求契约仍通过。

### 14.2 核验后未改的事项

- `browser.close()`：查阅 Playwright 官方
  [`Browser.close`](https://playwright.dev/docs/api/class-browser#browser-close) 与
  [`connectOverCDP`](https://playwright.dev/docs/api/class-browsertype#browser-type-connect-over-cdp)
  契约后，确认对外部 CDP 浏览器这里是断开连接语义，没有证据表明会结束用户的专用
  Chrome，因此未做猜测性修改。
- 真实 archive 审计：Facebook 47、Instagram 1020 个唯一 truth/manifest 记录；未发现
  坏 JSON、重复 ID、缺 truth、越界 `local_path` 或当前 `media_complete=False`。
- `ruff` 对本轮修改源码没有新增问题；全文件扫描仍会报告 `translate.py` 既有的一个
  有意延后 import（E402）与两处无占位符 f-string（F541），均不影响运行，本轮不为
  格式清理扩大改动边界。

### 14.3 本轮最终验证

- 截至 2026-08-31 02:16 PDT 的当前工作区全量：**18 套 974 项，全部 exit 0**。
  其中本轮直接受影响的
  `tests_store.py` / `tests_delta_logged_in.py` / `tests_translate.py` 分别为
  **81 / 138 / 234** 项；并行新增的 K 组测试只计兼容性，不计本轮审查成果。
- `python -m compileall -q core routes tools translate.py`：通过。
- `uv pip check --python .venv\\Scripts\\python.exe`：23 个包依赖兼容。
- `git diff --check`（仅本轮文件）：通过。
- 两份最新真实 capture 离线跑完整 `delta_once()`：Facebook 6 篇自家内容、
  Instagram 36 篇（原创 1 / 合作 35），均为 0 新增且无写盘；IG 的
  `--break-coauthors` 自检会被 `min_own_posts` 闸正确中止并点名 35 篇已知合作方。

---

## 15. 第三轮 · K 组与 G 组并行分支的同步审查（2026-08-31）

> 审查日期：2026-08-31（第五轮交接之后）
> 审查对象：`feat/image-de`（K 组）与 `feat/business-suite-publish`（G 组）
> **两条线当天都在各自的 git worktree 里活跃写入**，因此本轮是**只读审查**：
> 一行代码都没改，findings 全部落在本文件里等实施。理由是 `HANDOFF.md`
> 「接手时遵守」第 7 条——别人正在写的文件不要碰，并发写会丢更新。

### 15.1 审查边界与方法

- 工作区拓扑（`git worktree list` 实测）：
  `FacebookScraper` = `wip/parallel-2026-08-31`（干净）、
  `FacebookScraper-image-de` = `feat/image-de`、
  `FacebookScraper-publish` = `feat/business-suite-publish`。
  **`git worktree` 这条路走通了**——两条线各自一个物理目录，
  `PIPELINE_PLAN.md` 第 15 节说的 junction 共享 `archive/` 问题被绕开的方式是
  **只复制 `manifest.jsonl` / `translated.jsonl` 做离线夹具**，不共享 `archive/`。
- 两条分支的**全部** `tests/tests_*.py` 都跑过：**各 17 套，全绿**
  （K 侧含 `tests_localize_images.py`、无 `tests_publish.py`；G 侧相反）。
  交接文件记录的两处红（`run_images.bat` 裸 LF、`tests_translate.py` 缺 `import re`）
  **都已被 K 组修掉**：`run_images.bat` 现在 13 个 CRLF / 0 裸 LF / 无 BOM / 纯 ASCII。
- 断言口径改用真实数据复核，而不是读对方的自述：818 个归档图片文件、
  1057 条有正文的帖子、`translated.jsonl` 的真实当前版本条目。

### 15.2 跨组与集成（本轮价值最高的一组）

#### CR-46 · P1 · 合并顺序决定冲突数：1 个文件 vs 13 个文件

- **位置**：分支拓扑，不在代码里。
- **事实**（`git merge-base` / `git merge-tree --write-tree` 实测，零副作用）：
  两条 feature 分支都从 **`9ea342c`** 开出，而 `9ea342c` 早于
  `main`（`3736303`）**也早于整条 `wip/parallel-2026-08-31`**。
  所以 `main` **不是**两条分支的祖先，`PUBLISH_PLAN`/`IMAGE_PLAN` 写的
  `git merge --ff-only` **在这里必然失败**——那不是"main 被人动过"，
  是分支基点选早了。**下一个会话看到 ff 失败时不要 `-f`。**
- **实测冲突集**：

  | 合并 | 冲突文件数 | 冲突文件 |
  |---|---:|---|
  | `main ← feat/image-de` | **1** | `docs/IMPLEMENTATION_PLAN.md` |
  | `main ← feat/business-suite-publish` | **1** | `docs/IMPLEMENTATION_PLAN.md` |
  | `feat/image-de ← feat/business-suite-publish` | **2** | 两份共享文档；**`config.toml` 自动合干净** |
  | `wip ← feat/image-de` | **6** | 含 `localize_images.py` / `prompts/image_de.md` / `tests_localize_images.py` 三个 **add/add** |
  | `wip ← feat/business-suite-publish` | **10** | 含 `publish/business_suite.py` add/add，`core/chrome.py`、`config.toml`、`MANUAL_STEPS.md`、`PUBLISH_PLAN.md` 内容冲突 |

- **为什么会这样**：`wip` 里的 `29784d2` / `ee43aca` 是两条线**更早的快照**
  （`localize_images.py` 差 68+/42-、`publish/compose.py` 差 244+/15-，
  同一血缘的旧版本）。`main` 一旦快进到 `wip`，那两份旧版就进了主干，
  feature 分支再合就变成整文件 add/add 对撞。
- **结论**：**不要先把 `main` 快进到 `wip`**（这恰恰是最自然的直觉）。
  正确顺序是先落两条 feature 分支，再把 `wip` 上**只属于主干**的提交摘过去：
  - `c35715d`（CR-41~45）只碰 `core/capture.py`、`core/store.py`、`routes/delta.py`
    与其测试 + 本文件——**两条 feature 分支一个都没碰**，cherry-pick 干净；
  - `3047c65` 的 `config.toml` 部分带来 **`[pipeline]` 整段配置与
    `[publish]` 段末的子表警告注释**，见 CR-49。
- **不做**：本轮没有执行任何合并，`main` 仍停在 `3736303`。

#### CR-47 · P1 · K9 与 G8 卡在同一件事：`translate.py` 没有"选哪几篇"的作用域

- **位置**：`translate.py::pending`（`out.sort(key=lambda r: r.get("created_at") or "")`，
  升序）+ CLI 只有 `--limit` / `--account`。
- **问题**：两份任务书点名的验收标的是**最新那三篇**
  （IG `3975547640610092585`、IG `3973012230169803390`、FB `122123185335379375`）。
  但 `pending()` 是**最老优先**，`--limit N` 永远从 2020 年那头开始翻，
  **没有任何命令能只翻这三篇**。
- **实测现状**：`translated.jsonl` 全档只有 6 条当前版本译文
  （`PROMPT_VERSION=5`）——FB 3 条是 2026-06-29/30，IG 3 条是 2020-09-03/04，
  **点名的三篇一条都没有**。因此：
  - K 的 `--latest-posts 3`（专为 K9 加的作用域）当前必然算出 **0 张待处理**；
  - G 的 `compose_post` 对这三篇全部按"译文缺失"正确拦下（`PUBLISH_PLAN` §5 已记录）。
- **两条线各自都撞上了同一面墙**：K 组已在 `HANDOFF.md` 自己那一行写明
  「现有 F `--limit 2/3` 实测会选最老帖子，不能精确补目标，须先获授权增加精确选帖」。
  **这不是两个 bug，是一个卡点。**
- **建议**：给 `translate.py` 加 `--post-id`（可重复）与/或 `--latest-posts N`，
  形状照抄 K 已经实现的那一套（`localize_images.py::select_rows`）。
  按当前 usage 外推，补这三篇（其中有图的两篇）约 **US$0.02–0.05**，
  之后 K9 与 G8 同时解锁。
- ⚠️ **归属问题需要用户拍板**：`translate.py` 的 CLI 作用域**不在** K 组的所有权
  切片里（K 只拿 `run_review` / `sync_text_de`），也不属于 G 组。
  它是主干改动，必须显式指派，否则又是一次 `translate.py` 混线（CR-45 与 K8 已经混过一次）。

#### CR-48 · P2 · `media_de/` 同序号多候选：K 组当成设计特性，G 组当成硬错误

- **位置**：`localize_images.py::manual_override_paths` / `review_image_pairs`
  ↔ `publish/compose.py::_choose_images`。
- **问题**：K 组**刻意支持**「程序图 `01.jpg` 与人工修订 `01.png` 并存、人工优先」，
  代码注释写得很明确（"设计人员可能保留 01.jpg 程序图，另放 01.png 修订图"），
  判定所有权用的是 `images_de.jsonl` 里的 `output_sha256`。
  G 组的 `_choose_images` 用 `candidate.stem == original.stem` 收集候选，
  **多于一个就直接 `ComposeError`**（"media_de 里第 N 张图有多个候选"）。
  于是 **K 组专门为设计同事设计的那个场景，会让这篇帖子发不出去**。
- **失败场景**：设计同事按 `MANUAL_STEPS` 往 `media_de/` 放 `01.png`，
  K 组正确地跳过并保留人工版，G 组组装时该帖被拒。
- **建议**：让 G 侧沿用 K 的优先级规则——读 `images_de.jsonl`，
  程序所有权之外的那个文件优先（与 K 的 `_candidate_is_program_owned` 同源）。
  失败闭合本身没错，但两组对同一目录的语义必须只有一套。
  **改哪一侧需要用户定**（改 G 是加一次只读依赖；改 K 是砍掉人工并存能力）。

#### CR-49 · P2 · `[pipeline]` 整段配置只存在于 `wip`，按当前合并路径会丢

- **事实**：`grep -c '^\[pipeline\]'` 在 `main` / `9ea342c` / 两条 feature 分支
  上都是 **0**，只有 `wip/parallel-2026-08-31` 是 1。
  `[publish]` 段末那句「新标量键请加在这一行之前」的 TOML 子表警告同理。
- **后果**：两条 feature 分支先落 main（CR-46 推荐路径）之后，
  `PIPELINE_PLAN.md` 第 13/14 节引用的 `[pipeline].autonomy` /
  `dead_man_days` / `monthly_budget_usd` 在 `config.toml` 里**不存在**，
  L0b/L0c 一开工就会踩空；而那句警告丢了之后，G 组下次往 `[publish]`
  加标量键就会静默掉进 `price_map` 子表。
- **建议**：合并时把 `3047c65` 的 `config.toml` 部分一并摘过来（CR-46）。

#### CR-50 · P3 · K 组的 `--estimate` / `--dry-run` 自称"零写盘"，但 `Archive()` 会建目录

- **位置**：`localize_images.py::build_jobs`（`Archive(arc_base.parent, arc_base.name)`
  → `core/store.py::Archive.__init__` 里 `self.posts_dir.mkdir(parents=True, exist_ok=True)`）。
- **对照**：G 组在 `compose.py::_find_source` 里**显式先判后建**——
  `if not account_dir.is_dir() or not posts_dir.is_dir(): raise ArchivePathError("…compose 只读不创建")`，
  处理方式是对的。
- **影响**：今天目录都存在，所以是措辞问题不是行为问题；
  但"零写盘"这句话同时出现在 docstring 和 `--estimate` 的输出末尾，
  不改就等于在文档里立了一条实现没有兑现的保证。

### 15.3 K 组（`feat/image-de`）

#### CR-51 · P1 · `[image.keep_verbatim].models` 在工作区里丢了 `"S1 Pro"`

- **位置**：`config.toml` 第 316 行，**未提交的工作区改动**（`553fd79` 里还在）。
- **事实**：这一轮往列表里加了 `P1 Pro`、`Riko`、`RIKO`、`NeakasaP1Pro`
  （语料实测各 39 / 78 / 8 / 3 篇，**加得对**），
  但同一次编辑把原有的 **`"S1 Pro"` 删掉了**。
  语料实测 `S1 Pro` 出现在 **10 篇**帖子里；另有 `S1Pro`（10 篇）、
  `P1Pro`（3 篇）两种连写形态从来不在列表里。
- **为什么算 P1**：这张表就是 `IMAGE_PLAN` 第 3.1 节「译错 = 商业事故」那一类，
  型号是标识符。K3 预扫描已取消之后，**这张表在提示词里的分量比原计划更重**
  （`IMAGE_PLAN` §K4 原话），它漏一项没有任何机器能补。
- **状态**：改动仍在 in-flight，K 组 Agent 可能自己会补回。
  **合并前必须逐项核对这个列表，不要假定它只增不减。**

#### CR-52 · P2 · 一张图坏掉会掀掉整个账号的批次，`--estimate` 直接抛 traceback

- **位置**：`localize_images.py::build_jobs`（`_source_from_manifest` /
  `Image.open` / `legal_size` 三处都 `raise ValueError`），
  且 `run_estimate` / `run_show_prompt` 在 `main` 里**不在** try 之内（第 1543–1546 行）。
- **复现**（K 组 worktree，零 API）：
  `localize_images.py --estimate --all-history`
  → `ValueError: 原图不存在：posts/2020-09-04_1027_2390713492816847396/01.jpg`
  的**裸 traceback**。
- **两个独立问题**：
  1. `build_jobs` 在任何一张图上失败就整账号中止，一张都不处理——
     与 F 组已验证的做法相反（`translate.py` 逐篇失败、继续、留断点）；
  2. 决定"要不要花钱"的那条离线命令没有错误处理，用户看到的是 traceback。
- **今天不触发**：CR-14.2 的真实归档审计确认无越界 `local_path`、无坏 JSON，
  本轮独立复核 818 张图 **全部可解码、无一超过 3:1**。
  但 `--estimate` 是花钱前的最后一道人类判断，它崩掉的代价是"看不见账单就跑"。
- **建议**：per-image 失败计入 `RunStats.failed` 并列名继续；
  两条离线命令套上与 `run_localize` 同一层的异常处理。

#### CR-53 · P2 · `_is_fatal_api_error` 把整个 `openai.APIError` 当致命，一次抖动断掉整批

- **位置**：`localize_images.py::_is_fatal_api_error`（`isinstance(exc, openai.APIError)`）。
- **问题**：`openai.APIError` 是 `APIStatusError`（含 `RateLimitError` 429、
  `BadRequestError` 400）与 `APIConnectionError` / `APITimeoutError` 的**共同基类**。
  于是：SDK 重试用尽后的一次 429、一次网络超时、或**某一张图被内容审核拒掉**，
  都会抛 `FatalBatchError` 掀掉剩余全部图片，而且没有跳过这张图继续的办法。
- **权衡**：对付费批次"宁可停"是对的方向，鉴权/端点/模型不匹配必须立刻停。
  但把**单图 400** 和**瞬时网络**也算进去，等于让一张有问题的图永久堵住队列。
- **建议**：致命集合收窄到
  `AuthenticationError` / `PermissionDeniedError` / `NotFoundError` /
  `ModelMismatchError` / `ResponseContractError`；
  其余按单图失败处理，并加一条**连续失败预算**（形状照抄 C7 的失败预算）。

#### CR-54 · P3 · 没有"放大倍数"闸；顺带把 dHash / drift 的标定数据备好了

- **位置**：`localize_images.py::legal_size` / `aspect_drift_percent`。
- **先说好的**：用真实归档 818 张图逐张跑 `legal_size`，**0 失败**，
  任务书那张分布表逐行对得上（1080×1080→1088×1088、1080×1350→1088×1360、
  720×720→816×816、1440×1440 原样、1350×1687→1344×1680、1440×1080→1440×1088）。
- **缺口**：合法化会**放大**小图，而 `aspect_drift_percent` 抓不到这件事。
  线性缩放倍数实测分布：

  ```
  0.8× :   7      1.0× : 772      1.1× :  18      1.2× :   6
  1.3× :   5      1.4× :   4      1.5× :   3      1.7× :   1
  2.3× :   1      2.4× :   1
  ```

  最极端两张：`278×430 → 656×1008`（**2.35×**）、`320×400 → 720×912`（2.26×）。
  前者的宽高比形变只有 **0.66%**，稳稳低于 `aspect_drift_warn_percent = 2.0`，
  **会静默通过**——发出去的德语图是一张 2.35 倍放大的图。
- **建议**：`images_de.jsonl` 记一个 `scale_factor`，超过约 1.25× 进人工清单
  （13/818 会命中，量很小）。与 `IMAGE_PLAN` §3.1 那句
  "在无法验证的介质上做不可逆的数值变换"同源。
- **顺带交付两组标定数据（不用重新测）**：
  - **宽高比形变**：median `0.0000%` / p95 `0.653%` / max `2.794%`，
    全档只有 **3/818** 超过配置的 2.0%。
    → **`aspect_drift_warn_percent = 2.0` 标得很准，不要动它。**
  - **dHash 阈值**仍无真实样本（`dhash_max_distance = -1` 是对的）。
    `--estimate` 已经会打印全部距离供人眼标定，K5 的做法照原样执行即可。

#### CR-55 · P3 · `decode_image_payload` 只 `strip()` 就 `validate=True` 解码

- **位置**：`localize_images.py::decode_image_payload`。
- **问题**：`base64.b64decode(payload, validate=True)` 对**内部**空白字符（`\n`）
  一律报错。中转网关按 76 列折行返回 base64 是常见形态；一旦如此，
  **钱已经花掉了，产出全部被判"不是合法的裸 base64"丢弃**。
- **建议**：解码前 `re.sub(r"\s+", "", payload)`，并容忍
  `data:image/...;base64,` 前缀。两行代码换掉一整批付费结果的风险。
- **状态**：未在真实网关上观察到（本轮没发任何付费请求）。属于便宜的加固，不是已知缺陷。

#### CR-56 · P3 · `translate.py --review` 现在硬依赖 `[image]` 配置

- **位置**：`translate.py::run_review`（K8 接入处）无条件 `image_de.Settings()`。
- **问题**：`Settings.__init__` 会做 `[image]` 与 `[image.keep_verbatim]` 的
  双向配置审计并 `SystemExit`。于是 `[image]` 任何一处不合法，
  **F 组的审校清单整条命令就跑不出来**，即便这个账号一张德语图都没有。
- **建议**：延后构造或只取所需的 `aspect_drift_warn_percent`；
  它只在有 `pair.record` 时才被用到。

#### CR-57 · P3 · 改 `output_format` 会留下两份产物，正好撞上 CR-48

- **位置**：`localize_images.py::image_record_is_current` / `_target_for_job`。
- **问题**：完成判据（按任务书）不含 `output_format`。把 `jpeg` 改成 `png` 之后，
  旧 `01.jpg` 的记录仍然算"当前"，新 `01.png` 永远不会生成；
  一旦 `--force`，`media_de/` 里就同时有 `01.jpg` 与 `01.png` ——
  **而 G 组的 `_choose_images` 见到两个候选就拒发**（CR-48）。
- **建议**：要么把 `output_format` 纳入失效条件，要么在检测到与现有当前记录
  格式不同时显式拒绝运行并说清原因。

### 15.4 G 组（`feat/business-suite-publish`）

#### CR-58 · P2 · `compose_post` 没有任何用户入口，§5 的验收无法被复现

- **位置**：`publish/compose.py` 的唯一调用方是 `tests/tests_publish.py`
  （`publish/__init__.py` 只做 re-export）；`tools/` 下无 compose 入口，
  `scripts/run_publish.bat` 不存在。
- **问题**：`PUBLISH_PLAN` §5 的【验收】是"对最新 3 篇真实帖组装成功；
  人为把译文改过期 / 删掉一张图各自被正确拒绝"。
  这条验收目前**只能靠临时 `python -c` 做**，用户无法照 `MANUAL_STEPS` 复跑。
  项目工作协议要求"需要用户操作的功能同步更新 `MANUAL_STEPS.md`"——
  没有命令就没有可交接的验收。
- **建议**：加一个只读入口（如 `tools/compose_publish.py --post-id … --at …`），
  打印 `DePost.review_lines()` 与全部 warning，零浏览器操作。
  它同时正好是 `require_confirmation = true` 那道人工闸需要看的那张清单。

#### CR-59 · P3 · `cdp_ready(profile=…)` 每次都起一个 PowerShell，`launch()` 会起最多 16 次

- **位置**：`core/chrome.py::_cdp_profile_matches` ← `cdp_ready` ← `launch` 轮询循环。
- **问题**：`launch()` 在 15 秒窗口里每秒调一次 `cdp_ready(port, profile=…)`，
  每次 fork 一个 `powershell.exe`（`Get-NetTCPConnection` + `Win32_Process`，
  超时 5 秒）。功能是对的——`/json/version` 确实证明不了"是哪份 profile"，
  这道归属校验是 G0 的核心价值——但代价是启动脚本明显变慢，
  且在缺 `Get-NetTCPConnection` 的环境里每轮都白等。
- **建议**：端口 CDP 就绪之后**只核对一次** profile 归属，
  或按 `(port, profile)` 缓存结果；轮询阶段只用轻量的 `/json/version`。

### 15.5 复核后确认无需改动的

- **`publish/selectors.py` 是空的**（只有 TODO 注释）——**这是对的**，
  红线 5 与 `PUBLISH_PLAN` §12.5 要的就是这个。不要"顺手补两个看起来合理的"。
- **`publish/business_suite.py` 的五个函数在接触 `page` 前抛 `ProbeRequired`**——
  设计如此，不是未完成。
- **`browser.close()`**：CR-14.2 已经查过 Playwright 契约并判定对外部 CDP
  浏览器是断开语义，`tools/probe_publish.py` 与 `routes/backfill.py` /
  `routes/delta.py` 的写法一致。**本轮不再重提。**
- **`.bat` 字节约定**：`run_images.bat`（13 CRLF / 0 裸 LF / 无 BOM / 纯 ASCII）
  与 `start_chrome_publish.bat`（23 CRLF / 同上）都合规。
- **`config.toml` 两段互不重叠**：K 只改 `[image]` / `[image.keep_verbatim]`，
  G 只改 `[publish]`，`git merge-tree` 实测自动合干净。**所有权表起作用了。**
- **`docs/HANDOFF.md`**：两条线都只改了「现在卡在哪」里自己那一行。
  互相之间会冲突（同一张表相邻行），但内容都要保留，**不许二选一**。
- **G 组的归属硬闸对真实数据成立**：抽查 FB `post.json` 确认
  `owner` / `owner_name` / `coauthors` / `media_complete` 四个字段都在，
  `_choose_images` 与合作帖归属校验不会把 FB 原创帖误拦。
- **`probe_publish.py` 的隐私边界**：不记 class/CSS path、
  密码/邮箱/OTP 类输入不产生事件且截图遮罩、URL 去掉 query/fragment。
  与 `_PROBE_REQUIRED_OBSERVATIONS` 逐项对齐（`extra_notes` 正确地是可选项）。

### 15.6 ✅ 处置结果（2026-08-31 晚，两条分支都已收工之后）

> 上面 15.1–15.5 是**只读审查**时的记录，一行代码没改。
> 两个 Agent 收工、工作区变干净之后，按用户的四个决定逐条实施。
> **每条修复都带断言**，指向的是"这条不许被顺手改回去"，不只是防回归。

| # | 处置 | 落在哪条分支 |
|---|---|---|
| CR-46 合并顺序 | ⬜ **未执行**（用户决定 `main` 先不动，等验收） | — |
| CR-47 选帖作用域 | ✅ 已实施 **并真实翻译了那三篇** | `feat/image-de` |
| CR-48 media_de 语义 | ✅ 按用户拍板**改 G 侧** | `feat/business-suite-publish` |
| CR-49 `[pipeline]` 配置会丢 | ⬜ 记录在案，随 CR-46 一起处理 | — |
| CR-50 只读命令建目录 | ✅ 新增 `readonly_archive()` | `feat/image-de` |
| CR-51 `S1 Pro` 被删 | ✅ 补回 + 两种连写形态 + 六个断言 | `feat/image-de` |
| CR-52 单图失败掀整批 | ✅ 逐图跳过并点名；离线命令套异常处理 | `feat/image-de` |
| CR-53 `APIError` 全当致命 | ✅ 收窄 + `failure_budget` | `feat/image-de` |
| CR-54 无放大闸 | ✅ `scale_factor` + `scale_warn_factor` | `feat/image-de` |
| CR-55 base64 折行 | ✅ 先剥空白/前缀再严格解码 | `feat/image-de` |
| CR-56 `--review` 硬依赖 `[image]` | ✅ 失败降级 | `feat/image-de` |
| CR-57 `output_format` 留两份 | ✅ 判据加产出路径 | `feat/image-de` |
| CR-58 compose 无入口 | ✅ `tools/compose_publish.py` + `.bat` | `feat/business-suite-publish` |
| CR-59 每秒 fork PowerShell | ✅ 轮询轻量化，核对从 ≤16 次降到 2 次 | `feat/business-suite-publish` |
| **CR-60（实施中新发现）** | ✅ 见下 | `feat/business-suite-publish` |

#### CR-60 · P1 · `ZoneInfo("Europe/Berlin")` 在这台 Windows 上直接抛异常

- **发现经过**：给 `tools/compose_publish.py`（CR-58）接排期时区时当场撞上。
  **只读审查阶段没发现它**，因为在此之前**没有任何代码用过 `zoneinfo`**——
  `[publish].timezone = "Europe/Berlin"` 只是一个还没人消费的配置值。
- **事实**：实测 `zoneinfo.TZPATH` 是**空元组**，
  `ZoneInfo("Europe/Berlin")` → `ZoneInfoNotFoundError`。
  Windows 不自带 IANA 时区数据库，而 `zoneinfo` 只读系统数据库。
- **为什么是 P1**：`PUBLISH_PLAN` 第 3.3 节把显式时区转换写成**硬要求**
  （"代码里显式做时区转换，不依赖本地时区隐式生效"），
  并要求**在夏令时切换日各测一次**。两件事都不可能靠写死 UTC 偏移做对。
  **G5 一旦按计划实现就会当场失败**，而绕过它的那个"显然"的办法
  （硬编码 +01:00/+02:00）会在每年两天把帖子发到错误的时刻，
  **而且没人会立刻发现**——与 CR-19 的"静默丢弃 263 篇"同一类失效。
- **处置**：`requirements.txt` 加 `tzdata>=2024.1`（纯数据包、无原生代码、
  无传递依赖），理由按 Pillow 的先例写进注释。已装进 `.venv`（2026.3），
  并验证 2026 年两个切换日的偏移：
  `03-29 01:30 → +01:00`、`03-29 03:30 → +02:00`、
  `10-25 01:30 → +02:00`、`10-25 03:30 → +01:00`。
- ⚠️ **这是唯一一处越出所有权表的改动**（表里写着"`requirements.txt` 三组都不用改"）。
  那句话的本意是避免 K/G 同时改这一个文件；K 组已收工且没碰它，实际零冲突。
  偏差记在 `IMPLEMENTATION_PLAN` 的 G0c 里，不是默默改的。

#### 两条真实数据结果（不是离线断言）

1. **F 侧真实翻译**：计划点名的三篇全部译成，成功 3 / 失败 0，
   **实际费用上界 US$0.1799**。
   ⚠️ 离线外推给的是 US$0.038，**实际高 4.7 倍**，差在 44 327 reasoning tok。
   **CR-40 那条教训又验证了一次：thinking 主导的账单，离线外推仍然会低估。**
2. **G0b 真实验收通过**：`可组装 3 篇 / 被硬闸拦下 0 篇`。
   金额硬闸在真实正文的 `$219.99` / `$100` 上通过；
   11 张图全部按"缺德语图 → 回退原图"逐张点名（K 组还没跑，符合预期）；
   `--strict` 在 G1 未完成时正确失败闭合。

#### 合并态验证（用 `git merge-tree` + 一次性 worktree，**没有动任何分支**）

把 `main ← feat/image-de ← feat/business-suite-publish` 的合并结果落进一个
临时 detached worktree 跑了一遍：

- **代码里零冲突标记**（`config.toml` / `requirements.txt` / `translate.py` 全部自动合）；
- **18 套测试全绿**（K 的 17 + G 的 `tests_publish.py`）；
- **K → G 交接契约双向成立**：K 实际写出的 `images_de.jsonl` 记录，
  G 的 `_program_owned_media_de` / `_is_program_output` 能认；
  把产出文件的字节改掉之后，**两边都判为人工版本**（同向保守）。
- 仍然冲突的只有三份共享文档（`IMPLEMENTATION_PLAN` / `MANUAL_STEPS` / `HANDOFF`），
  按项目约定**两段都保留，不许二选一**。

临时 worktree 已删除，`main` 仍停在 `3736303`。

### 15.7 本轮没做什么

- **审查阶段没有改任何一行代码。** 两条 worktree 当时都在活跃写入
  （`config.toml` / `compose.py` / 文档的 mtime 都在分钟级刷新），
  按 `HANDOFF.md` 的协作纪律，findings 只落文档、不落别人的工作区。
  **修复是在两个 Agent 收工、工作区干净之后才做的**（见 15.6）。
- **没有执行任何合并、rebase、branch -f。** `main` 仍在 `3736303`。
- **图片侧没有发出任何付费请求**（GPT-Image-2 一次都没调）。
  翻译侧按用户授权发了 3 次真实请求，**费用上界 US$0.1799**，
  这是本轮唯一的支出。
- **没有真实社媒访问**（没跑 delta、没碰浏览器）。
- **计划任务仍未安装**：`python -m tools.schedule status` 实测
  `FBScraperDelta` 与 `FBScraperDeltaCatchup` 双双"未注册"。
  `PIPELINE_PLAN.md` 第 16 节那句话仍然成立——**这个项目的自动化程度还是 0。**

---

## 16. 第四轮 · 合并前的收尾审查（2026-08-31，接手会话）

> 审查对象与第 15 节相同的两条分支，但这次**两个工作区都已收工且干净**，
> 所以发现即修复，不再只落文档。
> 先读了 15.6 的处置结果表，**13 条已修的一条都没有重审**。

### 16.1 复核结论：`HANDOFF.md` 点名的四处全部通过

| # | 位置 | 结论 |
|---|---|---|
| 1 | `localize_images.py::_write_output` 的三次人工图复查窗口 | ✅ 逻辑正确。第三次检查发生在**所有权记录已 fsync 之后、`os.replace` 之前**，这个顺序是对的：此时 `owned_hashes` 已含新哈希，而磁盘上还是旧字节，于是"程序旧图"仍判为程序所有、"设计同事刚放进来的文件"仍判为人工。硬终止落在记录与 replace 之间时，`_record_output_exists` 的哈希比对会失败并重排该任务，不会把付费产物记成已完成 |
| 2 | `compose.py::_pick_localized` / `_is_program_output` ↔ `localize_images.py::_candidate_is_program_owned` | ✅ 两套规则同源：都按 `images_de.jsonl` 的 `out_path` + `output_sha256` 认所有权，`None`（旧 schema 无哈希）都退化为按路径认，字节被改过都判为人工。**序号口径也一致**——K 的 `_target_for_job` 用 `media_index+1`，G 的 `_choose_images` 用 `local_path` 的 stem，而 `core/capture.py::download_media` 正是按**全 media 列表**下标命名 `01.jpg`，三者对得上 |
| 3 | `translate.py::resolve_scope` / `pending` | ✅ **不是绕过数据契约的后门**。`pending` 里作用域过滤放在 `post_id` / `text` 两处 `SourceDataError` **之后**；`resolve_scope` 自己也对全部账号逐行校验。即使只翻一篇，整份 manifest 的形态问题仍在联网前失败闭合。`--latest-posts` 的排序键 `(created_at, 账号, post_id)` 与 `localize_images.py::select_rows`、`tools/compose_publish.py::_latest_post_ids` 三处完全一致 |
| 4 | `core/chrome.py::launch` 的轮询 | ✅ profile 归属核对**还在**，只是移出了轮询循环：端口就绪后 `return cdp_ready(port, profile=…)`，超时后的兜底同样带 profile。`argv` 用的是解析后的 `port` / `profile`，不是 `[chrome]` 的值，没有"参数化了一半"的问题 |

另外复核通过、无需改动的：两条分支 7 个 `.bat` 全部 0 裸 LF / 无 BOM / 纯 ASCII
（含 15.5 之后才新增的 `run_publish.bat`）；`assert_publish_chrome_isolated()`
在 `tools/start_chrome_publish.py` 与 `tools/probe_publish.py` 里都真的被调用了，
不是死代码；`publish/selectors.py` 仍是空的、`business_suite.py` 五个
`ProbeRequired` 仍在——**按红线 5，这是对的，本轮没有动它们**。

### 16.2 CR-61 · P2 · 发布侧只再判金额，不判话题标签

- **位置**：`publish/compose.py::_load_current_translation`。
- **事实**：`translate.py::run_translate` 把 `money_preserved` 与
  `hashtags_preserved` 的 violations **合成一个列表**，任一不过都不写盘——
  在写入侧，这两条本来就是同一类「不可改内容规则」。
  但 `PUBLISH_PLAN` 第 5 节的离线硬校验清单**只列了金额**
  （"直接复用 `money_preserved`，不重写第二份"），于是 compose 也只再判金额。
- **为什么"写盘时判过"不够**：`translated.jsonl` 是译文真相源，而**人工审校的
  修正就是直接改它**——`review.md` 自己写着"直接修改本文件不会回写数据"，
  `MANUAL_STEPS.md` 第 503 行同义。手工改动**不经过** `run_translate` 的写盘闸。
  这正是金额那道闸要在发布环节再过一次的全部理由，标签适用同一条推理。
- **失败场景**：审校同事在 `translated.jsonl` 里修一句德语措辞，顺手把
  `#NeakasaRiko` 打成 `#NeakasaRiko ` / `#Neakasariko` / 少写一个 →
  金额闸放行、`_validate_instagram` 的**数量**上限也放行（数量没变）→
  带错标签的帖子发到德语主页。IG 那侧标签是触达路径，不是装饰。
- **处置**：✅ 已修（`feat/business-suite-publish` @ `0377d1f`）。
  复用 `hashtags_preserved`，不重写第二份；`PUBLISH_PLAN` 第 5 节补第 2b 条。
- **上线前实测**：真实归档 9 条当前有效译文跑一遍新旧两道闸——
  **金额违规 0 / 标签违规 0**，新闸不改变任何现有可组装帖，
  G0b 已验收的"最新 3 篇可组装"不受影响。
- **断言**：三条（改写标签 / 删掉一个 / 只调换顺序），指向"这道闸不许被顺手去掉"，
  口径与 `translate.py` 写盘闸完全一致，不放宽。

### 16.3 本轮的测试口径

- `feat/image-de`：**17 套全绿**（`../FacebookScraper/.venv` 跑，`pwd` 已确认在
  feature worktree 里，不是主目录的旧快照）。
- `feat/business-suite-publish`：**17 套全绿**；`tests_publish.py` 从 89 项增至
  **92 项**（CR-61 的三条）。
- 两条分支 `compileall` 干净、`git diff --check` 干净。
- **合并落 `main` 之后**：**18 套 / 1108 项全绿**，`compileall -q .` 干净、
  `git diff --check` 干净、代码里零冲突标记、`tomllib` 解析通过、
  `uv pip check` 24 包兼容、8 个 `.bat` 全部 0 裸 LF / 无 BOM / 纯 ASCII。

### 16.4 合并态在**真实数据**上的复核（feature worktree 里做不到）

两条 feature worktree 都没有 `archive/`，所以它们跑 `--estimate` / `--dry-run`
只会得到误导性的 0（`HANDOFF` 早就写了这条）。归位成单目录之后补做的：

| 复核 | 结果 |
|---|---|
| `localize_images.py --dry-run --post-id ×3` | **11 张待处理**（FB 5 + IG 5 + IG 1），与合并前一致 |
| `--estimate --all-history` | 正常收尾、**零 traceback**（CR-52 的回归点） |
| 三道费用闸 | `--all-history` 真实运行退出码 **2**；`--latest-posts 4` 被拒；`--confirm-all-history-cost` 不能单独用 |
| `translate.py --dry-run --post-id ×3` | 作用域正确（作用域内 1 + 2 篇），不存在的 post_id 失败闭合退出 1 |
| `tools/compose_publish.py --post-id ×3` | **3 篇全部组装成功、零拦下**（G0b 在合并态复现）；`--strict` 3 篇全部正确失败闭合 |
| **K→G 所有权契约** | 7 条断言全过（见下） |
| **K8 并排审校** | 7 条断言全过（在真实数据的临时副本上真跑 `run_review`） |
| **CR-56 降级** | 把 `[image]` 弄坏之后审校清单照常生成，只是不打形变/放大告警 |
| **CR-60 tzdata** | `tzdata 2026.3`；2026 年两个切换日的偏移四次全对 |
| 浏览器隔离闸 | 真实 config 上 9222/`.fbscraper-chrome` 与 9223/`.fbscraper-publish` 互不相同 |

**K→G 所有权契约的七条**（这是两组唯一的交接面，也是 CR-48 改过的地方）：
K 写出的记录 K 认得、G 也认得；把产出字节改掉之后**两边同时**判为人工版本
（同向保守）；`01.jpg`（程序）与 `01.png`（人工）并存时，K 只把 `01.png` 当人工、
不误伤自己的 `01.jpg`，而 G 选人工版并显式告警——**这正是 CR-48 修掉的拒发场景**；
两个候选都不是程序产出时 G 失败闭合并点名，不替人猜。

**全程零 API 调用、零费用、零社媒访问，真实 `archive/` 一个字节都没动过。**

### 16.5 CR-62 · P2 · 切分支之后 `.bat` 会带着裸 LF，而 `git status` 看不见

- **位置**：不在代码里，在**工作区与 git filter 的交互**上。
- **发现经过**：合并推送完、主目录从 `wip` 切到 `main` 之后跑全量，
  `tests_schedule.py` 报 `run_images.bat 有裸 LF` ——
  而同一份文件在 feature worktree 里刚验过是 13 CRLF / 0 裸 LF。
- **事实**：`.gitattributes` 写着 `*.bat text eol=crlf`，所以
  **仓库里的 blob 是 LF，检出时才转 CRLF**。但 clean filter 把 CRLF 和 LF
  **归一成同一个 blob**，于是工作区那份即使是裸 LF，`git status` 也报干净——
  **git 结构性地看不见这个漂移**。
- **后果**：裸 LF 的 `.bat` 在 `cmd.exe` 里整行误解析，表现为
  `'xxx' 不是内部或外部命令`。用户双击就坏，而仓库里是对的。
- **处置**：✅ 让 git 重新物化该文件即可，仓库内容本身没问题：

  ```bash
  rm scripts/run_images.bat && git checkout -- scripts/run_images.bat
  ```

  修完 8 个 `.bat` 全部 0 裸 LF / 无 BOM / 纯 ASCII，18 套 1108 项全绿。
- ⛔ **不要手工改字节，更不要去改那条断言。**
  `tests_schedule.py` 的三条 `.bat` 字节断言就是为这件事写的，
  这一轮它是唯一发现问题的东西。**切分支之后跑一次全量，理由就是这个。**

---

## 17. CR-63 · P1 · 归属核对的超时是 5 秒，而这台机器要 7–9 秒（2026-09-01）

> **发现方式与前面所有条都不同：不是审查看出来的，是用户调 G1 时当场撞上的。**
> 离线测试全绿、17 套断言全过，而真实环境里这条路径**从来没成功过一次**。

### 17.1 现象

用户跑 `scripts\start_chrome_publish.bat`，**浏览器窗口正常打开了**，却得到：

```
等待发布调试端口就绪....
[!] 等了 15 秒，发布 Chrome 的调试端点 9223 仍未就绪。常见原因：
    1. 发布 profile 已被另一个 Chrome 占用……
```

接着 `tools/probe_publish.py` 说：

```
端口 9223 是 Chrome 调试端口，但不属于目标 profile，或 Windows 无法读取其进程归属。
不要继续附着；先关闭占错端口的 Chrome，再运行 scripts\start_chrome_publish.bat。
```

**两条消息都是错的**，而且第二条会让用户去关掉一个**本来就正确**的浏览器。

### 17.2 根因：一个没有被标定过的超时

`core/chrome.py::_cdp_profile_matches` 把
`Get-NetTCPConnection` + `Get-CimInstance Win32_Process` 串成一条 PowerShell
跑，**超时写死 5 秒**。本机（Windows 11 / Chrome 152）逐段实测：

| 步骤 | 耗时 |
|---|---:|
| `powershell.exe` 空跑（纯启动开销） | **2.17s** |
| ＋ `Get-NetTCPConnection` | **7.84s**（光加载 NetTCPIP 模块就 ~5.7s） |
| ＋ `Get-CimInstance Win32_Process` | 3.84s |
| `netstat -ano`（纯 exe，给出**同一个** PID） | **0.49s** |

整条实测 **7.4–9.4 秒**，于是**每一次都 `TimeoutExpired`**，
被 `except (OSError, subprocess.SubprocessError)` 吃掉、返回 `None`，
调用方按"核对不了"失败闭合。

**这是 CR-59 留下的尾巴**：那一轮正确地发现"这个调用很贵"并把它从
每秒一次降到 2 次，**却没有人真的测过它到底多贵**，5 秒的超时原样留着。
真实环境里它 100% 失败——而离线测试全部 mock 掉了 `_cdp_profile_matches`，
所以 17 套断言一条都没响。

> **教训**：`timeout=` 是一个**关于目标机器的经验断言**。
> 没有在目标机器上量过的超时值，和写死的选择器是同一类东西。

### 17.3 处置

**① 端口→PID 改用 `netstat -ano`，只留 PID→命令行走 PowerShell。**
两者给出同一个 PID，但 0.12s vs 5.7s。改完实测 **3.4–3.6 秒**。

**② `PROFILE_PROBE_TIMEOUT = 20.0`**，抽成模块常量并把上面那张实测表
写在它旁边。20 秒是在实测 3.5s 上留 5 倍余量：这条路径一轮只跑一两次，
**宁可慢也不能误判**。

**③ 三态分诊：`False`（确实是别的 profile）与 `None`（核对不了）必须分开说。**
原来两种情况印同一段话，于是正确的环境被报成"你开错了浏览器，去关掉它"。
**让人去关一个本来就对的 Chrome，比不报错更坏。**
`None` 那一支现在改成"**无法确认**它属于哪份 profile（不是说它错了）"，
并给出两条只读的自查命令（`netstat` + `Get-CimInstance`），
让用户自己看一眼 `--user-data-dir`。仍然失败闭合，但话说对了。

**④ `tools/start_chrome_publish.py` 在 `launch()` 返回 False 后重新分诊。**
原来只印"等了 15 秒端口没起来"那一种，还把秒数写死——
而归属核对失败时它其实只等了 4 秒，那份排查清单三条全不适用。

### 17.4 验证（真实环境，不是 mock）

- 用户当时开着的那个 Chrome：`_cdp_profile_matches` 从 `None` → **`True`**（4.02s）；
  `attach()` 成功（6.38s），`contexts=1 / pages=1`，窗口没被动过。
- `scripts\start_chrome_publish.bat` 现在正确识别为"已就绪"，退出码 0。
- 反向也验了：传抓取 profile → `False`；传没人监听的端口 → `None`。
- 新增 17 条断言（`tests_chrome.py` 33 → 50）：netstat 解析（含 IPv6、
  **端口整段匹配不能子串命中**、只认 LISTENING）、超时下限、
  三态不许塌成两态、attach 两支话不同。**19 套 / 1199 项全绿。**

### 17.5 顺带清掉的东西

用户那三次失败尝试在 `state/` 留下三份**空的** probe dump
（`interactions=0`、`observations` 全空）。它们不会被误当成真的——
`compose.py::_validated_probe_dump` 要求 ≥7 条可信交互与全部必填观察，
空 dump 会被拒。**但它们是噪音，G1 真正跑通后应当删掉，
免得下次有人对着一堆同名文件分不清哪份是真的。**

---

## 18. CR-64 · P0 · G1 recorder 静默记录不到：走完整个发帖流程才发现 dump 是空的（2026-09-01）

> 与 CR-63 同一天、同一个人、同一条链路上的**第二个**问题。
> 这条更贵：CR-63 至少**报了错**（虽然报错内容是错的），
> 这一条**从头到尾一句提示都没有**——用户完整走完一遍 Business Suite 发帖流程，
> 结束后才发现 `interactions` 是空的。20 分钟的人工操作直接作废。

### 18.1 现象

同一晚两次运行，同一个工具、同一个浏览器：

| dump | 交互数 | 起始页 | 结果 |
|---|---:|---|---|
| `..._195237_...` | **63** | 已经停在 `www.facebook.com` | 正常，跨 5 个 URL 全程记录 |
| `..._203718_...` | **1** | `chrome://new-tab-page` | 只记下 NTP 上那一次点击，**之后全丢** |

用户描述：「**点进网址之后的所有操作全部丢失**」。

### 18.2 根因：Playwright 的 page 对象是坏的，而失败被整个吞掉

对用户那个仍然开着的标签页实测（只读）：

| 探测 | 结果 |
|---|---|
| `page.url` | `''` ← 真实是 `business.facebook.com/latest/content_calendar/...` |
| `page.frames` | 1 个**空**帧 |
| `page.evaluate("1+1")` | **TimeoutError** |
| `expose_binding` 之后 `typeof window[binding]` | **`undefined`** |
| `add_init_script` | 不生效（SPA 客户端路由，**不产生新文档**） |
| **同一 target 的原始 CDP** `Runtime.evaluate("location.href")` | ✅ 返回真实 URL |
| **同一 target 的原始 CDP** `Page.getFrameTree` | ✅ 返回真实主帧 |
| **同一 target 的原始 CDP** 注入监听脚本 | ✅ `handlers = object` |

即：**`connect_over_cdp` 附着到"连接之前就已经打开"的页面时，
Playwright 的 page 对象可能永远拿不到帧树，而底层 CDP 完全正常。**

而旧代码是这么装监听器的：

```python
async def _install_existing(context, script):
    for page in list(context.pages):
        for frame in list(page.frames):
            try:
                await frame.evaluate(script)
            except Exception:
                continue          # ← 把上面那个 TimeoutError 整个吞掉
```

于是：**监听器一个都没装上 → 一条事件都没记 → 全程零提示。**
两次运行的差别只是"附着时那一页是不是刚打开的"，而这件事用户完全无从得知。

> **教训：`except Exception: continue` 用在"装设备"这一步上，
> 等于把"设备没装上"变成"设备装好了但什么都没发生"。
> 这两件事在输出上一模一样——和 §7 死人开关要区分的
> 「没跑」vs「跑了没事做」是同一类失效。**

### 18.3 实施中发现的第二个 bug：CDP 通路不 enable 就跨不过导航

改走 CDP 之后，scratch Chrome 上实测：首个文档记录正常，**一导航就再也收不到
`Runtime.bindingCalled`**——症状和原 bug 一模一样。
原因是 `Runtime.addBinding` 只会装进**已被跟踪的**执行上下文，
而不 `Runtime.enable` / `Page.enable` 时，新文档那个上下文根本不被跟踪。
**如果没有跨导航的回归测试，这个修复会带着同一个症状上线。**

### 18.4 处置

1. **安装改走 CDP**，`install_on_page()` 一次做四件事并**回读校验**：
   `Runtime.enable` → `Page.enable` → `Runtime.addBinding` →
   `Page.addScriptToEvaluateOnNewDocument`（覆盖后续文档/frame）→
   `Runtime.evaluate`（覆盖**当前**文档，SPA 路由不产生新文档，只靠前者会漏）→
   回读 `typeof window[registry]`，**不是 `"object"` 就算失败**。
2. **通路统一成 CDP binding**，页面侧改发 `JSON.stringify(payload)`
   （`Runtime.addBinding` 只接受 string 参数），Python 侧统一解析。
3. **装不上就大声说**：逐页打印挂载结果；一个都没挂上时直接告诉用户
   "现在开始走流程会全部记录不到，先按 F5 刷新那一页"。
4. **逐条回显**：每记录一条打一行 `#N click`。**屏幕不再跳数字 = 当场就知道没记上**，
   而不是走完 20 分钟才发现。
5. **收尾时空 dump 显式告警**：明说这份不能用来回填 `selectors.py`。
6. **定期巡检**（3 秒）：补装新开的页面，并用 `/json/list` 核对
   Playwright 有没有漏页——漏了就等于那一页全程不记录。
7. **截图**：给 Playwright 截图加 8 秒显式超时（`record()` 是**持锁**的，
   一次挂住会把后面所有事件堵死），失败时用 CDP `Page.captureScreenshot` 兜底。
   ⚠️ **回退路径同样遮罩敏感输入**：`mask=` 用不了，改成临时插一条
   CSS 把密码/邮箱/OTP 类输入模糊掉，截完撤掉；**插不进去就不截**——
   宁可没有截图，也不能把凭据拍进去。

### 18.5 验证

**真实浏览器**（scratch Chrome，真·可信点击，非 mock）：

- 首个文档点击 → 记录；**密码框点击 + 输入 → 不记录**（隐私闸仍然成立）；
- **导航一次 → 记录；再导航一次 → 记录**（三条分属三个不同 URL）；
- 截图全部生成、`screenshot_error` 全为 `None`。

**用户那个原本静默失败的页面**（只读复核）：
`install_on_page → True | ok`，页面内回读
`handlers=object | binding=function | https://business.facebook.com/latest/...`。
**同一个页面，旧代码装不上且不吭声，新代码装上了。**

新增 20 条断言（`tests_publish.py` 92 → 112），含 enable 顺序、
回读校验失败必须返回失败、坏 JSON 不炸 binding、
`session_id` 对不上仍拒收、CDP 截图遮罩插不进去就不截。
**19 套 / 1219 项全绿。**

### 18.6 给下一个人的一句话

**这一轮两个 bug（CR-63 的超时、CR-64 的静默）都不是审查能看出来的，
都是真机跑出来的。** 它们的共同点是：**离线测试把出问题的那一层整个 mock 掉了**。
`_cdp_profile_matches` 被 mock、`page.evaluate` 被 FakePage 替掉——
于是 17 套断言全绿，而真实环境里那两条路径从来没成功过。
**mock 掉的边界，就是没有被测到的边界。**
