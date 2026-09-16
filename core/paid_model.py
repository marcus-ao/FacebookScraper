"""付费模型共用的客户端、限速、锁、费用校验与耐久写入。"""
from __future__ import annotations

import json
import math
import os
import sys
import time
from contextlib import AbstractContextManager
from pathlib import Path
from typing import Any, Iterable, Mapping

from core.config import ROOT, cfg

__all__ = [
    "FileLock", "FileLockBusy",
    "ModelCredentials",
    "validate_endpoint", "validate_cost_rates",
    "build_client", "is_fatal_api_error",
    "atomic_write_text", "append_jsonl",
    "usage_number", "PaidCaller", "read_jsonl",
]


class FileLockBusy(RuntimeError):
    """锁被别的进程持有。调用方按自己的语义决定是等还是失败闭合。"""


class FileLock(AbstractContextManager):
    """非阻塞跨进程独占锁；busy_message 指定占用提示。"""

    def __init__(self, path: Path | str, *, busy_message: str,
                 error_type: type[Exception] = FileLockBusy) -> None:
        self.path = Path(path)
        self.busy_message = busy_message
        self.error_type = error_type
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
        except (OSError, BlockingIOError) as exc:
            self._file.close()
            self._file = None
            raise self.error_type(self.busy_message) from exc
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


class ModelCredentials:
    """读取密钥：环境变量优先，其次项目 .env。"""

    def __init__(self, env_name: str, *, what: str = "API 密钥") -> None:
        self.env_name = env_name
        self.what = what

    def _raw(self) -> tuple[str, str]:
        """返回 ``(密钥, 来源)``。**调用方不得打印第一个值。**"""
        key = os.environ.get(self.env_name, "").strip()
        if key:
            return key, "环境变量 %s" % self.env_name
        env_file = Path(cfg().get('runtime', 'env_file', str(ROOT / '.env')))
        if env_file.exists():
            for line in env_file.read_text(encoding="utf-8-sig").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                name, _, value = line.partition("=")
                if name.strip() == self.env_name:
                    return value.strip().strip('"').strip("'"), "%s 文件" % env_file.name
        return "", ""

    def optional_value(self) -> str:
        """Read an optional credential without logging its value or raising SystemExit."""
        return self._raw()[0]

    def api_key(self) -> str:
        key, _ = self._raw()
        if key:
            return key
        raise SystemExit(
            "没找到%s（变量名 %s）。两种设法任选其一：\n"
            "\n"
            "  【推荐】先复制项目里的 .env.example 为 .env，再把占位值替换成密钥：\n"
            "      Copy-Item .env.example .env\n"
            "  .env 内容应为一行：\n"
            "      %s=你的密钥\n"
            "      （.env 已在 .gitignore 里，不会进版本库）\n"
            "\n"
            "  【或者】设系统环境变量：\n"
            "      setx %s \"你的密钥\"\n"
            "      设完必须**重开终端 / 重新双击 .bat** 才生效\n"
            "\n"
            "  ❌ 不要写进 config.toml —— 那个文件会进版本库。"
            % (self.what, self.env_name, self.env_name, self.env_name))

    def status(self) -> str:
        """给 ``--check`` 打印用。只报告是否存在与来源，不暴露密钥值。"""
        key, source = self._raw()
        return "已设置（来源：%s）" % source if key else "(未设置)"


def validate_endpoint(section: str, *, timeout: float, max_retries: int,
                      gap: float) -> None:
    """两个付费阶段共有的三个连接参数。曾经这段在两边逐字相同。"""
    if timeout <= 0 or max_retries < 0 or gap < 0:
        raise SystemExit(
            "[%s] 的 timeout_seconds 必须 > 0，"
            "max_retries/request_gap_seconds 必须 >= 0" % section)


def validate_cost_rates(section: str, rates: Mapping[str, Any],
                        required: Iterable[str]) -> None:
    """校验费率键及非负有限数值；bool 不视为数值。"""
    required = set(required)
    if set(rates) != required:
        raise SystemExit(
            "[%s].cost_rates_usd_per_million 必须且只能包含 %s"
            % (section, "/".join(sorted(required))))
    for value in rates.values():
        if (not isinstance(value, (int, float)) or isinstance(value, bool)
                or not math.isfinite(float(value)) or float(value) < 0):
            raise SystemExit(
                "[%s].cost_rates_usd_per_million 的费率必须是非负有限数字" % section)


def usage_number(value: Any) -> float | None:
    """读取非负有限 usage 数值；缺失返回 None，不能按零费用放行。"""
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    number = float(value)
    if not math.isfinite(number) or number < 0:
        return None
    return number


def build_client(*, api_key: str, base_url: str, timeout: float,
                 max_retries: int):
    """构造 OpenAI 兼容客户端，由 SDK 处理鉴权和退避重试。"""
    from openai import OpenAI

    return OpenAI(api_key=api_key, base_url=base_url,
                  timeout=timeout, max_retries=max_retries)


def is_fatal_api_error(exc: BaseException, *,
                       extra_fatal: tuple[type, ...] = ()) -> bool:
    """鉴权、权限、端点和响应契约错误停止整批；429、超时与单条 400 计入连续失败预算。"""
    if extra_fatal and isinstance(exc, extra_fatal):
        return True
    try:
        import openai
    except ImportError:
        return False
    fatal = tuple(
        candidate for candidate in (
            getattr(openai, "AuthenticationError", None),
            getattr(openai, "PermissionDeniedError", None),
            getattr(openai, "NotFoundError", None),
            getattr(openai, "APIResponseValidationError", None),
        ) if isinstance(candidate, type))
    return bool(fatal) and isinstance(exc, fatal)


def atomic_write_text(path: Path, text: str, *, guard=None,
                      newline: str = "\n") -> None:
    """同目录写入、fsync 后原子替换；guard 核验目标、临时文件及替换前的目标。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if guard is not None:
        guard(path, "target")
    temporary = path.with_name("." + path.name + ".tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline=newline) as handle:
            if guard is not None:
                guard(temporary, "temp")
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        if guard is not None:
            guard(path, "target")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def atomic_write_json(path: Path, value: Any, *, guard=None,
                      indent: int | None = None, sort_keys: bool = False) -> None:
    """:func:`atomic_write_text` 的 JSON 版。"""
    text = json.dumps(value, ensure_ascii=False, indent=indent,
                      sort_keys=sort_keys)
    atomic_write_text(path, text + ("\n" if indent is not None else ""),
                      guard=guard)


def append_jsonl(path: Path, row: Mapping[str, Any], *, guard=None) -> None:
    """追加 JSONL 并 fsync；先隔开上次残行，guard 在写入前核验路径。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if guard is not None:
        guard(path)
    with path.open("a+b") as handle:
        if handle.seek(0, os.SEEK_END) > 0:
            handle.seek(-1, os.SEEK_END)
            if handle.read(1) != b"\n":
                handle.write(b"\n")
        handle.write(
            (json.dumps(row, ensure_ascii=False) + "\n").encode("utf-8"))
        handle.flush()
        os.fsync(handle.fileno())


class PaidCaller:
    """共用付费请求状态；子类初始化 _init_paid 并实现实际请求。"""

    def _init_paid(self, settings, client, paid_controller) -> None:
        self.s = settings
        self._client = client
        self._last_call = 0.0
        self._paid_controller = paid_controller
        self._no_sdk_retries = paid_controller is not None
        self._paid_job_key = ""
        self._paid_source_ref = ""
        self._paid_media_index: int | None = None
        self._paid_receipt = None

    def set_paid_context(self, job_key: str, source_ref: str,
                         media_index: int | None = None) -> None:
        self._paid_job_key = str(job_key)
        self._paid_source_ref = str(source_ref)
        self._paid_media_index = (None if media_index is None
                                  else int(media_index))

    def disable_sdk_retries(self) -> None:
        """外层自行登记付费（如风险预扫）时，同样禁止 SDK 隐式再次发送。"""
        self._no_sdk_retries = True

    @property
    def paid_request_id(self) -> str:
        return self._paid_receipt.request_id if self._paid_receipt else ""

    def finalize_paid(self, accepted: bool, reason: str = "") -> None:
        if self._paid_controller is None or self._paid_receipt is None:
            return
        receipt = self._paid_receipt
        self._paid_controller.finalize(receipt, accepted=accepted, reason=reason)
        self._paid_receipt = None

    @property
    def client(self):
        """惰性创建客户端，使 dry-run/estimate 无需凭据。"""
        if self._client is None:
            self._client = build_client(
                api_key=self.s.api_key(), base_url=self.s.base_url,
                timeout=self.s.timeout, max_retries=self.s.max_retries)
        if self._no_sdk_retries:
            from openai import OpenAI
            # SDK 重试会把首次超时后的第二笔费用藏进同一请求；核账必须先于再次发送。
            # 注入的 SDK 客户端也要遵守这条规则，复制选项不修改调用方的实例。
            if isinstance(self._client, OpenAI) and self._client.max_retries != 0:
                self._client = self._client.with_options(max_retries=0)
        return self._client

    def _pace(self) -> None:
        """相邻两次付费调用的最小间隔。"""
        gap = self.s.gap
        if gap <= 0:
            return
        elapsed = time.monotonic() - self._last_call
        if elapsed < gap:
            time.sleep(gap - elapsed)
        self._last_call = time.monotonic()


def read_jsonl(path: Path, *, on_corrupt, transform=None) -> list[dict]:
    """读取 JSONL，空行跳过、坏行报错；回调接收真实行号，transform 校验业务字段。"""
    path = Path(path)
    if not path.is_file():
        return []
    rows: list[dict] = []
    for number, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise on_corrupt(path, number, exc)
        if not isinstance(row, dict):
            raise on_corrupt(path, number, None)
        rows.append(row if transform is None else transform(row, path, number))
    return rows
