"""登出增量自测。对应实施计划 C2 的【验收】。

这套测试盯的不是"顺利时能解析"，而是**不顺利时的行为**。
增量每天跑一次、无人盯着，所以下面三件事比解析正确更重要：

  1. 每天跑一年也不能攒下一个 cookie —— 攒了，"没有可封的东西"就悄悄失效了；
  2. 登录墙触发时**不能重试** —— 重试只会加重触发；
  3. 任何"返回 0 条"都必须打印原因 —— 否则定时任务会安静地空跑一年，
     而它每天的输出看起来都很正常。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))   # tests/ 在子目录，得把项目根加进来
from core.console import force_utf8   # noqa: E402

force_utf8()   # 输出被重定向到文件/管道时，cp936 编不出 ß/⚠ 会让整套测试崩掉

import httpx                                                       # noqa: E402

from core.http import IG_APP_ID, logged_out_client                 # noqa: E402
from core.session import SAFARI_UA, Pacer                          # noqa: E402
from routes.delta import (                                         # noqa: E402
    IG_PROFILE_API, MAX_RATE_LIMIT_RETRIES, _body_hint, _login_wall_reason,
    _timeline_nodes, fetch_instagram,
)

fails = []


def check(cond, msg):
    print(("  OK   " if cond else "  FAIL ") + msg)
    if not cond:
        fails.append(msg)


class NoSleepPacer(Pacer):
    """真的 sleep 会让这套测试跑上一分钟。这里只记账，不睡。"""

    def __init__(self):
        super().__init__(0.0, 0.0)
        self.backoffs = 0

    def wait(self):
        pass

    def backoff(self, attempt):
        self.backoffs += 1


def node(nid, code, ts, text, typename="GraphImage", children=None, video=False,
         owner="acme_us"):
    n = {
        "__typename": typename,
        "id": nid,
        "shortcode": code,
        "taken_at_timestamp": ts,
        "display_url": "https://cdn.example.com/%s.jpg" % code,
        "dimensions": {"width": 1080, "height": 1350},
        "is_video": video,
        "edge_media_to_caption": {"edges": [{"node": {"text": text}}]},
        # 归属。真实响应里每个节点都带，缺了会被 partition_by_owner 丢掉——
        # 这正是 2026-08-30 修掉跨账号污染时加的闸。
        "owner": {"username": owner, "full_name": owner.replace("_", " ").title()},
    }
    if children is not None:
        n["edge_sidecar_to_children"] = {"edges": children}
    return n


def profile(nodes, private=False):
    return {"data": {"user": {
        "id": "9001", "username": "acme_us", "is_private": private,
        "edge_owner_to_timeline_media": {
            "count": len(nodes), "edges": [{"node": n} for n in nodes]},
    }}, "status": "ok"}


GOOD = profile([
    node("111", "ABC111", 1756000000, "Summer sale starts now"),
    node("222", "ABC222", 1755900000, "Three looks, one drop",
         typename="GraphSidecar"),                     # 轮播帖，端点只给封面
    node("333", "ABC333", 1755800000, "Behind the scenes",
         typename="GraphVideo", video=True),
])

JSON_HEADERS = {"content-type": "application/json; charset=utf-8"}


def serve(payload, status=200, headers=None):
    """返回一个 (handler, seen) 对：seen 记下每次请求，用来断言请求特征。"""
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(status, headers=headers or JSON_HEADERS,
                              json=payload)
    return handler, seen


def client_for(handler):
    return logged_out_client(transport=httpx.MockTransport(handler))


# --------------------------------------------------------------------------
print("[1] 正常响应能解析出帖子")
handler, seen = serve(GOOD)
c = client_for(handler)
posts = fetch_instagram("acme_us", client=c, pacer=NoSleepPacer())
check(len(posts) == 3, "3 个时间线节点解析出 3 篇（不多不少）")
by_id = {p.post_id: p for p in posts}
check(set(by_id) == {"111", "222", "333"}, "post_id 取的是节点 id")
check(all(p.text.strip() for p in posts), "每篇正文都非空 —— C2 验收的硬指标")
check(by_id["111"].text == "Summer sale starts now", "正文从 caption edges 里取对了")
check(all(p.source_route == "delta" for p in posts), "source_route 标成 delta")
check(all(p.platform == "instagram" and p.account == "acme_us" for p in posts),
      "platform / account 正确")
check(by_id["111"].permalink == "https://www.instagram.com/p/ABC111/",
      "permalink 由 shortcode 拼出")
check(by_id["111"].created_at.endswith("Z") and len(by_id["111"].created_at) == 20,
      "created_at 是 ISO 8601")

print("\n[2] 端点的已知限制被如实标注")
check(by_id["222"].media_complete is False,
      "轮播帖标 media_complete=False（该端点不给子项，只有封面）")
check(by_id["111"].media_complete is True, "单图帖是完整的")
check(by_id["333"].media[0].kind == "video", "视频帖记 kind=video（元数据留着，不下载）")
check(len(by_id["222"].media) == 1, "轮播帖只拿到 1 张封面")
c.close()

print("\n[3] 请求特征：端点、Header、以及最要紧的「没有 cookie」")
check(str(seen[0].url).startswith(IG_PROFILE_API), "打的是 web_profile_info")
check("username=acme_us" in str(seen[0].url), "username 作为 query 参数带上")
check(seen[0].headers.get("x-ig-app-id") == IG_APP_ID, "带 X-IG-App-ID")
check(seen[0].headers.get("referer") == "https://www.instagram.com/acme_us/",
      "Referer 指向该账号主页（缺了更容易撞登录墙）")
check(seen[0].headers.get("user-agent") == SAFARI_UA, "UA 是 Safari（该端点对 UA 敏感）")
check("doc_id" not in str(seen[0].url).lower(), "URL 里没有 doc_id（禁止事项 2）")

# 服务端硬塞 cookie，然后再跑一次：第二次请求依然不能带 Cookie 头。
def cookie_pusher(request):
    return httpx.Response(200, json=GOOD, headers=[
        ("content-type", "application/json"),
        ("set-cookie", "csrftoken=abc123; Path=/; Domain=.instagram.com"),
        ("set-cookie", "mid=xyz; Path=/"),
    ])


c2 = client_for(cookie_pusher)
fetch_instagram("acme_us", client=c2, pacer=NoSleepPacer())
fetch_instagram("acme_us", client=c2, pacer=NoSleepPacer())
check(len(c2.cookies) == 0, "服务端下发 Set-Cookie 后 jar 仍为空")
c2.close()

print("\n[4] 登录墙的三种长相：都返回空，且都不重试")
def redirect_to_login(request):
    if "/accounts/login" in str(request.url):
        return httpx.Response(200, headers={"content-type": "text/html"},
                              text="<html>login</html>")
    return httpx.Response(302, headers={
        "location": "https://www.instagram.com/accounts/login/?next=/acme_us/"})


hits = []
def counted(request):
    hits.append(request)
    return redirect_to_login(request)


c3 = client_for(counted)
check(fetch_instagram("acme_us", client=c3, pacer=NoSleepPacer()) == [],
      "302 到登录页 → 返回空列表")
check(len([h for h in hits if "/accounts/login" not in str(h.url)]) == 1,
      "只打了一次，没有重试（重试会加重触发）")
c3.close()

html_handler, html_seen = serve({}, headers={"content-type": "text/html"})
c4 = client_for(html_handler)
check(fetch_instagram("acme_us", client=c4, pacer=NoSleepPacer()) == [],
      "返回 HTML 而不是 JSON → 返回空列表")
check(len(html_seen) == 1, "HTML 登录墙同样不重试")
c4.close()

def broken_json(request):
    return httpx.Response(200, headers=JSON_HEADERS, content=b"{not json")


c5 = client_for(broken_json)
check(fetch_instagram("acme_us", client=c5, pacer=NoSleepPacer()) == [],
      "声称 JSON 但解析不了 → 返回空列表")
c5.close()

print("\n[5] 429 退避有上限；退避后成功要能用上")
always_429, seen_429 = serve({}, status=429)
c6 = client_for(always_429)
p429 = NoSleepPacer()
check(fetch_instagram("acme_us", client=c6, pacer=p429) == [], "持续 429 → 返回空列表")
check(len(seen_429) == MAX_RATE_LIMIT_RETRIES + 1,
      "总共请求 %d 次就收手" % (MAX_RATE_LIMIT_RETRIES + 1))
check(p429.backoffs == MAX_RATE_LIMIT_RETRIES, "退避了 %d 次" % MAX_RATE_LIMIT_RETRIES)
c6.close()

calls = []
def flaky(request):
    calls.append(request)
    if len(calls) == 1:
        return httpx.Response(429, json={})
    return httpx.Response(200, headers=JSON_HEADERS, json=GOOD)


c7 = client_for(flaky)
p7 = NoSleepPacer()
check(len(fetch_instagram("acme_us", client=c7, pacer=p7)) == 3,
      "第一次 429、第二次成功 → 正常拿到 3 篇")
check(p7.backoffs == 1, "只退避了一次")
c7.close()

print("\n[6] 其它失败路径都返回空，且都不会被当成「今天没有新帖」")
for status, label in ((401, "401"), (403, "403"), (500, "500")):
    h, s = serve({}, status=status)
    cx = client_for(h)
    check(fetch_instagram("acme_us", client=cx, pacer=NoSleepPacer()) == [],
          "HTTP %s → 返回空列表" % label)
    check(len(s) == 1, "HTTP %s 不重试" % label)
    cx.close()

def boom(request):
    raise httpx.ConnectError("nope", request=request)


c8 = client_for(boom)
check(fetch_instagram("acme_us", client=c8, pacer=NoSleepPacer()) == [],
      "连接失败 → 返回空列表而不是抛异常（定时任务不能因此挂掉）")
c8.close()

print("\n[7] 响应合法但内容为空的几种情况")
h, _ = serve({"data": {"user": None}, "status": "ok"})
c9 = client_for(h)
check(fetch_instagram("nosuchacct", client=c9, pacer=NoSleepPacer()) == [],
      "data.user 为 null（账号不存在）→ 返回空列表")
c9.close()

h, _ = serve(profile([node("1", "A1", 1756000000, "hi")], private=True))
c10 = client_for(h)
check(fetch_instagram("acme_us", client=c10, pacer=NoSleepPacer()) == [],
      "私密账号 → 返回空列表")
c10.close()

h, _ = serve(profile([]))
c11 = client_for(h)
check(fetch_instagram("acme_us", client=c11, pacer=NoSleepPacer()) == [],
      "时间线 0 条 → 返回空列表")
c11.close()

h, _ = serve([1, 2, 3])
c12 = client_for(h)
check(fetch_instagram("acme_us", client=c12, pacer=NoSleepPacer()) == [],
      "顶层 JSON 不是对象 → 返回空列表而不是崩")
c12.close()

print("\n[8] 两个纯函数的边界")
req = httpx.Request("GET", "https://www.instagram.com/accounts/login/")
check(_login_wall_reason(httpx.Response(200, request=req,
                                        headers=JSON_HEADERS)) is not None,
      "URL 落在 /accounts/login 上就算登录墙（哪怕 content-type 是 JSON）")
req2 = httpx.Request("GET", IG_PROFILE_API)
check(_login_wall_reason(httpx.Response(200, request=req2,
                                        headers={"content-type": "text/html"}))
      is not None, "content-type 不是 JSON 就算登录墙")
check(_login_wall_reason(httpx.Response(200, request=req2,
                                        headers=JSON_HEADERS)) is None,
      "正常 JSON 响应不误判")
check(_login_wall_reason(httpx.Response(200, request=req2, headers={})) is not None,
      "根本没有 content-type 也当登录墙处理（宁可保守）")

check(_timeline_nodes({}) == [], "空 payload 不崩")
check(_timeline_nodes({"data": {"user": "not-a-dict"}}) == [], "user 不是对象不崩")
check(_timeline_nodes({"data": {"user": {"edge_owner_to_timeline_media":
                                         {"edges": [{"node": None}, "junk"]}}}}) == [],
      "edges 里的脏项被跳过")
check(len(_timeline_nodes(GOOD)) == 3, "正常 payload 取出 3 个节点")

print("\n[9] 归属过滤：端点理论上只返回本账号，但仍然要拦")
MIXED = profile([
    node("111", "ABC111", 1756000000, "ours"),
    node("999", "ZZZ999", 1755000000, "someone else's", owner="another_account"),
])
h, _ = serve(MIXED)
c13 = client_for(h)
mixed = fetch_instagram("acme_us", client=c13, pacer=NoSleepPacer())
check([p.post_id for p in mixed] == ["111"], "别人账号的帖子被丢掉，只留本账号的")
check(mixed[0].owner == "acme_us", "保留下来的帖子带着归属，下游可复核")
c13.close()

h, _ = serve(profile([{"id": "1", "shortcode": "NOOWNER", "taken_at_timestamp": 1756000000,
                       "display_url": "https://cdn/x.jpg",
                       "edge_media_to_caption": {"edges": []}}]))
c14 = client_for(h)
check(fetch_instagram("acme_us", client=c14, pacer=NoSleepPacer()) == [],
      "归属未知的一律丢弃（宁可漏一篇自家的，不可混进一篇别人的）")
c14.close()

print("\n[10] 失败响应的正文摘要 —— 几种失败只看状态码是分不开的")
long_body = httpx.Response(429, request=req2, text="x" * 500)
check(_body_hint(httpx.Response(429, request=req2,
                                text="  Please wait a few\n  minutes  before you try again. "))
      == "Please wait a few minutes before you try again.",
      "换行与连续空格压成一行（这句正是计划点名的误导性文案）")
check(len(_body_hint(long_body)) == 201, "超长正文截断到 200 字符 + 省略号")
check(_body_hint(long_body).endswith("…"), "截断处留下省略号")
check(_body_hint(httpx.Response(429, request=req2, text="")) == "", "空正文返回空串，不打无用的行")

print("\n" + ("全部通过" if not fails else f"{len(fails)} 项失败"))
print("真实公开账号验收：.venv\\Scripts\\python.exe -m routes.delta <公开账号名>")
sys.exit(1 if fails else 0)
