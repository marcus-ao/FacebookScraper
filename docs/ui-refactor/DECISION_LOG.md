# 决策记录（Decision Log）

**锁定日期：2026-09-13。** 决策人：产品负责人。审计与提案基线：`3536e29`。

这份文件是**产品决策的真相源**。审计阶段的 [OPEN_QUESTIONS.md](OPEN_QUESTIONS.md) 里那 13 个问题全部有了答案；其它文件里凡与本文件冲突的描述，都以本文件为准，并已就地改掉。

新增或改变一条决策，改这里，然后同步受影响的文件。

---

## 0. 本轮批准的执行范围

| 批准 | 内容 |
|---|---|
| ✅ | Decision Lock + 文档收口 |
| ✅ | Stage A（基础设施、三个纯逻辑模块、领域类型、路由骨架、单测、dist 可配） |
| ✅ | Stage B1（应用外壳 + 设计系统定值）与 Stage B2（共享基元）—— 见 [STAGE_B_REPORT.md](STAGE_B_REPORT.md) |
| ✅ | 2026-09-13 后续授权：C → E′ → D1–D5 → F → G → H → I → J 连续实施，结果见 FINAL_IMPLEMENTATION_REPORT.md；阶段之间无需再次批准 |
| 未执行 | 生产 cutover、真实发布/付费/飞书操作、Git 提交与推送；额度重置最多 1 张，本次实际使用 0 张 |

**Stage B1 关掉的一项待定**：主色定为 **`#155EEF`**（对白色 5.41:1），判据与理由见 [DESIGN.md §5.2](DESIGN.md)，可复算的断言在 `web/ui-next/src/app/theme.test.ts`。

---

## 1. 十三条产品决策

### D1 — 队列分四个页签（原 Q1）

| 页签 | 包含的真实 status |
|---|---|
| **待我审** | `pending_review`、`edited` |
| **未就绪** | `not_ready` |
| **已挂起** | `snoozed` |
| **已处理** | `approved`、`scheduled`、`skipped`、`handed_off` |

四条附带约束：

1. **「已处理」只是 UI 分桶，不改变任何底层状态语义。** 每行仍然显示真实 status。
2. **`not_ready` 的用户可见文案改成「未就绪」**，不再显示「待处理」。这是对 `format.js` 里 `STATUS_LABEL.not_ready` 的一处有意改动，见 §3.1。
3. `not_ready` 的行**仍然可以打开详情**。
4. 第三方作者需要人工授权/初翻时，**留在「未就绪」里用 `ProblemIndicator` 明示**，不回塞「待我审」。

**改哪里**：`web/ui-next/src/lib/format.ts`（`STATUS_LABEL`）；Stage C 的队列分组与页签。

### D2 — 历史分页（原 Q2）

默认 **50**，可选 **20 / 50 / 100**，`limit` **必须进 URL**。

**改哪里**：Stage E′ 的历史列表；URL 契约见 §2.2。

### D3 — 本版不做批量动作（原 Q3）

第一版**不显示 checkbox 列、不启用 `rowSelection`、不显示批量操作条、不做任何批量 API**。

架构保持将来可加，但**不为"预留"在当前 UI 放没有功能的控件**。

**已同步改掉**：[UI_ARCHITECTURE_PROPOSAL.md §4.2/§4.4/§4.5](UI_ARCHITECTURE_PROPOSAL.md) 里的选择列、批量条与 `rowSelection` 映射。

### D4 — 本版不做历史搜索（原 Q4）

后端没有搜索契约之前，**不显示搜索框**。

### D5 — 「N 次提交结果需要核对」延期到 Stage H（原 Q5）

产品结论：**最终必须能直接定位具体帖子。**

但这是新的后端字段能力。**本轮 Stage A 不修改 runtime snapshot。** 见 §4 的 DEF-1。

### D6 — 标签输入保留 textarea（原 Q6）

保留 `textarea`、支持整段粘贴；**下方实时解析成 chips，可单个删除**。第一版不强制切换到 `Select mode="tags"`。

**已同步改掉**：[UI_ARCHITECTURE_PROPOSAL.md §5.6](UI_ARCHITECTURE_PROPOSAL.md) 的「待定」。

### D7 — 月历与排期只做轻方案（原 Q7）

**做**：详情决策区显示同渠道占用情况（数据取自 `/api/calendar` 的 `cards`，不新增接口）。

**不做**：从月历点空槽直接给某篇排期。

归属 **Stage D4**，不属于 Stage A。

### D8 — 忽略 `fixtures/risks.json`（原 Q8）

本轮忽略，**不为"演示模式"留任何 UI**。

### D9 — 运行状态移出主业务导航（原 Q9）

主导航只留四项业务界面。顶栏保留状态指示器，点击后进入完整 `/runtime` 页面。

**不同时保留主导航入口。**

### D10 — 风险预扫四态（原 Q10）

| 状态 | 呈现 |
|---|---|
| `not_scanned` | 中性状态标记，**不出 Alert** |
| `completed` + 0 风险 | **不渲染 Alert** |
| `completed` + 有风险 | 正文标记 + 当前项说明条 |
| `failed` / `stale` | `warning` Alert |

### D11 — 旧 URL 永久兼容（原 Q11）

`?task=` / `?view=` 的重定向**永久保留**，不设删除期限。

### D12 — 旧 `web/ui/` 无删除日期（原 Q12）

在产品显式批准之前，**一直保留、一直可构建、一直可回滚**。

### D13a — 批准的既有缺陷修复：`task.alerts` → `hard_alerts`（原 Q13a）

`web/ui/src/components/TaskList.vue:138` 写的是 `task.alerts?.some(...)`，而列表载荷里字段名是 `hard_alerts`，所以 `task.alerts` 恒为 `undefined`，第三方作者帖子的「查看并翻译」文案**从未渲染过**。

**决策：修。** 记为"批准的既有缺陷修复"，并加入回归测试。

本轮的实际落地（三层，见 §5.3 的执行记录）：

| 层 | Stage A 做了什么 |
|---|---|
| 旧 Vue 源码 | 一行字段名修正（`alerts` → `hard_alerts`） |
| 新 UI 类型层 | `ReviewListItem` 不存在 `alerts` 键，任何 `.alerts` 访问在 `tsc` 阶段就失败 |
| 契约层 | 对真实 `tasks.json` 的 shape 测试断言"有 `hard_alerts`、没有 `alerts`" |

浏览器层面的回归场景（第三方作者帖子在队列里的文案）归 **Stage C**，记为 [BASELINE_BEHAVIOR.md §12](BASELINE_BEHAVIOR.md) 的 **B21**。

### D13b — `fake_writer.py` / `_fake_state.json` 不动（原 Q13b）

---

## 2. 四个架构问题的修正

审计提案里有四处需要在写生产代码之前改掉。以下是最终口径，相关文件已同步。

### 2.1 队列页签的 URL 参数叫 `queue`，不叫 `status`

**原因**：一个页签包含多个真实 status（D1），用 `status` 表示页签会和"真实状态"这个词冲突，也会让人以为它能直接透传给后端的 `status` 查询参数。

```
/review?queue=review        ← 待我审（pending_review + edited）
/review?queue=not_ready     ← 未就绪
/review?queue=snoozed       ← 已挂起
/review?queue=processed     ← 已处理
```

**真实状态仍然叫 `status`**，留给"按单个真实状态筛选"这类将来可能的需求，以及后端已有的 `status` 查询参数。

`queue` 缺省时等于 `queue=review`。未知值回落 `review`。

### 2.2 详情 URL 必须携带来源列表上下文

**原因**：详情页要回答"第 n / N 篇"、要支持上一篇/下一篇、返回列表要恢复原筛选。这些都不能依赖内存状态，否则刷新详情页就全丢。

**从审校队列进入**，保留：`queue`、`platform`、`month`、`tag`、`alerts`，加详情自己的 `tab`。

```
/review/fa_neakasaofficial/122100548013379375?queue=review&platform=facebook&month=2026-07&tab=text
```

**从历史进入**，保留：`platform`、`month`、`tag`、`page`、`limit`，加 `tab`。

```
/history/in_neakasa.tech/3975547640610092585?platform=instagram&month=2026-08&page=2&limit=50&tab=text
```

四条硬要求：

1. 浏览器**刷新详情页后仍然知道"第 n / N 篇"**；
2. **上一篇 / 下一篇按进入详情时的筛选结果工作**；
3. **返回列表恢复原来的筛选和页码**；
4. **不把 React Router 的 `location.state` 当作唯一真相源**（它在刷新后为 `null`，也不能被分享）。

实现口径：详情页用 URL 里的这组参数重新发起**同一个列表查询**（TanStack Query 会命中列表页已有的缓存，不产生额外请求），从结果里算出当前 index 与上下相邻项。`location.state` 只允许作为"避免一次重新计算"的加速手段，不允许作为唯一来源。

### 2.3 列表里不做「译文四态」列

**原因**：列表 API 无法可靠区分"机器 / 人工 / 旧提示词"。这三者的依据（`text.de_human`、`text.machine_current`、`text.machine_prompt_version`）**只在详情载荷里有**；列表项只有 `text_de_excerpt`。

**禁止根据缺失字段猜测。**

Stage C 第一版：**要么不显示该列，要么只显示现有 payload 能可靠证明的「未就绪 / 有译文」**（判据：`text_de_excerpt` 是否为空串）。

将来确实需要四态，再单独增加后端字段，不在前端猜。

**已同步改掉**：[UI_ARCHITECTURE_PROPOSAL.md §4.2](UI_ARCHITECTURE_PROPOSAL.md) 的列定义、[UI_AUDIT.md §6](UI_AUDIT.md) 的专项表。

### 2.4 第一版没有批量，所以删掉相关设计

见 D3。已从 [UI_ARCHITECTURE_PROPOSAL.md](UI_ARCHITECTURE_PROPOSAL.md) 删除首版的 checkbox 列、`rowSelection` 与批量操作条。

---

## 3. 对既有实现的有意改动清单

Stage A 的原则是"三个纯逻辑模块算法 1:1，只加类型"。下面是**唯一被批准偏离这条原则的地方**，逐条记录以免将来被当成移植错误。

### 3.1 `STATUS_LABEL.not_ready`：`待处理` → `未就绪`

依据 D1 第 2 条。这是产品决定的用户可见文案变更，不是移植失误。

旧值保留在 `web/ui/src/format.js`（旧应用继续用旧文案，两份并行期间不强制一致）。

**其余七个状态文案、`AUTHOR_KIND_LABEL`、`RISK_KIND_LABEL`、`ACTION_LABEL`、`WEEKDAYS` 一字不改。**

---

## 4. 延期项（deferred）

| # | 内容 | 归属 | 前置 |
|---|---|---|---|
| **DEF-1** | `pipeline/runtime_status.py` 的阶段 5 增加 `task_ids`，让"N 次提交结果需要核对"能直接链到具体帖子；需补后端测试与前端链接 | **Stage H 再评估** | 本轮不改 runtime snapshot（D5） |
| **DEF-2** | 历史归档全文/摘要搜索（需要后端查询契约） | 未排期 | D4 |
| **DEF-3** | 批量"稍后再审 / 这篇不发" | 未排期 | D3，需先量实际需求 |
| **DEF-4** | 从月历空槽发起排期 | 未排期 | D7 只做轻方案 |
| **DEF-5** | 列表「译文四态」列（需要后端新增字段） | 未排期 | §2.3 |
| **DEF-6** | 正文分栏可拖动比例（`Splitter`） | 未排期 | §5.2，需运营确有需求 |
| **DEF-7** | `Table` 虚拟滚动 | 未排期 | §5.2，当前数据量不值得 |
| **DEF-8** | 演示模式与 `fixtures/risks.json` | 不做 | D8 |
| **DEF-9** | 删除 `web/ui/`、`web/ui-next` 改名、挂载点改回硬编码 | 待显式批准 | D12 |
| **DEF-10** | 飞书恢复自动展示接收组与原消息：当前 Outbox.status 不返回，界面要求运营核对后填写 | 后续后端契约 | Stage H 实测，当前写入 body 不变 |
| **DEF-11** | 原 tests_operating_settings 快速配置重载偶发失败：首轮 1 项失败，原样复跑通过，完整根因未证明 | 后续维护 | 本轮不扩改后端 |
| **DEF-12** | React 单包体积告警，当前 gzip 446.93 kB；按部署体验评估拆包 | 后续性能评估 | 构建成功、当前不加依赖 |
| **DEF-13** | 真实排期/刷新回执与 publication 细分类型取证 | 后续受控运营验证 | 本轮真实外部动作 0，不把 stub 当真实发布成功 |

---

## 5. Stage A 的执行边界

### 5.1 技术栈（产品指定）

**安装**：React、TypeScript（strict）、React Router v7、TanStack Query、Ant Design 6、`@ant-design/icons`、Vite。`dayjs` 只在代码确实直接 import 它（或它的 locale）时作为显式 dependency。

**不安装**：`@playwright/test`、Tailwind、shadcn、Radix、Framer Motion、React Hook Form、Ant Design Pro Components、Sonner。

TypeScript 三个开关保持：`strict`、`noUncheckedIndexedAccess`、`exactOptionalPropertyTypes`。

### 5.2 Ant Design 三项核验原则

不为了用新 API 增加风险：

| 项 | 第一版做法 |
|---|---|
| `Table` | **不用 `virtual`**，只验证 `sticky`。当前数据量（队列 26、历史每页 50）不值得引虚拟滚动 → DEF-7 |
| 正文分栏 | **不用 `Splitter`**，用 CSS Grid 1:1。将来运营确需拖动再加 → DEF-6 |
| `theme.cssVar` | 实际安装 antd 6 后验证。**不符合预期时不许 hack**：改为由同一个 TypeScript token object 同时生成 `ConfigProvider` 的 theme 与 CSS 语义变量，仍然保持"所有颜色、字号、间距、圆角的字面值只有一个真相源" |

### 5.3 三个纯逻辑模块的移植纪律

`marks.js → marks.ts`、`format.js → format.ts`、`api.js 核心 → services/http.ts`。

**不是"重写成更漂亮的 TS"，是算法 1:1、只加类型。** 先写单测再继续。

必须守住的九条：

| 模块 | 不许变 |
|---|---|
| marks | Python 码点下标 → JS 码点的换算 |
| marks | 重叠优先级算法（error > risk > warn） |
| marks | 排序规则（按英文原文位置；只有德语侧位置的排最后） |
| format | 柏林排期时刻**继续手工解析字符串** |
| format | **禁止**改成浏览器本地 `Date` 渲染 |
| format | 上海 / 柏林两套语义继续分开 |
| http | task id **继续逐段 encode**（不整体 `encodeURIComponent`） |
| http | `error.status` / `error.payload` 保留 |
| http | export 的下载文件名解析与 `revokeObjectURL` 行为保留 |

唯一例外是 §3.1 的一处文案。

### 5.4 领域类型

按 `state/audit-probe/` 里已捕获的**真实 JSON** 手写。

禁止：发明后端不存在的字段、把详情字段假装成列表字段、为了 UI 方便悄悄扩大 response shape。

开发环境只做**浅层 shape assertion**，失败 `console.error`，**不让页面白屏**。生产环境暂不做 runtime schema validation。

### 5.5 路由骨架

Stage A 可以建 router 与 placeholder pages，**不开始正式 UI**。

```
/review
/review/:account/:postId
/history
/history/:account/:postId
/calendar
/settings
/runtime
```

旧 URL 重定向永久保留（D11）。详情路由的 search params 按 §2.2 保留来源列表上下文。

### 5.6 测试

继续用现有 **Python Playwright + `tests/browser_fixture.py`**，不创建第二套 Node Playwright。

⛔ 任何自动化测试都不得指向真实 8765 host，尤其禁止命中真实 `POST /api/tasks/{id}/approve` 与 `POST /api/calendar/refresh`。

Stage A 至少完成：marks 单测、重叠优先级单测、emoji/补充平面码点单测、柏林格式化单测、DST 边界格式单测、`idPath` 单测、`error.payload`/`status` 单测、已捕获真实 JSON 的类型/shape smoke test、`npm run build`。

### 5.7 唯一允许的后端改动

`web/api/app.py` 的 `DIST` 来源改成可配置，使 `web/ui/dist` 与 `web/ui-next/dist` 都能独立构建和挂载。

| 要求 | |
|---|---|
| 默认 | 仍指向旧 `web/ui/dist` |
| 无显式配置时 | 现有生产行为不变 |
| 不修改 | 任何 API endpoint、任何字段、任何业务状态、`archive/` 与 `state/` 数据 |

---

## 6. 受本文件影响、已同步修改的文件

| 文件 | 改了什么 |
|---|---|
| [OPEN_QUESTIONS.md](OPEN_QUESTIONS.md) | 13 问全部标记为已决，指向本文件；保留延期项索引 |
| [UI_ARCHITECTURE_PROPOSAL.md](UI_ARCHITECTURE_PROPOSAL.md) | 队列四页签与 `queue` 参数；详情 URL 上下文；删译文四态列；删首版批量；标签 chips；AntD 三项核验原则；历史分页 |
| [REACT_MIGRATION_PLAN.md](REACT_MIGRATION_PLAN.md) | 路由与 search params 契约；依赖清单加 Vitest；Stage A 完成判据 |
| [BASELINE_BEHAVIOR.md](BASELINE_BEHAVIOR.md) | 回归清单新增 B21–B24 |
| [DESIGN.md](DESIGN.md) | §15 去掉已决的两项；状态文案表更新 `not_ready` |
| [SCREEN_INVENTORY.md](SCREEN_INVENTORY.md) | 无需改（它只描述现状） |
