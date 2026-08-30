# 人工操作指南

本文件**只讲需要你亲自动手的步骤**，自动化部分不在这里。
进度真相源仍是 [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md)，本文件是它的操作手册。

按顺序做即可。每一步都写了：**怎么做 → 成功长什么样 → 不对时怎么办**。

下文的“项目根目录”指包含 `README.md`、`config.toml`、`core/` 的目录；当前目录名
是 `FacebookScraper`。除非另有说明，命令都从这个目录运行，避免依赖某台机器上的
固定绝对路径。

---

## 当前进度

| 步骤 | 内容 | 状态 |
|---|---|---|
| A1 / A2 | 环境搭建、离线测试基线 | ✅ 已完成（无需你操作） |
| C1 / D1 / D2 | 登出客户端、完整性检查、通知 | ✅ 代码完成 |
| F1 / F2 / F3 | 翻译流水线 | ✅ 代码完成，**等你配 API + 等 B 组数据** |
| **第 1–3 步** | **起 Chrome、登录小号、填账号** | ⬅️ **现在做这个** |
| 第 4–5 步 | 人工滚动回填、抽查媒体 | 等第 3 步 |
| 第 6–7 步 | 配翻译 API、试跑与审校 | 第 6 步可与第 1–5 步并行 |
| G 组 | Business Suite 发布 | 还没到 |

> 第 6 步（配翻译 API）**不依赖前面任何一步**，你随时可以先做。

---

## 第 0 步 · 确认一条通知（30 秒）

我在验收 D2 时往你桌面发过一条测试通知，标题「测试」，正文「这是一条测试通知」。

**请回想或确认你是否看到了。** 程序侧显示发送成功，但如果你开着
「专注助手 / 请勿打扰」，通知会被系统吞掉而程序侧无感——
那样的话每天的增量告警你也收不到，得改用别的方式提醒。

看不到就告诉我，我把告警改成更醒目的形式。
不确定的话，可以在项目根目录打开命令提示符，自己发一条试试：

```bash
.venv\Scripts\python.exe -m core.notify
```

---

## 第 1 步 · 起专用 Chrome（对应 A3）

### 怎么做

在项目根目录里**双击 `scripts\start_chrome.bat`**。

### 成功长什么样

黑窗口里依次出现：

```
使用 Chrome: C:\Program Files\Google\Chrome\Application\chrome.exe
专用 profile: C:\Users\marcus_ao\.fbscraper-chrome
调试端口:     9222

等待调试端口就绪....
[ok] 调试端口 9222 已就绪。
```

同时弹出一个**全新的、什么都没登录的 Chrome 窗口**。

### 几件要知道的事

- **你日常用的 Chrome 可以照常开着，不冲突。** 这个脚本用的是独立的
  profile 目录（`%USERPROFILE%\.fbscraper-chrome`），Chrome 的「同一实例」
  限制是按 profile 目录算的，两者互不干扰。
- **这个新窗口在整个抓取过程中要一直开着。** 关了就得重来。
- 端口号想改的话只改 `config.toml` 的 `[chrome].debug_port` 一处即可，
  脚本会跟着走（旧版本还要同步改 .bat 顶部，现在不用了）。

### 不对时怎么办

| 现象 | 原因 | 处理 |
|---|---|---|
| `[i] 端口 9222 已在监听` | 专用 Chrome 已经在跑了 | 正常，不用重复启动，直接下一步 |
| 等了 15 秒仍未监听 | 该 profile 目录被残留进程占着 | 任务管理器 → 详细信息 → 找 `chrome.exe`，结束掉**用这个 profile 的**那些，再重跑 |
| `[!] .venv not found` | 环境没建 | 先双击 `scripts\setup.bat` |
| 提示找不到 Chrome | 装在非标准路径 | 把 `chrome.exe` 完整路径填进 `config.toml` 的 `[chrome].exe` |

---

## 第 2 步 · 登录抓取小号（对应 A4）

### 怎么做

1. 在**第 1 步打开的那个 Chrome 窗口**里，访问 `https://www.facebook.com/`
2. 用**抓取专用小号**登录，走完二次验证
3. 同一窗口再访问 `https://www.instagram.com/`，同样登录小号
4. **验证会话持久化**：完全关掉这个 Chrome 窗口 → 重新双击 `scripts\start_chrome.bat`
   → 打开 facebook.com，确认**仍是登录状态**

### 成功长什么样

重开之后不需要再输密码，直接就是登录态。说明 profile 目录生效了。

### ⚠️ 三条必须遵守的

1. **绝对不要用主账号或持有 DE 资产的账号。** 抓取路径存在被判定异常的可能，
   被封的必须是损失可控的小号。
2. **不要在这个窗口里做日常浏览。** 它的浏览器指纹应该只和抓取行为绑定。
3. **登录只做这一次，只在这里做。** 全项目只允许这一条登录路径——
   代码里没有任何自动登录，也不要加。多一条路径就多一份被 checkpoint 拦的机会。

### 不对时怎么办

- **重开后又要求登录** → profile 目录没写成功。确认
  `C:\Users\marcus_ao\.fbscraper-chrome` 存在且里面有文件；
  若目录是空的，检查该路径是否被安全软件拦了写入。
- **登录时被要求验证身份 / 出现 checkpoint** → 换个时间再试，不要连续重试。
  连续失败的登录尝试本身就是风险信号。

---

## 第 3 步 · 填写抓取目标（对应 A5）

### 怎么做

用 VS Code 打开 `config.toml`，找到 `[targets]`：

```toml
[targets]
facebook = "your-us-page"          # facebook.com/<这里>
instagram = "your_us_handle"       # instagram.com/<这里>
```

把引号里的值换成真实的。**只填 URL 里账号名那一段**，不要带
`https://`、不要带斜杠、不要带 `@`。

举例：

| 页面地址 | 该填什么 |
|---|---|
| `https://www.facebook.com/AcmeStoreUS` | `AcmeStoreUS` |
| `https://www.facebook.com/AcmeStoreUS/` | `AcmeStoreUS` |
| `https://www.instagram.com/acme.us/` | `acme.us` |
| `@acme_us`（IG 显示名） | `acme_us` |

### 验证

浏览器里打开这两个地址，确认都能打到正确页面：

```
https://www.facebook.com/<你填的 facebook 值>
https://www.instagram.com/<你填的 instagram 值>
```

### ⚠️ 一个可能的坑

如果这个 FB 主页**没有自定义用户名**，地址会长这样：

```
https://www.facebook.com/profile.php?id=100012345678901
```

这种情况**现在的代码拼不出正确 URL**（它只会拼 `facebook.com/<值>`）。
遇到了就告诉我，我改一下 URL 构造逻辑——别自己往 `[targets]` 里塞
`profile.php?id=...`，那样拼出来是坏地址。

---

## 第 4 步 · 人工滚动回填（对应 B1 / B3）

这是整个项目里**最耗你时间的一步**，两个平台各约 20 分钟。

### 为什么要你手滚

因为滚动的确实是人，没有任何可识别的自动化行为特征。
脚本全程不驱动页面，只在旁边把浏览器自己发出的接口响应捞下来。
这是拿 20 分钟换掉一整类封号风险，值得。

### 怎么做

**前置**：第 1 步的 Chrome 窗口开着，小号已登录。

1. 双击 **`scripts\run_backfill.bat`**，参数给 `facebook`
   （或在终端里 `scripts\run_backfill.bat facebook`）
2. 脚本会在那个 Chrome 里打开目标主页，终端出现：

   ```
   已打开 https://www.facebook.com/xxx
   ==============================================================
     现在请在那个 Chrome 窗口里手工向下滚动，直到看见最早的帖子。
     慢慢滚，让每屏内容都加载出来（看到图片显示出来再继续）。
     滚完之后回到这里按 Enter。
   ==============================================================
     已捕获 12 个响应 / 34 段 JSON
   ```

3. **切到 Chrome 窗口，开始往下滚。** 要点：
   - **慢滚。** 每屏停一下，等图片真的显示出来再继续。图片没加载 = 那批数据没请求 = 没捞到。
   - **用鼠标滚轮或方向键，不要按 End 键。** 一次跳到底会跳过中间的加载。
   - 时不时瞄一眼终端里的计数器，数字在涨说明在捞到东西。
   - 一直滚到**看见最早的一条帖子**（页面不再加载新内容）为止。
4. 滚完切回终端，**按 Enter**。
5. 终端会打印转储路径和解析结果。
6. Instagram 重复一遍：`scripts\run_backfill.bat instagram`。

### 成功长什么样

```
原始响应已转储 → D:\...\archive\fa_xxx\_capture_1756...json  (2048 KB)
解析出 37 篇帖子
  + 1234567890  3图/0视频  Summer sale starts now
  ...
新增 37 篇
```

### 请记录下来告诉我

- 捕获了多少个响应 / 多少段 JSON
- 解析出多少篇
- **你目测这个账号大概有多少篇帖子**（用来判断有没有漏抓）

### ⚠️ 解析出 0 篇怎么办

**不要重滚。** 这是预料之内的情况——解析器是按已知响应结构写的，
从没和真实响应比对过，B 组任务就是干这个的。

脚本在解析之前**已经无条件把原始响应转储**到
`archive/<账号>/_capture_<时间戳>.json`，你 20 分钟的成果一点没丢。
把这个文件告诉我，我按真实结构改解析器，然后离线重放，不用你再滚一次。

### 其它情况

| 现象 | 处理 |
|---|---|
| `调试端口 9222 没开` | 回第 1 步，Chrome 没起或被关了 |
| 滚到一半计数器不涨了 | 可能触发了限流。停下按 Enter 保住已有数据，隔几小时再补 |
| 超过 30 分钟自动收尾 | 正常保护机制。已捞到的会正常落盘；需要更久就调 `config.toml` 的 `[backfill].max_session_seconds` |
| 媒体下载报 403 | URL 签名过期。告诉我，需要调整下载时机 |

---

## 第 5 步 · 抽查媒体（对应 B5）

### 怎么做

打开 `archive\<账号>\media\` 目录：

1. **切换到「详细信息」视图，按大小排序** —— 确认**没有 0 字节的文件**
2. **随机打开 3 张图**，确认能正常显示、不是坏图
3. 右键 → 属性 → 详细信息，看分辨率

### 请记录下来告诉我

- 有没有 0 字节文件
- 图片的典型分辨率是多少（例如普遍 1080×1080 还是 640×640）

分辨率普遍低于 1000px 的话，下游图像处理的输入质量会受限——
这是爬取路径的固有代价（拿不到原始上传文件），但需要提前让设计同事知道。

---

## 第 6 步 · 配置翻译 API（F 组前置，可随时做）

### 6.1 先向网关管理员要这四样

翻译走**公司内部兼容 Anthropic Messages 的第三方端点**。你需要问到：

| 要问的 | 说明 | 举例 |
|---|---|---|
| **网关地址** | 填到 `/v1` 的**上一级**。SDK 自己会拼 `/v1/messages` | `https://llm.corp.example.com` |
| **模型名** | 以**网关暴露的名字**为准，不一定是 Anthropic 官方 ID | `claude-opus-5` 或 `corp-claude-opus` |
| **鉴权头风格** | `x-api-key`（Anthropic 官方风格）还是 `Authorization: Bearer` | 不确定就先试 `x-api-key` |
| **额外请求头** | 有些网关要租户 ID / 路由标识 | `X-Corp-Route: translation` |

> 如果对方给的地址已经**带 `/v1/messages`**，比如
> `https://llm.corp.example.com/v1/messages`，
> `base_url` 请填 `https://llm.corp.example.com`（去掉 `/v1/messages`）。
> 这是最常见的配置错误，症状是 404。

### 6.2 填 config.toml

打开 `config.toml`，找到 `[translate]`：

```toml
base_url = "https://llm.corp.example.com"   # ← 填网关地址
api_key_env = "ANTHROPIC_API_KEY"           # ← 通常不用改
auth_style = "x-api-key"                    # ← 或改成 "bearer"
model = "claude-opus-5"                     # ← 填网关的模型名
extra_headers = {}                          # ← 网关要额外头就填这里
```

`extra_headers` 有内容时写成这样：

```toml
extra_headers = { "X-Corp-Route" = "translation", "X-Tenant" = "marketing" }
```

### 6.3 放 API 密钥（⚠️ 不要发给我）

**推荐做法**：在项目根目录下新建一个名为
**`.env`** 的文件，内容一行：

```
ANTHROPIC_API_KEY=你的密钥
```

`.env` 已在 `.gitignore` 里，不会进版本库。

> VS Code 里新建：右键 `FacebookScraper` 文件夹 → 新建文件 → 文件名打 `.env`（含前面的点）。
> 资源管理器新建的话注意别被自动加上 `.txt` 后缀。

**替代做法**：设系统环境变量。

```bash
setx ANTHROPIC_API_KEY "你的密钥"
```

设完**必须重开终端 / 重新双击 .bat** 才生效。这种方式会把密钥写进用户注册表、
对你所有进程可见，所以更推荐上面的 `.env`。

**❌ 三个不要**：不要写进 `config.toml`（那个文件进版本库）；
不要发在聊天里给我；不要提交进 git。

### 6.4 验证

双击 `scripts\run_translate.bat`，参数给 `--check`
（或终端里 `scripts\run_translate.bat --check`）。

**成功长这样**：

```
=== 翻译网关自检 ===
  base_url    : https://llm.corp.example.com
  auth_style  : x-api-key
  密钥环境变量: ANTHROPIC_API_KEY = sk-a...wxyz　长度 108　来源：.env 文件
  model       : claude-opus-5

[ok] 网关连通。模型回了：'OK'
     用量：输入 15 tok / 输出 3 tok
     实际响应的 model 字段：claude-opus-5
```

**失败的话，程序会直接告诉你是哪一类问题**：

| 报错 | 含义 | 怎么改 |
|---|---|---|
| `401 鉴权失败` | 密钥不对，或鉴权风格选反了 | 先把 `auth_style` 在 `x-api-key` / `bearer` 之间换一个试 |
| `403 无权限` | 密钥有效但没这个模型的权限 | 找网关管理员开权限 |
| `404` | 模型名网关不认，**或** `base_url` 多带了 `/v1/messages` | 见 6.1 的提示框 |
| `400` 且提示某参数不认识 | 网关不支持较新参数 | 检查 `config.toml` 里 `effort` / `temperature` 是不是被打开了，注释掉再试 |
| `连不上` | 网络不通 | 确认是否需要公司 VPN / 代理 |

`--check` 只发一次十几个 token 的请求，可以放心反复试。

### 6.5 决定三项品牌语域（⚠️ 这一步比上面几步都重要）

`config.toml` 的 `[translate]` 里有三个选项，**它们决定每一篇德语译文的基调**。
这些是**模型不可能自己知道的品牌决策**——英文的 "you" 里没有这个信息。
不定死的话模型会篇篇自己挑，整个 feed 读起来就是散的。

#### ① `address_form` —— du 还是 Sie

德语营销里最重要的一个决定。

| | `du` | `Sie` |
|---|---|---|
| 语感 | 亲近、平等、年轻 | 尊重、专业、有距离 |
| 典型行业 | 时尚、快消、餐饮、运动、DTC 品牌 | B2B、金融、保险、医疗、高端奢侈品 |
| IG / FB 上的现状 | **消费品牌的主流** | 少数 |
| 选错的后果 | 高端品牌用 du 会显得轻浮 | 年轻品牌用 Sie 会显得老气、有距离感 |

当前默认是 **`du`**（消费品牌在社媒上的主流选择）。

#### ② `gender_style` —— 性别表达

德国市场的真实分歧点，没有"正确答案"，只有品牌立场。

| 取值 | 效果 | 风险 |
|---|---|---|
| `neutral`（当前默认） | 改写句子避开人称名词：`Tausende sind schon dabei` | 几乎没有。不站队，句子有时要绕一点 |
| `colon` | `Kund:innen`、`Mitarbeiter:innen` | 年轻受众认可，但**部分德国受众明确反感**，也有可读性争议 |
| `generic` | 传统阳性泛指 `Kunden` | 最保守，部分受众视为过时 |

`neutral` 是"不站队"的选择，所以设成默认。但如果你们品牌已经有明确立场，改掉它。

#### ③ `anglicism_policy` —— 英语借词

德语营销对英语词的容忍度比多数语言高（`Sale`、`Shop`、`Style`、`Hoodie` 都很自然），
但用过量会显得廉价。默认 `moderate`：已通用的保留，其余译出。

#### ④ 术语表（抓到真实文案后再填）

`config.toml` 末尾的 `[translate.glossary]`：

```toml
[translate.glossary]
"hoodie"        = "Hoodie"
"free shipping" = "kostenloser Versand"
"sold out"      = "ausverkauft"
```

**没有它，第 1 篇写 `Kapuzenpullover`、第 12 篇写 `Hoodie`。** 品牌内容不能这样。
建议第 4 步回填完之后，翻一遍英文原文，把反复出现的产品名和卖点词填进去。

> ⚠️ TOML 语法要求 `[translate.glossary]` 必须放在 `[translate]` 的**最后**，
> 它下面的键都算 glossary 的。别插到中间去。

### 6.6 看一眼实际发给模型的提示词

```
scripts\run_translate.bat --show-prompt
```

会打印**渲染后的完整 system prompt**（约 4800 字符），
包括你上面选的三项决策被翻译成了什么具体指令。

**建议把这份输出发给懂德语的同事过一遍**——花几分钟看提示词，
比事后审几十篇译文便宜得多。

提示词本体在 [prompts/translate_de.md](../prompts/translate_de.md)，
**是一个可以直接编辑的 Markdown 文件，不用改 Python**。
营销同事想加规则，直接在那里加，改完再跑一次 `--show-prompt` 看效果。

> 改了模板之后，记得把 `translate.py` 顶部的 `PROMPT_VERSION` 加 1。
> 译文行里记着这个版本号，将来才分得清哪批译文是哪版提示词产出的。

---

## 第 7 步 · 试跑翻译并审校（对应 F1 / F2 / F3）

> **前置**：第 4 步的回填已完成（要有真实文案），第 6 步的 `--check` 已通过。

### 7.1 先空跑看要翻哪些

```
scripts\run_translate.bat --dry-run
```

只列清单，不调用 API、不花钱。确认待译篇数符合预期。

### 7.2 小批量试跑 3 篇

```
scripts\run_translate.bat --limit 3
```

然后打开 `archive\<账号>\translated.jsonl`，看这 3 条德语译文。
**重点检查七件事**：

1. **称呼形式**全篇一致（du 或 Sie），**尤其是 CTA 那一句**——最容易漏
2. 德语是否通顺、有没有英语句法直译的痕迹（Denglisch）
3. 语气是否和英文原文一致（没有变得更浮夸、没有多出感叹号）
4. **换行和空行结构是否和原文逐行对齐**（下游要直接贴进 Business Suite）
5. **话题标签有没有超过 3 个**
6. 排版是否是德式：`19,99`（逗号小数）、`20 %`（有空格）、`24.12.2026`、`„引号"`
7. **金额有没有被擅自改动** —— 见下面的专门说明

### ⚠️ 关于美元金额：一律原样保留

**规则：原文的美元金额逐字符原样搬进德语译文，一个字符都不许改。**

源文案是美国站的，价格是美元。德国站卖多少钱是**商务决策**，模型无从知道。
把 `$49.99` 写成 `49,99 €` 不是翻译，是擅自改价——而且这种错**格式看着完全正确**，
人工审校时极易滑过去，是本项目里最贵的一类错误。

下面四种全部算违规，不只是换币种那一种：

| 原文 | ✅ 必须是 | ❌ 全是错的 |
|---|---|---|
| `$49.99` | `$49.99` | `49,99 €`（换了币种）<br>`49,99 $`（币种没换，但写法和符号位置全变）<br>`$49,99`（只改了小数点）<br>`44,99 €`（连数值都改了） |
| `$50` | `$50` | `50 $` / `50 €` |

**你不需要靠肉眼去抓这个** —— 程序会逐条比对原文与译文里的每一处金额：

- 翻译时终端打 `❗金额被改动`，并点名具体是哪个金额
- 跑完打一条汇总：`❗ N 篇的金额没有原样保留，模型没听提示词`
- `review.md` 抬头统计违规篇数，每篇标 ❗ 并多给一个 **必查** 勾选框

看到 ❗ 时的处理顺序：先 `--force` 重译一次（模型偶发不听话）；
仍然出现就告诉我，我加强提示词。

正确流程是：**模型保留原样 → 程序确认没被动过 → 你在审校时替换成德国站定价。**

不满意的话有两个调节位：

- **小调**：改 `config.toml` 的 `[translate].tone` 那一行
- **大调**：改 `prompts/translate_de.md` 提示词本体（可直接编辑，不用动 Python），
  或改 `address_form` / `gender_style` / `anglicism_policy` 三项决策

改完：

```
scripts\run_translate.bat --limit 3 --force
```

`--force` 会重译已翻译过的。调到满意为止再翻全量。

### 7.3 翻全量

```
scripts\run_translate.bat
```

已翻译过的会自动跳过，中途失败可以直接重跑补齐，不会重复花钱。
单条失败不会中断整批，失败的会带 `post_id` 打出来。

### 7.4 生成审校清单

```
scripts\run_translate.bat --review
```

产出 `archive\<账号>\review.md`。**在 VS Code 里打开它，按 `Ctrl+Shift+V` 开预览**——
配图会直接显示出来。

每篇一节，含：英文原文、德语译文、配图、以及勾选框：

```
- [ ] 译文已审校
- [ ] 数字已按德国站确认/替换      ← 只有含金额/尺码的帖子才有这一行
- [ ] 图内含英文文字，需人工替换
```

含金额 / 数字尺码 / 英制单位的帖子，译文下方会有一块警示，写清楚要人工做什么：

```
> ⚠️ 需人工确认的数字
> - 含货币金额 —— 需替换成德国站定价（提示词刻意不换算）
```

**如果模型擅自改动了金额**，警示会更醒目，并点名具体金额：

```
> ❗ 金额没有原样保留——模型没照提示词做，这条要重点看
> - ❗原文金额 $49.99、$50 未在译文里原样出现 —— 译文里凭空出现了 €/EUR，八成是被换算了
```

文件抬头会统计一共有多少篇需要人工处理、其中多少篇金额被动过，便于估工作量。

**直接在这个文件里改译文、勾框**，然后交给德语审校人。
图内英文文字本期走人工处理，勾上的交给设计同事替换。

> ⚠️ 改了 `review.md` 里的译文**不会自动回写** `translated.jsonl`。
> 如果你希望审校结果能回流到发布环节，告诉我，我加一个回写命令。

---

## 还没到的步骤

### G1 · Business Suite 探查（发布环节的前置）

到时候需要你：用**另一个专用 Chrome profile**登录有 DE Page 发布权的账号
（与抓取小号分开，避免指纹关联），手工走一遍完整的定时发帖流程。

这一步之前**不能写任何选择器**——Business Suite 是 React SPA，
class name 是构建期混淆的，凭猜写的选择器一定是错的。

到这一步时我会写详细的探查清单给你。

---

## 附录 A · 命令速查

全部在项目根目录下，双击 `.bat` 即可。

| 命令 | 作用 |
|---|---|
| `scripts\setup.bat` | 建环境、装依赖、跑全部离线测试（只需跑一次） |
| `scripts\start_chrome.bat` | 起专用 Chrome + 调试端口 |
| `scripts\run_backfill.bat facebook` | 回填 Facebook（需人工滚动） |
| `scripts\run_backfill.bat instagram` | 回填 Instagram（需人工滚动） |
| `scripts\run_translate.bat --check` | 验证翻译网关配置 |
| `scripts\run_translate.bat --show-prompt` | 打印实际发给模型的提示词（不调 API） |
| `scripts\run_translate.bat --dry-run` | 列出待译帖子，不调 API |
| `scripts\run_translate.bat --limit 3` | 试跑 3 篇 |
| `scripts\run_translate.bat` | 翻译全部未翻译的 |
| `scripts\run_translate.bat --review` | 生成人工审校清单 |

---

## 附录 B · 产物都在哪

```
FacebookScraper\
  config.toml             全部可调参数（含品牌语域三项决策与术语表）
  scripts\                ← 你要双击的入口都在这里
  docs\                   ← 本文件、进度真相源、会话交接
  prompts\
    translate_de.md       ← 翻译提示词本体。可直接编辑，不用改 Python
  archive\<平台前缀>_<账号>\
    manifest.jsonl        抓取产物。**不要手工改**，重跑抓取会重现
    translated.jsonl      德语译文。独立文件，抓取重跑不会冲掉它
    review.md             人工审校清单（Ctrl+Shift+V 预览）
    media\                配图
    _capture_*.json       原始响应转储，解析出问题时用它离线校准
  state\
    alerts.log            告警记录（含实际走的通知通道）
  .env                    你的 API 密钥（已 gitignore，不会进版本库）
```

---

## 附录 C · 卡住时给我什么

出问题时把下面这些贴给我，能省一轮来回：

- **完整的终端输出**（不要只截最后一行，前面的上下文往往才是原因）
- 解析出 0 篇 → `_capture_*.json` 的**路径和文件大小**
- 翻译网关报错 → `--check` 的**完整输出**（密钥已自动打码，可以直接贴）
- 通知没弹出来 → `state\alerts.log` 的内容

❌ **任何情况下都不要把 API 密钥、账号密码贴给我。**
`--check` 的输出已经做了打码处理，可以放心贴。
