"""人工滚动回填：捕获登录会话响应并归档，覆盖范围取决于实际滚动和响应。"""
from __future__ import annotations

import argparse
import asyncio
import sys
import threading
import time
from datetime import datetime, timedelta, timezone

from core import maintenance
from core.capture import INTEREST, Collector  # noqa: F401  （INTEREST 供外部引用）
from core.capture import atomic_write_json, download_media as _download
from core.chrome import attach
from core.config import cfg
from core import integrity
from core.parse import extract, partition_by_owner
from core.store import Archive

# 无新响应达到此时长后提示收尾。
STALL_HINT_SECONDS = 20


def within_window(posts, days, now):
    """按 created_at 把本次回填收进最近 N 天；不传 days 时保持全量。

    算不出日期的一律保留：内容已经抓到手了，媒体 CDN URL 带签名有时效，
    丢掉就重抓不回来——比多留几篇贵得多。窗口外的另计，让边界可复核。
    """
    if days is None:
        return list(posts), []
    floor = (now - timedelta(days=int(days))).strftime("%Y-%m-%dT%H:%M:%SZ")
    kept, skipped = [], []
    for post in posts:
        created = (post.created_at or "").strip()
        (skipped if created and created[:10] < floor[:10] else kept).append(post)
    return kept, skipped


class ScrollProgress:
    """增量解析响应以显示人工滚动进度；不驱动页面，最终归档重新解析全部响应。"""

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


@maintenance.guarded('backfill')
async def run(platform: str, days: int | None = None) -> int:
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

        # 停止监听后等待读取完成，避免遗漏末尾响应。
        page.remove_listener("response", response_handler)
        await col.drain()

        # 解析前先保存原始响应，结构变化时仍可离线重放。
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

        # 过滤推荐内容，保留目标账号及合作帖。
        posts, rejected = partition_by_owner(posts, account)
        if rejected:
            n_rej = arc.record_rejected(rejected)
            others = {r["owner_name"] or r["owner"] for r in rejected if r["owner"]}
            print(f"  - 丢弃 {len(rejected)} 个不属于 {account} 的节点"
                  f"（来自 {len(others)} 个其它账号），已记入 _rejected.jsonl"
                  f"（新增 {n_rej} 条）")

        posts, out_of_window = within_window(posts, days, datetime.now(timezone.utc))
        if out_of_window:
            print(f"  - 窗口外跳过 {len(out_of_window)} 篇（早于最近 {days} 天），未归档")

        # 分开统计原创与合作帖，使合作归属异常可见。
        target = (account or "").strip().lower()
        n_authored = sum(1 for p in posts if p.owner == target)
        print(f"本账号帖子 {len(posts)} 篇"
              f"（原创 {n_authored} · 合作 {len(posts) - n_authored}）")

        # 合作名单同时包含归档与本次结果，覆盖首次回填。
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
    parser = argparse.ArgumentParser(
        prog="python -m routes.backfill",
        description="人工滚动回填；程序只拦截响应并归档，不驱动页面。")
    parser.add_argument("platform", choices=("facebook", "instagram"))
    parser.add_argument("--days", type=int,
                        help="只归档最近 N 天的帖子；算不出日期的仍保留。默认全量。")
    args = parser.parse_args()
    if args.days is not None and args.days < 1:
        parser.error("--days 至少为 1")
    print(f"\n新增 {asyncio.run(run(args.platform, args.days))} 篇")
