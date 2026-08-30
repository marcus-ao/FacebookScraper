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

### 本轮之后的测试基线

**10 套 400 项，全绿**（本轮开始时是 9 套 282 项）。
新增 `tests_delta.py`（55）、`tests_parse.py` 的真实结构断言段、
`tests_store.py` 的布局与 reindex 段、`tests_backfill.py` 的归属拦截与进度显示段。

⚠️ 新增了一条以前没有的验证条件：**全套测试在 stdout 被管道重定向的情况下也必须全绿**。
本机代码页是 936，修复前 `tests_translate.py` 在这个条件下必崩
（`UnicodeEncodeError: 'gbk' codec can't encode character '\xdf'`）。
