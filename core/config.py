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
