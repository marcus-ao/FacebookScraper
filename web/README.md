# 审校台

React + TypeScript 界面位于 `web/ui/`，通过 FastAPI 读取归档并保存审校结果。
[接口契约](DESIGN.md) · [界面规范](ui/DESIGN.md) · [实现取舍](ui/DECISION_LOG.md) · [业务规则](../docs/FUNCTIONALITY.md) · [验收状态](../docs/REQUIREMENTS.md#10-五阶段验收状态)

## 构建与启动

源码服务机使用 `scripts\run_web_lan.bat`，监听与入口读取 [网络 JSON](../ops/service-machine.network.json)，用 `scripts\update_service_address.bat` 同步改址，见 [MANUAL_STEPS §13.1](../docs/MANUAL_STEPS.md#131-源码服务机一键改址)。页面、API、图片和下载使用相对路径，共用浏览器当前入口。下面命令用于独立开发，Vite 保留浏览器 Host 以符合后端同源检查。

在仓库根目录运行：

```powershell
scripts\run_web.bat
```

源码启动需要 Node.js/npm；脚本每次按锁文件安装依赖并构建前端，成功后才启动 Web，失败会保留 npm 错误并退出。这样 `git pull` 后重新运行脚本就会提供当前源码的页面。直接调用 Uvicorn 时，仍需自行先执行 `npm.cmd --prefix web/ui ci` 与 `npm.cmd --prefix web/ui run build`。

日常入口为 `http://127.0.0.1:8765`。FastAPI 读取 `config.toml` 的 `paths.web_dist = "web/ui/dist"`；本机数据、解释器和环境文件绑定见 [配置示例](../config.local.example.toml)。构建产物和依赖不入库。

源码部署不依赖 Actions 或云端制品。旧受管实例读取 `control/host.json`，带 `release.json` 的历史运行包仍使用包内前端、不需要 Node；自动制品交付明确延期，旧实例维护见 [第 15 节](../docs/MANUAL_STEPS.md#15-服务机部署与日常更新)。

开发时保持 FastAPI 运行，另执行：

```powershell
npm.cmd --prefix web/ui run dev
```

开发入口为 `http://127.0.0.1:5174`，`/api` 代理到 8765。

## 路由

| 路径 | 功能 |
|---|---|
| `/review` | 审校队列 |
| `/review/<account>/<post_id>` | 任务详情 |
| `/history` | 历史归档 |
| `/history/<account>/<post_id>` | 历史详情，冻结账号只读 |
| `/calendar` | 发布月历 |
| `/runtime` | 运行状态 |

`/?task=...` 和 `/?view=...` 链接继续可用。详情直接访问、刷新和返回列表须保留来源与筛选上下文。

## 验证

按 [AGENTS 第四节](../AGENTS.md#四按影响选测试默认不跑全量) 选范围：前端改动运行相关测试与构建，浏览器行为选对应场景；下表是可选入口，不是每次改动都要完成的清单。

| 根目录命令 | 范围 |
|---|---|
| `npm.cmd --prefix web/ui test -- src/app/deployment-store.test.ts` | 定向前端测试示例，按改动选择文件 |
| `npm.cmd --prefix web/ui run build` | 类型检查与生产构建 |
| `npm.cmd --prefix web/ui test` | 逻辑、组件 SSR、接口形状与设计约束 |
| `npm.cmd --prefix web/ui run typecheck` | TypeScript 类型 |
| `scripts\run_python.bat tests/tests_browser_workflow.py -v` | 临时后端的编辑、冲突、历史、设置、链接和日期交互 |
| `scripts\run_python.bat tests/browser_regression.py --stage REVIEW_MENU` | 定向浏览器示例，运行前先构建 |
| `scripts\run_python.bat tests/browser_regression.py --stage ALL` | 综合浏览器行为，影响范围需要时执行 |
| `scripts\run_python.bat tests/network_compare.py` | React 请求方法、路径及请求体 |
| `scripts\run_python.bat tests/cutover_rehearsal.py` | 实际 FastAPI 的深链接、资源和 API 404 |
| `scripts\run_python.bat tests/review_probe.py` | 界面密度、交互和可访问性 |
| `scripts\run_python.bat tests/history_thumbnail_cost.py` | 历史缩略图请求成本 |

写入测试使用隔离数据；模拟响应与真实企业服务验收分别记录，详见 [证据边界](../docs/HANDOFF.md)。

## 故障定位

- 页面未更新：核对 8765 进程的工作目录、`web_dist` 和 HTML 引用的构建资源；构建路径改变后重启对应 Web 进程。
- 详情刷新失败：核对 SPA 回落；未知 API、缺失资源及非 HTML 请求仍应返回 404。
- 图片晚于文字：先降到每页 20 条；测量懒加载时保持标签页可见。
- 保存冲突：通过页面恢复入口载入最新来源并保留草稿；不要删除账本或重建空 state。
