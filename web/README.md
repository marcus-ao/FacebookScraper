# 审校台

审校台读取本地归档，并把人工文案、本地化选择、分类标签和审校决定写入真实追加式真相源。`web/api/fake_writer.py` 与旧 `_fake_state.json` 只是原型遗留，当前 FastAPI 路由不使用它们。

业务规则以 [../docs/FUNCTIONALITY.md](../docs/FUNCTIONALITY.md) 为准，接口和界面约束看 [DESIGN.md](DESIGN.md)。

## 当前能力与限制（2026-09-12）

本 worktree 已核验备份并接续原 archive/state/.env/解释器，页面写入真实数据。本轮实际 Vue dist + 临时 ASGI 的版本化浏览器已扩为 7 场景；全量 65 脚本和最后相关补跑分开记录。真实历史与整月只读读取已有结果，新单渠道真实 G8 和运营飞书到排期仍未验收，证据范围见 [集成记录](../docs/INTEGRATION_2026-09-12.md)。发布浏览器中曾放入不可发布技术文案与 2 张历史原图作编辑器检查，可能留下草稿；没有发布/排期提交。

| 能力 | 状态 | 说明 |
|---|---|---|
| 当前任务列表与详情 | 离线通过 | 从 archive/账本读取；无数据时为空 |
| 人工文案、正文/标签/链接编辑 | 离线通过 | 写 `translated_human.jsonl` 与 `localization.jsonl`，带版本检查 |
| 七态审校 | 离线通过 | 挂起、不发、人工接管等写 `review_items.jsonl`，`actor=null` |
| ZIP 资源下载 | 离线通过 | 完整生成后才记录 `handed_off` |
| 本次 Planner 月历只读 | 真实通过 | 2026-09-13T05:01:33Z ready，35 格/4 公开帖/2 推荐时段已区分，3 个 IG remote ID；不代表新排期回读 |
| 审校通过并排期 | 代码未完成 | 快照、单渠道/全文/身份/时刻回读已离线通过；远端 scheduled 图片适配仍缺，不能只归为等待确认 |
| 历史列表与详情 | 真实通过 | 原 1,067 条与 SQLite 一致；page1/page2 每页 20 无重复，冻结只读；range/page/total、90 天外详情可用 |
| 损坏审校账本处理 | 离线通过 | 严格读取和定向测试已补，坏行显式失败并定位 |
| 风险预扫描代码 | 离线通过 | 真实扫描路径已接，未扫/失败/成功零风险/stale 分开；模型实测另验 |
| 三类 hashtag 采样代码 | 离线通过 | C7 持久停机/count 结构修复复审通过；Trends 证据门控导出/周月 CSV 已测；实际 IG/Trends 都因 429 停止 |
| 模型任务与批次恢复 | 离线通过 | job/批次 operation_id、费用、版本 CAS 与人工核对；后端定向验证，浏览器中断显示使用明确 UI 夹具 |
| 两个运营设置与说明 | 离线通过 | 默认柏林时刻/挂起天数，CAS 与原注释保留；controlled_fields/editable_help 展示只读说明 |
| 五阶段运行状态 | 离线通过 | Web/CLI 共用只读 snapshot，含费用/恢复、处理 P50/P95/晨间就绪率、采样阻塞、投递；UI 夹具回归已过 |
| 柏林选时与范围 | 离线通过 | 依录证可见窗口/DST 验证任意合法时刻；范围变化须重录，不永久固定同月 |

## 两份前端

`web/ui/`（Vue，旧）与 `web/ui-next/`（React，新）并存，各自构建到自己的 `dist/`。
生产挂哪一份由 `config.toml` 的 `[paths].web_dist` 决定，缺省是 `web/ui/dist`。
**切换与回滚都是改这一个值 + 重启 Web 进程**，不碰接口、字段、账本或归档。
逐步操作见 [../docs/MANUAL_STEPS.md](../docs/MANUAL_STEPS.md) 第 13 节。

React 那一侧的设计与决策口径在 [ui-next/DESIGN.md](ui-next/DESIGN.md)、
[ui-next/DECISION_LOG.md](ui-next/DECISION_LOG.md)；接口契约仍以本目录的
[DESIGN.md](DESIGN.md) 为准，两者冲突时接口契约赢。

⛔ **换掉 `web/api/app.py` 的 `SinglePageFiles` 前先看 `tests/tests_spa_static.py`。**
旧 Vue 的 URL 全都长在 `/` 上（`/?task=`、`/?view=`），服务端从不需要管前端路由；
React 用真实路径（`/review`、`/calendar`、`/review/<账号>/<帖子>`），裸的
`StaticFiles` 找不到同名文件就是 404 —— 侧栏点着能走，一按 F5、一个收藏、
一条粘给同事的链接就只剩一行 `{"detail":"Not Found"}`。回落同时要守住三条反例：
`/api` 的 404 必须还是 JSON，漏掉的产物必须当场 404，不收 `text/html` 的请求不回落。

## 启动

开发机构建前端（两份都要，旧的那份是回滚保险）：

```powershell
npm.cmd --prefix web/ui install
npm.cmd --prefix web/ui run build
npm.cmd --prefix web/ui-next install
npm.cmd --prefix web/ui-next run build
```

启动服务：

```powershell
scripts\run_python.bat -m uvicorn web.api.app:app --host 127.0.0.1 --port 8765
```

打开 [本机审校台](http://127.0.0.1:8765/)，API 文档在 `/api/docs`。通过 `scripts/run_python.bat` 保持与其它入口相同运行绑定；不要另外建立空 state。卡片的企业访问地址仍需在运营机器真实验证。

离线浏览器回归使用已入库的入口，不访问当前业务归档或账号：

```powershell
npm.cmd --prefix web/ui run build
scripts\run_python.bat tests/tests_browser_workflow.py -v
```

React 那一侧另有几个只读脚本，都跑在同一套隔离夹具上，产物落 `state/ui-regression/`：

```powershell
scripts\run_python.bat tests/browser_regression.py --stage ALL
scripts\run_python.bat tests/network_compare.py
scripts\run_python.bat tests/cutover_rehearsal.py --dist web/ui-next/dist
scripts\run_python.bat tests/review_probe.py
```

⚠️ 已知性能项，切换后第一周盯一下：历史列表首屏会为每一行发一次缩略图请求，
React 默认每页 50 条、旧 Vue 是 30 条，同一个接口新 UI 首屏要多付 1.67 倍。

成本随归档规模涨，两个口径差很远，别只看夹具那个数：

| 归档 | 单张缩略图 | 首屏 50 张估算 |
|---|---|---|
| 隔离夹具（63 篇） | 0.03–0.11 s | 约 3.9 s |
| **真实归档（1,067 篇）** | **中位 1.57 s，最大 2.0 s** | **约 12–13 s**（每源 6 并发） |

2026-09-14 切换当天在真实服务上直接 fetch 8 张量到的。**文字行是立刻出来的**
（50 行、状态、分页都不等图），缩略图在十几秒里陆续补齐，所以不拦使用；
但比旧 UI 慢一截是真的。成本在 `core/store.assert_physical_direct_path` 的
路径解析上，它随账号目录规模增长。

太慢时先让运营把分页改成 20 条/页；根治要缓存那段解析，而且对新旧两个前端同时生效。
复现：`scripts\run_python.bat tests/history_thumbnail_cost.py`。

📌 量这一项时别用后台标签页：图片是 `loading="lazy"`，页面没被绘制时浏览器
一个请求都不会发（实测 `document.visibilityState === 'hidden'` 下 38 秒零请求），
很容易误判成「卡死」。要么把窗口调到前台，要么像上面那样直接 `fetch`。

保存/来源冲突/历史/设置走临时真实 ASGI；运行恢复、消息未知、模拟排期/公开显示走明确浏览器 API 夹具，只验 UI。`tests/browser_fixture.py` 管临时数据与独立 Chrome，输出记录实际构建哈希和断言，不能把模拟排期显示当真实提交。

生产机运行静态构建不需要 Node；生产迁移本轮明确延期。若拷贝 `web/ui/dist/`，必须与同一提交的 API 一起部署，避免前后端契约漂移。

## 使用规则

- 当前 FB/IG 任务独立，不在 UI 合并。来源渠道是唯一发布渠道。
- 保存采用来源哈希和 revision 乐观并发控制；409 表示页面过期，应刷新后重做。
- 人工文案和图片优先，后台重建不能覆盖。
- 挂起默认三个上海工作日；“不发”必须有理由；“人工接管”与“不发”是不同终态。
- `scheduled` 只表示 Business Suite 回读确认排期，不表示帖子已公开。
- `stale=false` 只说明来源原文没变；详情还返回 machine_current 和机器/当前提示词版本。旧 FB 译文 prompt 5、当前 6，当前无可接受德语图且 FB 目录未发现 media_de；冻结 `.tech` 的历史图不移用，不因打开页面自动重做。
- Facebook 可在正文光标处插入链接区对应的 `{{linkN}}`，最终替换 URL 后计数；未插入链接仍末尾追加，非法 token 阻止通过，IG 使用 bio 话术。即时校验未返回前的本地计数显示“约”。
- 人工选择的柏林时刻若同渠道前后 90 分钟有占用，直接拒绝并给建议，不自动顺延。
- 每个发布动作都必须在最终确认对话框展示冻结后的文案、图片、账号、唯一渠道和时刻。

## 风险夹具

`web/api/fixtures/risks.json` 只用于稳定演示黄色风险标记。它不来自当前帖的模型扫描，不能参与批准、排序、飞书卡片或验收。正式风险结果必须绑定账号、post ID、来源文本哈希、模型/规则版本和生成时间；来源变化后旧结果应过期。

## 数据错误

来源版本、审校 revision 或锁冲突时刷新后重做决定；设置冲突也按新版本重载。账本损坏时停止写入并保留文件，在备份副本检查，不删坏行或置空。模型恢复需核对 job/request/进程与费用；不确定请求先人工核账，恢复不再次发付费请求。

## 发布边界

2026-09-01 dump 不含单渠道勾选；本轮新录 FB `Neakasa Deutschland`、IG `neakasa.de` 控件并完成一次真实月份读取。精确全文、完整日期格、唯一渠道/remote ID 和资产一致性代码已离线通过、复审关闭；远端 scheduled 图片读取适配器仍未实现，需受控样本排期后获取真实控件再补齐。G8 同时核全文、远端图片验证与冻结清单数量/顺序/source SHA，编辑器两图或旧双渠道 journal 都不足。FB 待制作包仍缺最终德语图，IG 无新 `.global` 素材；完成具体文图、账号/单渠道/柏林时间确认后才能继续。长文、多图、FB 链接、IG CTA 和运营飞书完整流程分别留证。
