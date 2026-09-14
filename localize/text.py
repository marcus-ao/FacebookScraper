"""将归档英文翻译为德语，译文独立写入 translated.jsonl；命令参数见 --help。"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import statistics
import sys
from collections import Counter
from collections.abc import Mapping
from contextlib import nullcontext
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]

from core.config import cfg                        # noqa: E402
from core.console import force_utf8                # noqa: E402
from core import paid_model                        # noqa: E402
from core import paid_requests                     # noqa: E402
from core.store import Archive, account_dirs        # noqa: E402
from core.store import post_dirname as post_dirname  # noqa: E402 兼容旧脚本
from core.paid_model import FileLock                # noqa: E402
from core.translated import (PROMPT_VERSION,        # noqa: E402
                             SourceTextError, apply_money_mapping as apply_money_mapping,
                             extract_hashtags as extract_hashtags, extract_money_tokens as extract_money_tokens,
                             hashtags_preserved, load_translated,
                             money_preserved, normalize_money_token as normalize_money_token,
                             numeric_flags as numeric_flags, render_glossary,
                             review_numeric_flags, source_text_sha256,
                             translation_is_current)
from core.translated import append_translated as append_jsonl  # noqa: E402
from core.localization import extract_urls, without_urls  # noqa: E402

# 提示词模板。放在独立文件里，改翻译行为不用改 Python，营销同事也能改。
TEMPLATE_PATH = ROOT / "prompts" / "translate_de.md"
DEEPSEEK_API_URL = "https://api.deepseek.com"

# 围栏前缀按长度降序，避免短前缀只剥掉部分语言标记。
_FENCES = ("```deutsch", "```german", "```text", "```de", "```")


# 配置

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
        self.failure_budget: int = int(g("failure_budget", 3))
        # 显式发送 thinking.enabled，不依赖端点默认值。
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
        paid_model.validate_endpoint(
            "translate", timeout=self.timeout,
            max_retries=self.max_retries, gap=self.gap)
        if self.style_examples < 0:
            raise SystemExit("[translate].style_examples 不能为负数")
        if self.failure_budget < 1:
            raise SystemExit("[translate].failure_budget 必须 >= 1")
        if self.reasoning_effort not in {"low", "high", "max"}:
            raise SystemExit("[translate].reasoning_effort 可选值：low, high, max")
        paid_model.validate_cost_rates(
            "translate", self.cost_rates, {"input", "cache_read", "output"})

    @property
    def credentials(self) -> paid_model.ModelCredentials:
        return paid_model.ModelCredentials(self.api_key_env, what="翻译 API 密钥")

    def api_key(self) -> str:
        return self.credentials.api_key()

    def credential_status(self) -> str:
        """只报告凭据是否存在及来源，不输出密钥片段或长度。"""
        return self.credentials.status()


def build_client(s: Settings):
    """构造 DeepSeek 官方 OpenAI 兼容客户端。实现在 core/paid_model，与 K 组共用。"""
    return paid_model.build_client(
        api_key=s.api_key(), base_url=s.base_url,
        timeout=s.timeout, max_retries=s.max_retries)


# 风格示例

def pick_style_examples(rows: list[dict], n: int, exclude_id: str | None = None,
                        owner: str | None = None) -> list[str]:
    """选取中等长度的英文风格示例；每账号复用同一组，保持语域和提示词缓存稳定。"""
    wanted_owner = (owner or "").strip().lower()
    texts = [without_urls(r.get("text") or "").strip() for r in rows
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


def render_style_examples(examples: list[str]) -> str:
    if not examples:
        return "（暂无——该账号还没有抓到足够长的文案。回填完成后这里会自动填充。）"
    # 用 JSON 字符串包裹外部正文，避免其围栏改变提示词结构。
    encoded = [json.dumps(ex, ensure_ascii=False)
               .replace("<", "\\u003c").replace(">", "\\u003e")
               for ex in examples]
    return "\n\n".join(
        f"<untrusted_style_reference index=\"{i}\">\n"
        f"{ex}\n"
        f"</untrusted_style_reference>"
        for i, ex in enumerate(encoded, 1))


def build_system_prompt(s: Settings, examples: list[str]) -> str:
    """渲染独立翻译模板；每账号复用相同系统提示词。"""
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
    # 先校验模板占位符；外部样例中的字面占位符不属于模板。
    placeholders = set(re.findall(r"\{\{[^{}\r\n]+\}\}", tpl))
    unknown = sorted(placeholders - set(subs))
    if unknown:
        raise SystemExit(f"提示词模板含未知占位符：{unknown}")
    for k, v in subs.items():
        tpl = tpl.replace(k, v)

    return tpl.strip()


# 翻译

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


class SourceDataError(FatalBatchError, SourceTextError):
    """归档输入契约错误，须在付费前停止整批。"""


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
    """复用公共致命错误分类，避免文本与图片的失败预算语义分叉。"""
    return paid_model.is_fatal_api_error(
        exc, extra_fatal=(ModelMismatchError, paid_requests.PaidRequestBlocked))


class Translator(paid_model.PaidCaller):
    """一次翻译调用。抽成类是为了测试能整体替换掉它，不必打桩到 SDK 内部。"""

    def __init__(self, settings: Settings, client=None,
                 paid_controller: paid_requests.RequestController | None = None) -> None:
        self._init_paid(settings, client, paid_controller)
        self.last_usage: dict[str, int] = {}
        self.last_model: str = ""
        # 保存实际响应内容，供 --check 核验 thinking 生效。
        self.last_blocks: list[str] = []
        self.usage_totals: Counter = Counter()

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
        # 不限制输出 token；thinking 模式忽略 temperature/top_p，故不发送。
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


def TranslationRunLock(path: Path) -> FileLock:   # noqa: N802 保留原名
    """整个付费批次单实例运行，防止双击两次造成同一帖子重复付费。"""
    return FileLock(path, error_type=SystemExit,
                    busy_message="另一个翻译批次正在运行。请勿重复双击；等它结束后再试。")


# 归档读写


def pending(rows: list[dict], done: dict[str, dict], force: bool,
            scope: frozenset[str] | None = None) -> list[dict]:
    """选择非空且缺当前译文的帖子；先校验整个 manifest，再筛选作用域。"""
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
    """解析 post_id 白名单；None 表示不限范围，latest-posts 不用更早帖替换无正文项。"""
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
                  scope: frozenset[str] | None = None, *,
                  refine_instruction: str = "", refine_id: str = "",
                  current_body: str = "", source_rows: list[dict] | None = None) -> tuple[int, int]:
    """翻译一个账号目录。返回 (成功数, 失败数)。"""
    arc = Archive(arc_base.parent, arc_base.name)
    rows = arc.rows() if source_rows is None else source_rows
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

    account_owner = arc_base.name.split("_", 1)[-1].strip().lower()
    examples = pick_style_examples(rows, s.style_examples, owner=account_owner)
    system = build_system_prompt(s, examples)
    if refine_instruction:
        if not re.fullmatch(r"[0-9a-f]{32}", refine_id) or len(todo) != 1:
            raise ValueError("文案优化必须指定唯一帖子和版本 ID")
        system += ("\n\n本篇运营优化指令；仍须遵守以上金额、品牌和标签规则：\n"
                   + json.dumps(without_urls(refine_instruction), ensure_ascii=False))
        if current_body:
            system += "\n当前德语正文供优化参考：\n" + json.dumps(without_urls(current_body), ensure_ascii=False)
    print(f"  提示词：{len(system)} 字符 / {len(examples)} 篇风格参照 / "
          f"称呼 {s.address_form} / 术语表 {len(s.glossary)} 条")

    ok = bad = flagged = money_violated = hashtag_violated = 0
    consecutive_failures = 0
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
             + "\0" + str(PROMPT_VERSION)
             + ("\0refine:" + refine_id if refine_instruction else "")).encode("utf-8")).hexdigest()
        if hasattr(translator, "set_paid_context"):
            translator.set_paid_context(paid_job_key, source_ref)
        try:
            de = translator.translate(without_urls(text), system)
        except Exception as e:
            print(f"  [{i}/{len(todo)}] {pid}  失败：{type(e).__name__}: {e}")
            bad += 1
            consecutive_failures += 1
            if _is_fatal_api_error(e):
                raise FatalBatchError(
                    "鉴权/权限/端点不存在/响应契约破裂属于整批共享错误，已立刻停止，"
                    "避免对剩余帖子重复无意义请求") from e
            if consecutive_failures >= s.failure_budget:
                raise FatalBatchError(
                    f"连续 {consecutive_failures} 篇失败，已达 "
                    f"[translate].failure_budget={s.failure_budget}，停止本批。"
                    "\n    单条失败不再掀掉整批，但连续失败说明问题不在"
                    "素材而在链路 —— 先看上面每条的原因，修掉后重跑即可"
                    "（已成功的不会重复付费）。") from e
            # 内容级输出错误只跳过这一条，保留批处理的断点续跑能力。
            continue

        consecutive_failures = 0
        money_violations = money_preserved(without_urls(text), de)
        hashtag_violations = hashtags_preserved(without_urls(text), de)
        violations = money_violations + hashtag_violations
        if extract_urls(de):
            violations.append('模型新增了网页链接，请在链接区确认德国站落地页；本次结果未写盘')
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
        if refine_instruction:
            row.update(refine_instruction=refine_instruction, refine_id=refine_id)
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
    if money_violated or hashtag_violated:
        if money_violated:
            print(f"  ❗ {money_violated} 篇的金额没有原样保留，已全部拒绝写盘并计为失败。")
            print("     金额必须原封不动（数值/小数点/符号/符号位置都不许改），"
                  "由人工替换成德国站定价。")
        if hashtag_violated:
            print(f"  ❗ {hashtag_violated} 篇的话题标签与原帖不完全一致，"
                  "已全部拒绝写盘并计为失败。")
            print("     标签必须保持相同数量、内容、大小写与顺序；"
                  "不得翻译、删减、新增或调序。")
        # 产出被拒仍已计费；重试受同一 job_key 的拒绝次数和预算限制。
        print(f"     这些 post_id 没被记成已完成，**但钱已经花了**。"
              f"同一条最多再重试 {paid_requests.REJECTED_RETRY_BUDGET - 1} 次；")
        print("     若是提示词的问题，改完提示词并把 PROMPT_VERSION +1，"
              "重试计数随之重置。")
        print("     查看当前被拒次数：python -m core.paid_requests --status")
    return ok, bad


# 连通性检查

def run_check(s: Settings,
              paid_controller: paid_requests.RequestController | None = None) -> int:
    """发起一次小额请求检查 API 配置。"""
    print("=== 翻译 API 自检 ===")
    print(f"  provider    : {s.provider}")
    print(f"  base_url    : {s.base_url}")
    print("  API 格式    : DeepSeek 官方 OpenAI 兼容 Chat Completions")
    print("  鉴权        : Authorization: Bearer（由 SDK 设置）")
    print(f"  密钥环境变量: {s.api_key_env} = {s.credential_status()}")
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
    """离线优先按实际 usage 估算；无记录时按字符估算并注明未含 reasoning。"""
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

    # 用 usage 中位数估算典型成本，避免单篇长尾主导均值。
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
    # limit 从最老待译项截取；latest-posts 用于选最近帖子。
    scope_group = ap.add_mutually_exclusive_group()
    scope_group.add_argument(
        "--post-id", action="append", default=None,
        help="只翻指定 post_id，可重复传入")
    scope_group.add_argument(
        "--latest-posts", type=int, default=None,
        help="全账号合计只选最新 N 篇（与 localize/images.py 的同名参数同义）")
    a = ap.parse_args(argv)
    if a.limit is not None and a.limit < 0:
        ap.error("--limit 不能为负数")
    if a.latest_posts is not None and a.latest_posts < 1:
        ap.error("--latest-posts 必须是正整数")

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")

    # 由应用入口注入预算策略，避免 core 反向依赖流水线。
    from pipeline.engine import budget_preflight   # noqa: PLC0415

    s = Settings()
    if a.check:
        return run_check(
            s, paid_controller=paid_requests.RequestController(
                cfg().state_dir, preflight=budget_preflight))

    root = cfg().archive_dir
    dirs = account_dirs(root, a.account)
    active = set(cfg().active_accounts())
    if a.account is None:
        dirs = [d for d in dirs if d.name in active]
    elif a.account not in active and not (a.estimate or a.show_prompt or a.dry_run):
        print(f"[!] {a.account} 已冻结为历史归档，只允许离线查看，不再翻译或改写。")
        return 2

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
              f"（改了模板记得把 core/translated.py 的 PROMPT_VERSION +1）")
        return 0

    if not dirs:
        print(f"[!] {root} 下没有找到任何含 manifest.jsonl 的账号目录。")
        print("    先跑回填（scripts\\run_backfill.bat facebook）把内容抓下来。")
        return 1

    try:
        scope = resolve_scope(dirs, post_ids=a.post_id,
                              latest_posts=a.latest_posts)
    except SourceTextError as e:          # SourceDataError 是它的子类
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
        except SourceTextError as e:          # SourceDataError 是它的子类
            print(f"[!] {e}")
            return 1

    if a.review:
        # 延迟导入跨模块审校工具，普通翻译无需加载图片依赖。
        from tools.review_report import run_review   # noqa: PLC0415

        print("=== 生成审核清单 ===")
        total = sum(run_review(d) for d in dirs)
        print(f"\n共 {total} 篇进入审核清单。")
        return 0

    translator = Translator(
        s, paid_controller=(
            None if a.dry_run
            else paid_requests.RequestController(
                cfg().state_dir, preflight=budget_preflight)))
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
