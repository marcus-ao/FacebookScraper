# F 组实施计划 · 接入 LLM API 完成德语翻译

> **给实施 Agent 的任务书。写这份文件时假设你看不到任何历史对话。**
> 需要的背景全在这里，不需要去翻聊天记录。
>
> 撰写：2026-08-30 · 对应 `docs/IMPLEMENTATION_PLAN.md` 的 **F1 / F2 / F3**
>
> ⚠️ **同一时间还有另一个 Agent 在这个仓库里工作**（在修 Instagram 增量抓取）。
> 第 2 节「文件所有权」是硬约束，**先读它再动手**。

---

## 0. 一句话任务

`translate.py` 的代码**已经写完了**（166 项离线断言全绿，零 API 调用）。
你要做的不是从头实现，而是：**把它接上真实的 LLM 网关，用真实文案跑通，
把 1010 篇英文社媒文案翻成可用的德语，并产出人工审校清单。**

代码完成 ≠ 验收通过。F1/F2/F3 三项的验收分别是「对 3 篇试跑」
「人工检查 3 篇输出」「预览器里图片能显示」——**三条都要真实网关 + 真实文案**，
而写代码的时候两样都还没有。现在文案有了（见第 3 节），网关要你去接。

---

## 1. 你的任务边界

### 要做

| # | 事情 | 对应 |
|---|---|---|
| T1 | 接通网关（`--check` 通过） | F1 前置 |
| T2 | 审提示词（`--show-prompt`），填术语表 | F2 |
| T3 | 试跑 3 篇，人工看质量，调到满意 | F1/F2 验收 |
| T4 | 跑全量 1010 篇 | F1 验收 |
| T5 | 生成 `review.md`，交给德语审校人 | F3 验收 |
| T6 | 更新计划文档、勾选、提交 | 工作协议 |

### ❌ 不要做

- **不要重写 `translate.py` 的架构。** 它已经过一轮代码审查（CR-01/02/09/10），
  修掉的问题都有回归测试钉着。看着可以简化的地方，先看第 6 节。
- **不要动抓取侧的任何东西**（见下）。
- **不要为了"更好"去改提示词的既定决策**（`du`/`neutral`/金额规则）——
  那些是用户拍板的品牌决策，不是可优化项。要改先问用户。
- **不要引入新依赖。** `anthropic` SDK 已在 `requirements.txt` 里。

---

## 2. ⚠️ 文件所有权（两个 Agent 并行，这条是硬约束）

另一个 Agent 正在修 Instagram 增量抓取，会改 `routes/`、`core/` 下的文件。
**你们在同一个工作目录、同一个 git 分支上。**

### 你可以改

```
translate.py                 你的主战场
prompts/translate_de.md      提示词本体（可直接编辑，不用动 Python）
config.toml                  **只改 [translate] 段和 [translate.glossary]**
tests/tests_translate.py     你的测试
docs/TRANSLATION_PLAN.md     本文件（记录进展）
docs/IMPLEMENTATION_PLAN.md  **只改 F1/F2/F3 三项**和附录 D 里你自己的记录
docs/MANUAL_STEPS.md         只改第 6–7 步（翻译相关）
.env                         你的密钥（已 gitignore）
archive/*/translated.jsonl   你的产出
archive/*/review.md          你的产出
```

### ❌ 绝对不要碰

```
routes/          delta.py / backfill.py / fb_graph.py —— 另一个 Agent 在改
core/            parse.py / capture.py / chrome.py / store.py / integrity.py
tools/           replay.py / layout.py / schedule.py / setup.py / start_chrome.py
scripts/*.bat    纯 ASCII + CRLF 约定，改坏了 cmd 会把行劈开当命令执行
config.toml      的 [delta] / [integrity] / [chrome] / [targets] 段
```

### git 纪律

1. **不要 `git checkout` 切分支**，会把另一个 Agent 的工作区带走。
   在当前分支上干活。开工前先 `git branch --show-current` 记下来。
2. **不要 `git add -A` 或 `git add .`**。只加你自己的文件：
   ```bash
   git add translate.py prompts/translate_de.md config.toml tests/tests_translate.py docs/
   ```
3. 提交信息用 Conventional Commits（`feat:` / `fix:` / `docs:`）。
4. 遇到不是你改的文件出现在 `git status` 里，**不要碰它，也不要 stash**。

---

## 3. 你要翻译的数据长什么样

抓取已经完成。产物在 `archive/`（**已 gitignore，只在本机**）：

| 归档目录 | 帖子数 | 有正文（＝待译） | 时间跨度 |
|---|---:|---:|---|
| `archive/in_neakasa.tech/` | 1019 | **1010** | 2020-09 ~ 2026-08 |
| `archive/fa_neakasaofficial/` | 46 | **45** | 2026-06 ~ 2026-08 |

**合计约 1055 篇待译。**

### 单篇长什么样

```
archive/in_neakasa.tech/posts/2026-08-27_0912_<post_id>/
    post.json      ← 真相源：post_id / owner / coauthors / created_at /
                     permalink / text / media[] / media_complete
    text.txt       ← 英文原文（派生，给人看）
    text_de.txt    ← 德语译文（**翻译之后由 --review 生成**）
    01.jpg 02.jpg  ← 配图
    media_de/      ← 设计同事回填德文版图的地方（本期人工处理）
```

`translate.py` 读的是账号级的 `manifest.jsonl`（`Archive.rows()`），
不是逐个文件夹扫。索引是派生的，与 `post.json` 一致。

### ⚠️ 关于 263 篇「合作帖」（必读，涉及法务）

Instagram 有**合作帖**：由一方发布、双方主页同时显示。这 1019 篇里有
**263 篇的原作者不是 `neakasa.tech`**（`post.json` 里 `owner` 字段是别人，
`coauthors` 里含 `neakasa.tech`）。拆开看：

- 34 篇原作者是自家品牌账号（`neakasa.global` 30、`neakasa.de` 4）
- **229 篇原作者是第三方创作者**（190 个宠物 UGC 账号）

> **用户 2026-08-30 明确拍板：全部进翻译与发布流水线。**
> 提示过"第三方内容的著作权在创作者手里，二次发布到 DE Page 有授权问题"，
> 用户确认合作协议已覆盖。**这是用户的决定，不要再重新讨论、不要自作主张过滤。**

`review.md` 会在每篇合作帖上标一行「原作者是 @xxx，发布前确认二次使用授权」，
这个提示**保留**，但它是给审校人看的信息，不是让你去劝阻的理由。

### 内容特征（影响你怎么审质量）

- 品牌：**Neakasa**，宠物用品（猫砂盆、宠物吹风机、除毛器等消费品）
- 文案是**美国站英文**，社媒口吻，带 emoji、话题标签、偶尔有价格
- ⚠️ **FB 有 1 篇（2026-08-24 那篇 IFA 展会）正文里混着德语**
  （英文正文 + 一句德语 P.S.）。提示词是按"英译德"写的，
  遇到已经是德语的段落会怎么处理**没有验证过**。只有 1 篇，试跑时留意它。

---

## 4. 现有实现：它已经能做什么

### 命令（都在项目根目录，venv 已激活；或双击 `scripts\run_translate.bat`）

```bash
.venv\Scripts\python.exe translate.py --check          # 网关自检，一次十几 token
.venv\Scripts\python.exe translate.py --show-prompt    # 打印渲染后的 system prompt，不调 API
.venv\Scripts\python.exe translate.py --dry-run        # 列出待译清单，不调 API
.venv\Scripts\python.exe translate.py --limit 3        # 试跑 3 篇
.venv\Scripts\python.exe translate.py                  # 翻全部未翻译的
.venv\Scripts\python.exe translate.py --force          # 重译已翻译过的
.venv\Scripts\python.exe translate.py --account in_neakasa.tech   # 只翻一个账号
.venv\Scripts\python.exe translate.py --review         # 生成 review.md + text_de.txt
```

### 已经实现并有测试保护的行为

| 能力 | 说明 |
|---|---|
| **幂等** | 已译的自动跳过。中途失败直接重跑补齐，不会重复花钱 |
| **单条失败不中断整批** | 失败的带 `post_id` 打出来，继续下一篇 |
| **`manifest.jsonl` 字节级不变** | 抓取产物不可变。译文写独立的 `translated.jsonl` |
| **金额强制保留** | 见下，这是本项目最重要的一道机器检查 |
| **截断/拒绝显式报错** | `stop_reason == max_tokens` 抛错，不返回半句德语 |
| **占位符校验** | 提示词模板里未知的 `{{XXX}}` 直接报错，不会原样发给模型 |
| **`--limit` 是全局额度** | 不是"每个账号 N 篇"（CR-09 修过） |
| **提示词每账号只构建一次** | 保证语域基准一致，也让缓存能命中 |
| **围栏剥离** | 模型若返回 ```` ```de ```` 包裹，会被剥掉（四种写法都有测试） |

### 产出文件

| 文件 | 是什么 |
|---|---|
| `archive/<账号>/translated.jsonl` | **译文的唯一真相源**。每行 `{post_id, text_de, translated_at, model, prompt_version}` |
| `archive/<账号>/posts/<帖>/text_de.txt` | 派生副本，`--review` 时同步，删了跑一次 `--review` 就回来 |
| `archive/<账号>/review.md` | 人工审校清单，与 `posts/` 同级，图片用相对路径引用 |

> ⚠️ **`translated.jsonl` 是真相源，不是 `text_de.txt`。**
> 这是刻意的：重跑抓取不得冲掉花钱买来的译文。别把方向反过来。

---

## 5. 🔴 三条不许碰的红线

### 5.1 金额一律原样保留

**源文案是美国站的，价格是美元。德国站卖多少钱是商务决策，模型无从知道。**

| 原文 | ✅ 必须是 | ❌ 全是错的 |
|---|---|---|
| `$49.99` | `$49.99` | `49,99 €`（换币种）、`49,99 $`（写法与符号位置全变）、`$49,99`（改了小数点） |

这条**已经做成机器检查**（`money_preserved()`）：逐 token 精确多重集比对，
只忽略空白。违规会在三处 surface：翻译时打 `❗金额被改动` 并点名、
跑完打汇总、`review.md` 里加警示块和必查勾选框。

**看到 ❗ 的处理顺序**：先 `--force` 重译一次（模型偶发不听话）；
仍然出现再考虑加强提示词。**不要去放宽这个检查**——
金额被悄悄换算是本项目最贵的一类错误：格式看着完全正确，
人工审几十篇时极易滑过去，而错的是价格。

⚠️ 提示词的第 4 节「德语排版」里**不得**再出现"货币符号写成 `19,99 €`"
这类规则——它与第 3 节的"金额原样复制"直接打架，模型可以"合规地"改错。
这个矛盾 2026-08-29 已经修掉，`tests_translate.py` 里有断言防它回来。

### 5.2 三项品牌语域决策是用户拍板的，不是默认值

`config.toml` 的 `[translate]`：

| 配置 | 值 | 谁定的 |
|---|---|---|
| `address_form` | `du` | **用户 2026-08-29 确认**（消费品牌在德语社媒的主流） |
| `gender_style` | `neutral` | **用户 2026-08-29 确认**（改写避开人称名词，不站队） |
| `anglicism_policy` | `moderate` | 已通用的英语词保留，其余译出 |

**要改需要用户重新确认，且改完必须 `--force` 重译全量**——
否则新旧两种语域会混在同一个 feed 里。

### 5.3 密钥不进 `config.toml`

`config.toml` **进版本库**。密钥走项目根目录的 `.env`（已 gitignore）
或环境变量。`--check` 的输出已做打码，可以放心贴给用户看。

---

## 6. 已经踩过的坑（别重踩）

这些都是真实发生过的，写在这里省你一轮排查。

| # | 坑 | 结论 |
|---|---|---|
| 1 | `base_url` 填成 `https://xxx/v1/messages` | **要填到 `/v1` 的上一级**，SDK 自己拼。症状是 404 |
| 2 | Bearer 模式同时传 `api_key` | 会发出两个鉴权头，严格网关拒绝。CR-01 已修，别改回去 |
| 3 | `temperature` | **Opus 5 / Sonnet 5 已移除该参数，发了直接 400**。默认注释掉的 |
| 4 | `effort` / `thinking` | 较新参数，兼容网关未必认。默认不发；官方端点上不发 thinking 即自适应思考 |
| 5 | `cache_control` 当成顶层参数发 | **2026-08-30 刚修**。它是**内容块上的字段**。见下 |
| 6 | 提示词里举了 Sie 形式的"正确示范" | 在 `du` 配置下会直接干扰模型。**示范性内容也必须跟着配置走** |
| 7 | 按每篇重算风格示例 | 导致 system prompt 篇篇不同，缓存全失效 + 语域漂移。已改为每账号一次 |
| 8 | 输出重定向后崩在 `UnicodeEncodeError` | 本机代码页 936，`⚠ ❗ ß` 编不出来。入口已调 `force_utf8()`，`.bat` 已设 `PYTHONIOENCODING` |
| 9 | ```` ```de ```` 与 ```` ```deutsch ```` 的剥离顺序 | 短的排前面会只切 5 字符，译文变成 `utsch\n...`。已修，有测试 |

### 关于第 5 条（提示词缓存）——你多半会用到

`prompt_cache` 默认 `false`。**1010 篇 × 约 2400 token 的 system prompt
是纯重复输入**，开缓存省的是大头。正确形态已经修好了：

```python
kw["system"] = [{"type": "text", "text": system,
                 "cache_control": {"type": "ephemeral"}}]
```

**打开之前先用 `--limit 3` 验证网关认这个形态**，不认就关掉——
兼容网关支持 `cache_control` 的比例并不高。`tests_translate.py` 里
有两条断言钉住请求形态，别把它们删了。

---

## 7. 分阶段任务清单

> 每完成一项：在 `docs/IMPLEMENTATION_PLAN.md` 对应项下追加
> `> 完成：<日期> · <实际做法与偏差>`，**验收没过不许勾 `[x]`**。

### T1 · 接通网关

**需要用户提供四样**（`MANUAL_STEPS.md` 第 6 步已经写好了问法，可直接转给用户）：

1. **网关地址** —— 填到 `/v1` 的上一级
2. **模型名** —— 以网关暴露的名字为准，不一定是 Anthropic 官方 ID
3. **鉴权头风格** —— `x-api-key` 还是 `Authorization: Bearer`
4. **额外请求头** —— 有些网关要租户 ID / 路由标识

填进 `config.toml` 的 `[translate]`，密钥放 `.env`，然后：

```bash
.venv\Scripts\python.exe translate.py --check
```

- 【验收】打印 `[ok] 网关连通。模型回了：'OK'` 并报出用量
- 失败时程序会把 401/403/404/400 分别解释清楚，照着改即可
- 【记录】写下最终的 `base_url` 形态、模型名、鉴权风格（**不要写密钥**）

⚠️ 拿不到网关信息时**不要伪造、不要改用别的 provider**。停下来告诉用户缺什么。

### T2 · 审提示词 + 填术语表

```bash
.venv\Scripts\python.exe translate.py --show-prompt
```

打印渲染后的完整 system prompt（约 4800 字符）。提示词本体是
**`prompts/translate_de.md`，一个可直接编辑的 Markdown 文件，改它不用动 Python**。

**术语表现在是空的，这一项是 T2 的主要工作量。**
`config.toml` 末尾的 `[translate.glossary]`：

```toml
[translate.glossary]
"litter box"    = "Katzenklo"
"self-cleaning" = "selbstreinigend"
"free shipping" = "kostenloser Versand"
```

**没有它，第 1 篇写 `Kapuzenpullover`、第 12 篇写 `Hoodie`。**
做法：把 1010 篇英文原文过一遍词频，把反复出现的**产品型号、品类词、
卖点词**挑出来定死译法。产品型号（如 `Neakasa M1`）应当**原样保留不译**。

⚠️ TOML 语法：`[translate.glossary]` 必须放在 `[translate]` 段的**最后**，
它下面的键都算 glossary 的。别插到中间去。

- 【验收】`--show-prompt` 输出里能看到术语表条目和三项语域决策的具体指令
- 【建议】把 `--show-prompt` 的输出发给懂德语的同事过一遍。
  花几分钟看提示词，比事后审几十篇译文便宜得多

### T3 · 试跑并调优（F1/F2 的验收）

```bash
.venv\Scripts\python.exe translate.py --dry-run     # 先确认待译篇数是 1055 左右
.venv\Scripts\python.exe translate.py --limit 3
```

打开 `archive/in_neakasa.tech/translated.jsonl` 看这 3 条。**重点检查七件事**：

1. **称呼形式全篇一致**（`du`），**尤其是 CTA 那一句**——最容易漏
2. 德语是否通顺、有没有英语句法直译的痕迹（Denglisch）
3. 语气与英文原文一致（没有变得更浮夸、没有多出感叹号）
4. **换行和空行结构与原文逐行对齐**（下游要直接贴进 Business Suite）
5. **话题标签不超过 3 个**（Meta 对冗长文案与标签堆砌有分发降权）
6. 排版德式：`19,99`（逗号小数）、`20 %`（有空格）、`24.12.2026`、`„引号“`
7. **金额原样保留**（程序会自动查，看有没有 ❗）

不满意的两个调节位：**小调**改 `config.toml` 的 `tone` 那一行；
**大调**改 `prompts/translate_de.md`。改完：

```bash
.venv\Scripts\python.exe translate.py --limit 3 --force
```

- 【验收 F1】产出结构正确的 `translated.jsonl`（3 条，字段齐全）
- 【验收 F2】**人工检查 3 篇输出，德语通顺且风格与原文一致**
  —— 这条需要懂德语的人看，你自己判断不了就明确说出来，别替用户下结论
- ⚠️ 改了 `prompts/translate_de.md` 之后，**把 `translate.py` 顶部的
  `PROMPT_VERSION` 加 1**。译文行里记着这个版本号，
  将来才分得清哪批译文是哪版提示词产出的

### T4 · 跑全量

```bash
.venv\Scripts\python.exe translate.py
```

约 1055 篇。注意事项：

- **先估成本**：`--dry-run` 给出篇数，乘以单篇 token 量（system ≈ 2400 +
  正文 ≈ 100–400 输入，输出 ≈ 100–400）。**跑之前把估算告诉用户**
- 考虑打开 `prompt_cache`（见第 6 节第 5 条），但**先 `--limit 3` 验证网关认**
- `request_gap_seconds` 默认 1.0 秒，避免打爆内部网关。跑 1055 篇约 20–40 分钟
- 中途失败可以直接重跑补齐，已译的自动跳过，不会重复花钱
- 跑完看汇总行里的 **❗金额被改动** 篇数，不为 0 就按第 5.1 节处理

- 【验收】`translated.jsonl` 行数 ≈ 待译篇数；失败的都带 `post_id` 打出来了

### T5 · 生成审校清单（F3 的验收）

```bash
.venv\Scripts\python.exe translate.py --review
```

产出 `archive/<账号>/review.md`，并把译文同步一份 `text_de.txt` 到每帖文件夹。

- 【验收 F3】**在 VS Code 里打开 `review.md`，按 `Ctrl+Shift+V` 预览，
  配图能正常显示**（图片用相对路径，`review.md` 与 `posts/` 同级）
- 清单里每篇含：原文、译文、配图、原帖链接、勾选框，
  合作帖额外一行原作者与授权提示，含金额/尺码的额外一块警示

⚠️ **一个已知的单向缺口**：审校人在 `review.md` 里改的译文**不会回写**
`translated.jsonl`。当前定位是"给人看的清单"而非"可编辑的数据源"。
若发布环节要吃审校后的结果，**需要再加一个回写命令**——
这一项**先问用户要不要做**，不要自作主张实现。

### T6 · 收尾

1. `docs/IMPLEMENTATION_PLAN.md` 里 F1/F2/F3 按实际情况勾选 + 写完成行
2. 附录 D 追加一条同步记录：**实际做法、与本计划的偏差、真实的成本数字**
3. `docs/MANUAL_STEPS.md` 第 6–7 步按实际流程校正
4. 本文件末尾追加「实施记录」
5. 跑一遍全部离线测试，确认仍全绿（见第 8 节）
6. 按第 2 节的 git 纪律提交

---

## 8. 每次改完都要过的关

```bash
# 全部离线测试（当前基线：12 套 582 项，全绿）
.venv\Scripts\python.exe tests/tests_translate.py

# 或者全跑（新增测试会被 glob 自动纳入）
for %f in (tests\tests_*.py) do .venv\Scripts\python.exe %f
```

⚠️ **有一条容易忽略的验证条件**：全套测试在 **stdout 被管道重定向**的
情况下也必须全绿。本机代码页是 936，修复前 `tests_translate.py` 在这个条件下
必崩（`UnicodeEncodeError: 'gbk' codec can't encode character '\xdf'`）。
双击 `.bat` 时永远看不到这个故障，只在写日志、计划任务这些场合发作。

```bash
.venv\Scripts\python.exe tests/tests_translate.py > nul 2>&1 && echo OK
```

另外：`python -W error::SyntaxWarning -m compileall -q translate.py` 应通过。

---

## 9. 需要用户配合的事（早点提，别憋到最后）

| 什么时候 | 需要用户做什么 |
|---|---|
| **T1 之前** | 提供网关地址 / 模型名 / 鉴权风格 / 额外头，并把密钥放进 `.env` |
| **T2** | 最好让懂德语的同事看一遍 `--show-prompt` 的输出 |
| **T3** | **必须有人懂德语来判断译文质量**——这是 F2 验收的硬要求 |
| **T4 之前** | 把成本估算告诉用户，让他确认再跑全量 |
| **T5 之后** | 把 `review.md` 交给德语审校人；确认要不要做"审校结果回写"功能 |

⚠️ **不要因为用户暂时给不了就跳过或伪造验收。**
本项目的工作协议明写：验收要真实数据而暂时拿不到的，
写 `> 进行中：...` 并说明缺什么，**不勾选**。

---

## 10. 汇报时请包含

- `--check` 的完整输出（密钥已自动打码，可直接贴）
- 试跑 3 篇的原文/译文对照
- 全量跑完的统计：成功 / 失败 / ❗金额被改动 / ⚠需人工确认 各多少篇
- **实际 token 用量与成本**（这是下次估算的唯一依据）
- 与本计划的所有偏差

---

## 附录 A · `[translate]` 全部配置项速查

| 键 | 默认 | 说明 |
|---|---|---|
| `base_url` | `""` | 空则走官方 `api.anthropic.com`。填到 `/v1` 上一级 |
| `api_key_env` | `ANTHROPIC_API_KEY` | **环境变量的名字**，不是密钥本身 |
| `auth_style` | `x-api-key` | 或 `bearer` |
| `model` | `claude-opus-5` | 以网关暴露的名字为准 |
| `extra_headers` | `{}` | 网关要租户 ID / 路由标识时填 |
| `max_tokens` | 4096 | 截断会显式报错而非静默 |
| `timeout_seconds` | 120 | |
| `max_retries` | 2 | SDK 自带指数退避 |
| `request_gap_seconds` | 1.0 | 相邻调用最小间隔 |
| `effort` | 注释掉 | 较新参数，兼容性未知 |
| `temperature` | 注释掉 | **Opus 5 / Sonnet 5 发了会 400** |
| `prompt_cache` | `false` | 见第 6 节第 5 条 |
| `style_examples` | 6 | 取长度中位数附近的英文原文做语气参照 |
| `tone` | 一行中文 | 营销同事可直接改 |
| `address_form` | `du` | **用户拍板** |
| `gender_style` | `neutral` | **用户拍板** |
| `anglicism_policy` | `moderate` | |
| `[translate.glossary]` | 空 | **T2 要填**。必须放在 `[translate]` 最后 |

## 附录 B · 风格示例的定位（容易理解错）

`style_examples` 取的是**英文原文单语示例**，不是英德翻译对照——
归档里本来就没有德语对照。它们在提示词里的定位是
**"这个品牌平时怎么说话"，不是"这句该怎么译"**，
提示词里显式写了「它们是语气参照，不是翻译对照，不要去翻译它们」。

选篇规则：按长度排序取**中位数附近**。最短的往往是 "New drop 🔥" 这种
没信息量的，最长的会把模型带向啰嗦，两头都不代表品牌常态口吻。

## 附录 C · 这个项目的两条通用纪律

1. **偏差必须记录，不要默默改代码。** 计划与现实冲突时以现实为准并写进文档。
   后续任务依赖这些事实。
2. **"离线测试全绿"和"能处理真实数据"是两件事。** 这个项目已经因此翻车三次
   （解析器跨账号污染、轮播子项、Instagram 合作帖）。
   三次的共同点都是：**测试构造的输入里没有那个字段**。
   你新增的断言，尽量用真实数据构造。

---

## 实施记录

> 实施 Agent 在这里追加。格式：`### <日期> · <做了什么>`

（待填）
