"""隔离验证扫描深度、异常停止、状态写入和失败预算；不访问真实账号。"""
import asyncio
import json
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from image_fixtures import image_bytes

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core.console import force_utf8   # noqa: E402

force_utf8()   # 输出被重定向到文件/管道时，cp936 编不出 ß/⚠ 会让整套测试崩掉

from core.capture import Collector, download_media, prune_captures  # noqa: E402
from core.monitoring import MonitoringJournal                       # noqa: E402
from core.store import Archive, Media, Post, iter_post_dirs         # noqa: E402
import routes.delta as delta                                        # noqa: E402
from routes.delta import (                                          # noqa: E402
    DeltaBlocked, DeltaConfig, blank_entry, budget_exhausted, delta_once,
    effective_stale_hours, human_scroll, login_wall_reason, profile_url,
    quiet_days, record_failure, record_success, stale_enough,
)

fails = []


def check(cond, msg):
    print(("  OK   " if cond else "  FAIL ") + msg)
    if not cond:
        fails.append(msg)


NOW = datetime(2026, 8, 30, 9, 0, 0, tzinfo=timezone.utc)


def test_cfg(**kw):
    """把所有等待时间清零：真睡的话这套测试要跑几分钟。"""
    base = dict(request_gap_seconds=0.0, first_screen_seconds=0.0,
                max_scrolls=2, max_session_seconds=30.0, keep_captures_days=3,
                failure_budget=3)
    base.update(kw)
    return DeltaConfig(**base)


# ---- 假的 Playwright 对象 ------------------------------------------------

class FakeResponse:
    def __init__(self, url, status=200, body="{}"):
        self.url, self.status, self._body = url, status, body

    async def text(self):
        return self._body


class FakeMouse:
    """滚轮会真的推动这张假页面的 scrollY —— `scroll_step=0` 模拟"滚了没动"。"""

    def __init__(self, page):
        self.page = page
        self.wheels = []
        self.moves = []

    async def move(self, x, y):
        self.moves.append((x, y))

    async def wheel(self, dx, dy):
        self.wheels.append((dx, dy))
        self.page.scroll_y += self.page.scroll_step


class FakePage:
    """goto 时把预置的响应喂给监听器，模拟首屏接口。"""

    def __init__(self, responses, final_url=None, scroll_step=800):
        self._responses = responses
        self._final_url = final_url
        self.url = ""
        self.scroll_y = 0.0
        self.scroll_step = scroll_step
        self.mouse = FakeMouse(self)
        self.handlers = {}
        self.closed = False
        self.goto_calls = []

    async def evaluate(self, expr):
        if "innerWidth" in expr:
            return [1280, 800]
        if "scrollY" in expr:
            return self.scroll_y
        if "querySelectorAll" in expr:
            return []
        return None

    def on(self, event, fn):
        self.handlers.setdefault(event, []).append(fn)

    def remove_listener(self, event, fn):
        if fn in self.handlers.get(event, []):
            self.handlers[event].remove(fn)

    async def goto(self, url, **kw):
        self.goto_calls.append(url)
        self.url = self._final_url or url
        for r in self._responses:
            for fn in self.handlers.get("response", []):
                fn(r)

    async def close(self):
        self.closed = True


class FakeRequest:
    def __init__(self, payload=image_bytes()):
        self.payload = payload
        self.gets = []

    async def get(self, url, headers=None):
        self.gets.append(url)
        payload = self.payload

        class R:
            ok = True
            status = 200
            headers = {"content-type": "image/jpeg"}

            async def body(self):
                return payload
        return R()


class FakeCtx:
    def __init__(self, page):
        self.page = page
        self.request = FakeRequest()

    async def new_page(self):
        return self.page


def ig_payload(pk="111", code="abc", ts=1755000000, owner="acme_us",
               text="hello world", n_media=1, coauthors=()):
    node = {"pk": pk, "code": code, "taken_at": ts, "media_type": 8 if n_media > 1 else 1,
            "user": {"username": owner, "full_name": "Acme US"},
            "caption": {"text": text},
            # 保留响应中通常为空的两类 coauthor 字段。
            "coauthor_producers": [{"pk": "9%d" % i, "username": u}
                                   for i, u in enumerate(coauthors)],
            "invited_coauthor_producers": [],
            "image_versions2": {"candidates": [
                {"url": "https://cdn.example.com/%s.jpg" % pk,
                 "width": 1080, "height": 1080}]}}
    if n_media > 1:
        node["carousel_media"] = [
            {"image_versions2": {"candidates": [
                {"url": "https://cdn.example.com/%s_%d.jpg" % (pk, i),
                 "width": 1080, "height": 1080}]}}
            for i in range(n_media)]
    return {"data": {"items": [node]}}


def resp(payload, url="https://www.instagram.com/api/v1/feed/user/1/"):
    return FakeResponse(url, 200, json.dumps(payload))


# ==========================================================================
print("[1] 登录墙 / 被拦：四种长相都要认出来，且理由各不相同")

check(login_wall_reason("https://www.instagram.com/acme_us/") is None,
      "正常落地页不误报")
check("会话可能已失效" in (login_wall_reason(
    "https://www.instagram.com/accounts/login/?next=%2Facme_us%2F") or ""),
      "被重定向到登录页 → 判为会话失效")
check(login_wall_reason("https://www.facebook.com/checkpoint/1234") is not None,
      "checkpoint 也算被拦（安全挑战）")
r429 = login_wall_reason("https://www.instagram.com/acme_us/",
                         (429, "https://www.instagram.com/api/v1/feed/user/1/"))
check("429" in (r429 or "") and "限流" in (r429 or ""),
      "页面正常但接口 429 → 判为限流（只看 URL 会漏掉这种）")
r401 = login_wall_reason("https://www.instagram.com/acme_us/",
                         (401, "https://www.instagram.com/api/v1/feed/user/1/"))
check("401" in (r401 or "") and "限流" not in (r401 or ""),
      "401 与 429 给的是不同的话 —— 一个要人去登录，一个是等")

col = Collector()
asyncio.run(col.on_response(FakeResponse(
    "https://www.instagram.com/api/v1/feed/user/1/", 401, "")))
check(col.blocked_status() == (
    401, "https://www.instagram.com/api/v1/feed/user/1/"),
      "非 200 的接口状态码被 Collector 留痕，而不是当成'没有数据'")
check(col.payloads == [], "被拦的响应不会被当成 payload")

col2 = Collector()
asyncio.run(col2.on_response(FakeResponse(
    "https://www.instagram.com/api/v1/feed/user/1/", 404, "")))
check(col2.blocked_status() is None, "404 不算被拦（不是会话问题）")


# ==========================================================================
print("\n[2] 抓取深度上限：只滚几屏，绝不滚到底（C7）")

for limit in (0, 1, 3):
    page = FakePage([])
    cfg_l = test_cfg(max_scrolls=limit)
    n, moved = asyncio.run(human_scroll(page, cfg_l))
    check(len(page.mouse.wheels) == limit == n,
          "max_scrolls=%d 时正好滚 %d 屏" % (limit, limit))

page = FakePage([])
_, moved = asyncio.run(human_scroll(page, test_cfg(max_scrolls=4)))
deltas = {dy for _, dy in page.mouse.wheels}
check(len(deltas) > 1, "每屏的滚动距离不相同 —— 匀速等距滚动本身是行为指纹")
check(all(0 < dy for _, dy in page.mouse.wheels), "只向下滚")

# 滚轮作用于鼠标所在位置，测试须覆盖主视口定位。
check(page.mouse.moves and page.mouse.moves[0] == (640, 400),
      "滚之前先把鼠标移到视口中间（默认 (0,0) 是导航栏，滚了也白滚）")
check(moved > 0, "返回值报出页面实际移动了多少像素")

dead = FakePage([], scroll_step=0)
_, moved0 = asyncio.run(human_scroll(dead, test_cfg(max_scrolls=2)))
check(moved0 == 0,
      "滚了但页面没动时返回 0 —— 这正是那次静默失败要被看见的地方")


# ==========================================================================
print("\n[3] 主流程：幂等、归属过滤、残缺帖可升级、dry-run 不写盘")

with tempfile.TemporaryDirectory() as d:
    arc = Archive(Path(d), "in_acme_us")
    page = FakePage([resp(ig_payload())])
    ctx = FakeCtx(page)
    n1 = asyncio.run(delta_once(ctx, "instagram", "acme_us", arc, test_cfg()))
    check(n1.new == 1, "第一次跑：新增 1 篇")
    check(len(arc.rows()) == 1, "写进了 manifest")
    post_dirs = list(iter_post_dirs(Path(d) / "in_acme_us"))
    check(len(post_dirs) == 1 and (post_dirs[0] / "post.json").exists(),
          "落进了每帖一个文件夹（J 组布局）")
    check(any(p.suffix == ".jpg" for p in post_dirs[0].iterdir()),
          "配图下载进了该帖自己的文件夹")

    arc2 = Archive(Path(d), "in_acme_us")
    page2 = FakePage([resp(ig_payload())])
    n2 = asyncio.run(delta_once(FakeCtx(page2), "instagram", "acme_us", arc2, test_cfg()))
    check(n2.new == 0, "第二次跑：新增 0 篇（幂等）")
    check(FakeCtx(page2).request.gets == [], "幂等时不重复请求 CDN")

with tempfile.TemporaryDirectory() as d:
    arc = Archive(Path(d), "in_acme_us")
    payload = {"data": {"items": [
        ig_payload("111", owner="acme_us")["data"]["items"][0],
        ig_payload("222", owner="someone_else")["data"]["items"][0],
    ]}}
    page = FakePage([resp(payload)])
    n = asyncio.run(delta_once(FakeCtx(page), "instagram", "acme_us", arc, test_cfg()))
    check(n.new == 1 and n.rejected == 1, "他人帖不计入新增，但计入丢弃数")
    rejected = (Path(d) / "in_acme_us" / "_rejected.jsonl").read_text(encoding="utf-8")
    check("someone_else" in rejected, "他人帖写进了 _rejected.jsonl，不是静默丢弃")
    check("222" not in json.dumps(arc.rows()), "他人帖没有进归档")

with tempfile.TemporaryDirectory() as d:
    # 残缺帖必须能被补全：用 should_append 而不是 has()
    arc = Archive(Path(d), "in_acme_us")
    stub = Post(post_id="111", platform="instagram", account="acme_us",
                text="", created_at="2025-08-12T00:00:00Z", owner="acme_us",
                media=[Media(url="https://cdn.example.com/111.jpg", kind="image")],
                media_complete=False)
    arc.append(stub)
    page = FakePage([resp(ig_payload("111", n_media=3))])
    n = asyncio.run(delta_once(FakeCtx(page), "instagram", "acme_us", arc, test_cfg()))
    check(n.new == 0 and n.upgraded == 1,
          "已存残缺帖记为旧帖修复，不冒充社媒新增并刷新零新增时钟")
    row = [r for r in arc.rows() if r["post_id"] == "111"][0]
    check(len(row["media"]) == 3 and row["media_complete"],
          "升级后媒体补全，media_complete 回到 True")

with tempfile.TemporaryDirectory() as d:
    # 上次两张只成功一张：新一轮应复用已落盘文件，只请求失败的那张。
    arc = Archive(Path(d), "in_acme_us")
    old = Post(post_id="reuse", platform="instagram", account="acme_us",
               text="partial", created_at="2026-08-12T00:00:00Z", owner="acme_us",
               media=[Media(url="https://cdn.example.com/reuse_1.jpg", kind="image"),
                      Media(url="https://cdn.example.com/reuse_2.jpg", kind="image")],
               media_complete=False)
    first = arc.media_path(old, 0, "image/jpeg")
    first.write_bytes(image_bytes())
    old.media[0].local_path = str(first.relative_to(arc.base)).replace("\\", "/")
    arc.append(old)
    fresh = Post(post_id="reuse", platform="instagram", account="acme_us",
                 text="partial", created_at="2026-08-12T00:00:00Z", owner="acme_us",
                 media=[Media(url="https://cdn.example.com/reuse_1.jpg", kind="image"),
                        Media(url="https://cdn.example.com/reuse_2.jpg", kind="image")],
                 media_complete=True)
    ctx = FakeCtx(FakePage([]))
    asyncio.run(download_media(ctx, arc, fresh, "https://www.instagram.com/acme_us/"))
    check(ctx.request.gets == ["https://cdn.example.com/reuse_2.jpg"],
          "残缺重试不重复请求已成功的 CDN 图片，只补失败项")
    check(all(m.local_path for m in fresh.media) and fresh.media_complete,
          "复用路径与新下载路径合并后可正常补全")

with tempfile.TemporaryDirectory() as d:
    arc = Archive(Path(d), "in_acme_us")
    ctx = FakeCtx(FakePage([resp(ig_payload())]))
    n = asyncio.run(delta_once(ctx, "instagram", "acme_us", arc, test_cfg(),
                               dry_run=True))
    check(n.new == 1, "--dry-run 会报出将要新增的篇数")
    check(arc.rows() == [], "--dry-run 不写 manifest")
    check(ctx.request.gets == [], "--dry-run 不下载媒体")
    check(not list((Path(d) / "in_acme_us").glob("_capture_delta_*.json")),
          "--dry-run 不落转储")


# ==========================================================================
print("\n[3b] 2026-08-30 实测暴露的缺陷：抓不到时间线必须和「真没新帖」区分得开")

# 缺陷二：那次 IG 只打了一句"新增 0 篇"，与"真的没新帖"完全无法区分。
with tempfile.TemporaryDirectory() as d:
    arc = Archive(Path(d), "in_acme_us")
    for i in range(6):        # 归档里已有 6 篇，说明这账号是有时间线的
        arc.append(Post(post_id="old%d" % i, platform="instagram",
                        account="acme_us", text="x", owner="acme_us",
                        created_at="2026-07-%02dT00:00:00Z" % (10 + i)))
    others = [ig_payload(str(900 + i), owner="brand%d" % i)["data"]["items"][0]
              for i in range(38)]
    page = FakePage([resp({"data": {"items": others + [
        ig_payload("111", ts=1749200000)["data"]["items"][0]]}})])
    try:
        asyncio.run(delta_once(FakeCtx(page), "instagram", "acme_us", arc, test_cfg()))
        check(False, "只看到 1 篇自家帖子必须中止")
    except DeltaBlocked as e:
        check("没有拿到时间线" in str(e) and "38" in str(e),
              "只看到 1 篇自家的、丢弃 38 篇他人的 → 判为『没拿到时间线』而不是『没新帖』")
        check(not e.hard, "这是我们这边的问题，不是对面在拦 —— 不牵连另一个平台")

with tempfile.TemporaryDirectory() as d:
    # 反面：归档本来就只有 1 篇时，看到 1 篇不该报警（阈值取 min(配置, 已有篇数)）
    arc = Archive(Path(d), "in_acme_us")
    arc.append(Post(post_id="old", platform="instagram", account="acme_us",
                    text="x", owner="acme_us", created_at="2026-07-01T00:00:00Z"))
    page = FakePage([resp(ig_payload("111"))])
    n = asyncio.run(delta_once(FakeCtx(page), "instagram", "acme_us", arc, test_cfg()))
    check(n.new == 1, "归档本身就很小时不误报（新账号、刚开始抓的情况）")

with tempfile.TemporaryDirectory() as d:
    # 但空归档不能把闸门降成 0；否则错误账号/全是推荐位也会刷新 last_success。
    arc = Archive(Path(d), "in_acme_us")
    other = ig_payload("foreign", owner="someone_else")
    page = FakePage([resp(other)])
    try:
        asyncio.run(delta_once(FakeCtx(page), "instagram", "acme_us", arc,
                               test_cfg(), dry_run=True))
        check(False, "空归档看到 0 篇自家内容必须中止")
    except DeltaBlocked as e:
        check("期望至少 1 篇" in str(e),
              "冷启动仍要求至少一篇自家内容，不把错误目标记成成功")

r = delta.ScanResult(new=0, own=1, rejected=38, newest_seen="2026-06-06T00:00:00Z",
                     oldest_seen="2026-06-06T00:00:00Z",
                     newest_known="2026-07-15T00:00:00Z")
check(r.stale_view(), "看到的最新一篇比归档还旧 → 判为视图陈旧")
check("新增 0 篇" in r.summary() and "本账号 1 篇" in r.summary()
      and "丢弃 38" in r.summary() and "2026-07-15" in r.summary(),
      "一行摘要同时给出：新增数、自家篇数与日期跨度、丢弃数、归档最新日期")
check(not delta.ScanResult(new=0, own=6, newest_seen="2026-08-25T00:00:00Z",
                           newest_known="2026-08-25T00:00:00Z").stale_view(),
      "看到的和归档一样新 → 不报警（这就是 FB 那次『真的没新帖』）")

# 分别检查原创和合作数，使合作解析退化可见。
split = delta.ScanResult(new=2, own=36, authored=1, collab=35, rejected=3,
                         newest_seen="2026-08-27T00:00:00Z",
                         oldest_seen="2026-06-06T00:00:00Z",
                         newest_known="2026-08-27T00:00:00Z")
check("原创 1" in split.summary() and "合作 35" in split.summary(),
      "摘要把『本账号 N 篇』拆成原创/合作两半 —— 合作判定失效时一眼可见")


print("\n[3c] 丢弃的里面有已知合作方 → 归属判定漏判的哨兵（那一类）")

with tempfile.TemporaryDirectory() as d:
    arc = Archive(Path(d), "in_acme_us")
    # 先归档已知合作关系。
    arc.append(Post(post_id="c1", platform="instagram", account="acme_us",
                    text="collab", owner="brand.x", coauthors=["acme_us"],
                    created_at="2026-07-01T00:00:00Z"))
    for i in range(3):
        arc.append(Post(post_id="o%d" % i, platform="instagram", account="acme_us",
                        text="own", owner="acme_us",
                        created_at="2026-07-%02dT00:00:00Z" % (10 + i)))
    # 本次响应保留作者但缺合作字段，模拟合作解析漂移。
    items = [ig_payload(str(700 + i))["data"]["items"][0] for i in range(3)]
    items.append(ig_payload("777", owner="brand.x")["data"]["items"][0])
    items.append(ig_payload("778", owner="chicagofire")["data"]["items"][0])
    page = FakePage([resp({"data": {"items": items}})])
    res = asyncio.run(delta_once(FakeCtx(page), "instagram", "acme_us", arc,
                                 test_cfg(), dry_run=True))
    check([s["post_id"] for s in res.suspect] == ["777"],
          "被丢弃的 brand.x 帖子被挑出来 —— 它是已知合作方，很可能就在本账号主页上")
    check(res.rejected == 2 and len(res.suspect) == 1,
          "陌生账号的推荐位照常丢弃、不进哨兵（否则这条告警会天天响、然后被无视）")

with tempfile.TemporaryDirectory() as d:
    # 合作判定退化时，中止原因须包含已知合作方信息。
    arc = Archive(Path(d), "in_acme_us")
    for i in range(4):
        arc.append(Post(post_id="c%d" % i, platform="instagram", account="acme_us",
                        text="collab", owner="brand.x", coauthors=["acme_us"],
                        created_at="2026-07-%02dT00:00:00Z" % (10 + i)))
    items = [ig_payload(str(800 + i), owner="brand.x")["data"]["items"][0]
             for i in range(5)]
    items.append(ig_payload("888")["data"]["items"][0])
    page = FakePage([resp({"data": {"items": items}})])
    try:
        asyncio.run(delta_once(FakeCtx(page), "instagram", "acme_us", arc,
                               test_cfg(), dry_run=True))
        check(False, "只看到 1 篇自家帖子必须中止")
    except DeltaBlocked as e:
        check("已知合作方" in str(e) and "brand.x" in str(e),
              "中止理由直接指向合作帖判定，而不是让人先去怀疑被拦了")


print("\n[4] 异常即停：这四种都必须当次中止并说清原因")

with tempfile.TemporaryDirectory() as d:
    arc = Archive(Path(d), "in_acme_us")
    ctx = FakeCtx(FakePage([resp(ig_payload())],
                           final_url="https://www.instagram.com/accounts/login/"))
    try:
        asyncio.run(delta_once(ctx, "instagram", "acme_us", arc, test_cfg()))
        check(False, "登录墙必须中止")
    except DeltaBlocked as e:
        check("会话" in str(e), "登录墙：中止并提示会话失效")
    check(ctx.request.gets == [], "被拦时**没有**继续下载媒体")
    check(arc.rows() == [], "被拦时不写归档")

with tempfile.TemporaryDirectory() as d:
    arc = Archive(Path(d), "in_acme_us")
    ctx = FakeCtx(FakePage([FakeResponse(
        "https://www.instagram.com/api/v1/feed/user/1/", 429, "")]))
    try:
        asyncio.run(delta_once(ctx, "instagram", "acme_us", arc, test_cfg()))
        check(False, "429 必须中止")
    except DeltaBlocked as e:
        check("429" in str(e), "接口 429：中止（URL 正常也要认出来）")

with tempfile.TemporaryDirectory() as d:
    arc = Archive(Path(d), "in_acme_us")
    ctx = FakeCtx(FakePage([]))
    try:
        asyncio.run(delta_once(ctx, "instagram", "acme_us", arc, test_cfg()))
        check(False, "一个响应都没捞到必须中止")
    except DeltaBlocked as e:
        check("一个接口响应都没捞到" in str(e), "零响应：中止而不是当成'今天没新帖'")

with tempfile.TemporaryDirectory() as d:
    arc = Archive(Path(d), "in_acme_us")
    ctx = FakeCtx(FakePage([resp({"data": {"unexpected": "shape"}})]))
    try:
        asyncio.run(delta_once(ctx, "instagram", "acme_us", arc, test_cfg()))
        check(False, "解析出 0 篇必须中止")
    except DeltaBlocked as e:
        check("解析器" in str(e), "解析出 0 篇：提示解析器可能失效，而不是静默结束")


# ==========================================================================
print("\n[4b] 被对面拦住时，同一次运行不再去敲另一个平台")


class FakePw:
    async def stop(self):
        pass


class FakeBrowser:
    async def close(self):
        pass


class MultiCtx:
    """一次 attach 服务两个平台，每次 new_page 换一张假页面。"""

    def __init__(self, pages):
        self.pages = list(pages)
        self.request = FakeRequest()

    async def new_page(self):
        return self.pages.pop(0)


def run_due(pages, state, tmp, platforms=("facebook", "instagram")):
    path = Path(tmp) / "s.json"
    delta.AccessController(Path(tmp)).initialize("isolated fixture")
    from core.capture_state import CaptureState
    CaptureState(Path(tmp)).initialize(Path(tmp), {"facebook": "acme_page", "instagram": "acme_us"},
                                       "isolated baseline", now=datetime(2025, 8, 1, tzinfo=timezone.utc))
    ctx = MultiCtx(pages)
    notices = []

    class FC:
        archive_dir = Path(tmp)
        state_dir = Path(tmp)
        detect_debug_port = 9224
        detect_profile_dir = Path(tmp) / "detect"

        def __getitem__(self, key):
            return {"facebook": "acme_page", "instagram": "acme_us"}

    saved = (delta.cfg, delta.attach, delta.notify)
    try:
        delta.cfg = lambda: FC()
        delta.notify = lambda t, m, **k: notices.append((t, m))

        async def fake_attach(**_kwargs):
            return FakePw(), FakeBrowser(), ctx
        delta.attach = fake_attach
        rc = asyncio.run(delta._run_due(list(platforms), test_cfg(), state,
                                        path, False))
    finally:
        (delta.cfg, delta.attach, delta.notify) = saved
    return rc, notices


def fb_payload(post_id="900", owner_slug="acme_page", ts=1755000000):
    return {"data": {"node": {
        "post_id": post_id,
        "message": {"text": "hi from fb"},
        "creation_time": ts,
        "url": "https://www.facebook.com/acme_page/posts/%s" % post_id,
        "actors": [{"name": "Acme Page",
                    "url": "https://www.facebook.com/%s" % owner_slug}],
        "attachments": [{"media": {"__typename": "Photo", "id": "1",
                                   "image": {"uri": "https://cdn.example.com/f.jpg",
                                             "width": 1080, "height": 1350}}}],
    }}}


with tempfile.TemporaryDirectory() as tmp:
    ig_page = FakePage([resp(ig_payload())])
    state = {}
    rc, notices = run_due(
        [FakePage([], final_url="https://www.facebook.com/login.php"), ig_page],
        state, tmp)
    check(rc == 1, "被拦时退出码非 0")
    check(ig_page.goto_calls == [],
          "facebook 被登录墙拦住后，**instagram 根本没有被打开**（同一会话同一指纹）")
    check(len(notices) == 1 and "中止" in notices[0][0],
          "被拦会告警（否则'悄悄坏掉'就没有任何出口）")
    check(state["facebook"]["consecutive_failures"] == 1
          and state["instagram"]["consecutive_failures"] == 1,
          "没跑的那个平台也记一次失败 —— 否则连续被拦时预算永远攒不满")
    check("同批次" in state["instagram"]["last_error"],
          "它的失败原因写明是被同批次连累，不会被误读成 IG 自己有问题")

with tempfile.TemporaryDirectory() as tmp:
    ig_page = FakePage([resp(ig_payload())])
    state = {}
    rc, notices = run_due([FakePage([resp({"data": {"nothing": 1}})]), ig_page],
                          state, tmp)
    check(rc == 1, "解析不出来也算失败")
    check(ig_page.goto_calls, "但这是我们自己这边的问题，instagram 照跑")
    check(state["instagram"]["last_success"] and state["instagram"]["last_new_count"] == 1,
          "instagram 正常抓到并记成功")

with tempfile.TemporaryDirectory() as tmp:
    state = {}
    rc, _ = run_due([FakePage([resp(fb_payload())]), FakePage([resp(ig_payload())])],
                    state, tmp)
    check(rc == 0, "两个平台都正常时退出码为 0")
    check(state["facebook"]["last_new_count"] == 1
          and state["instagram"]["last_new_count"] == 1,
          "两个平台各自记下新增")
    fb_rows = Archive(Path(tmp), "fa_acme_page").rows()
    check(len(fb_rows) == 1 and fb_rows[0]["owner"] == "acme_page",
          "Facebook 侧的归属取自 actors[0].url 的账号名段，不是展示名")

with tempfile.TemporaryDirectory() as tmp:
    # 补齐旧媒体不算发现新帖，不能刷新 quiet 时钟。
    arc = Archive(Path(tmp), "in_acme_us")
    arc.append(Post(
        post_id="111", platform="instagram", account="acme_us", text="old",
        owner="acme_us", created_at="2025-08-12T12:00:00Z",
        media=[Media(url="https://cdn.example.com/old.jpg", kind="image")],
        media_complete=False))
    old_new_at = "2026-07-01T00:00:00Z"
    state = {"instagram": {**blank_entry(),
                            "first_success": old_new_at,
                            "last_new_at": old_new_at}}
    rc, _ = run_due([FakePage([resp(ig_payload("111", n_media=3))])],
                    state, tmp, platforms=("instagram",))
    check(rc == 0 and Archive(Path(tmp), "in_acme_us").rows()[0]["media_complete"],
          "旧帖媒体修复仍算本轮成功并真正补全归档")
    check(state["instagram"]["last_new_count"] == 0
          and state["instagram"]["last_new_at"] == old_new_at,
          "但 last_new_at/last_new_count 不冒充新发布内容，quiet 时钟不被洗掉")

with tempfile.TemporaryDirectory() as tmp:
    state = {}
    saved_once = delta.delta_once

    async def unexpected(*_a, **_kw):
        raise RuntimeError("parser exploded")

    try:
        delta.delta_once = unexpected
        rc, notices = run_due([], state, tmp, platforms=("instagram",))
    finally:
        delta.delta_once = saved_once
    persisted = json.loads((Path(tmp) / "s.json").read_text(encoding="utf-8"))
    check(rc == 1 and state["instagram"]["consecutive_failures"] == 1,
          "非预期异常返回失败并累计平台失败预算")
    check("RuntimeError" in state["instagram"]["last_error"]
          and persisted == state,
          "非预期异常的类型/详情写入 delta_state，而不是只打到控制台")
    check(notices and "抓取异常" in notices[0][0],
          "非预期异常也会通知，不会在计划任务里静默消失")

with tempfile.TemporaryDirectory() as tmp:
    # 本地归档初始化失败只影响当前平台。
    state = {}
    ig_page = FakePage([resp(ig_payload())])
    saved_archive = delta.Archive

    def archive_with_denied_facebook(base, name):
        if name.startswith("fa_"):
            raise PermissionError("archive root denied")
        return saved_archive(base, name)

    try:
        delta.Archive = archive_with_denied_facebook
        rc, notices = run_due([ig_page], state, tmp)
    finally:
        delta.Archive = saved_archive
    persisted = json.loads((Path(tmp) / "s.json").read_text(encoding="utf-8"))
    check(rc == 1 and state["facebook"]["consecutive_failures"] == 1,
          "Archive 构造抛 PermissionError 时返回失败并累计对应平台预算")
    check("PermissionError" in state["facebook"]["last_error"]
          and persisted == state,
          "Archive 构造失败的类型/详情原子写入 delta_state")
    check(notices and "抓取异常 · facebook" in notices[0][0],
          "Archive 构造失败会通知并点名对应平台")
    check(ig_page.goto_calls and state["instagram"]["last_success"],
          "一个平台的 Archive 构造失败属于本地问题，另一个平台继续并成功")

with tempfile.TemporaryDirectory() as tmp:
    # attach 在平台循环之前；这条也必须把所有到期平台记失败。
    path = Path(tmp) / "attach_state.json"
    delta.AccessController(Path(tmp)).initialize("isolated attach failure")
    state = {}
    notices = []
    saved_attach, saved_notify = delta.attach, delta.notify

    async def broken_attach(**_kwargs):
        # attach 的 SystemExit 不属于 Exception，须单独覆盖。
        raise SystemExit("CDP context missing")

    try:
        delta.attach = broken_attach
        delta.notify = lambda t, m, **k: notices.append((t, m))
        rc = asyncio.run(delta._run_due(
            ["facebook", "instagram"], test_cfg(), state, path, False))
    finally:
        delta.attach, delta.notify = saved_attach, saved_notify
    check(rc == 1 and all(state[p]["consecutive_failures"] == 1
                          for p in ("facebook", "instagram")),
          "CDP attach 的 SystemExit 给所有到期平台各记一次失败")
    check(all("SystemExit" in state[p]["last_error"]
              for p in ("facebook", "instagram")),
          "SystemExit 类型与详情写进状态，未被宽泛 BaseException 吞掉")
    check(path.exists() and notices and "没能启动" in notices[0][0],
          "CDP 附着异常保存状态并通知")


print("\n[4c] 完整性检查接进增量收尾（D3）")

with tempfile.TemporaryDirectory() as tmp:
    arc = Archive(Path(tmp), "in_acme_us")
    for i in range(5):
        arc.append(Post(post_id="p%d" % i, platform="instagram", account="acme_us",
                        text="x", owner="acme_us",
                        created_at="2026-07-%02dT00:00:00Z" % (10 + i)))
    notices = []
    saved = delta.notify
    try:
        delta.notify = lambda t, m, **k: notices.append((t, m))
        entry = {**blank_entry(), "consecutive_quiet_days": 30}
        hits = delta.run_integrity(arc, entry, "instagram", now=NOW)
    finally:
        delta.notify = saved
    check([h["kind"] for h in hits] == ["quiet"], "零新增超阈值 → 检查命中")
    check(len(notices) == 1, "一个平台一条通知，不是一项一条")
    # 核验通知包含平台、实际天数和配置阈值，不固定业务阈值。
    _, threshold = delta.integrity.params("instagram")
    check("30 天" in notices[0][1] and ("%d 天" % threshold) in notices[0][1]
          and "instagram" in notices[0][1],
          "通知文案具体到平台/实际天数/阈值（反例：『发现问题』）")
    check("完整性告警" in notices[0][0] and "instagram" in notices[0][0],
          "标题点名是哪个平台")

with tempfile.TemporaryDirectory() as tmp:
    # 推进 last_new_at 触发告警；派生的 quiet 天数会被重算。
    seed = Archive(Path(tmp), "in_acme_us")      # 这篇已经在归档里 -> 本次 0 新增
    seed.append(Post(post_id="111", platform="instagram", account="acme_us",
                     text="hello world", owner="acme_us",
                     created_at="2025-08-12T12:00:00Z",
                     media=[Media(url="https://cdn.example.com/111.jpg",
                                  kind="image")]))
    ig_page = FakePage([resp(ig_payload())])
    long_ago = delta.iso(delta.utcnow() - timedelta(days=40))
    state = {"instagram": {**blank_entry(), "first_success": long_ago,
                           "last_new_at": long_ago, "last_success": long_ago}}
    rc, notices = run_due([ig_page], state, tmp, platforms=("instagram",))
    check(rc == 0, "检查命中不影响本次抓取的成败")
    check(state["instagram"]["consecutive_quiet_days"] == 40,
          "零新增天数由 last_new_at 重算得来（手改那个字段没用）")
    check(any("零新增" in m for _, m in notices),
          "成功跑完之后会自动跑一遍完整性检查并告警（D3 的接入点）")
    check(state["instagram"].get("alerts", {}).get("quiet_at"),
          "报过的记号落进了状态文件，明天不会再报一次")


print("\n[5] 运行状态：失败也要写，且不能刷新 last_success（C5）")

with tempfile.TemporaryDirectory() as tmp:
    dirty = Path(tmp) / "delta_state.json"
    dirty.write_text(json.dumps({"facebook": None, "instagram": "broken",
                                 "future_field": {"keep": True}}),
                     encoding="utf-8")
    original = dirty.read_bytes()
    try:
        delta.load_state(dirty)
    except delta.AccessDenied:
        check(dirty.read_bytes() == original, "坏平台状态失败闭合并保留原始字节")
    else:
        check(False, "坏平台状态不得被重建为零失败")


with tempfile.TemporaryDirectory() as tmp:
    path = Path(tmp) / "delta_state.json"
    old_state = {"facebook": {**blank_entry(), "consecutive_failures": 2}}
    path.write_text(json.dumps(old_state, ensure_ascii=False, indent=2),
                    encoding="utf-8")
    old_bytes = path.read_bytes()
    saved_replace = delta.Path.replace

    def fail_before_commit(self, target):
        raise OSError("simulated replace failure")

    replace_failed = False
    try:
        delta.Path.replace = fail_before_commit
        try:
            delta.save_state(path, {"facebook": blank_entry()})
        except OSError:
            replace_failed = True
    finally:
        delta.Path.replace = saved_replace
    check(replace_failed and path.read_bytes() == old_bytes,
          "replace 前失败不截断旧 state，失败预算/last_success 仍可完整读回")
    check(not list(path.parent.glob(".%s.*.tmp" % path.name)),
          "原子提交失败后 finally 清理同目录临时文件")

entry = blank_entry()
record_success(entry, NOW, 2)
check(entry["last_success"] == "2026-08-30T09:00:00Z", "成功刷新 last_success")
check(entry["last_new_count"] == 2 and entry["last_new_at"], "记下新增篇数与时刻")
check(entry["consecutive_quiet_days"] == 0, "刚抓到新帖，零新增天数为 0")

record_failure(entry, NOW + timedelta(days=1), "登录墙")
check(entry["last_success"] == "2026-08-30T09:00:00Z",
      "失败**不刷新** last_success —— 刷新了 --if-stale 就会以为跑过了")
check(entry["last_error"] == "登录墙" and entry["consecutive_failures"] == 1,
      "失败写下 last_error 并累加计数")
record_failure(entry, NOW + timedelta(days=2), "登录墙")
check(entry["consecutive_failures"] == 2, "连续失败继续累加")
record_success(entry, NOW + timedelta(days=3), 0)
check(entry["consecutive_failures"] == 0 and entry["last_error"] is None,
      "一次成功就把失败计数清零")
check(entry["consecutive_quiet_days"] == 3,
      "零新增天数按**天**算（距上次真抓到新帖 3 天），不是按跑了几次")

fresh = blank_entry()
check(quiet_days(fresh, NOW) == 0, "从没跑过时零新增天数为 0，不是无穷大")


# ==========================================================================
print("\n[6] 失败预算与降频（C7）")

e = blank_entry()
e["consecutive_failures"] = 2
check(not budget_exhausted(e, 3), "没到阈值时继续跑")
e["consecutive_failures"] = 3
check(budget_exhausted(e, 3), "达到阈值即停止自动运行")
check(not budget_exhausted(e, 0), "预算设 0 表示不启用该保护")

dcfg = test_cfg()
quiet = blank_entry()
quiet["consecutive_quiet_days"] = 10
check(effective_stale_hours(quiet, dcfg, "instagram", now=NOW) == 0.75,
      "连续零新增不再降频，仍按在岗最小 45 分钟")
check(effective_stale_hours(quiet, dcfg, "facebook", now=NOW)
      == effective_stale_hours(blank_entry(), dcfg, "facebook", now=NOW),
      "零新增天数不再影响间隔，安静账号与新账号取同一个值")
check(not hasattr(DeltaConfig(), "quiet_days_before_slowdown"),
      "降频旋钮已拆除，不留一个永远返回 0 的空壳让人误以为它还在工作")

run, why = stale_enough({"last_success": "2026-08-30T08:00:00Z"}, NOW, 26)
check(not run and "不足" in why, "距上次成功 1 小时 → 跳过，且说清楚为什么")
run, why = stale_enough({"last_success": "2026-08-28T08:00:00Z"}, NOW, 26)
check(run, "距上次成功 49 小时 → 该跑了")
run, _ = stale_enough(blank_entry(), NOW, 26)
check(run, "从没成功过 → 必须跑")


# ==========================================================================
print("\n[7] 入口：先判 stale 再抖动，预算用尽拒绝执行（C6/C7/E）")


class Recorder:
    def __init__(self):
        self.sleeps, self.notices, self.launched = [], [], []


def run_main(argv, state, rec, cdp=True, tmp=None, launch_error=None):
    """跑 main()，但把睡眠、通知、Chrome、真实抓取全部换成记账。"""
    path = Path(tmp) / "delta_state.json"
    for platform in delta.PLATFORMS:
        if platform in state:
            state[platform].setdefault("account", delta.cfg()["targets"][platform])
    path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    access = delta.AccessController(Path(tmp))
    access.path.unlink(missing_ok=True)  # each call is an independent isolated fixture
    access.initialize("isolated CLI fixture")
    saved = (delta.state_path, delta.time.sleep, delta.notify,
             delta.cdp_ready, delta.launch, delta.asyncio.run)
    try:
        delta.state_path = lambda: path
        delta.time.sleep = lambda s: rec.sleeps.append(s)
        delta.notify = lambda t, m, **k: rec.notices.append((t, m))
        delta.cdp_ready = lambda _p, **_kw: cdp
        def fake_launch(*_a, **_kw):
            rec.launched.append(True)
            if launch_error is not None:
                raise launch_error
            return True

        delta.launch = fake_launch
        delta.asyncio.run = lambda coro: (coro.close(), 0)[1]
        return delta.main(argv)
    finally:
        (delta.state_path, delta.time.sleep, delta.notify,
         delta.cdp_ready, delta.launch, delta.asyncio.run) = saved


with tempfile.TemporaryDirectory() as tmp:
    rec = Recorder()
    recent = delta.iso(delta.utcnow() - timedelta(minutes=10))
    state = {"facebook": {**blank_entry(), "last_success": recent},
             "instagram": {**blank_entry(), "last_success": recent}}
    rc = run_main(["--if-stale"], state, rec, tmp=tmp)
    check(rc == 0, "--if-stale 兼容参数不额外拦住持久到期的访问")
    check(rec.sleeps == [],
          "入口不重复睡眠或抽取随机时刻")

    rec = Recorder()
    old = delta.iso(delta.utcnow() - timedelta(days=3))
    state = {"facebook": {**blank_entry(), "last_success": old},
             "instagram": {**blank_entry(), "last_success": old}}
    run_main(["--if-stale"], state, rec, tmp=tmp)
    check(rec.sleeps == [], "入口不重新抽取延迟；仅共享持久 next_due 决定执行")

    draws = []
    for _ in range(3):
        rec = Recorder()
        run_main([], dict(state), rec, tmp=tmp)
        draws.append(rec.sleeps)
    check(draws == [[], [], []], "重启入口不重复抽取抖动")

    rec = Recorder()
    run_main(["--dry-run"], dict(state), rec, tmp=tmp)
    check(rec.sleeps == [], "--dry-run 不抖动（人在等着看结果）")
    rec = Recorder()
    run_main(["--no-jitter"], dict(state), rec, tmp=tmp)
    check(rec.sleeps == [], "--no-jitter 显式关掉抖动")

    # 失败预算用尽
    rec = Recorder()
    burnt = {"facebook": {**blank_entry(), "consecutive_failures": 3,
                          "last_error": "登录墙"},
             "instagram": {**blank_entry(), "consecutive_failures": 3,
                           "last_error": "登录墙"}}
    rc = run_main([], burnt, rec, tmp=tmp)
    check(rc == 2, "预算用尽 → 非 0 退出码，明确区别于'没到期'")
    check(rec.sleeps == [] and rec.launched == [],
          "预算用尽时连 Chrome 都不碰，更不会去抓")
    check(rec.notices and "停止自动运行" in rec.notices[0][0],
          "预算用尽会告警（这是唯一能让人知道该去重新登录的通道）")

    rec = Recorder()
    mixed = {"facebook": {**blank_entry(), "consecutive_failures": 3,
                           "last_error": "登录墙"},
             "instagram": blank_entry()}
    rc = run_main(["--no-jitter"], mixed, rec, tmp=tmp)
    check(rc == 2,
          "一边预算耗尽、另一边成功时仍返回 2，不把部分停摆记成整体成功")
    check(rec.notices and "停止自动运行" in rec.notices[0][0],
          "混合平台运行仍明确通知已停摆的平台")

    rc = run_main(["--reset-failures"], burnt, rec, tmp=tmp)
    saved_state = json.loads((Path(tmp) / "delta_state.json").read_text(encoding="utf-8"))
    check(rc == 2 and saved_state["facebook"]["consecutive_failures"] == 3,
          "--reset-failures 无 revision/reason 时拒绝清零")

    # 不同计划任务仍须共用进程锁。
    rec = Recorder()
    held = delta.DeltaRunLock(Path(tmp) / "delta.lock")
    held.__enter__()
    try:
        rc = run_main([], dict(state), rec, tmp=tmp)
    finally:
        held.__exit__(None, None, None)
    check(rc == 75 and rec.sleeps == [] and rec.launched == [],
          "另一个计划任务持锁返回75，不碰 Chrome，调度器保留到期时刻")

    # Chrome 没在跑
    rec = Recorder()
    run_main([], dict(state), rec, cdp=False, tmp=tmp)
    check(rec.launched, "Chrome 没在跑时会自动拉起（用户 2026-08-30 拍板）")

    saved_load = DeltaConfig.__dict__["load"]
    try:
        DeltaConfig.load = classmethod(
            lambda cls: test_cfg(autostart_chrome=False))
        rec = Recorder()
        rc = run_main([], dict(state), rec, cdp=False, tmp=tmp)
    finally:
        DeltaConfig.load = saved_load
    saved_state = json.loads(
        (Path(tmp) / "delta_state.json").read_text(encoding="utf-8"))
    check(rc == 1 and not rec.launched,
          "autostart_chrome=false 且端口未就绪时失败退出、不擅自拉起 Chrome")
    check(all(saved_state[p]["consecutive_failures"] == 1
              and "端口" in saved_state[p]["last_error"]
              for p in ("facebook", "instagram")),
          "禁止自动拉起的启动失败仍保存到两个到期平台的失败状态")
    check(rec.notices and "没能启动" in rec.notices[0][0],
          "禁止自动拉起的启动失败也会通知")

    # 覆盖 launch 返回 False、普通异常及 SystemExit。
    for launch_error in (PermissionError("profile denied"),
                         SystemExit("chrome exe missing")):
        rec = Recorder()
        rc = run_main([], dict(state), rec, cdp=False, tmp=tmp,
                      launch_error=launch_error)
        saved_state = json.loads(
            (Path(tmp) / "delta_state.json").read_text(encoding="utf-8"))
        error_type = type(launch_error).__name__
        check(rc == 1 and len(rec.launched) == 1,
              "launch 抛 %s 时进入失败闭环、不会直穿 main" % error_type)
        check(all(saved_state[p]["consecutive_failures"] == 1
                  and error_type in saved_state[p]["last_error"]
                  for p in ("facebook", "instagram")),
              "launch 的 %s 给所有到期平台记录失败详情" % error_type)
        check(rec.notices and "没能启动" in rec.notices[0][0],
              "launch 的 %s 保存状态后发出启动失败通知" % error_type)


# ==========================================================================
print("\n[8] 转储裁剪：只裁增量的，回填那两份永不自动删")

with tempfile.TemporaryDirectory() as d:
    base = Path(d)
    for i in range(5):
        (base / ("_capture_delta_%d.json" % (1788000000 + i))).write_text("{}")
    (base / "_capture_1788072462.json").write_text("{}")     # 回填留下的
    removed = prune_captures(base, keep=3)
    check(removed == 2, "只保留最近 3 份增量转储")
    check((base / "_capture_1788072462.json").exists(),
          "**回填的 capture 一份都没动** —— 那是离线重放的唯一输入")
    check(sorted(p.name for p in base.glob("_capture_delta_*.json"))
          == ["_capture_delta_1788000002.json", "_capture_delta_1788000003.json",
              "_capture_delta_1788000004.json"],
          "留下的是最近的三份，不是随便三份")


# ==========================================================================
print("\n[8.5] 逐篇记下『发现』与『落档』，两者必须能分开")


class FailingRequest(FakeRequest):
    """CDN 取不到图：源响应解析成功，但这一篇落不了档。"""

    async def get(self, url, headers=None):
        self.gets.append(url)

        class R:
            ok = False
            status = 404
            headers = {}

            async def body(self):
                return b""
        return R()


def facts_of(state_dir):
    path = Path(state_dir) / "monitoring_facts.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()]


with tempfile.TemporaryDirectory() as d:
    arc = Archive(Path(d), "in_acme_us")
    journal = MonitoringJournal(Path(d) / "state", now=NOW, inspect_running=False)
    asyncio.run(delta_once(FakeCtx(FakePage([resp(ig_payload())])), "instagram",
                           "acme_us", arc, test_cfg(), facts=journal))
    rows = facts_of(Path(d) / "state")
    check([row["event"] for row in rows] == ["post_discovered", "post_captured"],
          "发现先记、落档后记 —— 顺序反了就无法表达『发现了但没抓下来』")
    check(rows[0]["known"] is False and rows[0]["images"] == 1
          and rows[0]["platform"] == "instagram" and rows[0]["post_id"] == "111",
          "发现事实带平台、post_id、图片数和『是不是已有帖』")
    check(bool(rows[0]["permalink"]) and bool(rows[0]["created_at"]),
          "发现事实带原帖链接与原帖时间，卡片才能给出『查看原帖』")
    check(rows[1]["images"] == 1 and bool(rows[1]["folder"]),
          "落档事实带实际落档的图片数与落点目录名")

with tempfile.TemporaryDirectory() as d:
    arc = Archive(Path(d), "in_acme_us")
    asyncio.run(delta_once(FakeCtx(FakePage([resp(ig_payload())])), "instagram",
                           "acme_us", arc, test_cfg()))
    check(not (Path(d) / "state").exists(),
          "不传 facts 时一个字也不写，旧调用方行为不变")

with tempfile.TemporaryDirectory() as d:
    arc = Archive(Path(d), "in_acme_us")
    journal = MonitoringJournal(Path(d) / "state", now=NOW, inspect_running=False)
    asyncio.run(delta_once(FakeCtx(FakePage([resp(ig_payload())])), "instagram",
                           "acme_us", arc, test_cfg(), dry_run=True, facts=journal))
    check(facts_of(Path(d) / "state") == [], "--dry-run 不写观测事实")

with tempfile.TemporaryDirectory() as d:
    # 已有残缺帖同样三张图、同样 URL，新响应声称完整：should_append 放行（新的 media_complete
    # 为 True），但 CDN 全部取不到之后落盘成果没增加，append 拒绝写入。
    arc = Archive(Path(d), "in_acme_us")
    arc.append(Post(post_id="111", platform="instagram", account="acme_us",
                    text="hello world", created_at="2025-08-12T12:00:00Z", owner="acme_us",
                    permalink="https://www.instagram.com/p/abc/",
                    media=[Media(url="https://cdn.example.com/111_%d.jpg" % i, kind="image")
                           for i in range(3)],
                    media_complete=False))
    journal = MonitoringJournal(Path(d) / "state", now=NOW, inspect_running=False)
    ctx = FakeCtx(FakePage([resp(ig_payload("111", n_media=3))]))
    ctx.request = FailingRequest()
    asyncio.run(delta_once(ctx, "instagram", "acme_us", arc, test_cfg(), facts=journal))
    rows = facts_of(Path(d) / "state")
    check([row["event"] for row in rows] == ["post_discovered", "post_capture_incomplete"],
          "媒体补不全时记 post_capture_incomplete，不冒充已落档")
    check(rows[0]["known"] is True,
          "补齐已有帖标 known=True，不和社媒新增混在一个计数里")


# ==========================================================================
print("\n[9] 杂项")

check(profile_url("instagram", "acme.us") == "https://www.instagram.com/acme.us/",
      "IG 主页 URL 拼接正确")
check(profile_url("facebook", "acme") == "https://www.facebook.com/acme/",
      "FB 主页 URL 拼接正确")
check(delta.DeltaConfig.load().max_scrolls <= 3,
      "config.toml 里的 max_scrolls 没被调到接近『滚到底』的量级")
check(delta.DeltaConfig.load().failure_budget > 0,
      "config.toml 里的失败预算是启用的（设 0 等于关掉这道保护）")

print("\n" + ("全部通过" if not fails else "%d 项失败" % len(fails)))
raise SystemExit(1 if fails else 0)
