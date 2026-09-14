# 业务流水线第一批实施计划

本文件保存 2026-09-12 第一批实施时的范围、技术栈和结果，其中 Vue 仅描述当时版本。当前唯一前端与验收状态见 [五阶段实施清单](../../OPTIMIAZATION.md) 和 [审校台说明](../../../web/README.md)。

> **For agentic workers:** 使用 superpowers:subagent-driven-development 分工实现；按下列可验证边界集成。

**Goal:** 让运营在审校台保存的人工文案真正留档、被下游采用，并补上扩大自动化之前的内容保护。

**Architecture:** 保留现有 Python 单向模块依赖、文件真相源和 Vue 审校台布局。人工译文独立追加记录，机器重跑不覆盖；Web 只调用共享契约。发布沿用现有锁和证据闸。

**Tech Stack:** Python、FastAPI、Vue 3、现有离线测试脚本；本批不增加运行时依赖。

**Spec:** `docs/FUNCTIONALITY.md` §0.2、§2、§3、§5、§6；`docs/CONTEXT.md`；`docs/HANDOFF.md`。

## 全局约束

- 需求分支保留在原目录，开发位于 `.worktrees/business-workflow`，分支 `codex/business-workflow`，基于 `0eeb099`。
- 不改写另一位 Agent 负责的需求文档；以 FUNCTIONALITY 中的新决定覆盖旧 REQUIREMENTS。
- 本轮单人、中文桌面界面；行为记录 `actor: null`。
- 本地文件是真相；人工版本优先；源帖变更保留人工版本，要求重新审校。
- 不编造浏览器选择器。全德语图默认硬闸；原有金额、授权、预算和发布锁保留。
- 本批离线验证，不安装常驻计划任务，不调用付费模型，不提交真实排期或发送飞书消息。

## 已核实的结构与实施顺序

| 阶段 | 现有实现与本批边界 | 下一层依赖 |
|---|---|---|
| 监测 | `routes/backfill.py` / `delta.py` 复用 CDP 与解析器；本批保留当前频率 | 探测 profile 分离、`.global` 自动处理范围、分层调度 |
| 抓取与留档 | `post.json` 为源帖真相，manifest 为索引；本批新增人工译文账本 | 月份布局迁移、分类、SQLite、云盘镜像 |
| 本地化 | `translate.py` 与图片模块已具备付费账本；本批接通人工正文持久化 | 平台独立路由后实现标签、链接与模型优化指令 |
| 提醒 | 当前仅本机提醒；本批不新增外部发送 | 企业应用凭据、收件人、审校链接与在岗窗 |
| 审校与排期 | Web 当前假写；本批正文真写、缺图硬闸、回读诊断、时钟修复 | 渠道选择真实探查、真实 Web 批准、Planner 月历 |

代码核对纠正两处旧描述：发布锁已经存在于 `publish/journal.py`，不新建第二套；媒体 `local_path` 相对账号归档目录，迁移时应沿用代码契约。

## 任务 1：人工文案成为可恢复的真相源

**Files:** `core/translated.py`、`tools/review_report.py`、`tests/tests_human_translation.py`。

**Interfaces:**

```python
load_human_translated(path: Path) -> dict[str, dict]
append_human_translation(path: Path, source: dict, text_de: str, *, now=None) -> dict
effective_translation(source: dict, machine: dict | None, human: dict | None) -> dict | None
```

- [x] 先验证人工修改被机器重跑/报告覆盖的失败用例，再实现追加存储。
- [x] 人工记录包含源哈希、时刻、版本标识和 `actor: null`；统一选择结果提供 `is_human` 与 `stale`。
- [x] 源变更只标记待复核。旧的手改 `text_de.txt` 不能被重建吞掉；无法确认依据的旧内容按过期人工版本保留。
- [x] 报告与发布正文读取同一条优先级；图片仍按其生成依据校验，文案保存不自动触发图片付费。

## 任务 2：真实保存接口及界面交互接入

**Files:** `web/api/writer.py`、`reader.py`、`app.py`、Vue 文案与详情组件、`tests/tests_web_review.py`。

- [x] 临时归档的 API 测试先覆盖保存、刷新后读取、机器更新、源版本冲突与无效路径。
- [x] 保存前从已校验的 `post.json` 获取源帖，核对页面携带的源哈希和人工版本；冲突返回 409，保留用户草稿。
- [x] GET 不再叠加假状态。已有文案编辑界面显示实际人工版本与记录。
- [x] 未接真实发布的按钮明确禁用，接口不伪造已排期；保留既有布局。

## 任务 3：发布前内容保护及诊断

**Files:** `publish/compose.py`、`business_suite.py`、`journal.py`、`workflow.py`、`config.toml`、发布离线测试。

- [x] 缺少任一德语图，默认在 compose 阶段报错并给出对应图片命令；显式配置 false 才允许旧回退行为。
- [x] 回读诊断区分时间解析失败、时间不匹配、正文不匹配，记录有界卡片文本样本；保留完整正文判据。
- [x] 不将“增加诊断”当成长文案真机问题已解决；真实根因需下一次实际回读证据。

## 任务 4：流水线消费与整体验证

**Files:** `pipeline/engine.py`、`tests/tests_pipeline_human.py`、`tests/tests_pipeline_e2e.py`。

- [x] 人工译文存在时不重复花钱翻译；过期人工版本在付费前进入人工队列，保存复核后能重新对账。
- [x] 三处 `compose_post` 透传注入的 `now`；端到端测试自行创建 probe 夹具，避免依赖用户机器 state。
- [x] 使用现有 `.venv` 执行所有 `tests/tests_*.py`；新增用例参与相同自动发现。
- [x] 构建 Vue；浏览器验证真实保存、刷新与错误保留草稿；审查 diff 后报告已落地能力与尚需真机证据的边界。

运行离线脚本的解释器为原目录 `.venv/Scripts/python.exe`，工作目录必须为开发 worktree。测试输出写入 worktree 的忽略目录 `state/`，不影响真实业务归档。


## 本批验收与继续开发的入口

2026-09-12 在开发 worktree 完成验收：25 个 `tests/tests_*.py` 文件全部通过；Vue 构建通过；本批 Python 文件 Ruff 与 `git diff --check` 通过。真实 Chrome 使用临时归档验证了保存、刷新、源版本冲突保留草稿和重新复核保存，没有页面脚本错误。所有验证均未触发社媒发布、付费模型或飞书发送。

交叉审查补齐了三条跨模块边界：

- `core.store.read_post_truth` 统一读取实际源帖，报告与人工稿处理不再被旧 manifest 误导；实际补图 CLI 也使用同一依据。
- 未归档的旧文本与已有人工新稿冲突时，保留旧文件并明确提示，不把它自动追加为生效稿；首次导入与 Web 保存共用账本锁内版本检查。
- 图片按每张生成记录查找同一源文下的历史人工稿。普通文案编辑保留已生成图，缺图采用最新稿，显式重新生成采用最新稿；真正的源文变化要求重新复核。

下一批应先完成 `.global` 处理范围与探测身份隔离，以及独立渠道路由的真实浏览器证据。之后按 FUNCTIONALITY 的依赖顺序接入审校状态、真实批准排期、飞书提醒和存储派生层。本批尚未启用常驻调度；长文案实际回读与单渠道勾选仍待真机验证。
