"""登出 HTTP 客户端自测。对应实施计划 C1 的【验收】。

重点不是"构造出来时是空的"（那太容易满足了），而是**服务端塞 cookie 也塞不进来**。
增量路径每天跑，只要有一次把 Set-Cookie 收下并在下次带回去，
"登出所以没有可封的东西"这个前提就悄悄失效了，而且不会有任何迹象。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))   # tests/ 在子目录，得把项目根加进来

import httpx

from core.http import IG_APP_ID, ig_headers, logged_out_client
from core.session import SAFARI_UA

fails = []


def check(cond, msg):
    print(("  OK   " if cond else "  FAIL ") + msg)
    if not cond:
        fails.append(msg)


print("[1] 客户端基本属性")
c = logged_out_client()
check(len(c.cookies) == 0, f"初始无 cookie，实得 {len(c.cookies)} 个")
check(c.headers["user-agent"] == SAFARI_UA, "UA 是 SAFARI_UA")
check("Safari" in c.headers["user-agent"], "UA 确实是 Safari（该端点对 UA 敏感）")
check(c.timeout.read == 30, f"timeout=30，实得 {c.timeout.read}")
check(c.follow_redirects is True, "follow_redirects=True")
c.close()

print("\n[2] 服务端下发 Set-Cookie 也不会被收下（这条是关键）")
seen_headers = []


def handler(request: httpx.Request) -> httpx.Response:
    seen_headers.append(dict(request.headers))
    return httpx.Response(
        200,
        headers=[
            ("set-cookie", "sessionid=SHOULD_NOT_STICK; Domain=.example.com; Path=/"),
            ("set-cookie", "csrftoken=ALSO_NOT; Domain=.example.com; Path=/"),
        ],
        json={"ok": True},
    )


c = logged_out_client(transport=httpx.MockTransport(handler))
c.get("https://www.example.com/one")
check(len(c.cookies) == 0, f"收到 2 个 Set-Cookie 后 jar 仍为空，实得 {len(c.cookies)} 个")

c.get("https://www.example.com/two")
check(len(seen_headers) == 2, "两次请求都发出了")
check("cookie" not in seen_headers[1], "第二次请求不含 Cookie 头（没有把会话带回去）")
c.close()

print("\n[3] 多次新建客户端之间不共享状态")
a, b = logged_out_client(), logged_out_client()
check(a.cookies is not b.cookies, "每个客户端各自持有 jar，不是共用同一个")
a.close()
b.close()

print("\n[4] Instagram 必需 Header")
h = ig_headers("acme")
check(h["X-IG-App-ID"] == IG_APP_ID == "936619743392459", "X-IG-App-ID 正确")
check(h["X-ASBD-ID"] == "198387", "X-ASBD-ID 正确")
check(h["X-Requested-With"] == "XMLHttpRequest", "X-Requested-With 正确")
check(h["Referer"] == "https://www.instagram.com/acme/", "Referer 指向该账号主页")
check("doc_id" not in str(h).lower(), "不含 doc_id（禁止事项 2）")

print("\n[5] 自定义 Header 能合并进去，但不会覆盖掉 cookie 策略")
c = logged_out_client(headers=ig_headers("acme"), transport=httpx.MockTransport(handler))
check(c.headers["x-ig-app-id"] == IG_APP_ID, "自定义 Header 生效")
check(c.headers["user-agent"] == SAFARI_UA, "默认 UA 未被冲掉")
c.get("https://www.example.com/three")
check(len(c.cookies) == 0, "带自定义 Header 时 cookie 策略依然生效")
c.close()

print("\n" + ("全部通过" if not fails else f"{len(fails)} 项失败"))
sys.exit(1 if fails else 0)
