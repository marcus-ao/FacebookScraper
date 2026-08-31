r"""Day 1 · 一次性历史回填（登录态 + 人工滚动 + 响应拦截）

形态：脚本不驱动滚动，你自己滚。
    因此没有任何可识别的自动化行为特征 —— 滚动的确实是人。
    脚本只做三件事：挂响应监听、等你滚完、把捞到的 JSON 落盘。

前置：
    1. 双击 scripts\start_chrome.bat 起专用 Chrome
    2. 在那个窗口里登录抓取用的小号
    3. 保持窗口开着，运行本脚本

跑一次就够。跑完这个账号的历史内容就全在 archive/ 里了，
之后的新帖由 routes/delta.py 每天接手（方案 B：登录态 + CDP 附着，
见 docs/IMPLEMENTATION_PLAN.md 第 0 节的方案变更说明）。
"""
from __future__ import annotations

import asyncio
import sys
import threading
import time

from core.capture import INTEREST, Collector  # noqa: F401  （INTEREST 供外部引用）
from core.capture import atomic_write_json, download_media as _download
from core.chrome import attach
from core.config import cfg
from core import integrity
from core.parse import extract, partition_by_owner
from core.store import Archive

# 多久没有新响应就提示"可以收尾了"。页面滚到底之后不会再发请求，
# 但操作者看不见网络活动，只能靠猜。给个明确信号。
STALL_HINT_SECONDS = 20


class ScrollProgress:
    """给滚动的人看的进度。对应实施计划的 B8。

    **旧版打印的是"已捕获 N 个响应 / M 段 JSON"，那两个数字对操作者没有意义**——
    他无法据此判断还要滚多久、有没有到最早一篇。2026-08-30 的首次回填里，
    Facebook 只拿到约 2 个月的内容而 Instagram 有 6 年，当时**没人能判断
    这是"FB 就这么多"还是"没滚到底"**（后经用户确认是前者，但那是事后追认，
    不是当场可见）。

    这里只做增量解析用于显示：每次只解析新到的那几段 JSON，
    结果并不参与最终归档——收尾时会对全部 payload 重新做一次权威解析。
    显示用的统计允许有一点点偏差，换的是不卡住滚动的人。

    ❌ **本类不得驱动页面。** 人工滚动的全部价值在于滚动的确实是人。
    """

    def __init__(self, platform: str, account: str) -> None:
        self.platform = platform
        self.account = account
        self.ids: set[str] = set()
        self.earliest: str = ""
        self._scanned = 0

    def update(self, payloads: list[dict]) -> None:
        fresh = payloads[self._scanned:]
        self._scanned = len(payloads)
        if not fresh:
            return
        try:
            posts, _ = partition_by_owner(
                extract(fresh, self.platform, self.account, route="backfill"),
                self.account)
        except Exception:
            return          # 显示用的统计，坏了也不能影响正在进行的抓取
        for p in posts:
            self.ids.add(p.post_id)
            if p.created_at and (not self.earliest or p.created_at < self.earliest):
                self.earliest = p.created_at

    def line(self, quiet_seconds: float) -> str:
        earliest = self.earliest[:10] if self.earliest else "—"
        tail = ("  ·  %d 秒没有新内容了，可以按 Enter 收尾" % int(quiet_seconds)
                if quiet_seconds >= STALL_HINT_SECONDS else "")
        return "  已抓到 %d 篇（%s）· 最早 %s%s" % (
            len(self.ids), self.account, earliest, tail)


def _stdin_waiter(readline=None) -> tuple[threading.Event, threading.Thread]:
    """在守护线程里等 Enter；超时退出时不会被阻塞的 ``readline`` 拖住进程。"""
    done = threading.Event()
    read = readline or sys.stdin.readline

    def wait() -> None:
        try:
            read()
        except Exception:
            # stdin 被关闭也等价于“结束人工等待”；不要让守护线程打印无关 traceback。
            pass
        finally:
            done.set()

    thread = threading.Thread(target=wait, name="backfill-stdin", daemon=True)
    thread.start()
    return done, thread


async def run(platform: str) -> int:
    c = cfg()
    account = c["targets"][platform]
    url = ({"instagram": f"https://www.instagram.com/{account}/",
            "facebook": f"https://www.facebook.com/{account}/"})[platform]

    col = Collector()
    pw, browser, ctx = await attach()
    page = None
    response_handler = col.submit

    try:
        page = await ctx.new_page()
        page.on("response", response_handler)
        await page.goto(url, wait_until="domcontentloaded")
        print(f"\n已打开 {url}")
        print("=" * 62)
        print("  现在请在那个 Chrome 窗口里手工向下滚动，直到看见最早的帖子。")
        print("  慢慢滚，让每屏内容都加载出来（看到图片显示出来再继续）。")
        print("  下面的「最早」会随着你往下滚不断往前走 —— 它不动了就是到底了。")
        print("  滚完之后回到这里按 Enter。")
        print("=" * 62)

        deadline = time.monotonic() + c.get("backfill", "max_session_seconds", 1800)
        progress = ScrollProgress(platform, account)
        last_hits, last_change = col.hits, time.monotonic()
        width = 0
        waiter, _ = _stdin_waiter()
        while not waiter.is_set():
            await asyncio.sleep(2)
            if time.monotonic() > deadline:
                print("\n达到最长会话时间，自动收尾。")
                break
            progress.update(col.payloads)
            if col.hits != last_hits:
                last_hits, last_change = col.hits, time.monotonic()
            line = progress.line(time.monotonic() - last_change)
            # 补空格盖掉上一行的残留：新行比旧行短时，尾巴会留在屏幕上
            print("\r" + line.ljust(width), end="", flush=True)
            width = max(width, len(line))
        print()

        # 停止接收新事件，再等已经到达的响应体读完；否则用户按 Enter 时仍在处理的
        # 最后几段 JSON 会被无声漏出 capture 文件。
        page.remove_listener("response", response_handler)
        await col.drain()

        # 无条件转储原始响应，且必须在解析之前。
        # 解析器是按已知响应结构写的，真实结构一旦不同就会解析出 0 篇 ——
        # 没有这份转储，你滚了 20 分钟的成果会全部丢失，且无从排查。
        arc = Archive(c.archive_dir, f"{platform[:2]}_{account}")
        dump = arc.base / f"_capture_{int(time.time())}.json"
        atomic_write_json(dump, col.payloads)
        print(f"原始响应已转储 → {dump}  ({dump.stat().st_size // 1024} KB)")

        posts = extract(col.payloads, platform, account, route="backfill")
        print(f"解析出 {len(posts)} 个候选帖子")
        if not posts and col.payloads:
            print("\n[!] 捕获到响应但一篇都没解析出来 —— 说明响应结构与解析器不符。")
            print(f"    原始数据在 {dump.name}，把它发给我，我按真实结构改解析器。")
            print("    不用重滚。")

        # 人工滚动时页面会加载推荐内容和被 @ 的 UGC，它们和目标账号的帖子混在
        # 同一批响应里。不筛的话下游会翻译并发布他人内容（2026-08-30 实测，
        # Instagram 一次回填混进 266 条来自另外 195 个账号的帖子）。
        posts, rejected = partition_by_owner(posts, account)
        if rejected:
            n_rej = arc.record_rejected(rejected)
            others = {r["owner_name"] or r["owner"] for r in rejected if r["owner"]}
            print(f"  - 丢弃 {len(rejected)} 个不属于 {account} 的节点"
                  f"（来自 {len(others)} 个其它账号），已记入 _rejected.jsonl"
                  f"（新增 {n_rej} 条）")

        # 原创 / 合作分开报。Instagram 实测 1019 篇里 263 篇是合作帖
        # （别人发布、本账号是 coauthor，同样在本账号主页上），
        # 而且**最近一年的主要内容形式就是合作帖**——只报一个总数的话，
        # 合作帖判定哪天失效了，这里会安静地少掉几百篇而看不出来。
        target = (account or "").strip().lower()
        n_authored = sum(1 for p in posts if p.owner == target)
        print(f"本账号帖子 {len(posts)} 篇"
              f"（原创 {n_authored} · 合作 {len(posts) - n_authored}）")

        # 丢弃的里面有没有已知合作方？有就是"归属判定漏判"的强提示。
        # 名单同时取自归档与本次留下的这批：第一次回填时归档是空的。
        suspect = integrity.check_dropped_partners(
            rejected,
            integrity.known_partners(
                arc.rows() + [p.to_row() for p in posts], account))
        if suspect:
            who = sorted({(s.get("owner") or "?") for s in suspect})
            print(f"[!] 丢弃的里面有 {len(suspect)} 篇来自**已知合作方**"
                  f"（{'、'.join(who[:4])}）—— 合作帖判定可能漏判了。"
                  f"原始响应在 {dump.name} 里，可离线查，不用重滚。")

        n = 0
        for post in posts:
            if not arc.should_append(post):
                continue
            await _download(ctx, arc, post, url)
            if not arc.append(post):
                print(f"    ! {post.post_id} 媒体仍未补全，保留原归档并留待下次重试")
                continue
            n += 1
            imgs = sum(1 for m in post.media if m.kind == "image")
            vids = sum(1 for m in post.media if m.kind == "video")
            print(f"  + {post.post_id}  {imgs}图/{vids}视频  "
                  f"{post.text[:38].replace(chr(10), ' ')}")
        return n
    finally:
        try:
            if page is not None:
                page.remove_listener("response", response_handler)
                await col.drain()
                await page.close()
        finally:
            try:
                await browser.close()
            finally:
                await pw.stop()


if __name__ == "__main__":
    from core.console import force_utf8

    force_utf8()
    if len(sys.argv) != 2 or sys.argv[1] not in ("facebook", "instagram"):
        # 提示里给 .bat 的用法：实际跑这个脚本的人多半是双击 .bat 进来的，
        # 告诉他一条他敲不出来的命令没有意义
        raise SystemExit(
            "用法：scripts\\run_backfill.bat <facebook|instagram>\n"
            "      （等价命令：.venv\\Scripts\\python.exe -m routes.backfill facebook）")
    print(f"\n新增 {asyncio.run(run(sys.argv[1]))} 篇")
