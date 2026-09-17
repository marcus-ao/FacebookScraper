"""每日兜底深扫；复用 delta 的身份校验、抓取、落盘与失败预算。"""
from __future__ import annotations
from core import maintenance

import random
from dataclasses import replace

from routes import delta


@maintenance.guarded('reconcile')
def main(argv=None, *, rng=None) -> int:
    rng = rng or random.SystemRandom()
    config = delta.DeltaConfig.load()
    schedule = config.schedule
    depth = rng.randint(max(0, schedule.reconcile_scrolls - 1), schedule.reconcile_scrolls + 1)
    config = replace(config, max_scrolls=depth, run_kind="reconcile",
                     max_session_seconds=schedule.reconcile_max_session_seconds)
    # 时刻已由常驻调度器随机化；不要再额外延迟而越过早班处理截止线。
    args = list(argv or [])
    if "--no-jitter" not in args:
        args.append("--no-jitter")
    print(f"兜底对账：本轮随机滚动 {depth} 屏；仍使用 detect 专用会话")
    return delta.main(args, config=config)


if __name__ == "__main__":
    import sys
    raise SystemExit(main(sys.argv[1:]))
