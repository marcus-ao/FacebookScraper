r"""付费模型调用的共用底座。文本翻译（F 组）与图内德语化（K 组）都走这里。

**为什么有这个模块。** 2026-09-02 的架构审查发现 `translate.py` 与
`localize_images.py` 是同一个模块被写了两遍：凭据读取、端点校验、客户端构造、
限速、文件锁、usage 契约、成本计算、JSONL 落盘，17 组成对函数里有 8 组
相似度 > 0.74，其中 timeout/retries 校验相似度是 1.00。

双份实现不会停在"重复"，它会**漂移**，而且漂移是静默的：

    CR-53 查清了 `isinstance(exc, openai.APIError)` 是错的——那是
    RateLimitError(429)、APITimeoutError、BadRequestError(400) 的共同基类，
    一张有问题的图可以永久堵住队列。修复只落到了 localize_images.py，
    translate.py 直到 2026-09-02 仍是那个被判定为错的旧实现。
    结果：同一次 429，翻译阶段掀掉整批 1051 篇，调图阶段只算单条失败。

所以这里的规矩是：**任何两个付费阶段都会做的事，实现只能有一份。**
阶段特有的东西（提示词、产出校验、业务闸）留在各自模块，不要往这里塞。
"""
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


# ==========================================================================
# 跨进程文件锁
#
# 曾经这个类在仓库里有 5 份逐字节相同的副本（core/paid_requests.py、
# routes/delta.py、publish/journal.py、pipeline_assisted.py、translate.py），
# 差异只有类名、docstring 和抛出的异常类型。
# ==========================================================================

class FileLockBusy(RuntimeError):
    """锁被别的进程持有。调用方按自己的语义决定是等还是失败闭合。"""


class FileLock(AbstractContextManager):
    """跨进程独占锁。Windows 走 msvcrt，其它平台走 fcntl，都是非阻塞。

    ``busy_message`` 是拿不到锁时给人看的那句话——这是各调用点唯一需要
    定制的东西，所以它是参数而不是子类。
    """

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


# ==========================================================================
# 凭据
# ==========================================================================

class ModelCredentials:
    """密钥读取。环境变量优先，其次项目内的 ``.env``。

    支持 .env 是因为 ``setx`` 会把密钥写进用户注册表、对所有进程可见；
    放项目里的 .env 收敛得多（已在 .gitignore 中）。不引入 python-dotenv：
    只需要 ``KEY=value`` 这一种形态，十行够了，不值得多一个依赖。
    """

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


# ==========================================================================
# 配置校验
# ==========================================================================

def validate_endpoint(section: str, *, timeout: float, max_retries: int,
                      gap: float) -> None:
    """两个付费阶段共有的三个连接参数。曾经这段在两边逐字相同。"""
    if timeout <= 0 or max_retries < 0 or gap < 0:
        raise SystemExit(
            "[%s] 的 timeout_seconds 必须 > 0，"
            "max_retries/request_gap_seconds 必须 >= 0" % section)


def validate_cost_rates(section: str, rates: Mapping[str, Any],
                        required: Iterable[str]) -> None:
    """费率表：键必须精确匹配，值必须是非负有限数字（且不能是 bool）。

    排除 bool 不是洁癖：``isinstance(True, int)`` 为真，而 ``True`` 会被
    当成费率 1.0 静默算进成本。
    """
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
    """把 usage 里的一个字段读成非负有限数字，读不出来返回 None。

    调用方据此决定"算得出钱"还是"记成 unknown"——**不要在这里猜 0**：
    把未知当成 0 会让预算闸悄悄放行。
    """
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    number = float(value)
    if not math.isfinite(number) or number < 0:
        return None
    return number


# ==========================================================================
# 客户端
# ==========================================================================

def build_client(*, api_key: str, base_url: str, timeout: float,
                 max_retries: int):
    """构造 OpenAI 兼容客户端。

    SDK 会按官方约定使用 ``Authorization: Bearer``，并负责连接错误、429 与
    5xx 的退避重试；业务代码只负责请求与结果契约。
    """
    from openai import OpenAI

    return OpenAI(api_key=api_key, base_url=base_url,
                  timeout=timeout, max_retries=max_retries)


def is_fatal_api_error(exc: BaseException, *,
                       extra_fatal: tuple[type, ...] = ()) -> bool:
    """这个异常是否"整批都会重复犯"，从而应该立刻停止而不是跳过单条？

    ⛔ **不要退回 ``isinstance(exc, openai.APIError)``。** 那是
    ``RateLimitError``(429)、``APITimeoutError`` / ``APIConnectionError`` 和
    ``BadRequestError``(400，含内容审核拒绝) 的**共同基类**。用它当判据的话：
    SDK 重试用尽后的一次 429、一次网络抖动、或某一条被审核拒掉，都会掀掉
    剩余全部条目，而且没有跳过这一条继续的办法——一条有问题的输入可以
    永久堵住队列。这就是 CR-53。

    只有鉴权 / 权限 / 端点不存在 / 响应契约破裂算致命：这几类**每一条都会
    同样失败**，继续跑只是重复花钱。瞬时错误与单条 400 计为单条失败，由
    调用方的 ``failure_budget`` 连续失败计数兜住。

    ``extra_fatal`` 给各阶段补充自己的整批性错误（比如模型不匹配、
    模型目录预检失败）。
    """
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


# ==========================================================================
# 落盘
#
# 曾经原子写在仓库里有 6 份实现，临时文件清理写了三种不同的写法。
# ==========================================================================

def atomic_write_text(path: Path, text: str, *, guard=None,
                      newline: str = "\n") -> None:
    """同目录临时文件 → flush → fsync → ``os.replace``。

    临时文件必须和目标**同目录**：跨卷时 ``os.replace`` 不是原子的。

    ``guard(path, role)`` 让调用方插入路径安全断言，``role`` 是
    ``"target"`` 或 ``"temp"``。它会被调用三次：写之前验目标、写之后验
    临时文件、**替换之前再验一次目标**——最后那次是防目标在写临时文件
    期间被换成链接（core/store.py 原本就这么做，合并时保留了）。
    """
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
    """向真相源追加一行并 fsync。

    ⚠️ 先补一个换行再写：上一次写入若在换行前被中断，不补的话这一行会和
    残行粘成一行，两行一起变成无法解析的垃圾。

    ``guard`` 在写之前对目标路径做一次检查（各阶段用它做路径安全断言）。
    """
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
    """付费模型调用方的共用状态机。

    ``translate.Translator`` 与 ``localize_images.ImageEditor`` 原本各写了一遍
    这一整套：客户端惰性构造、调用间隔、付费上下文（job_key / source_ref /
    media_index）、收据与闭合。八个成员逐字节相同，只有 ``set_paid_context``
    的 ``media_index`` 是图片侧特有的。

    子类负责：在 ``__init__`` 里调 ``_init_paid``，以及真正发请求那一步。
    """

    def _init_paid(self, settings, client, paid_controller) -> None:
        self.s = settings
        self._client = client
        self._last_call = 0.0
        self._paid_controller = paid_controller
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
        """惰性构造。**不要在 __init__ 里建**：``--dry-run`` / ``--estimate``
        不该因为密钥没设就退出。"""
        if self._client is None:
            self._client = build_client(
                api_key=self.s.api_key(), base_url=self.s.base_url,
                timeout=self.s.timeout, max_retries=self.s.max_retries)
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
    """读一份追加式真相源。空行跳过，**坏行失败闭合**，非对象行也失败闭合。

    ``on_corrupt(path, line_number, exc_or_none)`` 返回要抛的异常——各真相源
    的异常类型与文案不同（付费账本、发布留痕、待人工确认队列），但"读法"
    只该有一份。

    ``transform(row, path, line_number)`` 在每一行上做各自的字段校验/补齐。
    它拿到的是**真实行号**（空行不计入序号会让报错指错地方）。

    ⚠️ 坏行绝不能静默跳过：这些文件是钱和"发出去了没有"的唯一凭据，
    少读一行就是少算一笔或漏掉一次未闭合的提交。
    """
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
