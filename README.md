# FB / IG 内容本地化流水线

本项目把美国站 Facebook / Instagram 的公开图文帖归档到本地，生成德语文案和德语图片，交给上海运营在中文审校台修改并选择柏林发布时间，最后通过 Meta Business Suite 创建单渠道排期。

业务规则以 [docs/FUNCTIONALITY.md](docs/FUNCTIONALITY.md) 为准；实施差距看 [docs/OPTIMIAZATION.md](docs/OPTIMIAZATION.md)；需要人亲自完成的步骤看 [docs/MANUAL_STEPS.md](docs/MANUAL_STEPS.md)。

## 当前基线（2026-09-12）

当前提交基线是 `c67c56a`。规划阶段曾通过 47 个离线测试脚本和一次前端构建；这说明离线契约在当时成立，不代表浏览器、模型、飞书、云盘或企业账号已经真实联调成功。

这个 worktree 目前没有 `archive/` 或 `state/` 运行数据。原工作区留有 Facebook 47 篇和旧目标 `in_neakasa.tech` 的 Instagram 1020 篇历史归档；它们是历史事实，不是新目标 `neakasa.global` 的当前基线，也不能直接复制后当成真实验收。

状态词统一为：

| 状态 | 含义 |
|---|---|
| **代码未完成** | 所需行为或失败闭合尚不存在 |
| **离线通过** | 有离线测试或构建证据，但没有真实外部系统证据 |
| **待真实联调** | 代码入口存在，仍需真实账号、浏览器、凭据或远端回执 |
| **真实通过** | 有注明日期和来源的真实证据 |
| **明确延期** | 业务已决定本轮不做，不应与缺陷混为一谈 |

已存在的离线能力包括响应拦截与回放、本地归档、德语翻译和图片本地化、付费账本、人工审校写入、七态审校流、Planner 缓存、调度器、飞书与云盘适配层、Business Suite 证据闸。它们仍需按 [docs/OPTIMIAZATION.md](docs/OPTIMIAZATION.md) 的清单完成缺口并逐项真实联调。

以下事项尚不能宣称完成：孤儿模型任务恢复、损坏审校账本的失败闭合、只展示最近 90 天的历史界面、跨月排期适配、当前调度器的安装流程、真实风险预扫描、真实标签采样、飞书当前内容/去重/日报、所有发布入口的不可变快照投影、单渠道选择与回读证据，以及企业飞书、云盘和 Business Suite 的端到端联调。

## 不会再变化的业务口径

- Facebook 与 Instagram 是两条独立车道。一篇来源帖只进入对应渠道，不跨平台合并，也不依赖 composer 默认双选。
- 当前目标是 Facebook `neakasaofficial` 与 Instagram `neakasa.global`。`.global` 时间线上的合作帖全部抓取，不按合作方白名单过滤。
- `in_neakasa.tech` 已冻结，只保留历史读取；不再抓取、加工或发布。
- 抓取、探测、发布分别使用三个 Chrome profile：回填 `9222`、发布 `9223`、探测 `9224`。只允许人工登录，不自动登录。
- 上海在岗窗是 08:00–19:00；运营选择柏林时刻；同一渠道前后 90 分钟内已有排期则拒绝，并给出可选时刻。
- 人工文案和人工图片优先，机器不得覆盖。付费调用必须同时满足统一预算和来源许可；请求是否已被远端计费不确定时，先核账再决定是否重放。
- 每张图最多受理三次优化。挂起默认三个上海工作日。本轮所有审校事件的 `actor` 固定为 `null`。
- 飞书云盘只接收本地单向镜像。内容更新时新增版本，不覆盖旧版；只允许用冻结内容补送缺失的旧版本。
- `scheduled` 只表示 Business Suite 排期已被回读确认，不表示帖子已经公开发布。
- 所有发布入口都必须冻结同一份最终文案、图片、渠道和时刻；任何可能提交的动作之前都要给用户看具体内容并获得确认。

## 安全地查看项目

首次安装：

```powershell
scripts\setup.bat
```

离线查看调度计划，不访问浏览器：

```powershell
.venv\Scripts\python.exe -m pipeline.scheduler --preview
```

启动审校台：

```powershell
.venv\Scripts\python.exe -m uvicorn web.api.app:app --host 127.0.0.1 --port 8765
```

若 `web/ui/dist/` 不存在，在开发机执行：

```powershell
cd web\ui
npm install
npm run build
```

生产运行不需要 Node；但生产迁移本轮明确延期。当前 worktree 没有运行数据，打开审校台只能验证服务和空状态，不能证明业务数据正确。

## 运行入口

| 目的 | 入口 | 说明 |
|---|---|---|
| 回填浏览器 | `scripts\start_chrome.bat` | 端口 9222，人工滚历史 |
| 探测浏览器 | `scripts\start_chrome_detect.bat` | 端口 9224，低深度增量探测 |
| 发布浏览器 | `scripts\start_chrome_publish.bat` | 端口 9223，Business Suite |
| 回填 | `scripts\run_backfill.bat <platform>` | 人工滚动，不自动滚到底 |
| 增量 | `scripts\run_delta.bat <platform>` | 仍受 C7 预算、单并发和失败预算约束 |
| 翻译/图片 | `scripts\run_translate.bat`、`scripts\run_images.bat` | 可能付费，先估算与确认 |
| 流水线 | `scripts\run_pipeline.bat` | 先运行 `preflight`，保留旧激活边界 |
| 调度器 | `scripts\run_scheduler.bat` | 默认预览；真实运行需显式参数 |
| 审校台 | `web.api.app:app` | 写入真实追加式真相源 |

`python -m tools.schedule install` 安装的是旧的每日任务组合，不是当前常驻调度器。当前调度器只能先生成 `scheduler-xml`，再由人在 Windows 任务计划程序中检查并导入；详见 [docs/MANUAL_STEPS.md](docs/MANUAL_STEPS.md)。

## 数据边界

```text
archive/<平台>_<账号>/
  posts/<日期>_<时分>_<post_id>/
    post.json                 来源事实
    text.txt                  派生原文
    text_de.txt               当前有效德语文案的派生副本
    01.jpg ...                原图，只读
    media_de/                 德语图，人工版优先
  manifest.jsonl              可从 posts/ 重建的派生索引
  translated.jsonl            机器译文真相源
  translated_human.jsonl      人工文案真相源
  localization.jsonl          本地化选择真相源
  review_items.jsonl          审校决定真相源

state/
  paid_requests.jsonl         付费请求真相源
  published.jsonl             发布尝试与回读真相源，不可重建
  planner_cache.json          可重建的 Planner 派生缓存
  index.sqlite                可重建的查询索引
```

`post.json`、人工文案、审校决定、付费账本和发布账本决定业务事实。`manifest.jsonl`、SQLite、HTML、Planner 缓存和云盘镜像都是派生物，冲突时不得反向覆盖本地真相源。

正式接续原工作区数据之前，先按 [docs/MANUAL_STEPS.md](docs/MANUAL_STEPS.md) 备份和核验 `archive/`、`state/`，保留旧发布记录和激活边界。凭据只放本机 `.env` 或本机配置，不写日志、不进版本库。

## 真实证据的边界

2026-09-01 的 Business Suite 探查文件曾真实记录 composer 与 Planner 行为，细节保留在 [docs/HANDOFF.md](docs/HANDOFF.md)。那份证据没有记录单渠道勾选交互，所以不能据此实现或宣称单渠道发布。新的选择器和成功判据只能来自新录制的真实 dump；截图里看见文字不等于存在可用的 DOM/可访问性定位。

## 本轮明确延期

生产机迁移、登录与 RBAC、视频加工/发布、跨平台复用、`supervised`/无人审核发布、发布队列和云盘反向同步均不在本轮。它们与尚待修复或联调的缺口分开管理。
