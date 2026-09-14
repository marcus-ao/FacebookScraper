# 切换前稳定化报告

**成文日期：2026-09-14。代码基线 `2c89f07`，未提交。**

上一轮是[实施后独立复核](POST_IMPLEMENTATION_REVIEW.md)（找问题）。这一轮只做一件事：
把「马上要把生产默认前端从旧 Vue 换成新 React」这件事，在换之前先跑一遍。

---

## 1. Executive Result

**CUTOVER_READY**（冻结后的最终结论，判据见 §17 与 §18）

> 写这一节时给的是 `CUTOVER_READY_WITH_NON_BLOCKING_FOLLOWUPS`，
> 唯一的保留是「那条部署级缺陷只有一个手工脚本守着」。§18 把它变成了正式
> Python 测试，保留随之取消。这里保留原判断的来龙去脉，不改写历史。

这一轮找到一个**会让切换当场出事**的问题，已修并验证：

> 在真实 FastAPI 上挂 `web/ui-next/dist`，`/review`、`/history`、`/calendar`、
> `/settings`、`/runtime`、`/review/<账号>/<帖子>` **全部返回 404**。
> 只有 `/` 是 200。

也就是说：运营从侧栏点进去能用，但**一按 F5、一个收藏夹、一条粘给同事的链接，
页面就变成一行 `{"detail":"Not Found"}`**。而「刷新详情页仍知道第 n / N 篇」
「返回列表恢复原筛选」正是这次重构写在 DECISION_LOG §2.2 里的目标。

浏览器回归 12/12 全绿照不出这一条 —— 原因见 §10。

剩下一条没解决、但不拦切换的：历史列表首屏要为每一行发一次缩略图请求 ——
新 UI 默认 50 行，旧 Vue 是 30 行，同一个接口新 UI 首屏要多付 1.67 倍，见 §13。

---

## 2. Baseline

| 项 | 值 |
|---|---|
| HEAD | `2c89f07`（本轮没有 commit / push / stash / reset） |
| 进来时的工作区 | 上一轮 review 的 6 个前端文件改动 + 4 个新文件，未提交 |
| 本轮结束时 | 见 §15 |

本轮改动分两类，报告里全程分开记：

- **A：上一轮 review fixes**（F-1 antd loading 图标离场、F-2 标签页挂载策略、
  F-3 回执非 scheduled 时重读详情、F-4 删死文件、F-5 历史列序）。
  **一个字节都没动**，只在 §9 里重新验证了一遍。
- **B：本轮 pre-cutover fixes**（下面 §3 的 A–I，加上 §10 找出来的那条）。

---

## 3. Fixes Applied

### FIX A — 月历那套 ARIA list 是无效的，删掉

**改哪里**：[CalendarPage.tsx:33-42](../../web/ui-next/src/pages/calendar/CalendarPage.tsx)、
[browser_regression.py:361](tools/browser_regression.py)

月历网格原来是 `role="list"`，但它的直接子节点里还混着七个星期标题和月初的补位格 ——
两样都不是 `listitem`。读屏拿到的是一个结构无效的列表。

想改成合法 ARIA 就得动布局（CSS Grid 靠的正是这些直接子节点），而「没有错误 ARIA」
比「有错误 ARIA」强。所以两个 role 都删了，日期格改成 `data-day="2026-09-14"`。

- 布局一个像素没动：`role` 是纯语义属性，`.day:nth-child(7n)` 那条边框规则依赖的
  子节点序也没变。
- 原来靠 `[role="listitem"]` 定位日期格的那句断言改成 `[data-day]`。
- **测试**：`review_probe.py --only calendar` 断言全页 `role="list"` = 0、
  `role="listitem"` = 0、`[data-day]` = 31。

### FIX B — 灰按钮的原因，键盘要够得到

**改哪里**：新增 [DisabledReason.tsx](../../web/ui-next/src/components/DisabledReason.tsx)；
用在 [DecisionPanel.tsx:11](../../web/ui-next/src/features/approval/DecisionPanel.tsx)、
[PaidActionButton.tsx](../../web/ui-next/src/components/PaidActionButton.tsx)、
[CalendarPage.tsx](../../web/ui-next/src/pages/calendar/CalendarPage.tsx)

原生 `disabled` 的 button 拿不到焦点。所以 Tooltip 的 hover 内容、按钮上的 `title`，
**只用键盘的人一个都走不到** —— Tab 直接跳过那个按钮，屏幕上只剩一个灰块。
UI_AUDIT P2-10 记的就是这件事；新 UI 已经把六个条件收敛成一句话了，
但那句话过去只挂在鼠标上。

按钮本身的启用判据一点没改，改的是外面那层包装：有原因时它自己成为一个
可聚焦的 `role="note"`，Tab 停在这里读到的是

> 通过并创建排期。当前不能操作：请先填写柏林发布时间

没有原因时整层不渲染，不留多余的 tab stop。

⛔ 没有把 `disabled` 换成「能点但拦 click」—— 那会让读屏把按钮念成可用的，
按下去却什么也不发生，比灰着更糟。

- **测试**：[DisabledReason.test.tsx](../../web/ui-next/src/components/DisabledReason.test.tsx) 6 条；
  `review_probe.py --only approval` 从页面开头连按 Tab，**第 30 次**落在这个节点上，
  读到的 `aria-label` 就是上面那一句。

### FIX C — 运营在设置页保存之后，长驻进程可能还按旧值办事

**改哪里**：[core/config.py](../../core/config.py) 新增 `invalidate_cfg_cache()`；
[core/operating_settings.py:183](../../core/operating_settings.py) 在 `os.replace` 之后调用它。

详见 §4。

### FIX D — 标签与链接页的常驻说明再压一遍

**改哪里**：[LocalizationEditor.tsx:55-57、74](../../web/ui-next/src/features/localization/LocalizationEditor.tsx)

原来两行常驻说明：

| 原文 | 处理 |
|---|---|
| 建议不自动写入。 | 挪到「采用勾选到编辑区」按钮旁边，只在真有建议时出现 |
| 保存会保留超出上限的标签，供人工调整。 | 只在 Instagram 且标签 ≥ 27 个时出现（上限 30；Facebook 没这个上限） |
| 插入链接可指定正文位置； | 删。「将链接 N 插入正文光标处」这个按钮名本来就说了 |
| 未插入的链接将放在文末。 | **保留**，缩成「没有插入正文的链接会放在文末。」 |
| 仅保存本篇选择。 | 删。系统实现说明 |

保留那一句不是可选的：它决定帖子发出去长什么样。运营如果不知道这条，
会以为没插入的链接就不发了。

净效果：这一页常驻文字从 2 句 44 字减到 1 句 17 字，且只在这一篇真有链接时出现。

### FIX E — 手改坏的 URL 不要甩一个 422 给她

**改哪里**：[search-params.ts:48-85](../../web/ui-next/src/app/search-params.ts)

三个参数逐个对过真实后端契约，**不是按感觉定的**：

| 参数 | 后端真实约束 | 前端处理 |
|---|---|---|
| `platform` | [web/api/app.py:95](../../web/api/app.py) 写死 `^(facebook\|instagram)$` | 只认这两个，别的回落 null |
| `month` | 没有 pattern，只做等值匹配（[core/index_db.py:182](../../core/index_db.py)）；取值是 `created_at[:7]`，另有一个真实取值 `undated`（同文件 :113） | 认 `YYYY-MM` 与 `undated` |
| `tag` | 没有 pattern，自由业务标签；另认一个哨兵 `__untagged__`（[core/index_db.py:186](../../core/index_db.py)） | **只去首尾空白，不编白名单** |

`platform` 那条是真会出事的：历史页的筛选是服务端做的，`/history?platform=facebookk`
直接 422，她看到的是整页「暂时无法读取历史归档」。

非法值同时从规范化后的 URL 里摘掉，所以坏链接走一圈就干净了，不会再带进详情页。

- **测试**：[search-params.test.ts](../../web/ui-next/src/app/search-params.test.ts) 新增 6 组，
  含「⛔ tag 不许编白名单」那一条 —— 归档里有什么标签是运营说了算的。

### FIX F — 月历标出「今天」

**改哪里**：[format.ts:27-36](../../web/ui-next/src/lib/format.ts) 新增 `berlinToday()`；
[CalendarPage.tsx](../../web/ui-next/src/pages/calendar/CalendarPage.tsx)、
[CalendarPage.module.css](../../web/ui-next/src/pages/calendar/CalendarPage.module.css)

⚠️ 口径是**柏林日期**，不是浏览器本地日期。这台机器在中国，柏林当地 00:00–07:00
那几个小时里浏览器已经是第二天了，`new Date().getDate()` 会把「今天」标到隔壁格子上。
月历的日期格和每张卡的 `at_business` 都是柏林墙上日期。

视觉很克制：一圈 1px 主色描边（`outline`，不参与布局，格子几何不变）+ 日期后面一个
小号「今天」。它压不过卡片上的已发布 / 已排期 / 待核验三种状态色 —— 那三个才是她要看的。

- **测试**：`format.test.ts` 三组固定时刻（跑在 `TZ=America/New_York` 下，
  柏林和上海都不是本地时区，所以任何「偷偷用了本地时区」的实现当场就红）；
  `review_probe.py --only calendar` 断言标记格恰好 1 个且 `data-day` 等于柏林今天。

### FIX G — 同渠道占用提示原来跨不过午夜

**改哪里**：新增 [occupancy.ts](../../web/ui-next/src/features/approval/occupancy.ts)；
[DecisionPanel.tsx:19-20](../../web/ui-next/src/features/approval/DecisionPanel.tsx)

原来只比同一个日历日的卡片。于是她选 00:30、前一天 23:30 已经有一条同渠道排期时，
界面说「当前缓存未发现这一天的同渠道记录」—— 而那两个时刻只差 60 分钟，
正是后端 90 分钟闸门要拦的情况。她点下「确认通过并创建排期」才吃到 409。

改成按真实时间差比，前后各看一天。两边都用柏林墙上时刻：她填的 `datetime-local`
不带偏移，换算成 epoch 等于替她猜一个偏移，夏令时那天猜错就是错一小时。
代价是夏令时切换当晚那一小时会有 ±60 分钟误差 —— 这只是本地提示，真正判定在后端。

折叠标题也跟着改成「查看所选时刻前后一天的同渠道占用」，免得标题说「当天」
而列表里出现昨天的卡片。

- **测试**：[occupancy.test.ts](../../web/ui-next/src/features/approval/occupancy.test.ts) 9 条，
  含 23:30 → 次日 00:30 = 60 分钟、边界值（正好等于间隔不算冲突）、另一个渠道不算占用。

### FIX H — 她把排期时间清空之后，不要再替她填回去

**改哪里**：[useApproval.ts:14-21](../../web/ui-next/src/hooks/useApproval.ts)；
判据抽到 [action-reasons.ts](../../web/ui-next/src/lib/action-reasons.ts) 的 `seedScheduleTime`

原来是 `if (!when && options.data?.earliest) setWhen(...)`。她手动清空之后，
下一次 approval-options 回来（重新核对排期条件、保存正文、改分类都会触发），
默认值又填回去了 —— 等于和人抢输入框，而这个字段决定帖子什么时候公开发出去。

现在只有三件事同时成立才填：没人碰过这个字段、当前是空的、后端给了可选范围。
清空也算「碰过」—— 那是一个决定，不是一个待补的空值。换一篇任务时重置。

- **测试**：`action-reasons.test.ts` 按「初次加载 → 清空 → refetch → 仍为空 → 换篇 → 又有默认值」
  六步逐条断言；`review_probe.py --only approval` 在真浏览器里走了一遍：
  首次 `2026-09-14T10:00`，清空后点「重新核对排期条件」，仍然是空串。
  **把判据改回旧行为后这条探针当场失败**（`她清空之后又被填回去了：'2026-09-14T10:00'`）。

### FIX I — 几处过深的三元表达式

**改哪里**：新增 [action-reasons.ts](../../web/ui-next/src/lib/action-reasons.ts)；
改 [useApproval.ts:23](../../web/ui-next/src/hooks/useApproval.ts) 与
[ContentJobs.tsx:32-35](../../web/ui-next/src/features/content-jobs/ContentJobs.tsx)

三条原因链原来是 7 层 / 5 层 / 7 层嵌套三元，读的人没法确认哪一条先赢。
改成纯函数里的顺序 `if`，每条 `return` 就是一级优先级：从「她自己动一下手就能解决的」
排到「只能等系统的」。

**行为完全不变**：每条文案一字未改，优先级逐条对过。没有做成通用规则引擎 ——
这里只有三个动作，多一层抽象就要多读一层。

- **测试**：`action-reasons.test.ts` 用 `it.each` 把三条链的**每一级**都单独断言了一遍，
  另加「正在编辑压过其它所有条件」「已排期那篇说的是『已有排期』不是『请先恢复审校』」
  这类会让她走错路的组合。

---

## 4. Config Reload Fix

运营在设置页把默认排期时刻从 `["10:00", "17:00"]` 改成 `["11:30", "18:00"]`，
点保存，页面说保存成功。但长驻的 Web 进程可能还在按 10:00 / 17:00 办事。

**原判据**：[core/config.py:169-177](../../core/config.py) 的 `cfg()` 用
`(st_mtime_ns, st_size)` 判断配置要不要重读。

**为什么会错**：`operating_settings.save()` 是**按原格式回填同一个键**的 ——
`["10:00", "17:00"]` → `["11:30", "18:00"]`、`snooze_default_days = 3` → `= 4`，
字节数一模一样。于是 `st_size` 恒等，能不能发现就全看 mtime 一个数。
上一轮实测：5000 次背靠背等长改写里有 **68.42%** 共用同一个 `st_mtime_ns`
（有效时间戳精度约 1 毫秒）。

**最终 fix**：保存的人自己知道刚写过什么，不必去猜。
`core/config.py` 新增 `invalidate_cfg_cache()`，`save()` 在 `os.replace` 成功之后调用它。
人手直接编辑 `config.toml` 仍然走原来的时间戳探测，不受影响。

**为何不改 API**：这是一个进程内缓存的失效时机问题。endpoint、请求体、响应体、
状态语义**全部 0 改动** —— `PUT /api/settings` 的请求和响应逐字节相同，
`read()` 的返回结构没动，配置文件格式没动，没有新依赖，也没有让 `cfg()`
每次都去 hash 文件。

**回归测试**：`tests/tests_operating_settings.py` 新增
`test_save_is_visible_when_the_rewrite_lands_in_the_same_timestamp_tick`。
它把 `os.replace` 包一层，落盘后立刻把 mtime 拨回保存之前 ——
于是 `(st_mtime_ns, st_size)` 这个判据**彻底看不见**这次改写。不 sleep，
也不靠跑很多遍碰运气。

确定性验证过：把 `invalidate_cfg_cache()` 那一行换成 `pass`，这条测试当场失败，
报的是 `AssertionError: 3 != 4`。

**100 次结果**：`tests_operating_settings.py` 连跑 100 次，**100 / 100 PASS**
（fix 落地后跑了一次 100/100，`web/api/app.py` 改完之后又跑了一次 100/100）。

---

## 5. Accessibility

| 项 | 结果 | 证据 |
|---|---|---|
| 灰按钮的原因键盘可达 | 纯 Tab 第 30 次到达，读到「通过并创建排期。当前不能操作：请先填写柏林发布时间」 | `review-probe.json` `approval_reason_tab_presses` |
| 付费动作同样处理 | 走同一个 `DisabledReason` 组件 | `DisabledReason.test.tsx` + `disabled_reason_labels_on_detail` |
| 月历不再有无效 ARIA | `role="list"` 0 个、`role="listitem"` 0 个 | `calendar_invalid_list_roles` |
| loading 可及名不再残留 | Stage I 连跑 **10/10** | §9 |
| 模态框接得住焦点 | 打开后焦点在 `.ant-modal` 内 | `focus_moved_into_modal: true` |
| Shift+Tab 不逃出模态框 | 是 | `shift_tab_stays_in_modal: true` |
| Esc 关得掉 | 是 | `modal_closed_by_escape: true` |
| 关掉之后焦点回原处 | 回到「编辑分类」按钮 | `focus_before_modal` == `focus_after_close` |
| 抽屉焦点 / 缩放后焦点返回 | 通过 | `browser-stage-d3.json` `zoom_escape_focus_return` |
| 全站 a11y 扫描 | 20 屏通过 | `capture_final.py` |

那个 loading 可及名的问题值得再说一句：它 width/opacity 都是 0，**屏幕上根本看不见**，
所以它不是视觉故障 —— 是按钮的可及名永远变成「loading 保存分类」。
读屏会念一个早就结束的 loading。上一轮修的就是它。

---

## 6. Calendar

- **今天**：按柏林日期标（`berlinToday()`）。探针实测柏林今天 `2026-09-14`，
  标记格恰好 1 个，`data-day` 也是 `2026-09-14`。当月历显示范围不含今天时标记数为 0，
  探针同样断言。
- **跨午夜占用**：同渠道占用改按真实时间差算，23:30 与次日 00:30 视为 60 分钟。
  后端 90 分钟闸门仍是唯一权威判定，这条只是让她在点确认之前就看得到。

---

## 7. Approval

| 行为 | 状态 |
|---|---|
| 清空排期时间后不自动填回 | 本轮修（FIX H），探针与单测双证 |
| 默认时刻只在首次 seed | 同上；换篇时重新允许 |
| 回执必须严格 `scheduled` | 上一轮已有，Stage D4 `strict_receipt` 仍通过 |
| 回执不严格时重读详情，让「核对并补齐本地回执」出现 | 上一轮 F-3，未动 |
| 409 的建议时刻点一下就填进去 | Stage D4 `clickable_409_suggestion` 仍通过 |
| 写入体仍是那五个字段、不带偏移 | `network-comparison.json` 16/16 逐字段相同 |

---

## 8. Localization Copy

**减掉的常驻说明**：

- 「建议不自动写入。保存会保留超出上限的标签，供人工调整。」整行常驻 → 拆成
  一句挂在采用按钮旁（只在有建议时）+ 一句只在 Instagram 接近 30 个上限时出现。
- 「插入链接可指定正文位置；……仅保存本篇选择。」两个半句删掉。

**保留的业务关键说明**：

- 「没有插入正文的链接会放在文末。」**这一句不能删** —— 它决定帖子发出去长什么样。
  只在这一篇真的有链接时显示。

---

## 9. Tests

| 门槛 | 要求 | 实测 |
|---|---|---|
| React 单元测试 | 全 PASS，不得靠删旧测试 | **496 / 496，26 个文件**（上一轮 434 / 23，只增不减） |
| TypeScript | 0 error | **0** |
| React 构建 | PASS | PASS：JS 1,400.75 kB / gzip 447.66 kB；CSS 17.95 kB / gzip 3.87 kB |
| Vue 回滚构建 | PASS | PASS：JS 164.54 kB / gzip 59.68 kB；CSS 31.27 kB / gzip 6.10 kB |
| Python 全量 | 65 / 65 | **65 / 65**（`web/api/app.py` 改完之后重跑过） |
| `tests_operating_settings` ×100 | 100 / 100 | **100 / 100** |
| Stage I ×10 | 10 / 10 | **10 / 10** |
| Stage ALL ×3 | 3 × 12 / 12 | **3 × 12 / 12** |
| 写入契约对照 | 16 / 16 | **16 / 16**，method / path / 完整 body 与 Vue 相同 |

> 500 kB 警告不阻塞（上一轮已实测首屏可接受，见 POST_IMPLEMENTATION_REVIEW §11）。
> 本轮 JS 比上一轮多 1.86 kB（1,398.89 → 1,400.75），gzip 多 0.67 kB ——
> `DisabledReason`、`action-reasons`、`occupancy` 三个新模块。
>
> Stage I ×10 与 Stage ALL ×3 都是在**全部改动落地之后**跑的，含 `web/api/app.py`
> 的 SPA 回落。Python 全量与 `tests_operating_settings` ×100 同样重跑过一遍。

---

## 10. Cutover Rehearsal

**这一节是本轮最重要的东西。**

复现脚本：[tools/cutover_rehearsal.py](tools/cutover_rehearsal.py)

```bash
scripts\run_python.bat docs/ui-refactor/tools/cutover_rehearsal.py --dist web/ui-next/dist
```

它在临时目录里复制一份 `config.toml`、写上 `[paths].web_dist`、绑一个临时归档，
然后让**真的 FastAPI + 真的 uvicorn** 伺服**真的 dist**。两次是分开的进程 ——
因为 `web/api/app.py` 在 import 时就把 `DIST` 定死了，这正是生产上「改 config + 重启」的语义。

### 第一次跑出来的结果

| 路径 | 状态码 |
|---|---|
| `/` | 200 |
| `/review`、`/history`、`/calendar`、`/settings`、`/runtime` | **404** |
| `/review/<账号>/<帖子>`、`/history/<账号>/<帖子>` | **404** |
| `/?view=...`、`/?task=...` | 200 |
| `/api/*` | 200 |

浏览器里直接打开 `/calendar`：页面上是一行 `{"detail":"Not Found"}`。刷新一次，还是它。

**根因**：`web/api/app.py` 把 `StaticFiles(html=True)` 挂在 `/`。旧 Vue 的 URL 全都长在
`/` 上（`/?task=`、`/?view=`），服务端从来不需要管前端路由；新前端用的是真实路径，
`StaticFiles` 找不到同名文件就是 404。

**为什么浏览器回归 12/12 照不出来**：[tests/browser_fixture.py](../../tests/browser_fixture.py)
配套的 `UIFixture.route()` 是在 Playwright 那一侧拦路由的，
找不到文件时它自己回落 `index.html`（[ui_fixture.py:57-58](tools/ui_fixture.py)）——
**它自带 SPA 回落**。所以 12/12 全绿证明的是前端逻辑对，不是这个部署形状立得住。

### 修法

**改哪里**：[web/api/app.py](../../web/api/app.py) 新增 `SinglePageFiles`，替掉裸的 `StaticFiles`。

找不到文件时把 `index.html` 交回去，让前端路由自己认领。三种情况**不回落**，
回落了反而会把真问题藏起来：

1. `/api/...` —— 接口的 404 必须还是 JSON，不能变成一页 HTML；
2. 带扩展名的请求（`/assets/xxx.js`）—— 漏掉一个产物要当场 404，
   回落会让浏览器拿到 HTML 再报一个看不懂的 MIME 错；
3. 不收 `text/html` 的请求（`<script src>`、fetch）—— 同上。

实现上踩到两个点，都写在代码注释里了：`StaticFiles` 找不到文件是**抛** `HTTPException`
不是返回 404 响应；判 `/api/` 要看 `scope["path"]`，因为 `StaticFiles` 给的 path
在 Windows 上已经被 normpath 成 `api\xxx`。

### 修完之后

| 检查 | 结果 |
|---|---|
| 上表所有路径 | **全部 200** |
| 直接打开 `/calendar` | 标题「发布月历」 |
| 在 `/calendar` 上刷新 | 标题「发布月历」，URL 不变 |
| `/assets/does-not-exist.js` | **404**（没有被回落藏住） |
| `/api/does-not-exist` | **404** JSON（没有变成 HTML） |
| 旧 `/?task=fa_neakasaofficial/1234567890` | → `/review/fa_neakasaofficial/1234567890` |
| 旧 `/?view=history` | → `/history?page=1&limit=50` |
| 旧 `/?view=calendar` | → `/calendar` |
| `/api/tasks`、`/api/runtime`、`/api/settings`、`/api/calendar` | 200，仍由 FastAPI 分流 |
| 页面 JS 错误 | 0 |

### 回滚路径

同一个隔离宿主换成 `--dist web/ui/dist`：

| 检查 | 结果 |
|---|---|
| `/`、`/?view=*`、`/?task=*` | 全部 200 |
| `/api/*` | 200 |
| 旧 URL | 停在原地不跳转（Vue 本来就用查询参数） |
| 页面 JS 错误 | 0 |

证据：[cutover-rehearsal-react.json](cutover-rehearsal-react.json)、
[cutover-rehearsal-vue.json](cutover-rehearsal-vue.json)。

---

## 11. Visual QA

`capture_final.py` 重拍 20 屏 × 两个分辨率，无障碍扫描通过。
`review_probe.py --only density` 自己量的口径（不抄 `final-measurements.json`）：

| 界面 | 1366×768 | 1920×1080 |
|---|---|---|
| 审校队列首屏行数 | 12 | 19 |
| 历史归档首屏行数 | 12 | 19 |
| 行高 | 48px | 48px |
| 详情滚动屏数 | 1.47 | 1.34 |
| 每页 h1 | 1 | 1 |
| 详情主按钮 | 1 | 1 |
| 行内 danger 按钮 | 0 | 0 |
| 横向溢出 | 无 | 无 |

本轮改到视觉的三处都复核过：月历「今天」标记（细描边，压不过状态色）、
标签页文字变少、灰按钮外面多了一层可聚焦包装（`display: inline-flex`，
不改变按钮位置）。三处都没引入新的横向溢出，行数与行高没有回退。

---

## 12. Safety

本轮所有自动化验证里的真实外部动作次数：

| 动作 | 次数 |
|---|---|
| 真实 approve | **0** |
| 真实 calendar refresh | **0** |
| 真实付费模型调用 | **0** |
| 真实飞书重发 | **0** |
| 对真实归档的非 GET 请求 | **0** |

`review-probe.json` 的 `non_get_requests` 是空数组，`page_errors` 是空数组。
`browser_regression` 与 `network_compare` 每次都断言 `fx.denied_backend_requests` 为空。
切换演练在 ASGI 层把所有非 GET 挡成 503，退出时校验真实 `config.toml` 未被修改。

**用到的真实数据**：本轮**没有**对真实归档发起任何请求（包括只读 GET）。
所有验证都跑在临时归档上。

---

## 13. Remaining Risks

### 风险 1：历史列表首屏要发 50 次缩略图请求，是旧 UI 的 1.67 倍 —— 未解决

这是本轮量出来、**没有修**的问题，也是我认为切换后最可能被运营察觉的一条。

**实测**（隔离夹具，63 篇历史；复现脚本
[tools/history_thumbnail_cost.py](tools/history_thumbnail_cost.py)，每个引擎独立
browser context，否则第二个会命中第一个留下的 HTTP 缓存）：

| | 首屏缩略图请求数 | 走到 networkidle（空载） |
|---|---|---|
| 新 React `/?view=history` | **50** | 3.86 s |
| 旧 Vue `/?view=history` | **30** | 1.95 s |

两边差在哪：React 的历史默认每页 50 条（DECISION_LOG D2），旧 Vue 是 30 条。
**同一个接口，新 UI 首屏要多付 1.67 倍的请求，时间也差不多是两倍。**
这个比值是稳定的、可复现的部分。

绝对耗时则跟机器负载强相关，差别很大：

- 空载时单次 `reader.image_bytes` 约 0.03–0.11 秒，上表是 3.86 s / 1.95 s；
- 本机同时在跑构建与单元测试时，同一页实测到过 **13.6 s / 10.4 s**（React）与
  **5.2 s**（Vue）—— 这正是 `browser_regression --stage I` 那 8 秒导航预算
  盖不住的时候，见 §9 与 `browser_regression.py` 里的注释。

成本在哪：cProfile 指向 `core/store.assert_physical_direct_path`
（Windows 的符号链接／重解析点检查）。一次命中的请求约 198 次
`nt._getfinalpathname`；帖子没有图、要走到 404 的那条路更贵，
单次量到过 1,110 次。

⚠️ **这个数字在生产上只会更差**：夹具只有 63 篇，真实归档有 1,067 篇历史，
而 `source_post` 的成本包含 `iter_post_dirs` 目录遍历，随归档规模增长。
我没有对真实归档做这项测量（本轮不碰真实数据）。

**它为什么不拦切换**：文字行是立刻出来的，缩略图是渐进补上的 ——
她可以马上开始扫列表。而且旧 UI 在同一个接口上同样慢，只是慢得少一点。

**建议的后续处理**（都不在本轮范围内）：

1. 后端：`core/store.assert_physical_direct_path` 每次请求要做几百到上千次
   `_getfinalpathname`，而同一个账号目录在一次进程生命周期内不会变，是可以缓存的。
   这是收益最大的一条，而且它对新旧两个前端同时生效。
2. 前端：把历史默认每页从 50 改回 20 或 30。这是一个产品决定，不该由我单方面改。
   在此之前运营可以自己在分页器上选「20 条/页」。

顺带说明：缩略图 `<img>` 上**已经**有 `loading="lazy"`（[columns.tsx:134](../../web/ui-next/src/features/post-list/columns.tsx)），
但 antd Table 不虚拟化，50 行全在 DOM 里、总高约 2,400px，落在 Chrome 的
懒加载预取窗口内，所以还是全都发了。不建议为此再加前端机制。

### 风险 2：R-8 —— 详情页三个写死的预留高度常量（沿用上一轮结论，本轮不动）

`--rc-prose-reserved-h`、`--rc-image-reserved-h`、`--rc-initial-panel-h`
依赖顶栏、详情头、动作条的固定高度。1366 / 1920 两个分辨率视觉都通过，
`final-measurements.json` 与本轮 density 探针都守着正文高度与溢出。
临切换前不要换成另一套 flex 架构。

### 风险 3：R-9 —— 大量超长单行（沿用上一轮结论，本轮不动）

Stage C–J 的新代码普遍压成超长单行，最长 765 字符。本轮只格式化了真正改到的局部。
马上要切换，大规模无行为 diff 会增加 review 面、盖住真正的功能修复、抬高回滚成本。

### 风险 4：一次未能复现的临时写文件失败

Stage ALL 第三次运行时报 `OSError: [Errno 22] Invalid argument` —— 写
`docs/ui-refactor/screenshots/final-review@1920x1080.png` 失败（stage C 的截图）。
同一条命令重跑立刻通过，之后连续多次都没再出现，也不是任何 UI 断言。
判断是本机文件锁（杀毒/同步/编辑器）造成的偶发写失败，与前端无关。
如果切换后在生产机上重跑取证脚本再次遇到，看一眼是不是有进程占着 `screenshots/`。

---

## 14. Deferred Post-Cutover Work

切换稳定运行之后再做，每一条都建议独立提交：

1. **formatter / lint 独立提交**：先做一次纯格式化（R-9），再定 Prettier + ESLint 策略。
   现在项目里没有 ESLint。
2. **缩略图接口的成本**：`core/store.assert_physical_direct_path` 的路径解析缓存（风险 1 第 1 条）。
3. **历史默认每页**：50 → 20/30 是产品决定，需要运营自己拍板（风险 1 第 2 条）。
4. **详情页预留高度**：R-8 的 flex 重构。
5. **月历多卡压力**：同一天多张卡时的格子高度与换行，目前夹具只覆盖到 2 张。
6. **多标签页并发改同一篇**：revision / CAS 在 UI 上的表现目前只有单页验证。
7. **DEF-1 / DEF-10 / DEF-13**：DECISION_LOG 里记的那三条延期项。
8. **`browser_regression.py` 按领域拆分**：41 KB 的单文件，12 个 stage。本轮没动 ——
   临切换前拆测试框架的风险高于收益。

---

## 15. Git Diff

HEAD 仍是 `2c89f07`。没有 commit、push、stash、reset、rebase。

> 下面这张表是**冻结之前**的状态。§18 那一轮又加了
> `tests/tests_spa_static.py`（新）、`tests/tests_operating_settings.py`、
> `design-discipline.test.ts`、`browser_regression.py` 的 Stage D4 断言，
> 以及 `CUTOVER_CHECKLIST.md` 与 `RELEASE_CANDIDATE_MANIFEST.md`。
> 最终清单以 [RELEASE_CANDIDATE_MANIFEST.md](RELEASE_CANDIDATE_MANIFEST.md) 为准。

改动 = 上一轮 review fixes（A）+ 本轮 pre-cutover fixes（B），一起摆在工作区里：

```
 core/config.py                                     |  20 ++++      B (FIX C)
 core/operating_settings.py                         |   5 +-       B (FIX C)
 web/api/app.py                                     |  52 ++++++++-  B (§10 SPA 回落)
 tests/tests_operating_settings.py                  |  26 +++++     B (FIX C 回归测试)
 docs/ui-refactor/tools/browser_regression.py       |  14 ++-       B (导航超时 + data-day 选择器)
 docs/ui-refactor/tools/network_compare.py          |   2 +-       A
 web/ui-next/src/app/search-params.ts               |  63 +++++++++-- B (FIX E)
 web/ui-next/src/app/search-params.test.ts          |  74 +++++++++++  B (FIX E)
 web/ui-next/src/components/PaidActionButton.tsx    |  20 ++--      B (FIX B)
 web/ui-next/src/components/PaidActionButton.module.css |   6 -     B (FIX B)
 web/ui-next/src/features/approval/DecisionPanel.tsx |  18 +--      B (FIX B / G)
 web/ui-next/src/features/content-jobs/ContentJobs.tsx |   9 +-     B (FIX I)
 web/ui-next/src/features/localization/CategoryEditor.tsx |  12 +-  A
 web/ui-next/src/features/localization/LocalizationEditor.tsx |  11 +-  B (FIX D)
 web/ui-next/src/hooks/useApproval.ts               |  28 ++++-     A + B (FIX H / I)
 web/ui-next/src/lib/format.ts                      |   9 ++       B (FIX F)
 web/ui-next/src/lib/format.test.ts                 |  33 ++++++    B (FIX F / G)
 web/ui-next/src/pages/Placeholder.tsx              | 122 -------    A (F-4)
 web/ui-next/src/pages/calendar/CalendarPage.tsx    |  24 +++-      B (FIX A / B / F)
 web/ui-next/src/pages/calendar/CalendarPage.module.css |   5 +    B (FIX F)
 web/ui-next/src/pages/history/HistoryPage.tsx      |  21 ++--      A (F-5)
 web/ui-next/src/pages/review-detail/ReviewDetailPage.tsx |  11 +-  A (F-2)
 web/ui-next/src/styles/global.css                  |  20 ++++     A (F-1)
 23 files changed, 414 insertions(+), 191 deletions(-)
```

新增未跟踪文件：

```
web/ui-next/src/components/DisabledReason.tsx            B (FIX B)
web/ui-next/src/components/DisabledReason.module.css     B (FIX B)
web/ui-next/src/components/DisabledReason.test.tsx       B (FIX B)
web/ui-next/src/features/approval/occupancy.ts           B (FIX G)
web/ui-next/src/features/approval/occupancy.test.ts      B (FIX G)
web/ui-next/src/lib/action-reasons.ts                    B (FIX H / I)
web/ui-next/src/lib/action-reasons.test.ts               B (FIX H / I)
web/ui-next/src/pages/review-detail/tab-mounting.test.tsx  A
docs/ui-refactor/PRE_CUTOVER_REPORT.md                   B（本文件）
docs/ui-refactor/POST_IMPLEMENTATION_REVIEW.md           A
docs/ui-refactor/tools/cutover_rehearsal.py              B (§10)
docs/ui-refactor/tools/history_thumbnail_cost.py         B (§13 复现)
docs/ui-refactor/tools/review_probe.py                   A（本轮加了 approval / keyboard 两组）
docs/ui-refactor/tools/flaky_settings_probe.py           A
docs/ui-refactor/cutover-rehearsal-react.json            B（证据）
docs/ui-refactor/cutover-rehearsal-vue.json              B（证据）
docs/ui-refactor/review-probe.json                       B（证据）
```

**后端改动只有两处，都不动接口契约**：

1. `core/config.py` + `core/operating_settings.py`：配置缓存显式失效（FIX C）。
2. `web/api/app.py`：静态产物的 SPA 回落（§10）。只改变原本 404 的那些路径，
   endpoint / request / response / state 语义 **0 改动**。

`pipeline/`、`publish/`、`routes/`、`core/` 的其它文件一个字节没动。

---

## 16. Exact Cutover Procedure

**以下只是步骤。本轮没有执行其中任何一步。**

### A. 记下现在的值

打开生产 checkout 的 `config.toml`，找到 `[paths]` 表，记下 `web_dist` 当前的值
（没有这个键就是默认 `web/ui/dist`）。回滚要用。

### B. 确认两份产物都在，而且是刚构建的

```bash
npm --prefix web/ui run build
npm --prefix web/ui-next run build
```

两个 `dist/index.html` 都要存在。**两份都要构建** —— `web/ui/dist` 是回滚的保险。

### C. 改一个值

`config.toml` 的 `[paths]` 表里：

```toml
web_dist = "web/ui-next/dist"
```

### D. 重启 Web 进程

`DIST` 在 `web/api/app.py` import 时解析，**不重启不生效**。

### E. 只读 smoke（按这个顺序，每一步都在地址栏直接敲，不要只点侧栏）

1. `/review`
2. `/history`
3. 一篇活账号的详情，然后**在这一页按 F5**
4. 一篇冻结账号（`read_only`）的详情 —— 确认没有任何写入入口
5. `/calendar`
6. `/settings`
7. `/runtime`
8. 旧链接 `/?task=<账号>/<帖子>` —— 应跳到 `/review/<账号>/<帖子>`
9. 旧链接 `/?view=history` —— 应跳到 `/history?page=1&limit=50`

第 3 步那个 F5 是这次切换最该盯的一下：§10 修的就是它。

### F. 低风险真实写入 smoke（建议步骤，本轮没有执行）

按风险从低到高：

1. 编辑一篇的德语正文并保存
2. 改一篇的产品分类并保存
3. 把一篇 snooze
4. 再把它 wake 回来

每一步之后回列表看一眼状态对不对。

### G. 确认以上都成功之后，由用户单独决定

- 一次 calendar refresh（会打开发布浏览器读后台，数十秒）
- 一次**受监督的**真实 approve

这两件事本轮一次都没做过，不要顺手带过。

### H. 回滚

把 `config.toml` 的 `[paths].web_dist` 改回 A 步记下的值
（原来没有这个键就整行删掉，回到默认 `web/ui/dist`），重启 Web 进程，
确认 `/` 出来的是旧 Vue 界面。

演练已经验证过这条路走得通（§10 回滚路径）。

---

## 17. Final Recommendation

> **冻结后的最终结论：CUTOVER_READY。**
>
> 上面 §1 写的 `CUTOVER_READY_WITH_NON_BLOCKING_FOLLOWUPS` 是冻结之前的判断 ——
> 当时唯一的保留是「那条部署级缺陷只有一个手工脚本守着」。§18 把它变成了
> 正式测试，这个保留就没有了。历史缩略图成本是性能 backlog，不是 cutover
> correctness 风险，不构成保留。

**适合现在切换，前提是先把 §10 那个改动一起带上。**

判断依据：

- §10 那条原本会让切换当场出事（任何非 `/` 的 URL 刷新就是 404），现在修了，
  并且有一个可重复的演练脚本证明它、也证明回滚路径可用。
- 业务契约没有动过：16 条写入的 method / path / 完整 body 与旧 Vue 逐字段相同，
  approve 的严格回执、revision/CAS、柏林墙上时刻、`wake_at +08:00`、
  `read_only` 无写入入口、`scheduled` 不显示成已发布 —— 这些在 12 组浏览器场景里
  连跑 3 轮全绿。
- 两处后端改动都不改接口语义，其中一处（配置缓存）修掉的是运营在设置页
  保存之后可能读到旧值的真实问题，并且有确定性回归测试 + 100/100。
- 旧 Vue 一个字节没删，构建仍然 PASS，回滚就是改一个值 + 重启。

**切换之后第一周要盯的**：历史归档页的缩略图补齐速度（§13 风险 1）。
如果运营反馈「历史页图老是慢慢才出来」，先让她把分页改成 20 条/页，
再排后端那条路径解析缓存。

**这一轮没有做的事**：没有执行生产切换，没有改真实 `config.toml`，
没有删旧 Vue，没有真实排期 / 刷新月历 / 付费调用 / 飞书重发，没有 commit，没有 push。

---

## 18. Release Candidate Freeze（2026-09-14 追加）

这一节是在上面全部内容之后追加的，只做一件事：把最后一个工程保护缺口补上，
然后冻结。**没有新增任何功能改动。**

### 补的缺口

§10 那条部署级缺陷原先只有 `cutover_rehearsal.py` 守着 —— 那是一个要手工跑的脚本，
不在日常测试里。现在它进了正式 Python 测试：

**[tests/tests_spa_static.py](../../tests/tests_spa_static.py)**，14 条，
用临时 dist + 临时 config，**不读 `web/ui-next/dist` 的当前内容**，
所以不会随谁有没有构建过前端而飘。覆盖：

| 契约 | 断言 |
|---|---|
| 深链接刷新 | 10 条前端路由（含带查询串、带 `tab=`）都返回 index.html |
| 带点的账号名 | `/review/in_neakasa.tech/9000000030` 也要回落 —— 判扩展名只看最后一段 |
| 漏掉的产物 | `/assets/does-not-exist.js` 仍是 404，不被回落藏住 |
| 接口边界 | `/api/does-not-exist` 仍是 JSON 404；`/api/tasks` 仍由 FastAPI 处理 |
| 非 HTML 客户端 | `Accept: application/json` / `*/*` / 空 一律 404 |
| HEAD | 实测行为：200 + `text/html` + 空 body（资源则保持自己的 content-type） |
| 写方法 | `POST/PUT/DELETE/PATCH /review` → 405，不是一页 HTML |
| 路径越界 | 四种 payload 都拿不到仓库里的文件 |

**这份测试确实会红**：把 `SinglePageFiles` 换回裸 `StaticFiles`，14 条里 **11 条当场失败**。

另外两处也从"只有报告说验证过"变成了有人守：

- `core/operating_settings.py` 落盘失败时**不能**作废配置缓存 —— 新增
  `test_cache_is_not_invalidated_when_the_write_fails`。把 `invalidate_cfg_cache()`
  挪到 `os.replace` 之前，`tests_operating_settings` 7 条里 2 条失败。
- antd loading 图标的离场规则、月历不许再出现无效 list 语义 —— 进了
  `design-discipline.test.ts`，这两样原先只有浏览器回归守着。
- 「提交失败之后必须重读这一篇」进了 `browser_regression.py` 的 Stage D4：
  断言最后一次 `POST /approve` 之后确实有一次 `GET /api/tasks/{id}`。
  把 `useApproval.ts` 里那行 `refresh()` 删掉，Stage D4 当场失败。

### SinglePageFiles 最终审查

只审不改。逐条核过、且有实测支持：只对 GET/HEAD 生效（写方法由 `StaticFiles`
自己挡成 405，根本走不到回落）、不吞 `/api`、不吞真实产物的 404、不吞带扩展名的错误、
Accept 判断合理、Windows 上的 `scope["path"]` 处理有明确注释、
路径安全仍然完全交给 `StaticFiles` 自己的查找。实现没有重写，只补了一段注释
说明"写方法和路径越界为什么不用在这里挡"。

### 冻结时的全量数字

| 项 | 结果 |
|---|---|
| React 单测 | **498 / 498，26 个文件**（上一轮 496，新增 2 条纪律断言） |
| TypeScript | 0 error |
| React 构建 | PASS，JS 1,400.75 kB / gzip 447.66 kB；CSS 17.95 kB / gzip 3.87 kB |
| Vue 回滚构建 | PASS，JS 164.54 kB / gzip 59.68 kB |
| Python 全量 | **66 / 66**（新增 `tests_spa_static`，上一轮 65） |
| `tests_operating_settings` | 7 条；连跑 **20 / 20**（此前已做过 100/100，本轮未改配置逻辑） |
| Stage I | **3 / 3** |
| Stage ALL | **12 / 12** |
| 演练 React | 18 条路径全 200；漏产物 404；接口 404 JSON；深链接与刷新都是「发布月历」；写入 0；页面错误 0 |
| 演练 Vue 回滚 | 11 条路径全 200；页面错误 0 |

正式 Python 测试与演练脚本对同一批行为给出的结论一致，没有分歧。

**网络契约未重跑**：本轮没有动 service / hook 的请求层
（`useApproval.ts` 只改了 seed 判据与原因链，请求体构造未变），
沿用 `network-comparison.json` 的 16 / 16。

### 仍然 deferred

历史缩略图成本（§13 风险 1）按要求**没有碰**：没改默认每页、没加虚拟化、
没加 IntersectionObserver、没缓存 `assert_physical_direct_path`、没动图片接口。
复现脚本与风险说明都保留。

R-8（预留高度）、R-9（全项目格式化）同样未动。

### 现在可以做什么

用户可以建立 release commit 了。文件归属见
[RELEASE_CANDIDATE_MANIFEST.md](RELEASE_CANDIDATE_MANIFEST.md)，
上线当天照着 [CUTOVER_CHECKLIST.md](CUTOVER_CHECKLIST.md) 勾。
