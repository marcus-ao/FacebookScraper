# 目标 UI 架构提案

**成文日期：2026-09-13。** 依据 [UI_AUDIT.md](UI_AUDIT.md) 的 8 条 P0 与 13 条 P1，以及 [BASELINE_BEHAVIOR.md](BASELINE_BEHAVIOR.md) 冻结的现有行为。

**落地状态：Stage A–J 已实施。** 下文保留目标架构，当前页面、请求缓存及实际验证见 [FINAL_IMPLEMENTATION_REPORT.md](FINAL_IMPLEMENTATION_REPORT.md)。

最终落地补充：第三方初翻授权在详情标题后、正文前显示，ContentJobs 通过 React portal 提供同一任务状态，轮询 hook 不重复挂载；正文与图片为这一业务区预留高度。标签 textarea 下实时解析为可删 chips。运行状态阅读区保持快照，后台更新只提示“有更新”。当前后端未提供未确认提交的 task_ids，保留维护说明。Modal/Drawer 保留 antd 的进入、Esc 与焦点恢复，额外补首尾 Tab 循环以覆盖 Chromium 移往浏览器工具栏的情形；详情快捷键只被可见浮层抑制。

## 关于 Ant Design 组件可用性的一句交代

本次会话**没有安装 Ant Design 的官方 Skill 或 MCP**（可用技能列表里只有面向 shadcn/Tailwind 的 `ui-ux-pro-max`，而 Tailwind 与 shadcn 在本轮是明确禁止引入的）。所以下面的组件映射是按 Ant Design 5/6 的既有 API 推导的。

**2026-09-13 决策收口后，三项存疑 API 的处置已经定下来**（[DECISION_LOG.md §5.2](DECISION_LOG.md)），不再作为"待核"悬着：

| 项 | 第一版做法 |
|---|---|
| `Table` 的 `virtual` | **不用**。只验证 `sticky`。队列 26 条、历史每页 50 条，不值得引虚拟滚动 → 延期项 DEF-7 |
| `Splitter` | **不用**。正文分栏用 CSS Grid 1:1。将来运营确需拖动比例再加 → 延期项 DEF-6 |
| `theme.cssVar` | Stage A 实际安装 antd 6 后核验。不符合预期时**不许 hack**：改为由同一个 TypeScript token object 同时生成 `ConfigProvider` theme 与 CSS 语义变量，仍然保持"字面值只有一个真相源" |

其余组件（`Layout`/`Menu`/`Tabs`/`Button`/`Dropdown`/`Tooltip`/`Popover`/`Drawer`/`Modal`/`Alert`/`Tag`/`Badge`/`List`/`Card`/`Collapse`/`Form`/`Select`/`Input`/`DatePicker`/`Segmented`/`Empty`/`Skeleton`/`Spin`/`Result`/`Descriptions`/`Timeline`）是长期稳定 API，按现有用法映射即可。

---

## 1. 这一天改成什么样

**现在**：08:30 打开，24 行一模一样的文字；点进一篇，首屏是一块黄色"尚未扫描"和一个红色"这篇不发"；滚两屏找到"通过并创建排期"；处理完返回，筛选清空，重筛一次，再点下一篇。

**目标**：08:30 打开，左侧导航四项，主区一张表，1366 下一屏 12 行。表上一眼能看出「3 篇要注意 · 9 篇正常 · 2 篇卡住」。点任意一行整行进详情；详情首屏就是英德并排的工作区，右上角一个蓝色"通过并创建排期"，左上角"第 3 / 12 篇"带上一篇/下一篇。改完按通过，自动进下一篇，筛选还在。

下面把这个形状拆开。

---

## 2. 全局外壳

### 2.1 现状与目标

现在是三条横带 115px（品牌 67 + 导航 47），每屏都在，不承载工作。目标是**顶栏 48px + 左侧导航 200px**，纵向只让出 48px。

```
┌─────────────────────────────────────────────────────────────────────────┐
│ DE 审校台            ⟨页面标题 / 面包屑⟩              ● 运行正常   ⚙   │ 48px
├──────────┬──────────────────────────────────────────────────────────────┤
│ 审校队列 │  ⟵ 工作区头（页面标题 + 筛选 + 计数），吸顶                  │
│ 历史归档 │ ─────────────────────────────────────────────────────────── │
│ 发布月历 │                                                              │
│ 运营设置 │  ⟵ 工作区体，内部滚动                                        │
│          │                                                              │
│          │                                                              │
│  ──────  │                                                              │
│  ● 运行  │                                                              │
└──────────┴──────────────────────────────────────────────────────────────┘
   200px
```

三条决定：

1. **品牌与副标题合成顶栏左端一行**。删掉 `US 站图文帖 → 德语正文与德语图 → 德国站定时发布` 与 `保存与排期均会留档；自动排期需完成本机核验`（[UI_AUDIT.md P1-8](UI_AUDIT.md)）。
2. **"运行状态"从主导航降级为顶栏右侧的状态指示器**。它是一天用不到一次的诊断页（P0-13）。指示器只显示一个圆点 + 一个词（正常 / 需要处理 / 未确认），点开进入诊断页。主导航只剩四项业务界面。
3. **左侧导航可折叠到 48px**（`Menu inlineCollapsed`）。1366 下折叠后主区宽度从 1,166px 回到 1,318px。默认展开；折叠状态存 `localStorage`（这是真正的本地 UI 偏好，见 [§13](#13-状态归属)）。

### 2.2 Ant Design 映射

| 元素 | 组件 | 说明 |
|---|---|---|
| 外壳 | `Layout` + `Layout.Sider` + `Layout.Header` + `Layout.Content` | `Sider` 用 `collapsible`，`trigger={null}` 自己放折叠按钮 |
| 导航 | `Menu mode="inline"` | 四项，`selectedKeys` 来自路由 |
| 队列计数 | `Menu` 项内嵌 `Badge count` | "审校队列"旁显示待处理数 |
| 运行指示器 | `Badge status` + `Button type="text"` | `status` ∈ `success`/`warning`/`error`/`default` |
| 顶栏页面标题 | 纯文本 + `Breadcrumb`（仅详情页） | 详情页显示「审校队列 / 这一篇」 |
| 全局错误 | `Alert type="error" banner closable` | 顶栏下方满宽，替换现在的 `.banner-error` |
| 索引过期提示 | `Alert type="warning" banner` | 统一一句文案（P3-1） |

---

## 3. 页面架构

四个业务界面 + 一个诊断界面 + 一个详情界面。

| 路由 | 界面 | 骨架 |
|---|---|---|
| `/review` | 审校队列 | 工作区头（筛选吸顶）+ 表 |
| `/review/:account/:postId` | 单篇审核 | 吸顶审校头 + 主工作区 + 右栏决策卡 |
| `/history` | 历史归档 | 工作区头（筛选吸顶）+ 表 + 吸底分页 |
| `/calendar` | 发布月历 | 工作区头 + 月格（内部滚动，表头吸顶） |
| `/settings` | 运营设置 | 两段：可编辑表单卡 + 折叠的只读配置 |
| `/runtime` | 运行状态 | 五个阶段卡，统一「状态 + 结论 + 动作 + 折叠技术细节」骨架 |

路由选型与 URL 兼容见 [REACT_MIGRATION_PLAN.md §4](REACT_MIGRATION_PLAN.md)——**旧的 `?task=` / `?view=` 必须继续可用**，因为飞书卡片里会带审校链接（`web/DESIGN.md §11`）。

---

## 4. 审校队列架构

### 4.1 布局

```
┌────────────────────────────────────────────────────────────────────────────┐
│ 审校队列                                            近 90 天 · 26 篇        │  ← 吸顶
│ ┌待我审 0┬未就绪 24┬已挂起 1┬已处理 1┐ 平台▾ 月份▾ 分类▾ [⚠ 只看 12 篇告警 ×]│    工作区头
├─┴────────┴─────────┴────────┴─────────┴───────────────────────────────────┤    ~96px
│ 图 │ 摘要                                │ 状态    │ 时刻        │ 平台 │⋯│
│────┼─────────────────────────────────────┼─────────┼─────────────┼──────┼─│
│ ▣ ⁴│ 🔴 Frische für Riko …               │ 待我审  │ 9/13 17:00 │  FB  │⋯│  48px
│ ▣  │ Zwei Napf-Modi im Test …            │ 已修改  │ 9/14 10:00 │  IG  │⋯│
│ ▣  │ Neue Farbe im Shop …                │ 未就绪  │ —          │  IG  │⋯│
└────────────────────────────────────────────────────────────────────────────┘
```

四个页签按 [DECISION_LOG.md D1](DECISION_LOG.md)：**待我审**（`pending_review` + `edited`）/ **未就绪**（`not_ready`）/ **已挂起**（`snoozed`）/ **已处理**（`approved` + `scheduled` + `skipped` + `handed_off`）。「已处理」只是 UI 分桶，行上仍然显示真实 status。

**没有选择列、没有批量条**——第一版不做批量（D3），也不为"预留"放没有功能的控件。

### 4.2 列定义

| 列 | 宽 | 内容 | 来源字段 |
|---|---|---|---|
| 缩略图 | 56px | 40×40 图 + 右下角张数角标 | `thumbnail_url`、`image_count` |
| 问题 | 32px | 一个图标：🔴 硬闸 / ⚠ 风险 / 👤 第三方 / 空 | `hard_alerts`、`risk_count`、`author_flag` |
| 摘要 | 自适应 | 90 字德语摘要，单行截断，`Tooltip` 给全文 | `text_de_excerpt` |
| 审校状态 | 88px | `Tag`（八态，`not_ready` 显示「未就绪」） | `status` + `STATUS_LABEL` |
| 时刻 | 140px | `建议 9/13 周日 17:00` / 已排期时加粗 / `—` | `schedule.at`、`status === 'scheduled'` |
| 平台 | 72px | 图标 + 文字 | `platform` |
| 分类 | 120px | 最多 2 个 `Tag`，多的 `+N` | `tags` |
| 动作 | 48px | `Dropdown` | — |

**没有「译文四态」列**（[DECISION_LOG.md §2.3](DECISION_LOG.md)）：列表载荷里没有区分"机器 / 人工 / 旧提示词"的依据——`text.de_human`、`text.machine_current`、`text.machine_prompt_version` **只在详情载荷里有**，列表项只有 `text_de_excerpt`。**禁止根据缺失字段猜测。** Stage C 第一版要么不显示，要么只显示 `text_de_excerpt` 能可靠证明的「未就绪 / 有译文」。真要四态就单独加后端字段 → 延期项 DEF-5。

「问题」列是 P0-2 的核心：**红色只出现在这一列**，定位固定，一眼扫一列就知道哪几行要注意。告警的具体文案进该图标的 `Tooltip`；风险条数同理。

### 4.3 行动作

整行可点进详情（P1-5）。行尾 `Dropdown`：

```
稍后再审
下载并由我处理
我已自行处理
──────────
这篇不发            ← danger
```

`这篇不发` 保留在一级菜单并标 `danger`（[UI_AUDIT.md §6](UI_AUDIT.md) 给了判断理由：需要可达，不需要抢眼）。

### 4.4 批量动作：本版不做

[DECISION_LOG.md D3](DECISION_LOG.md)：第一版不显示 checkbox 列、不启用 `rowSelection`、不显示批量操作条、不做批量 API。架构保持将来可加，但不为"预留"放没有功能的控件。

记录一条将来实现时的业务边界，免得以后重新讨论：**批量只能开放 `snoozed` 与 `skipped`**，都要一次模态确认与一份共用理由；**批量绝不开放通过/排期**——排期要逐篇核对内容指纹与 90 分钟同渠道冲突，批量在业务上不成立。→ 延期项 DEF-3。

### 4.5 Ant Design 映射

| 元素 | 组件 | 备注 |
|---|---|---|
| 表 | `Table` | `size="small"`、`sticky`（表头吸顶）、`onRow.onClick`；**不用 `rowSelection`**（D3）、**不用 `virtual`**（DEF-7） |
| 状态页签 | `Tabs` + 每项 `Badge count` | 四个页签（D1）；计数改用服务端 `summary.by_status` 按分桶相加（现在是客户端从全量数组算的，见 P0-3 方向） |
| 平台/月份/分类 | `Select showSearch allowClear` | 月份按年 `Select.OptGroup` 分组 |
| 只看告警 | 可关闭的 `Tag`（筛选 chip） | 数量为 0 时不渲染（P2-1） |
| 行状态 | `Tag` | 七态 + `not_ready` 共八种配色，见 [DESIGN.md §7](DESIGN.md) |
| 问题图标 | `Tooltip` 包 `@ant-design/icons` | `ExclamationCircleFilled` / `WarningFilled` / `UserOutlined`；第三方作者留在「未就绪」里用它明示（D1 第 4 条） |
| 行动作 | `Dropdown menu` + `MoreOutlined` | `danger: true` 用于"这篇不发" |
| 加载 | `Table loading` + `Skeleton` 行 | 固定行高避免跳变（P2-9） |
| 空 | `Table locale.emptyText` → `Empty` | 区分"筛选无结果"（给清除筛选按钮）与"真的没有" |

### 4.6 密度目标

| 分辨率 | 外壳 | 工作区头 | 行高 | 首屏行数 |
|---|---|---|---|---|
| 1920×1080 | 48 | 96 | 48 | **19** |
| 1366×768 | 48 | 96 | 48 | **12** |

对比现状 7 / 4 条。

---

## 5. 单篇审核详情架构

### 5.1 布局

```
┌───────────────────────────────────────────────────────────────────────────────┐
│ ⟨ 返回队列   第 3 / 12 篇  ‹ ›  │ FB · 待我审 │ 🔴2 ⚠3 ✔看过 │ 编辑德语 [通过并创建排期] ⋯│ 56px 吸顶
├──────────────────────────────────────────────────────────────┬────────────────┤
│ [ 正文对照 ] [ 图片 6 ] [ 标签与链接 ⚠ ]                      │ 决策            │
│ ┌──────────────────────────┬─────────────────────────────┐   │ ──────────      │
│ │ 英文原文                  │ 德语译文 · 人工版            │   │ 状态 待我审      │
│ │                          │                             │   │ 时刻 9/13 17:00 │
│ │  … Labor Day Blowout …   │  … Labor Day Blowout …      │   │ 渠道 Facebook   │
│ │                          │                             │   │ 冲突 无          │
│ │  ‹ 上一处 3/5 下一处 ›    │                             │   │                 │
│ └──────────────────────────┴─────────────────────────────┘   │ 来源 ──────      │
│ ⚠ 美国限定 "Labor Day" · 德国站没有这个节日，需要换成当地促销由头│ 原创 @neakasa… │
│                                                              │ 2026-07-01     │
│                                                              │ 查看原帖 ↗      │
│                                                              │                 │
│                                                              │ 处理记录 ›       │
│                                                              │ 诊断信息 ›       │
├──────────────────────────────────────────────────────────────┴────────────────┤
│ ▸ 单篇优化（付费）                                                             │
└───────────────────────────────────────────────────────────────────────────────┘
```

### 5.2 吸顶审校头（三段）

| 段 | 内容 | 解决 |
|---|---|---|
| 左 | `返回队列` · `第 n / N 篇` · `‹ ›` | P0-9（上一篇/下一篇）；队列上下文由 URL 携带，见下 |
| 中 | 平台 + 状态 `Tag` + 源文变更 `Tag` + 三档问题计数（可点，跳第一处） + 图片核对进度 | P0-7（三档同色同级） |
| 右 | `编辑德语`（default） · `通过并创建排期`（**primary，唯一**） · `更多` `Dropdown` | P0-4、P0-6 |

`更多` 菜单：稍后再审 / 下载并由我处理 / 我已自行处理 / ── / 这篇不发（danger）。

编辑态：右段换成 `放弃修改`（default） + `保存`（primary）。中段的计数改为实时校验结果。左段的上一篇/下一篇 disabled 并给 `Tooltip`"先保存或放弃当前编辑"——比现在把这句话放在两屏以下的排期区里有用（P1-8）。

高度固定 **56px，不允许折行**（P2-2）。`scroll-margin-block` 由 CSS 变量 `--review-header-h` 驱动，不再写死 96px。

**「第 n / N 篇」与上一篇/下一篇的数据来源**（[DECISION_LOG.md §2.2](DECISION_LOG.md)）：详情 URL 携带来源列表的筛选参数，详情页用这组参数重新发起**同一个列表查询**（TanStack Query 命中列表页已有缓存，不产生额外请求），从结果里算出当前 index 与相邻项。四条硬要求：刷新详情页后仍知道 n / N；上一篇/下一篇按进入时的筛选结果走；返回列表恢复原筛选与页码；**不把 `location.state` 当唯一真相源**（它刷新后为 `null`，也不能分享）。

### 5.3 主工作区：三个标签页

主工作区占「吸顶以下到视口底」的全部高度（P1-10），内部按标签页切换：

| 标签 | 内容 | 何时带标记 |
|---|---|---|
| **正文对照** | 英德并排 + 标记 + 当前那处的说明条 | 有 error/warn 时红点 |
| **图片 N** | 缩略图接触表（全部 N 张一次看全）+ 点开单张左右对照 | 有未看图片或缺德语图时标记 |
| **标签与链接** | 话题标签区 + 链接区 | `localization_validation.issues` 非空时 ⚠ |

三条理由：

1. 三者是**并列的核对维度**，不是上下顺序。现在把它们纵向排成 2.6 屏，导致每个维度都只看得到一部分。
2. 标签页让"我核到哪了"变成可见状态（带标记的标签页 = 还有问题）。
3. `图片` 标签页把 6 张图的接触表提到主位，解决"逐张点才知道有没有漏"（P1-12）。现有的 `seen` 机制与"还有 N 张没看"提示照旧保留并上移到吸顶头。

**正文对照的实现必须 1:1 移植三条既有细节**（[BASELINE_BEHAVIOR.md §5.3–5.4](BASELINE_BEHAVIOR.md)）：码点下标换算、重叠时 error>risk>warn、**不用 `behavior:'smooth'`**。

编辑态：德语栏必须有明确的编辑态形态（P0-4 之外的 UI_AUDIT 第 10 条）——栏头文字改「德语译文 · 正在编辑」、栏体加输入态边框与浅底、栏头右侧显示校验中/已校验。

### 5.4 右栏：决策卡 + 压缩来源

现在右栏是 393px 的 `元信息`（含 393px 里的操作记录）。改成：

| 区 | 内容 | 现在在哪 |
|---|---|---|
| **决策** | 状态 / 时刻（可编辑，带常用时间与可选范围）/ 渠道 / 同渠道冲突 / 阻塞原因 | 散在 y=1524 的排期区 |
| **来源** | 作者 + 合作方 + 原帖时间 + 原帖链接（4 行紧凑） | 元信息的 `<dl>` |
| **分类** | `Tag` + 编辑 | `TagEditor` |
| 入口 | `处理记录 ›`、`诊断信息 ›` 两个 `Drawer` | 常驻的操作记录、prompt/provider/attempt_id |

排期时刻放右栏的理由：它是**决定**，不是内容；放右栏后吸顶条的 primary 点下去可以直接读右栏的当前选择，不需要滚动。`通过并创建排期` 的 disabled 原因挂在按钮 `Tooltip`（P2-10），六个条件按优先级给一句话。

### 5.5 页面顶部的条件 `Alert`

只有三类信息配得上满宽 `Alert`（其余全部降级，见 [§8](#8-渐进披露策略)）：

| 条件 | 类型 | 文案方向 |
|---|---|---|
| `capabilities.third_party` | `warning` + 动作 | "这篇来自第三方作者，处理前请确认可用于德国站" + consent 复选 + 按钮 |
| `meta.compose_warnings` 非空 | `warning` 可折叠 | "缺德语图，已回退原图"等（内容问题，必须看见） |
| `risk_scan.status ∈ {failed, stale}` | `warning` | "风险预扫未完成 / 已失效，请人工复核双关、歧义与美国限定表达" |
| `read_only` | `info` | "这是冻结账号的历史归档，只能查阅" |

`risk_scan.status === 'not_scanned'` **不出 Alert**（P0-5）——它进中段的状态区，显示为一个中性标记。`completed` 且零风险也不出 Alert。

### 5.6 Ant Design 映射

| 元素 | 组件 | 备注 |
|---|---|---|
| 吸顶头 | 自定义 `div`（`position: sticky`）+ `Space` + `Button` | 不用 `PageHeader`（已从 antd 移除，且它的布局不是我们要的三段） |
| 上一篇/下一篇 | `Button.Group` + `LeftOutlined`/`RightOutlined` | — |
| 状态与计数 | `Tag` / `Badge` | 三档计数可点 |
| 主工作区标签 | `Tabs` + `Tabs.TabPane` 的 `Badge dot` | — |
| 英德并排 | `Splitter`（**待核** antd 6 API）或 CSS Grid | 用户可拖拽分栏比例；拿不到 `Splitter` 就固定 1:1 |
| 正文标记 | 原生 `<mark>` + CSS Modules | 保留现有 `mark.mk-*` 三档，不用组件 |
| 编辑器 | 原生 `<textarea>` + 镜像层 | **不用 `Input.TextArea`**：镜像层需要逐像素同排版，组件包装会带来不可控的内边距 |
| 当前那处说明 | `Alert type="warning"/"error" showIcon` 的紧凑变体 | 替换现有 `.explain` |
| 图片接触表 | `Image.PreviewGroup` + 自定义网格 | 保留 `seen` 机制与红条"缺德语图" |
| 图片指标 | `Popover` + `Descriptions size="small"` | 无记录时不渲染入口（P1-8） |
| 标签编辑 | **保留 `textarea`** + 下方实时解析出的可删 chips | [DECISION_LOG.md D6](DECISION_LOG.md)：支持整段粘贴，第一版不强制切 `Select mode="tags"` |
| 链接落地页 | `Form.Item` + `Input` + `Checkbox` | `{{linkN}}` 的显示层改造见 P2-11 |
| 决策卡 | `Card size="small"` + `Descriptions` + `DatePicker showTime` | `DatePicker` 的 `disabledDate`/`disabledTime` 用 `approval-options` 的 `earliest`/`latest` |
| 常用时间 | `Segmented` 或一排 `Button size="small"` | 来自 `options.default_times` |
| 冲突建议时刻 | `Alert` + 一排可点 `Tag` | 来自 409 的 `payload.suggestions` |
| 处理记录 | `Drawer` + `Timeline` | `trail[]`；`ACTION_LABEL` 表照搬 |
| 诊断信息 | `Drawer` + `Descriptions` | prompt/provider/model/attempt_id/snapshot_id/fingerprint |
| 优化区 | `Collapse`（默认收起）+ `Form` | 提交按钮改 `default`（P1-9） |
| 模板只读 | `Drawer` + `<pre>` | — |
| 决定对话框 | `Modal`（拆三种意图，见 P2-8） | 自带焦点管理与 Esc（修 P2-7） |

---

## 6. 历史归档架构

同 §4 的表框架，差异：

| 项 | 做法 |
|---|---|
| 分页 | 服务端分页（已有），`Table pagination` 吸在表下沿（修 P0-10） |
| 页大小 | **默认 50，可选 20 / 50 / 100，`limit` 必须进 URL**（[DECISION_LOG.md D2](DECISION_LOG.md)） |
| 行高 | 48px（现 102px） |
| 列 | 日期 · 缩略图 · 摘要 · 平台 · 账号 · 状态 · 分类 · 动作 |
| 账号与冻结 | 账号一列；冻结用账号名旁的 `LockOutlined` + `Tooltip`，不再每行写"冻结归档"（修 P1-13） |
| 月份筛选 | `Select` 按年 `OptGroup` 分组（71 项） |
| 搜索 | **本版不做**（[DECISION_LOG.md D4](DECISION_LOG.md)）。后端没有搜索契约之前不显示搜索框 → 延期项 DEF-2 |
| 批量 | **不做**（D3）：无 checkbox 列、无 `rowSelection` |
| 行组件 | 与审校队列共用 `PostRow` 列定义（修 P2-3） |

首屏目标：1366 下 12 行、1920 下 19 行。

---

## 7. 发布月历与设置、运行状态

### 7.1 发布月历

| 项 | 做法 |
|---|---|
| 日格内容 | 每条一行：`时刻` + 渠道 `Tag` + 投递状态 `Badge`。正文摘要进 `Popover`（修 P0-11） |
| 投递三态 | `published` → `Badge status="success"`；`scheduled` → `processing`；其它 → `default` + "待核验" |
| 日格高度 | 由最忙的一天决定，空格压到 64px |
| 表头 | 星期表头吸顶（面板内部滚动） |
| 刷新 | 按钮 + `Tooltip`"会打开发布浏览器读取后台，约需数十秒"；执行期 `Spin` 覆盖面板（修 P1-14） |
| 不可刷新原因 | 从 `<details>` 改为按钮的 `Tooltip` |
| 缓存元信息 | 一行：`数据截至 …` + `stale` 时一个 `Tag`；`月份换算` 说明进标题旁 `Tooltip` |
| 组件 | 自定义 CSS Grid（`Calendar` 的 `dateCellRender` 不适合这种多条目 + 跨月标记的形态）+ `Popover` / `Tag` / `Badge` / `Spin` |

### 7.2 运营设置

| 项 | 做法 |
|---|---|
| 结构 | 上：`Card` 里的 `Form`（两个字段）；下：`Collapse` 的七个只读配置（修 P2-4） |
| `editable_help` | 从常驻 `<small>` 改为 `Form.Item` 的 `tooltip`（修 P0-12）。**数据照旧请求与保留**，只改呈现位置 |
| 默认排期时刻 | `Select mode="tags"` 限 `HH:MM` 格式，或多个 `TimePicker`（比自由文本安全）。保存时仍发 `default_times: string[]` |
| 挂起天数 | `InputNumber min={1} max={30}` |
| 版心 | 两列（表单 + 说明），1366 下一屏放完 |
| 组件 | `Card` / `Form` / `Select` / `InputNumber` / `Collapse` / `Descriptions` |

### 7.3 运行状态

| 项 | 做法 |
|---|---|
| 入口 | 顶栏状态指示器（不在主导航，修 P0-13） |
| 阶段骨架 | 统一：`Badge status` + 阶段名 + **一句业务结论** + （有需要时）动作 + `Collapse` 的"技术细节" |
| 技术原文 | HTTP 码、Chrome 端口、检查名、`paid_request_ids` 全部进折叠层 |
| 需要处理的事 | 提到页面顶部一块"需要你处理"区，每条带动作按钮（修 P0-14） |
| 未确认提交 | 目标是带帖子链接。当前 snapshot 没有这个字段，先做成带动作的 `Alert`，字段问题记在 [OPEN_QUESTIONS.md](OPEN_QUESTIONS.md)（P0-15） |
| 重发飞书 | `Modal.confirm` 标题写清"将重新发送这张卡片"，确认框里摆接收组与内容摘要 |
| 自动刷新 | 保留 30 秒，但改成"有变化时提示刷新"而不是静默替换正在读的内容 |
| 组件 | `Card` / `Badge` / `Steps`（五阶段进度概览）/ `Collapse` / `Descriptions` / `Timeline`（投递历史）/ `Modal.confirm` / `Result`（整体不可用时） |

---

## 8. 渐进披露策略

三档的判据与承载方式。**L1 常驻、L2 一次交互可得、L3 二次交互可得。**

### 8.1 审校队列

| 档 | 内容 | 承载 |
|---|---|---|
| L1 | 缩略图、摘要、问题图标、译文状态、审校状态、时刻、平台 | 表格列 |
| L2 | 告警具体文案、风险条数、图片张数、摘要全文、分类（>2 个） | `Tooltip` / 角标 / `+N` |
| L3 | `source_text_sha256`、`review.revision`、`risk_scan_status`、`author_flag` 细节 | 不在列表出现（详情的诊断抽屉） |

### 8.2 单篇审核

| 档 | 内容 | 承载 |
|---|---|---|
| L1 | 英德正文 + 标记、当前那处说明、三档计数、状态、源文变更、主动作、未看图片数 | 吸顶头 + 主工作区 |
| L2 | 标签与链接（标签页）、图片逐张对照（标签页）、时刻与范围、冲突、分类、来源四行 | `Tabs` / 右栏决策卡 |
| L3 | 操作记录、prompt/provider/model、`attempt_id`、`snapshot_id`、`source_fingerprint`、图片指标、模板内容、任务 `paid_request_ids` | `Drawer` ×2 / `Popover` |

### 8.3 判据

一条信息进 L1 的条件是：**不看它就可能做出错误的业务决定。** 按这个标准逐条过：

| 信息 | 不看它会怎样 | 结论 |
|---|---|---|
| 缺德语图（`de_present: false`） | 可能发出英文图 | **L1**（图片标签页的标记 + 接触表的红条） |
| 源文已变更（`text.stale`） | 基于旧原文的译文被通过 | **L1**（吸顶头红 `Tag`） |
| 未看图片数 | 漏看一张德语图 | **L1**（吸顶头） |
| `localization_validation.issues` | 落地页未确认就通过 | **L1**（标签页 ⚠ + 通过按钮的阻塞原因） |
| 90 分钟同渠道冲突 | 排期被拒或撞车 | **L1**（右栏决策卡） |
| 风险扫描失败/过期 | 以为扫过了，实际没扫 | **L1**（满宽 `Alert`） |
| 风险扫描"尚未扫描" | 需要人工检查双关——但**每一篇都需要**，所以它不是这一篇的特征 | **L2**（状态区一个中性标记） |
| 图片 dHash / 形变 / 缩放 / 耗时 | 不会——这是程序自比指标，不能替代人看图 | **L3** |
| 操作记录 | 不会影响当前决定 | **L3** |
| prompt 版本 / provider / model | 不会 | **L3** |
| `attempt_id` | 不会（除非在排查回执） | **L3** |

**不能藏的四条**：缺德语图、源文变更、落地页未确认、排期冲突。它们是 `web/DESIGN.md §6/§9/§10` 明确要求可核对的业务事实。

---

## 9. 信息层级手段

优先级从高到低，禁用清单在后。

| 手段 | 用法 |
|---|---|
| **内容优先级** | 一屏之内先决定哪三件事最重要，其余下沉。这是最有效的一条 |
| **排版** | 三级标题 + 三级正文（见 [DESIGN.md §3](DESIGN.md)）。同级必须同字号 |
| **间距** | 8px 基准。区段之间 24px，区段内 12–16px，行内 8px |
| **分组** | 相关的东西共享一个容器与一个标题；不相关的东西之间用间距而不是边框 |
| **表面对比** | 页面底 `#f5f6f7`、卡片白、次级区域 `#fafafa`。三层足够 |
| 状态色 | 只用在状态标记与真正的告警上，不用来分区 |

不许用：给每样东西加边框、大面积色块分区、一屏超过 8 个 `Tag`、同级多种字号、常驻的解释文字、只靠颜色传达状态（必须同时有图标或文字）。

告警强度与后果对齐：

| 后果 | 形态 |
|---|---|
| 可能发错内容 / 撞车 / 花错钱 | 满宽 `Alert type="error"` 或 `warning` + 动作 |
| 这一处要人确认 | 正文内标记 + 说明条 |
| 某项还没做 | 状态标记（中性色） |
| 系统行为说明 | `Tooltip` 或不显示 |

---

## 10. 动作层级

### 10.1 三类分离

| 类 | 例子 | 位置 | 形态 |
|---|---|---|---|
| **导航** | 返回队列、上一篇/下一篇、标记游走、图片游走 | 吸顶头**左段**（帖子级）/ 内容区栏头（内容级） | `Button type="text"` 或 `Button.Group` |
| **业务** | 编辑德语、保存、通过并创建排期 | 吸顶头**右段** | 主动作 `primary`，**一个**；其余 `default` |
| **例外 / 破坏** | 稍后再审、这篇不发、下载并由我处理、我已自行处理 | 吸顶头右段的 `更多` `Dropdown` | 菜单项；`这篇不发` 标 `danger` |

关键：**把标记游走（上一处/下一处）从帖子级位置移到内容级位置**。它现在挨着"返回列表"，读起来像换篇（P0-6/P0-9）。

### 10.2 每个界面一个主动作

| 界面 | 主动作 | 其余 |
|---|---|---|
| 审校队列 | 无（列表是选择界面，整行可点就是主动作） | 批量条在有选中时出现 |
| 单篇审核（只读） | `通过并创建排期` | `编辑德语` default；例外动作进菜单 |
| 单篇审核（编辑） | `保存` | `放弃修改` default |
| 历史归档 | 无 | — |
| 发布月历 | 无（`刷新月历` 是 default + 明确提示） | — |
| 运营设置 | `保存设置` | `重新读取` `Button type="text"` |
| 运行状态 | 无（有待办时，待办卡里各自一个动作） | — |

**付费动作不是主动作**（P1-9）：`生成文案候选` / `生成图片` 改 `default`，金额继续写在按钮文案里，整区折叠。

### 10.3 破坏性动作的门

不放宽现有任何一道：

| 动作 | 门 |
|---|---|
| 这篇不发 | 菜单项（danger）→ `Modal` → **理由必填** → 确认按钮 `danger` |
| 我已自行处理 / 下载并由我处理 | 菜单项 → `Modal` → 说明后果 → 确认 |
| 通过并创建排期 | 内容指纹 + 三个 revision + 90 分钟冲突检查 + `result.ok && status==='scheduled'` 才算成功 |
| 重发飞书 | `Modal.confirm` + 展示接收组与内容摘要 |
| 关闭中断批次 | `Modal.confirm` + 有付费请求时的核账提示 |

⛔ `通过并创建排期` 与 `刷新月历` 是唯一两个动到平台侧的入口。实施期间的自动化测试一律打夹具，沿用 `tests/browser_fixture.py` 的 ASGI 白名单（只放行 `PUT /api/settings`、`PUT …/localization`、`POST …/check`）。

---

## 11. 通用 UI 模式

一套跨界面复用的组件约定，避免"同一种东西两套做法"（P2-3）。

| 模式 | 组件 | 用在哪 |
|---|---|---|
| `PostRow` 列定义 | 共享的 `ColumnsType<PostListItem>` 工厂 | 审校队列、历史归档 |
| `StatusTag` | 八态 → `Tag` 配色 + 文案 | 队列、历史、详情、月历 |
| `PlatformLabel` | 平台图标 + 文字 | 四处 |
| `BerlinTime` / `ShanghaiTime` | 两个纯展示组件，封装 [BASELINE_BEHAVIOR.md §7](BASELINE_BEHAVIOR.md) 的两套算法 | 全站所有时刻 |
| `ProblemIndicator` | 硬闸/风险/第三方 → 图标 + `Tooltip` | 队列、详情 |
| `IssueList` | `localization_validation.issues` → 紧凑列表 | 详情、通过按钮的 `Tooltip` |
| `ConflictRecovery` | 409 → 一句话 + 一个恢复按钮 | 五处（localization / review / tags / 初翻 / 优化） |
| `PaidActionButton` | 金额 + 剩余次数 + disabled 原因 | 初翻、优化 |
| `DiagnosticsDrawer` | 统一的技术细节抽屉 | 详情、运行状态 |
| `SectionCard` | 区段标题 + 可选折叠 + 内容 | 设置、运行状态、详情下半 |

**时刻组件是硬要求**：现在时区逻辑散在 `format.js`、`ApprovalPanel`、`ReviewActions`、`CalendarPanel`、`RuntimePanel` 五处。柏林时刻绝不能经浏览器本地时区渲染，这条必须由一个组件保证，不能靠每处调用方记得。

---

## 12. 桌面密度与窄屏策略

### 12.1 密度

一套密度，不做"紧凑/宽松"开关（内网工具，用户是同一批人）。

| 项 | 值 |
|---|---|
| 基础字号 | 14px（沿用现状） |
| 行高 | 1.5（正文）/ 1.4（表格） |
| 表格行 | 48px（`Table size="small"` 基础上调） |
| 按钮 | 32px（`size="middle"`）；表格内与工具条用 28px（`size="small"`） |
| 输入 | 32px |
| 区段间距 | 24px |
| 卡内边距 | 16px |
| 顶栏 | 48px |
| 吸顶工作区头 | 96px（队列/历史）、56px（详情） |

`ConfigProvider` 的 `theme.token` 里定：`controlHeight: 32`、`borderRadius: 6`（沿用现状）、`fontSize: 14`、`sizeUnit: 4`、`sizeStep: 4`。

### 12.2 窄屏（不做移动端）

99% Windows 桌面，**不为手机让路**。三个断点：

| 宽度 | 做法 |
|---|---|
| ≥1600px | 详情右栏 360px；队列显示全部列 |
| 1280–1599px | 详情右栏 320px；队列隐藏「分类」列（进 `Tooltip`）；侧边导航建议折叠 |
| 1024–1279px | 详情右栏折叠为一个抽屉入口；队列隐藏「平台」文字只留图标；月历横向滚动 |
| <1024px | 只保证不崩：单列堆叠，允许横向滚动。不优化 |

1366×768 落在第二档。它的具体保障：吸顶头不折行、队列首屏 ≥12 行、详情正文对照占满剩余高度。

**验收**：每个界面在 1920×1080 与 1366×768 各截一次，量 `rowsAboveFold`、`screensOfScroll`、吸顶高度是否折行。脚本已有（`tools/capture_baseline.py`），迁移后指向新应用即可对比。

---

## 13. 状态归属

三类状态，三种归宿。**不把所有东西塞进 TanStack Query。**

| 类 | 例子 | 归宿 |
|---|---|---|
| **服务端状态** | 任务列表、详情、月历、设置、运行状态、任务轮询 | TanStack Query（详见 [REACT_MIGRATION_PLAN.md §6](REACT_MIGRATION_PLAN.md)） |
| **URL 状态** | 当前界面、当前帖子、队列页签（`queue`）、平台/月份/分类/只看告警、历史的 `page`/`limit` 与筛选、详情当前标签页（`tab`）**以及详情从哪个列表进来的全套上下文** | React Router 的 `useSearchParams`（解决 P0-3）。参数名与语义见 [DECISION_LOG.md §2.1–2.2](DECISION_LOG.md)：页签用 `queue`（`review`/`not_ready`/`snoozed`/`processed`），**不复用 `status`**——一个页签含多个真实 status |
| **局部 UI 状态** | 编辑草稿、当前标记游标、图片 `seen` 集合、当前图片索引、模态框开关、抽屉开关 | `useState` / `useReducer`，组件内 |
| **本地偏好** | 侧边导航折叠、详情分栏比例 | `localStorage`（现在完全没有浏览器持久化，这是唯一新增的两项） |

**派生状态不存**：三档标记计数、字符数、页签计数、可编辑判据、disabled 判据全部由上面三类算出来，不另设 state。现在 `TaskList` 的页签计数是客户端从全量数组算的，改服务端分页后要换成 `summary.by_status`（[UI_AUDIT.md P0-3](UI_AUDIT.md)）。

---

## 14. 状态与反馈模式

| 情形 | 现状 | 目标 |
|---|---|---|
| 列表加载 | `正在读归档…` 纯文字 | `Skeleton` 行（固定行高，不跳变） |
| 详情加载 | `正在读这一篇…` | 分区 `Skeleton`（吸顶头 + 双栏 + 右栏） |
| 提交中 | 按钮文案变"保存中…" | `Button loading`，文案不变 |
| 轮询中 | 无可见状态 | 任务卡上 `Spin size="small"` + 已耗时 |
| 保存成功 | 页面内绿字，需要滚到才看见 | `message.success`（短），且吸顶头状态 `Tag` 同步更新 |
| 校验中 | 栏头"校验中…" | 保留（这是有用的即时反馈），改成栏头右侧一个小 `Spin` |
| 校验有问题 | 页面中段一行黄底文字 | 标签页 ⚠ + 通过按钮的 `Tooltip` + 就地 `Form.Item` 的 `help` |
| 409 冲突 | 红条 + 一个恢复按钮（五处各写一遍） | 统一 `ConflictRecovery`：一句话 + "载入最新内容并保留我的修改" |
| 请求失败 | 红字 + "重试" | `Alert type="error"` + 重试按钮；全局失败用顶栏 banner |
| 空（筛选无结果） | `当前筛选下没有帖子。` | `Empty` + "清除筛选"按钮 |
| 空（真的没有） | 同上 | `Empty` + 一句说明 |
| 整界面不可用 | 无 | `Result status="warning"` + 可操作的下一步 |
| 只读（冻结账号） | 一行文字 | `Alert type="info"` + 所有写入控件不渲染（不是 disabled） |

两条纪律：

1. **提示不拦人工**。`web/DESIGN.md` 与 `TaskDetail.vue:326-329` 都写了这条——"还有 N 张图没查看"、`/check` 的红色标记、`localization_validation.warnings` 全部只提示，**永不禁用"通过"**。迁移后必须有测试守住（[BASELINE_BEHAVIOR.md §12](BASELINE_BEHAVIOR.md) B8/B15）。
2. **禁用必须给原因**。任何 disabled 的按钮都要有 `Tooltip` 说明为什么（P2-10）。

---

## 15. 对实施顺序的评估

提纲给的顺序（A 基础 → B 外壳与 token → C 列表 → D 详情 → E 历史 → F 月历 → G 设置 → H 运行 → I 回归 → J 视觉 QA）**基本正确**，我只改三处：

| 改动 | 理由 |
|---|---|
| **Stage B 拆成 B1 外壳/token 与 B2 共享基元**（`StatusTag`、`PlatformLabel`、`BerlinTime`/`ShanghaiTime`、`ConflictRecovery`、`PostRow` 列工厂） | C 与 D 都要用这些。不先做，列表和详情会各写一套，回到 P2-3 的老路。时刻组件尤其不能等——它是业务正确性组件，不是样式组件 |
| **Stage C 与 E 合并（或紧邻）** | 队列与历史共用 `PostRow`。隔着详情做完再回来做历史，共享层会被详情的需求带偏 |
| **Stage I 的回归不放在最后，每个 Stage 结束都跑一次** | [BASELINE_BEHAVIOR.md §12](BASELINE_BEHAVIOR.md) 的 20 条里，B1–B4（路由与离开守卫）在 Stage B 就该绿；B5–B11 在 Stage D 结束时该绿。攒到最后一起查，定位成本会翻几倍 |

另外 Stage J 的视觉 QA 用现成脚本：`tools/capture_baseline.py` 已经能在两个分辨率上截 9 屏并量 `rowsAboveFold`/`screensOfScroll`。把它的 `base_url` 指向新应用，就有了新旧对照的量值，不需要靠肉眼比"是不是更干净了"。

**本阶段不实施任何一个 Stage。**

---

下一步看 [REACT_MIGRATION_PLAN.md](REACT_MIGRATION_PLAN.md)（技术路径）与 [DESIGN.md](DESIGN.md)（设计系统草案）。
