# Stage B 执行报告（B1 + B2）

**执行日期：2026-09-13。代码基线 `3536e29`。前置：[STAGE_A_REPORT.md](STAGE_A_REPORT.md)。**

范围是应用外壳、设计系统定值、共享基元三件事。**Stage C 没有开始**——没有真实筛选条、没有 `GET /api/tasks` 查询、没有队列表格、没有行点击工作流、没有行内业务动作。

---

## 0. 开工前的 Stage A 复核

15 分钟级别，只确认地基还在，没有重新审计：

| 项 | 结果 |
|---|---|
| `npm ci` | 118 个包，`0 vulnerabilities`；`package.json` / `package-lock.json` 的 SHA256 前后一致 |
| `npm test` | **148 passed**，与 Stage A 一字不差 |
| `npm run typecheck` | 0 错误 |
| `npm run build` | 通过，产物哈希与 Stage A 报告里的 `index-PcJekxGL.js` 一致 |
| 依赖版本 | React 19.3.0 / react-router 7.18.3 / antd 6.6.3 / TanStack Query 5.102.8 / TS 7.0.2 / Vite 8.3.0 / Vitest 5.0.0——一个都没动 |
| 工作区 | `git status --short` 与会话开始时逐行相同 |

没有阻塞问题，直接进 B1。

⚠️ 一处与 Stage A 报告的数字出入：那份写"共 162 个包"，`npm ci` 实际报 118。两者统计口径不同（`npm ls` 的依赖树节点 vs `npm audit` 的已安装包数），**版本与 lock 文件都没变**，不是依赖漂移。

---

## 1. Stage B 实际完成范围

### B1 — 应用外壳 + 设计系统

- 48px 顶栏（`DE` 图形标识 + 「审校台」+ 折叠按钮 + 页面标题/面包屑 + 运行状态入口）；
- 200 / 48px 可折叠左侧导航，四项业务界面，`selectedKeys` 全部由路由算出；
- 「运行状态」只在顶栏，**中性徽标**，不编造"运行正常"；
- 最终主色定值 `#155EEF`，含可复算的对比度判据与单测；
- token 单一来源扩展到 antd 的 `theme.components` 与 `--rc-*` 变量三路同源；
- 404 有一句业务语言 + 一个「返回审校队列」；
- `useUnsavedChangesGuard` 抽象建立（三层守卫），**没有**往占位界面塞假的 dirty 开关；
- 全局排版层级、等宽数位、焦点环、`prefers-reduced-motion`、禁用平滑滚动。

### B2 — 共享基元

`StatusTag`、`PlatformLabel`、`BerlinTime`/`ShanghaiTime`、`ProblemIndicator`、`ConflictRecovery`、`PaidActionButton`、`PostRow` 列工厂，外加一个**内部**核验页 `/_internal/design-check`。

### 明确没做

队列/历史/详情/月历/设置/运行状态的任何正式界面；任何列表查询；任何批量能力；任何后端改动。

---

## 2. 新增 / 修改文件

### 修改（已跟踪文件）：**0 个**

这一轮**没有改任何一个已在 git 里的文件**。会话开始时的七个修改状态文件，`git diff --stat` 输出逐行相同（见 §23）。

### 修改（Stage A 建立、尚未入库的文件）

| 文件 | 改了什么 |
|---|---|
| `web/ui-next/src/app/theme.ts` | 主色定值；新增 `strong`/`deep`/`onSoft`/`scrim`/`focusRing*`/`navItemHeight`/`thumbnailSize`/`iconSize`/`lineHeight` 等 token；新增 `antdComponents`；antd heading 字号拉回七级刻度 |
| `web/ui-next/src/app/router.tsx` | 挂 `AppShell`；每条路由加 `handle` 页面标识；404 换成 `NotFound`；`_stage-a-check` → `_internal/design-check`；导出 `routes` 供测试用 |
| `web/ui-next/src/main.tsx` | 挂 `antdComponents`；关掉 antd 的「通 过」自动插空格 |
| `web/ui-next/src/pages/Placeholder.tsx` | 页面标题改由 `<PageTitle />` 统一出；样式改走语义变量 |
| `web/ui-next/src/styles/global.css` | 排版层级、等宽数位、焦点环（见 §6）、禁用平滑滚动、刻度化滚动条 |
| `web/ui-next/vitest.config.ts` | `include` 加 `*.test.tsx`（组件测试） |

### 删除

| 文件 | 原因 |
|---|---|
| `web/ui-next/src/app/RootLayout.tsx` | Stage A 的脚手架外壳，被 `AppShell` 取代 |
| `web/ui-next/src/pages/StageACheck.tsx` | antd 三项核验台已完成使命，被 `DesignCheck` 取代 |

### 重命名

`src/app/navigation.ts` → `src/app/nav-model.ts`。理由是 Windows 上 `navigation.ts` 与新增的 `Navigation.tsx` 只有大小写之差，`tsc` 直接报 `TS1261`。

### 新增

```
web/ui-next/src/
  app/
    AppShell.tsx  AppShell.module.css      外壳（48 / 200 / 48）
    Navigation.tsx                          左侧 Menu
    RuntimeIndicator.tsx                    顶栏右侧入口
    PageTitle.tsx                           工作区头的 h1
    NotFound.tsx                            404
    nav-model.ts                            主导航模型（纯函数）
    page-meta.ts                            路由 handle → 页面标识
    ui-preferences.ts                       localStorage 的唯一入口
    theme.test.ts                           31 条
    nav-model.test.ts                       15 条
    ui-preferences.test.ts                  9 条
    AppShell.test.tsx                       22 条（真实路由表的集成测试）
    design-discipline.test.ts               39 条（纪律扫描）
  components/
    StatusTag.tsx          + .module.css + .test.tsx   24 条
    PlatformLabel.tsx      + .module.css + .test.tsx    6 条
    Time.tsx               + .module.css + .test.tsx   16 条
    ProblemIndicator.tsx   + .module.css + .test.tsx   18 条
    ConflictRecovery.tsx                 + .test.tsx   24 条
    PaidActionButton.tsx   + .module.css + .test.tsx   13 条
  features/post-list/
    columns.tsx            + .module.css + .test.tsx   26 条
  hooks/
    useUnsavedChangesGuard.tsx
  lib/
    contrast.ts            + .test.ts                  13 条
    unsaved-changes.ts     + .test.ts                  11 条
    css.ts                                             cx()
  pages/
    DesignCheck.tsx                         内部核验页

docs/ui-refactor/
  tools/capture_shell.py                    新外壳的截图与量尺寸
  screenshots/shell-*.png                   17 张（8 个界面 × 2 分辨率 + 1 张折叠态）
  screenshots/shell-measurements.json       量出来的值
  STAGE_B_REPORT.md                         本文件
```

`docs/ui-refactor/DESIGN.md` 同步改了四处：§5.2 主色定值与判据、新增 §5.2.1 红黄的可读性边界实测、§13 焦点环的实测结论、§15 关掉"主色待定"。
`README.md` 与 `DECISION_LOG.md §0` 加了 Stage B 的入口与批准记录。

---

## 3. npm dependency 是否变化

**没有变化。一个包都没加、没删、没升。**

```
package.json      d2aff636923b75dd6e05d5a7899f3437f26b2a9d3af252c2407d9043c18ae357
package-lock.json e21ce4fb228a95849d08aba88e043ae31c294dd50a2e93709201d88b60f9e7eb
```

这两个哈希是 `npm ci` 之前与全部工作完成之后各取一次，**逐字节相同**。全程没有跑过 `npm install <pkg>`、没有 `npm update`。

有一件事值得单独交代，因为它本来最可能变成"再装两个包"：

**B2 的组件测试没有装 `jsdom`，也没有装 `@testing-library/react`。** 组件用 `react-dom/server` 的 `renderToStaticMarkup` 渲染成 HTML 字符串再断言——`react-dom` 本来就在依赖里。实测 antd 6 + React 19 在 node 环境下 SSR 正常，`ant-menu-item-selected`、`disabled=""`、`ant-tag-filled` 这些都在字符串里读得到。

代价说清楚：**antd 的浮层（`Tooltip` / `Popover` 的弹出内容）走 portal，SSR 里不出现**。所以"Tooltip 里那句话"这类断言分两层——文案本身断言纯函数（`problemTooltip`、`CONFLICT_COPY`），真实弹出由 Python Playwright 在浏览器里看（§14）。`PaidActionButton` 的 disabled 原因额外挂了一个原生 `title`，让它在没有鼠标时也拿得到。

---

## 4. 最终 primary 颜色

## `#155EEF` · 对白色 **5.41:1**

算法在 `src/lib/contrast.ts`（WCAG 2.1 的相对亮度与对比度，13 条单测），判据在 `src/app/theme.test.ts`：

| 判据 | 实测 | 门槛 |
|---|---|---|
| 填充按钮上的白字 | **5.41:1** | ≥ 4.5 ✅ |
| 与错误红 `#dc2626`（色相 0°） | 距 **220°** | 明显区分 ✅ |
| 与风险黄 `#b45309`（色相 26°） | 距 **194°** | 明显区分 ✅ |
| 焦点环在白底 | 5.41:1 | ≥ 3（非文本）✅ |
| 焦点环在 `#f1f5f9` | **4.94:1** | ≥ 3 ✅ |
| 不是"通用 AI 紫" | 色相 **220°** | 不在 250–300° ✅ |

**选择理由，逐条：**

1. **Stage A 的 `#1677ff` 必须换。** 它对白色只有 **4.10:1**。这个数字有一条专门的测试记着（`contrast.test.ts` 里"复算出 Stage A 占位主色为什么不够"），免得以后有人凭肉眼换回一个更亮的蓝。
2. **旧 UI 的 `#4f46e5` 色相 244°**，正好在提纲点名要避开的紫区。`#155EEF` 是 220°，明确的冷蓝。
3. **不追求"刚好过线"。** 5.41 留了 0.9 的余量，因为主按钮上的字是「通过并创建排期」这类不能看错的文案，而她一天按几十次。
4. **hover / active 往深走**（`#0F47B8` / `#0C3A97`），不用 antd 默认的"变浅"。变浅会让白字对比度在 hover 那一刻掉下 4.5；往深走反而升到 8.03:1 / 10.16:1。这条也有断言。
5. **浅底上的文字用 `#0F47B8` 不用 base。** 12px 的 `Tag` 不适用大文本豁免，base 在 `#E8EFFD` 上只有 4.69:1，余量太小；`#0F47B8` 是 6.96:1。

**顺手钉住的一条实测**（写进 DESIGN.md §5.2.1）：红与黄沿用旧值，但它们**不是**处处都过 4.5——

```
#dc2626 on #ffffff = 4.83  ✅ 可做文字
#b45309 on #ffffff = 5.02  ✅ 可做文字
#dc2626 on #f1f5f9 = 4.41  ⚠ 只能做图标 / 线 / 标记底
#dc2626 on #fee2e2 = 3.95  ⚠ 同上
#b45309 on #fef3c7 = 4.28  ⚠ 同上
```

这三条不到 4.5 的组合以前没人算过。结论不是换颜色（DESIGN.md §5.4 明确说红黄沿用旧值，它们已经在真实中英德正文上看过），而是**限定用法**：`ProblemIndicator` 是图标 + Tooltip，不是 12px 彩色文字；`mark.mk-*` 的文字色是继承的 `#0f172a`。测试按"这个颜色实际落在什么底色上"分组断言，没有一刀切。

---

## 5. token source of truth 实现方式

`src/app/theme.ts` 里一组 `const`，**三路输出**：

```
neutral / status / primary / space / typography / layout / elevation / motion
        │
        ├── tokens          → TS 代码直接读（列宽、行高这类要数字的地方）
        ├── antdToken       → ConfigProvider theme.token
        ├── antdComponents  → ConfigProvider theme.components（Layout / Menu / Table / Tag / Breadcrumb）
        └── cssVariables    → :root 上的 --rc-*  → *.module.css
```

`antdComponents` 是这一轮新加的。加它的直接原因：**antd 的 `Layout.Header` 默认是深色 `#001529`**，不覆盖就是一条深蓝顶栏；`Menu` 的折叠宽度、`Typography.Title` 的 38/30/24/20/16px 五档营销级字号同理——不拉回刻度，Stage C 随手写一个 `<Title level={2}>` 就是 30px，「同级必须同字号」当场破掉。

**怎么保证它真的只有一个来源**——不是靠自觉，靠两组会红的测试：

`src/app/theme.test.ts`（31 条）
- 每一个 `--rc-*` 变量的值都能在 `tokens` 里找到来源（含 `0 16px` 这种组合值里的每一个数）；
- `antdToken` 与 `antdComponents` 的每个值同样来自 `tokens`；
- 48 / 200 / 48 三个外壳尺寸同时喂给 antd token 与 CSS 变量，两边一致；
- 间距只有六档且都在 4 的刻度上；`fontSize 14 / controlHeight 32 / borderRadius 6`；正文 15 / 1.7。

`src/app/design-discipline.test.ts`（39 条，扫整个 `src/`）
- 样式表里**一个十六进制颜色都没有**，也没有 `rgb()` / `hsl()`；
- TS/TSX 里除 `theme.ts` 与两份对照测试外没有颜色字面值；
- `*.module.css` 里 `font-size` / `padding` / `margin` / `gap` / `border-radius` / `height` / `line-height` **没有一个 px 字面值**；
- 反向确认：每个 `.module.css` 都确实在用 `var(--rc-`（不是靠"没写样式"混过去）。

扫描前会先去掉注释——规矩管的是代码，不是解释规矩的那句话（`global.css` 里写着「⛔ `scroll-behavior: smooth` 全局禁用」，那行注释正是我们要的东西）。

**三处不进刻度的值，逐个交代**（都在 `global.css`，都有注释）：

| 值 | 在哪 | 为什么 |
|---|---|---|
| `border: 3px solid transparent` | 滚动条滑块 | 滑块几何，不是版面间距 |
| `padding: 1px 0` / `border-radius: 2px` | `mark.mk` | 行内标记跟着字形走；最终画法归 Stage D1 |
| `max-width: 5em` | 分类 `Tag` | 用 em 不用 px：它是"最多几个字"，换字号时不会变成要人工重算的魔法数 |

---

## 6. AppShell 实现说明

```
┌──────────────────────────────────────────────────────────────┐
│ DE 审校台  ☰  审校队列                          ● 运行状态   │ 48px sticky
├──────────┬───────────────────────────────────────────────────┤
│ 审校队列 │  审校队列  ← h1 20/600                            │
│ 历史归档 │  ………                                              │
│ 发布月历 │                                                   │
│ 运营设置 │                                                   │
└──────────┴───────────────────────────────────────────────────┘
  200 / 48
```

- `Layout` / `Layout.Header` / `Layout.Sider` / `Layout.Content` / `Menu mode="inline"`，导航行为一行没自己写；
- `Sider collapsible trigger={null}`，折叠按钮放顶栏（带 `aria-label` 与 `aria-expanded`）；
- **48 / 200 / 48 三个数不在 `AppShell.tsx` 里**，从 `theme.ts` 来；
- 顶栏**没有**旧版那两行永久说明（「US 站图文帖 → …」「保存与排期均会留档…」），有一条测试专门断言它们不出现。

### 顶栏标题与 h1 的分工

顶栏中部是**定位文字**（14/600）或详情页的面包屑，**不是 h1**。界面的 `h1`（20/600）由 `app/PageTitle.tsx` 画在正文的工作区头第一行。

这是照 [UI_ARCHITECTURE_PROPOSAL.md §2.1](UI_ARCHITECTURE_PROPOSAL.md) 那张图的分工：顶栏回答"我现在在哪"（左侧折叠成 48px 图标之后，这行小字是唯一的定位信息），工作区头是标题。两处的文字来自**同一个路由 `handle`**，不可能不同步。

⚠️ 给 Stage C 的约束：**工作区头右侧接筛选与计数，不要再另起一个页面标题。** 一条测试断言每个界面恰好一个 `h1`（旧 UI 的详情页压根没有 h1）。

### 运行状态入口

`Badge status="default"` + 「运行状态」+ 链接到 `/runtime`。**没有绿点，没有"运行正常"。**

真实语义要把 `GET /api/runtime` 的五个阶段映射成一个词，而那五个阶段字段各不相同、后端也没给判别字段（[REACT_MIGRATION_PLAN.md §3.5](REACT_MIGRATION_PLAN.md) 第 12 条）。在做出那个映射之前写一个绿点等于对她撒谎，而这个指示器存在的唯一理由就是让她能信它。Stage H 补真实语义时只换 `status`，组件形状不用改。

用 `Link` 不用 `Button`：它就是一个链接，要能中键新开、要能复制地址；把 `<a>` 塞进 `<button>` 里两样都做不到，而且是非法 HTML。

### 折叠偏好

`localStorage` 的键只有一个：`rc.ui.sider-collapsed`。

`app/ui-preferences.ts` 是**唯一入口**，`design-discipline.test.ts` 扫整个 `src/`，除这个文件外任何地方出现 `localStorage` / `sessionStorage` / `document.cookie` 都会红。判据写在文件头上：**这条信息丢了，业务上有没有后果？** 有 → 不许放这里（筛选进 URL，草稿进组件 state，业务状态进服务端账本）。

storage 不可用（隐私模式、被策略禁掉）或一读就抛时整体降级成"不记住"，不让外壳崩；手改过的垃圾值一律当"没设过"，回落默认展开。

### 焦点环（一处需要交代的实现细节）

要求是"统一 2px primary outline + 2px offset"。实测发现做不到——**antd 至少有八条组件级 `:focus-visible` 规则**，特指度从 `a:focus-visible`（0,1,1）到 `.ant-menu-light .ant-menu-item:not(.ant-menu-item-disabled):focus-visible`（0,4,0），全都写死 `outline-offset: 1px`。裸选择器和 `:root ` 前缀都压不住，浏览器里实测是同一屏上两种焦点环（链接 2px offset、按钮与菜单 1px offset）。

用选择器去追那八条等于把 antd 的内部类名抄进我们的样式表，每次升级都要重新对一遍。所以选了和 `prefers-reduced-motion` 同一个手法：**`!important`**。理由也一样——这是无障碍底线，不接受任何组件覆盖。宽度那一侧不用抢：`antdToken.lineWidthFocus` 就是同一个 token。

浏览器复测：链接与 antd 按钮现在都是 `2px solid rgb(21, 94, 239)` / `outline-offset: 2px`。

### 离开守卫

`hooks/useUnsavedChangesGuard.tsx`，覆盖旧 UI 的全部三层：应用内导航与浏览器前进后退走 `useBlocker`，关标签页走 `beforeunload`。确认框从原生 `window.confirm` 换成 antd `Modal.confirm`，**文案一字不改**（换的理由不是好看：原生 confirm 不受 `ConfigProvider` 管，按钮是英文的 OK / Cancel，而这是中文界面）。

核心判断抽到 `lib/unsaved-changes.ts` 的纯函数，11 条单测：

- **不脏就不拦**——总是弹确认框的守卫，三天之内就会被训练成"闭着眼点继续"，那时它已经不保护任何东西；
- **只看 `pathname`，不看 `search`**——详情切标签页、列表改筛选都只动 search，拦它们是纯干扰；换界面、换另一篇、跳历史详情都拦；
- 两句文案与旧 UI 逐字一致，且只有这两句（没有自己发明的第三句）。

⚠️ **按 §18 的要求，没有往占位界面塞假的 dirty 开关。** 浏览器级的 B4 回归要等 Stage D / G 接上真实脏状态。

---

## 7. URL / router 是否发生变化

**路由表没有变化。** 七条业务路由、旧 URL 重定向、详情的 search params 契约全部照旧。

两处**新增**，都不影响既有契约：

| 变化 | 内容 |
|---|---|
| 每条路由加了 `handle` | 页面标识（标题 / 面包屑来源 / 是否内部页）。纯前端元数据，不进 URL |
| `/_stage-a-check` → `/_internal/design-check` | Stage A 的核验台换成 B2 的核验台。生产导航里没有入口，直达时外壳会挂一条"内部核验页"的说明 |

浏览器实测（`shell-measurements.json`）：

| 旧 URL | 落到 |
|---|---|
| `/?task=fa_neakasaofficial/122100548013379375` | `/review/fa_neakasaofficial/122100548013379375` |
| `/?view=history&task=in_neakasa.tech/3975547640610092585` | `/history/in_neakasa.tech/3975547640610092585` |
| `/?view=calendar` | `/calendar` |
| `/?view=nope` | `/review` |

**详情上下文整页重载后仍然成立**：

```
search       ?queue=processed&platform=facebook&month=2026-07&alerts=1&tab=text
backHref     /review?queue=processed&platform=facebook&month=2026-07&alerts=1
history.state.usr   null          ← 没有任何用户态，上下文全部来自 URL
history.state 的键   ["idx"]      ← 只有 React Router 自己的记账
```

`tab` 是详情自有参数，返回列表时被摘掉；其余五个一个不少。

---

## 8. shared primitives 清单

| 组件 | 文件 | 测试 |
|---|---|---|
| `StatusTag` | `components/StatusTag.tsx` | 24 |
| `PlatformLabel` | `components/PlatformLabel.tsx` | 6 |
| `BerlinTime` / `ShanghaiTime` | `components/Time.tsx` | 16 |
| `ProblemIndicator` | `components/ProblemIndicator.tsx` | 18 |
| `ConflictRecovery` | `components/ConflictRecovery.tsx` | 24 |
| `PaidActionButton` | `components/PaidActionButton.tsx` | 13 |
| `PostRow` 列工厂 | `features/post-list/columns.tsx` | 26 |

---

## 9. 每个 primitive 的职责边界

### `StatusTag`

**管**：八个展示态 → 文案 + 三档视觉重量（待办 / 中性 / 已推进）。顺带导出 `isTerminalStatus`。

**不管**：状态之间能不能转、行能不能点、该不该显示动作。

三条纪律有测试守着：`scheduled` 是「已排期」不是「已发布」（八个状态的渲染结果里都不出现"已发布"）；`skipped` 是中性档，渲染出来不带 `ant-tag-error` / `ant-tag-red`，而且 `.neutral` 那段 CSS 里连 `--rc-error` 都不许出现；每个状态都带文字。

`not_ready` = **未就绪**（不是旧 UI 的「待处理」）。

终态（`skipped` / `handed_off`）与 `not_ready` 的区分**做在行上**，不做在标记上——`postRowClassName` 给终态整行降饱和。`approved` 不是终态，它是"正在提交"。

### `PlatformLabel`

**管**：`facebook` / `instagram` → 图标 + 文字，全应用一种写法。

**不管**：窄屏什么时候切 icon-only（`iconOnly` 是个开关，谁需要谁传；真实断点策略归 Stage C）。

⛔ **不用品牌色**：在 72px 的平台列里放一个品牌蓝和一个品牌粉，等于凭空多两种饱和色，会削掉红黄两种真正的信号。图标走 `currentColor`，测试断言渲染结果里没有写死的 `fill="#…"`。

### `BerlinTime` / `ShanghaiTime`

**这是业务正确性组件，不是样式组件。** 存在的唯一理由：让 `new Date(...)` / `toLocaleString(...)` 在页面代码里一次都不出现。

**管**：把 ISO 串渲染成她能读的时刻，带时区词，带 `<time datetime>`，没有值时给占位不给空白。

**不管**：任何时间运算——两个组件都只调 `lib/format.ts`（从旧 `format.js` 1:1 移植，算法一字未改）。

测试跑在 `TZ=America/New_York`（故意不用柏林也不用上海——生产机在中国，用 `Asia/Shanghai` 跑测试时一个错误依赖本地时区的实现照样会过）。覆盖 DST 切换日 3/29 与 10/25 的两种偏移、UTC 串、上海跨日、冬夏两季，以及"同一个瞬间柏林与上海显示不同小时"。

`design-discipline.test.ts` 另有三条反向断言：展示层不出现 `toLocaleString`、不自己 `new Date()`、`Intl.DateTimeFormat` 只在 `format.ts` 里且钉死 `Asia/Shanghai`。

### `ProblemIndicator`

**管**：「这一篇有没有事」→ 固定优先级 **硬闸 > 风险 > 第三方作者 > 无**，一行只画一个图标，完整文案进 Tooltip。

**不管**：处理到哪一步了（那是 `StatusTag`）、怎么修。

判据只接列表载荷里真实存在的三个字段（`hard_alerts` / `risk_count` / `author_flag`）。有一条测试用 Proxy 把其它字段变成"一碰就抛"，确认组件真的没读别的。

`author_flag` 常态是 `null`（真实数据 26/26 篇都是），空串也当没有。

### `ConflictRecovery`

**管**：把 409 翻译成两句业务语言——**你的修改还在**，以及**按这个按钮会怎样**。

**不管**：怎么恢复。`onRecover` 由业务 feature 给，因为四种冲突的恢复动作完全不同（正文草稿要保留、分类要重读、设置要重取版本）。

测试拿一张 19 个词的黑名单（`409`、`HTTP`、`revision`、`CAS`、`conflict`、`mismatch`、`precondition`、`etag`、`version`、`payload`、`status`…）扫渲染结果与文案表，一个都不许出现。另有两条：每一句都明说"还在"；**不许写成"保存失败"**——她的修改没有丢，这正是要说清楚的部分。

恢复文案沿用她已经认识的说法（「载入最新分类」「刷新状态，保留填写内容」）。

### `PaidActionButton`

**管**：付费动作的形态——`default` 不是 `primary`，金额写在按钮上，旁边可选剩余次数。

**不管**：钱怎么算、任务怎么发。

⛔ **disabled 必须给原因，用类型堵死**：`disabledReason` 是唯一能让按钮变灰的入口，没有独立的 `disabled` prop，所以**构造不出"灰着但没有理由"的实例**（测试里有一条 `@ts-expect-error` 守着）。

旧 UI 最严重的一处是 `ApprovalPanel.disabled` = 六个条件的或，界面上只有一个灰按钮。这里调用方必须先把那六个条件收敛成一句话。

原因同时挂 Tooltip 与外层 `span` 的原生 `title`——antd 的 Tooltip 走 portal，只在悬停时挂到 body 上，光靠它"为什么不能点"在没有鼠标时是不可得的。

### `PostRow` 列工厂

**管**：缩略图 / 问题 / 摘要 / 状态 / 时刻 / 平台 / 分类 / 动作槽这八列的统一实现，以及终态行的降饱和类名。

**不管**：取数、筛选、排序、分页、行点击、具体业务动作——那些是 Stage C / E′。

**接口形状**：一个列清单 + 三个可选适配器（`problem` / `time` / `actions`），**不是 30 个 boolean**。队列与历史的差异只有这三处：要不要问题列、时刻列取哪个字段用哪套时区、行尾画什么。

三条硬约束，都有测试：

1. **没有勾选列**。列键里连 `selection` 这个概念都不存在；渲染结果里没有 `type="checkbox"` 也没有 `ant-checkbox`。
2. **只用列表载荷里有的字段**。`PostRowBase` 就是队列项与历史项的交集。有一条测试用 Proxy 把 16 个详情字段（`text` / `de_human` / `machine_current` / `localization` / `read_only` / `created_at` …）变成"一碰就抛"，然后把每一列的 cell 都渲染一遍。
3. **没有「译文来源」四态列**。列侧唯一可靠的译文依据是 `text_de_excerpt` 是否为空串，所以摘要列在空串时显示「还没有德语译文」——**不从 `status` 猜**。有一条测试双向验：空串 + 任意状态都显示"没译文"；有内容 + `not_ready` 也显示内容。

另外跑了一遍真实载荷：26 条队列数据 × 8 列、30 条历史数据 × 6 列，全部渲染不抛。

顺手修的一个真实问题：`thumbnail_url` 为空串时不再渲染 `<img src="">`（那会变成浏览器的碎图标，看起来像故障），改成画一个空位——"这一篇没有图"是正常状态。

---

## 10. 新增测试数与分类

**148 → 415，净增 267 条。** 一条旧测试都没删。

| 文件 | 条数 | 守什么 |
|---|---|---|
| `app/theme.test.ts` | **31** | 主色六条判据、状态色按实际底色分组的对比度、token 三路同源 |
| `app/design-discipline.test.ts` | **39** | 颜色/刻度/无障碍/动效/storage/时区/禁装清单/本版不做的东西 |
| `app/AppShell.test.tsx` | **22** | 真实路由表的集成：路由→选中项、顶栏、面包屑往返、404、zh-CN、内部页 |
| `app/nav-model.test.ts` | **15** | 四项导航、详情页归属、`/runtime` 不亮、前缀不误判 |
| `app/ui-preferences.test.ts` | **9** | 默认展开、往返、storage 不可用、垃圾值、只存 UI 偏好 |
| `components/StatusTag.test.tsx` | **24** | 八态文案、`scheduled` 不写"已发布"、`skipped` 不用红、三档、终态 |
| `components/ConflictRecovery.test.tsx` | **24** | 19 个工程词的黑名单 × 4 种冲突、"修改还在"、不写"失败" |
| `components/ProblemIndicator.test.tsx` | **18** | 四档优先级、Tooltip 文案、只读三个列表字段 |
| `components/Time.test.tsx` | **16** | DST 两个切换日、UTC、上海跨日、冬夏、两套语义不互相污染 |
| `components/PaidActionButton.test.tsx` | **13** | 不是 primary、金额可见、disabled 必有原因（含类型层） |
| `components/PlatformLabel.test.tsx` | **6** | 统一写法、图标+文字、iconOnly 的无障碍名、无品牌色 |
| `features/post-list/columns.test.tsx` | **26** | 无勾选、共用列、时区分工、不依赖详情字段、真实载荷 |
| `lib/contrast.test.ts` | **13** | WCAG 算法、黑白 21:1、复算 `#1677ff` 的 4.10 |
| `lib/unsaved-changes.test.ts` | **11** | 两句文案逐字、不脏不拦、只看 pathname、`beforeunload` |

Stage A 的五份（marks 22 / format 24 / http 25 / search-params 29 / shape 48）**一条没动，全绿**。

### 对着 §30 / §31 的清单逐条

| 要求 | 落在哪 | |
|---|---|---|
| nav route → selected Menu item | `AppShell.test.tsx` 6 条（真实路由表 + 真实 Menu，读 `ant-menu-item-selected`）+ `nav-model.test.ts` | ✅ |
| Sider collapsed preference | `ui-preferences.test.ts` + 浏览器实测（折叠→48px→刷新仍 48px） | ✅ |
| localStorage 只存 UI preference | `ui-preferences.test.ts` 4 条 + `design-discipline` 的全量扫描 | ✅ |
| old URL redirect 未破坏 | `search-params.test.ts`（Stage A 29 条）+ 浏览器实测 4 条 | ✅ |
| detail query context round-trip | `AppShell.test.tsx` 3 条 + 浏览器整页重载实测 | ✅ |
| 404 回队列 | `AppShell.test.tsx`（文案 + `href="/review"` + 不出现工程语言） | ✅ |
| final primary contrast ≥ 4.5:1 | `theme.test.ts` + `contrast.test.ts` | ✅ |
| theme token 与 semantic CSS variable 同源 | `theme.test.ts` 4 条 + `design-discipline` 的字面值扫描 | ✅ |
| zh-CN locale | `design-discipline`（`index.html` 的 lang、`zhCN`、dayjs）+ `AppShell.test.tsx`（空状态不是英文、按钮不插空格） | ✅ |
| focus-visible | `design-discipline` 3 条 + 浏览器实测（链接与按钮都是 2px/2px） | ✅ |
| reduced motion | `design-discipline` 2 条 + 浏览器确认规则在产物里 | ✅ |
| StatusTag 八态文案 | `StatusTag.test.tsx` | ✅ |
| BerlinTime 不受机器时区影响 | `Time.test.tsx`（跑在纽约时区） | ✅ |
| ShanghaiTime 按 Asia/Shanghai | `Time.test.tsx` + `design-discipline` 的反向断言 | ✅ |
| ProblemIndicator 优先级 | `ProblemIndicator.test.tsx` | ✅ |
| ConflictRecovery 不泄漏工程术语 | `ConflictRecovery.test.tsx` | ✅ |
| PaidActionButton disabled reason 可得 | `PaidActionButton.test.tsx`（含类型层与原生 title） | ✅ |
| PostRow 不依赖详情字段 | `columns.test.tsx` 的 Proxy 断言 | ✅ |

---

## 11. `npm test` 结果

```
 RUN  v5.0.0

 Test Files  19 passed (19)
      Tests  415 passed (415)
   Duration  5.23s
```

---

## 12. `npm run typecheck` 结果

```
> tsc --noEmit
（无输出）
```

零错误。`strict` + `noUncheckedIndexedAccess` + `exactOptionalPropertyTypes` 全开，一个开关都没放宽。

过程中撞到的两个真问题，都是修掉而不是绕过：

1. **`navigation.ts` 与 `Navigation.tsx` 在 Windows 上只差大小写**，`tsc` 报 `TS1261`。纯函数模块改名 `nav-model.ts`。
2. **CSS Modules 在 `noUncheckedIndexedAccess` 下每个类名都是 `string | undefined`。** 没有用断言硬压，而是加了一个 `lib/css.ts` 的 `cx()`——类名拼不出来时就是没有类名，**这正是运行期真实会发生的事**，断言只会把它藏起来。

---

## 13. `npm run build` 结果

```
> tsc --noEmit && vite build
vite v8.3.0 building client environment for production...
✓ 3172 modules transformed.
dist/index.html                     0.60 kB │ gzip:   0.47 kB
dist/assets/index-DOfPYov2.css      6.61 kB │ gzip:   1.87 kB
dist/assets/index-X2f_Z8Nw.js   1,138.95 kB │ gzip: 365.12 kB │ map: 4,768.72 kB
✓ built in 1.23s
```

对 Stage A（1,047 kB / gzip 335 kB）增加 **92 kB / gzip 30 kB**，来自 antd 的 Layout / Menu / Table / Tooltip / Alert / Breadcrumb / Badge 与七个基元。

`Some chunks are larger than 500 kB` 警告照旧**不处理**，理由与 Stage A 一样：内网工具，产物由 FastAPI 同源伺服、不走公网；现在动代码分割会干扰"新旧 dist 逐个界面对照"这件事。

---

## 14. Python browser regression 结果

### 现有 Python 套件

```
65/65 scripts passed
evidence: state/offline-validation-20260913T153057Z
```

与 Stage A 相同（65 个脚本、0 失败）。**这一轮没有改动任何 Python 代码**，跑它是为了确认新增的 `capture_shell.py` 没有污染 `tests/` 下的任何东西。

### 新外壳的浏览器取景

新增 `docs/ui-refactor/tools/capture_shell.py`，用的是**现有的 Python Playwright + `tests/browser_fixture.BrowserFixture`**，没有建第二套体系。

和 `capture_baseline.py` 的分工：那一份量旧 Vue 应用，这一份量 `web/ui-next/dist`。

**为什么伺服静态产物而不是 FastAPI 宿主**：B1/B2 的页面一个接口都不调——四个业务界面还是占位页，核验页用写死的假行。后端在这里不提供任何可验证的东西，只会多一条风险路径。所以起一个**纯静态 SPA 宿主（只有文件，没有 `/api`）**，并在跑完之后把整个会话的请求清单写进产出。

安全边界仍然挂在现有夹具上：浏览器可执行文件来自 `BrowserFixture`、`socket.connect` 的回环限制在本进程内生效、退出时校验真实 `config.toml` 未被改动、真实 `archive/` 与 `state/` 一个字节都不碰。

实测记录（`docs/ui-refactor/screenshots/shell-measurements.json`）：

| 字段 | 值 |
|---|---|
| `requests`（整个会话） | 75 条，全部是 `http://127.0.0.1:<临时端口>/` 下的静态文件 |
| `api_requests` | **[]** |
| `api_hits_at_server` | **[]** |
| `denied_backend_requests` | **[]** |
| `real_external_actions` | **false** |

---

## 15. 1366×768 截图结论

八个界面 + 折叠态，共 **9 张**（`docs/ui-refactor/screenshots/shell-*@1366x768.png`）。

| 量的是 | 值 | 判据 |
|---|---|---|
| 顶栏高 / 位置 | **48px** / `position: sticky`, `top: 0` | 固定 ✅ |
| 左侧展开宽 | **200px** | ✅ |
| 左侧折叠宽 | **48px** | ✅ |
| 内容区 | 宽 1166 / `top: 48` / `left: 200`（折叠后 1318 / 48 / 48） | 纵向只让出 48px ✅ |
| 导航项 | 4 项，**折行 0**，文字截断 0 | 不折行 ✅ |
| 主导航是否含「运行状态」 | **否** | ✅ |
| `h1` 数量 | **1**，内容 = 界面名 | ✅ |
| 同级标题字号种数 | H1 20px / H2 14px，各 **1 种** | 旧 UI 是 3 种 ✅ |
| 中文异常换行 / 溢出元素 | **0** | ✅ |
| 内容区宽度利用率 | **0.98** | 没有大面积无意义留白 ✅ |
| 行内 `danger` 按钮 | **0** | 旧 UI 队列页 17 个 ✅ |
| 页面滚动 | 占位页 1.0 屏；核验页 2.48 屏（它要展示七个基元，本来就长） | ✅ |
| 焦点环 | 链接与 antd 按钮都是 `2px solid rgb(21, 94, 239)` / offset `2px` | 统一 ✅ |
| `--rc-primary` / `--rc-topbar-h` / `--rc-sidebar-w` | `#155EEF` / `48px` / `200px` | token 真的落到 DOM 上 ✅ |
| `--ant-*` 变量规则数 | 416 | antd cssVar 生效 ✅ |
| `scroll-behavior` | `auto` | 平滑滚动全局禁用 ✅ |
| `prefers-reduced-motion` 规则 | 在产物里 | ✅ |

⚠️ 一条要解释的读数：**折叠态下 `navOverflow` = 4**。那是 antd 折叠时把标签文字压成零宽（`scrollWidth > clientWidth`），不是真的溢出——折叠态本来就只显示图标。展开态是 0。

**不要求 12 行 table**：Stage C 还没做，队列页现在是占位页。

## 16. 1920×1080 截图结论

同样八个界面，**8 张**（`shell-*@1920x1080.png`）。

顶栏 48 / 左侧 200 / 内容 `top: 48` `left: 200`、导航 0 折行、`h1` 1 个、溢出 0、行内 danger 0，与 1366 完全一致；差别只有内容宽度 **1720px**、利用率 **0.99**、核验页滚动降到 1.77 屏。

两个分辨率之间**没有任何需要分档处理的差异**——外壳在这一档是纯线性的。1280–1599 要隐藏哪些列是 Stage C 的事（DESIGN.md §15 已记），外壳这一层不涉及。

---

## 17. 是否修改任何 API contract

**没有。** 逐项核对：

| 项 | 结论 |
|---|---|
| endpoint | 一个都没加、没删、没改路径 |
| 请求字段 | 没改 |
| 响应字段 | 没改 |
| 业务状态语义 | 没改 |
| `revision` / CAS | 没碰 |
| `archive/` / `state/` | 没碰（新增的只有 `state/offline-validation-*` 测试证据目录，`state/` 已 gitignore） |
| `config.toml` / `config.local.toml` | 没改（`capture_shell.py` 退出时校验过真实 `config.toml` 字节未变） |
| `web/api/` 下任何文件 | **一个字节都没改**。Stage A 的 `DIST` 可配置已经完成，这一轮没有重新设计 |

这一轮**没有写过任何后端代码**。

---

## 18. 是否修改旧 `web/ui/`

**没有。** `web/ui/` 下一个文件都没动，`git status` 里它仍然只有 Stage A 那一处 D13a 修复（`task.alerts` → `task.hard_alerts`）。

旧应用继续可构建、可回滚（D12）。

---

## 19. 是否触碰 archive / state / config 真实数据

**没有。**

- `archive/`：零读写。B1/B2 的浏览器取景跑在静态产物上，根本没有后端。
- `state/`：只新增了两个测试证据目录（`offline-validation-20260913T153057Z` 与 `BrowserFixture` 的临时目录），都在 gitignore 的 `state/` 下。
- `config.toml`：`capture_shell.py` 与 Python 套件退出时都会校验它的字节未变，两次都通过。
- `config.local.toml`：没读写（除了 `tools/runtime.py` 自己解析解释器路径）。

---

## 20. 是否有任何真实 approve / calendar-refresh 请求

**没有。** 三层证据：

**一、物理上不可能。** B1/B2 的浏览器取景伺服的是一个**只有静态文件的 SPA 宿主**，`POST /api/tasks/{id}/approve` 与 `POST /api/calendar/refresh` 这两条路径在那个进程里不存在。宿主还额外记录任何以 `/api` 开头的请求——记录是空的。

**二、实际记录。** `shell-measurements.json` 里整个会话 75 条请求，全部是静态资源；`api_requests`、`api_hits_at_server`、`denied_backend_requests` 三个数组**都是空的**。

**三、前端单测一个 HTTP 请求都不发。** 415 条跑在 `environment: 'node'`；新增的组件测试用 `renderToStaticMarkup`，`columns.test.tsx` 里还有一条专门 stub `fetch` 并断言它没被调用过。整套测试里没有任何地方出现 `127.0.0.1:8765`。

另外交代：`tools/test_offline.py` 跑的 65 个 Python 脚本里包含 `tests_browser_workflow`，它用的是 `BrowserFixture` 的 ASGI 白名单（除 `PUT /api/settings`、`PUT …/localization`、`POST …/check` 外所有非 GET 返回 503），approve 与 calendar refresh 在名单之外。

---

## 21. 遗留问题 / deferred items

| # | 问题 | 影响 | 打算 |
|---|---|---|---|
| 1 | **离开守卫没有浏览器级回归（B4）** | 三层机制的核心判断有 11 条纯函数单测守着，但"真实页面上拦住了"还没在浏览器里断言过 | 按 §18 的要求没有塞假的 dirty 开关。等 Stage D / G 接上真实脏状态 |
| 2 | **`skipped` 的最终形态仍未定** | B2 给的是"中性 `Tag` + 终态整行降饱和"，`handed_off` 与它目前同形 | DESIGN.md §15 已记：Stage C 在真实列表上看过再定（删除线 / 锁图标 / 仅降饱和） |
| 3 | **`ShanghaiTime` 默认带「上海」二字，与旧 UI 的 `formatTrailTime` 不同** | 这是按 DESIGN.md §1.2「时刻永远带时区词」做的新行为，不是移植失误。需要逐字一致的地方传 `showZone={false}`（有测试） | Stage D 接操作记录时确认取哪一种 |
| 4 | **`PlatformLabel` 的 `iconOnly` 没有响应式策略** | 它现在只是一个开关，没人自动用 | Stage C 在真实列表上量过 1280–1599 档再定 |
| 5 | **产物 1,139 kB（gzip 365 kB）未分割** | 内网同源伺服，首屏影响有限 | 与 Stage A 同一个判断：现在动代码分割会干扰新旧 dist 对照 |
| 6 | **`/_internal/design-check` 留在路由表里** | 生产导航无入口，直达时有"内部核验页"横幅 | Stage C 起如果不再需要，删掉 `DesignCheck.tsx` 与那一条路由即可 |
| 7 | **焦点环用了 `!important`** | 它会让组件级的焦点定制失效 | 这是有意的（§6）。如果将来真有一处需要不同的焦点表现，那时再谈，而不是现在留口子 |

九个延期项（DEF-1..9）在 [DECISION_LOG.md §4](DECISION_LOG.md)，它们是已决的范围取舍，不是未解决的问题。

---

## 22. `git status --short`

```
 M .gitignore
 M core/index_db.py
 M core/review.py
 M tests/tests_history.py
 M web/api/app.py
 M web/api/query_index.py
 M web/ui/src/components/TaskList.vue
?? docs/ui-refactor/
?? web/ui-next/
```

**与本会话开始时逐行相同。**

其中四个（`core/index_db.py`、`core/review.py`、`tests/tests_history.py`、`web/api/query_index.py`）在 Stage A 之前就是修改状态，这两轮都没碰过；另外三个是 Stage A 的改动，这一轮也没碰。

---

## 23. `git diff --stat`

```
 .gitignore                         |  4 ++++
 core/index_db.py                   |  4 +++-
 core/review.py                     | 11 ++++++++--
 tests/tests_history.py             | 27 +++++++++++++++++++++++-
 web/api/app.py                     | 37 ++++++++++++++++++++++++++++++---
 web/api/query_index.py             | 42 ++++++++++++++++++++++++++++++++------
 web/ui/src/components/TaskList.vue |  8 +++++++-
 7 files changed, 119 insertions(+), 14 deletions(-)
```

**与 [STAGE_A_REPORT.md §11](STAGE_A_REPORT.md) 的输出逐字节相同。** Stage B 的全部产出都在未跟踪的 `web/ui-next/` 与 `docs/ui-refactor/` 里。

**没有提交任何东西，也没有推分支。**

---

## 24. 停在这里

**Stage B complete. Stage C not started.**

Stage C 需要批准之后才开始。届时它可以直接站在下面这些东西上，而不用再各写一套：

- 外壳、导航、页面标题、面包屑、离开守卫；
- `#155EEF` 与 token 的三路单一来源，以及会红的纪律测试；
- 七个共享基元与 `PostRow` 列工厂（历史 Stage E′ 共用同一套）。

第一件要做的事是 [BASELINE_BEHAVIOR.md §12](BASELINE_BEHAVIOR.md) 的 **B21**（第三方作者帖子的队列文案，D13a 欠的那条浏览器回归）与 **B22**（四个页签的分桶），两者都需要先有真实的队列界面。
