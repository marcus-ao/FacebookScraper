# 人工操作指南

本文件列人工依赖与开发机操作顺序，事实同步至 2026-09-14。**主干的 `archive/` 与 `state/` 就是真实生产数据**，审校写入会落到真实账本。业务规则看 [FUNCTIONALITY.md](FUNCTIONALITY.md)，每个验收单元的状态看 [REQUIREMENTS §10](REQUIREMENTS.md#10-五阶段验收状态)，证据边界看 [HANDOFF.md](HANDOFF.md)。五阶段与八批工作已接受，不重复申请普通文件修改/离线验证权限。

当前先保留现场：9224 访问 `.global`、Google Trends 公开页均实际 HTTP 429，分别保存在 `delta_state.json` 和 `trends_export_state.json`，均已停止；需要人工核对探测会话与出口。9223 已登录并录到目标 FB `Neakasa Deutschland`、IG `neakasa.de`，本次完整月份生产读取已经通过，尚无本轮新发布/排期提交。编辑器中曾放入不可发布的技术长文案与 2 张历史原图作控件观察，可能留下草稿；接手时不要直接点击提交。飞书缺凭据、镜像/外部心跳未启用，完整运营流程尚未通过。

## 1. 接续运行数据前先备份和核验

2026-09-12 已完成一次核验备份 `runtime-backups/20260912T100016Z/runtime.zip`，5,246 文件、465,064,677 字节；原激活时间 `2026-09-03T09:00:58.277710Z` 保留。主干直接按 `config.toml` 的 `[paths]` 读同目录的 archive/state；副本工作区用忽略入库的 `config.local.toml` 指回同一份数据。**不能把真实目录的配置文件或账本重置成空样例。**

以后再次接续或恢复时，先停止旧调度器与写入进程，备份完整 archive/state，另包含运行 `config.toml`、存在时的 `config.local.toml`、本机 `.env` 的受控副本和解释器位置/版本记录。备份凭据单独控制访问，不放公共云盘或证据报告；不要只复制 manifest/SQLite。

至少核验：

1. 原目录与备份的文件数/总大小、逐文件哈希一致，压缩包可读；配置与数据属于同一运行边界；
2. `state/published.jsonl`、`state/paid_requests.jsonl` 和激活状态存在且非空时完整复制；
3. 每个账号的 `review_items.jsonl`、`translated_human.jsonl`、`localization.jsonl` 都包含在内；
4. 旧 IG 目录 `in_neakasa.tech` 保持只读，不把它改名冒充 `.global`；
5. 新 `.global` 使用新的账号目录和增量游标；
6. 遇到坏账本先保留现场；严格读取应报具体文件/行，在副本检查，不删行或置空“修好”；
7. 在副本实际比较 SQLite 与源文件的 ID 集合、数量和展示字段，识别缺失/多余/内容不符；重建派生后人工/审校/付费/发布账本哈希不变；
8. 原图按每张实际字节哈希、数量、顺序核对，文件名/大小相同不能代表媒体未变；
9. Web、CLI、批处理和调度读取同一 archive/state/环境/解释器及共享锁，再恢复服务。

原工作区的 FB 47 / `.tech` 1020 是历史基线，不是 `.global` 的验收样本。本轮原 1,067 条与 SQLite 实际记录/字段已核对一致，第 1/2 页各 20 条无重复，冻结详情已真实只读访问；后续恢复仍须重新核对恢复出的副本。

一次 ZIP 不替代周期备份。周期保存不可重建账本及主配置，记录时间/文件数/哈希，并在副本恢复验证；大型 probe、截图、冻结媒体按独立 hash 版本留存，在账本中保存引用。该组合已有离线验证，企业云盘连续周期和实际恢复仍待联调。本机密钥/运行覆盖配置另作受控备份，不因云盘镜像启用而公开。云盘只单向接收，本地恢复不能自动用远端覆盖本地事实。

## 2. 配置本机凭据

已有本机绑定时编辑绑定指向的环境文件，保留已存在值；首次独立安装才从 `.env.example` 建本机 `.env`。模型/飞书凭据、收件配置、云盘目录和令牌不写入可提交配置、终端截图、probe dump 或日志。

企业管理员需提供应用 AppSecret 并完成应用授权、机器人可接收范围、德国运营/开发者两个接收组与云盘根目录权限。两个组是独立接收配置，可各为一人，不必都是群聊。还需运营机器能打开的开发机审校地址；当前均缺实际联调条件。拿到后按第 7 节验证，离线测试不代替权限。

## 3. 登录三个专用 Chrome

三个角色必须使用不同 profile 和端口：

| 角色 | 启动入口 | 默认端口 | 登录身份 |
|---|---|---:|---|
| 回填 | `scripts\start_chrome.bat` | 9222 | 抓取专用小号 |
| 发布 | `scripts\start_chrome_publish.bat` | 9223 | 持有德国站资产权限的发布账号 |
| 探测 | `scripts\start_chrome_detect.bat` | 9224 | 与回填隔离的探测专用小号 |

在各自 Chrome 人工登录/二次验证，核对三个 profile 路径、端口和身份。当前 9222/9224 已启动不表示都已有效登录；9224 已有 429 停机，应先人工检查账号与稳定出口，保留失败证据。F1-8 还需核对出口 ASN 类型、最近出口变化和证据时间，未知不能当合格。

任何 checkpoint 或会话异常都停下来。不要连续重试，不要增加自动登录脚本，也不要把发布账号拿去抓取。

## 4. 为当前目标建立归档

Facebook 目标是 `neakasaofficial`，Instagram 目标是 `neakasa.global`。确认配置后，回填由人在 9222 浏览器里打开目标主页并手工滚动；程序只拦截响应和保存内容。

```powershell
scripts\run_backfill.bat facebook
scripts\run_backfill.bat instagram
```

不要自动滚到底，不绕过 C7 会话/深度限制。完成后检查目标目录名、帖子数量、合作帖、拒绝记录和媒体完整性。`.global` 合作帖无论合作方是谁都应归档；`owner` 仍保存真实作者。

`.global` 覆盖、会话与当前安全闸未满足时不启动其自动处理/常驻任务。已授权且不依赖回填的代码/离线验收继续做；冻结 `.tech` 不因历史可查询而重译或发布。

## 5. 付费模型操作

先检查预算、来源许可和不确定请求：

```powershell
scripts\run_pipeline.bat preflight
scripts\run_pipeline.bat preflight --json
scripts\run_translate.bat --check
scripts\run_translate.bat --estimate
```

只有看到明确账号、篇数、预计费用和许可后再执行付费命令。第三方来源没有当前许可时不要运行。每张图最多受理三次优化。

若任务长时间 pending/running，在审校台查看同一 job 的拥有进程、更新时间、request ID 和恢复分类。用恢复入口核对当前 job 版本；它本身不重发模型请求。仍执行的不能抢占；未请求/已落盘/可能计费分别处理，可能计费先人工查供应商。源文/原图/提示词变化后旧候选不能继续应用，保留人工文案/图。不可重复点击或删账本绕过三次受理上限。

中断的自动批次还要核对运行状态中的 batch_id、state_revision、operation_id、paid request 与总费用。确认落盘结果和供应商状态后，使用当前版本进行“已核对结果”恢复；CLI 对应 `recover-processing --batch-id <id> --version <state_revision> --outputs-reviewed`。版本冲突则重读，恢复不自动重新发起模型请求。

当前旧 Facebook 机器译文为提示词版本 5，现版为 6；当前读取没有可接受的德语图，原 FB 目录未发现 media_de。冻结 `.tech` 的 3 张历史德语图不作 FB 或新 `.global` 素材。`stale=false` 只说明原文没变；看 `machine_current` 和机器/当前提示词版本，同时单独核对每张图。不要从版本差异推断曾有 FB 德语图，也不为准备演示自动付费重做。

风险需区分未扫、失败、成功零风险，真实扫描绑定源文/提示词，发生在翻译前。标签需分别核对 Trends DE 同英文标签候选组/同时间公开 CSV、IG 全球累计与德语同类账号周更；空名单跳过、失败/过期可手选。当前 429 下不继续 IG 或 Trends 采样。

Trends 人工确认访问恢复后，先取只读版本，再按实际核对理由恢复；`--version` 必填，恢复本身不导出：

```powershell
scripts\run_python.bat -m tools.hashtag_sampling trends-status
scripts\run_python.bat -m tools.hashtag_sampling trends-reset --reason "已人工核对访问和出口恢复" --version <revision>
```

`<revision>` 替换为刚读取的 revision，版本已变就重新核对，不删除 `trends_export_state.json`。随后用被动记录的真实 CSV 按钮生成本次 proof，核对同一英文标签的德语候选组、`geo=DE` 和起止日，再运行导出。Sign in 或任意链接不能作 CSV 控件证据；保留原始 CSV 字节、三个摘要和源文上下文，不用编辑器改换行后重新冒充原下载。当前没有可宣称通过的真实 CSV 导出。

## 6. 审校台人工检查

开发机 API 跑在 [127.0.0.1:8765](http://127.0.0.1:8765)，绑定真实数据。2026-09-13T07:13:27Z 首页、历史、2020 年冻结详情和运行状态四个 GET 均为 200，冻结详情与运行状态只读。可直接打开本机页面；**这不证明运营机器能访问。** 下列构建/启动步骤供后续停止或更新服务时使用，已有实例运行时不重复占用端口。

开发机构建前端（当前生产挂的是 React，见第 13 节）：

```powershell
npm --prefix web/ui-next install
npm --prefix web/ui-next run build
```

启动 API：

```powershell
scripts\run_python.bat -m uvicorn web.api.app:app --host 127.0.0.1 --port 8765
```

逐篇检查英文/德文、图片、标签、链接、作者/合作方和来源版本。保存、挂起、不发、资源下载和人工接管会写真实追加式账本；`fake_writer.py` 不参与当前请求。

Facebook 在链接区确定德国落地页后，可把对应 `{{linkN}}` 插入正文当前光标处；最终预览/计数会换成 URL，未插入的有效链接仍追加末尾。无效编号、缺目标或损坏占位符先修正，Instagram 使用 bio 话术。编辑中的“约”是临时估算，等待 `/check` 返回当前完整草稿的最终计数，包含标签、实际链接或 CTA；不以占位符短长度判断是否可发。

风险夹具只供明确演示模式，不能据此批准真实内容；正式页面读取实际风险状态和来源。采样降级时仍可人工决定，不把缺数据写成热度为零或已扫描安全。

从历史入口检查服务端分页、平台/月/tag/状态筛选、总数及 90 天外详情，冻结 `.tech` 只读；查询不会启动翻译。设置页只改默认柏林时刻与挂起工作日数，保存需版本校验；其它配置/模板只读并保留说明。五阶段运行状态显示真实激活、进程/处理、429、飞书凭据、镜像和月历状态，查看状态不触发外部操作。源发帖至首次就绪和批次处理时效分别显示，前者包含发现等待；没有历史就绪事实时不期待补出统计数字。

遇到 409 版本冲突时刷新后重做决定。遇到账本解析错误时停下来保留文件；不要删掉坏行让界面继续。

## 7. 企业飞书与云盘真实联调

卡片/聚合/恢复代码已有离线验证；取得上述企业条件后，在已授权的受控任务与测试目录验证：

1. 德国运营组接待审/积压/排期成功/失败，开发者组接系统告警，两个组不串；
2. 待审排队后修改文案/首图，核对有效人工/机器摘要、当前德语首图和检查；缺德语图明确原图预览，上传失败仍有文字/链接；
3. 同帖发现+处理+重启按帖只计一次；挂起/不发/接管/排期项从待审卡片退出；
4. 离岗三篇在次日 08:00 合并，含兜底补抓、分类跳过、延迟/失败与最早等待；无事不推空日报；
5. 受控不确定投递先核远端，再用运行页当前 delivery 版本确认已送达（填实际 message ID）或明确未送达；原卡仍有效时补送保持冻结 UUID/接收组/内容/图片引用，源文后来更新也不重组旧尝试；
6. 两个收件人中一人已收、另一人未收时令聚合卡部分帖子失效，确认旧卡与原尝试保留，仅把仍有效帖子的新提醒补给漏收者，不重复已收者、不复活失效帖；
7. 镜像 v1 后生成 v2，确认 v1 不覆盖；
8. 在受控恢复演练目录移除一个已备份测试版后，用冻结 v1 补送，确认没有误用当前 v2；
9. 验证两个周期的配置/账本包、大型证据独立版本和副本恢复；确认远端修改不会回写本地。

在运营机器点击真实卡片到对应帖子详情，记录两个接收组、日期和 message/file ID，不记录密钥。关机/断网/停调度的缺席告警由外部心跳服务验证，不能只看本机发送成功。

outbox 的终态在线保留期默认 30 天，过期完整关联组件归档后原卡片、UUID 与回执仍可读，事件去重索引留在主状态。未决、待重试、待发和离岗待发不归档；不要为缩小文件手工删除这些记录。归档异常时保留原文件与 hash 记录核对。

## 8. 录制单渠道 Business Suite 证据

2026-09-01 dump 没有单渠道交互。2026-09-12 已有 `channel_controls.json`、被动 `publish_probe_20260912_205317_056051.json` 的 22 条事件，观察到 FB `Neakasa Deutschland`、IG `neakasa.de`。`planner_controls.json` 和最新 `planner_month_20260913T044722903276.png` 与后续生产读取共同证明本次月份已完整读取；不证明本轮真实提交成功。继续录缺失部分时分别记录控件观察、编辑器操作和具体内容确认后的提交。

```powershell
scripts\run_python.bat -m tools._scaffolding.probe_publish
```

人在 Business Suite 中完成动作，录制器只观察。每条探查至少覆盖：

- 从默认状态切换成唯一目标渠道；
- 目标账号的可定位证据；
- 该渠道自己的日期与时间控件；
- 当前可见月份、每个日期格/带时刻条目、手工项与延迟加载；活跃时间建议不当帖子；
- 发布/排期详情的只读渠道证据和公开状态，不明确则 unknown；
- final 快照和遮罩截图。

本轮已经观察到已发布详情的渠道图标、`Published on` 与合作作者信息，以及两个未来 time-only 条目实际为推荐时段并已排除。生产 inventory 在 2026-09-13T05:01:33Z 返回 ready：8 月 30 日至 10 月 3 日的 35 格、4 条公开帖、3 个独立 IG remote ID；这只覆盖本次已观察月份。只有完成第 9 节具体内容确认后，才把一次 Schedule、成功 dialog、该渠道卡片/全文/时刻/remote ID 的回读录入提交验收；`Publish`/`Publish now` 不用于验证。

原 state 中 `composer_media_probe_20260913T054737Z_final.json/.png` 是另一次编辑器媒体观察：技术文案明确写有“Technischer Entwurf…Nicht zur Veröffentlichung vorgesehen.”，并上传 2 张历史原图。`composer_media_verification_20260913.json` 已在 06:01:17Z 验证这两图的数量、顺序和视觉一致性。未点 Schedule、Publish、Finish later 或 Cancel，可能留下未保存/自动保存草稿。它不是业务候选；后续接手先识别现场，不能直接提交。缩略图检查只证明编辑器中媒体准备状态，不能替代远端排期卡片中的最终图片证据。

先运行信号报告，不要直接手改 selector 注册表：

```powershell
scripts\run_probe_signals.bat --report state\<新的_probe_dump>.json
```

开发者和业务人员共同复核 dump 后才能回填定位。截图里的可见文字若没有 role/accessible name，不算定位证据。

## 9. 真实发布前的最终确认

在可能提交 Business Suite 的动作之前，先准备两篇实际联调内容（FB-only 一篇、IG-only 一篇），每篇给用户审查：

- 来源帖子和来源版本；
- 最终德语正文、hashtags、链接/bio 话术；
- 每张最终图片及哈希；
- 唯一渠道和目标账号；
- 柏林时刻，以及 UI 时区换算；
- Planner 当前覆盖与同渠道前后 90 分钟冲突结果；
- 本次冻结快照位置和预算状态。

当前这两个联调包尚未齐备。FB 已有来源为 2026-08-16“Cat or CCTV”的单图待制作包 `integration-candidates/facebook-122120460231379375/`（在当时开发副本的 `state/` 下）：德语正文提案、图片德语替换指令、原图与 manifest 已准备，最终德语图仍缺。拟定目标为 Neakasa Deutschland、2026-09-15 10:00 柏林，未占位、未批准；先确认这篇历史内容可作受控样本及图片付费许可，完成图片后再确认整包提交。该包不覆盖 FB 短链、IG bio 或真实多图。过期 8 月活动/美元促销需要业务重新判断；IG 尚无 `.global` 新素材。技术草稿和冻结 `.tech` 不能替代当前来源。

用户确认后才执行一次提交。提交前后都不要编辑冻结目录。只有 Planner 回读确认目标渠道、时刻和 remote ID 后才能写 `scheduled`。`scheduled` 不代表到时已经公开。

两篇合起来须分别覆盖长正文完整回读、多图字节/数量/顺序、FB 德国链接、IG CTA；人工选时若同渠道 90 分钟内冲突，拒绝并给建议，不自动顺延。提交前再读完整远端 Planner，不能用未加载完的月历判空档。公开状态仅只读观察，未证实写 unknown。

当前远端 scheduled 详情图片读取适配器还没有实现。受控样本具体确认后，先留取实际排期详情的图片控件证据，再补适配和回归，才能验证远端图片数量/顺序与冻结来源 SHA。编辑器两图通过、成功 dialog 或仅正文/ID 回读都不会让 G8 通过；历史 scheduled 仍保留防重，不为补验收重新提交。

最终由运营走完真实飞书卡片 → 当前帖审校/修改/检查 → 柏林选期/确认 → 单渠道回读 → 飞书回执，记录实际任务与消息。两次独立脚本提交不能代替此流程。

点击后进程退出、回读失败或 remote ID 不一致时，不要重试。查 `published.jsonl`、Planner 和冻结快照，按不确定提交处理。

当前还保留一条旧双渠道不确定账目：attempt `d9853906-57d9-437c-9f71-4ed72d103a81`、post `3965025107383038890`、2026-09-09 10:00 +02:00，缺 remote ID 和快照。需人工核对原提交现场/平台记录，当前月历没卡不能判成未提交；先保留该账目，不再次点击，也不把它算作本轮单渠道验收。

## 10. 激活边界

激活只影响边界之后的新内容，旧归档不得自动补发。接续旧状态时先运行只读 `preflight/status`，核对激活时间、已发布引用和不确定记录。不要为了“重新开始”删除状态。

本轮只读 preflight 的 exit 0 只表示查询完成，实际仍显示探测 blocked、内容 not_observed、飞书 disabled，以及两渠道 acceptance.verified=false。不能仅按退出码启动调度或提交。

旧激活边界已经存在，接续时保留，不能再 activate 覆盖成今天。在 `.global` 回填、关键代码缺口、企业联调和受控真实验收前置未满足时，不启用常驻自动运行；当前持久停机标记保留到人工核对后。

## 11. 安装当前调度器

本轮已实际只读运行 `python -m tools.schedule scheduler-status`，返回 `FBScraperScheduler: 未注册`，退出码 0。当前 8765 端口的 Web 服务已经启动，常驻监测任务仍未安装；两个进程的状态不能混用。

`tools.schedule install` 是旧每日组合。常驻 scheduler 当前已有下列入口，先做只读预览与安装演练：

```powershell
scripts\run_python.bat -m tools.schedule scheduler-xml
scripts\run_python.bat -m tools.schedule scheduler-install --dry-run
scripts\run_python.bat -m tools.schedule scheduler-status
```

检查 XML 的工作目录、`scripts\run_scheduler.bat`、运行账户、登录条件、日志位置与旧每日任务冲突。实际安装前已有状态仍应备份，并核对：

```powershell
scripts\run_scheduler.bat --preview
scripts\run_pipeline.bat preflight
```

只有真实依赖验收完成才用 `scheduler-install` 安装并允许 `--run`。管理入口 `scheduler-disable` 会停用定义并结束常驻实例；`scheduler-enable` 检查旧任务冲突后恢复并启动。它们会改变实际计划任务，按维护窗口执行，不为演示而运行。安装后验证查询、停用、恢复、重启单实例、不补跑睡眠全部轮次与三 profile 隔离；进程中断付费任务仍先核账，不自动重放。

提交最终验收报告前，在全部修改集成后构建前端，再运行隔离全部测试入口；保存命令、版本与结果。

```powershell
npm --prefix web/ui-next run build
npm --prefix web/ui-next test
scripts\run_python.bat -m tools.test_offline
```

`tools/test_offline.py` 给每个脚本独立的 archive/state/环境和日志——**它不会让测试结果自动变成真实集成结果。** 切换到 React 之后的最新一轮是 Python 66/66、React 498/498、typecheck 干净。

仅复验浏览器时：

```powershell
scripts\run_python.bat tests/browser_regression.py
```

它用实际 dist、临时真实 ASGI 和隔离 archive/state：保存、历史、设置、链接最终计数走临时真实后端，远端状态用显式夹具，只验证 UI 行为。每次回归记录构建 JS 哈希、截图、断言、页面错误、阻断请求和允许的临时写请求，产物落在已 gitignore 的 `state/ui-regression/`。

⛔ **不要对绑定真实数据的目录直接运行会写入的测试脚本。**

部署形状另有两个入口，浏览器回归照不出来（`tests/browser_fixture.py` 的 UIFixture 自带 SPA 回落，全绿只证明前端逻辑对）：

```powershell
scripts\run_python.bat tests/tests_spa_static.py
scripts\run_python.bat tests/cutover_rehearsal.py --dist web/ui-next/dist
```

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

## 13. 审校台前端的切换与回滚

**2026-09-14 已经切到 React。** 这一节留着有两个用途：出问题时照着回滚，以及下次再动挂载点时照着走一遍。

旧 Vue（`web/ui/`）与新 React（`web/ui-next/`）两份构建并存，生产挂哪一份由
`config.toml` 的 `[paths].web_dist` 决定。切换和回滚都只改这一个值 + 重启 Web 进程，
不碰接口、字段、账本或归档。

> ⚠️ 当前 `web_dist = "web/ui-next/dist"` 这一行**尚未提交**。缺省值是 `web/ui/dist`，
> 所以一次 `git checkout -- config.toml` 就会让审校台静悄悄退回旧 Vue，页面不会报错。

从上到下勾。任何一步失败就停，先判断要不要回滚（见最后两节）。

部署契约钉在 `tests/tests_spa_static.py`；切换与回滚的演练脚本是 `tests/cutover_rehearsal.py`。

---

## PRE-SWITCH

- [ ] 工作区已经形成你认可的 release commit
- [ ] React 构建 PASS — `npm --prefix web/ui-next run build`
- [ ] Vue 回滚构建 PASS — `npm --prefix web/ui run build`
- [ ] Python 全量 PASS — `scripts\run_python.bat tools/test_offline.py`
- [ ] React 单测 PASS — `npm --prefix web/ui-next test`
- [ ] 演练 React PASS — `scripts\run_python.bat tests/cutover_rehearsal.py --dist web/ui-next/dist`
- [ ] 演练 Vue 回滚 PASS — 同上，`--dist web/ui/dist`
- [ ] 记下当前 `config.toml` 里 `[paths].web_dist` 的值：`________________`（没有这个键就写"没有"）
- [ ] `web/ui/dist/index.html` 存在
- [ ] `web/ui-next/dist/index.html` 存在

---

## SWITCH

- [ ] `config.toml` 的 `[paths]` 表里设置 `web_dist = "web/ui-next/dist"`
- [ ] 重启 Web 进程

⛔ 只刷新浏览器没用：`DIST` 在 `web/api/app.py` import 时就定死了。

---

## READ-ONLY SMOKE

**每一条都从地址栏直接敲，不要只点侧栏导航**——侧栏走的是前端路由，
地址栏走的才是服务端。

- [ ] `/review`
- [ ] `/history`
- [ ] 一篇活账号详情
- [ ] **在那一页按 F5**
- [ ] 一篇冻结账号（`read_only`）详情：确认没有任何写入入口
- [ ] `/calendar`
- [ ] `/settings`
- [ ] `/runtime`
- [ ] 旧链接 `/?task=<真实账号>/<真实帖子>` → 跳到 `/review/...`
- [ ] 旧链接 `/?view=history` → 跳到 `/history?page=1&limit=50`
- [ ] 浏览器 console 无报错
- [ ] Network 里没有异常 404 / 422
- [ ] 柏林时刻显示合理（不是本地时区换算过的）
- [ ] 队列四个页签的计数与真实数据对得上

---

## LOW-RISK WRITE SMOKE

由你或授权运营手工做。每步之后回列表看一眼状态。

- [ ] 编辑德语正文并保存
- [ ] 返回列表，状态正确
- [ ] 再打开这一篇，内容仍然正确
- [ ] 修改产品分类并保存
- [ ] snooze 一篇
- [ ] wake 回来

---

## OPTIONAL EXTERNAL READ

前面全部成功之后才考虑。

- [ ] 由你决定是否执行一次 calendar refresh（会打开发布浏览器读后台，数十秒）

---

## SUPERVISED EXTERNAL WRITE

**必须单独得到你的批准才做。**

- [ ] 选一篇明确允许用来测试的帖子
- [ ] 选好柏林时间
- [ ] 执行真实 approve
- [ ] 在 Business Suite 里确认这条排期真的存在
- [ ] React 界面回读为「已排期」
- [ ] ⛔ 不能只因为 HTTP 200 就判成功——回执必须是 `ok === true && status === 'scheduled'`

---

## OBSERVE（第一个工作日）

- [ ] 历史归档页缩略图补齐速度（已知项，生产实测首屏约 12–13 秒，数字与量法见 [HANDOFF §7](HANDOFF.md)；太慢先改成 20 条/页）
- [ ] `/runtime` 状态
- [ ] 队列计数与列表局部更新是否对得上
- [ ] 记录出现过的每一次 409 恢复
- [ ] 记录运营任何一次"不知道该点哪"的瞬间

---

## ROLLBACK TRIGGERS

出现任意一条就回滚：

- 深链接又出现 404（`/review`、`/calendar`、详情页刷新）
- 页面白屏 / JS 报错
- 保存之后读不回来
- revision / 409 冲突恢复走不通
- 已排期状态显示错误（尤其把 `scheduled` 显示成已发布）
- 关键写入口消失（编辑德语、分类、snooze、wake、approve）
- 真实请求体异常（字段缺失、带了时区偏移、revision 不对）
- 历史页大面积不可用

---

## ROLLBACK

- [ ] `config.toml` 的 `[paths].web_dist` 改回 PRE-SWITCH 记下的值
      （原来没有这个键就把整行删掉），或直接写 `web_dist = "web/ui/dist"`
- [ ] 重启 Web 进程
- [ ] 打开 `/`，确认是旧 Vue 界面
- [ ] 核对几个基本 GET：`/?view=history`、`/?view=calendar`、`/?task=...`

归档与 state **不需要**回滚：这次切换没有改任何数据格式或写入契约。
