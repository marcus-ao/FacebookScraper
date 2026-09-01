r"""英文文案 → 德语翻译。对应实施计划 F 组（F1 主流程 / F2 风格 few-shot / F3 审核清单）。

用法（前置：scripts\setup.bat 已跑过，并已把 DeepSeek Key 放进项目 `.env`）：

    scripts\run_translate.bat --check              极小请求验证 API（会产生少量 token）
    scripts\run_translate.bat --estimate           离线估算 token / 费用
    scripts\run_translate.bat --account in_neakasa.tech --limit 3
    scripts\run_translate.bat                      翻译全部未翻译的帖子
    scripts\run_translate.bat --review             生成人工审核清单 review.md

**抓取产物不可变**：本模块只读 manifest.jsonl，译文写进同目录的
translated.jsonl。manifest 是重跑抓取就能重现的事实，译文是花钱买的加工结果，
两者混在一起会导致"重抓一次把译文冲掉"。

**API 直接走 DeepSeek 官方 OpenAI 兼容端点**：复用 OpenAI SDK 的
429/5xx 退避与分类异常，不手拼 HTTP，也不再经过 Anthropic 协议层。
DeepSeek V4 的 thinking 显式开启，默认推理强度为 `high`；请求不发送
`max_tokens` / `max_completion_tokens` 等客户端输出上限。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import statistics
import sys
import time
import unicodedata
from collections import Counter
from collections.abc import Mapping
from contextlib import AbstractContextManager, nullcontext
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from core.config import cfg                        # noqa: E402
from core.console import force_utf8                # noqa: E402
from core import paid_requests                     # noqa: E402
from core.store import (Archive, ArchivePathError, assert_physical_direct_path,
                        post_dirname)              # noqa: E402

# 提示词模板。放在独立文件里，改翻译行为不用改 Python，营销同事也能改。
TEMPLATE_PATH = ROOT / "prompts" / "translate_de.md"
DEEPSEEK_API_URL = "https://api.deepseek.com"

# 提示词版本。改了提示词就把它 +1：译文行里记着这个值，
# 于是"这批译文是旧提示词产出的"变成可查的事实，而不是靠记忆。
PROMPT_VERSION = 5

# 模型偶尔会在译文外面裹一层解释或代码围栏，这里做最小限度的剥离。
# 不做激进清洗——把模型真的想说的话删掉，比留着更难排查。
# ⚠️ 顺序必须长的在前：短的排前面会抢先匹配，
#    例如 "```de" 命中 "```deutsch" 只切掉 5 字符，留下 "utsch\n..." 污染译文。
_FENCES = ("```deutsch", "```german", "```text", "```de", "```")


# --------------------------------------------------------------------------
# 配置
# --------------------------------------------------------------------------

class Settings:
    """[translate] 段的读取。所有可调项集中在这里，代码里不写死。"""

    def __init__(self) -> None:
        c = cfg()
        g = lambda k, d=None: c.get("translate", k, d)      # noqa: E731
        self.provider: str = (g("provider", "deepseek") or "deepseek").strip().lower()
        self.base_url: str = (g("base_url", DEEPSEEK_API_URL)
                              or DEEPSEEK_API_URL).strip().rstrip("/")
        self.api_key_env: str = (g("api_key_env", "DEEPSEEK_API_KEY")
                                 or "DEEPSEEK_API_KEY").strip()
        self.model: str = (g("model", "deepseek-v4-pro")
                           or "deepseek-v4-pro").strip()
        self.timeout: float = float(g("timeout_seconds", 120))
        self.max_retries: int = int(g("max_retries", 2))
        self.gap: float = float(g("request_gap_seconds", 1.0))
        # 官方 OpenAI 兼容接口的 thinking 档位。业务默认 high；代码会同时发送
        # thinking.enabled，避免依赖端点默认值。
        self.reasoning_effort = str(g("reasoning_effort", "high") or "high").strip().lower()
        self.style_examples: int = int(g("style_examples", 6))
        self.tone: str = (g("tone", "") or "").strip()
        # 品牌语域决策。渲染进提示词，模型无法自行知道这些。
        self.address_form: str = (g("address_form", "du") or "du").strip()
        self.gender_style: str = (g("gender_style", "neutral") or "neutral").strip()
        self.anglicism_policy: str = (g("anglicism_policy", "moderate")
                                      or "moderate").strip()
        self.glossary: dict = dict(g("glossary", {}) or {})
        self.cost_rates: dict = dict(g("cost_rates_usd_per_million", {}) or {})
        self.validate()

    def validate(self) -> None:
        """在任何网络请求前给出可操作的配置错误，而不是等 SDK 返回模糊 400。"""
        if self.provider != "deepseek":
            raise SystemExit("config.toml 的 [translate].provider 目前只支持 deepseek；"
                             "翻译主干固定使用 DeepSeek 官方 OpenAI 兼容接口")
        if not self.base_url:
            raise SystemExit("[translate].base_url 不能为空；为避免把业务文案或内部密钥"
                             "误发到别的端点，本项目不做隐式官方回退")
        if self.base_url != DEEPSEEK_API_URL:
            raise SystemExit("provider=deepseek 时 base_url 必须是 " + DEEPSEEK_API_URL)
        if not self.model:
            raise SystemExit("[translate].model 不能为空")
        if self.model not in {
                "deepseek-v4-pro", "deepseek-v4-flash"}:
            raise SystemExit("DeepSeek 文本翻译模型只允许 deepseek-v4-pro 或 "
                             "deepseek-v4-flash；旧 deepseek-chat/reasoner 已停用")
        if not self.api_key_env:
            raise SystemExit("[translate].api_key_env 不能为空")
        if self.timeout <= 0 or self.max_retries < 0 or self.gap < 0:
            raise SystemExit("[translate] 的 timeout_seconds 必须 > 0，"
                             "max_retries/request_gap_seconds 必须 >= 0")
        if self.style_examples < 0:
            raise SystemExit("[translate].style_examples 不能为负数")
        if self.reasoning_effort not in {"low", "high", "max"}:
            raise SystemExit("[translate].reasoning_effort 可选值：low, high, max")
        if (set(self.cost_rates) != {"input", "cache_read", "output"}
                or any(not isinstance(value, (int, float))
                       or isinstance(value, bool)
                       or not math.isfinite(float(value)) or float(value) < 0
                       for value in self.cost_rates.values())):
            raise SystemExit(
                "[translate].cost_rates_usd_per_million 必须且只能包含"
                " input/cache_read/output，且费率是非负有限数字")

    def _raw_key(self) -> tuple[str, str]:
        """返回 (密钥, 来源)。环境变量优先，其次项目内的 .env。

        支持 .env 是因为 setx 会把密钥写进用户注册表、对所有进程可见；
        放项目里的 .env 收敛得多（已在 .gitignore 中）。不引入 python-dotenv：
        只需要 KEY=value 这一种形态，十行够了，不值得多一个依赖。
        """
        key = os.environ.get(self.api_key_env, "").strip()
        if key:
            return key, f"环境变量 {self.api_key_env}"
        env_file = ROOT / ".env"
        if env_file.exists():
            for line in env_file.read_text(encoding="utf-8-sig").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                name, _, value = line.partition("=")
                if name.strip() == self.api_key_env:
                    return value.strip().strip('"').strip("'"), f"{env_file.name} 文件"
        return "", ""

    def api_key(self) -> str:
        key, _ = self._raw_key()
        if not key:
            raise SystemExit(
                f"没找到 API 密钥（变量名 {self.api_key_env}）。两种设法任选其一：\n"
                f"\n"
                f"  【推荐】先复制项目里的 .env.example 为 .env，再把占位值替换成密钥：\n"
                f"      Copy-Item .env.example .env\n"
                f"  .env 内容应为一行：\n"
                f"      {self.api_key_env}=你的密钥\n"
                f"      （.env 已在 .gitignore 里，不会进版本库）\n"
                f"\n"
                f"  【或者】设系统环境变量：\n"
                f"      setx {self.api_key_env} \"你的密钥\"\n"
                f"      设完必须**重开终端 / 重新双击 .bat** 才生效\n"
                f"\n"
                f"  ❌ 不要写进 config.toml —— 那个文件会进版本库。"
            )
        return key

    def redacted(self) -> str:
        """给 --check 打印用。只露头尾各 4 位，中间打码。"""
        key, source = self._raw_key()
        if not key:
            return "(未设置)"
        shown = f"{key[:4]}...{key[-4:]}" if len(key) > 12 else "(已设置)"
        return f"{shown}　长度 {len(key)}　来源：{source}"


def build_client(s: Settings):
    """构造 DeepSeek 官方 OpenAI 兼容客户端。

    OpenAI SDK 会按官方约定使用 ``Authorization: Bearer``，并负责连接错误、
    429 与 5xx 的退避重试；业务代码只负责请求与结果契约。
    """
    from openai import OpenAI

    return OpenAI(
        api_key=s.api_key(),
        base_url=s.base_url,
        timeout=s.timeout,
        max_retries=s.max_retries,
    )


# --------------------------------------------------------------------------
# F2：风格 few-shot
# --------------------------------------------------------------------------

def pick_style_examples(rows: list[dict], n: int, exclude_id: str | None = None,
                        owner: str | None = None) -> list[str]:
    """从该账号已抓到的英文文案里挑 N 篇作风格锚点。

    取**长度中位数附近**的：最短的往往是"New drop 🔥"这种没信息量的，
    最长的又会把模型带向啰嗦，两头都不能代表这个品牌的常态口吻。

    注意这些是**单语示例**（只有英文原文，没有对应德文）——归档里本来就没有
    德语对照。所以它们的作用是"这个品牌平时怎么说话"，不是"这句该怎么译"。

    **每个账号只算一次**，不按帖排除当前篇：排除会让 system prompt 篇篇不同，
    既拿不到提示词缓存，也让不同帖子的语域基准出现漂移。
    某篇碰巧成为自己的风格参照是无害的（提示词已明确"不要翻译示例"），
    这点冗余远比缓存全失效划算。exclude_id 参数保留给调用方按需使用。
    """
    wanted_owner = (owner or "").strip().lower()
    texts = [(r.get("text") or "").strip() for r in rows
             if r.get("post_id") != exclude_id
             and (not wanted_owner
                  or (r.get("owner") or "").strip().lower() == wanted_owner)]
    texts = [t for t in texts if len(t) >= 20]
    if not texts or n <= 0:
        return []
    texts.sort(key=len)
    mid = len(texts) // 2
    picked, step = [], 0
    while len(picked) < min(n, len(texts)):
        for idx in (mid - step, mid + step) if step else (mid,):
            if 0 <= idx < len(texts) and texts[idx] not in picked:
                picked.append(texts[idx])
                if len(picked) >= min(n, len(texts)):
                    break
        step += 1
        if step > len(texts):
            break
    return picked


# 三项品牌语域决策的渲染文本。它们是**模型无法自行知道的决策**——
# 英文的 "you" 不含 du/Sie 的信息，不定死就会篇篇不一致。
_ADDRESS_FORM = {
    "du": "**称呼形式：du（非正式第二人称单数）。** 全篇一致使用 du / dein / dir / dich，"
          "祈使句用 du 形式（`Shop now` → `Jetzt shoppen`）。"
          "任何地方都不要切换成 Sie。",
    "sie": "**称呼形式：Sie（正式敬称）。** 全篇一致使用 Sie / Ihr / Ihnen，"
           "Sie 及其变格一律首字母大写，祈使句用 Sie 形式"
           "（`Shop now` → `Kaufen Sie jetzt`）。任何地方都不要切换成 du。",
}
_GENDER_STYLE = {
    "neutral": "**性别表达：中性改写。** 优先**改写句子结构以避开有性别的人称名词**"
               "（`Join thousands of happy customers` → `Tausende sind schon dabei`），"
               "而不是在 Kunden / Kund:innen 之间挑一个。实在避不开时用中性词"
               "（Menschen、Personen、Team）。不要使用冒号、星号、下划线等标记形式。",
    "colon": "**性别表达：冒号式。** 人称名词写成 Kund:innen / Mitarbeiter:innen。"
             "只用冒号，不要混用星号或下划线。",
    "generic": "**性别表达：传统阳性泛指。** 直接用 Kunden / Mitarbeiter，"
               "不使用任何标记形式，也不做中性改写。",
}
_ANGLICISM = {
    "moderate": "**英语借词：适度保留。** 德语营销已通用的英语词保留原样"
                "（Sale、Shop、Style、Look、Outfit、Hoodie、Basics、Must-have），"
                "其余一律译成德语。判断标准：这个词会不会出现在德国主流品牌的官网上。",
    "minimal": "**英语借词：尽量避免。** 只保留没有通行德语说法的词，其余全部译出。",
    "keep": "**英语借词：尽量保留。** 原文里的英语词优先不译，"
            "只翻译连接性的句子成分。",
}


def render_glossary(glossary: dict) -> str:
    if not glossary:
        return ("（本账号还没有配置术语表。请在同一批译文里对反复出现的产品名与卖点词"
                "保持一致的译法。）")
    lines = ["以下英文词/短语**必须**译成右列指定的德文，不得使用同义替换：", "",
             "| 英文 | 德文 |", "| --- | --- |"]
    lines += [f"| {k} | {v} |" for k, v in sorted(glossary.items())]
    return "\n".join(lines)


def render_style_examples(examples: list[str]) -> str:
    if not examples:
        return "（暂无——该账号还没有抓到足够长的文案。回填完成后这里会自动填充。）"
    # 抓到的正文属于不可信外部数据。用 JSON 字符串包裹而不是 Markdown 围栏：
    # 正文即使自带 ``` 或“忽略前文”也只是一段可识别的数据，不会提前闭合围栏。
    encoded = [json.dumps(ex, ensure_ascii=False)
               .replace("<", "\\u003c").replace(">", "\\u003e")
               for ex in examples]
    return "\n\n".join(
        f"<untrusted_style_reference index=\"{i}\">\n"
        f"{ex}\n"
        f"</untrusted_style_reference>"
        for i, ex in enumerate(encoded, 1))


def build_system_prompt(s: Settings, examples: list[str]) -> str:
    """渲染 prompts/translate_de.md 模板。

    提示词放在独立文件而不是这里，有两个理由：
    改翻译行为不需要改 Python（营销同事也能改），以及它足够长，
    塞进源码会把这个模块变成一坨字符串。

    渲染结果对同一个账号是**稳定的**（风格示例每账号只算一次），
    因此能命中提示词缓存，也保证篇与篇之间语域基准一致。
    """
    tpl = TEMPLATE_PATH.read_text(encoding="utf-8")
    # 剥掉给人看的 HTML 注释头，不发给模型
    while tpl.lstrip().startswith("<!--"):
        stripped = tpl.lstrip()
        end = stripped.find("-->")
        if end < 0:
            break
        tpl = stripped[end + 3:]

    def pick(table: dict, key: str, label: str) -> str:
        v = table.get((key or "").strip().lower())
        if v is None:
            raise SystemExit(
                f"config.toml 的 [translate].{label} = {key!r} 不认识。"
                f"可选值：{', '.join(sorted(table))}")
        return v

    subs = {
        "{{ADDRESS_FORM}}": pick(_ADDRESS_FORM, s.address_form, "address_form"),
        "{{GENDER_STYLE}}": pick(_GENDER_STYLE, s.gender_style, "gender_style"),
        "{{ANGLICISM_POLICY}}": pick(_ANGLICISM, s.anglicism_policy, "anglicism_policy"),
        "{{TONE}}": s.tone or "（无额外要求）",
        "{{GLOSSARY}}": render_glossary(s.glossary),
        "{{STYLE_EXAMPLES}}": render_style_examples(examples),
    }
    # 先在原模板上找未知占位符；替换后再查会把风格示例正文里合法的 ``{{...}}``
    # 也误当成模板错误。拼错占位符必须在调用 API 前失败，不能原样发给模型。
    placeholders = set(re.findall(r"\{\{[^{}\r\n]+\}\}", tpl))
    unknown = sorted(placeholders - set(subs))
    if unknown:
        raise SystemExit(f"提示词模板含未知占位符：{unknown}")
    for k, v in subs.items():
        tpl = tpl.replace(k, v)

    # 不在这里扫描最终文本：风格样例是外部数据，完全可能合法包含 ``{{TONE}}``
    # 这样的字面文本。未知模板占位符已在插入样例之前由 placeholders 检查过。
    return tpl.strip()


# --------------------------------------------------------------------------
# 需人工确认的数字（价格 / 尺码 / 英制单位）
# --------------------------------------------------------------------------

# 提示词要求模型**不要**换算金额与尺码，所以这些会原样留在德语译文里。
# 这里把它们标出来，让审校人一眼看到"这篇要改价"，而不是在几十篇里自己找。
_MONEY_RE = re.compile(
    r"[$€£¥]\s?\d"                                       # $50、€ 50（符号在前）
    r"|\d[\d.,]*\s?[$€£¥]"                               # 19,99 €（德式后置，我们自己要求的格式）
    r"|(?<![A-Za-z])\d[\d.,]*\s?(?:USD|EUR|Dollar|Euro)\b",
    re.I)
_SIZE_RE = re.compile(
    r"\b(?:US|UK)\s?\d{1,2}(?:[.,]5)?\b|\b(?:size|Größe|Gr\.)\s?\d{1,3}\b", re.I)
_IMPERIAL_RE = re.compile(
    r"\b\d[\d.,]*\s?(?:inch(?:es)?|lbs?|oz|ft|°F)\b|\d\s?\"", re.I)


def numeric_flags(text: str) -> list[str]:
    """译文里需要人工确认的数字。空列表表示这篇可以直接用。"""
    out = []
    if _MONEY_RE.search(text):
        out.append("含货币金额 —— 需替换成德国站定价（提示词刻意不换算）")
    if _SIZE_RE.search(text):
        out.append("含数字尺码 —— 需确认是否要转 EU 码")
    if _IMPERIAL_RE.search(text):
        out.append("含英制单位 —— 确认换算是否正确")
    return out


def review_numeric_flags(src_en: str, text_de: str) -> list[str]:
    """审校警示同时看原文和译文，避免模型删掉/改写数字后反而不报警。"""
    out: list[str] = []
    if _MONEY_RE.search(src_en or "") or _MONEY_RE.search(text_de or ""):
        out.append("含货币金额 —— 需替换成德国站定价（提示词刻意不换算）")
    if _SIZE_RE.search(src_en or "") or _SIZE_RE.search(text_de or ""):
        out.append("原文或译文含数字尺码 —— 确认未擅自换成 EU 码")
    if _IMPERIAL_RE.search(src_en or "") or _IMPERIAL_RE.search(text_de or ""):
        out.append("原文含/译文保留英制单位 —— 核对物理量换算与有效位数")
    return out


# 一个完整的金额 token：符号在前（$49.99）或在后（49,99 € / 50 USD）。
# 用 \d+(?:[.,]\d+)* 而不是 [\d.,]* ——后者会把句尾的句号也吃进来，
# 导致"原样出现"的比对因为一个标点而误报。
_MONEY_TOKEN_RE = re.compile(
    r"[$€£¥]\s?\d+(?:[.,]\d+)*"
    r"|\d+(?:[.,]\d+)*\s?(?:(?:USD|EUR|Dollar|Euro)\b|[$€£¥](?!\w))",
    re.I)


def normalize_money_token(tok: str) -> str:
    """比对用的归一化：只去掉空白。数值、分隔符、符号、符号位置都要求原样。"""
    return re.sub(r"\s+", "", tok)


# 兼容模块内旧名字；流水线预检使用公开入口，避免与实际替换规则漂移。
_norm_money = normalize_money_token


def extract_money_tokens(text: str) -> tuple[str, ...]:
    """公开给流水线分流使用的金额真相；与写盘硬闸共用同一正则。"""
    return tuple(token.strip() for token in _MONEY_TOKEN_RE.findall(text or ""))


def apply_money_mapping(text: str, mapping: Mapping[str, str]) -> str:
    """按完整金额 token 应用业务价格表；空白差异沿用金额硬闸的归一化。

    这一步只改最终发布副本，不回写 ``translated.jsonl``。配置里归一化后
    重复且值不同的键会失败，避免字典顺序偷偷决定价格。
    """
    normalized: dict[str, str] = {}
    for raw_key, raw_value in mapping.items():
        key = _norm_money(str(raw_key))
        value = str(raw_value).strip()
        if not key or not value:
            raise ValueError("价格映射的键和值都不能为空")
        if key in normalized and normalized[key] != value:
            raise ValueError("归一化后重复的金额映射值不一致：%r" % raw_key)
        normalized[key] = value
    return _MONEY_TOKEN_RE.sub(
        lambda match: normalized.get(_norm_money(match.group(0)), match.group(0)),
        text or "")


def money_preserved(src_en: str, text_de: str) -> list[str]:
    """检查原文每处金额是否原样出现，且译文没有新增金额。

    这是对提示词第 3 节的**代码侧强制**：提示词要求模型逐字符复制金额，
    但提示词只是要求，模型可能不听。金额被悄悄换算是本项目里
    最贵的一类错误——格式看着完全正确，人工审校时极易滑过去——
    所以必须有一道机器检查兜底。

    只去空白后比对，不做任何数值或格式归一：
    `$49.99` → `49,99 $` 币种没变，但写法和符号位置都变了，同样算违规。
    """
    source_tokens = _MONEY_TOKEN_RE.findall(src_en or "")
    translated_tokens = _MONEY_TOKEN_RE.findall(text_de or "")

    # 必须按 token 精确、多重集比对。子串判断会把 $5 错认成存在于 $50 中；
    # 单纯逐个 ``in`` 还会让原文出现两次、译文只留一次的金额漏检。
    translated_left = Counter(_norm_money(t) for t in translated_tokens)
    missing: list[str] = []
    for token in source_tokens:
        normalized = _norm_money(token)
        if translated_left[normalized] > 0:
            translated_left[normalized] -= 1
        else:
            missing.append(token.strip())

    source_left = Counter(_norm_money(t) for t in source_tokens)
    added: list[str] = []
    for token in translated_tokens:
        normalized = _norm_money(token)
        if source_left[normalized] > 0:
            source_left[normalized] -= 1
        else:
            added.append(token.strip())

    if not missing and not added:
        return []
    parts = []
    if missing:
        parts.append("原文金额 %s 未在译文里原样出现" % "、".join(missing))
    if added:
        parts.append("译文新增了原文没有的金额 %s" % "、".join(added))
    hint = ("译文里凭空出现了 €/EUR，八成是被换算了"
            if re.search(r"€|EUR\b|Euro", text_de or "", re.I)
            else "可能被改写、被换算、重复或整个漏掉了")
    return ["❗%s —— %s" % ("；".join(parts), hint)]


def _is_hashtag_char(ch: str) -> bool:
    """Meta 标签可用的 Unicode 字符：字母、数字、组合记号与下划线。"""
    return ch == "_" or unicodedata.category(ch)[:1] in {"L", "N", "M"}


def extract_hashtags(text: str) -> list[str]:
    r"""按出现顺序提取 hashtag，并保留原始大小写与 Unicode 码点。

    不用 ``\w+``：它会漏掉部分组合音标/分解式文字；也不用“读到空格为止”，
    否则句尾逗号或句号会被误算进标签。
    """
    value = text or ""
    out: list[str] = []
    i = 0
    while i < len(value):
        if value[i] != "#":
            i += 1
            continue
        if i and (_is_hashtag_char(value[i - 1]) or value[i - 1] == "#"):
            i += 1
            continue
        end = i + 1
        while end < len(value) and _is_hashtag_char(value[end]):
            end += 1
        if end > i + 1:
            out.append(value[i:end])
            i = end
        else:
            i += 1
    return out


def hashtags_preserved(src_en: str, text_de: str) -> list[str]:
    """标签必须逐个原样复制，数量、内容、大小写与顺序全部一致。"""
    source = extract_hashtags(src_en)
    translated = extract_hashtags(text_de)
    if source == translated:
        return []
    return [
        "❗话题标签没有按原帖逐个原样照搬（数量、内容、大小写和顺序都必须一致）"
        f" —— 原文 {json.dumps(source, ensure_ascii=False)}；"
        f"译文 {json.dumps(translated, ensure_ascii=False)}"
    ]


# --------------------------------------------------------------------------
# F1：翻译主流程
# --------------------------------------------------------------------------

def _strip_wrapper(text: str) -> str:
    """剥掉模型偶尔加的代码围栏。只处理围栏，不做别的清洗。"""
    t = text.strip()
    for fence in _FENCES:
        if t.lower().startswith(fence) and t.endswith("```"):
            t = t[len(fence):]
            if t.endswith("```"):
                t = t[:-3]
            return t.strip()
    return t


class ModelMismatchError(RuntimeError):
    """DeepSeek 会把未知模型静默映射到 Flash；不能让它污染 Pro 译文批次。"""


class FatalBatchError(RuntimeError):
    """鉴权、端点或请求配置是整批共享的；遇到这类错误必须立刻停。"""


class SourceDataError(FatalBatchError):
    """归档正文不满足付费请求的最小输入契约。"""


def source_text_sha256(text: str) -> str:
    """绑定模型实际收到的正文（strip 后 UTF-8），防止旧译文错配新正文。"""
    if not isinstance(text, str):
        raise SourceDataError("manifest 的 text 必须是字符串；已停止，未调用 API")
    return hashlib.sha256(text.strip().encode("utf-8")).hexdigest()


def translation_is_current(source: dict, translated: dict | None) -> bool:
    """只有源正文指纹与当前提示词版本同时匹配，译文才算当前可用。"""
    if not isinstance(translated, dict):
        return False
    text = source.get("text")
    return (isinstance(text, str)
            and translated.get("source_text_sha256") == source_text_sha256(text)
            and translated.get("prompt_version") == PROMPT_VERSION)


def _model_matches(requested: str, actual: str) -> bool:
    req = (requested or "").strip().lower()
    got = (actual or "").strip().lower()
    return bool(req and got and (req == got or req in got or got in req))


def _usage_values(usage) -> dict[str, int]:
    """把 DeepSeek OpenAI 兼容 usage 归一成可写盘、可汇总的整数。"""
    if usage is None:
        return {}
    raw = usage.model_dump() if hasattr(usage, "model_dump") else {}
    aliases = {
        "input_tokens": ("input_tokens", "prompt_tokens"),
        "output_tokens": ("output_tokens", "completion_tokens"),
        "prompt_cache_hit_tokens": ("prompt_cache_hit_tokens",),
        "prompt_cache_miss_tokens": ("prompt_cache_miss_tokens",),
        "reasoning_tokens": ("reasoning_tokens",),
    }
    out: dict[str, int] = {}
    for normalized, names in aliases.items():
        for name in names:
            value = raw.get(name, getattr(usage, name, None))
            if isinstance(value, int) and value >= 0:
                out[normalized] = value
                break
    for key in ("completion_tokens_details", "output_tokens_details"):
        details = raw.get(key)
        if isinstance(details, dict) and isinstance(details.get("reasoning_tokens"), int):
            out["reasoning_tokens"] = details["reasoning_tokens"]
            break
    return out


def _is_fatal_api_error(exc: Exception) -> bool:
    if isinstance(exc, (ModelMismatchError, paid_requests.PaidRequestBlocked)):
        return True
    try:
        import openai
        return isinstance(exc, openai.APIError)
    except (ImportError, AttributeError):
        return False


class Translator:
    """一次翻译调用。抽成类是为了测试能整体替换掉它，不必打桩到 SDK 内部。"""

    def __init__(self, settings: Settings, client=None,
                 paid_controller: paid_requests.RequestController | None = None) -> None:
        self.s = settings
        self._client = client
        self._last_call = 0.0
        self.last_usage: dict[str, int] = {}
        self.last_model: str = ""
        # 上一次响应实际返回的部分。--check 用它验证官方端点确实返回了思考内容，
        # 而不是只凭请求参数猜测 thinking 已生效。
        self.last_blocks: list[str] = []
        self.usage_totals: Counter = Counter()
        self._paid_controller = paid_controller
        self._paid_job_key = ""
        self._paid_source_ref = ""
        self._paid_receipt: paid_requests.PaidReceipt | None = None

    def set_paid_context(self, job_key: str, source_ref: str) -> None:
        self._paid_job_key = str(job_key)
        self._paid_source_ref = str(source_ref)

    @property
    def paid_request_id(self) -> str:
        return self._paid_receipt.request_id if self._paid_receipt else ""

    def finalize_paid(self, accepted: bool, reason: str = "") -> None:
        if self._paid_controller is None or self._paid_receipt is None:
            return
        receipt = self._paid_receipt
        self._paid_controller.finalize(
            receipt, accepted=accepted, reason=reason)
        self._paid_receipt = None

    @property
    def client(self):
        if self._client is None:
            self._client = build_client(self.s)
        return self._client

    def _pace(self) -> None:
        gap = self.s.gap
        if gap <= 0:
            return
        elapsed = time.monotonic() - self._last_call
        if elapsed < gap:
            time.sleep(gap - elapsed)
        self._last_call = time.monotonic()

    def request_kwargs(self, text: str, system: str) -> dict:
        """正式翻译和 --check 共用同一请求形态，避免自检假阳性。"""
        kw = {
            "model": self.s.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": text},
            ],
            "reasoning_effort": self.s.reasoning_effort,
            "extra_body": {"thinking": {"type": "enabled"}},
        }
        # 刻意不发送 max_tokens / max_completion_tokens / temperature / top_p。
        # 前两者会人为限制输出；后两者在 thinking 模式下会被 DeepSeek 忽略。
        return kw

    def translate(self, text: str, system: str) -> str:
        if self._paid_controller is None:
            return self._translate_once(text, system)
        # 客户端/密钥的纯本地构造先做；只有即将进入真实请求时才写 started。
        _ = self.client
        fallback = hashlib.sha256(
            (self.s.model + "\0" + system + "\0" + text).encode("utf-8")).hexdigest()
        job_key = self._paid_job_key or ("translation-check:" + fallback)
        source_ref = self._paid_source_ref or "check:translation"
        self._paid_receipt = None
        result, receipt = self._paid_controller.run(
            stage="translation", job_key=job_key, source_ref=source_ref,
            media_index=None, model=self.s.model,
            request=lambda: self._translate_once(text, system),
            usage_getter=lambda: self.last_usage,
            usage_errors=translation_usage_errors,
            usage_cost=lambda usage: usage_cost_upper_bound(self.s, dict(usage)))
        self._paid_receipt = receipt
        self._paid_job_key = ""
        self._paid_source_ref = ""
        return result

    def _translate_once(self, text: str, system: str) -> str:
        self._pace()
        self.last_usage = {}
        self.last_model = ""
        self.last_blocks = []

        resp = self.client.chat.completions.create(**self.request_kwargs(text, system))
        self.last_model = str(getattr(resp, "model", "") or "")
        self.last_usage = _usage_values(getattr(resp, "usage", None))
        self.usage_totals.update(self.last_usage)

        if self.s.provider == "deepseek" and not _model_matches(self.s.model, self.last_model):
            raise ModelMismatchError(
                f"请求模型 {self.s.model!r}，实际响应 model={self.last_model!r}。"
                "DeepSeek 会把不支持的模型名静默映射到 Flash，已停止批次以免混用模型")

        choices = getattr(resp, "choices", None) or []
        if not choices:
            raise RuntimeError("DeepSeek 响应没有 choices")
        choice = choices[0]
        message = getattr(choice, "message", None)
        if message is None:
            raise RuntimeError("DeepSeek 响应没有 assistant message")
        reasoning = str(getattr(message, "reasoning_content", "") or "")
        out = getattr(message, "content", "") or ""
        if not isinstance(out, str):
            raise RuntimeError("DeepSeek 返回的译文不是文本")
        if reasoning:
            self.last_blocks.append("reasoning")
        if out:
            self.last_blocks.append("text")

        finish_reason = str(getattr(choice, "finish_reason", "") or "")
        if finish_reason == "length":
            raise RuntimeError(
                "模型到达 DeepSeek 服务端输出上限，译文可能被截断；"
                "客户端没有发送 max_tokens 或同类输出限制。请缩短单条正文/提示词后重跑")
        if finish_reason == "content_filter":
            raise RuntimeError("DeepSeek 内容过滤器拦截了这条文案")
        if finish_reason == "insufficient_system_resource":
            raise RuntimeError("DeepSeek 当前系统资源不足，请稍后从断点重跑")
        if finish_reason not in {"", "stop"}:
            raise RuntimeError(f"DeepSeek 以非预期原因结束：finish_reason={finish_reason!r}")

        out = _strip_wrapper(out)
        if not out.strip():
            raise RuntimeError("模型返回了空译文")
        return out


def usage_cost_upper_bound(s: Settings, usage: dict[str, int]) -> float | None:
    """按 config 中的保守最高费率估算；没有完整费率时不编造金额。"""
    rates = s.cost_rates
    needed = {"input", "cache_read", "output"}
    if not needed.issubset(rates):
        return None
    hit = usage.get("prompt_cache_hit_tokens", 0)
    if "prompt_cache_miss_tokens" in usage:
        miss = usage["prompt_cache_miss_tokens"]
    else:
        miss = usage.get("input_tokens", 0)
    output = usage.get("output_tokens", 0)
    return (miss * float(rates["input"])
            + hit * float(rates["cache_read"])
            + output * float(rates["output"])) / 1_000_000


def translation_usage_errors(usage: Mapping[str, Any]) -> list[str]:
    """计费至少要有输出，以及总输入或完整 cache hit/miss。"""
    errors: list[str] = []
    output = usage.get("output_tokens")
    if not isinstance(output, int) or isinstance(output, bool) or output < 0:
        errors.append("output_tokens")
    total_input = usage.get("input_tokens")
    total_valid = (isinstance(total_input, int)
                   and not isinstance(total_input, bool) and total_input >= 0)
    hit = usage.get("prompt_cache_hit_tokens")
    miss = usage.get("prompt_cache_miss_tokens")
    split_valid = all(isinstance(value, int)
                      and not isinstance(value, bool) and value >= 0
                      for value in (hit, miss))
    if not total_valid and not split_valid:
        errors.append("input_tokens 或完整 prompt_cache_hit/miss_tokens")
    return errors


def print_usage_summary(s: Settings, usage: dict[str, int]) -> None:
    if not usage:
        print("用量：端点没有返回可识别的 usage 字段。")
        return
    print("用量：输入 %s tok / 缓存命中 %s tok / 输出 %s tok" % (
        usage.get("input_tokens", usage.get("prompt_cache_miss_tokens", "?")),
        usage.get("prompt_cache_hit_tokens", 0),
        usage.get("output_tokens", "?")))
    if "reasoning_tokens" in usage:
        print(f"      其中 reasoning {usage['reasoning_tokens']} tok")
    cost = usage_cost_upper_bound(s, usage)
    if cost is not None:
        print(f"费用上界：约 US${cost:.4f}（按 config.toml 记录的最高费率；"
              "实际账单受缓存与峰/谷时段影响）")


def _approx_tokens(text: str) -> int:
    """DeepSeek 官方粗略换算：英文字符约 0.3 token，中文字符约 0.6。"""
    cjk = len(re.findall(r"[\u3400-\u9fff]", text or ""))
    other = max(0, len(text or "") - cjk)
    return max(1, round(cjk * 0.6 + other * 0.3))


class TranslationRunLock(AbstractContextManager):
    """整个付费批次单实例运行，防止双击两次造成同一帖子重复付费。"""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._file = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._file = self.path.open("a+b")
        if self._file.seek(0, os.SEEK_END) == 0:
            self._file.write(b"0")
            self._file.flush()
        self._file.seek(0)
        try:
            if sys.platform.startswith("win"):
                import msvcrt
                msvcrt.locking(self._file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self._file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (OSError, BlockingIOError):
            self._file.close()
            self._file = None
            raise SystemExit("另一个翻译批次正在运行。请勿重复双击；等它结束后再试。")
        return self

    def __exit__(self, exc_type, exc, tb):
        if self._file is not None:
            try:
                self._file.seek(0)
                if sys.platform.startswith("win"):
                    import msvcrt
                    msvcrt.locking(self._file.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(self._file.fileno(), fcntl.LOCK_UN)
            finally:
                self._file.close()
                self._file = None
        return False


# --------------------------------------------------------------------------
# 归档读写
# --------------------------------------------------------------------------

def account_dirs(archive_root: Path, only: str | None = None) -> list[Path]:
    """archive/ 下所有含 manifest.jsonl 的账号目录。"""
    if not archive_root.exists():
        return []
    dirs = sorted(p for p in archive_root.iterdir()
                  if p.is_dir() and (p / "manifest.jsonl").exists())
    if only:
        dirs = [p for p in dirs if p.name == only]
    return dirs


def load_translated(path: Path) -> dict[str, dict]:
    """读 translated.jsonl，同 post_id 后写胜出（与 manifest 一致的语义）。"""
    out: dict[str, dict] = {}
    if not path.exists():
        return out
    assert_physical_direct_path(
        path.parent, path, kind="file", label="translated.jsonl")
    # 二进制逐行解码：若一次硬终止恰好截断 UTF-8 多字节字符，只跳过那一行；
    # 后面已经 fsync 的付费结果仍然必须可见，不能被整文件 UnicodeDecodeError 吞掉。
    with path.open("rb") as f:
        for raw_line in f:
            raw_line = raw_line.strip()
            if not raw_line:
                continue
            try:
                line = raw_line.decode("utf-8")
                r = json.loads(line)
                if (not isinstance(r, dict)
                        or not isinstance(r.get("post_id"), str)
                        or not r["post_id"].strip()
                        or not isinstance(r.get("text_de"), str)
                        or not r["text_de"].strip()
                        or not isinstance(r.get("translated_at"), str)
                        or not isinstance(r.get("model"), str)
                        or not isinstance(r.get("prompt_version"), int)):
                    continue
                out[r["post_id"]] = r
            except (UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError):
                # TypeError：整行是合法 JSON 但不是对象（如数组），下标取不到
                continue
    return out


def append_jsonl(path: Path, row: dict) -> None:
    """把付费结果安全追加成独立一行；坏尾/缺换行不能吞掉新结果。"""
    payload = (json.dumps(row, ensure_ascii=False) + "\n").encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    assert_physical_direct_path(
        path.parent, path, kind="file", label="translated.jsonl")
    with path.open("a+b") as f:
        end = f.seek(0, os.SEEK_END)
        if end:
            f.seek(-1, os.SEEK_END)
            if f.read(1) != b"\n":
                f.seek(0, os.SEEK_END)
                f.write(b"\n")
        f.seek(0, os.SEEK_END)
        f.write(payload)
        f.flush()
        os.fsync(f.fileno())


def pending(rows: list[dict], done: dict[str, dict], force: bool,
            scope: frozenset[str] | None = None) -> list[dict]:
    """待翻译：正文非空，且没有与当前正文指纹、提示词版本一致的译文。

    ``scope`` 是可选的 post_id 白名单（见 :func:`resolve_scope`）。
    **作用域过滤放在数据契约校验之后**：即使只翻一篇，整份 manifest 的
    形态问题仍然要在联网前失败闭合，不能被作用域悄悄绕过去。
    """
    out = []
    for r in rows:
        if not isinstance(r, dict):
            raise SourceDataError("manifest 含非对象记录；已停止，未调用 API")
        pid = r.get("post_id")
        text = r.get("text")
        if not isinstance(pid, str) or not pid.strip():
            raise SourceDataError("manifest 的 post_id 必须是非空字符串；已停止，未调用 API")
        if not isinstance(text, str):
            raise SourceDataError(
                f"manifest 帖子 {pid!r} 的 text 不是字符串；已停止，未调用 API")
        if scope is not None and pid.strip() not in scope:
            continue
        if not text.strip():
            continue
        if not force and translation_is_current(r, done.get(pid)):
            continue
        out.append(r)
    # 按时间正序翻，产出顺序稳定，便于人工按时间线审校
    out.sort(key=lambda r: r.get("created_at") or "")
    return out


def resolve_scope(dirs: list[Path], *, post_ids: list[str] | None = None,
                  latest_posts: int | None = None) -> frozenset[str] | None:
    """把 ``--post-id`` / ``--latest-posts`` 解析成一个 post_id 白名单。

    返回 ``None`` 表示不限定作用域，保持既有行为（全账号待译队列、最老优先）。

    **为什么需要它**（CR-47）：:func:`pending` 按 ``created_at`` **正序**排，
    所以 ``--limit N`` 永远从最老那一头开始翻。而 K9 与 G8 的验收标的都是
    **最新那几篇**，两组因此卡在同一件事上：K 的 ``--latest-posts 3``
    算出 0 张待处理，G 的 ``compose_post`` 把点名的三篇全按"译文缺失"拦下。

    参数形状**照抄** ``localize_images.py::select_rows``（同样是
    ``--post-id`` 可重复 + ``--latest-posts N`` 跨账号取最新），
    两边保持一致，免得下一个人要记两套语义。

    ``--latest-posts`` 与 K 组一样**不按正文过滤**：选的是最新 N 篇帖子本身。
    其中若有纯视频/无正文帖，:func:`pending` 会自然跳过，调用方负责把
    "选了 N 篇、实际待译 M 篇"如实打出来，而不是偷偷替换成别的帖子。
    """
    if post_ids is None and latest_posts is None:
        return None

    entries: list[tuple[str, str, str]] = []      # (created_at, 账号目录, post_id)
    for arc_base in dirs:
        arc = Archive(arc_base.parent, arc_base.name)
        for row in arc.rows():
            if not isinstance(row, dict):
                raise SourceDataError("manifest 含非对象记录；已停止，未调用 API")
            pid = row.get("post_id")
            if not isinstance(pid, str) or not pid.strip():
                raise SourceDataError(
                    "manifest 的 post_id 必须是非空字符串；已停止，未调用 API")
            created = row.get("created_at")
            entries.append((created if isinstance(created, str) else "",
                            arc_base.name, pid.strip()))

    if post_ids is not None:
        wanted = {pid.strip() for pid in post_ids if pid.strip()}
        if not wanted:
            raise SourceDataError("--post-id 不能是空字符串")
        found = {pid for _, _, pid in entries}
        missing = sorted(wanted - found)
        if missing:
            raise SourceDataError(
                "指定的 post_id 在所选账号归档里不存在：" + "、".join(missing)
                + "\n    （加了 --account 时只在那一个账号里找；去掉它再试）")
        return frozenset(wanted)

    if latest_posts is None or latest_posts <= 0:
        raise SourceDataError("--latest-posts 必须是正整数")
    ordered = sorted(entries, reverse=True)       # created_at → 账号 → post_id
    return frozenset(pid for _, _, pid in ordered[:latest_posts])


def run_translate(s: Settings, translator: Translator, arc_base: Path,
                  limit: int | None, force: bool, dry_run: bool,
                  scope: frozenset[str] | None = None) -> tuple[int, int]:
    """翻译一个账号目录。返回 (成功数, 失败数)。"""
    arc = Archive(arc_base.parent, arc_base.name)
    rows = arc.rows()
    out_path = arc_base / "translated.jsonl"
    done = load_translated(out_path)
    todo = pending(rows, done, force, scope)
    current_done = sum(
        1 for r in rows
        if isinstance(r, dict) and isinstance(r.get("post_id"), str)
        and translation_is_current(r, done.get(r["post_id"])))
    if limit is not None:
        todo = todo[:limit]

    print(f"\n=== {arc_base.name} ===")
    scope_note = ""
    if scope is not None:
        in_scope = sum(1 for r in rows
                       if isinstance(r, dict) and r.get("post_id") in scope)
        scope_note = f" / 作用域内 {in_scope} 篇"
    print(f"  归档 {len(rows)} 篇{scope_note} / 当前有效译文 {current_done} 篇 / "
          f"本次待译 {len(todo)} 篇")
    if not todo:
        print("  没有需要翻译的帖子。")
        return 0, 0

    # system prompt 每个账号只构建一次：内容对所有帖子相同，
    # 既能命中提示词缓存，也保证篇与篇之间的语域基准完全一致。
    account_owner = arc_base.name.split("_", 1)[-1].strip().lower()
    examples = pick_style_examples(rows, s.style_examples, owner=account_owner)
    system = build_system_prompt(s, examples)
    print(f"  提示词：{len(system)} 字符 / {len(examples)} 篇风格参照 / "
          f"称呼 {s.address_form} / 术语表 {len(s.glossary)} 条")

    ok = bad = flagged = money_violated = hashtag_violated = 0
    for i, r in enumerate(todo, 1):
        pid = r.get("post_id", "?")
        text = (r.get("text") or "").strip()

        if dry_run:
            print(f"  [{i}/{len(todo)}] {pid}  (dry-run，不调用 API)  "
                  f"{text[:44].replace(chr(10), ' ')}")
            ok += 1
            continue

        source_ref = "%s:%s" % (
            str(r.get("platform") or "source").strip().lower(), pid)
        paid_job_key = "translation:" + hashlib.sha256(
            (arc_base.name + "\0" + pid + "\0" + source_text_sha256(text)
             + "\0" + str(PROMPT_VERSION)).encode("utf-8")).hexdigest()
        if hasattr(translator, "set_paid_context"):
            translator.set_paid_context(paid_job_key, source_ref)
        try:
            de = translator.translate(text, system)
        except Exception as e:
            print(f"  [{i}/{len(todo)}] {pid}  失败：{type(e).__name__}: {e}")
            bad += 1
            if _is_fatal_api_error(e):
                raise FatalBatchError(
                    "API 鉴权/端点/模型或请求配置属于整批共享错误，已立刻停止，"
                    "避免对剩余帖子重复无意义请求") from e
            # 内容级输出错误只跳过这一条，保留批处理的断点续跑能力。
            continue

        money_violations = money_preserved(text, de)
        hashtag_violations = hashtags_preserved(text, de)
        violations = money_violations + hashtag_violations
        if violations:
            if hasattr(translator, "finalize_paid"):
                translator.finalize_paid(
                    False, "translation immutable-content gate rejected output")
            bad += 1
            money_violated += bool(money_violations)
            hashtag_violated += bool(hashtag_violations)
            print(f"  [{i}/{len(todo)}] {pid}  失败：输出违反不可改内容规则；未写盘")
            for v in violations:
                print(f"        {v}")
            continue

        row = {
            "post_id": pid,
            "source_text_sha256": source_text_sha256(text),
            "text_de": de,
            "translated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "model": getattr(translator, "last_model", "") or s.model,
            "prompt_version": PROMPT_VERSION,
        }
        usage = getattr(translator, "last_usage", None)
        if usage:
            row["usage"] = usage
        paid_request_id = str(getattr(translator, "paid_request_id", "") or "")
        if paid_request_id:
            row["paid_request_id"] = paid_request_id
        try:
            append_jsonl(out_path, row)
        except BaseException:
            if hasattr(translator, "finalize_paid"):
                translator.finalize_paid(
                    False, "translated artifact persistence failed")
            raise
        if hasattr(translator, "finalize_paid"):
            translator.finalize_paid(True, "translated artifact fsynced")
        ok += 1
        flags = review_numeric_flags(text, de)
        if flags:
            flagged += 1
        mark = "  ⚠需人工确认" if flags else ""
        print(f"  [{i}/{len(todo)}] {pid}  OK  "
              f"{len(text)}→{len(de)} 字符{mark}  {de[:36].replace(chr(10), ' ')}")
    if flagged:
        print(f"  ⚠ {flagged} 篇含金额/尺码/英制单位，需人工确认——"
              f"审校清单里会逐篇标出来。")
    if money_violated:
        print(f"  ❗ {money_violated} 篇的金额没有原样保留，已全部拒绝写盘并计为失败。")
        print(f"     金额必须原封不动（数值/小数点/符号/符号位置都不许改），"
              f"由人工替换成德国站定价。")
        print("     修正提示词/模型后直接重跑即可；这些 post_id 没被记成已完成。")
    if hashtag_violated:
        print(f"  ❗ {hashtag_violated} 篇的话题标签与原帖不完全一致，"
              "已全部拒绝写盘并计为失败。")
        print("     标签必须保持相同数量、内容、大小写与顺序；不得翻译、删减、新增或调序。")
        print("     直接重跑即可；这些 post_id 没被记成已完成。")
    return ok, bad


# --------------------------------------------------------------------------
# F3：人工审核清单
# --------------------------------------------------------------------------

def run_review(arc_base: Path) -> int:
    """生成 review.md：原文 / 译文 / 配图 / 图内英文待确认框。

    图片用相对路径引用，review.md 就放在同目录，所以 Markdown 预览器
    直接能显示——交给德语审校人时不用额外传文件。
    """
    arc = Archive(arc_base.parent, arc_base.name)
    row_list = [r for r in arc.rows()
                if isinstance(r, dict)
                and isinstance(r.get("post_id"), str) and r["post_id"].strip()
                and isinstance(r.get("text"), str)]
    rows = {r["post_id"]: r for r in row_list}
    # 目录名是 `<平台前缀>_<账号>`；用它判断一篇帖子是本账号原创还是合作帖
    this_account = arc_base.name.split("_", 1)[-1].strip().lower()
    trans = load_translated(arc_base / "translated.jsonl")
    if not trans:
        print(f"  {arc_base.name}：还没有译文，跳过（先跑一次翻译）")
        return 0
    # 延迟导入避免模块加载时形成 translate <-> localize_images 循环；这里只读
    # images_de.jsonl / media_de，不触发客户端构造、密钥读取或 API 调用。
    import localize_images as image_de
    image_state = image_de.load_image_state(arc_base / "images_de.jsonl")
    # [image] 配置只用来打一行形变/放大告警。**不能因为它不合法就让 F 组的
    # 审校清单整条命令跑不出来**（CR-56）：Settings() 会做双向配置审计并
    # SystemExit，而这个账号可能一张德语图都没有。
    try:
        image_settings = image_de.Settings()
    except SystemExit as exc:
        image_settings = None
        print(f"  ! [image] 配置当前不可用（{exc}）；"
              "K8 并排与逐类清单照常生成，只是不打印形变/放大告警")

    eligible = {pid for pid, r in rows.items() if (r.get("text") or "").strip()}
    orphan_ids = sorted(set(trans) - set(rows))
    translated_ids = {
        pid for pid in set(trans) & eligible
        if translation_is_current(rows[pid], trans[pid])
    }
    stale_ids = sorted((set(trans) & eligible) - translated_ids)
    missing_ids = eligible - translated_ids
    ordered = sorted(
        (trans[pid] for pid in translated_ids),
        key=lambda t: (rows.get(t["post_id"], {}).get("created_at") or "", t["post_id"]))

    n_flagged = sum(1 for t in ordered
                    if review_numeric_flags(rows[t["post_id"]].get("text") or "",
                                            t.get("text_de", "")))
    n_money_violated = sum(1 for t in ordered
                           if money_preserved(rows.get(t["post_id"], {}).get("text") or "",
                                              t.get("text_de", "")))
    n_hashtag_violated = sum(1 for t in ordered
                             if hashtags_preserved(
                                 rows.get(t["post_id"], {}).get("text") or "",
                                 t.get("text_de", "")))

    lines = [
        f"# 德语文案审校清单 · {arc_base.name}",
        "",
        f"生成时间：{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}　"
        f"可译 {len(eligible)} 篇 / 当前有效译文 {len(ordered)} 篇 / "
        f"待译 {len(missing_ids)} 篇（过期 {len(stale_ids)} 篇），"
        f"其中 **{n_flagged} 篇含需人工确认的数字**",
        "",
        "审校方式：逐篇看「德语译文」并勾选/批注。再次运行 `--review` 会重建本文件，"
        "程序会先把上一版保存为 `review.previous.md`；本文件不是译文真相源。",
        "",
        "两类必须人工处理的事：",
        "",
        "1. **数字**。提示词**刻意不换算**货币金额与数字尺码——德国站的定价与尺码"
        "对照是商务决策，模型无从知道，擅自换算就是把文案问题变成商业事故。"
        "含这类数字的帖子下面会标出来。",
        "2. **图内德语图**。程序产出、人工可覆盖；逐张并排核对德语正确性、"
        "不可改内容与产品外观。K3 预扫描已取消，因此下面的逐类人工清单是唯一验收口。",
        "",
    ]
    if orphan_ids:
        lines += [f"> ⚠️ `translated.jsonl` 有 {len(orphan_ids)} 条在当前 manifest 中找不到的"
                  "孤儿记录，本清单已忽略：" + "、".join(orphan_ids[:10]), ""]
    if stale_ids:
        lines += [f"> ⚠️ 有 {len(stale_ids)} 条译文对应旧版英文正文或旧提示词版本，"
                  "本清单已忽略；"
                  "普通重跑会只重译这些帖子：" + "、".join(stale_ids[:10]), ""]
    if missing_ids:
        lines += [f"> ℹ️ 当前是部分审校：还有 {len(missing_ids)} 篇正文尚未翻译。", ""]
    if n_money_violated:
        lines += [
            f"> ❗ **{n_money_violated} 篇的金额没有被原样保留**——模型没照提示词做，"
            f"这几篇下面标了 ❗，请优先核对。",
            "> 规则是：美元金额逐字符原样复制，`$49.99` 就得还是 `$49.99`，"
            "不许变成 `49,99 €`，也不许变成 `49,99 $`。",
            "",
        ]
    if n_hashtag_violated:
        lines += [
            f"> ❗ **{n_hashtag_violated} 篇的话题标签没有逐个原样照搬**——"
            "数量、内容、大小写或顺序与原帖不一致，请优先核对。",
            "",
        ]
    lines += ["---", ""]

    for i, t in enumerate(ordered, 1):
        pid = t["post_id"]
        src = rows.get(pid, {})
        created = (src.get("created_at") or "")[:10]
        lines += [f"## {i}. `{pid}`　{created}", ""]
        if src.get("permalink"):
            lines += [f"原帖：<{src['permalink']}>", ""]
        # 合作帖：它在本账号主页上，但**内容是别人创作的**。
        # 审校人需要知道这一点——二次发布到 DE Page 涉及的是对方的著作权，
        # 而译文本身看不出这个区别。
        if (src.get("owner")
                and str(src["owner"]).strip().lower() != this_account):
            lines += [f"> 🤝 **合作帖**：原作者是 `@{src['owner']}`，"
                      f"本账号是 coauthor。发布前确认二次使用授权。", ""]

        lines += ["**英文原文**", ""]
        lines += markdown_text_block(src.get("text") or "") + [""]
        lines += ["**德语译文**", ""]
        lines += markdown_text_block(t.get("text_de", "")) + [""]

        money_violations = money_preserved(src.get("text") or "", t.get("text_de", ""))
        hashtag_violations = hashtags_preserved(
            src.get("text") or "", t.get("text_de", ""))
        if money_violations:
            lines.append("> ❗ **金额没有原样保留——模型没照提示词做，这条要重点看**")
            lines += [f"> - {v}" for v in money_violations]
            lines.append("> - 规则：金额必须逐字符原样复制（数值/小数点/货币符号/"
                         "符号位置都不许改），德国站定价由人工替换")
            lines.append("")
        if hashtag_violations:
            lines.append("> ❗ **话题标签没有逐个原样照搬——这条要重点看**")
            lines += [f"> - {v}" for v in hashtag_violations]
            lines.append("")

        flags = review_numeric_flags(src.get("text") or "", t.get("text_de", ""))
        if flags:
            lines.append("> ⚠️ **需人工确认的数字**")
            lines += [f"> - {f}" for f in flags]
            lines.append("")

        media = [m for m in (src.get("media") or []) if m.get("kind") == "image"]
        local = [m for m in media if m.get("local_path")]
        if local:
            lines.append("**配图**")
            lines.append("")
            for m in local:
                p = str(m["local_path"]).replace("\\", "/")
                dim = (f"（{m.get('width')}×{m.get('height')}）"
                       if m.get("width") else "")
                lines.append(f"![{pid}]({p}) {dim}")
            lines.append("")
        elif media:
            lines += ["**配图**：有 %d 张，但本地文件缺失（media 未下载成功）" % len(media), ""]
        else:
            lines += ["**配图**：无", ""]

        if not src.get("media_complete", True):
            lines += ["> ⚠️ 该帖媒体不全（登出增量只拿到轮播封面），"
                      "配图可能少于实际。", ""]

        image_pairs = image_de.review_image_pairs(
            arc_base, src, t, state=image_state)
        if image_pairs:
            lines += ["**原图 / 德语图并排审校（K8）**", "",
                      "| 原图 | 德语图 |", "| --- | --- |"]
            for pair in image_pairs:
                source_ref = pair.source_rel.replace("\\", "/")
                original_cell = f"![{pid} 原图 {pair.media_index + 1}](<{source_ref}>)"
                if pair.localized_rel:
                    localized_ref = pair.localized_rel.replace("\\", "/")
                    kind = "人工覆盖" if pair.manual else "程序产出"
                    localized_cell = (
                        f"![{pid} 德语图 {pair.media_index + 1}](<{localized_ref}>)"
                        f"<br>{kind}")
                else:
                    localized_cell = "⚠️ **尚未生成德语图**"
                lines.append(f"| {original_cell} | {localized_cell} |")
            lines.append("")

            for pair in image_pairs:
                lines += [f"**第 {pair.media_index + 1} 张图逐类检查**", ""]
                if pair.record:
                    distance = pair.record.get("dhash_distance", "?")
                    drift = pair.record.get("aspect_drift_percent", "?")
                    scale = pair.record.get("scale_factor")
                    lines.append(
                        f"> 程序记录：size {pair.record.get('size_requested', '?')} → "
                        f"{pair.record.get('size_returned', '?')}；dHash 距离 {distance}；"
                        f"宽高比形变 {drift} %"
                        + (f"；放大 {scale}x" if scale is not None else "") + "。")
                    if (image_settings is not None and isinstance(drift, (int, float))
                            and drift > image_settings.aspect_drift_warn_percent):
                        lines.append(
                            f"> ⚠️ 宽高比形变超过配置阈值 "
                            f"{image_settings.aspect_drift_warn_percent:g} %，重点检查构图。")
                    if (image_settings is not None and isinstance(scale, (int, float))
                            and scale > image_settings.scale_warn_factor):
                        lines.append(
                            f"> ⚠️ 原图被放大 {scale}x（超过 "
                            f"{image_settings.scale_warn_factor:g}x）——"
                            "德语图是放大件，请确认清晰度可接受再勾选。")
                    lines.append("")
                elif pair.manual:
                    lines += ["> ℹ️ 当前德语图是人工覆盖版本，仍需逐类验收。", ""]
                else:
                    lines += ["> ⚠️ 先生成/补齐德语图，以下项目才能勾选。", ""]
                lines += [
                    "- [ ] 德语文字正确，且所有应译英文均已替换（无漏译/错译）",
                    "- [ ] 优惠码 / 折扣码逐字符未变",
                    "- [ ] 品牌名 / Logo / 商标逐字符未变",
                    "- [ ] 产品型号逐字符未变",
                    "- [ ] 合作方水印 / 署名 / 作者账号逐字符未变",
                    "- [ ] 金额 / 货币符号 / 小数点 / 符号位置逐字符未变",
                    "- [ ] 数值与单位逐字符未变，且没有换算",
                    "- [ ] 认证、合规与法律标记逐字符未变",
                    "- [ ] @提及 / #标签 / URL / 二维码 / 条码 / 人名地名未变",
                    "- [ ] 产品外观、人物、背景、构图、配色与非文字元素未变",
                    "",
                ]

        lines.append("- [ ] 译文已审校")
        if money_violations:
            lines.append("- [ ] **金额已核对回原文写法**（模型改动过，必查）")
        if hashtag_violations:
            lines.append("- [ ] **话题标签已恢复为与原帖完全一致**（模型改动过，必查）")
        if flags:
            lines.append("- [ ] 数字已按德国站确认/替换")
        lines += ["- [ ] 图内德语图已逐张完成 K8 审校（见上方逐类清单）",
                  "", "---", ""]

    out = arc_base / "review.md"
    backup = arc_base / "review.previous.md"
    assert_physical_direct_path(
        arc_base, out, kind="file", label="review.md")
    assert_physical_direct_path(
        arc_base, backup, kind="file", label="review.previous.md")
    backed_up = False
    if out.exists():
        out.replace(backup)
        backed_up = True
    out.write_text("\n".join(lines), encoding="utf-8")
    current_trans = {pid: trans[pid] for pid in translated_ids}
    n_de = sync_text_de(arc_base, row_list, current_trans)
    print(f"  {arc_base.name}：{out}　（{len(ordered)} 篇，"
          f"另写出 {n_de} 个 text_de.txt）")
    if backed_up:
        print(f"    上一版审校清单已备份：{backup.name}")
    return len(ordered)


def sync_text_de(arc_base: Path, rows: list[dict], trans: dict[str, dict]) -> int:
    """把译文同步一份 `text_de.txt` 到每帖文件夹里（J 组布局）。

    **`translated.jsonl` 仍然是译文的唯一真相源**，这里写的是**派生副本**。
    这条边界是刻意保留的：计划里已经拍板"译文写独立文件，重跑抓取不能冲掉
    花钱买来的结果"，把真相源挪进文件夹会动到那个决策，不值得为整洁去动它。

    副本的价值在人：设计同事拿到一个文件夹，里面英文、德文、配图齐全，
    不用再去翻一个几 MB 的 JSONL。删了也没关系，跑一次 --review 就回来了。
    """
    written = removed = 0
    for row in rows:
        # manifest 可能有脏行（run_review 明确承诺脏输入不崩）。
        # 这是个派生副本的生成器，没有任何理由成为整批的失败点。
        if not isinstance(row, dict) or not row.get("post_id"):
            continue
        entry = trans.get(row["post_id"])
        de = entry.get("text_de") if isinstance(entry, dict) else None
        current = bool(de and translation_is_current(row, entry))
        posts_dir = arc_base / "posts"
        d = posts_dir / post_dirname(row["post_id"], row.get("created_at"))
        try:
            assert_physical_direct_path(
                posts_dir, d, kind="directory", label="译文帖子目录")
        except ArchivePathError as exc:
            print(f"    ! 跳过不安全的 text_de.txt 目标：{exc}")
            continue
        if not d.is_dir():
            continue          # 还没迁移到新布局，或该帖文件夹被人删了
        text_de = d / "text_de.txt"
        try:
            assert_physical_direct_path(
                d, text_de, kind="file", label="text_de.txt")
        except ArchivePathError as exc:
            print(f"    ! 跳过不安全的 text_de.txt 目标：{exc}")
            continue
        if not current:
            if text_de.exists():
                try:
                    text_de.unlink()
                    removed += 1
                except OSError as exc:
                    print(f"    ! 无法移除过期的 text_de.txt：{exc}")
            continue
        text_de.write_text(de, encoding="utf-8")
        written += 1
    if removed:
        print(f"    - 已移除 {removed} 个过期 text_de.txt；旧付费记录仍保留在 translated.jsonl")
    return written


def markdown_text_block(text: str) -> list[str]:
    """把外部正文放进不会被其自身反引号提前闭合的 Markdown 围栏。"""
    runs = re.findall(r"`+", text or "")
    width = max(3, max((len(run) for run in runs), default=0) + 1)
    fence = "`" * width
    return [fence + "text", (text or "").rstrip(), fence]


# --------------------------------------------------------------------------
# 连通性自检
# --------------------------------------------------------------------------

def run_check(s: Settings,
              paid_controller: paid_requests.RequestController | None = None) -> int:
    """--check：用一次极小的请求验证 API 配置对不对。

    把"密钥错了"、"base_url 错了"、"模型名端点不认"等常见配置错误
    分别报出来，而不是让人对着一个状态码猜。
    """
    print("=== 翻译 API 自检 ===")
    print(f"  provider    : {s.provider}")
    print(f"  base_url    : {s.base_url}")
    print("  API 格式    : DeepSeek 官方 OpenAI 兼容 Chat Completions")
    print("  鉴权        : Authorization: Bearer（由 SDK 设置）")
    print(f"  密钥环境变量: {s.api_key_env} = {s.redacted()}")
    print(f"  model       : {s.model}")
    print(f"  thinking    : enabled / effort={s.reasoning_effort}")
    print("  输出上限    : 未发送 max_tokens 或同类限制")
    print()

    import openai
    translator = Translator(s, paid_controller=paid_controller)
    try:
        text = translator.translate(
            "Compute 17 * 19 silently. If the result is 323, reply with exactly OK.",
            "This is a connectivity check. Return exactly OK and nothing else.")
    except ModelMismatchError as e:
        print(f"[!] 模型回退：{e}")
        return 1
    except openai.APIConnectionError as e:
        print(f"[!] 连不上：{e}")
        print("    检查 base_url 是否可达、是否需要公司 VPN 或代理。")
        return 1
    except openai.APIStatusError as e:
        status = getattr(e, "status_code", None)
        if status == 401:
            print("[!] 401 鉴权失败。确认 .env 中的 "
                  f"{s.api_key_env} 来自 DeepSeek API 控制台。")
        elif status == 402:
            print("[!] 402 账户余额不足。请先到 DeepSeek API 控制台充值，再从断点重跑。")
        elif status == 403:
            print("[!] 403 无权限。密钥有效但没有调用该模型的权限。")
        elif status == 404:
            print(f"[!] 404。DeepSeek 端点路径不对（当前：{s.base_url}）。")
            print(f"    官方 OpenAI 兼容 base_url 必须是 {DEEPSEEK_API_URL}")
        elif status == 400:
            print(f"[!] 400 请求被拒：{e}")
            print("    检查 reasoning_effort；自检与正式翻译使用的是同一请求形态。")
        elif status == 422:
            print(f"[!] 422 参数语义不被端点接受：{e}")
        elif status == 429:
            print(f"[!] 429 请求过快或并发额度已满：{e}")
            print("    稍后再试；不要连续重复双击翻译脚本。")
        elif isinstance(status, int) and status >= 500:
            print(f"[!] DeepSeek 服务暂时不可用（HTTP {status}）：{e}")
            print("    已停止，不会继续消耗整批请求；稍后从断点重跑。")
        else:
            print(f"[!] API HTTP {status or '?'}：{e}")
        return 1
    except Exception as e:
        print(f"[!] {type(e).__name__}: {e}")
        return 1

    print(f"[ok] API 连通。模型回了：{text.strip()!r}")
    print_usage_summary(s, translator.last_usage)
    print(f"实际响应的 model 字段：{translator.last_model or '?'}")

    reasoning_tokens = translator.last_usage.get("reasoning_tokens", 0)
    if "reasoning" not in translator.last_blocks and reasoning_tokens <= 0:
        print()
        print("[!] 请求已发送 thinking=enabled / reasoning_effort="
              f"{s.reasoning_effort}，但响应没有可验证的 reasoning_content/token。")
        print("    为避免假绿灯，先核对 DeepSeek 端点和模型支持情况，再开始整批翻译。")
        translator.finalize_paid(False, "connectivity check could not verify thinking")
        return 1
    print(f"thinking：已确认启用，effort={s.reasoning_effort}"
          + (f"，本次 {reasoning_tokens} reasoning tok" if reasoning_tokens else ""))
    translator.finalize_paid(True, "connectivity check verified")
    return 0


# --------------------------------------------------------------------------

def run_estimate(s: Settings, dirs: list[Path], limit: int | None,
                 force: bool, scope: frozenset[str] | None = None) -> int:
    """不联网，按真实待译正文与当前 prompt 给出保守 token/费用区间。

    ⚠️ **thinking 开着时，字符换算出来的数字会严重低估。**
    2026-08-31 实测：一篇 1261 字符的帖子，可见译文 420 tok，
    而 reasoning **9899 tok** —— 98% 的输出费用花在看不见的地方。
    只按正文长度估，会给出一个偏低一到两个数量级的预算。

    所以这里**优先用已译帖子的真实 usage 外推**：每篇的 usage 已经写进
    `translated.jsonl`，试跑 3 篇之后就有了真实的每篇 reasoning 量。
    没有任何已译记录时才退回字符换算，并明确标注它不含 reasoning。
    """
    total_posts = input_all_miss = input_cached = cache_hit = output_est = 0
    measured: list[dict] = []          # 已译帖子的真实 usage，用于外推
    per_account: list[tuple[str, int, list[dict]]] = []
    remaining = limit
    for arc_base in dirs:
        if remaining == 0:
            break
        arc = Archive(arc_base.parent, arc_base.name)
        rows = arc.rows()
        done = load_translated(arc_base / "translated.jsonl")
        todo = pending(rows, done, force, scope)
        if remaining is not None:
            todo = todo[:remaining]
        # 只收**当前提示词版本**的 usage：换了提示词，思考量不可比
        seen_usage = [r["usage"] for r in done.values()
                      if isinstance(r.get("usage"), dict)
                      and r.get("prompt_version") == PROMPT_VERSION]
        measured.extend(seen_usage)
        per_account.append((arc_base.name, len(todo), seen_usage))
        owner = arc_base.name.split("_", 1)[-1].strip().lower()
        examples = pick_style_examples(rows, s.style_examples, owner=owner)
        system_tokens = _approx_tokens(build_system_prompt(s, examples))
        text_tokens = sum(_approx_tokens((r.get("text") or "").strip()) for r in todo)
        n = len(todo)
        # DeepSeek 的磁盘缓存自动工作且要求相同前缀；按前两次构建缓存估一个乐观场景。
        warmups = min(2, n)
        input_all_miss += text_tokens + n * system_tokens
        input_cached += text_tokens + warmups * system_tokens
        cache_hit += max(0, n - warmups) * system_tokens
        output_est += round(text_tokens * 1.25)  # 德语通常比英文略长；只是预算估值
        total_posts += n
        if remaining is not None:
            remaining -= n
        print(f"  {arc_base.name}: {n} 篇 / system prompt 约 {system_tokens} tok")

    print(f"\n待译合计：{total_posts} 篇")
    print(f"粗估 token：输入 {input_all_miss:,}（全未命中）/ "
          f"{input_cached:,} 未命中 + {cache_hit:,} 命中（缓存生效）/ "
          f"可见译文输出约 {output_est:,}")
    worst = usage_cost_upper_bound(s, {
        "input_tokens": input_all_miss, "output_tokens": output_est})
    cached = usage_cost_upper_bound(s, {
        "input_tokens": input_cached, "prompt_cache_hit_tokens": cache_hit,
        "output_tokens": output_est})
    if worst is not None and cached is not None:
        print(f"基础费用参考：约 US${cached:.2f}–US${worst:.2f} "
              "（只按正文长度算，**不含 thinking 的 reasoning 输出**）")

    if not measured:
        print("\n还没有任何当前提示词版本的译文，无法估计 reasoning 开销。"
              "\n先分账号各试跑 3 篇，再跑一次本命令 —— 那时会用真实 usage 外推。")
        print("注意：这是离线预算，不会调用 API，也不会产生费用。")
        return 0

    # 用真实 usage 外推。取**中位数**而不是均值：单篇思考量长尾很重，
    # 一篇特别难的帖子会把均值拉偏，而预算要的是"典型值 × 篇数"。
    print("\n--- 按已译 %d 篇的**真实 usage** 外推（thinking=%s）---"
          % (len(measured), s.reasoning_effort))
    grand = 0.0
    for name, n_todo, seen in per_account:
        if not n_todo:
            continue
        sample = seen or measured           # 该账号还没试跑过就借全局样本
        out_med = statistics.median(u.get("output_tokens", 0) for u in sample)
        rea_med = statistics.median(u.get("reasoning_tokens", 0) for u in sample)
        in_med = statistics.median(
            u.get("prompt_cache_miss_tokens", u.get("input_tokens", 0))
            for u in sample)
        hit_med = statistics.median(u.get("prompt_cache_hit_tokens", 0)
                                    for u in sample)
        per_post = usage_cost_upper_bound(s, {
            "prompt_cache_miss_tokens": int(in_med),
            "prompt_cache_hit_tokens": int(hit_med),
            "output_tokens": int(out_med)})
        if per_post is None:
            continue
        grand += per_post * n_todo
        share = (rea_med / out_med * 100) if out_med else 0.0
        print("  %-24s 待译 %5d 篇 × 每篇约 US$%.4f = US$%7.2f"
              % (name, n_todo, per_post, per_post * n_todo))
        print("  %-24s   （中位：输出 %d tok，其中 reasoning %d tok = %.0f%%%s）"
              % ("", int(out_med), int(rea_med), share,
                 "；样本借自其它账号" if not seen else ""))
    print("  %-24s **合计约 US$%.2f**" % ("", grand))
    print("\n⚠️ reasoning 占了输出的绝大部分。想压这笔钱，把 config.toml 的"
          " [translate].reasoning_effort 从 high 调到 low —— "
          "实测同一篇帖子 reasoning 从 9899 tok 降到 373 tok（费用 1/4），"
          "但德语排版细节（„…“ 引号、句中大小写）会变差。这是业务取舍，代码不替你定。")
    print("注意：这是离线预算，不会调用 API，也不会产生费用。")
    return 0


# --------------------------------------------------------------------------

def main(argv=None) -> int:
    # 本模块打 ❗金额被改动 —— 全项目最重要的一条安全信号。cp936 编不出这个字符，
    # 输出一旦被重定向就会崩在那一行，正好是最需要它出现的时候。
    force_utf8()

    ap = argparse.ArgumentParser(
        description="把归档的英文文案翻译成德语（F 组）",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true",
                    help="用一次极小请求验证 API 配置（会产生少量 token，不翻译归档）")
    ap.add_argument("--show-prompt", action="store_true",
                    help="打印实际发给模型的 system prompt（渲染后），不翻译")
    ap.add_argument("--estimate", action="store_true",
                    help="按当前待译正文粗估 token/费用，不调用 API")
    ap.add_argument("--review", action="store_true",
                    help="用已有译文生成人工审核清单 review.md")
    ap.add_argument("--limit", type=int, default=None,
                    help="所有账号合计最多翻译 N 篇，用于小批量试跑")
    ap.add_argument("--account", default=None,
                    help="只处理 archive/ 下的这一个目录名，如 in_acme")
    ap.add_argument("--force", action="store_true",
                    help="即使正文与提示词版本都有效也强制重翻（同版本重试时用）")
    ap.add_argument("--dry-run", action="store_true",
                    help="只列出将要翻译哪些帖子，不调用 API")
    # 作用域（CR-47）。待译队列是**最老优先**的，所以 --limit 到不了最新那几篇；
    # K9 / G8 的验收标的恰恰是最新几篇。形状与 localize_images.py 保持一致。
    scope_group = ap.add_mutually_exclusive_group()
    scope_group.add_argument(
        "--post-id", action="append", default=None,
        help="只翻指定 post_id（可重复传入）；K9/G8 验收就用这个精确补译文")
    scope_group.add_argument(
        "--latest-posts", type=int, default=None,
        help="全账号合计只选最新 N 篇（与 localize_images.py 的同名参数同义）")
    a = ap.parse_args(argv)
    if a.limit is not None and a.limit < 0:
        ap.error("--limit 不能为负数")
    if a.latest_posts is not None and a.latest_posts < 1:
        ap.error("--latest-posts 必须是正整数")

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")

    s = Settings()
    if a.check:
        return run_check(
            s, paid_controller=paid_requests.RequestController(cfg().state_dir))

    root = cfg().archive_dir
    dirs = account_dirs(root, a.account)

    if a.account and not dirs:
        avail = [p.name for p in account_dirs(root)]
        print(f"[!] archive/ 下没有名为 {a.account!r} 的账号目录。")
        print("    现有：%s" % (", ".join(avail) if avail else "（一个都没有，先跑回填）"))
        return 1

    if a.show_prompt:
        # 没有归档也要能看：让人在回填之前就能审提示词本身
        if dirs:
            for d in dirs:
                arc = Archive(d.parent, d.name)
                owner = d.name.split("_", 1)[-1].strip().lower()
                examples = pick_style_examples(arc.rows(), s.style_examples, owner=owner)
                print(f"\n{'=' * 72}\n账号：{d.name}（仅使用 @{owner} 自有正文，"
                      f"{len(examples)} 篇风格参照）\n{'=' * 72}")
                print(build_system_prompt(s, examples))
        else:
            print("（尚无归档，风格参照为空；回填后重跑本命令可看到完整版）\n")
            print("=" * 72)
            print(build_system_prompt(s, []))
            print("=" * 72)
        print(f"\n模板文件：{TEMPLATE_PATH}")
        print(f"提示词版本：{PROMPT_VERSION}　"
              f"（改了模板记得把 translate.py 的 PROMPT_VERSION +1）")
        return 0

    if not dirs:
        print(f"[!] {root} 下没有找到任何含 manifest.jsonl 的账号目录。")
        print("    先跑回填（scripts\\run_backfill.bat facebook）把内容抓下来。")
        return 1

    try:
        scope = resolve_scope(dirs, post_ids=a.post_id,
                              latest_posts=a.latest_posts)
    except SourceDataError as e:
        print(f"[!] {e}")
        return 1
    if scope is not None:
        print(f"作用域：只处理 {len(scope)} 个 post_id —— "
              + "、".join(sorted(scope)))
        print("（纯视频/无正文帖会被自然跳过，不会替换成别的帖子）")

    if a.estimate:
        print("=== DeepSeek 翻译费用离线预算 ===")
        try:
            return run_estimate(s, dirs, a.limit, a.force, scope)
        except SourceDataError as e:
            print(f"[!] {e}")
            return 1

    if a.review:
        print("=== 生成审核清单 ===")
        total = sum(run_review(d) for d in dirs)
        print(f"\n共 {total} 篇进入审核清单。")
        return 0

    translator = Translator(
        s, paid_controller=(
            None if a.dry_run
            else paid_requests.RequestController(cfg().state_dir)))
    ok = bad = 0
    remaining = a.limit
    lock = nullcontext() if a.dry_run else TranslationRunLock(cfg().state_dir / "translate.lock")
    try:
        with lock:
            for d in dirs:
                if remaining == 0:
                    break
                o, b = run_translate(s, translator, d, remaining, a.force,
                                     a.dry_run, scope)
                ok, bad = ok + o, bad + b
                if remaining is not None:
                    remaining -= o + b
    except FatalBatchError as e:
        print(f"\n[!] {e}")
        if translator.usage_totals:
            print_usage_summary(s, dict(translator.usage_totals))
        return 1

    if a.dry_run:
        print(f"\n预览完成：列出 {ok} 篇待译正文，零 API 调用。")
    else:
        print(f"\n完成：成功 {ok} 篇，失败 {bad} 篇。")
        print_usage_summary(s, dict(translator.usage_totals))
    if bad:
        print("[!] 有失败项。上面每条都带了 post_id 与原因，修掉后重跑即可"
              "（已成功的不会重复翻译）。")
        return 1
    if ok and not a.dry_run:
        print("下一步：scripts\\run_translate.bat --review 生成人工审核清单。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
