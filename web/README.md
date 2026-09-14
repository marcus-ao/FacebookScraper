# 审校台

审校台读取本地归档，并把人工文案、本地化选择、分类标签和审校决定写入追加式真相源。当前只有一套前端：`web/ui/` 下的 React + TypeScript 应用。接口契约以 [DESIGN.md](DESIGN.md) 为准，界面设计和已定取舍分别见 [ui/DESIGN.md](ui/DESIGN.md) 与 [ui/DECISION_LOG.md](ui/DECISION_LOG.md)，业务规则以 [../docs/FUNCTIONALITY.md](../docs/FUNCTIONALITY.md) 为准。

## 当前能力与限制

| 能力 | 状态 | 说明 |
|---|---|---|
| 当前任务列表与详情 | 离线通过 | 从 archive/账本读取；无数据时为空 |
| 人工文案、正文/标签/链接编辑 | 离线通过 | 写 `translated_human.jsonl` 与 `localization.jsonl`，带版本检查 |
| 七态审校 | 离线通过 | 挂起、不发、人工接管等写 `review_items.jsonl`，`actor=null` |
| ZIP 资源下载 | 离线通过 | 完整生成后才记录 `handed_off` |
| 本次 Planner 月历只读 | 真实通过 | 2026-09-13T05:01:33Z ready，35 格、4 个公开帖、2 个推荐时段已区分，含 3 个 IG remote ID；不代表新排期回读 |
| 审校通过并排期 | 代码未完成 | 快照、单渠道、全文、身份及时刻回读有离线证据；远端 scheduled 图片读取仍缺 |
| 历史列表与详情 | 真实通过 | 原 1,067 条与 SQLite 一致；第 1/2 页各 20 条且无重复，冻结账号只读 |
| 风险预扫描与 hashtag 采样 | 离线通过 | 真实模型和三类来源仍须分别联调；IG 与 Trends 当前因 429 停止 |
| 模型任务与批次恢复 | 离线通过 | 恢复要求版本与付费账本核对，不会隐含再次调用模型 |
| 设置与运行状态 | 离线通过 | 设置保存使用 CAS；状态读取不触发平台、模型或消息调用 |

历史证据必须按版本理解。2026-09-12/13 的 65 个 Python 脚本记录、Vue 50 modules 构建和七个浏览器场景来自旧版本，详见 [集成记录](../docs/INTEGRATION_2026-09-12.md)。React 迁移曾有 496 项组件/逻辑测试记录，它同样早于本次单目录整理。

2026-09-14 的当前单目录版本已经完成锁定安装、26 个文件共 505 项测试及 TypeScript + Vite 构建；证据位于 `state/ui-consolidation-20260914T063209Z`。日期格式修改先让 9 个相关用例失败，随后 46 个动作与禁用原因用例通过；锁文件中的 162 个依赖条目未升级，只调整根包标识。构建仍有 1.39 MB JavaScript chunk 警告。

当前集成 worktree 的 `state/offline-validation-20260914T065258Z` 记录完整 66 个 Python 脚本全部通过，其中 `state/offline-browser-20260914T065304Z-21788` 记录八个浏览器工作流场景全部通过、页面错误和拒绝请求均为零。`state/ui-regression` 还记录 12 组综合浏览器检查、16 个显式网络契约和实际 FastAPI 静态演练通过；静态演练没有外部写入。`state/ui-consolidation-20260914T063209Z/review-probe.json` 的七项界面探测也通过：正文页签没有提前请求图片，图片页签请求两张，切换后保留已挂载的本地化内容，并且 approve、月历刷新、付费模型与飞书调用均为零，页面错误为空。上述浏览器结果使用临时 archive/state 或明确的界面响应替代，不能升级为真实模型、飞书或 Business Suite 验收。合并到原 `main` 后的日常服务启动和只读检查仍要按下文执行。

真实边界没有因前端整理改变：原 1,067 篇是 Facebook 47 篇加冻结 `.tech` 1,020 篇，当前 `.global` 仍为零且未首次回填；本轮没有真实模型调用、付费供应商核账、飞书发送或新的 Facebook/Instagram 发布/排期提交；远端 scheduled 图片数量和顺序仍无法读回，`acceptance.verified` 仍为 false；单渠道验收和运营从飞书到排期的流程仍未完成。

## 目录与路由

`web/ui/` 是唯一前端源码目录，构建输出固定为 `web/ui/dist/`。`config.toml` 的 `[paths]` 必须显式包含：

```toml
[paths]
archive = "archive"
state = "state"
web_dist = "web/ui/dist"
```

`config.local.toml` 只用于本机 archive/state 接续，不选择前端目录。FastAPI 在进程启动时解析 `web_dist`，所以修改构建路径后必须重启 Web 进程。

浏览器路由如下：

| 路径 | 功能 |
|---|---|
| `/review` | 审校队列 |
| `/review/<account>/<post_id>` | 当前任务详情 |
| `/history` | 历史归档 |
| `/history/<account>/<post_id>` | 历史来源详情；写入口由 `detail.read_only` 决定，冻结账号为只读 |
| `/calendar` | 发布月历 |
| `/settings` | 运营设置 |
| `/runtime` | 运行状态 |

旧查询链接 `/?task=...` 与 `/?view=history|calendar|settings|runtime` 由前端重定向到对应路径。`SinglePageFiles` 必须让直接打开或刷新上述路由仍返回应用，同时保持三条边界：`/api/...` 的 404 仍是 JSON，不存在的静态资源返回 404，不接受 HTML 的请求不做应用回落。

## 安装、构建与开发

在仓库根目录安装锁定依赖、运行单元测试并构建：

```powershell
npm.cmd --prefix web/ui ci
npm.cmd --prefix web/ui test
npm.cmd --prefix web/ui run build
```

开发时先启动 FastAPI，再启动 Vite：

```powershell
scripts\run_python.bat -m uvicorn web.api.app:app --host 127.0.0.1 --port 8765
npm.cmd --prefix web/ui run dev
```

Vite 固定使用 `http://127.0.0.1:5174`，并把 `/api` 代理到 `http://127.0.0.1:8765`。构建后由 FastAPI 同源提供静态文件，不经过 Vite proxy。部署静态构建的机器不需要 Node。

本次开发合并回原 `main` 后，日常服务应在原主工作区运行：

```powershell
scripts\run_web.bat
```

该入口监听 `127.0.0.1:8765`，并沿用原主工作区的 archive/state 绑定。不要从临时开发 worktree 长期提供日常服务，也不要另建空 state。

## 当前验证入口

以下命令都从仓库根目录运行。它们使用隔离数据或只读探测；运行后仍要按各自报告区分真实后端行为、界面替代响应和外部系统事实。

```powershell
npm.cmd --prefix web/ui ci
npm.cmd --prefix web/ui test
npm.cmd --prefix web/ui run build
scripts\run_python.bat tests/tests_browser_workflow.py -v
scripts\run_python.bat tests/browser_regression.py --stage ALL
scripts\run_python.bat tests/network_compare.py
scripts\run_python.bat tests/cutover_rehearsal.py
scripts\run_python.bat tests/review_probe.py
scripts\run_python.bat tests/history_thumbnail_cost.py
```

各入口的当前职责：

| 入口 | 覆盖范围 |
|---|---|
| `tests/tests_browser_workflow.py` | 迁移后的七个临时真实后端场景，外加日期控件回归；保存、来源冲突、历史、设置和链接计数使用临时 archive/state |
| `tests/browser_regression.py --stage ALL` | React 路由、列表、详情、月历、设置、运行状态、可访问性与视觉行为的综合浏览器检查 |
| `tests/network_compare.py` | 当前 React 的 16 个显式网络契约；核对方法、路径和请求体，不再比较另一套前端 |
| `tests/cutover_rehearsal.py` | 默认读取 `web/ui/dist`，由实际 FastAPI 提供深链接、旧链接、API 与静态资源 404 行为 |
| `tests/review_probe.py` | 1366×768 与 1920×1080 的界面结构、密度和交互探测 |
| `tests/history_thumbnail_cost.py` | React 历史页文字与缩略图请求成本；只生成测量结果，不代表远端业务验收 |

运行恢复、消息未知、模拟排期与公开状态使用明确的浏览器响应替代，只能证明界面行为。没有任何离线入口可以替代真实模型账单、飞书权限、Business Suite 单渠道提交或远端媒体回读。

## 历史缩略图成本

历史列表先显示文字行，再按行加载缩略图。路径校验会遍历账号目录，所以成本随归档规模增长。`3718c0d` 记录的 2026-09-14 测量如下：

| 数据范围 | 单张缩略图 | 50 张估算 |
|---|---|---|
| 隔离夹具（63 篇） | 0.03–0.11 s | 约 3.9 s |
| 原归档（1,067 篇） | 中位 1.57 s，最大 2.0 s | 约 12–13 s（每源 6 并发） |

原归档结果是在实际服务上直接 fetch 8 张得到的。文字、状态和分页不等待图片，因此页面可先操作，图片随后补齐。若实际使用太慢，先把每页数量改为 20；长期修复点是缓存 `core/store.assert_physical_direct_path` 的目录解析。

测量时必须让浏览器窗口处于前台，或直接 fetch 图片 URL。图片使用 `loading="lazy"`；实测后台标签页在 `document.visibilityState === "hidden"` 时 38 秒没有发出图片请求，这种结果不能解释成服务卡住。

## 使用规则

- Facebook 与 Instagram 任务独立，来源渠道就是唯一发布渠道。
- 保存使用来源哈希与 revision 乐观并发控制；409 表示页面已经过期，应载入最新内容并保留草稿后重新核对。
- 人工文案和图片优先，后台重建不得覆盖。
- 挂起默认三个上海工作日；“不发”必须有理由；“人工接管”与“不发”是不同终态。
- `scheduled` 只表示 Business Suite 已回读确认排期，不表示帖子已经公开。
- `stale=false` 只说明来源原文未变化，还要核对 `machine_current` 与提示词版本。
- Facebook 的 `{{linkN}}` 只作为保存和渲染契约；界面显示可读的链接标记，最终计数以服务器替换真实 URL 后的结果为准。Instagram 使用 bio 话术。
- 同一渠道前后 90 分钟有占用时拒绝人工所选时刻并给出建议，不自动顺延。
- 账本损坏必须显式失败并保留现场，不能删除坏行换取页面继续运行。
- 任何真实提交前仍需展示最终文案、图片、账号、唯一渠道和柏林时刻并取得具体确认。
