# 审校台前端重构 · 审计与架构提案

**成文日期：2026-09-13。代码基线 `3536e29`（核心集成 `565f17c`）。**

**状态：Stage A–J 已实施，保留 Vue 与 React 两份构建，生产挂载未切换。** 本次授权按 C → E′ → D1–D5 → F → G → H → I → J 连续执行；实施与验收结果见 [FINAL_IMPLEMENTATION_REPORT.md](FINAL_IMPLEMENTATION_REPORT.md)，执行清单见 [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md)。

最终证据：[浏览器 B1–B24](browser-stage-all.json)、[16 条新旧请求对照](network-comparison.json)、[20 张截图与尺寸](final-measurements.json)、[文件变化清单](final-file-manifest.json)、[原始文件完整性](integrity-check.json)。阶段报告 C、D、E、F、G、H、I、J 分别说明交付与相关验证。

## 按顺序读

| # | 文件 | 回答什么 |
|---|---|---|
| 0 | [DECISION_LOG.md](DECISION_LOG.md) | **产品决策的真相源**：13 条决议、4 个架构修正、9 个延期项、Stage A 的执行边界。与其它文件冲突时它赢 |
| 1 | [BASELINE_BEHAVIOR.md](BASELINE_BEHAVIOR.md) | 现在按下去会发生什么。重构后必须一字不差还在的行为，末尾有 20 + 4 条回归清单 |
| 2 | [SCREEN_INVENTORY.md](SCREEN_INVENTORY.md) | 六个界面各自的任务、控件、状态、量出来的尺寸、截图索引 |
| 3 | [UI_AUDIT.md](UI_AUDIT.md) | 8 条 P0、13 条 P1、11 条 P2、6 条 P3，每条带现状值与处理方向；含二十问逐屏答案、列表与详情专项 |
| 4 | [UI_ARCHITECTURE_PROPOSAL.md](UI_ARCHITECTURE_PROPOSAL.md) | 目标信息架构：外壳、导航、列表、详情、渐进披露、动作层级、Ant Design 映射、密度与窄屏策略 |
| 5 | [REACT_MIGRATION_PLAN.md](REACT_MIGRATION_PLAN.md) | Vue → React + TypeScript 的路径：目录结构、领域类型、TanStack Query 边界、迁移顺序、回归与回滚 |
| 6 | [DESIGN.md](DESIGN.md) | 未来设计系统草案（视觉与交互口径，**不是**接口契约） |
| 7 | [OPEN_QUESTIONS.md](OPEN_QUESTIONS.md) | 那 13 个问题的**提问依据**（已全部决议，口径见 DECISION_LOG） |
| 8 | [STAGE_A_REPORT.md](STAGE_A_REPORT.md) | Stage A 的执行结果：安装的包与版本、文件清单、测试结果、antd 6 三项核验、新旧 dist 切换方式 |
| 9 | [STAGE_B_REPORT.md](STAGE_B_REPORT.md) | Stage B1+B2 的执行结果：**最终主色 `#155EEF` 及其判据**、应用外壳、七个共享基元、267 条新增测试、两个分辨率的量值 |
| 10 | [POST_IMPLEMENTATION_REVIEW.md](POST_IMPLEMENTATION_REVIEW.md) | **实施后独立复核（2026-09-13，基线 `2c89f07`）**：另一位复核者自己量的密度与请求数、对 FINAL 报告 12 条 claim 的逐条验证（其中「浏览器 12/12 PASS」**本机复现不出来**）、5 处已修与 8 类明确不修、`tests_operating_settings` 波动的根因实测 |
| 11 | [PRE_CUTOVER_REPORT.md](PRE_CUTOVER_REPORT.md) | **切换前稳定化（2026-09-14，基线 `2c89f07`）**：在真实 FastAPI 上挂 React dist 的演练 —— **所有非 `/` 的路径原本 404**，刷新详情页就是一行 `{"detail":"Not Found"}`；这一条的修法、回滚路径验证、9 项 A–I 修复、配置缓存失效的确定性回归测试，以及唯一没解决的历史缩略图成本。§18 是 Release Candidate 冻结，最终结论 **CUTOVER_READY** |
| 12 | [CUTOVER_CHECKLIST.md](CUTOVER_CHECKLIST.md) | **上线当天从头勾到尾的那一张**：切换前、切换、只读 smoke、低风险写入、受监督的真实 approve、回滚触发条件与回滚步骤 |
| 13 | [RELEASE_CANDIDATE_MANIFEST.md](RELEASE_CANDIDATE_MANIFEST.md) | 建 release commit 时哪些文件是长期资产、哪些只是这几轮的取证材料 |

## 不要搞混的两个 DESIGN.md

| 文件 | 管什么 | 冲突时 |
|---|---|---|
| [`web/DESIGN.md`](../../web/DESIGN.md) | **接口与业务契约的真相源**：状态模型、字段、验收 | 它赢 |
| [`docs/ui-refactor/DESIGN.md`](DESIGN.md) | 视觉与交互口径草案 | 服从上面那一份 |

## 取证材料

```
screenshots/
  <screen>@1920x1080.png / <screen>@1366x768.png     九个界面 × 两个分辨率
  review-detail-editing@1366x768.png                 编辑态
  decision-dialog@1366x768.png                       "这篇不发"模态框
  measurements.json                                  每屏 11 项尺寸与按钮层级计数
tools/
  audit_fixture_host.py                              隔离夹具宿主（18 个多样样本，可人工观察）
  capture_baseline.py                                两个分辨率批量截图 + 量尺寸
```

两个脚本都跑在 `tests/browser_fixture.py` 的隔离数据上：临时 archive/state、`socket.connect` 限回环、除 `PUT /api/settings`／`PUT …/localization`／`POST …/check` 之外的非 GET 一律 503、退出时校验真实 `config.toml` 未变。**真实 `archive/` 与 `state/` 一个字节都不碰。**

复现：

```bash
scripts\run_python.bat docs/ui-refactor/tools/capture_baseline.py
```

人工观察（起宿主后保持运行，自己开浏览器看）：

```bash
scripts\run_python.bat docs/ui-refactor/tools/audit_fixture_host.py
```

## 真实数据侧的只读取证

另外一次取证直接连**真实归档**（`config.local.toml` 绑定的原 archive/state），只做 GET，响应存在 `state/audit-probe/`（`state/` 已 gitignore）：

| 端点 | 结果 |
|---|---|
| `GET /api/tasks` | 26 篇：24 `not_ready`、1 `snoozed`、1 `skipped`、`pending_review` **0**、硬闸 0 |
| `GET /api/tasks?scope=history&limit=30` | 1,067 篇、36 页、71 个月份 |
| `GET /api/tasks/{id}` ×2 | 一篇活账号 FB、一篇冻结 IG（`read_only: true`） |
| `GET /api/calendar` | `status: stale`、4 张卡、覆盖 8/30–10/3 |
| `GET /api/settings` | `editable` 两个字段 + 七组受控配置 |
| `GET /api/runtime` | 五阶段：blocked / available / not_observed / disabled / available |

这一次没有任何 POST / PUT。

## 最要紧的五件事

如果只看五行：

1. **审校列表扫不动**：1366×768 首屏 **4 条**，真实数据下 24 行文字完全相同。
2. **红色被用在常态上**：一屏 17 个红色"这篇不发"按钮、0 个主按钮，红色不再是信号。
3. **筛选每次进出详情都被清空**（已实测），而且**没有上一篇/下一篇**——她的核心循环每一圈都在重做工作。
4. **详情首屏没有主动作**：`通过并创建排期` 在第 1,524 像素，首屏唯一有颜色的按钮是红色的"这篇不发"。
5. **实现细节外泄给业务用户**：运营设置页原样显示 `config.toml` 的开发者注释，内含三个源码文件路径。
