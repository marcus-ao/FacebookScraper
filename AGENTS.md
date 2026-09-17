# 项目协作约定

面向在这个仓库里干活的 Agent。动手前先读 [docs/HANDOFF.md](docs/HANDOFF.md) 的第 1 节（当前现场）和第 2 节（红线）。

| 要查什么                               | 去哪                                           |
| -------------------------------------- | ---------------------------------------------- |
| 业务流程、功能编号、术语表             | [docs/FUNCTIONALITY.md](docs/FUNCTIONALITY.md) |
| 做到什么算够、每个验收单元现在什么状态 | [docs/REQUIREMENTS.md](docs/REQUIREMENTS.md)   |
| 红线、架构、证据能证明到哪             | [docs/HANDOFF.md](docs/HANDOFF.md)             |
| 要人亲自动手的步骤                     | [docs/MANUAL_STEPS.md](docs/MANUAL_STEPS.md)   |

## 一、不可逆的事要先问

这个项目的风险是**不对称**的。翻译错了重跑一次，抓取被封则整条链路断掉且不可恢复；发布出去只能删帖，而删帖在粉丝侧是可见的。

- **不自动登录、不自动滚历史。** 人在三个专用 Chrome profile（回填 9222 / 发布 9223 / 探测 9224）里登录，代码只附着。三个身份不可混用。
- **付费调用**必须同时满足统一预算与来源许可。计费状态不确定时先核账，不自动重放。
- **真实发布/排期**前先冻结并展示最终文案、图片、账号、唯一渠道和柏林时刻，等用户确认。`scheduled` 只表示远端回读确认，不等于已公开。
- **删除或覆盖**真相源（`published.jsonl`、`paid_requests.jsonl`、`review_items.jsonl`、`translated_human.jsonl`、归档原图）之前先问。它们不可重建，`published.jsonl` 丢了会导致重复发帖。
- 推送、删远端分支、改他人未提交的工作，都要先确认。接手前先看 `git status`。

## 二、简洁，但不要删掉护栏

`docs/` 之外的文件不是工作日志：注释、`config.toml`、`README.md`、`.bat` 只写读代码的人当场需要知道的东西。进度、证据、验收状态属于 `docs/` 的四份文件，写到别处必然过期且没人更新。

**边做边同步。** 每完成一个可验证的小步，就同步更新受影响的说明文档、配置项及示例；进度、证据和验收状态更新到上述文档的对应位置。实现、配置、文档一致，才算这一步完成，不能攒到整个大功能结束再补。

**该删**：实施过程叙事、变更日志（git 记得）、状态播报（"本轮已通过 N/M"）、指向已删文件或评审编号的引用、把一句话拉成一段的铺陈。

**该留**——这一条比上一条重要，因为删错了不会当场报错：

- **"不要退回 X" 型注释**。它们的存在是因为 X 试过并且失败了。例：`publish/business_suite.py` 写明不要把 `insert_text` 改回 `keyboard.type()`（正文里的 `@`/`#` 会唤起 typeahead 改写内容，而提交前没有任何提示）。
- **API 的反直觉约定**。例：`config.toml` 写明不得发送 `input_fidelity` —— openai SDK 的签名里**有**这个参数、docstring 还说它受支持，照签名写会让整组请求 400。
- **静默出错的陷阱**。例：TOML 子表必须放在段末，否则之后的键会悄悄归给子表；`legal_size()` 的 16 像素量化，不走它半数以上原图会 400。
- **实测数字**，因为它们是阈值的依据（818 张图标定出的形变线、2.35 倍放大的实例）。
- **例外的理由**。`requirements.txt` 的 Pillow 与 tzdata 违反"不加依赖"的约定，理由写在旁边；删掉理由就成了无解释的违规。

标记克制：`⛔` 只给不可逆后果，`⚠️` 给容易误判的陷阱，密度每 40 行不超过一个。两种标记混成一片，读的人两种都不会信。

## 三、证据的说法要准

状态词只有五个（判据见 [REQUIREMENTS 第 0 节](docs/REQUIREMENTS.md)）：`代码未完成`、`离线通过`、`待真实联调`、`真实通过`、`明确延期`。

- **`真实通过` 要求证据现在还能复核。** 证据被删之后，结论跟着降级——2026-09-14 清库时有六条就是这么退回去的。
- 离线测试通过 ≠ 真实服务验收。夹具、mock 回执、隔离浏览器场景都不能升级成模型账单、飞书权限或 Business Suite 提交的结论。
- 报告写清楚：跑了什么、用的是夹具还是真实账号、哪些外部依赖仍未联调。

## 四、按影响选测试，默认不跑全量

- **小改动，快验证。** 单点修复、简单功能只跑直接相关的测试；纯文案或文档改动检查内容、引用和 diff 即可。
- **复杂改动，按需扩大。** 跨模块改造、共享契约变更、主流程重构或影响范围不清时，按实际影响选择子系统或全量回归。跑全量前简述必要性，不把全量测试当作每次改动的固定流程。
- **验证通过就收尾。** 只有新增改动、测试失败或明确的覆盖缺口，才扩大或重复测试。

以下命令按需选用：

```powershell
scripts\run_python.bat tests\tests_parse.py      # 定向示例，按改动选择相关脚本
scripts\run_python.bat -m tools.test_offline      # 全量离线，确认必要时再运行
scripts\run_python.bat tests\tests_hygiene.py     # 配置键、零引用、重复实现、模块图
npm.cmd --prefix web/ui test                      # 前端单测
npm.cmd --prefix web/ui run build                 # 浏览器回归需要 dist
```

`tests_hygiene` 会当场拦住三类常犯错误：加了配置键却没人读、加了函数却没人调、同一段逻辑写在两个文件里。改动涉及写入的测试一律用隔离数据，不要对绑定真实数据的目录跑会写盘的脚本。

## 五、Windows 上的两个坑

- **`scripts/*.bat` 必须是 CRLF**，裸 LF 会让 cmd 误解析整行，表现为 "'xxx' 不是内部或外部命令"。`.gitattributes` 已规定，`tests_hygiene` 检查 [5] 会验。
- **`*.py` / `*.md` / `*.toml` 必须是 LF。** 用 Python 批量改文件时记得 `newline=""`——默认换行翻译会把读进来的 `\n` 写成 `\r\n`，把 LF 文件变成 CRLF。这个坑刚让 `config.toml` 变成 CRLF，再经夹具二次翻译成 `\r\r\n`，浏览器回归里报出一个完全看不出是换行问题的 TOML 解析错误。

## 六、代码放哪

```text
routes/     抓取：CDP 附着真实 Chrome，拦截响应
localize/   加工：text 走 DeepSeek 翻译，images 走 GPT-Image-2 图内德语化
publish/    发布：冻结快照、证据闸、Business Suite 提交与回读
pipeline/   编排：五阶段状态、激活边界、调度器、CLI
core/       契约与真相源：配置、归档、译文、付费账本
web/        审校台：FastAPI + React，只调上面的入口，反向不依赖
```

`archive/` 与 `state/` 是业务数据，不是构建产物，不随清理删除。派生物（manifest、SQLite、HTML、Planner 缓存、云盘镜像）可以重建，且不得反向覆盖真相源。

## 七、只做当前主线，达到验收就收尾

- 实现和测试以本轮已确认的设计与验收要求为边界，优先打通主流程、修复阻塞项。不阻塞主线的边角优化、泛化和额外抽象，确有价值的简记到现有文档待办，不持续扩展实现和测试。
- **安全工作同样受范围约束。** 已明确的红线照办；新增防护必须对应当前主线的具体问题或明确验收要求，不能为假想风险加功能、加测试、加审批，拖延开发和交付速度。

## 八、提交统一使用英文 Conventional Commits

- 提交标题和正文一律使用英文。标题格式为 `type(scope): summary`，`scope` 可省略；使用 `feat`、`fix`、`docs`、`refactor`、`test`、`chore` 等标准类型，摘要用简洁的英文祈使句。示例：`docs(agents): clarify testing and delivery rules`。
- 在本项目中创建 Git Commit 或 Pull Request 时，只使用我当前配置的 Git 用户身份，禁止添加任何 Claude、Anthropic 或 AI 相关署名，包括 Co-Authored-By、Claude-Session、Generated with Claude Code 等内容。提交和推送前请检查并移除所有此类 AI attribution，不要修改我的 user.name 和 user.email。

## 九、功能工作先建立独立工作树与分支

- 开始任一功能的计划拟定、开发实现或相关测试前，先检查 `git status`，为该功能创建独立 Git worktree 和对应分支，再在其中开展工作。不得在主检出或其他功能的工作树中混写改动。
- 同一功能的后续配置、修复和交付复用其既有工作树与分支；不同功能分别隔离，确保文件改动和提交可追踪。
- 分支和工作树名称必须概括具体功能或实现目的，例如 `codex/windows-lan-access`、`codex/publication-freeze`；禁止使用 `stage-1`、`phase-3`、`phrase-3` 等只有阶段编号的模糊命名。
- 清理工作树前按现有证据与数据规则保全其独有文件；创建隔离工作树不代表允许复制凭据、接入真实业务数据或删除其他工作。
