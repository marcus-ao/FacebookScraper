# F 组实施与操作计划 · DeepSeek 德语翻译

> 状态核对：2026-08-30。对应 `IMPLEMENTATION_PLAN.md` 的 F1 / F2 / F3。

## 1. 当前结论

翻译主干已经实现并通过离线回归，直接接入 DeepSeek 官方 OpenAI 兼容接口。
真实归档共有 **1055 条有正文的帖子**：

| 归档 | 帖子 | 有正文、可翻译 |
|---|---:|---:|
| `archive/fa_neakasaofficial/` | 46 | 45 |
| `archive/in_neakasa.tech/` | 1019 | 1010 |

当前机器已有真实 `.env`。旧版 Anthropic 路径曾完成 `--check` 与每账号 3 条试译，
但这 6 条使用的是“关闭 thinking + 标签最多 3 个”的旧契约。当前已按用户决定切换到
官方 OpenAI 接口、High thinking、标签全部照搬，并把 `PROMPT_VERSION` 升至 5；旧结果
会自动判为过期，不能冒充新版验收。新版真实连通、每账号 3 条重试译和德语人工确认
仍待执行；本轮没有擅自发起新的付费请求。

## 2. 已实现的业务流程

`translate.py` 现在提供以下入口：

```powershell
scripts\run_translate.bat --check
scripts\run_translate.bat --show-prompt
scripts\run_translate.bat --estimate
scripts\run_translate.bat --dry-run
scripts\run_translate.bat --account in_neakasa.tech --limit 3
scripts\run_translate.bat
scripts\run_translate.bat --review
```

主干行为：

- 读取各账号 `manifest.jsonl`，只处理非空正文；不修改抓取真相源。
- 译文追加到账号级 `translated.jsonl`，每条同时保存实际送入模型的英文正文 SHA-256；
  只有 `post_id + 正文指纹 + 当前提示词版本` 都一致才算完成，可安全断点续跑。
- 正文被解析修复或人工纠正后，旧译文自动标为过期，普通重跑只补这一篇；
  `--review` 不会把旧德文配给新英文，也会移除帖子目录中的旧 `text_de.txt` 派生副本。
- 已成功项默认跳过；金额校验失败或调用失败的项不记完成，下次直接补跑。
- 标签必须保持相同数量、内容、大小写与顺序；翻译、删减、新增或调序都会拒绝写盘。
- 一个付费批次持有单实例锁，重复双击不会让同一帖子重复付费。
- 鉴权、端点、模型回退等整批错误在第一条即熔断，不会对 1055 条重复失败。
- 保存实际响应模型、提示词版本与可识别的 token usage，终端汇总费用上界。
- `--review` 生成 `review.md`，并把译文派生为各帖目录的 `text_de.txt`；
  再次生成前把旧清单保存为 `review.previous.md`，避免人工批注静默消失。
- 合作帖进入翻译流程，但只用目标账号自己的正文构造风格参照；审校清单标明原作者。
- 外部社媒正文按不可信数据封装进提示词与 Markdown，不能闭合标签或代码围栏。

## 3. DeepSeek 默认契约

`config.toml` 已给出可直接使用的默认值：

```toml
[translate]
provider = "deepseek"
base_url = "https://api.deepseek.com"
api_key_env = "DEEPSEEK_API_KEY"
model = "deepseek-v4-pro"
reasoning_effort = "high"
```

核对日期为 2026-08-30：官方最新高质量文本模型为 `deepseek-v4-pro`，更快、更省的
显式备选是 `deepseek-v4-flash`。请求由 OpenAI SDK 发往 `/chat/completions`，使用
`Authorization: Bearer`；显式发送 `thinking={type="enabled"}` 与
`reasoning_effort="high"`。代码不配置、不发送 `max_tokens`、
`max_completion_tokens`、`temperature` 或 `top_p`，由模型按自身能力完成翻译。

官方参考：

- [Chat Completions API](https://api-docs.deepseek.com/api/create-chat-completion/)
- [Thinking Mode](https://api-docs.deepseek.com/guides/thinking_mode/)
- [上下文缓存](https://api-docs.deepseek.com/guides/kv_cache/)
- [模型与价格](https://api-docs.deepseek.com/quick_start/pricing/)
- [错误码](https://api-docs.deepseek.com/quick_start/error_codes/)

翻译主干不再保留 `custom_anthropic` 分支，避免当前业务同时维护两套协议、鉴权和
响应解析。`provider=deepseek` 会严格校验官方 base URL 与受支持模型。

## 4. 品牌与安全决策

以下不是模型自行选择的默认值，已经固化在配置和提示词里：

- 称呼：`du`。
- 性别表达：`neutral`，优先中性改写。
- 英语借词：`moderate`。
- 语气：简洁直接，不加码情绪。
- 话题标签：原帖有几个就逐个照搬几个，内容、大小写和顺序全部不变，不设数量上限。
- 13 条高频术语按 Neakasa 德国站现有用语统一；上线前仍由德语业务人员确认。
- 金额逐字符原样保留。`$49.99` 不得变成 `49,99 €`、`49,99 $` 或 `$49,99`。
  程序会把任何遗漏、新增、换算或格式改写作为硬失败，拒绝写盘。
- 数字尺码不自动换算；英制物理量可以换算，但会进入人工核对清单。
- 图内英文不在本期自动处理，由审校清单交给设计人员确认。

第三方创作者的 Instagram 合作帖已按用户确认全部进入下游；`owner` 仍保留真实作者，
审校清单继续提示发布前确认二次使用授权。

## 5. 业务人员只需按此执行

### 5.1 放置密钥

在项目根目录执行：

```powershell
Copy-Item .env.example .env
```

然后只在 `.env` 中把占位值替换为真实 Key：

```text
DEEPSEEK_API_KEY=你的真实密钥
```

`.env` 已被 Git 忽略。不要把密钥写进 `config.toml`、提交到版本库或粘贴到聊天。

### 5.2 连通与预算

```powershell
scripts\run_translate.bat --check
scripts\run_translate.bat --estimate
```

`--check` 会发一个极小请求并产生少量 token；自检与正式翻译使用同一请求形态，
同时校验实际响应模型与 reasoning 证据，防止端点忽略 thinking 或把错误模型名静默
回退为 Flash。
`--estimate` 完全离线，不调用 API。

基于当前 1055 条真实正文和当前两个账号的实际提示词，离线估算为：

- 全未命中输入约 3,573,968 token；
- 缓存场景约 161,093 未命中 + 3,412,875 命中；
- 输出约 182,254 token；
- 不含 reasoning 的基础费用参考约 **US$1.08–5.44**。

High thinking 的 reasoning token 由模型按内容决定，无法离线可靠估计，因此上述数字
不是总费用上界。必须以新版接口真实 3 条试跑返回的 usage 外推后再决定全量。

### 5.3 每个账号分别试译 3 条

不要只运行全局 `--limit 3`：目录排序会先命中 Facebook，无法验证 Instagram 语料。

```powershell
scripts\run_translate.bat --account fa_neakasaofficial --limit 3
scripts\run_translate.bat --account in_neakasa.tech --limit 3
```

懂德语的业务人员至少检查：称呼一致、语气自然、换行结构、品牌型号、标签与原帖的
数量/内容/大小写/顺序完全一致、
金额原样、数字/尺码/单位、已经是德语的原文片段。如果需要调整，可修改
`config.toml` 的语气/术语表或 `prompts/translate_de.md`；修改提示词时把
`PROMPT_VERSION` 加 1，普通运行就会自动把旧版本列为待译；同版本强制重试才使用
`--force --limit 3`。

### 5.4 全量与审校

试译被业务人员确认后：

```powershell
scripts\run_translate.bat
scripts\run_translate.bat --review
```

全量中断可直接重跑，成功项会跳过。打开两个账号目录下的 `review.md` 做 Markdown
预览，确认配图可见，然后逐篇勾选译文、数字和图内文字。`translated.jsonl` 是译文
真相源；`review.md` 与 `text_de.txt` 是派生的人工作业视图。

审校人员直接改 `review.md` 不会回写 `translated.jsonl`。当前已实现边界没有审校回写
或发布模块，若后续需要，必须作为发布链路的独立需求处理，不能在本轮暗中扩展。

## 6. 验收状态

| 项目 | 状态 | 证据 / 下一步 |
|---|---|---|
| DeepSeek 官方 API、High thinking、无输出上限 | ✅ 离线完成 | SDK 线级契约通过 |
| 真实归档发现、双账号 prompt、基础预算 | ✅ 完成 | 45 + 1010 条；本轮零 API 调用 |
| T1：新版真实 `--check` | ⏳ 待执行 | `.env` 已有；会产生少量 token |
| T2：提示词与术语表初版 | ✅ 完成 | 德语业务人员仍需确认措辞 |
| T3：每账号 3 条新版试译与人工质量确认 | ⏳ 待执行 | 旧版 6 条已自动过期 |
| T4：1055 条全量翻译 | ⏳ 待 T3 通过 | 不可在未确认质量时直接全跑 |
| T5：真实 `review.md` 带图预览 | ⏳ 待译文 | 代码路径已用合成数据验证 |

因此 F1/F2/F3 的代码可以标为已实现，但真实业务验收仍不能勾成完成。

## 7. 故障处理

- 缺 Key：程序给出复制 `.env.example` 的完整命令，不会联网。
- 401/403/404/400/422/429/402/5xx：`--check` 分类别给出处理建议。
- 模型回退：立即停止，不混用 Pro / Flash 结果。
- 金额被改：该条不写盘，修正提示词或模型后直接重跑，无需全量 `--force`。
- 标签少贴、多贴、翻译或调序：该条不写盘；原帖多少个就必须照搬多少个。
- 单条内容输出为空、截断或拒绝：记失败并保留断点；共享 API 错误则整批熔断。
- `translated.jsonl` 坏尾：新结果会先补换行边界并 `flush + fsync`；读取时逐行独立
  解码，截断的 UTF-8 坏行不会遮住后面已经付费且已落盘的有效结果。
- 英文正文后来变化或提示词版本升级：旧译文在审校清单中标为过期，普通重跑只处理
  受影响帖子。
- 重复双击：第二个付费进程会被 `state/translate.lock` 拒绝。

每次修改翻译实现后至少执行：

```powershell
.venv\Scripts\python.exe tests\tests_translate.py
.venv\Scripts\python.exe -m compileall -q .
git diff --check
```
