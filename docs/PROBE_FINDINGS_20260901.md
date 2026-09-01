# 真实 Business Suite 长什么样 · 2026-09-01 首份完整 v2 dump 的判读

> 来源：`state/publish_probe_20260901_054226_378622.json`
> —— **56 条可信交互 / 71 条被动语义快照 / 117 张截图，v2 契约首次完整通过。**
>
> 这是这个项目**第一次**拿到能证明「账号 → 提交 → 成功 → Planner → final」
> 整条因果链的真实录制。此前所有关于 Business Suite DOM 的断言，
> 要么来自旧版创作者后台（另一个界面），要么来自被打断的半截录制。
>
> **本文件里每一条都能回查到具体的快照/交互编号。** 复现：
>
> ```bash
> scripts\run_probe_signals.bat --report state\publish_probe_20260901_054226_378622.json
> ```

---

## 一句话结论

**录制本身是完美的，缺的东西不是没录到 —— 是这个 UI 上根本不存在。**

`--check` 报"推不出账号上下文"和"推不出排期卡片"。追进去看，真相是：

- **composer 上从头到尾没有出现过 IG 账号名**（46 张 composer 快照，`neakasa.de` 命中 0 次）；
- **Planner 上没有"一张卡片带全部元数据"这种东西**：FB 和 IG 是**两个独立对象、
  两个独立 remote ID、两个独立详情弹窗**。

所以**重录一百遍也不会有**。要动的是证据契约，不是录制流程。
这正是项目协议里那条「计划与现实冲突时，以现实为准并记录」。

---

## 一、Composer（`business.facebook.com/latest/composer/`）

| 要素 | 真实语义 | 证据 |
|---|---|---|
| 渠道指示 | `img 'Facebook'` + `img 'Instagram'` —— **图标，不带账号名** | 快照 24 #2/#3 |
| 「发到哪」标题 | `heading 'Post to'` | 快照 24 #1 |
| FB 目标主页 | 只在 FB 预览里：`article` → `heading h2 'Neakasa Deutschland'` | 快照 24 #43/#44 |
| **IG 目标账号** | **⛔ 整个 composer 一次都没出现过** | 全部 46 张 composer 快照 |
| 加图入口 | `button 'Add photo/video'` | 交互 #12 |
| 正文框 | `combobox`（富文本，`is_contenteditable`） | 交互 #14–21 |
| 定时开关 | `switch`（`input type=checkbox`） | 交互 #34 |
| 日期框 | `textbox` 名 `'mm/dd/yyyy'` | 交互 #37/#44 |
| 日期选择 | `button 'Tuesday, 15 September 2026'` | 交互 #39/#45 |
| 时/分 | `spinbutton` | 交互 #40–43、#46–49 |
| **提交按钮** | **`button 'Schedule'`** | 交互 #50（ord=96）候选 1 |
| **成功信号** | `dialog 'Your post is scheduled …'` + 子 `heading 'Your post is scheduled'` | 快照 50（ord=100） |
| 其它出口 | `button 'Publish'`（立即发）、`button 'Finish later'`、`button 'Cancel'` | 快照 24 #37–39 |

⚠️ **提交按钮的候选表第 0 条 `role` 是空的**，第 1 条才是 `role=button`。
空 role 能通过 dump 回查（回查对空 role 放行），但运行时
`page.get_by_role("", …)` 是废的。`probe_signals.py` 已改成优先取带真实 role 的候选。

⚠️ **`Schedule` 与 `Publish` 是两个不同的按钮。** 打开定时开关后点的是 `Schedule`。
自动化只能点 `Schedule`，点错就是立即发布。

---

## 二、Planner（`business.facebook.com/latest/content_calendar/`）

| 要素 | 真实语义 | 证据 |
|---|---|---|
| 页面标题 | `heading 'Planner'` | 快照 62 #0 |
| **可见范围** | `heading 'September'` + `heading '2026'` —— **两条独立 heading，没有"start - end"串** | 快照 62 #12/#13 |
| 视图切换 | `button 'Week'` / `button 'Month'` | 快照 62 #6/#7 |
| **月视图条目** | `link '10:00 AM'` —— **只有时刻，没有正文没有日期** | 快照 62 #16/#17 |
| **周视图条目** | `link 'Hello, this is a test. 🤪 #test September 15, 2026, 10:00 AM'` —— **正文 + 完整日期时刻在同一条 link 的可访问名里** | 快照 57 / 66 |
| 渠道图标 | `img 'Facebook'` / `img 'Instagram'`（页面级，不属于某条目） | 快照 62 #5/#18 |
| **图片数量** | **⛔ 不存在任何数量语义** | 全部 25 张 planner 快照 |

### 详情弹窗：**一个渠道一个，各有独立 remote ID**

点日历条目会打开 `dialog`。**FB 和 IG 是两个不同的弹窗、两个不同的 ID。**

**Facebook**（快照 61，交互 #53 打开）：

```
dialog  'Post details ID: 1887083152480681 Close ​ Post overview This view of your post
         may not represent exactly how it appears on Facebook's Feed. Actions ​
         Neakasa Deutschland September 15 at 10:00 AM Hello, this is a test. #test …'
  ├ heading 'Post details'
  ├ heading 'Post overview'
  ├ article 'Neakasa Deutschland September 15 at 10:00 AM Hello, this is a test. #test …'
  │   ├ heading h2 'Neakasa Deutschland'        ← FB 主页名，独立子语义 ✅
  │   ├ link      'September 15 at 10:00 AM'    ← 时刻，独立子语义 ✅
  │   ├ link      '#test'                       ← 只有标签，**正文不是独立元素**
  │   ├ img       '🤪'
  │   └ img       'May be an image of text'     ← 有媒体的**存在性**证据，不是数量
  └ button  'Publish now' / 'Boost' / 'Close ​'
```

**Instagram**（快照 68，交互 #55 打开）：

```
dialog  'Post details ID: 4378984725697354 Close ​ Post overview This view of your post
         may not represent exactly how it appears on your Instagram feed. Actions ​
         neakasa.de neakasa.de Hello, this is a test. 🤪 #test Boost Publish now'
  └ 子元素全是通用按钮（Post details / Close / Post overview / Actions / Boost / Publish now）
     ⛔ neakasa.de 与正文**只存在于 dialog 自己那一整串可访问名里**，不是独立子元素
```

> 为什么 IG 侧没有独立子语义：probe 只采样带 role 的元素。IG 预览里账号名和正文
> 是无 role 的 `<span>`/`<div>`，被过滤掉了，只在祖先的文本拼接里留下痕迹。
> **这是设计，不是 bug** —— 但它决定了 IG 只能按"整串里含 token"来判。

---

## 三、现有证据契约里，哪几条被现实推翻

| 契约要求 | 现实 | 结论 |
|---|---|---|
| `composer_account_context`：composer 上同一容器同时露出 FB Page 与 IG 帐号 | composer 上**没有 IG 账号名** | ⛔ 不可满足。提交前只能证明 FB |
| `planner_scheduled_card`：一张卡片内含时刻/正文/FB/IG/图片数五组独立子语义 | FB 与 IG 是两个独立弹窗、两个 remote ID | ⛔ 不可满足。必须改成**两条独立渠道证据** |
| `range_regex`：start/end 两个同格式日期 | `heading 'September'` + `heading '2026'` | ⛔ 不可满足。就绪信号应改用 `heading 'Planner'` 或月份 heading |
| `image_count_regex` / `media_image_role` | Planner 侧**零**数量语义 | ⛔ 不可满足。图片数只能在 composer 侧上传后数缩略图 |
| `caption_role` + `caption_probe_text`：正文是卡片内独立子元素 | FB 弹窗里正文不是独立元素 | ⚠️ 部分可满足：**周视图那条 `link` 的可访问名同时含正文与完整日期时刻**，那是最好的锚点 |

---

## 四、可满足的最小严格契约（建议，未实现）

下面每一条都能从这份 dump 里逐字回查到。**不放松"必须两个渠道都确认"这条**，
只是把确认的时机从"提交前"挪到"提交后"—— 与已有结论
（渠道默认全勾选、提交后回读、少任一渠道就转人工）一致。

**提交前**
- `composer_account_context` → 只验 FB：`article` 容器内 `heading h2` == `[publish].facebook_page_name`（快照 24 #44）。
  **IG 提交前不可证** → 明确记为已知缺口，由提交后回读兜住。

**提交**
- `composer_submit_button` → `button 'Schedule'`（交互 #50，可信 click，取带 role 的候选）。
- `composer_success_signal` → `heading 'Your post is scheduled'`，容器 `dialog`（快照 50）。

**回读（三条独立证据，全过才记 `scheduled`）**
1. **时刻 + 正文**：周视图 `link`，可访问名同时匹配目标正文片段与
   `September 15, 2026, 10:00 AM` 形状（`%B %d, %Y, %I:%M %p`）—— 快照 57/66。
2. **FB 渠道**：`dialog` 名匹配 `Post details ID: (?P<remote_id>\d+)` 且含
   `Facebook's Feed`；其 `article` 子元素内 `heading h2` == FB page name，
   `link` 匹配 `%B %d at %I:%M %p` —— 快照 61。
3. **IG 渠道**：`dialog` 名匹配 `Post details ID: (?P<remote_id>\d+)` 且含
   `Instagram feed` 与 `[publish].instagram_account` —— 快照 68。

**Planner 数据就绪** → `heading 'Planner'` 出现即视为壳已渲染；
真正的"数据就绪"用月份 heading（`heading 'September'`）。

**图片数量** → Planner 侧放弃；改为 composer 上传后数 `listitem`
（快照 24 #6/#7 那种 `'1920 x 1080 Edit photo …'`）。

⚠️ **两个 remote ID 不是一个。** 现有 `journal` 只存一个 `remote_id`，
建议改成 `{"facebook": …, "instagram": …}`，否则跨渠道对账会缺一半。

---

## 五、这次测试帖的实际参数（**还挂在远端，记得取消**）

| 项 | 值 |
|---|---|
| 排期时刻 | **2026-09-15 10:00 AM**（UI 显示 `September 15, 2026, 10:00 AM`） |
| 正文 | `Hello, this is a test. 🤪 #test` |
| 图片 | 1 张，1920 × 1080 |
| FB post ID | `1887083152480681` |
| IG post ID | `4378984725697354` |
| 渠道 | FB + IG **都排上了**（印证"进 composer 默认全勾选"） |

**去 Business Suite 内容日历把它取消掉。** 留着不会造成技术问题，
但 9 月 15 日它会真的发出去。

---

## 六、顺带确认/推翻的几件事

✅ **确认**：进 composer 时 FB 与 IG 默认全勾选，不需要点 —— 一次提交两个渠道都排上了。

✅ **确认**：日期输入框 placeholder 是 `mm/dd/yyyy`（美式），时间是 12 小时制 + AM/PM。
`selectors.py` 里那两条旧记录仍然成立。

✅ **确认**：`business_suite` 走 `Schedule` 而不是 `Publish` 是对的 —— 两个按钮都在。

⚠️ **推翻**：`selectors.py` 的 `composer_done_button`（`button 'Done'`）
**在这份 dump 里没有出现**。它来自 24 条那份旧 dump，
现在的流程里没有这一屏。代码里已经把它当可选步骤（出现就点），所以不阻塞，
但别再把它当"必经的一步"。

⚠️ **新增事实**：composer 上有 `button 'Finish later'`（存草稿）。
提交失败时误点它会留下草稿 —— `journal` 里 `prepared` 不算已排期那条设计因此更重要了。

---

## 七、契约已按上面改完（2026-09-01 当天）

第四节那套最小严格契约**已经实现并落地**，五件生产证据全部从这份 dump
机械推导 + 逐条回查通过，三道闸现在是开的：

```
[开] 账号上下文        [开] 提交按钮 + 成功信号        [开] Planner 回读
```

改了什么：

| 文件 | 改动 |
|---|---|
| `publish/evidence.py` | `_verify_planner_structure` 整段重写：条目（正文+时刻同一条 link）+ 每渠道一个详情弹窗 + 月份/年份 heading。新增 `token_present()`（按独立词判账号，`neakasa.de` 不再被 `neakasa.deals` 冒充）、`parse_entry_moment()`（判"只指向一个时刻"而不是"只匹配一次"） |
| `publish/business_suite.py` | `require_account_context_evidence` 收窄为只验 FB；`require_readback_evidence` 换新属性表；`_planner_cards` 改为按 role 全取再按内容筛；新增 `_open_channel_dialogs`（点开弹窗读渠道与 remote id，**只按 Escape 关，弹窗里什么都不点**）；`_visible_calendar_range` 改用月份+年份 heading；`ensure_logged_in` 改为 FB 完整值比较 |
| `publish/journal.py` | 新增 `remote_ids`，每渠道一个远端 ID |
| `tools/probe_signals.py` | 推导跟着改；新增 `--report`；提交按钮改按**整词**措辞排序（子串匹配曾把 `schedule` 命中成功提示「Your post is **scheduled**」，于是"提交按钮"被推成那条 toast） |
| `config.toml` | `ui_probe_dump` 已填这份 dump |

⚠️ **改动过程中抓到的三个同型 bug，都是"拼接文本"造成的**：
`_row_text` 把 `accessible_name` 与 `visible_text` 拼起来，而这两者常常一模一样。
于是 `"5 photos"` 被数成两处数字、`"September"` 变成 `"September September"` 解析不动、
一个时刻被当成两个时刻。**凡是要在单值上判的，就不能拿拼接串去判。**

## 八、还差什么

| 项 | 状态 |
|---|---|
| 五件生产证据 | ✅ 已推导、已回查、已落 `publish/signals_backfilled.py` |
| `[publish].ui_probe_dump` | ✅ 已填 |
| 20 个观察项 | 🟡 **4 项已按 dump 证据填**（入口 URL / 成功信号 / 日期时间输入方式 / 时区），**14 项仍需你亲眼量** |
| `[publish].ui_constraints_verified` | ⛔ 仍为 `false` —— 14 项填完才能改 true |
| 远端那条测试排期 | ⛔ **还挂着，2026-09-15 会真的发出去，去取消** |

那 14 项是 IG 的画幅/图片数/正文长度/标签数四类上限与各自的拒绝行为，
加上排期窗口的上下限。**这些 dump 里没有，也不该由程序猜**（全局红线 5）。

填法见 `docs/GO_LIVE.md` 第 3b 步 —— 现在支持 `--set-note` 一条命令填完，
不用再走 20 问的交互。
