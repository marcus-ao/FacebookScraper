"""配置加载与 Chrome 路径探测。"""
from __future__ import annotations

import os
import math
import re
import tomllib
from dataclasses import dataclass
from datetime import datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent

# Windows 上 Chrome 的两种常见安装位置，外加 32 位路径
CHROME_CANDIDATES = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
    # 开发机回退
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
]


def per_platform(raw, platform: str, default):
    """读取平台阈值：支持通用数值或 facebook/instagram 内联表。"""
    if isinstance(raw, dict):
        value = raw.get(platform)
        return default if value is None else value
    return default if raw is None else raw


class Config:
    def __init__(self, path: Path | str | None = None, *, runtime_path: Path | str | None = None):
        p = Path(path) if path else ROOT / "config.toml"
        if not p.exists():
            raise SystemExit(f"缺少配置文件 {p}")
        self._d = tomllib.loads(p.read_text(encoding="utf-8"))
        self.path = p.resolve()
        self._file_stamp = (p.stat().st_mtime_ns, p.stat().st_size)
        self.runtime_path = Path(runtime_path).resolve() if runtime_path else None
        if self.runtime_path and self.runtime_path.exists():
            local = tomllib.loads(self.runtime_path.read_text(encoding='utf-8'))
            allowed = {'paths': {'archive', 'state'}, 'runtime': {'python', 'env_file', 'backup_manifest'}}
            for section, values in local.items():
                if section not in allowed or not isinstance(values, dict) or set(values) - allowed[section]:
                    raise ValueError('本机配置只能绑定路径，不能覆盖业务规则：' + section)
                for key, value in values.items():
                    if not isinstance(value, str) or not value.strip() or not Path(value).is_absolute():
                        raise ValueError(f'本机配置 {section}.{key} 必须为绝对路径')
                self._d.setdefault(section, {}).update(values)

    def __getitem__(self, k: str):
        return self._d[k]

    def get(self, section: str, key: str, default=None):
        return self._d.get(section, {}).get(key, default)

    def active_accounts(self) -> tuple[str, ...]:
        """只有当前监测目标进入自动处理；历史账号仍可显式只读浏览。"""
        names = []
        for platform in ("facebook", "instagram"):
            account = str(self.get("targets", platform, "")).strip().lower()
            if not account or not re.fullmatch(r"[a-z0-9_.]+", account) or ".." in account:
                raise ValueError(f"[targets].{platform} 不是有效账号名")
            names.append(f"{platform[:2]}_{account}")
        return tuple(names)

    # ---- 派生路径 ----
    @property
    def archive_dir(self) -> Path:
        return ROOT / self.get("paths", "archive", "archive")

    @property
    def state_dir(self) -> Path:
        d = ROOT / self.get("paths", "state", "state")
        d.mkdir(parents=True, exist_ok=True)
        return d

    @property
    def profile_dir(self) -> Path:
        raw = self.get("chrome", "profile_dir", "~/.fbscraper-chrome")
        return Path(os.path.expandvars(raw)).expanduser()

    @property
    def debug_port(self) -> int:
        return int(self.get("chrome", "debug_port", 9222))

    @property
    def detect_profile_dir(self) -> Path:
        raw = self.get("detect", "profile_dir", "~/.fbscraper-detect")
        return Path(os.path.expandvars(raw)).expanduser()

    @property
    def detect_debug_port(self) -> int:
        return int(self.get("detect", "port", 9224))

    def assert_chrome_profiles_isolated(self) -> None:
        roles = (("chrome", self.debug_port, self.profile_dir),
                 ("detect", self.detect_debug_port, self.detect_profile_dir),
                 ("publish", self.publish_debug_port, self.publish_profile_dir))
        for i, (name, port, profile) in enumerate(roles):
            if not 1024 <= port <= 65535:
                raise SystemExit(f"[{name}] CDP 端口必须在 1024..65535")
            for other, other_port, other_profile in roles[:i]:
                if port == other_port or profile.resolve() == other_profile.resolve():
                    raise SystemExit(f"[{name}] 与 [{other}] 必须使用独立端口和 profile")

    @property
    def publish_profile_dir(self) -> Path:
        """发布账号专用 profile；绝不能与抓取小号的 profile 混用。"""
        raw = self.get("publish", "profile_dir", "~/.fbscraper-publish")
        return Path(os.path.expandvars(raw)).expanduser()

    @property
    def publish_debug_port(self) -> int:
        """发布 Chrome 的 CDP 端口，与抓取侧 9222 并存。"""
        return int(self.get("publish", "debug_port", 9223))

    def assert_publish_chrome_isolated(self) -> None:
        """误配成抓取目标时失败闭合，不能只靠默认配置碰巧写对。"""
        if self.publish_debug_port == self.debug_port:
            raise SystemExit(
                "[publish].debug_port 与 [chrome].debug_port 相同；"
                "发布与抓取必须使用两个独立端口。")
        if (self.publish_profile_dir.resolve(strict=False)
                == self.profile_dir.resolve(strict=False)):
            raise SystemExit(
                "[publish].profile_dir 与 [chrome].profile_dir 指向同一路径；"
                "禁止让 DE 发布账号与抓取小号共用浏览器 profile。")
        self.assert_chrome_profiles_isolated()

    @property
    def chrome_exe(self) -> str:
        configured = (self.get("chrome", "exe", "") or "").strip()
        if configured:
            if not Path(configured).exists():
                raise SystemExit(f"config.toml 里的 chrome.exe 不存在：{configured}")
            return configured
        env = os.environ.get("CHROME_EXE", "").strip()
        if env and Path(env).exists():
            return env
        for c in CHROME_CANDIDATES:
            if c and Path(c).exists():
                return c
        raise SystemExit(
            "未找到 Chrome。请在 config.toml 的 [chrome].exe 填写完整路径，"
            "或设置环境变量 CHROME_EXE。\n已尝试：\n  " + "\n  ".join(CHROME_CANDIDATES)
        )


_cfg: Config | None = None


def cfg() -> Config:
    global _cfg
    if _cfg is None:
        _cfg = Config(runtime_path=os.environ.get('FBSCRAPER_RUNTIME_CONFIG') or ROOT / 'config.local.toml')
    elif hasattr(_cfg, '_file_stamp'):
        current = _cfg.path.stat()
        if (current.st_mtime_ns, current.st_size) != _cfg._file_stamp:
            _cfg = Config(_cfg.path, runtime_path=_cfg.runtime_path)
    return _cfg


def invalidate_cfg_cache() -> None:
    """本进程写配置后显式失效缓存；等长快速改写可能保留同一 mtime 和 size。"""
    global _cfg
    if _cfg is not None:
        _cfg = Config(_cfg.path, runtime_path=_cfg.runtime_path)


@dataclass(frozen=True)
class MonitorSchedule:
    """监测与消息静默共用的上海作息；只计算时间，不执行任务。"""

    on_duty_window: tuple[str, str] = ("08:00", "19:00")
    on_duty_interval_min: float = 60
    off_duty_interval_min: float = 180
    jitter_ratio: float = 0.25
    reconcile_at: str = "07:00"
    reconcile_jitter_min: float = 30
    reconcile_scrolls: int = 3
    reconcile_max_session_seconds: float = 90
    processing_budget_min: float = 25

    def __post_init__(self):
        if len(self.on_duty_window) != 2:
            raise ValueError("on_duty_window 必须包含起止两个 HH:MM")
        start, end = (self.parse_time(v) for v in self.on_duty_window)
        self.parse_time(self.reconcile_at)
        if start >= end:
            raise ValueError("在岗窗必须是上海同一天内的递增时段")
        if not all(math.isfinite(value) for value in (
                self.on_duty_interval_min, self.off_duty_interval_min, self.jitter_ratio,
                self.reconcile_jitter_min, self.reconcile_max_session_seconds, self.processing_budget_min)):
            raise ValueError("监测间隔与预算不能为 NaN 或无穷大")
        if (self.on_duty_interval_min <= 0
                or self.off_duty_interval_min < self.on_duty_interval_min
                or not 0 < self.jitter_ratio < 1
                or self.reconcile_jitter_min <= 0
                or self.reconcile_scrolls < 1
                or self.reconcile_max_session_seconds <= 0
                or self.processing_budget_min < 0):
            raise ValueError("监测间隔、抖动和兜底预算必须为有效正值")
        if self.reconcile_deadline_margin_minutes() <= 0:
            raise ValueError("兜底最晚触发加抓取和处理预算超过在岗截止线；请提前 reconcile_at")

    @staticmethod
    def parse_time(value: str) -> time:
        if not isinstance(value, str) or not re.fullmatch(r"\d{2}:\d{2}", value):
            raise ValueError("时刻必须使用 HH:MM")
        return time.fromisoformat(value)

    @staticmethod
    def local(now: datetime) -> datetime:
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("监测时刻必须包含时区")
        return now.astimezone(ZoneInfo("Asia/Shanghai"))

    def is_on_duty(self, now: datetime) -> bool:
        start, end = (self.parse_time(v) for v in self.on_duty_window)
        return start <= self.local(now).time() < end

    def interval_minutes(self, now: datetime, *, quiet: bool = False) -> float:
        return (self.on_duty_interval_min if self.is_on_duty(now)
                else self.off_duty_interval_min)

    def minimum_interval_minutes(self, now: datetime, *, quiet: bool = False) -> float:
        return self.interval_minutes(now, quiet=quiet) * (1 - self.jitter_ratio)

    def reconcile_deadline_margin_minutes(self, platform_count: int = 2) -> float:
        def minute(value):
            parsed = self.parse_time(value)
            return parsed.hour * 60 + parsed.minute
        return (minute(self.on_duty_window[0]) - minute(self.reconcile_at)
                - self.reconcile_jitter_min - self.processing_budget_min
                - platform_count * self.reconcile_max_session_seconds / 60)

    @classmethod
    def load(cls, c: Config | None = None) -> "MonitorSchedule":
        c = c or cfg()
        return cls(
            on_duty_window=tuple(c.get("delta", "on_duty_window", ["08:00", "19:00"])),
            on_duty_interval_min=float(c.get("delta", "on_duty_interval_min", 60)),
            off_duty_interval_min=float(c.get("delta", "off_duty_interval_min", 180)),
            jitter_ratio=float(c.get("delta", "jitter_ratio", 0.25)),
            reconcile_at=c.get("delta", "reconcile_at", "07:00"),
            reconcile_jitter_min=float(c.get("delta", "reconcile_jitter_min", 30)),
            reconcile_scrolls=int(c.get("delta", "reconcile_scrolls", 3)),
            reconcile_max_session_seconds=float(c.get("delta", "reconcile_max_session_seconds", 90)),
            processing_budget_min=float(c.get("delta", "processing_budget_min", 25)),
        )
