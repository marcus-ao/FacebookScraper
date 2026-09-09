r"""用已保存的增量转储，离线跑一遍**增量的真实代码路径**。

`tools/replay.py` 重放的是**回填**，而且只走 `extract()` + `partition_by_owner()`。
本工具补的是另一半：把 `_capture_delta_*.json` 喂给一个假页面，让
`routes.delta.delta_once()` **原样跑完**——响应收集、登录墙判定、归属过滤、
合作方哨兵、`min_own_posts` 闸、`ScanResult` 摘要、`should_append` 幂等判断，
全都是真实实现，只有浏览器是假的。

**这个工具存在的理由**：增量每跑一次就是一次真实露面，而"改完解析器到底对不对"
不该靠再露面一次来回答。2026-08-30 的教训是反过来的版本——
当时靠一次实测才发现 IG 只认出 1 篇，然后又猜错了原因。有了这个工具，
同样的问题在盘上就能复现和验证。

    python -m tools.dryrun_delta instagram
    python -m tools.dryrun_delta facebook --capture archive/fa_x/_capture_delta_1.json
    python -m tools.dryrun_delta instagram --break-coauthors   # 自检：闸响不响

⚠️ **全程零网络**：不附着 Chrome、不发请求、不下载媒体、不写归档
（内部固定 `dry_run=True`）。媒体下载那一段因此**验证不到**——
那是唯一还需要真实跑一次的东西，见 `docs/MANUAL_STEPS.md` 第 2 节。

`--break-coauthors` 把 `parse.on_timeline_of` 换回"只比 owner"的旧实现
（CR-19 修复前的形态），用来回答**"哨兵真的会响吗"**。
它只改内存里的函数引用，不碰任何文件。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import parse                                       # noqa: E402
from core.config import cfg                                  # noqa: E402
from core.console import force_utf8                          # noqa: E402
from core.store import Archive                               # noqa: E402
from routes.delta import DeltaBlocked, DeltaConfig, delta_once   # noqa: E402

PREFIX = {"facebook": "fa", "instagram": "in"}

# 假响应的 URL 必须命中 core.capture.INTEREST，否则 Collector 会直接忽略它。
# 两个平台各取一条真实形态的接口路径。
FAKE_URL = {
    "instagram": "https://www.instagram.com/api/v1/feed/user/1/",
    "facebook": "https://www.facebook.com/api/graphql/",
}


class FakeResponse:
    def __init__(self, url: str, body: str) -> None:
        self.url, self.status, self._body = url, 200, body

    async def text(self) -> str:
        return self._body


class FakeMouse:
    """滚轮推动假页面的 scrollY —— 让 human_scroll 的"滚了没动"回读逻辑走真的分支。"""

    def __init__(self, page: "FakePage") -> None:
        self.page = page

    async def move(self, x, y) -> None:
        pass

    async def wheel(self, dx, dy) -> None:
        self.page.scroll_y += 800


class FakePage:
    """goto 时把转储里的每一段 JSON 当成一条接口响应喂给监听器。

    ⚠️ **一段 payload 一条响应**，不是把整个数组当成一条：转储里那 17 段
    本来就来自 17 次响应，合并成一条会让 Collector 的分段统计与真实情况不符，
    而那个数字正是排查时要看的。
    """

    def __init__(self, payloads: list, url: str) -> None:
        self._payloads = payloads
        self._url = url
        self.url = ""
        self.scroll_y = 0.0
        self.mouse = FakeMouse(self)
        self.handlers: dict[str, list] = {}

    async def evaluate(self, expr: str):
        if "innerWidth" in expr:
            return [1280, 800]
        if "scrollY" in expr:
            return self.scroll_y
        if "querySelectorAll" in expr:
            return []
        return None

    def on(self, event, fn) -> None:
        self.handlers.setdefault(event, []).append(fn)

    def remove_listener(self, event, fn) -> None:
        if fn in self.handlers.get(event, []):
            self.handlers[event].remove(fn)

    async def goto(self, url, **kw) -> None:
        self.url = url
        for payload in self._payloads:
            body = json.dumps(payload, ensure_ascii=False)
            for fn in self.handlers.get("response", []):
                fn(FakeResponse(self._url, body))

    async def close(self) -> None:
        pass


class FakeRequest:
    async def get(self, url, headers=None):
        raise AssertionError("离线自检不应该发起任何网络请求")


class FakeCtx:
    def __init__(self, page: FakePage) -> None:
        self.page = page
        self.request = FakeRequest()

    async def new_page(self) -> FakePage:
        return self.page


def newest_delta_capture(base: Path) -> Path | None:
    caps = sorted(base.glob("_capture_delta_*.json"))
    return caps[-1] if caps else None


def offline_cfg() -> DeltaConfig:
    """真实配置，但把所有等待清零——假页面上没有什么好等的。

    ⚠️ **`min_own_posts` 保持配置里的真值**：它正是要验的东西，改了就等于没验。
    """
    d = DeltaConfig.load()
    d.request_gap_seconds = 0.0
    d.first_screen_seconds = 0.0
    d.max_session_seconds = 120.0
    return d


def run(platform: str, capture: Path | None, break_coauthors: bool) -> int:
    c = cfg()
    account = c["targets"][platform]
    base = c.archive_dir / ("%s_%s" % (PREFIX[platform], account))
    cap = capture or newest_delta_capture(base)
    if cap is None or not cap.exists():
        print("[!] %s 下没有 _capture_delta_*.json —— 先让用户跑一次增量" % base)
        print("    （回填的 _capture_*.json 请用 python -m tools.replay 重放）")
        return 1

    payloads = json.loads(cap.read_text(encoding="utf-8"))
    if not isinstance(payloads, list):
        print("[!] %s 的顶层不是数组，不是增量转储的形态" % cap.name)
        return 1

    print("平台     : %s / %s" % (platform, account))
    print("capture  : %s  (%d KB, %d 段响应)"
          % (cap.name, cap.stat().st_size // 1024, len(payloads)))
    if break_coauthors:
        print("模式     : --break-coauthors（把合作帖判定退回 CR-19 修复前）")
    print()

    arc = Archive(c.archive_dir, base.name)
    dcfg = offline_cfg()
    ctx = FakeCtx(FakePage(payloads, FAKE_URL[platform]))

    original = parse.on_timeline_of
    if break_coauthors:
        # 只比 owner —— 这正是当初丢掉 263 篇的那一版判定
        parse.on_timeline_of = lambda post, target: (
            post.owner == (target or "").strip().lower())
    try:
        res = asyncio.run(delta_once(ctx, platform, account, arc, dcfg,
                                     dry_run=True))
    except DeltaBlocked as e:
        print("[!] 当次中止：%s" % e)
        print("\n判读：中止本身可能是**对的**——"
              "它说明那道闸拦住了一次'没拿到时间线'。")
        # --break-coauthors 下中止是预期结果，不算失败
        return 0 if break_coauthors else 1
    finally:
        parse.on_timeline_of = original

    print(res.summary())
    if res.suspect:
        who = sorted({(s.get("owner") or "?") for s in res.suspect})
        print("[!] 丢弃的 %d 篇里有 %d 篇来自已知合作方（%s%s）"
              % (res.rejected, len(res.suspect), "、".join(who[:6]),
                 " 等" if len(who) > 6 else ""))
    print()
    print("解析出的段数 : %d" % res.payloads)
    print("本账号       : %d（原创 %d · 合作 %d）"
          % (res.own, res.authored, res.collab))
    print("丢弃         : %d，其中疑似漏判 %d" % (res.rejected, len(res.suspect)))
    print("按归档判定新增: %d 篇（dry-run，未写盘）" % res.new)
    if res.stale_view():
        print("[!] 看到的最新一篇（%s）比归档最新（%s）还旧"
              % (res.newest_seen[:10], res.newest_known[:10]))

    if break_coauthors and not res.suspect:
        print("\n[!] **自检失败**：合作帖判定已被退回旧实现，哨兵却没有响。"
              "说明这道防线不管用了，去看 core/integrity.check_dropped_partners。")
        return 1
    if break_coauthors:
        print("\n自检通过：判定退化时哨兵会响。")
    return 0


def main(argv=None) -> int:
    force_utf8()
    ap = argparse.ArgumentParser(
        description="用已保存的增量转储离线跑一遍增量的真实代码路径（零网络）")
    ap.add_argument("platform", choices=sorted(PREFIX))
    ap.add_argument("--capture", type=Path, default=None,
                    help="指定转储文件；不给则取该账号最新的一份增量转储")
    ap.add_argument("--break-coauthors", action="store_true",
                    help="自检：把合作帖判定退回旧实现，验证哨兵/闸会不会响")
    args = ap.parse_args(argv)
    return run(args.platform, args.capture, args.break_coauthors)


if __name__ == "__main__":
    raise SystemExit(main())
