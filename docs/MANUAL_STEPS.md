# 人工操作指南

本文件只列需要人亲自完成的动作。当前 worktree 没有 `archive/` 或 `state/`，因此不要直接把它当生产目录启动真实流程。业务规则看 [FUNCTIONALITY.md](FUNCTIONALITY.md)，缺口看 [OPTIMIAZATION.md](OPTIMIAZATION.md)。

## 1. 接续运行数据前先备份和核验

从原工作区接续时，先停止旧调度器和所有写入进程，再复制完整的 `archive/` 与 `state/` 到带日期的备份位置。不要只复制 manifest 或 SQLite。

至少核验：

1. 原目录与备份的文件数/总大小一致；
2. `state/published.jsonl`、`state/paid_requests.jsonl` 和激活状态存在且非空时完整复制；
3. 每个账号的 `review_items.jsonl`、`translated_human.jsonl`、`localization.jsonl` 都包含在内；
4. 旧 IG 目录 `in_neakasa.tech` 保持只读，不把它改名冒充 `.global`；
5. 新 `.global` 使用新的账号目录和增量游标；
6. 在恢复代码完成前，遇到坏账本或缺失文件先保留现场，不手工删行“修好”。

原工作区的 FB 47 / `.tech` 1020 是历史基线，不是 `.global` 的验收样本。

## 2. 配置本机凭据

复制 `.env.example` 为 `.env`，只在本机填写模型和企业集成凭据。飞书应用 ID/密钥、收件人、云盘目录和任何访问令牌不得写进 `config.toml` 的可提交部分、终端截图、probe dump 或日志。

企业飞书需要管理员完成应用授权、机器人收件范围和云盘目标目录权限。拿到权限后再做 [第 7 节](#7-企业飞书与云盘真实联调)，离线测试不能代替。

## 3. 登录三个专用 Chrome

三个角色必须使用不同 profile 和端口：

| 角色 | 启动入口 | 默认端口 | 登录身份 |
|---|---|---:|---|
| 回填 | `scripts\start_chrome.bat` | 9222 | 抓取专用小号 |
| 发布 | `scripts\start_chrome_publish.bat` | 9223 | 持有德国站资产权限的发布账号 |
| 探测 | `scripts\start_chrome_detect.bat` | 9224 | 与回填隔离的探测专用小号 |

在各自打开的 Chrome 窗口里人工登录并完成二次验证。完全关闭后重新启动，确认仍登录且三个 profile 路径不同。不要在这些窗口做日常浏览。

任何 checkpoint 或会话异常都停下来。不要连续重试，不要增加自动登录脚本，也不要把发布账号拿去抓取。

## 4. 为当前目标建立归档

Facebook 目标是 `neakasaofficial`，Instagram 目标是 `neakasa.global`。确认配置后，回填由人在 9222 浏览器里打开目标主页并手工滚动；程序只拦截响应和保存内容。

```powershell
scripts\run_backfill.bat facebook
scripts\run_backfill.bat instagram
```

不要自动滚到底，不绕过 C7 会话/深度限制。完成后检查目标目录名、帖子数量、合作帖、拒绝记录和媒体完整性。`.global` 合作帖无论合作方是谁都应归档；`owner` 仍保存真实作者。

新 `.global` 基线完成前，不启动付费处理、发布或常驻调度。

## 5. 付费模型操作

先检查预算、来源许可和不确定请求：

```powershell
scripts\run_pipeline.bat preflight
scripts\run_translate.bat --check
scripts\run_translate.bat --estimate
```

只有看到明确账号、篇数、预计费用和许可后再执行付费命令。第三方来源没有当前许可时不要运行。每张图最多受理三次优化。

若模型任务长时间保持 `pending`/`running`，先停止新增请求并核对付费账本与供应商记录。当前孤儿恢复逻辑尚未完成，不能通过重复点击或删账本来恢复。

## 6. 审校台人工检查

开发机构建前端：

```powershell
Set-Location web\ui
npm install
npm run build
Set-Location ..\..
```

启动 API：

```powershell
.venv\Scripts\python.exe -m uvicorn web.api.app:app --host 127.0.0.1 --port 8765
```

逐篇检查英文/德文、图片、标签、链接、作者/合作方和来源版本。保存、挂起、不发、资源下载和人工接管会写真实追加式账本；`fake_writer.py` 不参与当前请求。

当前风险黄色标记若来自 `web/api/fixtures/risks.json`，只能用于界面演示，不得据此批准真实帖子。真实风险扫描和真实 hashtag sampler 完成前，运营需要人工判断这些部分。

遇到 409 版本冲突时刷新后重做决定。遇到账本解析错误时停下来保留文件；不要删掉坏行让界面继续。

## 7. 企业飞书与云盘真实联调

代码缺口补齐后，使用一个不会触发发布的测试任务验证：

1. 待审事件排队后再修改文案，确认卡片显示发送时的当前内容；
2. 重启服务并重复处理同一事件，确认只送达一次；
3. 在离岗窗产生三条事件，确认下一 08:00 合并且不丢条目；
4. 模拟零新帖与全部视频两天，确认日报文本不同；
5. 制造一次不确定投递，确认不会盲重放；
6. 镜像 v1 后生成 v2，确认 v1 不覆盖；
7. 删除远端某个测试版后，用冻结 v1 补送，确认没有误用当前 v2；
8. 确认远端修改不会回写本地。

记录企业应用、测试收件人、日期和远端 message/file ID；不要记录密钥。

## 8. 录制单渠道 Business Suite 证据

2026-09-01 的历史 dump 没有渠道勾选交互，不能解锁当前单渠道发布。需要用发布 Chrome 9223 新录至少两条探查：FB-only 和 IG-only。

```powershell
.venv\Scripts\python.exe -m tools._scaffolding.probe_publish
```

人在 Business Suite 中完成动作，录制器只观察。每条探查至少覆盖：

- 从默认状态切换成唯一目标渠道；
- 目标账号的可定位证据；
- 该渠道自己的日期与时间控件；
- `Schedule` 提交，绝不点击 `Publish` / `Publish now`；
- 成功 dialog、Planner 数据真正就绪、对应渠道卡片和 remote ID；
- 当前可见日期范围；
- final 快照和遮罩截图。

先运行信号报告，不要直接手改 selector 注册表：

```powershell
scripts\run_probe_signals.bat --report state\<新的_probe_dump>.json
```

开发者和业务人员共同复核 dump 后才能回填定位。截图里的可见文字若没有 role/accessible name，不算定位证据。

## 9. 真实发布前的最终确认

在任何可能点击 Business Suite 的命令之前，先准备给用户审查的具体清单：

- 来源帖子和来源版本；
- 最终德语正文、hashtags、链接/bio 话术；
- 每张最终图片及哈希；
- 唯一渠道和目标账号；
- 柏林时刻，以及 UI 时区换算；
- Planner 当前覆盖与同渠道前后 90 分钟冲突结果；
- 本次冻结快照位置和预算状态。

用户确认后才执行一次提交。提交前后都不要编辑冻结目录。只有 Planner 回读确认目标渠道、时刻和 remote ID 后才能写 `scheduled`。`scheduled` 不代表到时已经公开。

点击后进程退出、回读失败或 remote ID 不一致时，不要重试。查 `published.jsonl`、Planner 和冻结快照，按不确定提交处理。

## 10. 激活边界

激活只影响边界之后的新内容，旧归档不得自动补发。接续旧状态时先运行只读 `preflight/status`，核对激活时间、已发布引用和不确定记录。不要为了“重新开始”删除状态。

在 `.global` 回填、关键代码缺口、企业联调、单渠道 probe 和一次受控真实验收全部完成前，不激活常驻流水线。

## 11. 安装当前调度器

`python -m tools.schedule install` 是旧的每日任务组合，不能安装当前 `pipeline.scheduler`。当前流程是先生成 XML：

```powershell
.venv\Scripts\python.exe -m tools.schedule scheduler-xml
```

人工检查 XML 中的工作目录、`scripts\run_scheduler.bat` 路径、运行账户、登录条件和日志位置，再在 Windows 任务计划程序导入。导入前先备份状态并运行：

```powershell
scripts\run_scheduler.bat --preview
scripts\run_pipeline.bat preflight
```

只有在真实依赖验收完成后才允许 `--run`。安装后重启机器一次，确认只启动一个调度器、不会补跑睡眠期间的所有轮次、三个浏览器 profile 不冲突。

## 12. 卡住时保留什么

不要先清理现场。提供：

- 完整命令和时间；
- `git rev-parse --short HEAD` 与 `git status --short`；
- 相关日志最后一段，先检查没有凭据；
- 对应 archive/state 文件名和哈希；
- 若涉及 Business Suite，probe dump 名、交互序号和遮罩截图；
- 若涉及付费，request/job ID、账本状态和供应商侧是否计费；
- 若涉及飞书/云盘，非敏感的 message/file ID 和远端错误码。

不要提供 cookie、token、`.env`、完整用户数据截图或未遮罩的浏览器 dump。
