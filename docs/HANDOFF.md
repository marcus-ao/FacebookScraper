# 项目交接

**交接基线：2026-09-12，提交 `c67c56a`。** 业务功能以 [FUNCTIONALITY.md](FUNCTIONALITY.md) 为准；本文件保存动代码前必须知道的边界和真实证据。

## 1. 当前工作区事实

- 工作分支是 `codex/business-workflow`，在 `.worktrees/business-workflow` 开发。
- 基线之外已有 6 个未提交代码文件；接手者要先看 `git status`，不得覆盖或把它们误算成文档改动。
- 当前 worktree 没有 `archive/` 和 `state/` 运行数据。
- 原工作区历史归档是 Facebook 47 篇、旧 IG `in_neakasa.tech` 1020 篇。新目标 `neakasa.global` 尚无本 worktree 真实基线。
- 规划阶段记录为 47 个离线测试脚本和前端构建通过；没有本轮 live run。不要把离线测试写成真实验收。
- 接续原 archive/state 之前先做完整备份和一致性核验，保留旧 `published.jsonl`、付费账本和激活边界。

## 2. 红线

1. 不自动登录。人在三个专用 Chrome profile 登录，代码只附着。
2. 不自动滚历史。回填由人滚；增量和兜底对账始终受 C7 深度、频率、会话和失败预算约束。
3. 抓取、探测、发布三种身份分离：9222 / 9224 / 9223。不要恢复旧“两浏览器”设计。
4. Facebook 与 Instagram 是独立车道，不配对、不合并、不沿用 composer 默认双选。
5. 所有 Business Suite 定位和成功判据必须能回查真实 probe dump 的交互/快照。截图见到文字不等于存在可定位元素。
6. 人工文案、人工图片和人工决定不能被重建、重译或后台任务覆盖。
7. 付费请求必须经过统一预算和来源许可。不确定计费先核账，不自动重放。
8. 发布前先冻结并展示具体文案、图片、账号、单一渠道和时刻，等用户确认后才能提交。
9. `review_items.jsonl`、`paid_requests.jsonl`、`published.jsonl` 的坏数据必须失败闭合。不得把损坏当空文件继续运行。
10. 凭据只在本机配置，不写日志、dump、截图或版本库。

## 3. 架构与真相源

```text
浏览器响应/人工回填
        ↓
post.json + 原图
        ↓
机器译文/图片/付费账本
        ↓
人工文案 + 本地化选择 + review_items.jsonl
        ↓
不可变发布快照
        ↓
Business Suite intent / 回读 / published.jsonl

manifest / SQLite / HTML / Planner cache / 飞书云盘
        ↑ 只能由上述本地真相派生
```

对账器只读各阶段真相并调用阶段入口，不建立发布队列，也不自己解析响应、拼模型提示词或点浏览器。`web/` 可以依赖核心层，核心层不得依赖 `web/`。

`published.jsonl` 不可重建。它和激活边界共同阻止旧帖被再次提交。云盘是单向镜像，不是恢复时的自动反向数据源。

## 4. 当前关键缺口

以下是当前缺口，不是延期项：

- 进程退出后的孤儿模型任务仍缺安全恢复、费用核对和终态分类；
- 审校账本坏行会被静默忽略；
- 历史 UI 尚未落实“只展示最近 90 天”；
- 月末/跨月可选范围仍缺动态适配；
- 常驻 scheduler 只有 XML 生成，没有当前安装 CLI；旧 `tools.schedule install` 不适用；
- 风险预扫描仍是手写夹具；真实 hashtag samplers 不存在；
- 飞书还缺发送时当前内容投影、完整业务去重和每日摘要；
- 不是所有发布入口都已证明使用同一不可变快照；
- 单渠道勾选、目标账号和回读没有真实 probe 证据；
- 企业飞书、云盘和 Business Suite 的端到端真实联调尚未完成。

生产迁移、登录/RBAC/actor、视频、跨平台复用、`supervised` 和无人审核发布是明确延期，单独管理。

## 5. 抓取侧真实事实

### Instagram

- `web_profile_info` 对登出访客已关闭，2026-08-30 首次请求即 429。
- 登录态时间线可通过真实浏览器请求拦截获得；不要硬编码每 2–4 周可能轮换的 GraphQL `doc_id`。
- 旧 `doc_id` 返回 401 且提示稍后重试，这不是限流，等待不会恢复。
- `web_profile_info` 形态不含完整轮播子项时必须标 `media_complete=false`。
- 合作帖是否在目标时间线用 `parse.on_timeline_of()`，不能用 `owner == account`。

### Facebook

- 登录态通过 CDP 附着真实 Chrome 并拦截 `/api/graphql` 响应。
- `mbasic.facebook.com` 已在 2024-12-03 宣布下线，2026 年行为不稳定，不得作为地基。

### 通用

- 媒体 CDN URL 有签名和时效，拿到响应后立即下载；不能假设以后能重抓。
- 数据中心 IP 的真实尝试曾在首次请求被拦；抓取应使用稳定住宅网络。
- 历史归档的“1051 条待译正文”不是可发布口径。2026-08/09 旧归档统计为：

| 平台 | 总数 | 有正文 | 静态图文可发 | 纯视频 | 无媒体 |
|---|---:|---:|---:|---:|---:|
| Facebook | 47 | 46 | 27 | 17 | 2 |
| `in_neakasa.tech` | 1020 | 1011 | 443 | 568 | 0 |

这些数字只描述旧归档，不能外推 `neakasa.global`。

## 6. 2026-09-01 Business Suite 真实探查

历史来源是 `state/publish_probe_20260901_054226_378622.json`：56 条可信交互、71 条被动语义快照、117 张截图。旧工作区曾用下列命令检查信号：

```powershell
scripts\run_probe_signals.bat --report state\publish_probe_20260901_054226_378622.json
```

该文件不在当前 worktree，且没有单渠道切换交互。保留下列发现作为历史事实；任何新选择器仍需新 probe。

### Composer

| 要素 | 2026-09-01 观察 | 证据位置 |
|---|---|---|
| 渠道图标 | `img 'Facebook'` 与 `img 'Instagram'`，不带账号名 | 快照 24 #2/#3 |
| FB 主页 | FB 预览中的 `heading h2 'Neakasa Deutschland'` | 快照 24 #43/#44 |
| IG 账号 | 46 张 composer 快照未命中可访问名 | 全部 composer 快照 |
| 正文框 | `combobox`，富文本/contenteditable | 交互 #14–21 |
| 定时开关 | `switch`，`input type=checkbox` | 交互 #34 |
| 日期 | 每个渠道一套 `textbox 'mm/dd/yyyy'` | 交互 #37/#44 |
| 时间 | 每个渠道一组 `spinbutton` | 交互 #40–43/#46–49 |
| 提交 | `button 'Schedule'` | 交互 #50 |
| 成功 | dialog 内 `heading 'Your post is scheduled'` | 快照 50 |

`Schedule` 与 `Publish` 是不同按钮，误点后者会立即发布。两个渠道各有日期和时间控件；只设置第一组可能使另一渠道保持默认时刻。FB 主页 heading 要到图片上传后才出现，因此账号核对放在上传后、提交前。历史失败截图曾显示 IG 下拉值 `Neakasa Deutschland and neakasa.de`，但 probe 没采到可访问定位，不能仅凭截图实现定位。

### Planner

| 要素 | 2026-09-01 观察 |
|---|---|
| 可见范围 | 月份和年份是两条独立 heading |
| 月视图条目 | link 可访问名只有时刻 |
| 周视图条目 | link 可访问名含正文、完整日期和时刻 |
| 图片数 | 25 张 Planner 快照没有数量语义 |
| 渠道详情 | FB/IG 各有独立弹窗和 remote ID |

历史 probe 的 FB remote ID 为 `1887083152480681`，IG 为 `4378984725697354`。这些 ID 只属于那次测试，不能写成配置。Planner 弹窗除 Escape 关闭外不要点击；其中的 `Publish now` 会立即发布。

旧“页面出现月份 heading 就算数据就绪”曾被真实 UI 推翻：外壳先出现，卡片仍在加载。应等到可解析时刻的条目或明确空态。

### UI 上限的历史实测

| 项 | 2026-09-01 观察 |
|---|---|
| 单帖图片 | 最多 10 张 |
| 画幅比 | 4:5 至 1.91:1 |
| IG 正文 | UI 提示最多 2200 characters；代码可继续保守按 UTF-8 bytes |
| hashtags | 最多 30 个 |
| 定时下限 | 当时未观察到最小提前量 |
| 定时上限 | 当时日期选择器不能跨当前可见月份 |

“不能跨月”是当时 UI 事实，不是永久业务决定。每次真实联调要记录当前 UI 覆盖范围，代码需支持月度适配。

## 7. 内容安全规则

- 优惠码、品牌、型号、@提及、合作方水印/署名和配置中的保留词不改。
- 图片中的数值和单位不自动换算；不确定时保持原样。
- 图片和正文共用同一术语表。
- 没有当前版正文译文时，不开始图片本地化。
- 确定性错误与模型风险提示分开展示。风险夹具不得混入真实任务。

## 8. 工作协议

动手前读取本文件、[FUNCTIONALITY.md](FUNCTIONALITY.md) 和相关模块测试。先用离线输入复现，再决定是否需要真实浏览器。任何真实发布前都准备具体内容给用户确认。

验证报告必须写清：运行了什么、使用的是离线夹具还是真实账号、通过日期、哪些外部依赖仍未联调。不得用测试名称或 mock 回执替代真实证据。
