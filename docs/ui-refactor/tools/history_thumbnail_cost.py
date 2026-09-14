"""历史列表首屏的缩略图成本：新旧两个前端各量一次。

PRE_CUTOVER_REPORT §13「风险 1」的复现脚本。回答两个问题：

  1. 历史列表首屏要为每一行发一次缩略图 GET，新 React 默认每页 50 条
     （DECISION_LOG D2），旧 Vue 是 30 条 —— 同一个接口，新 UI 首屏多付多少；
  2. 那个接口单次到底多慢，钱花在哪。

只读：跑在隔离夹具的临时归档上，不碰真实 archive / state / config。

用法：
    scripts\\run_python.bat docs/ui-refactor/tools/history_thumbnail_cost.py
"""
from __future__ import annotations

import cProfile
import json
import pstats
import sys
import time
from io import StringIO
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from playwright.sync_api import sync_playwright  # noqa: E402
from ui_fixture import ROOT, BrowserFixture, UIFixture  # noqa: E402


def main() -> int:
    report: dict = {}
    with BrowserFixture() as fx:
        from web.api import reader

        # UIFixture 会再往归档里加一批帖子，必须先建好再统计 ——
        # 否则量到的行数和浏览器真正看到的不是同一批。
        ui = UIFixture(fx, "react")

        # 单次成本：进程内直接调，避开 HTTP 与浏览器的噪音。
        history = fx.client.get("/api/tasks?scope=history&limit=50").json()
        report["history_rows_first_page"] = len(history["tasks"])
        report["history_total"] = history["pagination"]["total"]
        timings = []
        for row in history["tasks"][:6]:
            start = time.monotonic()
            found = reader.image_bytes(row["id"], 0, "de")
            timings.append({"id": row["id"], "hit": found is not None,
                            "seconds": round(time.monotonic() - start, 3)})
        report["single_call_seconds"] = timings

        profile = cProfile.Profile()
        profile.enable()
        reader.image_bytes(history["tasks"][0]["id"], 0, "de")
        profile.disable()
        stream = StringIO()
        pstats.Stats(profile, stream=stream).sort_stats("tottime").print_stats(6)
        report["profile_top"] = [line.strip() for line in stream.getvalue().splitlines()
                                 if "store.py" in line or "_getfinalpathname" in line]

        # 首屏成本：两个前端各走一次真实的历史列表。
        # ⚠️ 每个引擎一个**独立 context** —— 共用一个的话第二个前端会命中
        #    第一个留下的 HTTP 缓存，量出来的请求数直接塌成个位数。
        with sync_playwright() as play:
            browser = play.chromium.launch()
            for engine, path in [("react", "/?view=history"), ("vue", "/?view=history")]:
                ui.dist = ROOT / "web" / ("ui-next" if engine == "react" else "ui") / "dist"
                ui.requests.clear()
                context = browser.new_context(viewport={"width": 1366, "height": 768})
                page = context.new_page()
                ui.attach(page)
                start = time.monotonic()
                page.goto(fx.base_url + path, wait_until="domcontentloaded", timeout=120000)
                page.wait_for_load_state("networkidle", timeout=120000)
                report[engine] = {
                    "thumbnail_requests": len([r for r in ui.requests if "/image/" in r["path"]]),
                    "seconds_to_networkidle": round(time.monotonic() - start, 2),
                }
                context.close()
            browser.close()

    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
