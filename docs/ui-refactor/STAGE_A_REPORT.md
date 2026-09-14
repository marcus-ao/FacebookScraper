# Stage A 执行报告

**执行日期：2026-09-13。代码基线 `3536e29`。范围：[DECISION_LOG.md §5](DECISION_LOG.md) 的 Stage A。**

**Stage B1 未开始。** 没有做应用外壳、没有做共享基元、没有做任何一个正式界面。

---

## 1. 实际安装的包与精确版本

`web/ui-next/` 是独立的 npm 工程，与 `web/ui/` 互不影响。共 **162 个包**，`npm audit` **0 个漏洞**。

### dependencies

| 包 | 声明 | 实际解析 |
|---|---|---|
| react | ^19.3.0 | **19.3.0** |
| react-dom | ^19.3.0 | **19.3.0** |
| react-router | ^7 | **7.18.3** |
| @tanstack/react-query | ^5.102.8 | **5.102.8** |
| antd | ^6.6.3 | **6.6.3** |
| @ant-design/icons | ^6.3.4 | **6.3.4** |
| dayjs | ^1.11.23 | **1.11.23** |

### devDependencies

| 包 | 声明 | 实际解析 |
|---|---|---|
| typescript | ^7.0.2 | **7.0.2** |
| vite | ^8.3.0 | **8.3.0** |
| @vitejs/plugin-react | ^6.1.1 | **6.1.1** |
| vitest | ^5.0.0 | **5.0.0** |
| @types/react | ^19.3.0 | **19.3.0** |
| @types/react-dom | ^19.3.0 | **19.3.0** |
| @types/node | ^26.5.1 | **26.5.1** |

### 三件需要你知道的事

1. **`react-router` 按指定钉在 7.x。** 仓库上现在的 latest 是 8.3.1，我没有用它——你写的是 v7。
2. **`dayjs` 是显式 dependency**，因为 `src/main.tsx` 直接 `import 'dayjs/locale/zh-cn'` 并调 `dayjs.locale('zh-cn')`。界面是中文，日期选择器不能是英文。这符合你给的条件（"如果代码直接 import dayjs 或 dayjs locale，把 dayjs 作为显式 dependency"）。
3. **`vitest` 是你那份清单之外唯一新增的一项，需要你追认。** Stage A 的完成判据里有九类单测，需要一个运行器；Vitest 与 Vite 同源、复用同一份配置，是这里成本最低的选择，且只是 devDependency、不进生产产物。它不在你的禁装清单里。**禁装清单上的八项一个都没装**：`@playwright/test`、Tailwind、shadcn、Radix、Framer Motion、React Hook Form、Ant Design Pro Components、Sonner。

### TypeScript 开关

`web/ui-next/tsconfig.json` 按要求全开：

```
strict: true
noUncheckedIndexedAccess: true
exactOptionalPropertyTypes: true
```

另外开了 `noUnusedLocals`、`noUnusedParameters`、`noImplicitOverride`、`verbatimModuleSyntax`、`erasableSyntaxOnly`、`isolatedModules`。

⚠️ **TS 7 移除了 `baseUrl`**（`error TS5102`）。改成只用 `paths: { "@/*": ["./src/*"] }`，相对路径按 tsconfig 所在目录解析。这是 Stage A 撞到的第一个版本差异，已解决。

---

## 2. 新增与修改的文件

### 修改（3 个已跟踪文件）

| 文件 | 改动 | 依据 |
|---|---|---|
| `web/api/app.py` | `DIST` 改为可配（+34 行，见 §9） | [DECISION_LOG §5.7](DECISION_LOG.md) |
| `web/ui/src/components/TaskList.vue` | **一处字段名**：`task.alerts` → `task.hard_alerts`（+7 行，6 行是注释） | [DECISION_LOG D13a](DECISION_LOG.md) |
| `.gitignore` | 加 `web/ui-next/node_modules/`、`web/ui-next/dist/`（+4 行） | 迁移期两份 dist 并存 |

### 新增：`web/ui-next/`（22 个入库文件 + 11 份夹具）

```
web/ui-next/
  package.json  package-lock.json  tsconfig.json
  vite.config.ts  vitest.config.ts  index.html
  src/
    main.tsx                         ConfigProvider + Query + Router 装配
    app/
      theme.ts                       设计 token 的唯一真相源（双向输出）
      queryClient.ts                 Query 全局配置
      router.tsx                     路由表 + 旧 URL 重定向
      RootLayout.tsx                 ⚠️ Stage A 脚手架，不是设计稿
      search-params.ts               URL 契约（queue / 详情上下文 / 旧 URL）
      search-params.test.ts          29 条
    lib/
      marks.ts        marks.test.ts  从 marks.js 1:1 移植 · 22 条
      format.ts       format.test.ts 从 format.js 1:1 移植 · 24 条
    services/
      http.ts         http.test.ts   从 api.js 核心 1:1 移植 · 25 条
      assert-shape.ts                开发期浅层形状断言
    types/
      domain.ts                      领域类型，逐字段按真实载荷手写
      brands.ts                      TaskId / Sha256 / Revision
      shape.test.ts                  48 条，跑在真实载荷的脱敏夹具上
      __fixtures__/*.json            11 份（见 §6）
    pages/
      Placeholder.tsx                ⚠️ 六个界面的占位页
      StageACheck.tsx                ⚠️ antd 6 三项核验台，B1 起可删
    styles/global.css                极少量全局样式 + 三档标记
```

### 新增：`docs/ui-refactor/`

决策收口改了 6 份文档、新增 2 份：

| 文件 | 状态 |
|---|---|
| `DECISION_LOG.md` | **新增**，产品决策的真相源 |
| `STAGE_A_REPORT.md` | **新增**，本文件 |
| `OPEN_QUESTIONS.md` | 13 问全部标记已决，转为依据记录 |
| `UI_ARCHITECTURE_PROPOSAL.md` | 四处架构修正 + 四页签 + 历史分页 + 标签 chips + antd 三项处置 |
| `REACT_MIGRATION_PLAN.md` | URL 契约、依赖清单（加 Vitest）、Stage A 判据 |
| `BASELINE_BEHAVIOR.md` | 回归清单新增 B21–B24 |
| `DESIGN.md` | `not_ready` 文案、§15 去掉两项已决 |
| `README.md` | 加 DECISION_LOG 与本报告的入口 |
| `tools/redact_probe.py` | **新增**，真实载荷 → 脱敏夹具 |

---

## 3. 是否修改了 `web/ui/` 旧代码

**改了，一个文件一处字段名，是你批准的 D13a。**

```diff
- {{ task.alerts?.some(item => item.code === 'unknown_collaborator') ? '查看并翻译' : '查看' }}
+ {{ task.hard_alerts?.some(item => item.code === 'unknown_collaborator') ? '查看并翻译' : '查看' }}
```

另加 6 行注释说明原因与依据。**旧应用的其它 22 个文件一字未动。**

两件要交代的事：

1. **我重新构建了 `web/ui/dist/`**，否则源码修好了、产物还是旧的。新产物 `index-BFSrMYLJ.js`（SHA256 `3d3902bb95383362acd5e98973b57a5023d5462ef8409b49b6d585dd3daaded0`），替换了 `index-Dk6O8FJl.js`。`dist/` 是 gitignore 的，不进版本库；生产机要生效仍需按原流程拷过去。重建后 7 场景浏览器回归全过（见 §7）。
2. **D13a 的浏览器级回归还没有。** 现有夹具里没有第三方作者（`unknown_collaborator`）的帖子，所以那 7 个场景覆盖不到这条文案。Stage A 用两层替代守住了根因，浏览器断言记为 **B21，归 Stage C**：

| 层 | Stage A 的覆盖 |
|---|---|
| 类型 | `ReviewListItem` 没有 `alerts` 键 → 任何 `.alerts` 访问在 `tsc` 就失败 |
| 契约 | shape 测试对真实 `tasks.json` 的 26 条逐条断言"有 `hard_alerts`、没有 `alerts`" |
| 浏览器 | ❌ 待 Stage C（B21） |

---

## 4. 是否修改了 API 契约

**没有。** 逐项核对：

| 项 | 结论 |
|---|---|
| endpoint | 一个都没加、没删、没改路径 |
| 请求字段 | 没改 |
| 响应字段 | 没改 |
| 业务状态语义 | 没改（七态 + `not_ready` 照旧；「已处理」只是前端分桶） |
| `archive/` 数据 | 没动 |
| `state/` 数据 | 只新增了 `state/audit-probe/` 的四份只读 GET 取证与浏览器回归产物目录，都在 gitignore 的 `state/` 下 |
| `config.toml` | **没改**。§9 的新键有默认值，不写进配置文件也成立 |
| `config.local.toml` | 没改 |

唯一的后端改动是 `web/api/app.py` 里前端产物目录的来源，它不在任何请求路径上。

有一处**用户可见文案**按 [D1](DECISION_LOG.md) 改了：新 UI 里 `not_ready` 显示「未就绪」而不是「待处理」。旧 UI 的 `format.js` 保持原文案，两份并行期间不强制一致。这是产品决定，记在 [DECISION_LOG §3.1](DECISION_LOG.md)。

---

## 5. npm build 结果

```
> tsc --noEmit && vite build
vite v8.3.0 building client environment for production...
✓ 1546 modules transformed.
dist/index.html                     0.60 kB │ gzip:   0.48 kB
dist/assets/index-B7g4578s.css      1.32 kB │ gzip:   0.64 kB
dist/assets/index-PcJekxGL.js   1,047.45 kB │ gzip: 334.80 kB │ map: 4,405.39 kB
✓ built in 962ms
```

`tsc --noEmit` **零错误**（strict + `noUncheckedIndexedAccess` + `exactOptionalPropertyTypes` 全开）。

有一条构建警告：`Some chunks are larger than 500 kB`。**不处理**，理由：antd 未做代码分割时就是这个量级；这是内网工具，`web/ui/vite.config.js` 原本就写着"体积不是约束"，而产物由 FastAPI 同源伺服、不走公网。真要分割是 Stage B1 之后的事。

---

## 6. 新增单测结果

```
RUN  v5.0.0
Test Files  5 passed (5)
     Tests  148 passed (148)
  Duration  424ms
```

| 文件 | 条数 | 守什么 |
|---|---|---|
| `src/lib/marks.test.ts` | **22** | Python 码点 vs JS UTF-16、重叠优先级、排序规则 |
| `src/lib/format.test.ts` | **24** | 柏林手工解析、DST 边界、上海时刻、八个状态文案 |
| `src/services/http.test.ts` | **25** | `idPath` 逐段编码、`error.status`/`payload`、export 下载与延迟 revoke |
| `src/app/search-params.test.ts` | **29** | `queue` 分桶、详情上下文往返、历史分页、旧 URL 重定向 |
| `src/types/shape.test.ts` | **48** | 11 份真实载荷的形状 |

### 你点名要的九项，逐条对应

| 要求 | 落在哪 | 结论 |
|---|---|---|
| marks 单测 | `marks.test.ts` 全部 22 条 | ✅ |
| overlap priority 单测 | `marks.test.ts` 的「重叠时 error > risk > warn」4 条（含"与标记先后顺序无关"） | ✅ |
| emoji / 补充平面码点单测 | `charLength` 3 条 + `segment` 的「表情之后的 span 落在正确的字符上」与「用 UTF-16 切会切错」 | ✅ |
| Berlin 格式化单测 | `formatSchedule` 夏令时/冬令时/UTC/星期/补零共 9 条 | ✅ |
| DST 边界格式单测 | 3/29 与 10/25（都是周日）3 条，含"同一天里 +02:00 与 +01:00 都只看墙上时刻" | ✅ |
| `idPath` 单测 | 5 条 | ✅ |
| `error.payload`/`status` 单测 | 8 条（含排期 `suggestions` 与月历 `cards` 两条真实降级路径） | ✅ |
| 真实 JSON 的类型/shape smoke test | `shape.test.ts` 48 条 | ✅ |
| `npm run build` | §5 | ✅ |

### 两个值得单说的测试设计

**时区测试跑在 `TZ=America/New_York`。** 故意不用柏林也不用上海：生产机在中国，用 `Asia/Shanghai` 跑测试时，一个错误地依赖本地时区的"上海时刻"实现照样会通过。换成第三个时区，两套语义都必须自己算对，任何本地时区泄漏当场暴露。

**shape 夹具是真实载荷的机械脱敏产物，不是手写的。** 手写夹具会和后端漂移，而那正是要防的事。`docs/ui-refactor/tools/redact_probe.py` 只动值不动键：64 位 hex → 合成 hex、UUID → 合成 UUID、http URL → `example.invalid`、超 40 字的自由文本 → `『文本 N 字』`；**枚举、状态词、时区名、平台名、短标签、数字、布尔、null、数组长度、嵌套结构全部原样保留**。`/api/` 路径也原样保留（它是结构不是内容）。

⚠️ 副作用：脱敏后 `en_span` / `de_span` 不再指向被截短的正文。夹具只用于形状断言；标记定位的测试用手写输入。这条写在脚本头部和夹具测试的注释里。

取证规模：审校队列 26 条、历史 1,067 条的第 1 页、两份详情（活账号 FB + 冻结 IG）、月历 4 张卡、设置七组受控配置、运行五阶段、approval-options、初翻/优化能力、文案模板。**全部来自真实归档的只读 GET，没有一个 POST/PUT。**

### 顺手钉住的四条数据模型事实

审计文档里几个推断，现在有测试守着了：

- 队列列表项**没有** `read_only` / `created_at`（历史项才有）→ 不许当队列字段用；
- 队列列表项**没有** `text` / `machine_current` → 所以做不了「译文四态」列（[§2.3](DECISION_LOG.md)）；
- 运行状态阶段五有 `unconfirmed_attempts` 但**没有** `task_ids` → DEF-1 的依据；
- `business.processing` 在没有批次时只有三个键，恢复接口要的 `batch_id` 不在里面。

---

## 7. 现有 Python 测试结果

**全量 65 个测试文件，65 通过、0 失败。**

```
=== PASS=65 FAIL=0 ===
failed:
```

改动前先跑了针对性的一组（都通过）：`tests_web_review`、`tests_hygiene`、`tests_runtime_config`、`tests_integrity`、`tests_history`、`tests_index_db`、`tests_query_index`、`tests_review`。

**Vue 浏览器回归在重建 dist 之后重跑过**，7 场景全过：

```
Ran 7 tests in 13.256s — OK
```

证据目录 `state/offline-browser-20260913T141353Z-17236/`，`report.json` 关键字段：

| 字段 | 值 |
|---|---|
| `real_external_actions` | **false** |
| `server_network_or_mutation_denials` | **[]** |
| 每个场景 `page_errors` | **0** |
| 每个场景 `denied_requests` | **0** |
| `vue_build` | `index-BFSrMYLJ.js` → `3d3902bb…aded0`（含 D13a 修复） |

---

## 8. Ant Design 6 三项核验结果

全部在**实际安装的 antd 6.6.3 上**核验，浏览器里读的是构建产物（不是 dev server）。

### 8.1 `theme.cssVar` — 有变化，已按你的规则处理

**结论：antd 6 把 `cssVar` 收窄成只接对象。** v5 的 `cssVar: true` 在 v6 直接类型报错：

```
src/main.tsx(33,34): error TS2559:
  Type 'true' has no properties in common with type '{ prefix?: string; key?: string; }'.
```

`node_modules/antd/es/config-provider/context.d.ts:126` 的定义确认了这一点（只有 `prefix` 与 `key` 两个可选键）。

按 [DECISION_LOG §5.2](DECISION_LOG.md)「不符合预期时不许 hack」，我做了两件事：

1. 用 v6 的**正式形式** `cssVar: { prefix: 'ant' }` —— 这是官方 API，不是绕路；
2. 更要紧的是：**我们自己的 token 不依赖 cssVar。** `src/app/theme.ts` 里一个 TypeScript 对象同时生成 `ConfigProvider` 的 `theme.token` 与 `:root` 上的 `--rc-*` 语义变量。所以"所有颜色、字号、间距、圆角的字面值只有一个真相源"这条不受 antd API 变动影响；cssVar 只是让 antd 组件也走变量、少一层重算。

浏览器实测（`/​_stage-a-check`，1366×768，构建产物）：

| 观测 | 值 |
|---|---|
| 样式表里 `--ant-*` 变量**声明**数 | **688** |
| 样式表里 `var(--ant-*)` **引用**数 | **690** |
| primary 按钮的 class | `ant-btn css-var-_r_0_ ant-btn-primary …`（`css-var-` 前缀 = 变量模式已生效） |
| primary 按钮实际背景色 | `rgb(22, 119, 255)` = `#1677ff` = 我们 token 里的 `colorPrimary` |
| `:root` 上我们的变量 | `--rc-bg #f1f5f9`、`--rc-primary #1677ff`、`--rc-error #dc2626`、`--rc-risk #b45309`、`--rc-row-h 48px`、`--rc-review-header-h 56px`、`--rc-space-4 16px`、`--rc-font-prose 15px` 全部就位 |

### 8.2 `Table` 的 `sticky` — 生效，且**没有**开 `virtual`

按你的要求第一版不用 `virtual`。核验台渲染 60 行、`size="small"`、`sticky`：

| 观测 | 值 |
|---|---|
| `.ant-table-sticky-holder` 的 `position` / `top` | `sticky` / `0px` |
| 滚动前 `thead` 的视口位置 | 226px |
| 滚动 900px 之后 | `thead.top = 0`，仍在视口内 |
| 页面高 / 视口 | 2650px / 1366×768 |

**结论：`sticky` 单独使用可靠。** `virtual` 记为延期项 DEF-7，当前数据量（队列 26、历史每页 50）不值得引。

### 8.3 `Splitter` — 按要求**没有引入**

正文分栏用 CSS Grid 1:1。`Splitter` 记为延期项 DEF-6，等运营确实需要拖动比例再评估。Stage A 的代码里不出现这个组件。

### 8.4 顺带核验：路由与 URL 契约（浏览器实测，构建产物）

这不在你列的三项里，但它是 [§2.1–2.2](DECISION_LOG.md) 两条架构修正的落地证据：

| 核验 | 结果 |
|---|---|
| SPA fallback（8 条深链接直接 GET） | 全部 200 |
| `/?task=fa_neakasaofficial/122100548013379375` | → `/review/fa_neakasaofficial/122100548013379375`，task id 正确拼回并显示 |
| `/?view=history&task=in_neakasa.tech/3975547640610092585` | → `/history/in_neakasa.tech/3975547640610092585`，来源识别为历史归档 |
| 带筛选进详情 | `/review/…?queue=processed&platform=facebook&month=2026-07&alerts=1` —— 上下文进了 URL |
| **整页重新加载该详情 URL** | 上下文仍在；`history.state` 为 `null`，证明**不依赖 `location.state`** |
| 返回列表 | `href` = `/review?queue=processed&platform=facebook&month=2026-07&alerts=1`，筛选与页码恢复，`tab` 被摘掉 |
| 往返幂等 | 来回切换不丢参数、不长出参数（单测另有断言） |

---

## 9. 新旧 dist 如何启动和切换

### 改动本身

`web/api/app.py` 的 `DIST` 从写死改为读 `config.toml` 的 `[paths].web_dist`，默认 `web/ui/dist`：

```python
DEFAULT_DIST_REL = "web/ui/dist"

def _dist_dir() -> Path:
    raw = str(cfg().get("paths", "web_dist", DEFAULT_DIST_REL) or DEFAULT_DIST_REL).strip()
    candidate = Path(raw)
    resolved = (candidate if candidate.is_absolute() else ROOT / candidate).resolve()
    if resolved == ROOT.resolve() or not resolved.is_relative_to(ROOT.resolve()):
        print("⚠ [paths].web_dist 指向仓库之外或仓库根，已回落 %s" % DEFAULT_DIST_REL)
        return (ROOT / DEFAULT_DIST_REL).resolve()
    return resolved

DIST = _dist_dir()
```

选 `[paths]` 而不是环境变量，是因为 `config.local.toml` 里 `archive` / `state` 已经在这一节——运维切 UI 时，跟他看归档路径是同一个地方。

加了一道**越界护栏**：这个目录会被 `StaticFiles` 直接伺服，指到仓库外面就等于把任意目录挂上 HTTP。越界或指到仓库根时回落默认值并打印警告。判据和归档那边同一条纪律。

`config.local.toml` 的覆盖白名单只允许 `paths.archive` / `paths.state`，所以 `web_dist` **不能**从运行绑定文件设置，只能改 `config.toml`。这是有意的：切 UI 是一次明确的决定，不该混在机器级绑定里。

`/` 的 503 提示也跟着变成按当前挂载点生成，不再写死 `web/ui`。

### 实测

| 场景 | 结果 |
|---|---|
| 不配置（现状） | `DIST = web/ui/dist`，与改动前**逐字节一致**；`index.html` 存在 |
| 配 `web_dist = "web/ui-next/dist"` | `DIST = web/ui-next/dist` |
| 配 `web_dist = "../../../Windows"` | 打印警告并回落 `web/ui/dist` |

真实 `config.toml` **没有改**，上面第 2/3 条是在临时配置文件上验的。

### 怎么用

构建两份产物：

```bash
cd web/ui && npm install && npm run build
```

```bash
cd web/ui-next && npm install && npm run build
```

默认仍然跑旧 Vue 应用，命令与 README 一致：

```bash
scripts\run_python.bat -m uvicorn web.api.app:app --host 127.0.0.1 --port 8765
```

要切到新 React 应用，在 `config.toml` 的 `[paths]` 下加一行，然后重启进程：

```toml
[paths]
web_dist = "web/ui-next/dist"
```

回滚就是删掉那一行（或改回 `web/ui/dist`）再重启。**不碰接口、字段、账本、归档**；两份产物都留着、都能构建。

开发时两个应用可以同时起：旧的 5173，新的 5174（`web/ui-next/vite.config.ts` 里定的），`/api` 都 proxy 到 8765。

⚠️ 目前 `web/ui-next/dist` 是 Stage A 的脚手架（占位页 + antd 核验台），**不要**切到生产。切换能力现在就绪只是为了 Stage B1 起能随时 A/B 对照。

---

## 10. 尚未解决的问题

| # | 问题 | 影响 | 打算 |
|---|---|---|---|
| 1 | **D13a 缺浏览器级回归**（现有夹具没有第三方作者帖子） | 字段名根因已被类型层与契约层守住，但"队列上那行文案真的出现了"还没在浏览器里断言过 | B21，Stage C 造一篇 `unknown_collaborator` 夹具补上 |
| 2 | **`vitest` 需要你追认** | 它不在你给的安装清单里，也不在禁装清单里 | 见 §1 第 3 条；不认可的话可以换 `node:test` + `tsx`，代价是要自己拼 JSON 导入与别名解析 |
| 3 | **构建产物 1,047 kB（gzip 335 kB）未分割** | 内网同源伺服，首屏影响有限 | B1 之后评估；现在动代码分割会干扰"新旧 dist 逐个界面对照"这件事 |
| 4 | **`approve` / `calendar/refresh` 的响应形状仍未捕获** | `ApproveReceipt` 的类型是按 `pipeline/approval.py` 源码写的，不是实测 | 这两个接口一动就有平台侧后果，**不测**。等真实受控排期取证时顺便留一份回执 |
| 5 | **`publication` 仍是 `Record<string, unknown>`** | 原样透传的 journal 记录，没有任何契约文件定义它的形状 | 等 §4 有真实回执时一起定 |
| 6 | **TS 7 是刚 GA 的大版本** | 目前 `tsc --noEmit` 零错误、antd 6 与 React 19 的类型都通过 | 继续用。真出问题就退 5.9，tsconfig 只需要把 `baseUrl` 加回来 |
| 7 | **`--rc-*` 与 `--ant-*` 两套变量并存** | 有意的（前缀区分"我们的主张"与"antd 默认"），但 B1 写 CSS Modules 时要有纪律 | DESIGN.md §2 已写明只准用 `--rc-*` |

**不在这张表上的**：九个延期项（DEF-1..9）在 [DECISION_LOG §4](DECISION_LOG.md)，它们是已决的范围取舍，不是未解决的问题。

---

## 11. `git diff --stat`

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

⚠️ **其中四个不是我改的**：`core/index_db.py`、`core/review.py`、`tests/tests_history.py`、`web/api/query_index.py` 在我这次会话开始之前就已经是修改状态（会话起始的 git 快照里就有它们）。我一次都没碰过这四个文件。

**我实际改的三个**：`.gitignore`（+4）、`web/api/app.py`（+34/-3）、`web/ui/src/components/TaskList.vue`（+7/-1）。

未跟踪的新增：`docs/ui-refactor/`（9 份文档 + 3 个脚本 + 18 张截图 + measurements.json）与 `web/ui-next/`（22 个源文件 + 11 份夹具 + `package.json`/`package-lock.json`）。`node_modules/` 与两个 `dist/` 都在 gitignore 里。

**没有提交任何东西**，也没有推分支。

---

## 12. 明确确认：没有任何测试请求打到真实 approve / calendar refresh

**确认。** 三层证据：

**一、隔离夹具的 ASGI 白名单。** `tests/browser_fixture.py` 在 app 外面包了一层：除 `GET`/`HEAD` 以及 `PUT /api/settings`、`PUT …/localization`、`POST …/check` 三条之外，**所有非 GET 请求返回 503 并记进 `denied_backend_requests`**。`POST /api/tasks/{id}/approve` 与 `POST /api/calendar/refresh` 在这个名单之外，物理上到不了真实实现。

**二、本轮浏览器回归的实际记录**（`state/offline-browser-20260913T141353Z-17236/report.json`）：

```
real_external_actions            : false
server_network_or_mutation_denials: []
```

七个场景的 `denied_requests` 全是 0（说明连尝试都没有），被放行的写入只有这五条，全部落在临时归档上：

```
POST  /api/tasks/fa_neakasaofficial/1234567890/check
POST  /api/tasks/in_neakasa.global/2234567890/check
PUT   /api/settings                                   （临时 TOML）
PUT   /api/tasks/fa_neakasaofficial/1234567890/localization
PUT   /api/tasks/in_neakasa.global/2234567890/localization
```

命中 `approve` 或 `calendar/refresh` 的次数：**0**。

**三、Stage A 新增的 148 条前端单测一个 HTTP 请求都不发。** 它们跑在 `environment: 'node'`，`fetch` 全部是 `vi.stubGlobal` 的替身；`src/types/shape.test.ts` 读的是入库的静态 JSON 夹具。整套测试里没有任何地方出现 `127.0.0.1:8765`。

另外交代两件我**自己**做过的真实请求，都不是测试、都是只读 GET：

1. 审计阶段的七份载荷取证（已报过）；
2. Stage A 为写类型补的四份取证：`GET …/approval-options`、`GET /api/refinements/task/{id}`、`GET /api/initial-translation/task/{id}`、`GET /api/templates/text`。四个都是 GET，都是详情页每次打开就会调的接口。取完立刻停了服务进程。

浏览器核验用的是 `vite preview` 起的 4174 端口（只有静态产物，没有后端），**没有**指向 8765。

---

## 13. 停在这里

Stage A 完成。**没有自动进入 B1。**

Stage B1 需要你批准之后才开始，届时的内容是：应用外壳（48px 顶栏 + 200px 可折叠左侧 Menu + 顶栏运行状态指示器）、`theme.ts` 的主色定值、`tokens.css`、以及 B1–B4 四条回归（路由与三层离开守卫）。

按 [REACT_MIGRATION_PLAN §9.1](REACT_MIGRATION_PLAN.md)，B1 之后还有一个 **B2**（共享基元：`StatusTag`、`PlatformLabel`、`BerlinTime`/`ShanghaiTime`、`ProblemIndicator`、`ConflictRecovery`、`PaidActionButton`、`PostRow` 列工厂），它必须在 C 与 E′ 之前做完——否则队列和历史会各写一套行，回到 P2-3 的老路。
