r"""英文文案 → 德语翻译。对应实施计划 F 组（F1 主流程 / F2 风格 few-shot / F3 审核清单）。

用法（前置：scripts\setup.bat 已跑过，且已设好 API Key 环境变量）：

    scripts\run_translate.bat --check              先验证网关能通（不消耗正式流程）
    scripts\run_translate.bat --limit 3            小批量试跑
    scripts\run_translate.bat                      翻译全部未翻译的帖子
    scripts\run_translate.bat --review             生成人工审核清单 review.md

**抓取产物不可变**：本模块只读 manifest.jsonl，译文写进同目录的
translated.jsonl。manifest 是重跑抓取就能重现的事实，译文是花钱买的加工结果，
两者混在一起会导致"重抓一次把译文冲掉"。

**API 走公司内部兼容 Anthropic Messages 的网关**：用官方 anthropic SDK 加
base_url 覆盖，而不是自己拼 HTTP —— SDK 自带 429/5xx 指数退避与分类异常，
自己写一遍只会写得更差。默认**不发送** thinking / effort / temperature，
因为兼容网关未必认这些较新的参数；需要时在 config.toml 里显式打开。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from core.config import cfg                        # noqa: E402
from core.console import force_utf8                # noqa: E402
from core.store import Archive, post_dirname       # noqa: E402

# 提示词模板。放在独立文件里，改翻译行为不用改 Python，营销同事也能改。
TEMPLATE_PATH = ROOT / "prompts" / "translate_de.md"

# 提示词版本。改了提示词就把它 +1：译文行里记着这个值，
# 于是"这批译文是旧提示词产出的"变成可查的事实，而不是靠记忆。
PROMPT_VERSION = 3

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
        self.base_url: str = (g("base_url", "") or "").strip()
        self.api_key_env: str = (g("api_key_env", "ANTHROPIC_API_KEY") or "").strip()
        self.auth_style: str = (g("auth_style", "x-api-key") or "x-api-key").strip().lower()
        self.model: str = (g("model", "claude-opus-5") or "").strip()
        self.extra_headers: dict = dict(g("extra_headers", {}) or {})
        self.max_tokens: int = int(g("max_tokens", 4096))
        self.timeout: float = float(g("timeout_seconds", 120))
        self.max_retries: int = int(g("max_retries", 2))
        self.gap: float = float(g("request_gap_seconds", 1.0))
        self.effort = g("effort", None)
        self.temperature = g("temperature", None)
        self.prompt_cache: bool = bool(g("prompt_cache", False))
        self.style_examples: int = int(g("style_examples", 6))
        self.tone: str = (g("tone", "") or "").strip()
        # 品牌语域决策。渲染进提示词，模型无法自行知道这些。
        self.address_form: str = (g("address_form", "du") or "du").strip()
        self.gender_style: str = (g("gender_style", "neutral") or "neutral").strip()
        self.anglicism_policy: str = (g("anglicism_policy", "moderate")
                                      or "moderate").strip()
        self.glossary: dict = dict(g("glossary", {}) or {})

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
                f"  【推荐】在 {ROOT} 下新建 .env 文件，内容一行：\n"
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
    """构造 anthropic 客户端，指向配置里的 base_url。

    鉴权风格两选一：官方用 x-api-key，不少内部网关用 Authorization: Bearer。
    SDK 对这两种分别是 api_key= 和 auth_token= 两个参数。
    """
    import anthropic

    kw = {
        "timeout": s.timeout,
        "max_retries": s.max_retries,
    }
    if s.base_url:
        kw["base_url"] = s.base_url
    if s.extra_headers:
        kw["default_headers"] = s.extra_headers
    if s.auth_style not in {"x-api-key", "bearer"}:
        raise SystemExit(
            f"config.toml 的 [translate].auth_style = {s.auth_style!r} 不认识。"
            f"可选值：x-api-key, bearer")
    if s.auth_style == "bearer":
        kw["auth_token"] = s.api_key()
    else:
        kw["api_key"] = s.api_key()
    return anthropic.Anthropic(**kw)


# --------------------------------------------------------------------------
# F2：风格 few-shot
# --------------------------------------------------------------------------

def pick_style_examples(rows: list[dict], n: int, exclude_id: str | None = None) -> list[str]:
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
    texts = [(r.get("text") or "").strip() for r in rows
             if r.get("post_id") != exclude_id]
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
    return "\n\n".join(f"**参照 {i}**\n```\n{ex}\n```"
                       for i, ex in enumerate(examples, 1))


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

    left = [k for k in subs if k in tpl]
    if left:
        raise SystemExit(f"模板渲染后仍有未替换的占位符：{left}")
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


# 一个完整的金额 token：符号在前（$49.99）或在后（49,99 € / 50 USD）。
# 用 \d+(?:[.,]\d+)* 而不是 [\d.,]* ——后者会把句尾的句号也吃进来，
# 导致"原样出现"的比对因为一个标点而误报。
_MONEY_TOKEN_RE = re.compile(
    r"[$€£¥]\s?\d+(?:[.,]\d+)*"
    r"|\d+(?:[.,]\d+)*\s?(?:(?:USD|EUR|Dollar|Euro)\b|[$€£¥](?!\w))",
    re.I)


def _norm_money(tok: str) -> str:
    """比对用的归一化：只去掉空白。数值、分隔符、符号、符号位置都要求原样。"""
    return re.sub(r"\s+", "", tok)


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


class Translator:
    """一次翻译调用。抽成类是为了测试能整体替换掉它，不必打桩到 SDK 内部。"""

    def __init__(self, settings: Settings, client=None) -> None:
        self.s = settings
        self._client = client
        self._last_call = 0.0

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

    def translate(self, text: str, system: str) -> str:
        self._pace()
        kw = {
            "model": self.s.model,
            "max_tokens": self.s.max_tokens,
            "system": system,
            "messages": [{"role": "user", "content": text}],
        }
        # 这几项默认不发。兼容网关未必认；官方 Opus 5 上不发 thinking 即自适应。
        if self.s.effort:
            kw["output_config"] = {"effort": self.s.effort}
        if self.s.temperature is not None:
            kw["temperature"] = float(self.s.temperature)
        if self.s.prompt_cache:
            # system prompt 每篇都一样（风格示例每账号只算一次），缓存命中率应接近 100%。
            # 兼容网关未必支持 cache_control，所以默认关闭。
            kw["cache_control"] = {"type": "ephemeral"}

        resp = self.client.messages.create(**kw)

        # 截断必须显式报错。默默返回半句德语，人工审校时未必看得出来。
        if getattr(resp, "stop_reason", None) == "max_tokens":
            raise RuntimeError(
                f"译文被 max_tokens={self.s.max_tokens} 截断，"
                f"请调大 config.toml 的 [translate].max_tokens 后重跑")
        if getattr(resp, "stop_reason", None) == "refusal":
            detail = getattr(resp, "stop_details", None)
            raise RuntimeError(f"模型拒绝了这条内容（stop_details={detail}）")

        out = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
        out = _strip_wrapper(out)
        if not out.strip():
            raise RuntimeError("模型返回了空译文")
        return out


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
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
                out[r["post_id"]] = r
            except (json.JSONDecodeError, KeyError, TypeError):
                # TypeError：整行是合法 JSON 但不是对象（如数组），下标取不到
                continue
    return out


def pending(rows: list[dict], done: dict[str, dict], force: bool) -> list[dict]:
    """待翻译：正文非空、且尚未翻译过。"""
    out = []
    for r in rows:
        if not (r.get("text") or "").strip():
            continue
        if not force and r.get("post_id") in done:
            continue
        out.append(r)
    # 按时间正序翻，产出顺序稳定，便于人工按时间线审校
    out.sort(key=lambda r: r.get("created_at") or "")
    return out


def run_translate(s: Settings, translator: Translator, arc_base: Path,
                  limit: int | None, force: bool, dry_run: bool) -> tuple[int, int]:
    """翻译一个账号目录。返回 (成功数, 失败数)。"""
    arc = Archive(arc_base.parent, arc_base.name)
    rows = arc.rows()
    out_path = arc_base / "translated.jsonl"
    done = load_translated(out_path)
    todo = pending(rows, done, force)
    if limit is not None:
        todo = todo[:limit]

    print(f"\n=== {arc_base.name} ===")
    print(f"  归档 {len(rows)} 篇 / 已译 {len(done)} 篇 / 本次待译 {len(todo)} 篇")
    if not todo:
        print("  没有需要翻译的帖子。")
        return 0, 0

    # system prompt 每个账号只构建一次：内容对所有帖子相同，
    # 既能命中提示词缓存，也保证篇与篇之间的语域基准完全一致。
    examples = pick_style_examples(rows, s.style_examples)
    system = build_system_prompt(s, examples)
    print(f"  提示词：{len(system)} 字符 / {len(examples)} 篇风格参照 / "
          f"称呼 {s.address_form} / 术语表 {len(s.glossary)} 条")

    ok = bad = flagged = violated = 0
    for i, r in enumerate(todo, 1):
        pid = r.get("post_id", "?")
        text = (r.get("text") or "").strip()

        if dry_run:
            print(f"  [{i}/{len(todo)}] {pid}  (dry-run，不调用 API)  "
                  f"{text[:44].replace(chr(10), ' ')}")
            ok += 1
            continue

        try:
            de = translator.translate(text, system)
        except Exception as e:
            # 单条失败跳过，不中断整批；但必须带 post_id 打出来，不得静默吞掉
            print(f"  [{i}/{len(todo)}] {pid}  失败：{type(e).__name__}: {e}")
            bad += 1
            continue

        row = {
            "post_id": pid,
            "text_de": de,
            "translated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "model": s.model,
            "prompt_version": PROMPT_VERSION,
        }
        with out_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
        ok += 1
        violations = money_preserved(text, de)
        flags = numeric_flags(de)
        if flags:
            flagged += 1
        if violations:
            violated += 1
        mark = "  ❗金额被改动" if violations else ("  ⚠需人工确认" if flags else "")
        print(f"  [{i}/{len(todo)}] {pid}  OK  "
              f"{len(text)}→{len(de)} 字符{mark}  {de[:36].replace(chr(10), ' ')}")
        for v in violations:
            print(f"        {v}")

    if flagged:
        print(f"  ⚠ {flagged} 篇含金额/尺码/英制单位，需人工确认——"
              f"审校清单里会逐篇标出来。")
    if violated:
        print(f"  ❗ {violated} 篇的金额**没有原样保留**，模型没听提示词。")
        print(f"     金额必须原封不动（数值/小数点/符号/符号位置都不许改），"
              f"由人工替换成德国站定价。")
        print(f"     处理：先看 review.md 里标红的那几篇；确认是模型的问题就用"
              f" --force 重译，仍不行告诉我，我加强提示词。")
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
    rows = {r["post_id"]: r for r in arc.rows()}
    # 目录名是 `<平台前缀>_<账号>`；用它判断一篇帖子是本账号原创还是合作帖
    this_account = arc_base.name.split("_", 1)[-1].strip().lower()
    trans = load_translated(arc_base / "translated.jsonl")
    if not trans:
        print(f"  {arc_base.name}：还没有译文，跳过（先跑一次翻译）")
        return 0

    ordered = sorted(trans.values(),
                     key=lambda t: (rows.get(t["post_id"], {}).get("created_at") or ""))

    n_flagged = sum(1 for t in ordered if numeric_flags(t.get("text_de", "")))
    n_violated = sum(1 for t in ordered
                     if money_preserved(rows.get(t["post_id"], {}).get("text") or "",
                                        t.get("text_de", "")))

    lines = [
        f"# 德语文案审校清单 · {arc_base.name}",
        "",
        f"生成时间：{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}　"
        f"共 {len(ordered)} 篇，其中 **{n_flagged} 篇含需人工确认的数字**",
        "",
        "审校方式：逐篇看「德语译文」，直接在本文件里改。",
        "",
        "两类必须人工处理的事：",
        "",
        "1. **数字**。提示词**刻意不换算**货币金额与数字尺码——德国站的定价与尺码"
        "对照是商务决策，模型无从知道，擅自换算就是把文案问题变成商业事故。"
        "含这类数字的帖子下面会标出来。",
        "2. **图内英文文字**。本期走人工处理，勾上对应的框由设计同事替换。",
        "",
    ]
    if n_violated:
        lines += [
            f"> ❗ **{n_violated} 篇的金额没有被原样保留**——模型没照提示词做，"
            f"这几篇下面标了 ❗，请优先核对。",
            "> 规则是：美元金额逐字符原样复制，`$49.99` 就得还是 `$49.99`，"
            "不许变成 `49,99 €`，也不许变成 `49,99 $`。",
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
        if src.get("owner") and src["owner"] != this_account:
            lines += [f"> 🤝 **合作帖**：原作者是 `@{src['owner']}`，"
                      f"本账号是 coauthor。发布前确认二次使用授权。", ""]

        lines += ["**英文原文**", "", "```text", (src.get("text") or "").rstrip(), "```", ""]
        lines += ["**德语译文**", "", "```text", t.get("text_de", "").rstrip(), "```", ""]

        violations = money_preserved(src.get("text") or "", t.get("text_de", ""))
        if violations:
            lines.append("> ❗ **金额没有原样保留——模型没照提示词做，这条要重点看**")
            lines += [f"> - {v}" for v in violations]
            lines.append("> - 规则：金额必须逐字符原样复制（数值/小数点/货币符号/"
                         "符号位置都不许改），德国站定价由人工替换")
            lines.append("")

        flags = numeric_flags(t.get("text_de", ""))
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

        lines.append("- [ ] 译文已审校")
        if violations:
            lines.append("- [ ] **金额已核对回原文写法**（模型改动过，必查）")
        if flags:
            lines.append("- [ ] 数字已按德国站确认/替换")
        lines += ["- [ ] 图内含英文文字，需人工替换", "", "---", ""]

    out = arc_base / "review.md"
    out.write_text("\n".join(lines), encoding="utf-8")
    n_de = sync_text_de(arc_base, rows, trans)
    print(f"  {arc_base.name}：{out}　（{len(ordered)} 篇，"
          f"另写出 {n_de} 个 text_de.txt）")
    return len(ordered)


def sync_text_de(arc_base: Path, rows: list[dict], trans: dict[str, dict]) -> int:
    """把译文同步一份 `text_de.txt` 到每帖文件夹里（J 组布局）。

    **`translated.jsonl` 仍然是译文的唯一真相源**，这里写的是**派生副本**。
    这条边界是刻意保留的：计划里已经拍板"译文写独立文件，重跑抓取不能冲掉
    花钱买来的结果"，把真相源挪进文件夹会动到那个决策，不值得为整洁去动它。

    副本的价值在人：设计同事拿到一个文件夹，里面英文、德文、配图齐全，
    不用再去翻一个几 MB 的 JSONL。删了也没关系，跑一次 --review 就回来了。
    """
    written = 0
    for row in rows:
        # manifest 可能有脏行（run_review 明确承诺脏输入不崩）。
        # 这是个派生副本的生成器，没有任何理由成为整批的失败点。
        if not isinstance(row, dict) or not row.get("post_id"):
            continue
        entry = trans.get(row["post_id"])
        de = entry.get("text_de") if isinstance(entry, dict) else None
        if not de:
            continue
        d = arc_base / "posts" / post_dirname(row["post_id"], row.get("created_at"))
        if not d.is_dir():
            continue          # 还没迁移到新布局，或该帖文件夹被人删了
        (d / "text_de.txt").write_text(de, encoding="utf-8")
        written += 1
    return written


# --------------------------------------------------------------------------
# 连通性自检
# --------------------------------------------------------------------------

def run_check(s: Settings) -> int:
    """--check：用一次极小的请求验证网关配置对不对。

    把"密钥错了"、"base_url 错了"、"模型名网关不认"、"鉴权风格选反了"
    这几种最常见的配置错误分别报出来，而不是让人对着一个 401 猜。
    """
    print("=== 翻译网关自检 ===")
    print(f"  base_url    : {s.base_url or '(空 → Anthropic 官方 api.anthropic.com)'}")
    print(f"  auth_style  : {s.auth_style}")
    print(f"  密钥环境变量: {s.api_key_env} = {s.redacted()}")
    print(f"  model       : {s.model}")
    if s.extra_headers:
        print(f"  额外请求头  : {', '.join(s.extra_headers)}")
    print()

    import anthropic
    try:
        client = build_client(s)
        resp = client.messages.create(
            model=s.model, max_tokens=16,
            messages=[{"role": "user", "content": "Reply with the single word: OK"}],
        )
    except anthropic.AuthenticationError:
        print("[!] 401 鉴权失败。密钥不对，或鉴权风格选反了。")
        print(f"    当前是 auth_style = \"{s.auth_style}\"，"
              f"试试改成 \"{'bearer' if s.auth_style != 'bearer' else 'x-api-key'}\"。")
        return 1
    except anthropic.PermissionDeniedError:
        print("[!] 403 无权限。密钥有效但没有调用该模型的权限，找网关管理员确认。")
        return 1
    except anthropic.NotFoundError:
        print("[!] 404。多半是两件事之一：")
        print(f"    1. model 名字网关不认（当前：{s.model}）——问网关方要准确的模型名")
        print(f"    2. base_url 写错了（当前：{s.base_url or '官方'}）——"
              "应填到 /v1 的上一级，SDK 自己会拼 /v1/messages")
        return 1
    except anthropic.BadRequestError as e:
        print(f"[!] 400 请求被拒：{e}")
        print("    若提示某个参数不认识，检查 config.toml 里是否开了 effort / temperature——"
              "兼容网关未必支持，注释掉再试。")
        return 1
    except anthropic.APIConnectionError as e:
        print(f"[!] 连不上：{e}")
        print(f"    检查 base_url 是否可达、是否需要公司 VPN 或代理。")
        return 1
    except Exception as e:
        print(f"[!] {type(e).__name__}: {e}")
        return 1

    text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
    print(f"[ok] 网关连通。模型回了：{text.strip()!r}")
    usage = getattr(resp, "usage", None)
    if usage:
        print(f"     用量：输入 {getattr(usage, 'input_tokens', '?')} tok / "
              f"输出 {getattr(usage, 'output_tokens', '?')} tok")
    print(f"     实际响应的 model 字段：{getattr(resp, 'model', '?')}")
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
                    help="只验证 API 网关配置是否正确，不翻译")
    ap.add_argument("--show-prompt", action="store_true",
                    help="打印实际发给模型的 system prompt（渲染后），不翻译")
    ap.add_argument("--review", action="store_true",
                    help="用已有译文生成人工审核清单 review.md")
    ap.add_argument("--limit", type=int, default=None,
                    help="所有账号合计最多翻译 N 篇，用于小批量试跑")
    ap.add_argument("--account", default=None,
                    help="只处理 archive/ 下的这一个目录名，如 in_acme")
    ap.add_argument("--force", action="store_true",
                    help="已翻译过的也重翻（改过提示词后用）")
    ap.add_argument("--dry-run", action="store_true",
                    help="只列出将要翻译哪些帖子，不调用 API")
    a = ap.parse_args(argv)
    if a.limit is not None and a.limit < 0:
        ap.error("--limit 不能为负数")

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")

    s = Settings()
    if a.check:
        return run_check(s)

    root = cfg().archive_dir
    dirs = account_dirs(root, a.account)

    if a.show_prompt:
        # 没有归档也要能看：让人在回填之前就能审提示词本身
        examples: list[str] = []
        if dirs:
            arc = Archive(dirs[0].parent, dirs[0].name)
            examples = pick_style_examples(arc.rows(), s.style_examples)
            print(f"（风格参照取自 {dirs[0].name}，共 {len(examples)} 篇）\n")
        else:
            print("（尚无归档，风格参照为空；回填后重跑本命令可看到完整版）\n")
        print("=" * 72)
        print(build_system_prompt(s, examples))
        print("=" * 72)
        print(f"\n模板文件：{TEMPLATE_PATH}")
        print(f"提示词版本：{PROMPT_VERSION}　"
              f"（改了模板记得把 translate.py 的 PROMPT_VERSION +1）")
        return 0

    if not dirs:
        if a.account:
            # 报"先跑回填"会把人引向错误的方向——真实原因多半是把目录名打错了
            avail = [p.name for p in account_dirs(root)]
            print(f"[!] archive/ 下没有名为 {a.account!r} 的账号目录。")
            print("    现有：%s" % (", ".join(avail) if avail else "（一个都没有，先跑回填）"))
        else:
            print(f"[!] {root} 下没有找到任何含 manifest.jsonl 的账号目录。")
            print("    先跑回填（scripts\\run_backfill.bat facebook）把内容抓下来。")
        return 1

    if a.review:
        print("=== 生成审核清单 ===")
        total = sum(run_review(d) for d in dirs)
        print(f"\n共 {total} 篇进入审核清单。")
        return 0

    translator = Translator(s)
    ok = bad = 0
    remaining = a.limit
    for d in dirs:
        if remaining == 0:
            break
        o, b = run_translate(s, translator, d, remaining, a.force, a.dry_run)
        ok, bad = ok + o, bad + b
        if remaining is not None:
            remaining -= o + b

    print(f"\n完成：成功 {ok} 篇，失败 {bad} 篇。")
    if bad:
        print("[!] 有失败项。上面每条都带了 post_id 与原因，修掉后重跑即可"
              "（已成功的不会重复翻译）。")
        return 1
    if ok and not a.dry_run:
        print("下一步：scripts\\run_translate.bat --review 生成人工审核清单。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
