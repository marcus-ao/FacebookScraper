# 人工操作指南

**本周先按[第 15 节](#15-服务机部署与日常更新)把服务机装起来，再在服务机上按[第 14 节](#14-阶段一真实验收监测与抓取)做阶段一（监测与抓取）的真实验收。** 1–13 节是分主题的长期参考；第 14 节把与阶段一相关的挑出来排成一条可以照着走的线，第 15 节是一次性的装机顺序和日常更新动作。

本文件列人工依赖与操作顺序（2026-09-15 起开发机与服务机分开，分工见第 15 节），存储说明同步至 2026-09-15。**原主工作区的 `archive/` 与 `state/` 是实际业务数据**，审校写入会落到真实账本。业务规则看 [FUNCTIONALITY.md](FUNCTIONALITY.md)，每个验收单元的状态看 [REQUIREMENTS §10](REQUIREMENTS.md#10-五阶段验收状态)，证据边界看 [HANDOFF.md](HANDOFF.md)。五阶段与八批工作已接受，不重复申请普通文件修改/离线验证权限。

当前 Stage 1 worktree 的 archive 为空，state 只有隔离测试报告，`.env` 是占位符；没有复制真实凭据，也没有运行真实平台或飞书请求。2026-09-14 删除了旧 429 证据，不能把历史观察当成当前持久停机状态。9223 的 Business Suite 观察和后续发布约束继续按第 8–10 节执行。

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

在各自 Chrome 人工登录或完成二次验证，核对 profile 路径、端口和身份。进程已启动不表示会话有效；实际平台返回登录、checkpoint、challenge、401/403/429 时按共享访问控制立即停机并保留证据。

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

只有看到明确账号、篇数、预计费用和许可后再执行付费命令。第三方来源没有当前许可时不要运行。每张图最多受理三次优化，**失败的那次也算一次**（在受理入口计数）。生成过的版本在审校台图片页可以比较后换回去，换版不调用模型也不计费。

### 5.1 切换图片模型前先自检

`[image].model` 可选 `gpt-image-2` 与 `gpt-image-2.5`。改之前做两件事，顺序不能反：

1. **把 `[image].cost_rates_usd_per_million` 里该模型的三个费率改成网关价目表的真实值。** 仓库里 `gpt-image-2.5` 的值是照 2 抄的占位值，不改就会让账本里的估算一直偏。
2. 跑一次自检，确认网关确实提供这个模型、返回尺寸与请求一致：

```powershell
scripts\run_python.bat -m localize.images --check
```

⚠️ **`SIZE_STEP`、像素上下限、最大边长和 3:1 这组尺寸契约是按 gpt-image-2 标定的，2.5 没有实测。** 自检只验证 816×816 可解码、返回尺寸及 usage 结构；通过不证明其它尺寸边界也兼容。自检不过就不要切，通过后再用经确认的业务样本核对实际请求尺寸和费用。

### 5.2 标定「图内文字到底改没改」的告警线

每张德语图都会记录 `changed_pixel_ratio`（改动像素占比）。它忽略最大通道差不超过 24 的像素，因此 0 只表示“未检测到明显像素变化”，不证明一个像素都没改，也不证明未翻译。没有英文、模型未按要求处理、低对比度文字改动均可能得到 0。这里仅提示；先放大核对文字，再决定是否花费优化次数。

`[image].change_ratio_warn` 是"改得太少"的告警线，当前是 `-1`（关闭）。⚠️ **不要凭感觉填。** 合成样本上实测过：JPEG q60 重编码的噪声占比 0.079%，而一次真实改写文字只有 0.062%——两者会重叠，阈值只能用真实产出标定。

首批真实出图之后这样取数：

```powershell
scripts\run_python.bat -c "import json,pathlib;print(sorted(json.loads(l)['changed_pixel_ratio'] for l in pathlib.Path(r'<账号目录>/images_de.jsonl').read_text(encoding='utf-8').splitlines() if l.strip() and 'changed_pixel_ratio' in l))"
```

把"确实译过的图"和"人眼确认没译的图"两组分开看，取两组之间的空档；两组重叠就继续保持 `-1`，不要挑一个中间值。

### 5.3 业务自己换图

业务在审校台图片页点击“下载本篇素材”，在外部编辑后上传替换某一张（JPEG / PNG / WebP，最大 32 MB）。替换之后：

- 这一张按**人工图优先**使用，程序不会再重生成覆盖它，该张的模型优化入口也会停用并写明原因；
- 这篇记为 **edited**，继续留在系统里走排期发布——上传是换素材，不是转交人工；
- 被替换掉的旧图保留在该帖 `media_de/superseded/`，付费产出不销毁；写入失败时当前图保持可用；
- 人工图标记、替换时间、画幅偏差提示会显示在当前图旁，连续上传后应看到新的图片；
- `media_de/manual_uploads.jsonl` 保存明确的人工选择，备份/迁移时连同图片保存，不要单独删除；否则上传历史生成图的人工决定会丢失；
- 历史版本先预览后采用，免费换版不改变日/月费用统计。已换人工图时历史版本只供查看，不允许报告成功却未生效的切换；
- 要"下载素材但不转交"时用下载按钮；"转交人工、系统不再代发"是另一个按钮，它会把这篇推进 `handed_off` 终态。

若任务长时间 pending/running，在审校台查看同一 job 的拥有进程、更新时间、request ID 和恢复分类。用恢复入口核对当前 job 版本；它本身不重发模型请求。仍执行的不能抢占；未请求/已落盘/可能计费分别处理，可能计费先人工查供应商。源文/原图/提示词变化后旧候选不能继续应用，保留人工文案/图。不可重复点击或删账本绕过三次受理上限。

中断的自动批次还要核对运行状态中的 batch_id、state_revision、operation_id、paid request 与总费用。确认落盘结果和供应商状态后，使用当前版本进行“已核对结果”恢复；CLI 对应 `recover-processing --batch-id <id> --version <state_revision> --outputs-reviewed`。版本冲突则重读，恢复不自动重新发起模型请求。

翻译提示词当前为版本 7，优化建议提示词为版本 2。`stale=false` 只说明原文没变；还需看 `machine_current`
和机器/当前提示词版本，同时单独核对每张图。旧机器译文或建议的版本失效不授权自动付费重做；
历史冻结账号的素材不能替代新账号素材，也不能从提示词版本差异推断已有合格德语图。

风险需区分未扫、失败、成功零风险，真实扫描绑定源文/提示词，发生在翻译前。标签需分别核对 Trends DE 同英文标签候选组/同时间公开 CSV、IG 全球累计与德语同类账号周更；空名单跳过、失败/过期可手选。当前 429 下不继续 IG 或 Trends 采样。

Trends 人工确认访问恢复后，先取只读版本，再按实际核对理由恢复；`--version` 必填，恢复本身不导出：

```powershell
scripts\run_python.bat -m tools.hashtag_sampling trends-status
scripts\run_python.bat -m tools.hashtag_sampling trends-reset --reason "已人工核对访问恢复" --version <revision>
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

**审校台按平台分了两个入口**：Facebook 待审、Instagram 待审。两边的链接、标签和长度上限规则不同，
一次只面对一套。历史归档仍然跨平台检索。

逐篇检查英文/德文、图片、标签、链接、作者/合作方和来源版本。保存、挂起、不发、资源下载和人工接管会写真实追加式账本。

正文区的「复制发布文案」给的是最终成品——正文加链接或引导话术加标签，就是要贴进 Business Suite 的那一份。
校验还没回来时按钮是灰的：那会儿只有近似值，复制出一份和实际发布不一致的文案比没有这个按钮更糟。

「优化建议」是可选的一步，模型逐条指出可以改的地方，**采纳与否你定**；它不会动任何译文，
改完仍要自己点保存。译文改过之后旧建议会标失效并停用「采用」，需要就重新生成。

生成建议审的是点击时编辑区中的正文，尚未保存的修改也会带上；请求不代为保存。
后台等待期间继续改正文会使返回的旧建议失效；采用一条后也要重新核对剩余建议。
忽略只作用于这一轮，重新生成后相同片段的建议仍会展示。复制受浏览器限制时会打开完整文案供手动复制。

⚠️ **本轮标签没有热度推荐**（[REQUIREMENTS §9](REQUIREMENTS.md#9-明确延期与固定边界)），标签区只做人工选取与编辑。

Facebook 在链接区确定德国落地页后，可把对应 `{{linkN}}` 插入正文当前光标处；最终预览/计数会换成 URL，未插入的有效链接仍追加末尾。无效编号、缺目标或损坏占位符先修正，Instagram 使用 bio 话术。编辑中的“约”是临时估算，等待 `/check` 返回当前完整草稿的最终计数，包含标签、实际链接或 CTA；不以占位符短长度判断是否可发。

风险夹具只供明确演示模式，不能据此批准真实内容；正式页面读取实际风险状态和来源。采样降级时仍可人工决定，不把缺数据写成热度为零或已扫描安全。

从历史入口检查服务端分页、平台/月/tag/状态筛选、总数及 90 天外详情，冻结 `.tech` 只读；查询不会启动翻译。设置页只改默认柏林时刻与挂起工作日数，保存需版本校验；其它配置/模板只读并保留说明。五阶段运行状态显示真实激活、进程/处理、429、飞书凭据、镜像和月历状态，查看状态不触发外部操作。源发帖至首次就绪和批次处理时效分别显示，前者包含发现等待；没有历史就绪事实时不期待补出统计数字。

遇到 409 版本冲突时刷新后重做决定。遇到账本解析错误时停下来保留文件；不要删掉坏行让界面继续。

### 6.1 上线前的一个业务前置：IG 的 bio 聚合页

⛔ **这是业务要先建的东西，不是系统功能**，`[publish].ig_bio_url` 现在是空的，链接区显示"未配置"。

Instagram 文案里的链接点不动，运营靠一句"详见主页"把人引到账号主页。所以主页那个位置
**必须是一个稳定的聚合页**（Linktree，或 `de.neakasa.com` 上一个入口页），不能是某一篇帖子的落地页。

理由是它和定时发帖硬矛盾：帖子排到 9/15 10:00 自动发出，文案写着"Link in Bio"——
**谁在那个时刻去把 bio 改成这一篇对应的落地页？** 要让每篇都对，就得有人守在每个发布时刻改 bio，
那等于把定时发布的价值抵消掉。

建好之后把地址填进 `config.toml` 的 `[publish].ig_bio_url`。系统只在链接区**只读显示**它，
不会自动改 bio，也不会提醒你去改。

## 7. 飞书群与云盘真实联调

卡片/聚合/恢复代码已有离线验证。第 2.1 节需人工核对四张自检卡的真实发送者；HTTP 成功只证明接口接受请求，不能独自证明机器人对应、业务内容或运营能否打开链接。逐条验证：

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

- 人工确认 9222/9224 登录、账号可用后取得至少一篇 Facebook、一篇 Instagram 真实图文，至少一篇多图；不自动登录或滚历史。
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

本节用于未接入部署控制器的独立检出。受管服务机按[第 15 节](#15-服务机部署与日常更新)安装唯一控制器任务，通过 `deployment mode` 管理调度，不并行安装这里的旧任务。

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
scripts\run_python.bat tests/cutover_rehearsal.py
scripts\run_python.bat tests/review_probe.py
scripts\run_python.bat tests/history_thumbnail_cost.py
```


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

## 14. 阶段一真实验收：监测与原帖抓取

本节是阶段一唯一的真实启用顺序。全程不加 `--process`，不触发模型、标签、审校唤醒、月历、云盘镜像或发布。不得自动登录或自动滚历史。

**整段在服务机上做**（装机顺序见[第 15 节](#15-服务机部署与日常更新)）。验收证据只对产生它的那台机器成立，开发机上跑过的不算数。

### A. 准备与访问初始化

⛔ **全程不要加 `--process`。** 它需要先过激活边界，而激活边界要阶段五的 G8 真机验收证据。阶段一的范围止于监测与抓取，翻译与图片是后面几轮的事。

1. 人工登录 9222（回填）、9224（探测）、9223（发布）三个独立 profile，核对身份。阶段一只让 9222/9224 访问源站。
2. 配置同群 detect、capture、publish、alert 四个机器人及运营要使用的 `[feishu].base_url`。base_url 只校验 URL 格式。
   → 2026-09-15 在**开发机**上收到过四个机器人的响应，只证明通道可达。这一轮改在服务机上运行，**这一条要在服务机上重做**，那次不算数。
3. 离线执行 `scripts\run_pipeline.bat preflight --json`、`scripts\run_python.bat -m routes.delta --status`、`scripts\run_scheduler.bat --preview`。`preflight --json` 与 `GET /api/runtime` 应显示 monitoring state/status/pause/quota/next_due/capture_revision/items/baselines；读取不访问平台或发消息。
4. 人工核对 9224 身份后初始化：
```powershell
scripts\run_python.bat -m routes.delta --initialize-access --reason "人工核对 profile 身份，允许阶段一受控探测"
```

缺初始化或坏状态时真实导航必须失败闭合。

### B. 人工回填与独立基线
人在 9222 分别打开 FB `neakasaofficial`、IG `neakasa.global`，手工滚到 30 天前：

```powershell
scripts\run_backfill.bat facebook --days 30
scripts\run_backfill.bat instagram --days 30
```

核对独立数量、最早/最新时间、实际 owner/coauthors、媒体顺序与完整性。视频、混合、无媒体、无正文也归档计数。historical 只汇总，不逐帖推群。

⚠️ **IG 的合作帖判据是 `parse.on_timeline_of()`，不是 `owner == account`。** 只比 owner 会把 `.global` 的帖子整批漏掉——这是 2026-09-11 真实发生过的，263 篇合作帖静默丢失。

确认覆盖后另行执行：
```powershell
scripts\run_python.bat -m routes.delta --initialize-baseline --reason "人工核对 FB/IG 最近 30 天回填完整"
```

访问初始化和基线初始化是两步；基线写 `capture_state.json.baselines` 与 monitoring enabled_at，不创建或改写发布激活边界。

### C. 预览、扫描与 72 小时试运行

预览核对每天含周末：08–19 点间隔 45–75 分钟，19–08 点 135–225 分钟；晨间 06:30–07:30 随机 2–4 屏；08:00 首轮在 08:00–08:15 且距上次主页至少 45 分钟。预览离线。随后先单轮核对，再启动常驻试运行：

```powershell
scripts\run_scheduler.bat --run --once
scripts\run_scheduler.bat --run
```

常驻命令保持运行至少 72 小时，不加 `--process`。

普通轮次只开主页首屏、不滚动，每平台最多 300 秒。所有 GraphQL/feed 候选先持久化再下载。仅来源媒体不完整者可打开一次匹配详情，不滚动、不翻轮播、不重放 GraphQL。真实浏览器 dry-run 也写停机和配额。

连续观察至少 72 小时，覆盖 08:00、19:00、晨间和周末；不足则延长。记录平台、计划/实际时刻、skip reason、主页/详情配额、pause/hard-stop、候选/归档/卡片。核对 scheduler、CLI、`--if-stale` 共用 `next_due`；19:00 不重抽；重启不追补；普通/晨间不足 45 分钟合并；锁忙（退出码 75）不推进；统计不改变间隔。

主页不超过 24 次/平台/滚动 24 小时；详情不超过 1 次/帖/扫描、3 次/平台/扫描、12 次/平台/滚动 24 小时。401/403/429 或登录/checkpoint/challenge 立即全 profile 停机且不再导航、滚动、下载；CDN 过期 403 仅单项失败，CDN 429 全局停机；三次普通失败暂停平台。失败、超额、超时立即待人工，普通扫描不重试。

### D. 核对事实与卡片

核对类别 new/historical/source_updated/recovered/time_unknown；原文、元数据、owner/coauthors、`items[key].source.media` 和媒体线索齐全。`source_media_complete` 与 `media_complete` 分开；后者要求每张静态图全图解码、SHA 和原子落盘。IG 重复封面/视频缩略图不计图片；每媒体一个尺寸、顺序不变；未知总数明确 unknown。revision 只由正文与有序实际媒体 SHA 改变。

detect 每平台/扫描至多一张摘要，零新增不发。capture 对每个合格候选恰好一张卡，包含完整/部分完成/失败/待人工、类别、平台、账号、owner/coauthors、三个北京时间、标签、英文前 300 字符与截断、正文状态、验证图片数/已知总数、安全原因和下一步。主按钮按归档状态去 `/history/{account_dir}/{post_id}` 或 `/runtime?capture={key}`，次按钮到源帖。卡片无缩略图，不声称进入本地化队列。

消息异步入 durable outbox，不阻塞下载；重启补入已持久候选漏掉的意图。未知结果人工核对，不自动重发。FB 多图顺序、IG carousel 顺序、四机器人真实发送者/链接、自然新帖分别留真实证据；无自然新帖时平台捕获仍待真实联调。

### E. CAS 恢复与证据

先读取 revision，人工核对后执行：

```powershell
scripts\run_python.bat -m routes.delta --recover-access --expected-revision <N> --reason "具体核对原因"
scripts\run_python.bat -m routes.delta --recover-post PLATFORM:ACCOUNT:POST_ID --expected-revision <N> --reason "具体失败原因"
```

访问恢复不清配额/历史；单帖恢复只尝试一次并服从同一护栏。Web `POST /api/runtime/capture/recover` 含 key/version/reason。`monitor/recover` 含 version/reason 与可选 platform，且不访问平台。版本冲突先重读。

本地落点也要核（云盘本轮不做，没有可比对的远端）：

- [ ] `archive/` 下出现对应帖子文件夹，落在正确的月份和 tag 下
- [ ] 文件夹名是四段：`<北京日期_时分>_<IG|FB>_<产品>_<摘要>`，没有看不懂的数字尾巴。
      没匹配上型号表的帖子没有产品段，落在 `未分类/` 下；没正文的帖子没有摘要段
- [ ] 名字里的日期时分是**原帖发布的北京时间**，和卡片上那一行对得上

报告保存命令、版本、真实/夹具来源、平台/账号、访问与基线 revision、计划/实际时刻、配额、类别、三个时间、owner/coauthors、两层 completeness、图片 SHA/顺序、delivery 状态和未联调项。离线夹具、无自然新帖、HTTP 接受或已删除的旧 429 记录不能写成真实通过。

### 这一轮能证明什么、不能证明什么

| 做完之后成立 | **仍然不成立** |
|---|---|
| 监测能发现真实新帖并在同一轮抓取归档 | 翻译、图片德语化、审校、排期任何一段 |
| 本地三层布局与业务可读的文件夹名 | 云盘镜像——本轮明确延期，没有任何远端证据 |
| 共享访问预算、硬停机与配额在真实往返里成立 | 长期稳定性——封号风险的反馈是延迟的，且只反馈一次 |
| 同群四个机器人分工正确、逐帖卡链接可点达 | 运营完整流程（她从卡片进去审完再排期） |
| 详情补齐与媒体校验在真实响应上成立 | 晨间轮次真的补到过漏帖（要等一次真实漏帖） |

验收报告里写清楚：跑了什么、哪几条是真实账号的结果、哪些外部依赖仍未联调。离线测试通过不能替代上面任何一项。

## 14.1 打开内容处理：抓到就翻译和出图

阶段一固定不带 `--process`。这一节是把它打开的顺序。**它是本轮第一次让非开发者触发付费调用**，
所以前面每一条都是硬前置，缺一条就停在那里。

### A. 四条硬前置

| 前置 | 怎么确认 | 缺了会怎样 |
|---|---|---|
| 阶段一真实验收已通过 | 第 14 节走完，归档里有真实新帖 | 归档是空的，没有东西可翻 |
| 激活边界已设 | [第 10 节](#10-激活边界) | `Runtime` 直接抛「流水线尚未激活」 |
| 本机 `.env` 有 `DEEPSEEK_API_KEY` 与 `IMAGE_API_KEY` | [第 2 节](#2-配置本机凭据) | 失败闭合，不会制造"已向远端发出"的假账 |
| `[pipeline]` 的日/月预算是业务认可的数 | 当前 `monthly_budget_usd = 60`、`daily_budget_usd = 5` | 超预算停止，中途停在一半 |

### B. 先看清楚这一轮要花什么

三条都是**只读**的：零网络、零费用、零写盘。业务点下去之前能看到的就是这些。

```powershell
scripts\run_pipeline.bat processing-preview
```

逐帖列出平台、账号、`source_ref`、文案动作、待生成图片索引/张数、排除或阻塞理由及美元参考。
复用自动处理的激活边界、来源许可、人工稿、审校/发布状态和图片规划；没有译文的新帖也会列出翻译成功后要生成的图片。
已有人工图保留；已有程序图因尚未生成的新译文而可能失效时，显示数量区间并单列待核对索引，不当作必然重做。
可加 `--account fa_neakasaofficial` 或 `--account in_neakasa.global` 限定平台账号，预算仍核算全账号；`--json` 可保存预览快照供核对。

```powershell
scripts\run_pipeline.bat status
```

逐账号显示归档与各阶段积压。**积压不是本次处理作用域**：历史帖、未获许可的合作帖或人工保留项都可能在积压中；确认付费范围以上面的 `processing-preview` 为准。

```powershell
scripts\run_scheduler.bat --preview
```

只看下次到期时刻，不访问浏览器。处理预览只读取已经落地的归档，不能预知下次真实扫描新发现的帖子；新帖落地后应重看处理预览。

美元参考小计包括文案基础估值与图片历史估值。文案按当前提示词/正文粗估 token、德语可见输出为英文的 1.25 倍、缓存全未命中计算，**不含 thinking 的 reasoning 输出**；英文风险预扫是额外付费请求，费用未知，单列次数且不计入小计。
`gpt-image-2` 图片沿用 2026-09-11 的 US$0.211/张历史参考（当时约 2.5 分钟/张），**不是当前保证、完整账单或预算上界**。
切换到 `gpt-image-2.5` 时预览不沿用这个单价，待出图费用与参考小计显示未知；模型真实费率仍按 §5.1 核验。
日/月预算及已有费用另行展示，实际请求逐次核账，预算或失败可使批次提前停止。
预览不验证凭据、模型、浏览器或外部服务；真实跑完之后用当前账单和耗时补充真实联调证据。

### C. 打开

```powershell
scripts\run_scheduler.bat --run --process --once
```

`--process` 必须与 `--run` 一起给。`--once` 只扫描当前到期的一轮，并等待已启动的翻译/出图批次完成，
再汇总待审或失败通知、等待当轮投递结束后退出；不会为等出图再扫描一轮。离岗静默规则仍生效，
应静默的卡片保留到下一次在岗投递。首次验收先用单轮模式，确认结果后再常驻。

扫描发现内容后，同一轮里依次做：英文风险预扫描 → 翻译 → 逐张出图 → 进审校队列 → 发布机器人推待审卡。
每个付费请求后按刚落盘的 usage 重算预算；算不出费用就停。

已完整入库的图文按逐帖采集结果进入处理队列：同轮其它帖抓取失败不阻止它，人工恢复成功后也会在下一次
维护时入队。只有启用 `--process`、激活之后且属于当前账号的来源进入此路径；不完整素材仍交人处理。
入队回执与批次原子保存，飞书确认、重复维护或进程重启不会重新受理同一结果；不确定付费批次仍须先核账。
恢复操作只结转旧批次，不隐含启动付费任务。中断期间已入队的后继结果保留，等待新的处理请求；
重复读取同一采集结果不算新请求。

### D. 通过判据

| 做完之后成立 | **仍然不成立** |
|---|---|
| 真实新帖在同一轮里产出德语文案与德语图 | 文案质量——那要业务看过"出片"效果才知道 |
| 付费账本有 request ID、用量和费用，且对得上供应商账单 | 长期费用曲线——一轮不代表一个月 |
| 处理失败时已生成的内容保留，不重复扣费 | 中断恢复——要真的中断过一次才算验过 |

⛔ **付费调用不可撤销。** 第一次真实跑之前先用 B 段的三条确认作用域；不确定就先别跑。

## 15. 服务机部署与日常更新

服务机使用 **Actions 构建 → 本机主动获取 → 空闲切换**。开发机负责代码与隔离测试；服务机是唯一真实运行业务的机器。自动部署的验收与真实抓取、付费和发布验收分别记录，见 [REQUIREMENTS §10.7](REQUIREMENTS.md#107-服务机自动部署)。

### 15.1 服务机与账户

使用 Windows x64、Python **3.12.9**、系统 Chrome；Node **24.12.0** 只用于 CI/开发机构建，服务机日常更新无需 Node、Git 或访问 PyPI。服务机须可出站访问 GitHub API 和 Actions 制品域名，并保持接电、不休眠、足够磁盘空间。

所有任务、Python 和三个 Chrome profile 都由**运营账户**运行。控制器计划任务使用 `InteractiveToken`、最小权限和登录触发；Windows 重启后尚无人登录时不保证业务运行。技术账户不能替代运营账户的 Chrome 会话。

### 15.2 首次安装已验证制品

首次安装与真实业务启用分开进行。先在 GitHub 的 `Windows release` 工作流中核对：仓库 `marcus-ao/FacebookScraper`、分支 `main`、当前提交 SHA、全部检查成功；下载该次 `fbscraper-windows` 制品，解压到临时目录。不要从来源不明的 ZIP 启动安装程序。

以下路径仅为示例，在运营账户的 PowerShell 中执行：

```powershell
Set-Location D:\Downloads\fbscraper-windows
py -3.12 -m deployment install --root D:\FacebookScraperService --release D:\Downloads\fbscraper-windows
```

安装器检查 Python 版本、包哈希、前后端指纹和旧计划任务冲突，在**最终目录**创建两个独立虚拟环境，并从包内 wheel 安装。临时业务数据上的 Web 验证成功才完成安装。目标必须为空；安装器不会覆盖已有业务目录。失败时保留现场，查明原因后处理安装目录，不能把未完成安装误当成可运行实例。

```text
D:\FacebookScraperService\
  controller\              固定控制器与独立 .venv
  releases\<sha>\          程序、dist、.venv、config.local.toml
  shared\archive\          原帖、原图、人工稿与人工图片
  shared\state\            发布/付费/审校及运行状态
  shared\.env              业务凭据
  shared\instance.json     业务实例标识
  control\                 部署记录、维护会话和 GitHub 凭据
  logs\                    每个受管进程的日志
```

各版本由安装器生成绝对路径绑定；Web、调度器和受管 CLI 使用同一 `shared`。缺失绑定或实例标识不一致时拒绝启动，不回落到版本内的空数据。不得手改标识掩盖目录错误。

### 15.3 配置凭据与初始数据

按 `.env.example` 填写 `shared\.env`。GitHub 的仓库范围细粒度凭据只需 `Actions: read`、`Contents: read`，写入 `control\github.token` 一行；不放进业务 `.env`、Git、日志或聊天。安装器为 token 文件设置运营账户 ACL；按组织政策保管和轮换。认证失败时保留当前服务。

默认新建空业务实例，调度与付费处理均关闭。开发机和服务机各自的测试数据不能混入生产账本。若要接续已经存在的真实业务数据，先停止其写入，按[第 1 节](#1-接续运行数据前先备份和核验)完整备份核验，再由技术人员在新实例首次启动前接续完整 `archive/state` 与必要凭据；核对全部路径及实例归属。不得只搬部分 JSONL，不能覆盖已有运行中的 `shared`，也不自动合并两份账本。

两个运营可编辑偏好保存在 `shared\state\operator_preferences.json`：默认柏林排期时刻与挂起工作日数。其余配置、提示词和业务规则由已验证版本交付；不要在服务机直接改 `releases\<sha>\config.toml`。

### 15.4 注册控制器与首次启动

先处理旧 `FBScraperScheduler`、`FBScraperDelta`、`FBScraperDeltaCatchup` 任务。确认旧业务空闲并正常退出后，人工停用旧启动机制；不使用 `schtasks /End` 中断长任务。安装和启动控制器遇到启用的旧任务会拒绝继续。

```powershell
Set-Location D:\FacebookScraperService\controller
.venv\Scripts\python.exe -m deployment install-task --root D:\FacebookScraperService --dry-run
.venv\Scripts\python.exe -m deployment install-task --root D:\FacebookScraperService
.venv\Scripts\python.exe -m deployment supervise --root D:\FacebookScraperService
```

核对 XML 的运营账户、`InteractiveToken`、`LeastPrivilege`、固定 controller 工作目录、单实例规则。最后一条可用于前台观察；计划任务与手动重复启动受到控制器锁保护。首次成功启动只开放 Web，默认不开启真实调度或 `--process`。

访问本机审校台及 `/api/health`，核对 `deployment_ready=true`、运行 SHA、前后端指纹和实例 ID。控制器自身及 CLI 的入口始终在 `controller`，不要跳到某个旧版本目录运行。

### 15.5 受管业务命令与人工登录

既有手册中的 Python 命令通过控制器 `exec` 执行，例如：

```powershell
.venv\Scripts\python.exe -m deployment exec --root D:\FacebookScraperService -- -m pipeline preflight --json
.venv\Scripts\python.exe -m deployment exec --root D:\FacebookScraperService -- -m tools.start_chrome_detect
.venv\Scripts\python.exe -m deployment exec --root D:\FacebookScraperService -- -m tools.start_chrome
.venv\Scripts\python.exe -m deployment exec --root D:\FacebookScraperService -- -m tools.start_chrome_publish
```

`exec` 使用实际当前版本、同一业务绑定，并将完整命令生命周期登记为在途工作。三个 Chrome 仍须人工登录，不搬开发机 profile、不混用 9222/9223/9224。Chrome 不属于更新时退出的进程树。`preflight` 返回 0 只表示状态查询完成，仍须逐项看业务前置。

飞书自检、访问初始化、真实采集、模型和发布照各节的人工授权要求进行。例如 `exec ... -- -m pipeline notifications --self-test` 会真实发送消息，不属于部署健康检查。

### 15.6 授权调度与内容处理

完成[阶段一验收](#14-阶段一真实验收监测与原帖抓取)后，显式设置模式：

```powershell
.venv\Scripts\python.exe -m deployment mode --root D:\FacebookScraperService --scheduler on
```

只有另行满足[第 14.1 节](#141-打开内容处理抓到就翻译和出图)的付费许可、预算和激活要求后，才执行：

```powershell
.venv\Scripts\python.exe -m deployment mode --root D:\FacebookScraperService --scheduler on --process on
```

模式变化同样等待空闲并经过维护切换；控制器不会随代码更新自动加上 `--process`。关闭处理可用 `--process off`；关闭调度同时指定 `--scheduler off --process off`。保留模式指令与实际运行模式分别展示，失败模式须核对后显式重试。

### 15.7 日常自动更新

服务机每 60 秒核对当前 main 的成功工作流与制品身份。下载、哈希校验、依赖安装和隔离预检发生在旧版本运行期间。候选准备好后等待后台工作及编辑结束，页面预告 60 秒；运营可点击“延后 30 分钟”。持续等待超过 30 分钟通知一次。

未保存草稿、上传和正在执行或排队的任务都会阻止切换。失联的脏会话不会因超时自动忽略；先恢复原标签页保存或明确放弃内容。确认原编辑确已不存在后，技术人员才能用 `clear-session` 解除其阻塞。

维护期间旧进程自行退出，新版本使用同一数据启动；部署验证最长默认 90 秒，检查通过才恢复业务。正常切换约 1–2 分钟是目标，须在服务机实测；长模型请求没有强杀截止。旧页面的写请求会收到明确版本冲突并保留草稿。

只改文档/测试且运行指纹相同的提交记录为已检查，无需重启；“实际运行 SHA”与“已检查 SHA”不能混写。网络错误、CI 失败、过期包或摘要不符保留当前服务；失败候选不循环安装，须新版本或显式 `retry`。

开发变更若涉及真相源格式，必须同步改变发布包兼容声明，交由技术维护进行备份、迁移与验收；不能保留旧 `truth_contract` 来让迁移混入自动更新。固定控制器及其依赖代码指纹变化也会拒绝自动切换。单纯兼容的派生索引变化仍需离线重建验证。

### 15.8 状态、暂停、重试与回退

```powershell
.venv\Scripts\python.exe -m deployment status --root D:\FacebookScraperService
.venv\Scripts\python.exe -m deployment pause --root D:\FacebookScraperService
.venv\Scripts\python.exe -m deployment resume --root D:\FacebookScraperService
.venv\Scripts\python.exe -m deployment retry --root D:\FacebookScraperService
.venv\Scripts\python.exe -m deployment rollback --root D:\FacebookScraperService --sha <已保留的完整成功SHA>
.venv\Scripts\python.exe -m deployment clear-session --root D:\FacebookScraperService --session <已核对的会话ID>
```

`pause` 暂停发现和安装自动更新，保留业务进程。`rollback` 仍经过空闲协调；不能直接杀掉可能正在提交的业务。旧代码、环境和版本配置保留在本机，恢复不依赖联网。

自动回退**不恢复旧业务数据**：发布防重、付费记录、人工稿、审校决定、激活边界和访问额度始终使用最新共享数据。真相源格式变化或需要新控制器的版本交技术维护。控制器切换中断时读取持久记录核对进程；无法确认身份时保持阻塞，不能按窗口标题结束进程。

已启用的新版本若进程退出，恢复旧版后仍会阻止故障 SHA 自动重装；不会在“新版故障—回退—再次安装”之间循环。普通 Windows/控制器重启则恢复最后已确认的版本和模式，不擅自退到更早版本。

### 15.9 故障与通知

运行页展示实际版本、候选、阶段、阻塞与最近结果；详细进程日志在 `logs`，部署事务在 `control\deployment.json`，维护与会话在 `control\maintenance.json`。不要删除这些记录来“解锁”。端口被无关进程占用时不会结束它。

飞书 `alert` 机器人通知完成、失败、回退及长期等待，并链接审校台。部署通知使用独立发件箱，发送失败或结果不确定不改变已经提交的部署结果，也不自动重放不确定通知。卡片链接默认只供运营在服务机桌面打开；手机或其他电脑打不开 `127.0.0.1`。

GitHub 构建成功不代表服务机已更新。缺少 Chrome 登录、业务未激活或历史费用待核对属于业务状态；部署检查不自动抓取、付费、自检飞书或排期。

### 15.10 受控验收与持续运行

开发机可先执行版本化本机演练（Python 3.12.9、现有前端依赖及已下载的锁定 wheel）：

```powershell
scripts\run_python.bat tests\windows_deployment_rehearsal.py --wheelhouse state\release-wheelhouse --out state\deployment-implementation\windows-rehearsal
```

`--out` 必须是新目录。演练复制源码构建两个明确标为夹具的版本，在最终路径创建虚拟环境，启动真实本机 Web 进程，验证更新、健康失败回退和控制器重建后的恢复；最后按进程身份协作退出并保留日志、文件哈希与报告。远端版本、通知与时间推进为替身，调度和付费始终关闭，不注册计划任务、不使用 Chrome 业务 profile。这不能代替下一步的 GitHub 和服务机验收。

先用另一套**隔离实例、空凭据与假外部服务**演练：真实 GitHub 制品获取、成功切换、错误版本/绑定拒绝、候选启动失败回退、控制器在停止/启动/验证阶段中断后恢复、端口冲突、失联草稿、多标签页及旧页面提交。逐项记录实际 SHA、实例 ID、进程创建时间、切换耗时、前后数据哈希和通知状态。

再在业务服务机核对已有人工登录、各阶段验收、共享数据、已授权模式及运营体验。部署演练只能升级部署能力证据；不能升级抓取、模型账单或 Business Suite 发布证据。

每日核对审校台 `/runtime`、部署状态与调度实际心跳；机器无人登录时不报“业务正常”。本地告警不能发现本机断电，既有外部心跳仍按配置单独验收。

### 15.11 备份与旧手工流程

普通兼容更新不逐次复制整套图库；保留旧代码只提供代码回退能力。首次接续、数据迁移前完整备份核验；日常继续按第 1 节外拷整个 `shared/archive`、`shared/state` 及受保护凭据，备份须覆盖人工图片与设置。云盘镜像本轮延期；首次真实发布后每天异机备份，不能把制品保留期当作数据备份。

受管实例停用原来的“scheduler-disable → git pull → setup → scheduler-enable”流程。开发检出仍可手动安装和测试；生产目录由控制器管理，不 stash、不原地 pull、不复制旧虚拟环境，不同时安装第二套调度计划任务。
