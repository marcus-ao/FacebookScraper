# 审校台

React + TypeScript 界面位于 `web/ui/`，通过 FastAPI 读取归档并保存审校结果。
[接口契约](DESIGN.md) · [界面规范](ui/DESIGN.md) · [实现取舍](ui/DECISION_LOG.md) · [业务规则](../docs/FUNCTIONALITY.md) · [验收状态](../docs/REQUIREMENTS.md#10-五阶段验收状态)

## 构建与启动

受管服务机通过部署安装参数配置局域网入口，页面/API/图片/下载共用持久的 `public_base_url`。默认回环模式；首次办公网部署、防火墙和业务电脑验收见 [MANUAL_STEPS 第 17 节](../docs/MANUAL_STEPS.md#17-办公局域网接入)。下面命令用于独立开发，Vite 保留浏览器 Host 以符合后端同源检查。

在仓库根目录运行：

```powershell
scripts\run_web.bat
```

源码启动需要 Node.js/npm；脚本每次按锁文件安装依赖并构建前端，成功后才启动 Web，失败会保留 npm 错误并退出。这样 `git pull` 后重新运行脚本就会提供当前源码的页面。直接调用 Uvicorn 时，仍需自行先执行 `npm.cmd --prefix web/ui ci` 与 `npm.cmd --prefix web/ui run build`。

日常入口为 `http://127.0.0.1:8765`。FastAPI 读取 `config.toml` 的 `paths.web_dist = "web/ui/dist"`；本机数据、解释器和环境文件绑定见 [配置示例](../config.local.example.toml)。构建产物和依赖不入库。带 `release.json` 的运行包使用包内已验证的前端，跳过源码构建，不需要 Node；受管实例仍通过部署控制器启动和更新。

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
| `/settings` | 运营设置 |
| `/runtime` | 运行状态 |

`/?task=...` 和 `/?view=...` 链接继续可用。详情直接访问、刷新和返回列表须保留来源与筛选上下文。

## 验证

| 根目录命令 | 范围 |
|---|---|
| `npm.cmd --prefix web/ui test` | 逻辑、组件 SSR、接口形状与设计约束 |
| `npm.cmd --prefix web/ui run typecheck` | TypeScript 类型 |
| `scripts\run_python.bat tests/tests_browser_workflow.py -v` | 临时后端的编辑、冲突、历史、设置、链接和日期交互 |
| `scripts\run_python.bat tests/browser_regression.py --stage ALL` | 综合浏览器行为 |
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
