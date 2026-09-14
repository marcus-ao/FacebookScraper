# 审校台 React 前端 · 实施后独立复核

**复核日期：2026-09-13。基线 commit：`2c89f07`（`feat(ui): complete React review console migration`）。**
**复核者与实现者不是同一位。本文不引用 [FINAL_IMPLEMENTATION_REPORT.md](FINAL_IMPLEMENTATION_REPORT.md) 的任何结论作为证据，所有数字都是本轮自己量的。**

复核开工时工作区 `git status --short` 为空，`git log --oneline -1` 为 `2c89f07`。本轮没有 commit、push、reset、rebase、clean，没有改动后端业务代码，没有执行生产 cutover，没有删除旧 Vue。

---

## 1. Executive Review

**APPROVE_WITH_FIXES_APPLIED**

这次重构解决的是它该解决的问题。审校队列 1366×768 首屏从 4 行变成实测 12 行、行高稳定 48px，详情页从 2.49 屏压到 1.47 屏且首屏唯一主按钮就是「通过并创建排期」，月历从文字墙变成一眼能扫的日期格，运营设置把两个她真能改的字段放在最上面。这些不是"看起来更干净"的感觉，是本轮独立量出来的。16 条写入契约与旧 Vue 逐字段一致，七态语义、柏林墙上时刻、`wake_at +08:00`、图片未看不阻断排期这些业务不变量都还在。

但上一位实现者的两条关键结论**在本机复现不出来**，而且其中一条指向一个真实缺陷：

- 「浏览器集成 **12/12 场景组 PASS**」——`--stage I` 在本机是**高频失败**的：把工作区 stash 回未改动的 `2c89f07` 重建 dist 后单跑即失败，在同一失败点连跑 6 次为 **1 PASS / 5 FAIL**（测量方法与边界见 R-1）。根因不是测试脚本写错，是 antd 按钮的 loading 图标离场动画收不到 `transitionend`，把 `aria-label="loading"` 永久留在无障碍树里，按钮的可及名一直是「loading 保存分类」。见 §3 的 R-1。
- 「图片 unseen 不阻断」这条是对的，但同一节提到的"图片因 tab 常驻而提前加载"被当成可接受的代价记在 M 节。实测：**只读正文也会先把当前这张的原图和德语图各拉一次**，而图片响应带 `Cache-Control: no-cache`，这是每进一篇详情都要付的真实往返。见 §3 的 R-2。

本轮改了 6 个前端文件（净 −76 行）、加了 1 个测试文件和 2 个只读探针脚本。修完之后本机 `--stage ALL` 连跑 2 次 12/12，`--stage I` 单跑 6/6，React 434 测试全过，TypeScript 0 错，两套构建通过，Python 65 个脚本本轮 **65/65**。

---

## 2. Original Product Goal

这次重构最初不是因为旧代码用 Vue。真正要解决的是两件事：**旧页面文字量过大**（解释、系统说明、开发者信息常驻，主任务和辅助信息视觉权重接近），和**交互与视觉组件过于原始**（Button / Select / Modal / Card 没有统一语言，像工程 Demo）。目标不是"漂亮"，是**清楚、克制、现代、低噪、精确、高频可用**。

衡量它的唯一尺子是一名上海运营每天重复十几到几十次的那条循环：

> 打开队列 → 挑一篇 → 读英文 → 读德语 → 逐个看标记 → 改文案 → 核对图片 → 选一个柏林时刻 → 通过 → 下一篇

本文所有 UX 判断都只问一句：**这项设计是否让这条循环更清楚、更快、更不容易做错。**

---

## 3. Independent Findings

分级定义沿用本轮授权：P0 = 可能造成错误业务决定、数据损坏、错误写入、流程无法完成或不可逆副作用；P1 = 每天明显拖慢运营、增加认知负担或造成误操作；P2 = 代码质量、视觉一致性、无障碍、性能或长期维护；P3 = 小型 polish。

**P0：0 条。** 本轮没有发现会写错数据、跳过版本校验、把 `scheduled` 说成已发布、或让终态任务重新可写的问题。§9 逐条核对了业务不变量。

### P1

#### R-1 · 保存失败后按钮的可及名永久卡在「loading …」

- **位置**：`web/ui-next/src/styles/global.css`（修复落点）；触发点在任何 `loading` 会快速翻转的 antd 按钮，最容易踩到的是 `features/localization/CategoryEditor.tsx:24` 的「保存分类」。
- **现状**：antd 的 `Button` 用 `@rc-component/motion` 做 loading 图标的进出场，`removeOnLeave: true` 但**没有 `motionDeadline`**（`node_modules/antd/es/button/DefaultLoadingIcon.js:52-61`）。`loading` 在一次 180ms 动效之内 true→false 时，离场的 `transitionend` 收不到，节点就永远停在 `-leave-active`。
- **为什么是问题**：节点的 `width/opacity/transform` 都是 0，屏幕上看不见，所以这不是视觉故障——问题在无障碍树。里面那个 `role="img" aria-label="loading"` 还在，按钮的可及名从「保存分类」变成「loading 保存分类」并且**再也不会变回去**。屏幕阅读器会念一个早就结束的 loading；按可及名定位的自动化也再找不到这个按钮。
- **如何复现**：详情页 → 编辑分类 → 后端返回 409（本机 FastAPI 实测 **8ms** 返回）→ 恢复 → 再点保存。失败时 `document.querySelector('.ant-modal .ant-btn-loading-icon')` 的 class 为 `ant-btn-loading-icon-motion-leave ant-btn-loading-icon-motion-leave-active`，`transitionDuration: 0.18s`，`prefers-reduced-motion` 为 `false`；同时用注入的调试属性确认组件自己的 `busy`、`recovering`、`error` 三个状态**都已经是干净值**——卡住的不是 React，是那个没被移除的图标节点。
- **测量口径（必须说清楚）**：把工作区 stash 回未改动的 `2c89f07` 并重建 dist 后单跑 `--stage I`，失败，与含本轮改动时的失败点、失败现象完全一致；那次 **1 PASS / 5 FAIL** 的 6 连跑是在已含 F-2～F-5（都不碰按钮 loading 与 CategoryEditor）的树上做的。两处证据指向同一个成因，但"6 次里错 5 次"这个具体比例来自后者，不是逐次在 HEAD 上测的。
- **测试覆盖**：有，但被当成通过了——`browser_regression.py:448` 的 `get_by_role('button', name='保存分类', exact=True)` 正是这条断言，它一直在失败，只是 FINAL 报告写成了 PASS。
- **风险**：本身不改任何数据；影响面是"所有会快速失败的保存"，因为本机后端的响应普遍在 180ms 以内。
- **本轮处置**：已修，见 §5 F-1。

#### R-2 · 只看正文也会先把图片拉下来

- **位置**：`web/ui-next/src/pages/review-detail/ReviewDetailPage.tsx`（图片工作区原先常驻在 `hidden` 容器里）。
- **现状**：`ImageWorkspace` 无条件挂载，其中对照区的两个 `<img>` 没有 `loading="lazy"`，所以进详情就发出当前这张的 `original` 与 `de` 两次请求。
- **为什么是问题**：`web/DESIGN.md §7` 规定图片响应 `Cache-Control: no-cache`（避免继续展示旧人工图），所以这两次是**每进一篇详情都要付的真实往返**，而运营的循环里绝大多数时间先读正文。真实帖子多图时这条只会更贵。
- **如何复现**：`docs/ui-refactor/tools/review_probe.py --only images`。修复前 `images_requested_on_text_tab = 2`、打开图片页之后仍是 2——也就是**图片流量 100% 发生在她看之前**。
- **测试覆盖**：修复前无。
- **风险**：纯性能/带宽，不影响正确性。
- **本轮处置**：已修，见 §5 F-2。

#### R-3 · 切一下正文，已经花钱生成的标签建议就没了

- **位置**：`ReviewDetailPage.tsx` 原先用 `{tab === 'localization' && <LocalizationEditor …/>}` 条件渲染。
- **现状**：「话题标签与链接」页一离开就整块卸载，`LocalizationEditor.tsx:21-22` 的 `suggestion` / `selected` 随之丢失。
- **为什么是问题**：生成建议是付费动作（`POST /api/hashtags/task/{id}`，按钮文案就写着「按实际用量计费」）。而且**同一个组件自己会触发这次卸载**：`LocalizationEditor.tsx:67` 的「将链接 N 插入正文光标处」会 `changeTab('text')`。运营生成候选 → 插一个链接 → 回到标签页，候选已经不在了，只能再花一次钱。挂载策略正好反了：不该常驻的图片常驻，该留住的标签页反而卸载。
- **如何复现**：`review_probe.py --only localization`。修复前打开标签页再切回正文，`section[aria-label="话题标签选择"]` 计数 1 → 0；同时图片工作区在正文页仍然是 1。
- **测试覆盖**：修复前无。
- **风险**：重复付费。
- **本轮处置**：已修，见 §5 F-2（与 R-2 同一处修改）。

#### R-4 · 排期回执"不是严格 scheduled"时，收尾入口不出现

- **位置**：`web/ui-next/src/hooks/useApproval.ts` 的 `submit()` 失败分支。
- **现状**：`isScheduledReceipt(result)` 为假时抛错，catch 里只 `options.refetch()`（重读排期条件），**不重读这一篇**。
- **为什么是问题**：`POST /approve` 返回 200 但 `status !== 'scheduled'` 时，后端已经留下发布尝试。前端的 `detail` 还停在提交前，于是 `DecisionPanel.tsx:37` 那个 `detail.publication || detail.status === 'approved'` 判据为假——**「核对并补齐本地回执」这个按钮根本不渲染**。界面上只剩一句「排期尚未确认，请核对回执后再处理」，却没有任何可点的下一步，运营只能整页刷新才能继续。
- **如何复现**：把 `POST …/approve` 的响应改成 `{ok: true, status: 'approved'}`，观察 DecisionPanel 里没有恢复按钮。
- **测试覆盖**：B18 只验了"200 非严格 scheduled 仍报待核对"，没有验之后还走不走得下去。
- **这不是本次重构引入的**：旧 Vue `web/ui/src/components/ApprovalPanel.vue:54-59` 的 catch 同样只 `await load()`。属于原样移植过来的缺口，不是回归。
- **风险**：流程在 UI 里断掉；不会造成重复提交（后端有 revision 校验），但运营会以为系统坏了。
- **本轮处置**：已修，见 §5 F-3。

### P2

#### R-5 · 死文件 `pages/Placeholder.tsx`

122 行，Stage C–H 全部完成后已无任何引用（`grep -rn "Placeholder" src` 除自身外 0 命中），但仍然参与构建与设计纪律扫描。**改哪里**：删除 `web/ui-next/src/pages/Placeholder.tsx`。已修，见 §5 F-4。

#### R-6 · 历史列表用写死下标插列

`pages/history/HistoryPage.tsx` 原先是 `createPostColumns([...5 列])` 之后 `columns.unshift(日期)` 再 `columns.splice(5, 0, 账号)`。那个 `5` 依赖上一行列清单的长度和顺序：任何人改一次列清单，列序就会静默错位，而 Table 不会报错。**改哪里**：同文件按最终列序直接拼数组。已修，见 §5 F-5。

#### R-7 · 月历网格的 `role="list"` 结构不合法

`pages/calendar/CalendarPage.tsx:33-42` 给 CSS Grid 容器加了 `role="list"`，但它的直接子元素里有 7 个星期标题 `div` 和月初的空白补位格，两者都不是 `listitem`。ARIA 规定 `list` 的子元素必须是 `listitem`，混进别的角色时读屏行为不可预期。**改哪里**：同文件。**本轮不改**，理由见 §6。

#### R-8 · 详情正文/图片高度靠三个写死的"上方已用高度"常量

`app/theme.ts:149-151` 的 `proseReservedHeight: 380`、`imageReservedHeight: 528`、`initialPanelHeight: 112` 是"视口减去上面所有东西"的手算结果。吸顶头、字数行、授权区任何一处改高度，正文栏就会算错，而且没有测试守住这个关系。**改哪里**：`theme.ts` + `pages/review-detail/ReviewDetailPage.module.css`，改成 flex 撑满而不是减法。**本轮不改**（属于布局重做，超出精修范围），记为残留风险。

#### R-9 · Stage C–J 的新代码普遍压成超长单行

`features/diagnostics/DetailDrawers.tsx` 最长一行 **765 字符**，`LocalizationEditor.tsx` 707，`features/runtime/model.ts` 604，`pages/settings/SettingsPage.tsx` 585。对比 Stage A/B 留下的 `features/post-list/columns.tsx`（最长 225 字符、有完整注释），风格断层非常明显。这不是"文件太大"，是**一行里塞了三四个职责**，review 和 diff 都读不动（`DetailDrawers.tsx` 的技术诊断整块 JSON 序列化就写在那一行里）。**改哪里**：仅格式，无行为。**本轮不改**——大面积重排会把本次 review 的真实改动淹没在 diff 里，违背"所有修改必须可归因到本次 review"。建议作为独立的一次格式化提交处理，并同时补一条 Prettier/ESLint 约束，否则下一位实现者还会这么写。

#### R-10 · 标签页上重新长出两句常驻系统说明

`features/localization/LocalizationEditor.tsx:57` 的「建议不自动写入。保存会保留超出上限的标签，供人工调整。」和 `:74` 的「插入链接可指定正文位置；未插入的链接将放在文末。仅保存本篇选择。」——`DESIGN.md §10.3` 的"不许常驻"清单里点名的两个例子正是「保存不会自动删减超出上限的标签。」和「这里保存本篇选择，不修改全局映射表。」。**本轮不改**，理由见 §6。

### P3

- **R-11**：`search-params.ts` 对 `platform` / `month` / `tag` 不做白名单校验，非法值直接进历史查询的 query key 并发给后端，后端 pattern 校验 422 后页面显示"暂时无法读取历史归档"。`page` / `limit` / `queue` / `tab` 都已正确回落，只有这三个没有。影响仅限手改 URL。
- **R-12**：`HistoryPage.tsx:21-26` 用 `useEffect` 把解析后的 `page` / `limit` 写回 URL，是典型的"effect 同步 state"。行为正确（只在缺参数时 `replace` 一次），但读起来像有两个真相源。
- **R-13**：月历没有"今天"标记。`2026-09-13` 那一格和别的格子完全一样，运营要靠自己知道日期来定位。
- **R-14**：`DecisionPanel.tsx:19` 的同渠道占用只比较**同一天**的卡片，跨午夜的 90 分钟冲突本地看不出来。这只是缓存提示，真正的判定在后端 409，所以不是正确性问题。
- **R-15**：`useApproval.ts:13` 的 `useEffect` 在 `when` 为空时自动回填，导致运营清空时间输入框后立刻被填回去。
- **R-16**：`useApproval.ts:15` 的 `reason` 是 7 层三元表达式；`ContentJobs.tsx:32-33` 的两个 `*Reason` 同理。逻辑正确但没法读。

---

## 4. Prior Agent Claims Verification

抽查 10 条最关键的 claim，自己复现或重新量。

| # | FINAL 报告的说法 | 本轮实测 | 结论 |
|---|---|---|---|
| 1 | 浏览器集成 **12/12 场景组 PASS** | `--stage I` 在 `2c89f07` 上单跑即失败；同一失败点连跑 6 次 **1 PASS / 5 FAIL**（口径见 R-1）。本轮修复后 `--stage I` 6/6、`--stage ALL` 连跑 2 次 12/12 | **NOT CONFIRMED**（修复后成立） |
| 2 | 首屏 **12 行 @1366、19 行 @1920**，行高 48px | review 12 / 19，history 12 / 19，`rowHeights` 两分辨率都是 `[48]` | CONFIRMED |
| 3 | 详情总高 **≤1.5 viewport** | `review-detail@1366` **1.47** 屏、`@1920` **1.34** 屏 | CONFIRMED |
| 4 | 每个可操作详情首屏 **primary=1** | detail 1（「通过并创建排期」）、settings 1（「保存设置」）、review/history/calendar/runtime 均 0 | CONFIRMED |
| 5 | 行内 **danger=0** | 六个界面 × 两分辨率，可见 `.ant-btn-dangerous` 全为 0；「这篇不发」在 Dropdown 菜单项里，确认按钮才是 danger | CONFIRMED |
| 6 | **无横向溢出**，h1=1 | 12 个页面/分辨率组合，`scrollWidth > innerWidth` 全为 false，`h1` 全为 1 | CONFIRMED |
| 7 | **16/16 写入契约一致** | 本轮重跑 `network_compare.py`，16 个流程的 method/path/完整 body 全等 | CONFIRMED |
| 8 | 图片未看 **不阻断排期** | `ImageWorkspace.tsx:18-19` 只出提示与 Alert，approve 的 `reason` 链（`useApproval.ts:15`）不含图片条件 | CONFIRMED |
| 9 | `scheduled` 文案不写"已发布" | 全站搜「已发布」只出现在月历的「已观测到公开发布」（对应 `delivery === 'published'`），`StatusTag` 里 `scheduled` 是「已排期」 | CONFIRMED |
| 10 | 「图片因 tab 常驻而提前加载」（M 节承认，未列为问题） | 实测正文页就发出 2 次图片 GET，占该篇图片流量的 100% | **PARTIALLY CONFIRMED**——事实属实，但"可接受"的定性不成立，见 R-2 |
| 11 | runtime 保守 mapping：进程活跃不等于成功 | `features/runtime/model.ts` 的 `runtimeSummary` 只在五阶段齐全且无 warning/default 时给「已有运行记录」，`stageSummary` 对 `active` 只给「进程活跃」 | CONFIRMED |
| 12 | Python 首轮 **64/65**，单项复跑通过 | 本轮全量 **65/65**（`state/review-python-suite.log`）；400 次定向复现 0 失败，但证明了成因，见 §13 | CONFIRMED（波动真实存在） |

---

## 5. Fixes Applied

### F-1 · 让 antd 的 loading 图标离场不再挂住无障碍名（对应 R-1）

- **改哪里**：`web/ui-next/src/styles/global.css` 末尾新增一条 `.ant-btn-loading-icon-motion-leave { display: none !important; }`，并把成因写在规则上方。
- **before**：`loading` 在 180ms 内 true→false 时，图标节点停在 `-leave-active`，按钮可及名永久变成「loading 保存分类」。
- **fix**：离场态直接不渲染。视觉上它进入离场时已被 `onLeaveActive` 压到 0 宽，去掉的只是那 180ms 的收缩动画——而 `DESIGN.md §12` 的口径本来就是"只有状态过渡，没有入场编排"。用 `!important` 和焦点环、`prefers-reduced-motion` 是同一个理由：这是底线，不接受组件覆盖。
- **test**：`browser_regression.py --stage I` 本身就是这条的回归，从 1/6 变成 **6/6**；`--stage ALL` 连跑 2 次 12/12。
- **顺带**：`features/localization/CategoryEditor.tsx` 把"重新读取最新分类"拆成独立的 `recovering` 状态，不再和保存共用 `busy`。另外三个冲突恢复入口（正文草稿、排期、设置）本来就是分开的，只有这里挤在一起；恢复只是重读一遍，不该让主按钮转圈。**这一条不是根因修复**——单独做它时失败率只从 5/6 降到 3/6，真正解决问题的是上面那条 CSS。

### F-2 · 详情三个标签页改成"打开过才挂载，之后一直留着"（对应 R-2、R-3）

- **改哪里**：`web/ui-next/src/pages/review-detail/ReviewDetailPage.tsx`。用一个 `opened` ref 记录访问过的 tab；图片与标签页都改成 `opened.current.has(key) && <div hidden={…}>`。正文页是默认页，保持无条件挂载（它没有网络成本，而且插入链接的 `insertAtCursor` 依赖它的 ref 一直在）。
- **before**：图片常驻（提前请求）、标签页条件渲染（切走就卸载）。
- **fix**：两边对调成同一条策略。换篇时整棵子树随 `AppShell` 的 `<Outlet key={location.pathname}>` 重建，`ImageWorkspace` 的 `key={detail.id}` 保留不动。
- **不改变的行为**：吸顶头那个「图片 已看/总数」计数在挂载前后都是 `images.length - 1`（`ImageWorkspace` 的初始 `seen` 就是 `{0}`），所以 B15、D3 依赖的 `图片 1/N` 文案一字未变。
- **test**：新增 `web/ui-next/src/pages/review-detail/tab-mounting.test.tsx`（4 条）。已验证它**能抓住回归**：把挂载条件改回原样后该文件立刻红。真实交互侧由 `review_probe.py` 的 R1/R2 断言守。
- **实测**：正文页图片请求 **2 → 0**；打开图片页后仍为 2（该拉的时候才拉）。标签页 0 → 1 → 切回正文仍为 1。

### F-3 · 回执不确定时重新读这一篇（对应 R-4）

- **改哪里**：`web/ui-next/src/hooks/useApproval.ts` 的 `submit()` catch 分支，加 `await refresh().catch(() => undefined)`。
- **before**：只 `options.refetch()`，`detail` 停在提交前，`DecisionPanel` 的「核对并补齐本地回执」不渲染。
- **fix**：先重读详情再重读排期条件；重读失败不覆盖原始错误（所以运营看到的仍然是真实的失败原因，而不是"读取失败"）。
- **test**：`network_compare.py` 重跑，16 条写入契约不变——这次改动只增加一次只读 GET，没有新增或改变任何写入。

### F-4 · 删除死文件（对应 R-5）

- **改哪里**：删除 `web/ui-next/src/pages/Placeholder.tsx`（−122 行）。
- **test**：`npm run typecheck` 0 错，434 测试通过，构建产物体积无变化（它本来就被 tree-shake 掉了，留着只会误导下一个读代码的人）。

### F-5 · 历史列表按最终列序拼数组（对应 R-6）

- **改哪里**：`web/ui-next/src/pages/history/HistoryPage.tsx`，`unshift` + `splice(5,0,…)` 换成一个按视觉顺序书写的数组字面量，并注明为什么这两列不进共用列工厂（`columns.tsx` 的第 2 条约束：只用列表载荷里真实存在的字段）。
- **before / after**：最终列序完全相同（日期、缩略图、摘要、状态、平台、账号、分类）。
- **test**：`--stage E`（历史 31 条冻结归档的筛选、总数、分页、第二页、详情返回）PASS。

---

## 6. Fixes Deliberately Not Applied

| 编号 | 为什么不改 |
|---|---|
| R-7 月历 `role="list"` | 正确的修法要把星期标题移出 list，而 CSS Grid 要求它们是同级兄弟，得靠 `display: contents` 包一层——为一个次要 ARIA 合法性问题动布局，风险大于收益。另外 `browser_regression.py:361` 的日格最小高度断言正是按 `role="listitem"` 定位的，改结构会连带改既有回归。记为残留风险。 |
| R-8 高度常量 | 属于布局重做，不是精修。本轮授权明确"不重新设计"。 |
| R-9 超长单行 | 大面积重排会让本次 review 的真实改动在 diff 里找不到，违背"所有修改必须可归因到本次 review"。建议单独一次格式化提交 + 一条 lint 约束。 |
| R-10 两句常驻说明 | 这是 `DESIGN.md §10.3`（不许常驻）与 `§9.2`（改变字段业务含义的说明可以留）之间的判断题。「未插入的链接将放在文末」确实改变了她对最终成品的预期，属于 §9.2 的例外；「保存会保留超出上限的标签」更接近 §10.3 点名的系统行为说明。这是措辞偏好的边界，上一位实现者做过取舍，复核者不该只因为自己会写得更短就改掉。 |
| R-11～R-16 | 都是小型 polish 或纯可读性。其中 R-15（清空时间框会被自动填回）、R-13（月历没有"今天"）建议进 backlog，它们对那条日常循环有一点点真实影响。 |
| `tests_operating_settings` 的后端成因 | 见 §13。已证明根因，但修复落在 `core/config.py`，属于后端且不是 UI 行为变更，按授权本轮只提建议不动手。 |
| 拆包（DEF-12） | 见 §11：本机实测交互没有性能问题，1.4MB / 447kB gzip 全部走回环，不值得为 benchmark 引入复杂度。 |
| 拆分 `browser_regression.py` | 见 §12。它确实已经 41KB，但拆分会牵动全部 `browser-stage-*.json` 证据文件，而本轮的新增断言已经放在独立的 `review_probe.py` 里，等于按领域拆了一刀，没有重建框架。 |

---

## 7. UX Review

逐页在 1366×768 与 1920×1080 实际打开看过，不是只读 DOM。

**审校队列。** 第一眼落在摘要列上，这是对的——她要回答的就是"现在打开哪一篇"。红色只出现在最左边那一列（问题指示），位置固定，扫一列就够；这是相对旧版 17 个红色行内按钮最大的改善。12 行密度没有换来拥挤：48px 行里只有一行文字加一枚 40px 缩略图，行间有呼吸。三个筛选下拉在右上角，视觉重量明显低于表格本身，不抢注意力。

两点保留意见，都不构成缺陷：一是「待我审」桶里状态列几乎全是「待我审」，只有偶尔一个「已修改」——这一列在当前 tab 下信息量接近 0，但 `DECISION_LOG` 明确要求每行显示真实 status（尤其"已处理"桶里必须区分 approved/scheduled/skipped/handed_off），所以这是有意的取舍。二是真实文案的前缀高度重复（多篇都是 `Hallo Katzeneltern! 😸 Ein kurzes Update aus dem Studio.`），区分度全靠开头几个词；这是数据特征不是 UI 问题，但如果以后摘要能优先取标题行会更好扫。

**详情（最重要的一页）。** 第一屏确实是"英德正文 + 当前问题 + 主动作"。56px 吸顶条三段分工清楚：左边返回与相邻篇、中间平台/状态/`0 错 · 2 待确认 · 3 注意`/图片进度、右边编辑与唯一主按钮。1366 下英德两栏各约 560px，15px/1.7 的正文一行落在 30 字上下，读起来不串行。标记的视觉重量排序肉眼可辨：error 是浅红底加深红实线，warn 浅红底浅线，risk 只有黄褐下划线——`$99.00` 和 `12 in wide` 放在一起一眼能分出哪个更要紧。右侧没有抢正文空间的决策面板，排期区在正文下方，这个选择是对的：她读完才排期。

三个 Tab 比旧版纵向堆叠高效，因为正文和图片本来就不同时看。图片页的 contact sheet 好用：缩略图带"看过"勾和"缺德语图"标注，缺德语图那张同时有行内提示和满宽 Alert，不会漏。付费区（单篇优化）收在 Collapse 里且是 `default` 形态带金额，没有和主按钮竞争。第三方授权用 warning Alert 出现在正文之前——这一条属于"看不到就会做错决定"，常驻是对的。

**历史归档。** 与队列共用同一套行，多了日期和账号两列，冻结账号带锁图标和"只供查阅"。分页控件在标题行右侧、首屏可达。从历史进详情再返回，筛选、页码、每页数都回来了。

**发布月历。** 从文字墙变成了真正的时间扫描工具：周一起始，每格显示"时刻 + 渠道 + 业务状态"，正文进 Popover。状态措辞守住了业务口径——「已观测到公开发布」/「已创建定时任务」/「发布状态待核验」三分，没有任何地方把 `scheduled` 写成"已发布"。一天多条时格子会长高，本轮 fixture 最多一天一条，多条撑爆的情况没有真实样本，记为未验证项。缺一个"今天"标记（R-13）。

**运营设置。** 第一眼就是那两个她真能改的字段，`Card` 里两行，说明写在 `Form.Item` 的 `extra` 上且都是业务语言。下方七组受控配置全部收起，没有变成新的信息墙。`config.toml` 的开发者注释仍然在数据里（`web/DESIGN.md §7` 要求"注释未丢"）但收在「运营字段的配置说明」折叠里，没有把源码路径摊在她面前。

**运行状态。** 顶部「需要你处理」把可执行的事项拎出来了，中断批次和飞书投递各带一个确认动作。但阶段 1 显示「尚未确认 / 现有记录不足以确认这一阶段的结果。」时，一个非技术运营看完仍然不知道该做什么——这是诚实的代价：后端确实没给判别依据，按口径不能编。我认为这是正确的取舍，不是缺陷；真要改善需要后端补字段（DEF-1 同类）。

---

## 8. React Architecture Review

**总体：没有"为了架构而架构"。** 我特意找了授权里列的十二种过度抽象，命中很少：

- 服务层是 `fetch` 的一层薄包装，不是三层：除了 `http.ts`（传输层）和 `assert-shape.ts`（契约断言），按领域分的九个模块都是 4–12 行（`settings.ts` 4 行、`calendar.ts` 4 行、`tasks.ts` 8 行）。它们的存在理由是 URL 与 body 只写一份，成立。
- 没有为未来写的 generic interface。`columns.tsx` 的适配器设计（少传一个适配器那一列就整根消失，而不是渲染成空）是从真实差异出发的，只有三处差异就只有三个可选适配器。
- `any` / `@ts-ignore` / `@ts-expect-error` / `eslint-disable` **全库 0 处**；非空断言 1 处（`useContentJob.ts:12`，有 `enabled: !!selected` 配对，成立）。
- `catch {}` 吃异常的地方只有一处：`useLocalization.ts:33` 的 `/check` 失败静默——这是**有意的**，`web/DESIGN.md` 和 `DESIGN.md §9.4` 都写了校验不能挡保存，注释也说明了。

**Router / URL。** URL 确实是筛选上下文的唯一真相源。`search-params.ts` 是纯函数、零 React 依赖、有 255 行测试。`location.state` 只在一个地方出现（`ReviewPage.tsx:24` 的 `refreshReview`），而且只当加速手段用，刷新后为 null 也不影响正确性。旧 `?task=` / `?view=` 永久重定向在位。非法 `queue` / `tab` / `page` / `limit` 都会回落，`platform` / `month` / `tag` 不回落（R-11）。

**Query。** key 里的变量确实都影响 `queryFn`：`historyListOptions` 把全部筛选放进 key，`useApproval` 的 approval-options key 带源文哈希与三个 revision（内容一变就重新核对发布条件，这是对的）。队列共享 `['tasks','review']` 且 `refetchOnMount:false` / `gcTime:Infinity`，筛选在已取到的列表里做——所以本轮实测"首次载入 + 五项筛选 + 详情往返"只有 1 次列表 GET，写入后 0 次。`useContentJob` 的 `refetchInterval` 只在 `pending/running` 时返回毫秒数，终态返回 `false`，轮询会停。

`patchReviewList`（`features/post-list/model.ts:19`）是唯一有 race 风险的地方，我逐条核过：状态桶计数只在 `previous.status !== detail.status` 时增减，不会双减；`by_status` 用 `Math.max(0, …)` 兜底；行留在原地只改字段，不搬桶（桶是渲染时按 `bucketOf` 算的，所以不存在"stale row 留在错误 tab"）；详情与列表在 `cacheTask` 里一次更新，不会只更一边。

**State。** 服务端状态没有被复制进 local state（唯一例外是 `RuntimePage` 的 `data`，那是**有意的**快照——30 秒轮询不能在她读的时候把页面换掉，`runtimeSignature` 比较后提示"有更新"）。派生值基本都在渲染时算。用 `useEffect` 同步 state 的只有三处：`LocalizationEditor.tsx:25`（`[editing]`，逐个场景核过不会造成输入与草稿不一致）、`useApproval.ts:13`（R-15）、`HistoryPage.tsx:21`（R-12）。没有发现 `useMemo` / `useCallback` 的滥用——非测试源码里 `useMemo` 只有 **1 处**（`useLocalization.ts:23`，包住真实有成本的 `buildMarks`），`useCallback` **1 处**。

**Components。** 没有只有一行的 wrapper，没有只有一个 caller 的 helper 被抬到共享层。`ContentJobs.tsx:54` 用 `createPortal` 把第三方授权区投到正文之前，是全库最绕的一处；它解决的是真实约束（授权提示必须在正文上方，但它的状态属于任务流组件），可以接受。

**唯一需要指出的结构问题**是 R-9 的书写密度：职责是清楚的，但一行 700 字符让这些正确的设计读不出来。

---

## 9. API / Business Contract Review

逐条核对授权列出的业务不变量：

| 不变量 | 结论 | 依据 |
|---|---|---|
| 所有 write 带正确 revision / hash | 通过 | `localizationBody` / `decisionBody` / `approvalBody` / `jobVersions` 四个构造器；16 条写入与 Vue 逐字段一致 |
| `/check` 绝不阻止 save | 通过 | `useLocalization.ts:33` 静默失败；`issues` 只出 Alert；保存按钮的 disabled 只看 `saving` / `recovering` |
| 图片未看完不阻断 approval | 通过 | Tooltip 明写"此提示不阻止排期"；`reason` 链不含图片条件 |
| approval 200 ≠ 成功 | 通过 | `isScheduledReceipt` 严格要求 `ok === true && status === 'scheduled'` |
| DST：柏林墙上时刻不过本地时区 | 通过 | `formatSchedule` 手工解析字符串里的偏移；`berlinInput` 显式 `timeZone: 'Europe/Berlin'`；测试跑在 `TZ=America/New_York` 下，任何本地时区泄漏会当场露馅 |
| `wake_at` 带 `+08:00` | 通过 | `services/review.ts:20` |
| marks 用 Python codepoint 语义 | 通过 | `lib/marks.ts` + 231 行测试；补充平面表情定位有专门用例 |
| 人工文案 / 人工图优先 | 通过 | `text.de_human \|\| de_machine`；`image.de_url \|\| original_url` 并明示回退 |
| `read_only` 冻结账号无写入入口 | 通过 | `ReviewDetailPage` 三处 `!detail.read_only` 闸；`ReviewActions` 自己也判 `read_only`；`canEditTask` 再判一次 |
| `scheduled` 不显示"已发布" | 通过 | 见 §4 第 9 条 |
| skip 必须有理由 | 通过 | `ReviewActions.tsx:55` 的 `disabled: action === 'skipped' && !form.reason.trim()` |
| 付费动作的 consent / 金额 / 次数 | 通过 | 无 consent 时 `firstReason` 非空即 disabled；`PaidActionButton` 用类型堵住"灰着但说不出原因"；图片优化显示剩余次数与单价 |

**没有发现业务契约被 UI 重构削弱的地方。** 本轮唯一改动到业务流的是 F-3，它只增加一次只读 GET。

---

## 10. Accessibility Review

自己验，不接受上一轮的"PASS"。

**通过的：** 焦点环全局 2px 主色 + 2px offset，`html:root :focus-visible` 用 `!important` 压过 antd 八条组件级规则（这是必要的，否则同屏两种焦点环）；Modal/Drawer 的打开焦点、Esc 关闭、关闭后焦点回到触发元素由 antd 保证，`useDialogTabLoop` 只补首尾 Tab 循环；`lang="zh-CN"` + antd `zhCN` + dayjs `zh-cn`；动态区域用 `role="status"` / `role="alert"`（`ConflictRecovery` 用 alert，正文标记说明用 status）；状态一律"颜色 + 文字"，`StatusTag` 从不只靠颜色；`capture_final.py` 的 20 张截图跑过无障碍检查。

**修掉的：** R-1 让按钮可及名永久错误——这是本轮最实在的一条无障碍缺陷，而且它是**沉默的**（视觉完全正常）。

**仍需留意的：**

- `ProblemIndicator` 的具体告警文案只在 Tooltip 里，但组件同时把它写进了 `aria-label`（`ProblemIndicator.tsx:94`），所以读屏可得，键盘 Tab 到行时也能听到——**这一条没有问题**，授权里担心的"只有 hover 才知道"不成立。
- `PaidActionButton` 的 disabled 原因走 Tooltip + 原生 `title` 双保险（`PaidActionButton.tsx:73-77`）；`ApprovalAction` 同样（`DecisionPanel.tsx:12`）。disabled 元素本身不可聚焦，所以严格说键盘用户仍拿不到原因——这是 HTML disabled 的固有限制，要彻底解决得改成 `aria-disabled` + 拦截点击。**记为残留风险**，不在本轮改。
- 正文里的 `<mark>` 可点但不可聚焦（`TextWorkspace.tsx:23`）。键盘用户靠 N / P 和「上一处 / 下一处」按钮遍历，路径是通的，所以不算"对键盘不存在"。
- R-7 的月历 ARIA 结构不合法。

---

## 11. Performance Review

**先测再说，没有为 benchmark 引入任何新依赖。**

冷启动与交互都在隔离宿主 + 真实 Chrome（1366×768）下跑：12 个页面/分辨率组合的 `wait_until="networkidle"` 全部在默认超时内完成，`page_errors` 为 0。列表 26–33 行的 antd Table 无虚拟滚动，切 tab、筛选、进出详情没有可感延迟。

**bundle：1,398.89 kB raw / 446.99 kB gzip，本轮修改后与基线完全一致**（删掉的 `Placeholder.tsx` 本来就被 tree-shake）。这个体积触发了 Vite 的 500kB 告警，但：它全部走回环，生产机是同一台 Windows 桌面，首屏没有外链字体，实测没有可感的启动延迟。**按授权"如果性能没问题，bundle >500k 不需要修"，不拆包。** 真要拆，第一刀应该是 route-level lazy import（`DesignCheck.tsx` 245 行是纯核验页，生产导航里没有入口，最适合第一个懒加载），而不是 `manualChunks`。

**本轮实际改善的性能项**是 R-2：每进一篇详情少两次全尺寸图片往返。在只读正文的场景下，这是该篇 100% 的图片流量。

`useRuntime` 的 30 秒轮询是全局共享的单个 query（Header 与运行状态页同 key），后台标签页会自动暂停，没有重复定时器。

---

## 12. Test Quality Review

**434 个 React 测试不等于 434 份安全感。** 分层是合理的：纯逻辑（marks / format / http / search-params / 契约 shape）在 Vitest 里，跨组件真实流程在 Python Playwright 里，这个边界划得对。

需要指出两件事：

1. **SSR 字符串断言的边界要说清楚。** 项目没装 jsdom / testing-library（依赖零新增是明确决定），组件测试用 `renderToStaticMarkup` 断言 HTML 字符串。这能可靠验证"初次渲染画了什么"——我加的 `tab-mounting.test.tsx` 正是用它，而且验证过它会红。但它**验不了任何交互后的状态**，也验不了 antd 的浮层（portal 在 SSR 里不出现）。所以 R-3"切走之后还在不在"只能由浏览器测试守。这不是假安全感，前提是不要把它当成组件测试的全部——目前的 23 个文件里没有越界的。
2. **`browser_regression.py` 已经 41KB / 12 个 stage**，是典型的 God Script。授权允许按领域拆，但拆分会牵动全部 `browser-stage-*.json` 证据。本轮的处理是：**新断言不往里堆**，放进独立的 `docs/ui-refactor/tools/review_probe.py`（图片挂载、标签页存活、密度、标记键盘、月历语义五组，外加每次运行都统计的外部动作计数）。这等于按领域拆了第一刀，没有重建框架。

**测试盲区（本轮未补）：** 月历一天多条卡片的布局、真实多图帖子的 contact sheet 翻页、`patchReviewList` 的并发写入（两个 tab 同时改同一篇）。

---

## 13. Flaky Python Test Investigation

**结论：根因已证明，本轮不改后端。**

`tests_operating_settings.test_preserves_other_bytes_and_reloads_current_values` 首轮失败的断言是 `3 != 4`（`tests/tests_operating_settings.py:34`）。

成因在 `core/config.py:169-177`：`cfg()` 用 `(st_mtime_ns, st_size)` 判断配置文件是否需要重载。而这个测试里的 `save()` 把 `["10:00", "17:00"]` 改成 `["11:30", "18:00"]`、`snooze_default_days = 3` 改成 `= 4`——**两处替换都不改变字节数**。于是能否重载完全取决于 `st_mtime_ns` 是否变化。

两组实测（`docs/ui-refactor/tools/flaky_settings_probe.py`，本机 Windows 11 / NTFS）：

| 度量 | 结果 |
|---|---|
| 定向复现该用例 **400 次** | 失败 **0** 次 |
| 同一次里 `st_size` 前后相同 | **400/400（100%）**——stamp 的 size 那一半对这个测试完全无效 |
| `st_mtime_ns` 前后相同 | 0/400，最小间隔 **996,300ns ≈ 1ms** |
| 背靠背等长改写 **5000 次**，两次 `st_mtime_ns` 相同 | **3421 次（68.42%）** |

也就是说：文件时间戳的有效分辨率约 1ms，**同一个刻度内的两次等长写入会共用 mtime**，这时 `cfg()` 完全看不见修改。空载时 `read()` + `save()` 稳定跨过 1ms 所以复现不出来；全量套件下调度不同就会偶发落在同一刻度内——这正好解释"首轮 64/65、单独复跑通过"。本轮全量重跑 **65/65**，没有再现。

**生产影响评估：** `operating_settings.read()` 直接读文件，所以设置页显示的永远是新值；只有长驻 Web 进程在同一毫秒内再次调用 `cfg()` 时会读到旧值，下一次调用即自愈。影响很小，但它是真实存在的判据缺陷，不只是测试问题。

**建议（未实施）：** 在 `core/config.py` 的 stamp 里加一个廉价的内容判据，或者让 `operating_settings.save()` 写完后显式刷新 `config._cfg`（它本来就知道自己写了）。这属于后端且不是 UI 行为变更，按授权只提建议。

---

## 14. Safety Evidence

| 真实外部动作 | 本轮次数 |
|---|---:|
| approve / 创建远端排期 | **0** |
| calendar refresh / 远端刷新 | **0** |
| paid model / 模型计费 | **0** |
| Feishu resend / 飞书重发 | **0** |

所有浏览器验证都跑在 `tests/browser_fixture.py` 的隔离宿主上：临时 archive/state、`socket.connect` 限回环、除少数隔离写入外的非 GET 一律 503、退出时校验真实 `config.toml` 未变。`review_probe.py` 每次运行都会统计并打印这四项计数，最新一次全为 0，`non_get_requests` 为空数组，`page_errors` 为空。

**对真实数据的读取：0 次。** 本轮没有连过 `config.local.toml` 绑定的真实归档，连只读 GET 都没有——上一轮已有的真实只读取证（26 篇待办 / 1,067 篇历史 / 月历 / 设置 / runtime）足够，没有必要重取。

没有请求或使用额度重置工具。

---

## 15. Regression Results

| 检查 | 结果 |
|---|---|
| React `npm test` | **434/434 PASS，23 文件**（原 430 + 本轮新增 4） |
| TypeScript `npm run typecheck` | **0 错** |
| React `npm run build` | PASS，JS 1,398.89 kB / gzip 446.99 kB（与基线一致） |
| Vue `npm --prefix web/ui run build` | PASS，JS 164.54 kB / gzip 59.68 kB |
| Python 全量 `tools/test_offline.py` | **65/65 脚本 PASS**，`state/review-python-suite.log` |
| 浏览器 `--stage ALL` | **12/12 场景组 PASS**，修复后连跑 2 次 |
| 浏览器 `--stage I` 稳定性 | 修复前 **1/6**，修复后 **6/6** |
| 新旧网络契约 | **16/16 写入 method/path/完整 body 一致** |
| 最终视觉 | 20 张截图重新捕获，无障碍检查通过 |
| 本轮新增探针 | `review_probe.py` 五组断言全过 |

B1–B24 的覆盖关系未变（本轮没有删除或放宽任何既有断言），其中与本轮改动直接相关的是：B4（三路径 dirty guard，D1/D/G）、B7（草稿/排期/分类/审校/设置 CAS，D1/D4/I/G）、B15（图片未看不阻断，D3/D）、B16（缺德语图回退，D3）、B18（200 非严格 scheduled，D4）、B19/B24（历史分页与 limit，E）。全部随 `--stage ALL` 通过。

---

## 16. Git Diff

本轮修改（相对 `2c89f07`），源码部分：

```text
 web/ui-next/src/features/localization/CategoryEditor.tsx   |  12 +-
 web/ui-next/src/hooks/useApproval.ts                       |  10 +-
 web/ui-next/src/pages/Placeholder.tsx                      | 122 ---------
 web/ui-next/src/pages/history/HistoryPage.tsx              |  21 +--
 web/ui-next/src/pages/review-detail/ReviewDetailPage.tsx   |  11 +-
 web/ui-next/src/styles/global.css                          |  20 ++
 6 files changed, 60 insertions(+), 136 deletions(-)
```

新增文件：

```text
 web/ui-next/src/pages/review-detail/tab-mounting.test.tsx   （4 条断言，守 F-2）
 docs/ui-refactor/tools/review_probe.py                      （独立复核探针，5 组断言）
 docs/ui-refactor/tools/flaky_settings_probe.py              （§13 的定向复现器）
 docs/ui-refactor/POST_IMPLEMENTATION_REVIEW.md              （本文）
```

证据文件因重跑而更新：`browser-stage-*.json`、`browser-stage-all.json`、`network-comparison.json`（其中 `explanation` 一句按 F-2 后的实际行为改写）、`final-measurements.json`、`screenshots/final-*.png`。`docs/ui-refactor/tools/network_compare.py` 改的只是这句说明文字。

**没有 commit、push、PR、reset、rebase、clean。** 后端 Python、旧 Vue 源码、`config.toml` / `config.local.toml`、两套 `package.json` / lock 一个字节都没动。

---

## 17. Remaining Issues

| 残留 | 影响 |
|---|---|
| R-7 月历 `role="list"` 结构不合法 | 读屏在月历上的行为不可预期。改它要动布局，未做。 |
| R-8 详情高度的三个写死常量 | 任何人改吸顶头或字数行的高度，正文栏就会算错，且没有测试守住。 |
| R-9 新代码的超长单行 | 维护成本；下一次 review 会更难。建议独立格式化提交 + lint 约束。 |
| disabled 按钮的原因对键盘用户不可达 | HTML disabled 的固有限制，要改成 `aria-disabled` 才能彻底解决。 |
| `core/config.py` 的 `(mtime_ns, size)` 重载判据 | 根因已证明（§13），后端未改。长驻进程在同一毫秒内可能读到旧配置，下一次调用自愈。 |
| 月历一天多条卡片 / 真实多图帖子的 contact sheet | 没有真实样本，未验证。 |
| `patchReviewList` 的多标签页并发 | 两个标签页同时改同一篇的行为未测。 |
| DEF-1 / DEF-10 / DEF-13 | 上一轮既有的后端能力缺口，本轮未触及，也未被掩盖。 |

---

## 18. Recommendation

**READY_WITH_MINOR_FOLLOWUPS**

理由：这个新前端在那条日常循环上确实更清楚、更快、更不容易做错，本轮独立量到的密度、层级、按钮层级数据全部达标；16 条写入契约与旧版逐字段一致，业务不变量没有一条被 UI 重构削弱。本轮修掉的四个问题里，两个有真实业务代价（重复付费、每篇多两次图片往返），一个让流程在 UI 里断掉，一个让按钮的无障碍名永久错误并且掩盖了一个一直在失败的回归。

跟进项都不阻断受控切换：R-7/R-8/R-9 是维护性，`core/config.py` 是既有后端缺陷且影响自愈，其余是 backlog。

**本轮未执行生产 cutover，未删除旧 Vue，未修改生产配置，未 commit / push。** 切换步骤仍以 FINAL 报告 T 节为准；建议切换前先把 R-9 的格式化和 lint 约束落掉，否则下一位实现者会继续往同一个方向写。
