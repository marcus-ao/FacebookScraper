r"""归档图片里的英文文字 -> 德语（K 组，GPT-Image-2）。

本模块沿用 :mod:`translate` 已验证的批处理结构。原图与 ``manifest.jsonl``
只读；付费产物写入每帖的 ``media_de/``，元数据追加到账号目录下的
``images_de.jsonl``。人工放进 ``media_de/`` 的文件永远优先。

真实 API 自检会产生费用，只有显式传入 ``--check`` 才会执行：

    scripts\run_images.bat --check

普通离线检查可使用 ``--show-prompt`` / ``--estimate`` / ``--dry-run``；
它们都不会调用 API。
"""
from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import io
import json
import math
import os
import re
import statistics
import sys
import tempfile
import time
from contextlib import nullcontext
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Mapping

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from core.config import cfg                         # noqa: E402
from core.console import force_utf8                 # noqa: E402
from core import paid_model
from core import paid_requests                      # noqa: E402
from core.store import (Archive, ArchivePathError,  # noqa: E402
                        assert_physical_direct_path, post_dirname)
import translate as translation                    # noqa: E402
from core.paid_model import FileLock
from core import imagehash                          # noqa: E402


TEMPLATE_PATH = ROOT / "prompts" / "image_de.md"
INFERERA_API_URL = "https://api.inferera.com/v1"
IMAGE_PROMPT_VERSION = 2

# GPT-Image-2 的 size 线级契约。它们不是业务旋钮，不能被配置成网关不接受的值。
SIZE_STEP = 16
MIN_PIXELS = 655_360
MAX_PIXELS = 8_294_400
MAX_EDGE = 3_840
MAX_ASPECT_RATIO = 3.0
MAX_LATEST_POSTS = 3

IMAGE_CONFIG_KEYS = frozenset({
    "provider",
    "base_url",
    "api_key_env",
    "model",
    "quality",
    "output_format",
    "timeout_seconds",
    "max_retries",
    "request_gap_seconds",
    "incremental_since",
    "cost_rates_usd_per_million",
    "dhash_max_distance",
    "aspect_drift_warn_percent",
    "scale_warn_factor",
    "failure_budget",
})
KEEP_VERBATIM_KEYS = frozenset({"promo_codes", "brands", "models", "events", "marks"})
IMAGE_RATE_KEYS = frozenset({"text_input", "image_input", "image_output"})


# ---------------------------------------------------------------------------
# 配置与 API 客户端（K1）
# ---------------------------------------------------------------------------

def _non_bool_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


class Settings:
    """严格读取 ``[image]``，配置与代码双向对账，不允许静默默认值。"""

    def __init__(self, image_config: Mapping[str, Any] | None = None,
                 glossary: Mapping[str, Any] | None = None) -> None:
        if image_config is None:
            config = cfg()
            raw = config["image"]
            translate_config = config["translate"]
            if (not isinstance(translate_config, Mapping)
                    or "glossary" not in translate_config):
                raise SystemExit(
                    "config.toml 缺少 [translate.glossary]；"
                    "图片提示词不得静默丢失正文术语表")
            glossary_raw = translate_config["glossary"]
        else:
            raw = image_config
            glossary_raw = {} if glossary is None else glossary
        if not isinstance(raw, Mapping):
            raise SystemExit("config.toml 的 [image] 必须是 TOML 表")

        # tomllib 把 [image.keep_verbatim] 放进 image 表的嵌套字典。
        top_keys = set(raw) - {"keep_verbatim"}
        missing = sorted(IMAGE_CONFIG_KEYS - top_keys)
        extra = sorted(top_keys - IMAGE_CONFIG_KEYS)
        if missing or extra:
            details = []
            if missing:
                details.append("代码要求但配置缺少：" + "、".join(missing))
            if extra:
                details.append("配置存在但代码未消费：" + "、".join(extra))
            raise SystemExit("[image] 配置项审计失败；" + "；".join(details))

        keep = raw.get("keep_verbatim")
        if not isinstance(keep, Mapping):
            raise SystemExit("config.toml 缺少 [image.keep_verbatim]")
        keep_keys = set(keep)
        missing_keep = sorted(KEEP_VERBATIM_KEYS - keep_keys)
        extra_keep = sorted(keep_keys - KEEP_VERBATIM_KEYS)
        if missing_keep or extra_keep:
            details = []
            if missing_keep:
                details.append("缺少：" + "、".join(missing_keep))
            if extra_keep:
                details.append("未消费：" + "、".join(extra_keep))
            raise SystemExit("[image.keep_verbatim] 配置项审计失败；" + "；".join(details))

        self.provider = self._string(raw, "provider").lower()
        self.base_url = self._string(raw, "base_url").rstrip("/")
        self.api_key_env = self._string(raw, "api_key_env")
        self.model = self._string(raw, "model")
        self.quality = self._string(raw, "quality").lower()
        self.output_format = self._string(raw, "output_format").lower()
        self.timeout = self._number(raw, "timeout_seconds")
        self.max_retries = self._integer(raw, "max_retries")
        self.gap = self._number(raw, "request_gap_seconds")
        self.incremental_since = self._string(raw, "incremental_since")
        self.dhash_max_distance = self._integer(raw, "dhash_max_distance")
        self.aspect_drift_warn_percent = self._number(raw, "aspect_drift_warn_percent")
        self.scale_warn_factor = self._number(raw, "scale_warn_factor")
        self.failure_budget = self._integer(raw, "failure_budget")

        rates = raw["cost_rates_usd_per_million"]
        if not isinstance(rates, Mapping):
            raise SystemExit("[image].cost_rates_usd_per_million 必须是 TOML 内联表")
        self.cost_rates = dict(rates)

        self.keep_verbatim: dict[str, tuple[str, ...]] = {}
        for category in sorted(KEEP_VERBATIM_KEYS):
            values = keep[category]
            if (not isinstance(values, list)
                    or any(not isinstance(v, str) or not v.strip() for v in values)):
                raise SystemExit(
                    f"[image.keep_verbatim].{category} 必须是非空字符串数组")
            self.keep_verbatim[category] = tuple(v.strip() for v in values)

        if not isinstance(glossary_raw, Mapping):
            raise SystemExit("[translate.glossary] 必须是 TOML 表")
        self.glossary = dict(glossary_raw)
        self.validate()

    @staticmethod
    def _string(raw: Mapping[str, Any], key: str) -> str:
        value = raw[key]
        if not isinstance(value, str) or not value.strip():
            raise SystemExit(f"[image].{key} 必须是非空字符串")
        return value.strip()

    @staticmethod
    def _number(raw: Mapping[str, Any], key: str) -> float:
        value = raw[key]
        if not _non_bool_number(value) or not math.isfinite(float(value)):
            raise SystemExit(f"[image].{key} 必须是有限数字")
        return float(value)

    @staticmethod
    def _integer(raw: Mapping[str, Any], key: str) -> int:
        value = raw[key]
        if not isinstance(value, int) or isinstance(value, bool):
            raise SystemExit(f"[image].{key} 必须是整数")
        return value

    def validate(self) -> None:
        """在第一次网络请求前失败闭合，避免把内容或密钥发错端点。"""
        if self.provider != "inferera":
            raise SystemExit("[image].provider 目前只支持 inferera")
        if self.base_url != INFERERA_API_URL:
            raise SystemExit(
                "provider=inferera 时 [image].base_url 必须是 " + INFERERA_API_URL)
        if self.model != "gpt-image-2":
            raise SystemExit("[image].model 必须是 gpt-image-2；不得静默使用 free/其它模型")
        if self.quality not in {"low", "medium", "high"}:
            raise SystemExit("[image].quality 可选值：low, medium, high（不得使用 auto）")
        if self.output_format not in {"jpeg", "png", "webp"}:
            raise SystemExit("[image].output_format 可选值：jpeg, png, webp")
        paid_model.validate_endpoint(
            "image", timeout=self.timeout,
            max_retries=self.max_retries, gap=self.gap)
        try:
            parsed_cutoff = datetime.strptime(
                self.incremental_since, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        except ValueError as exc:
            raise SystemExit(
                "[image].incremental_since 必须是 UTC 时间 YYYY-MM-DDTHH:MM:SSZ") from exc
        if parsed_cutoff.year < 2000:
            raise SystemExit("[image].incremental_since 年份异常")
        if self.dhash_max_distance < -1 or self.dhash_max_distance > 64:
            raise SystemExit("[image].dhash_max_distance 必须是 -1（关闭）或 0..64")
        if self.aspect_drift_warn_percent < 0:
            raise SystemExit("[image].aspect_drift_warn_percent 不能为负数")
        if self.scale_warn_factor < 1.0:
            raise SystemExit(
                "[image].scale_warn_factor 必须 >= 1.0（它是放大倍数的告警线，"
                "不是缩小线）")
        if self.failure_budget < 1:
            raise SystemExit("[image].failure_budget 必须 >= 1")
        paid_model.validate_cost_rates("image", self.cost_rates, IMAGE_RATE_KEYS)
        if any(not isinstance(k, str) or not isinstance(v, str)
               or not k.strip() or not v.strip() for k, v in self.glossary.items()):
            raise SystemExit("[translate.glossary] 的键和值都必须是非空字符串")

    @property
    def credentials(self) -> paid_model.ModelCredentials:
        return paid_model.ModelCredentials(self.api_key_env, what="图片 API 密钥")

    def api_key(self) -> str:
        return self.credentials.api_key()

    def credential_status(self) -> str:
        """只报告是否存在与来源，不暴露密钥片段、长度或值。"""
        return self.credentials.status()


def build_client(settings: Settings):
    """构造 OpenAI 兼容客户端。实现在 core/paid_model，与 F 组共用。"""
    return paid_model.build_client(
        api_key=settings.api_key(), base_url=settings.base_url,
        timeout=settings.timeout, max_retries=settings.max_retries)


class ModelMismatchError(RuntimeError):
    """网关返回的实际模型与付费请求不一致。"""


class ModelUnavailableError(RuntimeError):
    """模型目录未精确列出请求模型；必须在付费 edits 请求前停止。"""


class ModelCatalogPreflightError(RuntimeError):
    """模型目录请求本身失败；本客户端不再重试目录或进入付费调用。"""


class FatalBatchError(RuntimeError):
    """鉴权、端点、模型或共享请求契约错误，继续整批只会重复花钱。"""


class ResponseContractError(RuntimeError):
    """网关响应缺少整批都依赖的字段；首张即熔断，避免逐张付费后丢弃。"""


@dataclass(frozen=True)
class EditResult:
    b64_json: str
    model: str
    usage: dict[str, Any]
    model_verification: str = "response"


def _as_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if hasattr(value, "model_dump"):
        dumped = value.model_dump()
        return dict(dumped) if isinstance(dumped, Mapping) else {}
    return {}


def normalize_usage(usage: Any) -> dict[str, Any]:
    """保留 GPT-Image-2 usage，转成可 JSON 序列化的非负整数。"""
    raw = _as_dict(usage)
    out: dict[str, Any] = {}
    for key in ("input_tokens", "output_tokens", "total_tokens"):
        value = raw.get(key, getattr(usage, key, None))
        if _valid_token_count(value):
            out[key] = value
    for key in ("input_tokens_details", "output_tokens_details"):
        details_raw = raw.get(key, getattr(usage, key, None))
        details = _as_dict(details_raw)
        clean = {
            name: value for name, value in details.items()
            if _valid_token_count(value)
        }
        if clean:
            out[key] = clean
    return out


def _valid_token_count(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def usage_contract_errors(usage: Mapping[str, Any]) -> list[str]:
    """最低 usage 契约；输出明细是 Images API 的可选字段。"""
    errors: list[str] = []
    for key in ("input_tokens", "output_tokens"):
        if not _valid_token_count(usage.get(key)):
            errors.append(key)
    input_details = usage.get("input_tokens_details")
    if not isinstance(input_details, Mapping):
        errors.append("input_tokens_details")
    else:
        for key in ("image_tokens", "text_tokens"):
            if not _valid_token_count(input_details.get(key)):
                errors.append(f"input_tokens_details.{key}")
    return errors


def safe_error_summary(exc: Exception) -> str:
    """API 异常只暴露类型、HTTP 状态和安全 request ID。"""
    error_type = type(exc).__name__
    try:
        import openai
    except ImportError:
        return f"{error_type}: {exc}"
    api_error = getattr(openai, "APIError", None)
    if not isinstance(api_error, type) or not isinstance(exc, api_error):
        return f"{error_type}: {exc}"

    parts = [error_type]
    status = getattr(exc, "status_code", None)
    response = getattr(exc, "response", None)
    if not isinstance(status, int) and response is not None:
        status = getattr(response, "status_code", None)
    if isinstance(status, int):
        parts.append(f"HTTP {status}")

    request_id = getattr(exc, "request_id", None)
    if not isinstance(request_id, str) and response is not None:
        headers = getattr(response, "headers", {})
        if isinstance(headers, Mapping):
            request_id = headers.get("x-request-id") or headers.get("request-id")
    if (isinstance(request_id, str)
            and re.fullmatch(r"[A-Za-z0-9._:/-]{1,200}", request_id)):
        parts.append(f"request_id={request_id}")
    return " / ".join(parts)


class ImageEditor:
    """一次 GPT-Image-2 edits 调用；整体可替换，离线测试不接触 SDK/网络。"""

    def __init__(self, settings: Settings, client=None,
                 paid_controller: paid_requests.RequestController | None = None) -> None:
        self.s = settings
        self._client = client
        self._last_call = 0.0
        self._catalog_attempted = False
        self._catalog_verified = False
        self._catalog_error: Exception | None = None
        self.last_usage: dict[str, Any] = {}
        self.last_model = ""
        self.last_model_verification = ""
        self._paid_controller = paid_controller
        self._paid_job_key = ""
        self._paid_source_ref = ""
        self._paid_media_index: int | None = None
        self._paid_receipt: paid_requests.PaidReceipt | None = None

    def set_paid_context(self, job_key: str, source_ref: str,
                         media_index: int) -> None:
        self._paid_job_key = str(job_key)
        self._paid_source_ref = str(source_ref)
        self._paid_media_index = int(media_index)

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
        if self.s.gap <= 0:
            return
        elapsed = time.monotonic() - self._last_call
        if elapsed < self.s.gap:
            time.sleep(self.s.gap - elapsed)
        self._last_call = time.monotonic()

    def verify_model_available(self) -> None:
        """每个客户端实例只查一次目录，且必须先于付费图片调用。"""
        if self._catalog_verified:
            return
        if self._catalog_attempted:
            assert self._catalog_error is not None
            raise self._catalog_error
        self._catalog_attempted = True
        try:
            catalog = self.client.models.list()
        except Exception as exc:
            cached = ModelCatalogPreflightError(
                "模型目录预检失败，已停止本批且本客户端不再重试："
                + safe_error_summary(exc))
            self._catalog_error = cached
            raise cached from exc
        raw = _as_dict(catalog)
        entries = raw.get("data", getattr(catalog, "data", None))
        if not isinstance(entries, (list, tuple)):
            entries = []
        model_ids = {
            value
            for entry in entries
            for value in [
                _as_dict(entry).get("id", getattr(entry, "id", None))
            ]
            if isinstance(value, str)
        }
        if self.s.model not in model_ids:
            cached = ModelUnavailableError(
                f"模型目录未返回精确模型 {self.s.model!r}；"
                "已在图片 edits 付费请求前停止")
            self._catalog_error = cached
            raise cached
        self._catalog_verified = True

    def request_kwargs(self, image, prompt: str, size: str, *,
                       quality: str | None = None) -> dict[str, Any]:
        """正式处理与自检共用；只列网关确认支持的字段。"""
        selected_quality = self.s.quality if quality is None else quality
        if selected_quality not in {"low", "medium", "high"}:
            raise ValueError("quality 必须是 low/medium/high")
        return {
            "model": self.s.model,
            "prompt": prompt,
            "image": image,
            "n": 1,
            "size": size,
            "quality": selected_quality,
            "output_format": self.s.output_format,
        }

    def edit(self, source_path: Path, prompt: str, size: str, *,
             quality: str | None = None) -> EditResult:
        if self._paid_controller is None:
            return self._edit_once(source_path, prompt, size, quality=quality)
        # 免费模型目录预检与本地参数/源文件检查先完成；started 紧贴真实 edits。
        self.verify_model_available()
        self.request_kwargs(None, prompt, size, quality=quality)
        with Path(source_path).open("rb"):
            pass
        fallback = hashlib.sha256(
            (self.s.model + "\0" + str(Path(source_path).resolve()) + "\0"
             + prompt + "\0" + size).encode("utf-8")).hexdigest()
        job_key = self._paid_job_key or ("image-check:" + fallback)
        source_ref = self._paid_source_ref or "check:image"
        self._paid_receipt = None
        result, receipt = self._paid_controller.run(
            stage="image", job_key=job_key, source_ref=source_ref,
            media_index=self._paid_media_index, model=self.s.model,
            request=lambda: self._edit_once(
                source_path, prompt, size, quality=quality),
            usage_getter=lambda: self.last_usage,
            usage_errors=usage_contract_errors,
            usage_cost=lambda usage: image_usage_cost(self.s, usage))
        self._paid_receipt = receipt
        self._paid_job_key = ""
        self._paid_source_ref = ""
        self._paid_media_index = None
        return result

    def _edit_once(self, source_path: Path, prompt: str, size: str, *,
                   quality: str | None = None) -> EditResult:
        self.last_usage = {}
        self.last_model = ""
        self.last_model_verification = ""
        self.verify_model_available()
        self._pace()
        with Path(source_path).open("rb") as image_file:
            response = self.client.images.edit(
                **self.request_kwargs(image_file, prompt, size, quality=quality))

        response_raw = _as_dict(response)
        # 响应已经产生费用：先取 usage，再做模型/内容契约。即使后续拒绝产出，
        # paid ledger 也能记下真实成本，而不是退化成未知金额。
        usage_value = response_raw.get("usage", getattr(response, "usage", None))
        self.last_usage = normalize_usage(usage_value)
        usage_errors = usage_contract_errors(self.last_usage)
        reported_model = response_raw.get("model", getattr(response, "model", None))
        reported_model = str(reported_model).strip() if reported_model is not None else ""
        if reported_model and reported_model.lower() != self.s.model.lower():
            raise ModelMismatchError(
                f"请求模型 {self.s.model!r}，实际响应 model={reported_model!r}；"
                "已停止，避免把 free/其它模型产物混进正式批次")
        if reported_model:
            self.last_model = reported_model
            self.last_model_verification = "response"
        else:
            self.last_model = self.s.model
            self.last_model_verification = "catalog"

        if usage_errors:
            raise ResponseContractError(
                "图片 edits 响应 usage 契约不完整，缺少：" + "、".join(usage_errors))

        data = response_raw.get("data", getattr(response, "data", None)) or []
        if not data:
            raise ResponseContractError("图片 edits 响应没有 data[0]")
        first_raw = _as_dict(data[0])
        payload = first_raw.get("b64_json", getattr(data[0], "b64_json", None))
        if not isinstance(payload, str) or not payload.strip():
            raise ResponseContractError("图片 edits 响应没有 data[0].b64_json")
        return EditResult(
            payload.strip(), self.last_model, self.last_usage,
            self.last_model_verification)


_DATA_URL_PREFIX_RE = re.compile(r"^data:image/[A-Za-z0-9.+-]+;base64,")


def decode_image_payload(payload: str) -> bytes:
    """严格解码网关返回的 base64，并确认 Pillow 可识别。

    ``validate=True`` 保留（不接受任何非 base64 字母表的字符），但先把**空白**
    和可选的 ``data:image/...;base64,`` 前缀剥掉再解码（CR-55）：
    中转网关按 76 列折行返回 base64、或补上 data URL 前缀都是常见形态，
    而这一步失败时**钱已经花掉了**，整批产出会被判"不是合法的裸 base64"丢弃。
    剥空白不会放松校验 —— 折行是编码传输格式，不是数据内容。
    """
    if not isinstance(payload, str) or not payload.strip():
        raise ValueError("b64_json 为空")
    cleaned = _DATA_URL_PREFIX_RE.sub("", payload.strip())
    cleaned = re.sub(r"\s+", "", cleaned)
    if not cleaned:
        raise ValueError("b64_json 去掉空白后是空串")
    try:
        data = base64.b64decode(cleaned, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("b64_json 不是合法的 base64") from exc
    if not data:
        raise ValueError("b64_json 解码后是空字节")
    try:
        with Image.open(io.BytesIO(data)) as image:
            image.verify()
    except Exception as exc:
        raise ValueError("b64_json 能解码，但不是 Pillow 可识别的图片") from exc
    return data


def run_check(settings: Settings, editor: ImageEditor | None = None) -> int:
    """发一个最小 low edits 请求，验证网关、模型、图片与 usage 契约。"""
    print("=== GPT-Image-2 API 自检 ===")
    print("  ⚠ 这会发送 1 个真实 low 质量图片 edits 请求并产生少量费用。")
    print(f"  provider    : {settings.provider}")
    print(f"  base_url    : {settings.base_url}")
    print("  API 格式    : OpenAI Images edits / multipart/form-data")
    print("  鉴权        : Authorization: Bearer（由 SDK 设置）")
    print(f"  密钥        : {settings.api_key_env} {settings.credential_status()}")
    print(f"  请求 model  : {settings.model}")
    print("  自检 quality: low（正式处理仍使用 config.toml 的显式 quality）")

    editor = editor or ImageEditor(settings)
    with tempfile.TemporaryDirectory(prefix="image-check-") as tmp:
        source = Path(tmp) / "check.png"
        image = Image.new("RGB", (816, 816), "white")
        draw = ImageDraw.Draw(image)
        draw.rectangle((224, 288, 592, 528), outline="black", width=16)
        draw.text((338, 392), "TEST", fill="black")
        image.save(source, format="PNG")
        try:
            result = editor.edit(
                source,
                "Keep the image unchanged. This is a harmless connectivity check.",
                "816x816",
                quality="low",
            )
            data = decode_image_payload(result.b64_json)
            with Image.open(io.BytesIO(data)) as returned:
                returned.load()
                returned_size = returned.size
        except SystemExit:
            raise
        except Exception as exc:
            if hasattr(editor, "finalize_paid"):
                editor.finalize_paid(False, "image connectivity check rejected output")
            print(f"[!] {safe_error_summary(exc)}")
            return 1

    if returned_size != (816, 816):
        if hasattr(editor, "finalize_paid"):
            editor.finalize_paid(False, "image connectivity check wrong dimensions")
        print(f"[!] 自检返回尺寸 {returned_size[0]}x{returned_size[1]}，预期 816x816")
        return 1
    errors = usage_contract_errors(result.usage)
    if errors:
        if hasattr(editor, "finalize_paid"):
            editor.finalize_paid(False, "image connectivity check usage invalid")
        print("[!] usage 契约不完整，缺少：" + "、".join(errors))
        return 1
    print(f"[ok] API 连通；model={result.model} "
          f"(verification={result.model_verification})；图片 816x816 可解码。")
    print("usage：" + json.dumps(result.usage, ensure_ascii=False, sort_keys=True))
    if hasattr(editor, "finalize_paid"):
        editor.finalize_paid(True, "image connectivity check verified")
    return 0


# ---------------------------------------------------------------------------
# 尺寸合法化（K2）
# ---------------------------------------------------------------------------

def _axis_candidates(target: float) -> list[int]:
    """给目标边长周围的 16 倍数；范围够跨过像素上下限的量化误差。"""
    floor_value = math.floor(target / SIZE_STEP) * SIZE_STEP
    values = {
        floor_value + offset * SIZE_STEP
        for offset in range(-4, 6)
    }
    return sorted(v for v in values if SIZE_STEP <= v <= MAX_EDGE)


def _is_legal_size(width: int, height: int) -> bool:
    if width <= 0 or height <= 0:
        return False
    if width % SIZE_STEP or height % SIZE_STEP:
        return False
    if width > MAX_EDGE or height > MAX_EDGE:
        return False
    pixels = width * height
    if pixels < MIN_PIXELS or pixels > MAX_PIXELS:
        return False
    return max(width / height, height / width) <= MAX_ASPECT_RATIO + 1e-12


def aspect_drift_percent(source_width: int, source_height: int,
                         output_width: int, output_height: int) -> float:
    """输出宽高比相对原图的变化百分比；0 表示没有几何形变。"""
    source_ratio = source_width / source_height
    output_ratio = output_width / output_height
    return abs(output_ratio / source_ratio - 1.0) * 100.0


def scale_factor(source_width: int, source_height: int,
                 output_width: int, output_height: int) -> float:
    """线性缩放倍数（按面积开方）；>1 表示为了过像素下限把原图放大了。

    **为什么单独记这一项**（CR-54）：`aspect_drift_percent` 只看宽高比，
    等比放大的形变是 0，所以它抓不到"小图被放大"这件事。
    真实归档实测（818 张）：772 张不缩放，但 13 张放大超过 1.3 倍，
    最极端的 278x430 -> 656x1008 放大了 **2.35 倍**，而它的宽高比形变只有
    0.66%，稳稳低于 2% 的告警线，会静默通过。发出去的德语图就是一张
    2.35 倍放大的图 —— 与 IMAGE_PLAN 第 3.1 节
    「在无法验证的介质上做不可逆的变换」是同一类问题。
    """
    return math.sqrt((output_width * output_height)
                     / (source_width * source_height))


def legal_size(width: int, height: int) -> tuple[int, int]:
    """返回尽量贴近原比例、且满足 GPT-Image-2 四条约束的尺寸。

    只做同比例缩放与 16 像素量化；不会裁剪或填充。若原图本身超过 3:1，
    不可能在不改画面的前提下合法化，故显式失败。
    """
    if (not isinstance(width, int) or isinstance(width, bool)
            or not isinstance(height, int) or isinstance(height, bool)
            or width <= 0 or height <= 0):
        raise ValueError("原图宽高必须是正整数")
    source_aspect = max(width / height, height / width)
    if source_aspect > MAX_ASPECT_RATIO + 1e-12:
        raise ValueError(
            f"原图宽高比 {source_aspect:.4f}:1 超过 GPT-Image-2 的 3:1；"
            "本项目不自动裁剪或填充")

    pixels = width * height
    scale = 1.0
    if max(width, height) > MAX_EDGE:
        scale = min(scale, MAX_EDGE / max(width, height))
    if pixels * scale * scale > MAX_PIXELS:
        scale = min(scale, math.sqrt(MAX_PIXELS / pixels))
    if pixels * scale * scale < MIN_PIXELS:
        scale = math.sqrt(MIN_PIXELS / pixels)
        if max(width, height) * scale > MAX_EDGE + 1e-9:
            raise ValueError("无法同时满足最小像素数与最大边长")

    target_width = width * scale
    target_height = height * scale
    rounded_width = math.floor(target_width / SIZE_STEP + 0.5) * SIZE_STEP
    rounded_height = math.floor(target_height / SIZE_STEP + 0.5) * SIZE_STEP

    candidates: list[tuple[int, int]] = []
    width_candidates = _axis_candidates(target_width)
    height_candidates = _axis_candidates(target_height)
    for candidate_width in width_candidates:
        # 从宽反推高、从原目标取高，两种都放进候选，避免只量化一个轴产生偏置。
        inferred_height = candidate_width * height / width
        for candidate_height in set(height_candidates + _axis_candidates(inferred_height)):
            if _is_legal_size(candidate_width, candidate_height):
                candidates.append((candidate_width, candidate_height))
    for candidate_height in height_candidates:
        inferred_width = candidate_height * width / height
        for candidate_width in _axis_candidates(inferred_width):
            if _is_legal_size(candidate_width, candidate_height):
                candidates.append((candidate_width, candidate_height))

    if not candidates:
        raise ValueError(
            f"无法把 {width}x{height} 合法化：没有同时满足 16 倍数、像素范围、"
            "最大边长与 3:1 的候选")

    def rank(size: tuple[int, int]) -> tuple[int, float, float, int, int]:
        candidate_width, candidate_height = size
        drift = aspect_drift_percent(width, height, candidate_width, candidate_height)
        target_error = (abs(candidate_width - target_width) / max(target_width, 1.0)
                        + abs(candidate_height - target_height) / max(target_height, 1.0))
        width_rounding_error = abs(candidate_width - rounded_width)
        height_rounding_error = abs(candidate_height - rounded_height)
        # 最后一项偏向较大候选，保证 1080x1080 的等距平局选到任务书指定的 1088。
        # 先锁定宽边的最近 16 倍数，再用高边修正宽高比：任务书给出的
        # 1080x1350 -> 1088x1360 与 1440x1080 -> 1440x1088 都依赖这个顺序。
        return (width_rounding_error, drift, target_error, height_rounding_error,
                -(candidate_width * candidate_height))

    chosen = min(set(candidates), key=rank)
    if not _is_legal_size(*chosen):
        raise AssertionError("legal_size 内部错误：返回值不满足 GPT-Image-2 契约")
    return chosen


# ---------------------------------------------------------------------------
# 提示词（K4）
# ---------------------------------------------------------------------------

_IMAGE_PROMPT_PLACEHOLDERS = frozenset({
    "{{TEXT_DE}}", "{{GLOSSARY}}", "{{KEEP_VERBATIM}}",
})
_KEEP_LABELS = {
    "promo_codes": "优惠码 / 折扣码",
    "brands": "品牌名 / 商标",
    "models": "产品型号",
    "events": "展会 / 活动名",
    "marks": "认证、合规与法律标记",
}


def render_keep_verbatim(keep: Mapping[str, tuple[str, ...]]) -> str:
    """把配置清单渲染成明确的逐字符规则；输出顺序固定，便于审计提示词。"""
    lines = []
    for category in ("promo_codes", "brands", "models", "events", "marks"):
        values = keep.get(category, ())
        encoded = "、".join(f"`{value}`" for value in values)
        lines.append(f"- **{_KEEP_LABELS[category]}**：{encoded or '（当前为空）'}")
    return "\n".join(lines)


def _untrusted_text_de(text_de: str) -> str:
    """把社媒译文封成 JSON 数据，正文无法闭合标签或 Markdown 围栏。"""
    encoded = (json.dumps(text_de, ensure_ascii=False)
               .replace("<", "\\u003c").replace(">", "\\u003e"))
    return ("<untrusted_text_de_reference>\n"
            + encoded
            + "\n</untrusted_text_de_reference>")


def build_image_prompt(settings: Settings, text_de: str) -> str:
    """渲染 ``prompts/image_de.md``；未知/缺失占位符在联网前失败。"""
    if not isinstance(text_de, str) or not text_de.strip():
        raise ValueError("当前帖子没有非空 text_de；K 组不做无译文降级")
    template = TEMPLATE_PATH.read_text(encoding="utf-8")
    while template.lstrip().startswith("<!--"):
        stripped = template.lstrip()
        end = stripped.find("-->")
        if end < 0:
            raise SystemExit(f"提示词模板 {TEMPLATE_PATH} 的 HTML 注释未闭合")
        template = stripped[end + 3:]

    found = set(re.findall(r"\{\{[^{}\r\n]+\}\}", template))
    unknown = sorted(found - _IMAGE_PROMPT_PLACEHOLDERS)
    missing = sorted(_IMAGE_PROMPT_PLACEHOLDERS - found)
    if unknown or missing:
        details = []
        if unknown:
            details.append(f"未知占位符 {unknown}")
        if missing:
            details.append(f"缺少占位符 {missing}")
        raise SystemExit("图片提示词模板契约失败：" + "；".join(details))

    # 不可信正文必须最后插入；否则正文中的字面 ``{{KEEP_VERBATIM}}`` 会被后续
    # 替换成真实规则，等于让外部数据改变了提示词结构。
    substitutions = (
        ("{{GLOSSARY}}", translation.render_glossary(settings.glossary)),
        ("{{KEEP_VERBATIM}}", render_keep_verbatim(settings.keep_verbatim)),
        ("{{TEXT_DE}}", _untrusted_text_de(text_de.strip())),
    )
    for placeholder, value in substitutions:
        template = template.replace(placeholder, value)
    # 不扫描最终文本：外部 text_de 完全可以合法包含 ``{{TEXT_DE}}`` 字面量。
    return template.strip()


# ---------------------------------------------------------------------------
# 产出硬校验（K5）
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ValidatedImage:
    data: bytes
    width: int
    height: int
    image_format: str
    dhash_distance: int


def _open_loaded_image(data: bytes) -> Image.Image:
    try:
        image = Image.open(io.BytesIO(data))
        image.load()
        return image
    except Exception as exc:
        raise ValueError("产出字节不是 Pillow 可完整解码的图片") from exc


def dhash_distance(left: Image.Image, right: Image.Image) -> int:
    """两张已打开的图之间的 dHash 距离。实现在 core/imagehash，与配对侧共用。"""
    return imagehash.hamming(
        imagehash.dhash_value(left), imagehash.dhash_value(right))


def _reject_placeholder(image: Image.Image) -> None:
    sample = image.convert("RGB")
    sample.thumbnail((128, 128), Image.Resampling.LANCZOS)
    colors = sample.getcolors(maxcolors=sample.width * sample.height + 1)
    if colors is not None and len(colors) == 1:
        raise ValueError("产出是纯色占位图")

    gray = sample.convert("L").resize((64, 64), Image.Resampling.LANCZOS)
    values = list(gray.getdata())
    near_black = sum(value <= 3 for value in values) / len(values)
    near_white = sum(value >= 252 for value in values) / len(values)
    if max(near_black, near_white) >= 0.995:
        side = "全黑" if near_black > near_white else "全白"
        raise ValueError(f"产出接近{side}占位图")


def validate_output(payload: str, source_path: Path,
                    requested_size: tuple[int, int], output_format: str,
                    dhash_max_distance: int) -> ValidatedImage:
    """写盘前硬闸；任一失败都不返回可写字节。"""
    data = decode_image_payload(payload)
    output = _open_loaded_image(data)
    try:
        if output.size != requested_size:
            raise ValueError(
                f"产出尺寸 {output.width}x{output.height} 与请求 "
                f"{requested_size[0]}x{requested_size[1]} 不一致")
        expected_format = {"jpeg": "JPEG", "png": "PNG", "webp": "WEBP"}[output_format]
        actual_format = str(output.format or "").upper()
        if actual_format != expected_format:
            raise ValueError(f"产出格式 {actual_format or '?'} 与请求 {expected_format} 不一致")
        _reject_placeholder(output)
        try:
            with Image.open(source_path) as source:
                source.load()
                distance = dhash_distance(source, output)
        except ValueError:
            raise
        except Exception as exc:
            raise ValueError(f"原图无法解码：{source_path}") from exc
        if dhash_max_distance >= 0 and distance > dhash_max_distance:
            raise ValueError(
                f"dHash 距离 {distance} 超过阈值 {dhash_max_distance}；"
                "模型可能整张重画，产出未写盘")
        return ValidatedImage(data, output.width, output.height, actual_format, distance)
    finally:
        output.close()


# ---------------------------------------------------------------------------
# 真相源、人工优先与批处理（K6）
# ---------------------------------------------------------------------------

@dataclass
class ImageState:
    latest: dict[tuple[str, int], dict[str, Any]]
    owned_paths: set[str]
    owned_hashes: dict[str, set[str | None]]


@dataclass(frozen=True)
class ImageJob:
    account: str
    arc_base: Path
    post_id: str
    media_index: int
    source_path: Path
    source_rel: str
    source_sha256: str
    source_size: tuple[int, int]
    text_de: str
    text_de_sha256: str
    requested_size: tuple[int, int]
    aspect_drift_percent: float
    scale_factor: float
    out_path: Path
    out_rel: str

    @property
    def key(self) -> tuple[str, int]:
        return self.post_id, self.media_index


@dataclass
class RunStats:
    queued: int = 0
    succeeded: int = 0
    failed: int = 0
    skipped_current: int = 0
    skipped_manual: int = 0
    skipped_no_translation: int = 0
    skipped_bad_source: int = 0


@dataclass(frozen=True)
class ReviewImagePair:
    media_index: int
    source_rel: str
    localized_rel: str | None
    record: dict[str, Any] | None
    manual: bool


def _safe_rel_string(value: Any) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    pure = PurePosixPath(value.strip().replace("\\", "/"))
    if pure.is_absolute() or ".." in pure.parts or not pure.parts:
        return None
    return pure.as_posix()


def _valid_sha256(value: Any) -> bool:
    return isinstance(value, str) and bool(re.fullmatch(r"[0-9a-f]{64}", value))


def _record_path_matches_key(post_id: str, media_index: int, out_rel: str) -> bool:
    """所有权记录必须绑定到自己的帖子目录、media_de 与两位媒体序号。"""
    pure = PurePosixPath(out_rel)
    if (len(pure.parts) != 4 or pure.parts[0] != "posts"
            or pure.parts[2] != "media_de"):
        return False
    filename = PurePosixPath(pure.parts[3])
    if filename.stem != f"{media_index + 1:02d}" or filename.suffix.lower() not in {
            ".jpg", ".jpeg", ".png", ".webp"}:
        return False
    safe_id = post_dirname(post_id, None).removeprefix("undated_")
    folder = pure.parts[1]
    return (folder == f"undated_{safe_id}"
            or bool(re.fullmatch(
                rf"\d{{4}}-\d{{2}}-\d{{2}}_\d{{4}}_{re.escape(safe_id)}", folder)))


def load_image_state(path: Path) -> ImageState:
    """逐行容错读取 ``images_de.jsonl``；同键后写胜出，并保留程序所有权历史。"""
    latest: dict[tuple[str, int], dict[str, Any]] = {}
    owned_paths: set[str] = set()
    owned_hashes: dict[str, set[str | None]] = {}
    if not path.exists():
        return ImageState(latest, owned_paths, owned_hashes)
    assert_physical_direct_path(path.parent, path, kind="file", label="images_de.jsonl")
    with path.open("rb") as handle:
        for raw_line in handle:
            raw_line = raw_line.strip()
            if not raw_line:
                continue
            try:
                row = json.loads(raw_line.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
            if not isinstance(row, dict):
                continue
            post_id = row.get("post_id")
            media_index = row.get("media_index")
            out_rel = _safe_rel_string(row.get("out_path"))
            output_sha = row.get("output_sha256")
            model_verification = row.get("model_verification")
            if (not isinstance(post_id, str) or not post_id.strip()
                    or not isinstance(media_index, int) or isinstance(media_index, bool)
                    or media_index < 0
                    or not _valid_sha256(row.get("source_sha256"))
                    or not _valid_sha256(row.get("text_de_sha256"))
                    or not isinstance(row.get("prompt_version"), int)
                    or not isinstance(row.get("model"), str) or not row["model"].strip()
                    or (model_verification is not None
                        and model_verification not in {"response", "catalog"})
                    or not isinstance(row.get("size_requested"), str)
                    or not isinstance(row.get("size_returned"), str)
                    or not isinstance(row.get("quality"), str)
                    or not isinstance(row.get("created_at"), str)
                    or out_rel is None
                    or (output_sha is not None and not _valid_sha256(output_sha))):
                continue
            post_id = post_id.strip()
            if not _record_path_matches_key(post_id, media_index, out_rel):
                continue
            row["out_path"] = out_rel
            key = (post_id, media_index)
            latest[key] = row
            owned_paths.add(out_rel)
            owned_hashes.setdefault(out_rel, set()).add(output_sha)
    return ImageState(latest, owned_paths, owned_hashes)


def append_image_jsonl(path: Path, row: Mapping[str, Any]) -> None:
    """追加一条付费结果；坏尾补换行，写后 flush+fsync。"""
    paid_model.append_jsonl(path, dict(row), guard=lambda p: assert_physical_direct_path(
        p.parent, p, kind="file", label="images_de.jsonl"))


def ImageRunLock(path: Path) -> FileLock:      # noqa: N802（保留原名）
    """整个付费图片批次单实例，防止重复双击造成重复收费。"""
    return FileLock(path, error_type=SystemExit,
                    busy_message="另一个图片德语化批次正在运行。请勿重复双击；等它结束后再试。")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def text_de_sha256(text: str) -> str:
    return hashlib.sha256(text.strip().encode("utf-8")).hexdigest()


def _extension(output_format: str) -> str:
    return {"jpeg": ".jpg", "png": ".png", "webp": ".webp"}[output_format]


def image_record_is_current(job: ImageJob, record: Mapping[str, Any] | None) -> bool:
    """完成判据：任务书那五项，**外加产出路径必须就是这次要写的那个**。

    ⚠️ 与 IMAGE_PLAN 第 4 节的偏差，理由记在这里（CR-57）：
    路径由 ``output_format`` 与帖子目录名共同决定。只认那五项时，
    把 ``output_format`` 从 jpeg 改成 png 之后旧的 ``01.jpg`` 记录仍算"当前"，
    新的 ``01.png`` 永远不会生成；一旦 ``--force``，``media_de/`` 里会同时
    出现两个文件，而 ``publish/compose.py`` 见到同序号多候选就拒发（CR-48）。
    加这一项只会让判定**更保守**（更容易重做），不会漏掉本该重做的图。
    """
    if not isinstance(record, Mapping):
        return False
    return (record.get("post_id") == job.post_id
            and record.get("media_index") == job.media_index
            and record.get("source_sha256") == job.source_sha256
            and record.get("text_de_sha256") == job.text_de_sha256
            and record.get("prompt_version") == IMAGE_PROMPT_VERSION
            and record.get("out_path") == job.out_rel)


def _source_from_manifest(arc_base: Path, row: Mapping[str, Any],
                          media: Mapping[str, Any]) -> tuple[Path, str]:
    raw = _safe_rel_string(media.get("local_path"))
    if raw is None:
        raise ValueError("图片 local_path 缺失或不是安全相对路径")
    pure = PurePosixPath(raw)
    expected_dir = PurePosixPath(
        "posts", post_dirname(str(row["post_id"]), row.get("created_at")))
    if pure.parent != expected_dir:
        raise ValueError(
            f"图片 local_path 不在当前帖子目录：{raw}（预期 {expected_dir.as_posix()}/）")

    posts_dir = arc_base / "posts"
    post_dir = posts_dir / expected_dir.name
    source = post_dir / pure.name
    assert_physical_direct_path(arc_base, posts_dir, kind="directory", label="posts 根目录")
    assert_physical_direct_path(posts_dir, post_dir, kind="directory", label="帖子目录")
    assert_physical_direct_path(post_dir, source, kind="file", label="原图")
    if not source.is_file():
        raise ValueError(f"原图不存在：{raw}")
    return source, raw


def _target_for_job(arc_base: Path, row: Mapping[str, Any], media_index: int,
                    output_format: str) -> tuple[Path, str]:
    post_name = post_dirname(str(row["post_id"]), row.get("created_at"))
    out_rel = PurePosixPath(
        "posts", post_name, "media_de", f"{media_index + 1:02d}{_extension(output_format)}")
    out_path = arc_base.joinpath(*out_rel.parts)
    return out_path, out_rel.as_posix()


def _record_output_exists(arc_base: Path, record: Mapping[str, Any]) -> bool:
    out_rel = _safe_rel_string(record.get("out_path"))
    if out_rel is None:
        return False
    pure = PurePosixPath(out_rel)
    path = arc_base.joinpath(*pure.parts)
    try:
        assert_physical_direct_path(path.parent, path, kind="file", label="德语图")
    except ArchivePathError:
        return False
    if not path.is_file():
        return False
    expected_sha = record.get("output_sha256")
    return expected_sha is None or (
        _valid_sha256(expected_sha) and sha256_file(path) == expected_sha)


def _candidate_is_program_owned(candidate: Path, rel: str, state: ImageState) -> bool:
    expected_hashes = state.owned_hashes.get(rel)
    if not expected_hashes:
        return False
    # 旧版任务书 schema 没有 output_sha256；对这样的既有行保持兼容。
    if None in expected_hashes:
        return True
    return sha256_file(candidate) in expected_hashes


def manual_override_paths(job: ImageJob, state: ImageState) -> list[Path]:
    """同一序号下没有程序所有权记录的文件都视为人工版本。"""
    media_de = job.out_path.parent
    post_dir = media_de.parent
    assert_physical_direct_path(post_dir, media_de, kind="directory", label="media_de 目录")
    if not media_de.exists():
        return []
    manual: list[Path] = []
    for candidate in sorted(media_de.glob(f"{job.media_index + 1:02d}.*")):
        assert_physical_direct_path(media_de, candidate, kind="file", label="media_de 图片")
        rel = candidate.relative_to(job.arc_base).as_posix()
        if not _candidate_is_program_owned(candidate, rel, state):
            manual.append(candidate)
    return manual


def readonly_archive(arc_base: Path) -> Archive:
    """构造 Archive，但先确认目录已存在，绝不靠 mkdir 兜底。

    ``Archive.__init__`` 里有一句 ``posts_dir.mkdir(parents=True, exist_ok=True)``，
    所以直接构造它的 ``--estimate`` / ``--dry-run`` 并不是真的"零写盘"（CR-50）。
    先判后构造之后，目录已存在时 mkdir 是空操作，缺目录时给出可操作的错误
    而不是悄悄建一个空壳。做法与 ``publish/compose.py::_find_source`` 一致。
    """
    posts_dir = arc_base / "posts"
    assert_physical_direct_path(
        arc_base.parent, arc_base, kind="directory", label="账号归档目录")
    assert_physical_direct_path(
        arc_base, posts_dir, kind="directory", label="posts 根目录")
    if not arc_base.is_dir() or not posts_dir.is_dir():
        raise ValueError(
            f"账号归档与 posts/ 必须已存在，本模块只读不创建：{arc_base}")
    return Archive(arc_base.parent, arc_base.name)


def build_jobs(settings: Settings, arc_base: Path, rows: list[dict], *,
               force: bool = False,
               media_index_filter: int | None = None,
               report: Any = None) -> tuple[list[ImageJob], ImageState, RunStats]:
    """把帖子展开成图片任务；没有当前译文、人工覆盖或已完成项均不入队。

    **单张图的问题只跳过这一张**（CR-52）：原图缺失、无法解码、宽高比越界
    都记进 ``stats.skipped_bad_source`` 并继续，不再掀掉整个账号的批次。
    manifest 本身的形态错误仍然是致命的 —— 那是数据完整性问题，
    与 ``translate.py::SourceDataError`` 同一条纪律。
    """
    # 只读边界校验；不新建任何目录（CR-50）。
    readonly_archive(arc_base)
    translated = translation.load_translated(arc_base / "translated.jsonl")
    state = load_image_state(arc_base / "images_de.jsonl")
    stats = RunStats()
    jobs: list[ImageJob] = []

    for row in rows:
        if (not isinstance(row, dict)
                or not isinstance(row.get("post_id"), str) or not row["post_id"].strip()
                or not isinstance(row.get("text"), str)):
            raise ValueError("manifest 帖子记录不满足 post_id/text 最小契约")
        post_id = row["post_id"].strip()
        trans = translated.get(post_id)
        if not translation.translation_is_current(row, trans):
            image_count = sum(
                1 for index, media in enumerate(row.get("media") or [])
                if (media_index_filter is None or index == media_index_filter)
                and isinstance(media, dict) and media.get("kind") == "image")
            stats.skipped_no_translation += image_count
            continue
        text_de = trans["text_de"].strip()
        media_list = row.get("media") or []
        if not isinstance(media_list, list):
            raise ValueError(f"帖子 {post_id} 的 media 不是数组")
        for media_index, media in enumerate(media_list):
            if (media_index_filter is not None
                    and media_index != media_index_filter):
                continue
            if not isinstance(media, dict) or media.get("kind") != "image":
                continue
            # 这一段的任何失败都只影响这一张图：缺文件、坏字节、超过 3:1
            # 都是**单张素材**的问题，不该让同账号其它图片一张都跑不了（CR-52）。
            try:
                source_path, source_rel = _source_from_manifest(
                    arc_base, row, media)
                try:
                    with Image.open(source_path) as source_image:
                        source_image.load()
                        source_size = source_image.size
                except Exception as exc:
                    raise ValueError(f"原图无法解码：{source_rel}") from exc
                requested = legal_size(*source_size)
            except (ValueError, ArchivePathError) as exc:
                stats.skipped_bad_source += 1
                message = (f"  ! {post_id}[{media_index}] 跳过（素材问题）："
                           f"{type(exc).__name__}: {exc}")
                if report is None:
                    print(message)
                else:
                    report(message)
                continue
            out_path, out_rel = _target_for_job(
                arc_base, row, media_index, settings.output_format)
            job = ImageJob(
                account=arc_base.name,
                arc_base=arc_base,
                post_id=post_id,
                media_index=media_index,
                source_path=source_path,
                source_rel=source_rel,
                source_sha256=sha256_file(source_path),
                source_size=source_size,
                text_de=text_de,
                text_de_sha256=text_de_sha256(text_de),
                requested_size=requested,
                aspect_drift_percent=aspect_drift_percent(
                    *source_size, *requested),
                scale_factor=scale_factor(*source_size, *requested),
                out_path=out_path,
                out_rel=out_rel,
            )
            manual = manual_override_paths(job, state)
            if manual:
                stats.skipped_manual += 1
                print(f"  ! {post_id}[{media_index}] 跳过：发现人工德语图 "
                      + "、".join(path.name for path in manual))
                continue
            record = state.latest.get(job.key)
            if (not force and image_record_is_current(job, record)
                    and _record_output_exists(arc_base, record)):
                stats.skipped_current += 1
                continue
            jobs.append(job)
    jobs.sort(key=lambda job: (job.account, job.post_id, job.media_index))
    return jobs, state, stats


def _write_output(job: ImageJob, validated: ValidatedImage,
                  record: dict[str, Any], state: ImageState) -> None:
    """先 fsync 所有权记录，再原子替换图片，消除硬终止后的“假人工图”窗口。"""
    manual = manual_override_paths(job, state)
    if manual:
        raise RuntimeError("API 返回期间出现人工德语图，已放弃写盘："
                           + "、".join(path.name for path in manual))

    media_de = job.out_path.parent
    post_dir = media_de.parent
    assert_physical_direct_path(post_dir, media_de, kind="directory", label="media_de 目录")
    media_de.mkdir(parents=True, exist_ok=True)
    assert_physical_direct_path(post_dir, media_de, kind="directory", label="media_de 目录")
    assert_physical_direct_path(media_de, job.out_path, kind="file", label="德语图")

    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
                mode="wb", dir=media_de, prefix=f".{job.out_path.name}.",
                suffix=".tmp", delete=False) as handle:
            temporary = Path(handle.name)
            assert_physical_direct_path(
                media_de, temporary, kind="file", label="德语图临时文件")
            handle.write(validated.data)
            handle.flush()
            os.fsync(handle.fileno())
        # 生成期间人可能放入文件；在 replace 前再次以旧所有权集合判定。
        manual = manual_override_paths(job, state)
        if manual:
            raise RuntimeError("写盘前发现人工德语图，已放弃覆盖："
                               + "、".join(path.name for path in manual))
        # 此时付费结果已在临时文件里完整 fsync 且通过全部硬闸。先把绑定输出哈希的
        # 所有权记录 fsync：硬终止若发生在 replace 前，目标不存在/哈希不匹配，
        # 下次仍会重试；若发生在 replace 后，记录与文件同时成立，不会误判成人工图。
        append_image_jsonl(job.arc_base / "images_de.jsonl", record)
        state.latest[job.key] = record
        state.owned_paths.add(job.out_rel)
        state.owned_hashes.setdefault(job.out_rel, set()).add(record["output_sha256"])
        # JSONL fsync 本身有可见耗时。设计人员若恰在上一次检查之后放入同序号
        # 文件（含覆盖同名程序旧图），必须在原子替换前最后再判一次，不能覆盖。
        manual = manual_override_paths(job, state)
        if manual:
            raise RuntimeError("所有权记录落盘后发现人工德语图，已放弃覆盖："
                               + "、".join(path.name for path in manual))
        assert_physical_direct_path(media_de, job.out_path, kind="file", label="德语图")
        os.replace(temporary, job.out_path)
        temporary = None
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


def _is_fatal_api_error(exc: Exception) -> bool:
    """整批性错误的判据。实现在 core/paid_model，与 F 组共用同一份（CR-53）。

    K 组特有的整批性错误（模型目录预检失败、模型不匹配、模型不可用、
    响应契约破裂）通过 ``extra_fatal`` 补进去。
    """
    return paid_model.is_fatal_api_error(exc, extra_fatal=(
        ModelCatalogPreflightError, ModelMismatchError,
        ModelUnavailableError, ResponseContractError,
        paid_requests.PaidRequestBlocked))


def _scale_mark(job: ImageJob, settings: Settings) -> str:
    """放大倍数超线时的行内标记；只提示，不拦截（阈值未在真实产出上标定）。"""
    if job.scale_factor <= settings.scale_warn_factor:
        return ""
    return (f"  !放大 {job.scale_factor:.2f}x"
            f"（超过 scale_warn_factor={settings.scale_warn_factor:g}，"
            "德语图是放大件，请人眼确认清晰度）")


def run_localize(settings: Settings, editor: ImageEditor | None, arc_base: Path,
                 rows: list[dict], limit: int | None, force: bool,
                 dry_run: bool, media_index_filter: int | None = None) -> RunStats:
    """处理一个账号；离线 dry-run 会完整建任务/渲染提示词但零 API、零写盘。"""
    jobs, state, stats = build_jobs(
        settings, arc_base, rows, force=force,
        media_index_filter=media_index_filter)
    if limit is not None:
        jobs = jobs[:limit]
    stats.queued = len(jobs)
    print(f"\n=== {arc_base.name} ===")
    print(f"  选中 {len(rows)} 篇 / 待处理 {len(jobs)} 张 / "
          f"当前跳过 {stats.skipped_current} / 人工优先 {stats.skipped_manual} / "
          f"缺当前译文 {stats.skipped_no_translation} / "
          f"素材问题 {stats.skipped_bad_source}")
    prompt_cache: dict[str, str] = {}
    editor = editor if editor is not None else (None if dry_run else ImageEditor(settings))
    consecutive_failures = 0
    for index, job in enumerate(jobs, 1):
        prompt = prompt_cache.get(job.post_id)
        if prompt is None:
            prompt = build_image_prompt(settings, job.text_de)
            prompt_cache[job.post_id] = prompt
        size_string = f"{job.requested_size[0]}x{job.requested_size[1]}"
        scale_mark = _scale_mark(job, settings)
        if dry_run:
            print(f"  [{index}/{len(jobs)}] {job.post_id}[{job.media_index}] "
                  f"{job.source_size[0]}x{job.source_size[1]} -> {size_string} "
                  f"(dry-run，零 API / 零写盘){scale_mark}")
            continue

        try:
            manual = manual_override_paths(job, state)
            if manual:
                stats.skipped_manual += 1
                print(f"  [{index}/{len(jobs)}] {job.post_id}[{job.media_index}] "
                      "跳过：调用前发现人工德语图")
                continue
            started = time.monotonic()
            assert editor is not None
            paid_job_key = "image:" + hashlib.sha256(
                (job.account + "\0" + job.post_id + "\0"
                 + str(job.media_index) + "\0" + job.source_sha256 + "\0"
                 + job.text_de_sha256 + "\0" + str(IMAGE_PROMPT_VERSION))
                .encode("utf-8")).hexdigest()
            if hasattr(editor, "set_paid_context"):
                editor.set_paid_context(
                    paid_job_key, "%s:%s" % (job.account, job.post_id),
                    job.media_index)
            result = editor.edit(job.source_path, prompt, size_string)
            validated = validate_output(
                result.b64_json, job.source_path, job.requested_size,
                settings.output_format, settings.dhash_max_distance)
            elapsed = time.monotonic() - started
            record: dict[str, Any] = {
                "post_id": job.post_id,
                "media_index": job.media_index,
                "source_sha256": job.source_sha256,
                "text_de_sha256": job.text_de_sha256,
                "prompt_version": IMAGE_PROMPT_VERSION,
                "model": result.model,
                "model_verification": result.model_verification,
                "source_size": f"{job.source_size[0]}x{job.source_size[1]}",
                "size_requested": size_string,
                "size_returned": f"{validated.width}x{validated.height}",
                "quality": settings.quality,
                "output_format": settings.output_format,
                "out_path": job.out_rel,
                "output_sha256": hashlib.sha256(validated.data).hexdigest(),
                "aspect_drift_percent": round(job.aspect_drift_percent, 6),
                "scale_factor": round(job.scale_factor, 6),
                "dhash_distance": validated.dhash_distance,
                "elapsed_seconds": round(elapsed, 3),
                "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "usage": result.usage,
            }
            paid_request_id = str(getattr(editor, "paid_request_id", "") or "")
            if paid_request_id:
                record["paid_request_id"] = paid_request_id
            _write_output(job, validated, record, state)
            if hasattr(editor, "finalize_paid"):
                editor.finalize_paid(True, "localized image artifact fsynced")
            stats.succeeded += 1
            consecutive_failures = 0
            drift_mark = ("  !形变告警" if job.aspect_drift_percent
                          > settings.aspect_drift_warn_percent else "")
            print(f"  [{index}/{len(jobs)}] {job.post_id}[{job.media_index}] OK  "
                  f"dHash={validated.dhash_distance} / {elapsed:.1f}s"
                  f"{drift_mark}{scale_mark}")
        except Exception as exc:
            if editor is not None and hasattr(editor, "finalize_paid"):
                editor.finalize_paid(False, "localized image output rejected")
            stats.failed += 1
            consecutive_failures += 1
            print(f"  [{index}/{len(jobs)}] {job.post_id}[{job.media_index}] "
                  f"失败：{safe_error_summary(exc)}")
            if _is_fatal_api_error(exc):
                raise FatalBatchError(
                    "API 鉴权/端点/模型或共享请求错误，已停止剩余图片，"
                    "避免逐张重复无意义付费") from exc
            if consecutive_failures >= settings.failure_budget:
                raise FatalBatchError(
                    f"连续 {consecutive_failures} 张失败，已达 "
                    f"[image].failure_budget={settings.failure_budget}，停止本批。"
                    "\n    单张失败不再掀掉整批（CR-53），但连续失败说明问题不是"
                    "单张素材 —— 先看上面每条的原因，修掉后重跑即可"
                    "（已成功的不会重复付费）。") from exc
    return stats


# ---------------------------------------------------------------------------
# K8 给 translate.run_review 使用的只读配对
# ---------------------------------------------------------------------------

def review_image_pairs(arc_base: Path, row: Mapping[str, Any],
                       translated: Mapping[str, Any],
                       state: ImageState | None = None) -> list[ReviewImagePair]:
    """返回当前原图与可用德语图；旧程序产物不会错配新原图/新译文。"""
    state = state or load_image_state(arc_base / "images_de.jsonl")
    post_id = row.get("post_id")
    if not isinstance(post_id, str) or not post_id.strip():
        return []
    text_de = translated.get("text_de")
    if not isinstance(text_de, str) or not text_de.strip():
        return []
    media_list = row.get("media") or []
    if not isinstance(media_list, list):
        return []

    pairs: list[ReviewImagePair] = []
    for media_index, media in enumerate(media_list):
        if not isinstance(media, Mapping) or media.get("kind") != "image":
            continue
        try:
            source_path, source_rel = _source_from_manifest(arc_base, row, media)
        except (ValueError, ArchivePathError):
            # 旧布局/缺文件仍由 run_review 原有“配图”段展示；K8 只接受可物理比对的图片。
            continue
        source_sha = sha256_file(source_path)
        text_sha = text_de_sha256(text_de)
        record = state.latest.get((post_id, media_index))
        localized_rel: str | None = None
        current_record: dict[str, Any] | None = None
        manual = False
        # 人工版本永远优先于程序当前版本：设计人员可能保留 01.jpg 程序图，
        # 另放 01.png 修订图；K8 若先选程序记录，就会在唯一验收口审错文件。
        media_de = source_path.parent / "media_de"
        assert_physical_direct_path(
            source_path.parent, media_de, kind="directory", label="media_de 目录")
        if media_de.exists():
            for candidate in sorted(media_de.glob(f"{media_index + 1:02d}.*")):
                assert_physical_direct_path(
                    media_de, candidate, kind="file", label="media_de 图片")
                rel = candidate.relative_to(arc_base).as_posix()
                if not _candidate_is_program_owned(candidate, rel, state):
                    localized_rel = rel
                    manual = True
                    break
        if (not manual and isinstance(record, dict)
                and record.get("source_sha256") == source_sha
                and record.get("text_de_sha256") == text_sha
                and record.get("prompt_version") == IMAGE_PROMPT_VERSION
                and _record_output_exists(arc_base, record)):
            localized_rel = record["out_path"]
            current_record = record
        pairs.append(ReviewImagePair(
            media_index=media_index,
            source_rel=source_rel,
            localized_rel=localized_rel,
            record=current_record,
            manual=manual,
        ))
    return pairs


# ---------------------------------------------------------------------------
# 作用域、真实 usage 预算（K7）与 CLI
# ---------------------------------------------------------------------------

def select_rows(settings: Settings, dirs: list[Path], *,
                latest_posts: int | None = None,
                post_ids: list[str] | None = None,
                all_history: bool = False) -> dict[Path, list[dict]]:
    """默认只选立项后的增量；K9/历史必须通过显式作用域覆盖。"""
    rows_by_dir: dict[Path, list[dict]] = {}
    flat: list[tuple[Path, dict]] = []
    for arc_base in dirs:
        arc = readonly_archive(arc_base)          # 只读；不新建目录（CR-50）
        rows = arc.rows()
        rows_by_dir[arc_base] = rows
        flat.extend((arc_base, row) for row in rows)

    selected: dict[Path, list[dict]] = {arc_base: [] for arc_base in dirs}
    if post_ids:
        wanted = {post_id.strip() for post_id in post_ids if post_id.strip()}
        found: set[str] = set()
        for arc_base, row in flat:
            if row.get("post_id") in wanted:
                selected[arc_base].append(row)
                found.add(row["post_id"])
        missing = sorted(wanted - found)
        if missing:
            raise ValueError("指定 post_id 在所选账号归档中不存在：" + "、".join(missing))
        return selected

    if latest_posts is not None:
        if latest_posts <= 0 or latest_posts > MAX_LATEST_POSTS:
            raise ValueError(
                f"--latest-posts 必须在 1..{MAX_LATEST_POSTS}；"
                "它只用于 K9 最新三篇验收，不能替代全历史费用闸")
        ordered = sorted(
            flat,
            key=lambda pair: (
                str(pair[1].get("created_at") or ""),
                pair[0].name,
                str(pair[1].get("post_id") or "")),
            reverse=True,
        )[:latest_posts]
        for arc_base, row in ordered:
            selected[arc_base].append(row)
        return selected

    if all_history:
        return rows_by_dir

    # ISO UTC 固定宽度，可直接按字符串比较；不认识的旧时间不猜、不纳入增量。
    for arc_base, rows in rows_by_dir.items():
        selected[arc_base] = [
            row for row in rows
            if isinstance(row.get("created_at"), str)
            and row["created_at"] >= settings.incremental_since
        ]
    return selected


def image_usage_cost(settings: Settings, usage: Mapping[str, Any]) -> float | None:
    """按真实 token 和配置费率计算上游成本；不完整样本不编金额。"""
    if usage_contract_errors(usage):
        return None
    input_details = usage["input_tokens_details"]
    return (
        int(input_details["text_tokens"]) * float(settings.cost_rates["text_input"])
        + int(input_details["image_tokens"]) * float(settings.cost_rates["image_input"])
        + int(usage["output_tokens"]) * float(settings.cost_rates["image_output"])
    ) / 1_000_000


def run_estimate(settings: Settings, scoped_rows: Mapping[Path, list[dict]], *,
                 force: bool = False, limit: int | None = None,
                 media_index_filter: int | None = None) -> int:
    """离线统计待处理量，并只用当前提示词版本的真实 usage 中位数外推。"""
    remaining = limit
    total_pending = 0
    measured: list[dict[str, Any]] = []
    distances: list[int] = []
    print("=== GPT-Image-2 图片费用离线预算 ===")
    for arc_base, rows in scoped_rows.items():
        if remaining == 0:
            break
        jobs, state, stats = build_jobs(
            settings, arc_base, rows, force=force,
            media_index_filter=media_index_filter)
        if remaining is not None:
            jobs = jobs[:remaining]
        total_pending += len(jobs)
        if remaining is not None:
            remaining -= len(jobs)
        current_records = []
        for record in state.latest.values():
            if (record.get("prompt_version") == IMAGE_PROMPT_VERSION
                    and isinstance(record.get("usage"), Mapping)
                    and image_usage_cost(settings, record["usage"]) is not None
                    and _record_output_exists(arc_base, record)):
                current_records.append(record)
        measured.extend(current_records)
        distances.extend(
            int(record["dhash_distance"])
            for record in current_records
            if isinstance(record.get("dhash_distance"), int)
        )
        print(f"  {arc_base.name}: 选中 {len(rows)} 篇 / 待处理 {len(jobs)} 张 / "
              f"当前版本真实样本 {len(current_records)} 张 / "
              f"人工跳过 {stats.skipped_manual} / 素材问题 {stats.skipped_bad_source}")
        upscaled = [job for job in jobs
                    if job.scale_factor > settings.scale_warn_factor]
        if upscaled:
            print(f"    ! 其中 {len(upscaled)} 张需要放大超过 "
                  f"{settings.scale_warn_factor:g} 倍才能过像素下限，最大 "
                  f"{max(job.scale_factor for job in upscaled):.2f}x："
                  + "、".join(f"{job.post_id}[{job.media_index}]"
                              for job in upscaled[:5])
                  + ("…" if len(upscaled) > 5 else ""))

    print(f"\n待处理合计：{total_pending} 张")
    if not measured:
        print("还没有当前提示词版本的真实 usage 样本，不能可靠外推账单。")
        print("量级参考（不是上界）：任务书记录 1024² high 上游约 US$0.219/张；"
              "inferera 通常另有加价。先跑经确认的小批次，再重跑本命令。")
    else:
        costs = [image_usage_cost(settings, record["usage"]) for record in measured]
        complete_costs = [cost for cost in costs if cost is not None]
        median_cost = statistics.median(complete_costs)
        projected = median_cost * total_pending
        print(f"按 {len(complete_costs)} 张当前版本的真实 usage 中位数外推：")
        print(f"  上游中位成本 US${median_cost:.4f}/张 × {total_pending} 张 "
              f"= 约 US${projected:.2f}")
        print("  ⚠ 这是按 config.toml 上游 token 费率计算的量级；"
              "真实账单以 inferera 控制台为准，不把它冒充上界。")
    if distances:
        ordered = sorted(distances)
        print("\ndHash 真实距离分布（阈值仍为 -1，只记录）：")
        print(f"  n={len(ordered)} / min={ordered[0]} / "
              f"median={statistics.median(ordered):g} / max={ordered[-1]}")
        print("  values=" + ",".join(str(value) for value in ordered))
        print("  请结合原图/德语图人眼判定后再设置 dhash_max_distance；代码不会代拍板。")
    else:
        print("\n暂无真实 dHash 距离；第一批后本命令会打印完整分布供人眼标定。")
    print("\n注意：--estimate 全程离线，零 API 调用、零费用。")
    return 0


def run_show_prompt(settings: Settings,
                    scoped_rows: Mapping[Path, list[dict]]) -> int:
    """对选中范围内每个账号展示一篇真实当前译文的完整图片提示词。"""
    shown = 0
    for arc_base, rows in scoped_rows.items():
        translated = translation.load_translated(arc_base / "translated.jsonl")
        for row in sorted(
                rows,
                key=lambda value: str(value.get("created_at") or ""),
                reverse=True):
            post_id = row.get("post_id")
            entry = translated.get(post_id) if isinstance(post_id, str) else None
            if not translation.translation_is_current(row, entry):
                continue
            print("\n" + "=" * 72)
            print(f"账号：{arc_base.name} / post_id={post_id} / 零 API 调用")
            print("=" * 72)
            print(build_image_prompt(settings, entry["text_de"]))
            shown += 1
            break
    if not shown:
        print("[!] 选中范围没有当前版本 text_de；先完成 F 组翻译，或扩大离线展示范围。")
        return 1
    print(f"\n模板文件：{TEMPLATE_PATH}")
    print(f"图片提示词版本：{IMAGE_PROMPT_VERSION}（改模板时必须 +1）")
    return 0


# ---------------------------------------------------------------------------

def main(argv=None) -> int:
    force_utf8()
    parser = argparse.ArgumentParser(
        description="把归档图片中的英文文字替换成德语（K 组）",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--check", action="store_true",
        help="发送一次最小 low edits 请求验证 API（会产生少量真实费用）")
    mode.add_argument("--show-prompt", action="store_true",
                      help="渲染真实帖子的完整图片提示词，零 API 调用")
    mode.add_argument("--estimate", action="store_true",
                      help="按真实 usage 中位数离线外推费用，零 API 调用")
    mode.add_argument("--dry-run", action="store_true",
                      help="列出将处理的图片与合法尺寸，零 API、零写盘")
    parser.add_argument("--account", default=None,
                        help="只处理 archive/ 下一个账号目录，如 in_neakasa.tech")
    parser.add_argument("--limit", type=int, default=None,
                        help="所有账号合计最多处理 N 张图片")
    parser.add_argument(
        "--media-index", type=int, default=None,
        help="只处理单个 --post-id 的指定媒体序号（从 0 开始；0 对应 01.jpg）")
    parser.add_argument("--force", action="store_true",
                        help="当前指纹有效的程序产物也重做；人工文件仍绝不覆盖")
    scope = parser.add_mutually_exclusive_group()
    scope.add_argument("--latest-posts", type=int, default=None,
                       help="全账号合计只选最新 N 篇（K9 验收使用 3）")
    scope.add_argument("--post-id", action="append", default=None,
                       help="只选指定 post_id；可重复传入")
    scope.add_argument("--all-history", action="store_true",
                       help="显式选择全部历史；真实运行还需 --confirm-all-history-cost")
    parser.add_argument("--confirm-all-history-cost", action="store_true",
                        help="确认全历史真实费用；本期用户已决定不使用")
    args = parser.parse_args(argv)
    if args.limit is not None and args.limit < 0:
        parser.error("--limit 不能为负数")
    if args.media_index is not None:
        if args.media_index < 0:
            parser.error("--media-index 不能为负数")
        if not args.post_id or len(args.post_id) != 1:
            parser.error("--media-index 必须与恰好一个 --post-id 同时使用")
        if args.check or args.show_prompt:
            parser.error("--media-index 不能与 --check/--show-prompt 同时使用")
    if (args.latest_posts is not None
            and not 1 <= args.latest_posts <= MAX_LATEST_POSTS):
        parser.error(f"--latest-posts 必须在 1..{MAX_LATEST_POSTS}（只用于 K9 验收）")
    if args.confirm_all_history_cost and not args.all_history:
        parser.error("--confirm-all-history-cost 只能与 --all-history 同时使用")
    settings = Settings()
    if args.check:
        return run_check(
            settings,
            ImageEditor(
                settings,
                paid_controller=paid_requests.RequestController(cfg().state_dir)))

    root = cfg().archive_dir
    dirs = translation.account_dirs(root, args.account)
    if args.account and not dirs:
        available = [path.name for path in translation.account_dirs(root)]
        print(f"[!] archive/ 下没有账号目录 {args.account!r}。")
        print("    现有：" + ("、".join(available) if available else "（无）"))
        return 1
    if not dirs:
        print(f"[!] {root} 下没有含 manifest.jsonl 的账号目录；先完成抓取。")
        return 1

    try:
        scoped = select_rows(
            settings, dirs, latest_posts=args.latest_posts,
            post_ids=args.post_id, all_history=args.all_history)
    except ValueError as exc:
        print(f"[!] {exc}")
        return 1
    if args.media_index is not None:
        selected_exists = any(
            isinstance(media_list := row.get("media"), list)
            and args.media_index < len(media_list)
            and isinstance(media_list[args.media_index], dict)
            and media_list[args.media_index].get("kind") == "image"
            for rows in scoped.values()
            for row in rows
            if isinstance(row, dict)
        )
        if not selected_exists:
            print(f"[!] 指定帖子没有可处理的 media_index={args.media_index} 图片。")
            return 1

    # 离线命令也要失败闭合成可读的一行，而不是抛 traceback（CR-52）：
    # --estimate 是"要不要花这笔钱"的最后一道人类判断，它崩掉的代价是
    # 看不见预算就直接跑。
    try:
        if args.show_prompt:
            return run_show_prompt(settings, scoped)
        if args.estimate:
            return run_estimate(
                settings, scoped, force=args.force, limit=args.limit,
                media_index_filter=args.media_index)
    except (ValueError, ArchivePathError, OSError) as exc:
        print(f"[!] {type(exc).__name__}: {exc}")
        return 1
    if args.all_history and not args.dry_run and not args.confirm_all_history_cost:
        print("[!] 全历史真实运行被安全闸拒绝。818 张 high 约 US$179，且用户已决定不补历史。")
        print("    离线查看可加 --dry-run/--estimate；未来业务重新拍板后才可再加 "
              "--confirm-all-history-cost。")
        return 2

    editor = (None if args.dry_run else ImageEditor(
        settings,
        paid_controller=paid_requests.RequestController(cfg().state_dir)))
    total = RunStats()
    remaining = args.limit
    lock = nullcontext() if args.dry_run else ImageRunLock(cfg().state_dir / "images.lock")
    try:
        with lock:
            for arc_base in dirs:
                if remaining == 0:
                    break
                stats = run_localize(
                    settings, editor, arc_base, scoped.get(arc_base, []),
                    remaining, args.force, args.dry_run,
                    media_index_filter=args.media_index)
                for field in RunStats.__dataclass_fields__:
                    setattr(total, field, getattr(total, field) + getattr(stats, field))
                if remaining is not None:
                    remaining -= stats.queued
    except (FatalBatchError, ValueError, ArchivePathError, OSError) as exc:
        print(f"\n[!] {exc}")
        return 1

    if args.dry_run:
        print(f"\n预览完成：{total.queued} 张待处理；零 API 调用、零写盘。")
    else:
        print(f"\n完成：成功 {total.succeeded} 张 / 失败 {total.failed} 张 / "
              f"当前跳过 {total.skipped_current} / 人工优先 {total.skipped_manual} / "
              f"缺当前译文 {total.skipped_no_translation} / "
              f"素材问题 {total.skipped_bad_source}。")
        if total.succeeded:
            print("下一步：运行 translate.py --review 生成含原图/德语图的 K8 审校清单。")
    if total.skipped_bad_source:
        print(f"[!] 有 {total.skipped_bad_source} 张图因素材问题被跳过（原因见上），"
              "它们不会被静默当成已完成；修好素材后重跑即可。")
    return 1 if (total.failed or total.skipped_bad_source) else 0


if __name__ == "__main__":
    raise SystemExit(main())
