# FB / IG 内容本地化流水线

本项目把美国站 Facebook / Instagram 的公开图文帖归档到本地，生成德语文案和德语图片，交给上海运营在中文审校台修改并选择柏林发布时间，最后通过 Meta Business Suite 创建单渠道排期。

业务规则以 [docs/FUNCTIONALITY.md](docs/FUNCTIONALITY.md) 为准；实施差距看 [docs/OPTIMIAZATION.md](docs/OPTIMIAZATION.md)；本轮代码/真实证据看 [集成记录](docs/INTEGRATION_2026-09-12.md)；人工操作顺序看 [docs/MANUAL_STEPS.md](docs/MANUAL_STEPS.md)。

## 当前基线（2026-09-12）

规划基线是 `c67c56a`，当时 47 个离线脚本和一次前端构建通过；本轮代码截至 `b1a56bb`，核心集成为 `565f17c`。最新全量执行 65 脚本，首轮 64/65，唯一测试文件默认编码问题修复后相关补跑通过，65 个脚本均有通过记录。最终 Vue 50 modules 构建及实际新 dist + 临时 ASGI 的 7 场景浏览器回归通过，页面错误与外部请求均为 0；启动脚本端口统一为 8765 后另有 2 项相关复验。这是全量加相关补跑，不是首次单次全绿。

这个 worktree 已通过忽略入库的 `config.local.toml` 接续原 archive/state/.env/解释器。核验备份在本 worktree 的 `state/runtime-backups/20260912T100016Z/runtime.zip`（5,246 文件、465,064,677 字节），保留激活 `2026-09-03T09:00:58.277710Z`。归档 1,067 篇是 FB 47 + 冻结 `.tech` 1,020，不能当作 `.global` 回填证据。

状态词统一为：

| 状态 | 含义 |
|---|---|
| **代码未完成** | 所需行为或失败闭合尚不存在 |
| **离线通过** | 有离线测试或构建证据，但没有真实外部系统证据 |
| **待真实联调** | 该验收行代码前置具备，剩余是真实账号、人工操作、权限或远端证据 |
| **真实通过** | 有注明日期和来源的真实证据 |
| **明确延期** | 业务已决定本轮不做，不应与缺陷混为一谈 |

已存在的离线能力包括响应拦截与回放、本地归档、德语翻译和图片本地化、付费账本、人工审校写入、七态审校流、Planner 缓存、调度器、飞书与云盘适配层、Business Suite 证据闸。它们仍需按 [docs/OPTIMIAZATION.md](docs/OPTIMIAZATION.md) 的清单完成缺口并逐项真实联调。

本轮已修复采样 C7 持久停机与数值结构判定，补齐模型/标签/投递独立执行器、批次费用 CAS 恢复、飞书固定消息人工 resolve、多收件人漏发修复、配置与账本周期备份及大证据独立版本。历史交接的统一来源摘要/AST 守卫、兜底过期仍保留普通探测/告警、飞书 30 天终态归档、FB 正文链接占位符和最终计数也已补上。Trends 导出与单渠道全文/身份/时刻回读有离线和复审结论；远端 scheduled 图片读回适配器仍未实现，需要受控排期取证后补齐。代码项和真实依赖按实施清单分开。

真实只读验证已经取得结果：原 1,067 条归档与 SQLite 实际一致；历史每页 20 条读取第 1/2 页无重复，冻结详情只读。月历生产读取在 `2026-09-13T05:01:33Z` 返回 ready，覆盖 35 格（8/30–10/3），识别 4 条公开帖及 2 个推荐时段，包含 3 个独立 IG remote ID。推荐时段经 tooltip 明确排除，旧双渠道排期记录不作为本轮新 G8。

本轮没有真实模型、飞书调用或新发布/排期提交。9223 的 FB `Neakasa Deutschland`、IG `neakasa.de` 已确认；IG `.global` 与 Google Trends 公开页均遇 HTTP 429，已分别持久停机且未重试。`.global` 尚未首次回填；飞书 Secret/两接收组、云盘根目录及可达审校 URL 缺失，外部心跳未启用。FB 旧机器译文为提示词版本 5、当前为 6，当前没有可接受的德语图，原 FB 目录也未发现 media_de。冻结 `.tech` 保留的 3 张历史德语图不能作为 FB 或新 `.global` 素材。已有一份 FB 单图待制作包，仍缺最终德语图和具体确认，尚无两篇齐备的最终联调包。所有真实提交仍须先展示具体文图、账号、单渠道和柏林时刻给用户确认。

为探查编辑器媒体控件，发布浏览器曾放入明确标记不可发布的技术长文案和 2 张历史原图，未点 Schedule、Publish、Finish later 或 Cancel；可能留下未保存或自动保存草稿。编辑器中的两图数量/顺序视觉核验已有真实证据，不能作为业务候选或远端排期图片回读结论。

本机审校入口目前运行在 [127.0.0.1:8765](http://127.0.0.1:8765)，绑定原数据。2026-09-13T07:13:27Z 首页、每页 20 条历史、2020 年冻结详情与运行状态四个 GET 均返回 200，冻结详情/运行状态为只读；该检查没有 POST。它证明本机入口可用，运营机器的网络可达性与企业流程仍待联调。

## 已确认的业务口径

- Facebook 与 Instagram 是两条独立车道。一篇来源帖只进入对应渠道，不跨平台合并，也不依赖 composer 默认双选。
- 当前目标是 Facebook `neakasaofficial` 与 Instagram `neakasa.global`。`.global` 时间线上的合作帖全部抓取，不按合作方白名单过滤。
- `in_neakasa.tech` 已冻结，只保留历史读取；不再抓取、加工或发布。
- 抓取、探测、发布分别使用三个 Chrome profile：回填 `9222`、发布 `9223`、探测 `9224`。只允许人工登录，不自动登录。
- 上海在岗窗是 08:00–19:00；运营选择柏林时刻；同一渠道前后 90 分钟内已有排期则拒绝，并给出可选时刻。
- 人工文案和人工图片优先，机器不得覆盖。付费调用必须同时满足统一预算和来源许可；请求是否已被远端计费不确定时，先核账再决定是否重放。
- 每张图最多受理三次优化。挂起默认三个上海工作日。本轮所有审校事件的 `actor` 固定为 `null`。
- 飞书云盘只接收本地单向镜像。内容更新时新增版本，不覆盖旧版；只允许用冻结内容补送缺失的旧版本。
- 飞书业务消息和开发者告警分两个接收组；卡片取当前有效德语首图/摘要/检查，原图回退明示，已尝试消息的 UUID 和内容冻结。
- 历史支持服务端分页、总数、平台/月/tag 筛选，90 天外详情仍可读；查询不会启动翻译。风险与标签分别保留来源、版本和采样时间，三个标签来源都在范围内。
- `scheduled` 只表示 Business Suite 排期已被回读确认，不表示帖子已经公开发布。
- 所有发布入口都必须冻结同一份最终文案、图片、渠道和时刻；任何可能提交的动作之前都要给用户看具体内容并获得确认。

## 安全地查看项目

首次安装：

```powershell
scripts\setup.bat
```

离线查看调度计划，不访问浏览器：

```powershell
scripts\run_scheduler.bat --preview
scripts\run_pipeline.bat preflight --json
```

启动审校台：

```powershell
scripts\run_python.bat -m uvicorn web.api.app:app --host 127.0.0.1 --port 8765
```

若 `web/ui/dist/` 不存在，在开发机执行：

```powershell
cd web\ui
npm install
npm run build
```

生产运行静态构建不需要 Node，生产迁移本轮明确延期。启动包装入口读取同一本机运行绑定；审校台现在连到真实数据，保存和审校动作会写真实账本。只读 `preflight --json` 和 `/api/runtime` 使用相同五阶段状态，不触发外部调用。

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

`tools.schedule install` 是旧每日任务组合。当前入口为 `scheduler-install`、`scheduler-status`、`scheduler-disable`、`scheduler-enable`，另保留 `scheduler-xml`。代码已做离线任务命令验证，真实安装/停用/恢复仍待前置通过后验收；步骤见 [docs/MANUAL_STEPS.md](docs/MANUAL_STEPS.md)。

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

再次接续或恢复时，先按 [docs/MANUAL_STEPS.md](docs/MANUAL_STEPS.md) 备份和核验 archive、state、运行配置及本机环境的受控副本，保留旧发布记录和激活边界。周期账本备份与大型媒体/probe 按版本保存另行验收；已做一次备份不能代替周期恢复能力。凭据不进日志和版本库。

## 真实证据的边界

2026-09-01 的 composer/Planner 探查保留在 [docs/HANDOFF.md](docs/HANDOFF.md)，未含单渠道交互。本轮新增 22 事件 probe、单渠道控件及真实整月读取；它们证明当前读取范围，不证明长文、多图、FB 链接、IG CTA 和两篇新排期。远端图片数量/顺序仍未知，不能用本地快照代替回读。运营从真实飞书卡片到审校、选期、回执的完整流程仍待验收。

## 本轮明确延期

生产机迁移、登录与 RBAC、视频加工/发布、跨平台复用、`supervised`/无人审核发布、发布队列和云盘反向同步均不在本轮。它们与尚待修复或联调的缺口分开管理。
