"""登录态增量自测（方案 B）。对应实施计划 C2–C7 的【验收】里能离线做的部分。

这套测试盯的**不是"顺利时能抓到帖子"**，而是不顺利时的行为——增量每天跑一次、
无人盯着，所以下面几件事比解析正确更要紧：

  1. **异常即停**：登录墙 / 401 / 429 / 解析出 0 篇，都必须当次立即停止，
     不重试、不换 UA、不绕。继续试探是把"可能被注意到"变成"确定被注意到"；
  2. **深度上限**：只滚 `max_scrolls` 屏，绝不滚到底；
  3. **失败也要写状态**：否则"连续三天失败"和"连续三天没新帖"长得一模一样，
     而这两件事一个要人去重新登录、一个什么都不用做；
  4. **失败预算**：连续失败达阈值就停止自动运行——每天硬撞一个已失效的会话，
     是这条路径上最坏的行为；
  5. **先判 stale 再抖动**：反过来的话每次唤醒都要先睡半小时才发现不用跑。

⚠️ 真实抓取的验收（owner 全对、连跑两次第二次 0 新增）要对真实账号跑，
不在这套里——每跑一次就是一次真实露面。
"""
import asyncio
import json
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core.console import force_utf8   # noqa: E402

force_utf8()   # 输出被重定向到文件/管道时，cp936 编不出 ß/⚠ 会让整套测试崩掉

from core.capture import Collector, prune_captures                  # noqa: E402
from core.store import Archive, Media, Post                         # noqa: E402
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
                max_scrolls=2, max_session_seconds=30.0, keep_captures=3,
                start_jitter_minutes=45.0, failure_budget=3,
                stale_after_hours=26.0, slowdown_stale_after_hours=72.0,
                _quiet_slowdown={"facebook": 14, "instagram": 7})
    base.update(kw)
    return DeltaConfig(**base)


# ---- 假的 Playwright 对象 ------------------------------------------------

class FakeResponse:
    def __init__(self, url, status=200, body="{}"):
        self.url, self.status, self._body = url, status, body

    async def text(self):
        return self._body


class FakeMouse:
    def __init__(self):
        self.wheels = []

    async def wheel(self, dx, dy):
        self.wheels.append((dx, dy))


class FakePage:
    """goto 时把预置的响应喂给监听器，模拟首屏接口。"""

    def __init__(self, responses, final_url=None):
        self._responses = responses
        self._final_url = final_url
        self.url = ""
        self.mouse = FakeMouse()
        self.handlers = {}
        self.closed = False
        self.goto_calls = []

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
    def __init__(self, payload=b"\xff\xd8jpegbytes"):
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
               text="hello world", n_media=1):
    node = {"pk": pk, "code": code, "taken_at": ts,
            "user": {"username": owner, "full_name": "Acme US"},
            "caption": {"text": text},
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
    n = asyncio.run(human_scroll(page, cfg_l, cfg_l.pacer()))
    check(len(page.mouse.wheels) == limit == n,
          "max_scrolls=%d 时正好滚 %d 屏" % (limit, limit))

page = FakePage([])
asyncio.run(human_scroll(page, test_cfg(max_scrolls=4), test_cfg().pacer()))
deltas = {dy for _, dy in page.mouse.wheels}
check(len(deltas) > 1, "每屏的滚动距离不相同 —— 匀速等距滚动本身是行为指纹")
check(all(0 < dy for _, dy in page.mouse.wheels), "只向下滚")


# ==========================================================================
print("\n[3] 主流程：幂等、归属过滤、残缺帖可升级、dry-run 不写盘")

with tempfile.TemporaryDirectory() as d:
    arc = Archive(Path(d), "in_acme_us")
    page = FakePage([resp(ig_payload())])
    ctx = FakeCtx(page)
    n1 = asyncio.run(delta_once(ctx, "instagram", "acme_us", arc, test_cfg()))
    check(n1 == 1, "第一次跑：新增 1 篇")
    check(len(arc.rows()) == 1, "写进了 manifest")
    post_dirs = [p for p in (Path(d) / "in_acme_us" / "posts").iterdir() if p.is_dir()]
    check(len(post_dirs) == 1 and (post_dirs[0] / "post.json").exists(),
          "落进了每帖一个文件夹（J 组布局）")
    check(any(p.suffix == ".jpg" for p in post_dirs[0].iterdir()),
          "配图下载进了该帖自己的文件夹")

    # 【C2 验收】连续运行两次，第二次新增 0 篇
    arc2 = Archive(Path(d), "in_acme_us")
    page2 = FakePage([resp(ig_payload())])
    n2 = asyncio.run(delta_once(FakeCtx(page2), "instagram", "acme_us", arc2, test_cfg()))
    check(n2 == 0, "第二次跑：新增 0 篇（幂等）")
    check(FakeCtx(page2).request.gets == [], "幂等时不重复请求 CDN")

with tempfile.TemporaryDirectory() as d:
    arc = Archive(Path(d), "in_acme_us")
    payload = {"data": {"items": [
        ig_payload("111", owner="acme_us")["data"]["items"][0],
        ig_payload("222", owner="someone_else")["data"]["items"][0],
    ]}}
    page = FakePage([resp(payload)])
    n = asyncio.run(delta_once(FakeCtx(page), "instagram", "acme_us", arc, test_cfg()))
    check(n == 1, "他人帖不计入新增")
    rejected = (Path(d) / "in_acme_us" / "_rejected.jsonl").read_text(encoding="utf-8")
    check("someone_else" in rejected, "他人帖写进了 _rejected.jsonl，不是静默丢弃")
    check("222" not in json.dumps(arc.rows()), "他人帖没有进归档")

with tempfile.TemporaryDirectory() as d:
    # 残缺帖必须能被补全：用 should_append 而不是 has()（CR-03）
    arc = Archive(Path(d), "in_acme_us")
    stub = Post(post_id="111", platform="instagram", account="acme_us",
                text="", created_at="2025-08-12T00:00:00Z", owner="acme_us",
                media=[Media(url="https://cdn.example.com/111.jpg", kind="image")],
                media_complete=False)
    arc.append(stub)
    page = FakePage([resp(ig_payload("111", n_media=3))])
    n = asyncio.run(delta_once(FakeCtx(page), "instagram", "acme_us", arc, test_cfg()))
    check(n == 1, "已存但媒体不全的帖子会被重新抓取并升级（不是 has() 语义）")
    row = [r for r in arc.rows() if r["post_id"] == "111"][0]
    check(len(row["media"]) == 3 and row["media_complete"],
          "升级后媒体补全，media_complete 回到 True")

with tempfile.TemporaryDirectory() as d:
    arc = Archive(Path(d), "in_acme_us")
    ctx = FakeCtx(FakePage([resp(ig_payload())]))
    n = asyncio.run(delta_once(ctx, "instagram", "acme_us", arc, test_cfg(),
                               dry_run=True))
    check(n == 1, "--dry-run 会报出将要新增的篇数")
    check(arc.rows() == [], "--dry-run 不写 manifest")
    check(ctx.request.gets == [], "--dry-run 不下载媒体")
    check(not list((Path(d) / "in_acme_us").glob("_capture_delta_*.json")),
          "--dry-run 不落转储")


# ==========================================================================
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
    ctx = MultiCtx(pages)
    notices = []

    class FC:
        archive_dir = Path(tmp)
        state_dir = Path(tmp)

        def __getitem__(self, key):
            return {"facebook": "acme_page", "instagram": "acme_us"}

    saved = (delta.cfg, delta.attach, delta.notify)
    try:
        delta.cfg = lambda: FC()
        delta.notify = lambda t, m, **k: notices.append((t, m))

        async def fake_attach():
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


print("\n[5] 运行状态：失败也要写，且不能刷新 last_success（C5）")

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
check(effective_stale_hours(quiet, dcfg, "instagram") == 72.0,
      "IG 连续 10 天零新增（阈值 7）→ 降频到 72 小时")
check(effective_stale_hours(quiet, dcfg, "facebook") == 26.0,
      "同样 10 天，FB 阈值是 14 天 → 仍按 26 小时跑（两个账号节奏差一个量级）")
check(DeltaConfig(_quiet_slowdown=9).quiet_days_before_slowdown("facebook") == 9,
      "阈值写成一个数时两个平台通用（向后兼容，不强制写成表）")
loaded = DeltaConfig.load()
check(loaded.quiet_days_before_slowdown("facebook")
      != loaded.quiet_days_before_slowdown("instagram"),
      "config.toml 里两个平台的降频阈值确实是分开的")

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


def run_main(argv, state, rec, cdp=True, tmp=None):
    """跑 main()，但把睡眠、通知、Chrome、真实抓取全部换成记账。"""
    path = Path(tmp) / "delta_state.json"
    path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    saved = (delta.state_path, delta.time.sleep, delta.notify,
             delta.cdp_ready, delta.launch, delta.asyncio.run)
    try:
        delta.state_path = lambda: path
        delta.time.sleep = lambda s: rec.sleeps.append(s)
        delta.notify = lambda t, m, **k: rec.notices.append((t, m))
        delta.cdp_ready = lambda _p: cdp
        delta.launch = lambda *a, **k: rec.launched.append(True) or True
        delta.asyncio.run = lambda coro: (coro.close(), 0)[1]
        return delta.main(argv)
    finally:
        (delta.state_path, delta.time.sleep, delta.notify,
         delta.cdp_ready, delta.launch, delta.asyncio.run) = saved


with tempfile.TemporaryDirectory() as tmp:
    rec = Recorder()
    recent = delta.iso(delta.utcnow() - timedelta(hours=1))
    state = {"facebook": {**blank_entry(), "last_success": recent},
             "instagram": {**blank_entry(), "last_success": recent}}
    rc = run_main(["--if-stale"], state, rec, tmp=tmp)
    check(rc == 0, "--if-stale 且都不到期 → 正常退出")
    check(rec.sleeps == [],
          "不到期时**没有先睡半小时** —— 顺序必须是先判 stale 再抖动")

    rec = Recorder()
    old = delta.iso(delta.utcnow() - timedelta(days=3))
    state = {"facebook": {**blank_entry(), "last_success": old},
             "instagram": {**blank_entry(), "last_success": old}}
    run_main(["--if-stale"], state, rec, tmp=tmp)
    check(len(rec.sleeps) == 1 and 0 <= rec.sleeps[0] <= 45 * 60,
          "到期时才抖动，且在 0..start_jitter_minutes 之间")

    draws = []
    for _ in range(3):
        rec = Recorder()
        run_main([], dict(state), rec, tmp=tmp)
        draws.append(rec.sleeps[0])
    check(len(set(draws)) == 3, "连续三次启动的延迟各不相同（不是固定整点）")

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

    rc = run_main(["--reset-failures"], burnt, rec, tmp=tmp)
    saved_state = json.loads((Path(tmp) / "delta_state.json").read_text(encoding="utf-8"))
    check(rc == 0 and saved_state["facebook"]["consecutive_failures"] == 0,
          "--reset-failures 清零，人工确认后能继续")

    # Chrome 没在跑
    rec = Recorder()
    run_main([], dict(state), rec, cdp=False, tmp=tmp)
    check(rec.launched, "Chrome 没在跑时会自动拉起（用户 2026-08-30 拍板）")


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
