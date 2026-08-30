"""配置与 Chrome 路径探测（Windows 目标平台）。

开发机可能是 macOS，但部署固定为 Windows，因此路径探测以 Windows 为主，
其余平台保留最小回退，只为让核心逻辑能在开发机上跑测试。
"""
from __future__ import annotations

import os
import sys
import tomllib
from pathlib import Path

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
    """配置项允许写成一个数（两平台通用）或 ``{ facebook = 7, instagram = 21 }``。

    用**内联表**而不是 ``[delta.facebook]`` 子表，是为了避开 TOML 的排序陷阱：
    子表一旦插在普通键中间，它后面的键就全归子表了——项目里
    ``[translate.glossary]`` 已经因为这条规则专门写过警告。内联表是一行，
    放在哪儿都不改变语义。

    为什么需要按平台分：实测两个账号的节奏差一个量级——Facebook 发帖
    中位间隔 1.0 天（2026-08-25 还在发），Instagram 中位 1.6 天但已经
    连续 45 天没发。同一个阈值不可能同时适配这两种。
    """
    if isinstance(raw, dict):
        value = raw.get(platform)
        return default if value is None else value
    return default if raw is None else raw


class Config:
    def __init__(self, path: Path | str | None = None):
        p = Path(path) if path else ROOT / "config.toml"
        if not p.exists():
            raise SystemExit(f"缺少配置文件 {p}")
        self._d = tomllib.loads(p.read_text(encoding="utf-8"))

    def __getitem__(self, k: str):
        return self._d[k]

    def get(self, section: str, key: str, default=None):
        return self._d.get(section, {}).get(key, default)

    def get_platform(self, section: str, key: str, platform: str, default=None):
        """按平台取值。写成一个数时两平台通用，写成内联表时各取各的。"""
        return per_platform(self.get(section, key, None), platform, default)

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


def is_windows() -> bool:
    return sys.platform.startswith("win")


_cfg: Config | None = None


def cfg() -> Config:
    global _cfg
    if _cfg is None:
        _cfg = Config()
    return _cfg
