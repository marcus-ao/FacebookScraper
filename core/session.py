r"""共用常量与限速器。

设计前提（工程约束，不是合规约束）：账号被封是这条路径唯一不可恢复的失败模式，
所以这里的每个默认值都偏保守。

**本模块不含任何登录逻辑，这是刻意的。**
项目里只允许存在一条登录路径：人工在 scripts\start_chrome.bat 起的专用 Chrome
里登录一次，会话留在该 Chrome 的 profile 目录，由 CDP 附着复用。
不要在这里加 storageState 存取或自动登录——多一条登录路径就多一份
被 checkpoint 拦截的机会，且两份会话状态必然会漂移。

并发恒为 1，请求间隔随机化。这条路上没有任何值得用速度换的东西。
"""
from __future__ import annotations

import random
import time

# Safari UA：Instagram 的 web_profile_info 端点对 UA 敏感，
# 换成 Chrome UA 时该端点行为不一致。
# ⚠️ 该端点 2026-08-30 起对登出访客关闭，本常量随 core/http.py 一并保留备用。
SAFARI_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.0 Safari/605.1.15"
)


class Pacer:
    """随机化间隔的节流器。

    默认 6-14 秒，对应约 250-600 请求/小时的上限，明显低于观测到的封禁阈值。
    抓几十篇帖子总共几分钟，没有必要压这个数。
    """

    def __init__(self, lo: float = 6.0, hi: float = 14.0):
        self.lo, self.hi = lo, hi
        self._last = 0.0

    def wait(self) -> None:
        gap = random.uniform(self.lo, self.hi)
        elapsed = time.monotonic() - self._last
        if elapsed < gap:
            time.sleep(gap - elapsed)
        self._last = time.monotonic()

    def backoff(self, attempt: int) -> None:
        """指数退避 + 抖动。401/429 后调用。

        注意：Instagram 的 401 常常不是限流，而是会话失效或 doc_id 轮换。
        退避对那种 401 无效——等多久都不会恢复。重试 2 次仍失败就应该
        换路线或检查会话，而不是继续等。
        """
        delay = min(300.0, (2 ** attempt) * 15.0) * random.uniform(0.8, 1.2)
        time.sleep(delay)
