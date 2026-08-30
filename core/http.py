"""登出增量专用的 HTTP 客户端。对应实施计划的 C1。

⚠️ **2026-08-30 起本模块不再被使用，但保留，不要删。**
它是给"增量完全登出"那条路径写的。实测 + 用户确认，FB/IG 在没有登录态时
直接报错（`web_profile_info` 对登出访客首次请求即 429），该路径不存在了。
用户拍板走方案 B：增量改走登录态 CDP，媒体下载走浏览器请求栈
（复用它的 cookie 与 TLS 指纹），两处都用不到这个客户端。

保留是因为：端点若重新对登出开放，切回去是**风险更低**的路径，
而这份实现是对的（17 项断言全过，含 Set-Cookie 攻防）。
静态检查报"未使用"是预期的。

**这个模块存在的唯一理由是「一个 cookie 都不带」。**
增量路径每天跑，如果它带上了会话，封号风险就从"一次性"变成"累积性"——
而登出访问没有账号、没有 session、没有 cookie，**因此没有可封的东西**，
最坏情况只是 IP 被临时限流，换个时间重试即可。

注意：光是 `httpx.Client()` 不够。httpx 默认带一个 cookie jar，
它会**接受响应里的 Set-Cookie 并在后续请求里带回去**——跑几次之后
这条路径就悄悄地不再是"登出"的了，而且没有任何迹象。
所以这里显式装一个「什么都不接受」的 jar，见 _NO_COOKIES。
"""
from __future__ import annotations

import http.cookiejar

import httpx

from core.session import SAFARI_UA

# 登出请求的超时。给足余量：宁可等，也不要因为超时去重试（重试会加重触发登录墙）
TIMEOUT_SECONDS = 30

# Instagram 的 web 端点必需的 App ID。这是个**公开且长期稳定**的常量，
# 与每 2–4 周轮换的 doc_id 完全是两回事，可以放心写死。
IG_APP_ID = "936619743392459"
IG_ASBD_ID = "198387"


def _blocking_jar() -> http.cookiejar.CookieJar:
    """一个拒绝存储任何 cookie 的 jar。

    `allowed_domains=[]` 是空列表而非 None —— 空列表表示"允许的域名一个都没有"，
    于是 set_ok / return_ok 对所有域名都返回 False。
    写成 None 反而是"不限制"，正好相反。
    """
    policy = http.cookiejar.DefaultCookiePolicy(allowed_domains=[])
    return http.cookiejar.CookieJar(policy=policy)


def logged_out_client(**kwargs) -> httpx.Client:
    """返回一个永远不会携带 cookie 的 httpx.Client。

    UA 必须是 Safari：Instagram 的 web_profile_info 端点对 UA 敏感，
    换成 Chrome UA 时该端点行为不一致（见计划第 1 节）。
    """
    headers = {
        "User-Agent": SAFARI_UA,
        "Accept": "*/*",
        "Accept-Language": "en-US,en;q=0.9",
    }
    headers.update(kwargs.pop("headers", {}) or {})
    return httpx.Client(
        headers=headers,
        cookies=_blocking_jar(),
        timeout=kwargs.pop("timeout", TIMEOUT_SECONDS),
        follow_redirects=kwargs.pop("follow_redirects", True),
        **kwargs,
    )


def ig_headers(username: str) -> dict:
    """Instagram web_profile_info 端点的必需 + 辅助 Header。

    Referer 要指向该账号的主页：缺了它该端点更容易返回登录墙。
    """
    return {
        "X-IG-App-ID": IG_APP_ID,
        "X-ASBD-ID": IG_ASBD_ID,
        "X-Requested-With": "XMLHttpRequest",
        "Referer": f"https://www.instagram.com/{username}/",
    }
