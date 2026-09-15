# 人工操作指南

**本周要做阶段一（监测与抓取）的真实验收，整套顺序在[第 14 节](#14-阶段一真实验收监测与抓取)。** 下面 1–13 节是分主题的长期参考，第 14 节把其中与阶段一相关的挑出来排成一条可以照着走的线。

本文件列人工依赖与开发机操作顺序，存储说明同步至 2026-09-15。**原主工作区的 `archive/` 与 `state/` 是实际业务数据**，审校写入会落到真实账本。业务规则看 [FUNCTIONALITY.md](FUNCTIONALITY.md)，每个验收单元的状态看 [REQUIREMENTS §10](REQUIREMENTS.md#10-五阶段验收状态)，证据边界看 [HANDOFF.md](HANDOFF.md)。五阶段与八批工作已接受，不重复申请普通文件修改/离线验证权限。

当前先保留现场：9224 访问 `.global`、Google Trends 公开页均实际 HTTP 429，分别保存在 `delta_state.json` 和 `trends_export_state.json`，均已停止；需要人工核对探测会话与出口。9223 已登录并录到目标 FB `Neakasa Deutschland`、IG `neakasa.de`，本次完整月份实际读取已经通过，尚无本轮新发布/排期提交。编辑器中曾放入不可发布的技术长文案与 2 张历史原图作控件观察，可能留下草稿；接手时不要直接点击提交。消息已改走群自建机器人（同群四个机器人地址，与云盘应用权限独立）；云盘镜像仍缺应用凭据，外部心跳未启用，完整运营流程尚未通过。

## 1. 接续运行数据前先备份和核验

主干直接按 `config.toml` 的 `[paths]` 读同目录的 archive/state；副本的绑定以 `config.local.toml` 和运行状态为准。本轮 `codex/raw-post-storage` 位于 `.worktrees/storage`，单独绑定该目录中的 archive/state/.env，只复用主工作区 Python 解释器，未接主工作区业务数据。不能把这种隔离假定到其他副本。

⛔ 2026-09-14 已按业务决定把 archive/state 整库清空且**没有备份**，现在两个目录都是空的。这条决定是一次性的：**以后再接续或恢复，仍然必须先备份再动**，下面这套核验步骤照做。

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

一次 ZIP 不替代周期备份。周期保存不可重建账本及主配置，记录时间/文件数/哈希，并在副本恢复验证；大型 probe、截图、冻结媒体按独立 hash 版本留存，在账本中保存引用。该组合已有离线验证。本机密钥/运行覆盖配置另作受控备份，不因云盘镜像启用而公开。云盘只单向接收，本地恢复不能自动用远端覆盖本地事实。

⛔ **云盘本轮延期（[REQUIREMENTS §9](REQUIREMENTS.md#9-明确延期与固定边界)），所以这一节是本轮唯一的备份手段，不是可选项。** `state/published.jsonl` 不可重建——丢了会让已发过的帖子被再发一遍，而 2026-09-14 那次清库就是没有备份的。上线后按固定节奏把整个 `archive/` 与 `state/` 拷到本机之外的另一块盘，每次记下时间、文件数、字节数和哈希。**没有任何代码会替你做这件事。**

## 2. 配置本机凭据

已有本机绑定时编辑绑定指向的环境文件，保留已存在值；首次独立安装才从 `.env.example` 建本机 `.env`。模型/飞书凭据和访问令牌不写入可提交配置、终端截图、probe dump 或日志。云盘 folder token 是目标资源标识，不能代替应用凭据和目录授权。

**飞书消息使用同一业务群的四个机器人**。四个地址必填，启用签名校验时再填各自密钥（第 2.1 节）：

| 变量 | 是什么 |
|---|---|
| `FEISHU_WEBHOOK_DETECT` | 新帖检测推送机器人：检测到新帖 |
| `FEISHU_WEBHOOK_CAPTURE` | 新帖爬取推送机器人：抓取完成及逐篇落档结果 |
| `FEISHU_WEBHOOK_PUBLISH` | 新帖发布推送机器人：待审、排期成功 |
| `FEISHU_WEBHOOK_ALERT` | 状态告警推送机器人：积压、排期失败、晨间摘要、系统异常 |
| 上述变量各加 `_SECRET` | 对应机器人的签名密钥。不填就必须在该机器人配置关键词 |

⛔ **地址本身带 token，等于密钥。** 不要贴进 config、截图、日志或版本库。

另外这些凭据按对应能力配置：`IPINFO_TOKEN`（F1-8 的出口证据，`[network_evidence]` 默认已开，缺它每天会来一条"出口信息服务暂未返回有效结果"）、`FEISHU_APP_ID` / `FEISHU_APP_SECRET`（只给 `[mirror]` 的云盘镜像用，不参与发消息；**云盘本轮延期，所以这两个本轮不需要**）。

**本轮还差的是运营机器能打开的审校地址**——当前审校台在 `127.0.0.1:8765`，卡片里的「去审校」她点不开，这条与消息通道无关。企业管理员那条路（应用 AppSecret、应用授权、云盘根目录权限）随云盘一起延期，见 [REQUIREMENTS §9](REQUIREMENTS.md#9-明确延期与固定边界)。

### 2.1 配置同群四个机器人

用户已建好四个机器人。逐个核对现有机器人即可，不重复创建：

1. 进入同一业务群 → 群设置 → 群机器人，分别打开四个自定义机器人；
2. 核对名称为「新帖检测推送机器人」「新帖爬取推送机器人」「新帖发布推送机器人」「状态告警推送机器人」，与上表对应；
3. 安全设置里勾**签名校验**，把密钥和 webhook 地址一起抄下来。两种校验的区别：
   - **签名校验**（推荐）：和卡片内容无关，改文案不会失效；
   - **关键词**：卡片里必须出现该词。要用就设成 `Neakasa`（当前卡片标题里有），但请记住这个约束只写在群设置里，**以后改标题会静默失效**；
4. 四个地址及各自签名密钥填进实际运行绑定的 `.env`。`FEISHU_WEBHOOK_OPS/TECH` 已停用，不能把一个旧地址复制到多个新角色。然后人工自检——这一步会**由四个机器人各发一条消息**：

```powershell
scripts\run_python.bat -m pipeline notifications --self-test
```

同一个群应收到四张「通道自检」卡片，标题和正文各自写明预期机器人。逐张核对消息的真实发送者与卡片名称一致。同群正常；四个角色复用相同 webhook 地址会在发送前拒绝。运行页 `bots` 显示各自是否配置和格式是否有效，`duplicate_bot_targets` 显示重复地址；这些字段不含地址或密钥，也不证明实际可投递。

日常只读投递状态、以及核对不确定投递：

```powershell
scripts\run_python.bat -m pipeline notifications
```

⚠️ **群机器人不返回飞书消息 ID。** 所以登记"已送达"时填的是你自己写的核对说明（例如「业务群 21:07 已收到」），不是平台 ID；审校台运行页上的输入框同理。填不出 ID 不是你操作错了。

**未知结果不自动重发。** 超时、5xx、响应无法解析或进程中断会留在 `uncertain`。在群里核对机器人、扫描时刻和内容，再按 delivery ID 与当前 version 登记；已送达填核对说明，确认未送达才恢复。明确限流或拒绝的请求会退避 15 分钟。不要删除发件箱或更改角色来“重试”。

旧发件箱会在写入投递流程时升级为版本 2：已送达记录保留旧角色且不重发；从未尝试的记录转当前阶段；旧未知结果先核对。确认旧通道未送达后，有效内容才转当前机器人，原卡与旧 ID 保留；部分过期卡只补仍有效的内容。当前主工作区在本轮整合前没有 `feishu_outbox.json`，升级行为由隔离夹具验证。

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
scripts\run_backfill.bat facebook --days 30
scripts\run_backfill.bat instagram --days 30
```

`--days N` 只归档最近 N 天，并打印窗口外跳过数；算不出日期的仍保留。不带该参数是全量。
它收紧的是归档范围，不改人工滚动——仍要自己滚到看见窗口边界为止。

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

开发机构建唯一 React 前端（见第 13 节）：

```powershell
npm.cmd --prefix web/ui ci
npm.cmd --prefix web/ui test
npm.cmd --prefix web/ui run build
```

启动 API：

```powershell
scripts\run_python.bat -m uvicorn web.api.app:app --host 127.0.0.1 --port 8765
```

逐篇检查英文/德文、图片、标签、链接、作者/合作方和来源版本。保存、挂起、不发、资源下载和人工接管会写真实追加式账本。

Facebook 在链接区确定德国落地页后，可把对应 `{{linkN}}` 插入正文当前光标处；最终预览/计数会换成 URL，未插入的有效链接仍追加末尾。无效编号、缺目标或损坏占位符先修正，Instagram 使用 bio 话术。编辑中的“约”是临时估算，等待 `/check` 返回当前完整草稿的最终计数，包含标签、实际链接或 CTA；不以占位符短长度判断是否可发。

风险夹具只供明确演示模式，不能据此批准真实内容；正式页面读取实际风险状态和来源。采样降级时仍可人工决定，不把缺数据写成热度为零或已扫描安全。

从历史入口检查服务端分页、平台/月/tag/状态筛选、总数及 90 天外详情，冻结 `.tech` 只读；查询不会启动翻译。设置页只改默认柏林时刻与挂起工作日数，保存需版本校验；其它配置/模板只读并保留说明。五阶段运行状态显示真实激活、进程/处理、429、飞书凭据、镜像和月历状态，查看状态不触发外部操作。源发帖至首次就绪和批次处理时效分别显示，前者包含发现等待；没有历史就绪事实时不期待补出统计数字。

遇到 409 版本冲突时刷新后重做决定。遇到账本解析错误时停下来保留文件；不要删掉坏行让界面继续。

## 7. 飞书群与云盘真实联调

卡片/聚合/恢复代码已有离线验证。第 2.1 节需人工核对四张自检卡的真实发送者；HTTP 成功只证明接口接受请求，不能独自证明机器人对应、业务内容或运营可达性。逐条验证：

0. 恢复 9224 探测会话后跑一轮真实探测：检测机器人先发「监测到新帖」，爬取机器人后发「原帖抓取完成」，明细与本地目录、图片数一致；在受控演练中停止探测 Chrome，由状态告警机器人提示系统异常；
1. 同一群内按四机器人核对全部八类：检测、抓取各一类；发布接待审/排期成功；状态告警接积压/排期失败/晨间摘要/系统异常。`scheduled` 仍只表示远端排期回读确认；
2. 待审排队后修改文案/首图，核对卡片给的是当前有效人工/机器摘要与检查结果；首图状态写成文字且注明「未随卡片投递」——**卡片里没有缩略图是当前形态，不是故障**；重读失败时卡片应明说内容可能不是最新；
3. 同帖发现+处理+重启按帖只计一次；挂起/不发/接管/排期项从待审卡片退出；
4. 离岗三篇在次日 08:00 合并，含兜底补抓、分类跳过（四类都应是中文）、延迟/失败与最早等待；无事不推空日报；
5. 受控未知投递重启后保持暂停；先在群里核对，再按当前 delivery 版本登记已送达或确认未送达。当前角色的有效原卡保持 ID/内容；旧角色转交则保留旧记录并创建当前阶段投递。不能凭网络异常断言未送达；
6. ~~多收件人部分失效~~：每个角色现在只有一个群，这条在生产里不再触发（[REQUIREMENTS §10.4](REQUIREMENTS.md#104-阶段四飞书主入口与运行可见性) 已标明确延期）。加第二个群之后再验；
7. 镜像 v1 后生成 v2，确认 v1 不覆盖；
8. 在受控恢复演练目录移除一个已备份测试版后，用冻结 v1 补送，确认没有误用当前 v2；
9. 验证两个周期的配置/账本包、大型证据独立版本和副本恢复；确认远端修改不会回写本地。

在运营机器点击真实卡片到对应帖子详情，记录群、机器人名称、日期、任务与云盘 file ID，不记录地址或密钥。机器人没有 message ID；证据是群里实际消息、人工核对记录与本地 delivery ID。关机/断网/停调度的缺席告警由外部心跳服务验证。

outbox 的终态在线保留期默认 30 天，过期完整关联组件归档后原卡片、UUID 与回执仍可读，事件去重索引留在主状态。未决、待重试、待发和离岗待发不归档；不要为缩小文件手工删除这些记录。归档异常时保留原文件与 hash 记录核对。

### 7.1 原帖云盘接入与恢复

⛔ **本节本轮不执行（2026-09-15 业务决定，见 [REQUIREMENTS §9](REQUIREMENTS.md#9-明确延期与固定边界)）。** 下面的步骤留着，拿到应用凭据后照做；在那之前不要因为 `mirror_status` 显示 `disabled` 就来补实现或改配置。

**目标已确定为[原帖归档文件夹](https://genhigh.feishu.cn/drive/folder/TsQef8msClS2i6dlzpfcpfn6nAc)。** 代码配置保留该 folder token，`mirror.enabled=false`。目标内容、权限、容量与运营账号可见性均未实测；本轮 worktree 环境文件没有应用凭据。

**管理员准备：**

1. 在飞书开放平台创建企业自建应用，启用机器人；按下表申请接口权限，由管理员批准并发布可用版本。当前文档提供的细分权限为：建目录 `space:folder:create`、上传 `drive:file:upload`、目录列表 `space:document:retrieve`、移动 `space:document:move`；[异步任务查询](https://open.feishu.cn/document/uAjLw4CM/ukTMukTMukTM/reference/drive-v1/file/task_check)另需 `drive:drive.metadata:readonly`。各接口也接受范围较大的 `drive:drive`；是否授予统一权限由企业管理员按实际调用范围决定。
2. 按[应用获得目录访问权限的官方步骤](https://open.feishu.cn/document/uAjLw4CM/ugTN1YjL4UTN24CO1UjN/trouble-shooting/how-to-add-permissions-to-app)，将已发布机器人加入授权群，并把目标文件夹分享给该群。应用权限和资源权限是两道条件；开发者本人能打开目录不能证明应用有权限。运营读者配置为只读。
3. 在实际运行绑定的 `.env` 中填写 `FEISHU_APP_ID`、`FEISHU_APP_SECRET`，不放进 Git。用审校台运行状态检查是否存在凭据；该状态不会打印值。
4. 在目标下人工创建独立验收子目录，记下其 folder token。验收使用独立 archive/state/.env 绑定，在该运行配置的 `[mirror]` 设 `enabled=true`、验收子目录 token、`mirror_state=false`，先只验原帖。已有镜像队列不能换根 token 后重置；不同验收根应使用独立 state，保留旧队列和冻结字节。

**接口限制与实现依据：**

| 操作 | 核对入口和实现约束 |
|---|---|
| 建目录 | [create_folder](https://open.feishu.cn/document/server-docs/docs/drive-v1/folder/create_folder)，目录名按 UTF-8 计最多 256 字节；分类名与本地相同。 |
| 元数据分页 | [list](https://open.feishu.cn/document/server-docs/docs/drive-v1/folder/list)，逐页处理 `has_more/next_page_token`，单页至多 200 项；同名文件不是相同内容的证据。 |
| 普通上传 | [upload_all](https://open.feishu.cn/document/server-docs/docs/drive-v1/upload/upload_all)，上限 20,971,520 字节；普通 Drive 文件使用 `explorer` 父节点。空正文由清单表达。 |
| 大文件 | [分片上传](https://open.feishu.cn/document/uAjLw4CM/ukTMukTMukTM/reference/drive-v1/file/multipart-upload-file-/introduction)，保存 upload ID、分片及完成进度；超过 24 小时有效期停止，完成请求结果未知先核对。 |
| 移动 | [move](https://open.feishu.cn/document/uAjLw4CM/ukTMukTMukTM/reference/drive-v1/file/move)，采用 20 次/分钟的较严限制，真实回读确认之前保持待处理。 |

**操作命令**（在已核对绑定的 worktree 根目录运行）：

```powershell
scripts\run_python.bat -m tools.mirror status
scripts\run_python.bat -m tools.mirror preflight
scripts\run_python.bat -m tools.mirror run --account in_neakasa.global
```

无参数和 `status` 都是本地只读预览。`preflight` 会认证并读取目录元数据，返回 `root_visible` 和 `write_permission=unverified`：可见不等于可上传或移动。`run` 才会冻结、上传和处理移动；兼容旧 `--run`。常驻服务独立执行镜像，原帖入档即可排队，不等待本地化完成。

**遇到 `uncertain` 或 `blocked`：**

1. 保留 `mirror_queue.json`、`mirror_spool/` 及操作 ID、错误码、请求标识。先运行 `status`，再用 `preflight` 读取分页目录和任务元数据。不要删 token、队列或冻结文件来“重试”。
2. 唯一同名目录只是候选，需要核对父目录与意图；多个候选或权限不足继续保留不确定。文件由人工核对远端文件、时间、名称和内容；必要时下载到**隔离目录**，用 `Get-FileHash -Algorithm SHA256` 比较冻结摘要。代码不会从云端下载正文或原图。
3. 明确成功后绑定原操作 ID 和远端 token，文件操作必须提供对应冻结 SHA-256；未创建则填写相同操作的未创建确认及依据：

```powershell
scripts\run_python.bat -m tools.mirror resolve --operation-id OPERATION_ID --sha256 FROZEN_SHA256 --remote-token VERIFIED_TOKEN --note "已核对远端文件与冻结摘要，记录见验收表"
scripts\run_python.bat -m tools.mirror resolve --operation-id OPERATION_ID --sha256 FROZEN_SHA256 --not-created --note "已检查原父目录和操作记录，确认未创建"
```

这两条是二选一的模板，替换为实际值后执行。目录操作不要求文件 SHA；移动成功使用原目录 token。`resolve` 只记本地人工结论，不执行上传，不能和 `--run` 组合。明确未创建后，另行运行 `run` 恢复。计数限流先退避；权限、容量或每日配额先解除阻断；请求结果未知不得直接重发。

**本地与数据库恢复：**

```powershell
scripts\run_python.bat -m tools.layout recover-tags instagram --dry-run
scripts\run_python.bat -m tools.layout recover-tags instagram
scripts\run_python.bat -m tools.layout reindex-db
```

分类移动恢复只处理已记录的意图；损坏或内容冲突保留现场，在副本核对。普通查询不会触发恢复写入。数据库重建只写派生 SQLite；历史入口独立使用 `index-history.sqlite`，首次查询会按当前源文件重建。详情显示媒体保存数及上次数据库核验时间。

**真实验收清单：**

- 人工确认 9222/9224 登录、账号和出口可用后取得至少一篇 Facebook、一篇 Instagram 真实图文，至少一篇多图；不自动登录或滚历史。
- 比较原文、来源平台、目标账号/真实作者/合作方、图片数量和顺序。新帖目录用北京时间，原 ISO 保留；型号别名归一，人工清空仍有效。
- 查询 SQLite 三表，核对帖子身份、分类顺序、媒体路径、尺寸、字节数及 SHA。对已知缺图应明确显示未完整。
- 在验收子目录核对 `月份/产品/单帖/01_原帖[_vN]`、file/folder ID、运营只读可见性；人工下载隔离副本逐项比较 SHA。
- 再次同步不重复；只改分类移动目录且不重传；原文或原图修订产生新版本；缺图补齐和重启补送不冒充已完成，旧版本仍可核查。
- 保存 UTC 测试时间、样本 ID、文件清单/哈希、远端回执与人工核验结论。只有这些企业证据可复核后，才将相应单元从“待真实联调”改为“真实通过”。

## 8. 录制单渠道 Business Suite 证据

**2026-09-14 运行数据整库清空之后，这里一份证据都没有了。** 原来的 `publish_probe_*.json`、`channel_controls.json`、`planner_controls.json` 和月历截图随 `state/` 一起删除，`config.toml` 的 `[publish].ui_probe_dump` 与 `ui_constraints_verified` 也已退回未签字状态。所以 preflight 的 G6/G6c 现在全关——要走到真实发布，下面这套录证得从零做一遍。

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

**那 14 个只能人亲眼量的 UI 上限**（图片数、画幅比、正文与标签上限、定时上下限）录制器证明不了，`preflight` 的第 2 项会逐条列出还差哪些。用 `--set-note KEY=VALUE` 一次填完，未知或畸形的键会当场报错、不会静默丢弃：

```powershell
scripts\run_python.bat -m tools._scaffolding.probe_publish --set-note <KEY>=<实测值>
```

录完之后把 `config.toml` 的 `[publish].ui_probe_dump` 填成新 dump 的文件名，`ui_constraints_verified` 改回 `true`。⛔ 换 dump 就要重新量：这一位签的是"那份 dump 里的观察项有人亲眼看过"，不是"这个项目量过一次"。

本轮已经观察到已发布详情的渠道图标、`Published on` 与合作作者信息，以及两个未来 time-only 条目实际为推荐时段并已排除。实际 inventory 在 2026-09-13T05:01:33Z 返回 ready：8 月 30 日至 10 月 3 日的 35 格、4 条公开帖、3 个独立 IG remote ID；这只覆盖本次已观察月份。只有完成第 9 节具体内容确认后，才把一次 Schedule、成功 dialog、该渠道卡片/全文/时刻/remote ID 的回读录入提交验收；`Publish`/`Publish now` 不用于验证。

清库前做过一次编辑器媒体观察：放入明确不可发布的技术文案与 2 张历史原图，核验了缩略图的数量、顺序与视觉一致性，未点 Schedule / Publish / Finish later / Cancel。**发布浏览器里可能还留着那份草稿**，接手时先识别现场，不要直接提交。缩略图检查只证明编辑器侧准备状态，不替代远端排期卡片里的最终图片证据。

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

两个联调包都不存在：清库把归档和此前准备的 FB 待制作包一起删了。要重新走到这一步，得先回填出有正文和图片的新帖，再逐篇准备。

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

提交最终验收报告前，在全部修改集成后安装锁定依赖、运行 React 测试和构建，再运行隔离测试入口；保存命令、版本与结果。

```powershell
npm.cmd --prefix web/ui ci
npm.cmd --prefix web/ui test
npm.cmd --prefix web/ui run build
scripts\run_python.bat -m tools.test_offline
```

`tools/test_offline.py` 给每个脚本独立的 archive/state/环境和日志——**它不会让测试结果自动变成真实外部系统结果。** 2026-09-14 当前集成版本是 Python 66/66、React 26 个文件共 505 项测试和 TypeScript + Vite 构建通过；构建保留 1.39 MB JavaScript chunk 警告。

完整前端验证入口为：

```powershell
scripts\run_python.bat tests/tests_browser_workflow.py -v
scripts\run_python.bat tests/browser_regression.py --stage ALL
scripts\run_python.bat tests/network_compare.py
scripts\run_python.bat tests/cutover_rehearsal.py
scripts\run_python.bat tests/review_probe.py
scripts\run_python.bat tests/history_thumbnail_cost.py
```

`tests/tests_browser_workflow.py` 的七个迁移场景加日期回归使用实际 dist、临时真实 ASGI 和隔离 archive/state；保存、历史、设置、链接最终计数走临时后端，远端状态用明确的界面响应替代。`network_compare.py` 核对 16 个 React 请求契约，`cutover_rehearsal.py` 默认使用 `web/ui/dist` 和实际 FastAPI，`review_probe.py` 核对密度、图片按需请求和零外部动作。产物统一落在已 gitignore 的 `state/` 下，跑一次回归不会弄脏工作区。

⛔ **不要对绑定真实数据的目录直接运行会写入的测试脚本。**

部署形状另有静态路由测试；浏览器夹具自带的 SPA 回落不能替代它：

```powershell
scripts\run_python.bat tests/tests_spa_static.py
scripts\run_python.bat tests/cutover_rehearsal.py
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

## 13. 更新并启动审校台

项目只维护 `web/ui/` 下的 React + TypeScript 前端。构建输出为 `web/ui/dist/`，`config.toml` 的 `[paths].web_dist` 显式指向该目录；`config.local.toml` 只接续 archive/state，不选择前端。部署契约由 `tests/tests_spa_static.py` 与 `tests/cutover_rehearsal.py` 守住。

⚠️ 清库后 `web/ui/node_modules` 与 `web/ui/dist` 都已删除，**打开页面之前必须先装依赖并构建**：

```powershell
npm --prefix web/ui ci
npm --prefix web/ui run build
```

下面的清单用于每次更新前端后的复验。

从上到下检查。任何一步失败就停止更新并保留报告，不对 archive/state 做恢复操作。

### 集成前检查

- [ ] 锁定依赖安装 PASS — `npm.cmd --prefix web/ui ci`
- [ ] React 单测 PASS — `npm.cmd --prefix web/ui test`
- [ ] React 构建 PASS — `npm.cmd --prefix web/ui run build`
- [ ] Python 全量 PASS — `scripts\run_python.bat tools/test_offline.py`
- [ ] FastAPI 静态演练 PASS — `scripts\run_python.bat tests/cutover_rehearsal.py`
- [ ] `config.toml` 的 `[paths].web_dist` 是 `web/ui/dist`
- [ ] `web/ui/dist/index.html` 存在

### 合并与启动

- [ ] 把开发分支合并到原 `main`
- [ ] 在原主工作区运行 `scripts\run_web.bat`
- [ ] 确认服务监听 `127.0.0.1:8765`

只刷新浏览器不会重新读取构建目录：`DIST` 在 `web/api/app.py` import 时确定。

### 只读检查

每一条都从地址栏直接输入，覆盖服务端深链接回落：

- [ ] `/review`
- [ ] `/history`
- [ ] 一篇活账号详情并在该页刷新
- [ ] 一篇冻结账号详情：确认 `read_only` 且没有写入入口
- [ ] `/calendar`
- [ ] `/settings`
- [ ] `/runtime`
- [ ] 旧链接 `/?task=<真实账号>/<真实帖子>` 跳到 `/review/...`
- [ ] 旧链接 `/?view=history` 跳到 `/history?page=1&limit=50`
- [ ] 浏览器 console 无报错
- [ ] Network 没有异常 404/422
- [ ] 柏林时刻和四个队列计数与实际数据一致

### 低风险写入检查

由你或授权运营手工执行，每步之后回列表核对状态：

- [ ] 编辑德语正文并保存，返回列表后再打开仍正确
- [ ] 修改产品分类并保存
- [ ] 挂起一篇并恢复

### 可选外部只读检查

- [ ] 只有需要刷新 Planner 时才执行一次 calendar refresh；它会打开发布浏览器读取后台，通常需要数十秒

### 经确认的外部写入检查

先核对当前会话是否已明确授权这篇具体帖子、最终文图、目标账号、唯一渠道和柏林时刻。已有这份具体授权就继续，不重复申请；缺少任一项时停在提交前补齐确认。

- [ ] 选一篇明确允许用来测试的帖子和柏林时刻
- [ ] 执行真实 approve
- [ ] 在 Business Suite 确认排期存在
- [ ] 审校台回读为「已排期」
- [ ] 回执必须是 `ok === true && status === 'scheduled'`，不能只因为 HTTP 200 判成功

### 第一个工作日观察

- [ ] 历史归档页缩略图补齐速度；原归档估算首屏约 12–13 秒，见 [HANDOFF §7](HANDOFF.md#7-审校台缩略图实测)，太慢先改成每页 20 条
- [ ] `/runtime` 状态
- [ ] 队列计数与列表局部更新是否一致
- [ ] 记录 409 恢复和运营不清楚下一步的情况

### 出现问题时停止更新并留证

- [ ] 停止当前服务，保存命令、版本、浏览器错误和测试报告
- [ ] 记录 `git rev-parse --short HEAD`、实际 build hash 与 `config.toml` 的 `web_dist`
- [ ] 保存失败页面、console、Network 和对应测试报告
- [ ] 核对 `/api` 404 仍是 JSON、缺失静态资源仍是 404、深链接刷新仍返回应用
- [ ] 保留 archive/state 原样；前端整理没有改变数据格式或写入契约

## 14. 阶段一真实验收：监测与抓取

这一节回答一个问题：**怎么确认「监测到新帖 → 抓取 → 存储 → 飞书告诉我」这条链路在真实环境里真的跑通了。**

按 A→G 顺序做，每一段都有明确的通过判据。任何一段不通过就停在那里，按[第 12 节](#12-卡住时保留什么)留证，不要跳过去做下一段。

⛔ **全程不要加 `--process`。** 它需要先过激活边界，而激活边界要阶段五的 G8 真机验收证据。阶段一的范围止于抓取和存储，翻译与图片是后面几轮的事。

### A. 前置：三样东西必须先到位

- [ ] **三个 Chrome 各自人工登录**（[第 3 节](#3-登录三个专用-chrome)）。阶段一只用到 9222（回填）和 9224（探测），但三个 profile 的隔离断言会一起检查。**9224 此前撞过 429**，先确认账号能正常打开目标主页再往下走。
- [x] **同群四个机器人已建好、地址和各自密钥已填**（[第 2.1 节](#21-配置同群四个机器人)），并且 `notifications --self-test` 在同群收到四张自检卡、真实发送者与卡片中的机器人名称对应。**这一条先过，不然后面分不清"没有新帖"和"通道不通"。**
      → 2026-09-15 用户已在群里收到四个机器人的响应。**这只证明通道可达**，不证明真实探测往返或运营能打开审校链接。换机器、换绑定或换群之后要重做。
- [ ] **本机 `.env` 还要 `IPINFO_TOKEN`**（F1-8 的出口证据）。名称见 `.env.example`。
- [ ] **`config.toml`**：`[feishu].base_url` 填好后把 `enabled` 改成 `true`。缺 `base_url` 加载即失败，不会静默降级。

⛔ **云盘本轮不做**（[REQUIREMENTS §9](REQUIREMENTS.md#9-明确延期与固定边界)），所以这一段**不需要** `FEISHU_APP_ID` / `FEISHU_APP_SECRET`，`[mirror]` 保持 `enabled = false`。代价是本轮没有异地备份，而 `state/published.jsonl` 不可重建——按[第 1 节](#1-接续运行数据前先备份和核验)自己定期拷一份到本机之外。

`base_url` 要填**运营那台机器能打开的地址**。填 `127.0.0.1` 卡片上的「去审校」按钮她点不开，而这一点要到她第一次点的时候才会发现。阶段一还不会产生待审卡，所以这条不阻塞本轮，但监测与落档卡上也有这个按钮。

### B. 只读预检：确认配置真的生效

```powershell
scripts\run_pipeline.bat preflight --json
```

这条命令零写入、不碰 FB/IG。输出里 `stages` 按阶段编号排列，逐项核对：

- [ ] 阶段 4「飞书提醒」的 `outbox.credentials_present` 是 `true`——为 `false` 说明至少一个机器人地址未读到；还应核对 `bot_configuration_valid=true`、`duplicate_bot_targets=false`，实际权限另验
- [ ] 同上 `outbox.bots` 的四个名称齐全，均为 `configured=true`、`valid=true`。同群是预期配置；`duplicate_bot_targets=true` 表示不同阶段复用了同一个机器人地址，需要逐个核对
- [ ] 同上 `outbox.enabled` 是 `true`，`outbox.status` 不是 `configuration_invalid`
- [ ] 阶段 2「源内容与归档」的 `mirror_status` 是 `disabled`——本轮这就是**期望值**，不是缺陷
- [ ] 顶层 `network` 的 `last_error` 是 `null`、`network_type` 不是 `unknown`。**`last_error` 为 `missing_token` 就是 `IPINFO_TOKEN` 没配**，此时 F1-8 拿不到任何证据，而且每天会来一条"出口信息服务暂未返回有效结果"的技术告警
- [ ] `network.network_type` 不是机房段（`hosting`）。是的话先换网络，别开监测——红线 2
- [ ] `network.scope` 是 `python_http_to_ipinfo`：它量的是**本进程**的出口。Chrome 若走另一条线路（代理、分流），这个数字证明不了抓取那一跳，得另行核对

刚配好时 `network.stability` 会是 `insufficient_samples`，属正常——它要攒够几次采样才能判断出口稳不稳。

⚠️ 这一步只证明**配置**成立，不证明任何消息发得出去。

### C. 建立归档基线：回填最近一个月

在 9222 浏览器里打开目标主页，手工向下滚到一个月前，回到终端按 Enter：

```powershell
scripts\run_backfill.bat facebook --days 30
scripts\run_backfill.bat instagram --days 30
```

- [ ] 两个账号都打印了新增篇数，以及「窗口外跳过 N 篇」
- [ ] IG 的合作帖进来了。判据是 `parse.on_timeline_of()` 而不是 `owner == account`，**只看 owner 会把 `.global` 的帖子整批漏掉**
- [ ] 媒体完整性没有大面积 `media_complete: false`

⛔ 回填帖**不会**产生飞书发现卡片——判据是 `post.json` 的 `source_route`，回填是人主动滚出来的历史。这一段收不到推送是正确的，不是通道坏了。

### D. 核对本地存储契约

打开 `archive/` 逐项看：

- [ ] 布局是 `archive/<账号>/posts/<月份>/<产品 tag>/<帖子>/`
- [ ] 型号表命中的落在对应 tag 目录，没命中的落在 `未分类/`，**没有乱猜的分类**
- [ ] 文件夹名是 `2026-09-10_1423_IG_P1-Pro_p1-pro-anniversary-sale` 这个形状：
      北京日期时分 · 平台 · 产品 · 摘要，**四段之外没有别的东西**
- [ ] 月份目录和名字开头的日期一致，且都是**原帖发布的北京时间**，不是抓取时间
- [ ] 帖子目录里有 `post.json`、`text.txt` 和原图
- [ ] 随便挑一篇，`post.json` 的 `media[].local_path` 指向的文件真的存在

⚠️ **改过分类的帖子，名字里的产品会和父目录不一致，以父目录为准。** 名字记的是建档
时的产品，改 tag 只移动目录不改名——改名会让每张图的 `local_path` 一起失效。

然后重建查询索引，确认派生层与文件一致：

```powershell
scripts\run_python.bat -m tools.layout reindex-db
```

- [ ] 重建不报错，条数与归档篇数一致

### E. 飞书通道：先确认通道，再等真实数据

直接等真实新帖来验通道，一旦不通就分不清是"通道坏了"还是"今天没有新帖"。**通道自检已经在[第 2.1 节](#21-配置同群四个机器人)做过**，这里只把它作为一道闸再确认一次：

```powershell
scripts\run_python.bat -m pipeline notifications --self-test
```

- [ ] 同一业务群收到四张「Neakasa 德国站 · 通道自检」卡片，每张标明不同的预期机器人
- [ ] 逐张核对实际发送者：检测、爬取、发布、状态告警各一张；同群正常，重复 webhook 地址会被拒绝
- [ ] `notifications`（不带参数）打印的 `status` 是 `ready`，`counts` 里没有 `retry` / `uncertain`

⚠️ **不要再用"往临时归档里塞一份假 post.json"那套办法。** 两张监测卡的内容来自抓取时逐篇记下的
事实（`state/monitoring_facts.jsonl`），不是遍历归档推导出来的——往归档里放假帖子一张卡也不会产生。
这正是"发现了但没落档"能被看见的原因，代价就是通道形状和卡片内容得分两步验：通道在这里验，
卡片内容留到 G 段用真实帖子验。

### F. 开监测，等第一条真实新帖

```powershell
scripts\run_scheduler.bat --preview
```

- [ ] 打印出四个任务（两平台 × delta/reconcile）和各自的下次触发时刻
- [ ] 兜底时刻落在上海 07:00 附近、带抖动，且早于 08:00 截止点

确认无误后启动常驻：

```powershell
scripts\run_scheduler.bat --run
```

在岗窗每小时一次、离岗窗每三小时一次，各带 ±25% 抖动。让它跑着，然后等美国站发新帖。

**这一段的等待时间不可控**，历史样本是 5.9 篇/周。等待期间可以确认这些：

- [ ] 进程没退出，`state\scheduler.json` 的 `last_tick` 在往前走
- [ ] 探测号会话没失效——失效会由状态告警机器人发一条「监测未完成」，这本身也是通道可用的证明
- [ ] 每轮输出里有分类跳过数（视频 x / 混合 y / 无正文 z）。「今天没有新帖」和「今天发了 8 条全是视频」必须长得不一样

### G. 收到那两张真实卡片之后

这是阶段一的验收时刻。**一轮扫描发现新帖就该有两张卡，先「监测到新帖」再「原帖抓取完成」。**

监测卡（发现）：

- [ ] 检测机器人在本轮抓取事实形成后几分钟内发送，离岗时也不等次日 08:00
- [ ] 逐篇列出 post_id、原帖时刻（**上海**，和卡片顶部那一行同一时区）、图片/视频数、正文首行
- [ ] 合作帖标出了「合作帖，原作者 X」和「合作方 Y」，没有被当成本账号原创
- [ ] 「查看原帖」点开就是那一篇
- [ ] 本轮跳过数按四类中文标签列出（视频 / 图文混合 / 无媒体 / 无正文），不是英文键名

落档卡（成败）——由「新帖爬取推送机器人」发送，逐篇说明成败：

- [ ] 头一行是「发现 N 篇，成功落档 M 篇，失败 K 篇」（全成功时写「没有失败」）
- [ ] 每篇都带一个明确状态词：`已落档` 或 `未落档`
- [ ] `已落档` 的那几行给出实际图/视频数和归档落点（`posts/<月份>/<产品 tag>/<帖子>`），与本地 `archive/` 下真实存在的目录一致
- [ ] 数字对得上：发现数 = 成功 + 失败，没有哪一篇在两张卡之间消失
- [ ] 如果有失败，卡片上有「原图链接有时效，不能假设下一轮还能补回来」这句，而不是只报个数

⛔ **两张卡的篇数不一致是要查的信号，不是显示问题。** 发现 3 篇只落档 2 篇意味着有一篇的原图没拿到，
而原图 CDN URL 带签名且有时效——下一轮不一定还能补回来。

本地落点（云盘本轮不做，没有可比对的远端）：

- [ ] `archive/` 下出现对应帖子文件夹，落在正确的月份和 tag 下
- [ ] 文件夹名是四段：`<北京日期_时分>_<IG|FB>_<产品>_<摘要>`，没有看不懂的数字尾巴。
      没匹配上型号表的帖子没有产品段，落在 `未分类/` 下；没正文的帖子没有摘要段
- [ ] 名字里的日期时分是**原帖发布的北京时间**，和卡片上那一行对得上

再把两个容易被忽略的行为确认一遍：

- [ ] 同一轮扫描不会推出重复的卡片（`event_id` 绑扫描开始时刻）
- [ ] 在岗窗之外发布的帖子**照样立刻推**（两张监测卡都不受静默窗限制）
- [ ] 检测与爬取卡由各自机器人发送；状态告警机器人只收约定的四类状态消息，发布机器人只收待审与排期成功

### 这一轮能证明什么、不能证明什么

| 做完之后成立 | **仍然不成立** |
|---|---|
| 监测能发现真实新帖并抓取归档 | 翻译、图片德语化、审校、排期任何一段 |
| 本地三层布局与业务可读的文件夹名 | 云盘镜像——本轮明确延期，没有任何远端证据 |
| — | 长期稳定性——封号风险的反馈是延迟的，且只反馈一次 |
| 同群四个机器人分工正确、卡片可点达，且发现与落档数字对得上 | 运营完整流程（她从卡片进去审完再排期） |
| 出口 ASN 类型与稳定性有据可查 | 兜底对账真的补到过漏帖（要等一次真实漏帖） |

验收报告里写清楚：跑了什么、哪几条是真实账号的结果、哪些外部依赖仍未联调。离线测试通过不能替代上面任何一项。
