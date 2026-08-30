"""完整性检查自测。对应实施计划 D1 的【验收】。

三条验收：有缺口能检出、均匀序列不误报、media_complete=False 进待补清单。
另外补了边界与脏输入——这个模块本身是用来发现问题的，
它自己漏报（返回空）比崩掉更危险，因为崩掉至少看得见。
"""
import sys
from pathlib import Path
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))   # tests/ 在子目录，得把项目根加进来

from core.integrity import (check_continuity, check_incomplete, check_quiet,
                            check_undated, params)
from core.store import Archive, Media, Post

fails = []


def check(cond, msg):
    print(("  OK   " if cond else "  FAIL ") + msg)
    if not cond:
        fails.append(msg)


def row(pid, day, month=8):
    return {"post_id": pid, "created_at": f"2026-{month:02d}-{day:02d}T12:00:00Z"}


print("[1] 有明显缺口的时间序列 -> 能检出")
# 8/01, 8/02, 8/03 之后跳到 8/20（17 天），再 8/21
rows = [row("p1", 1), row("p2", 2), row("p3", 3), row("p4", 20), row("p5", 21)]
gaps = check_continuity(rows, gap_days=5)
check(len(gaps) == 1, f"检出 1 个缺口，实得 {len(gaps)}")
check(gaps[0]["after"] == "p3", f"缺口在 p3 之后，实得 {gaps[0]['after']}")
check(gaps[0]["before"] == "p4", f"缺口在 p4 之前，实得 {gaps[0]['before']}")
check(gaps[0]["gap_days"] == 17.0, f"缺口 17.0 天，实得 {gaps[0]['gap_days']}")

print("\n[2] 均匀序列 -> 不误报")
even = [row(f"q{i}", i) for i in range(1, 15)]
check(check_continuity(even, gap_days=5) == [], "每天一帖，无缺口")

print("\n[3] 输入乱序也能正确工作（manifest 是追加写的，顺序不保证）")
shuffled = [row("p4", 20), row("p1", 1), row("p5", 21), row("p3", 3), row("p2", 2)]
g2 = check_continuity(shuffled, gap_days=5)
check(len(g2) == 1 and g2[0]["after"] == "p3", "先排序再比较，结果与有序输入一致")

print("\n[4] 阈值边界：严格大于才算缺口")
two = [row("a", 1), row("a2", 6)]          # 正好 5 天
check(check_continuity(two, gap_days=5) == [], "间隔 == 阈值不算缺口")
check(len(check_continuity([row("a", 1), row("a2", 7)], gap_days=5)) == 1,
      "间隔 > 阈值才算")

print("\n[5] 退化输入不崩、不误报")
check(check_continuity([], gap_days=5) == [], "空列表返回空")
check(check_continuity([row("only", 1)], gap_days=5) == [], "单条返回空")

print("\n[6] created_at 不可用的记录单独汇报，不当成盲区")
dirty = [row("ok1", 1), {"post_id": "bad1", "created_at": ""},
         {"post_id": "bad2", "created_at": "not-a-date"},
         {"post_id": "bad3"}, row("ok2", 2)]
und = check_undated(dirty)
check(len(und) == 3, f"3 条无日期记录被挑出，实得 {len(und)}")
check({r["post_id"] for r in und} == {"bad1", "bad2", "bad3"}, "挑出的正是那三条")
check(check_continuity(dirty, gap_days=5) == [], "无日期记录不参与连续性比较，也不引发误报")

print("\n[7] check_quiet")
state = {"instagram": {"consecutive_quiet_days": 5},
         "facebook": {"consecutive_quiet_days": 2}}
check(check_quiet(state, "instagram", 4) is True, "5 天 >= 阈值 4 -> 告警")
check(check_quiet(state, "facebook", 4) is False, "2 天 < 阈值 4 -> 不告警")
check(check_quiet(state, "instagram", 5) is True, "正好等于阈值 -> 告警")
check(check_quiet({}, "instagram", 4) is False, "无该平台记录 -> 不告警（是没数据，不是安静）")
check(check_quiet({"instagram": None}, "instagram", 4) is False, "脏 state 不崩")
check(check_quiet({"instagram": {"consecutive_quiet_days": True}}, "instagram", 1) is False,
      "布尔值不被当成天数（True 会被 int 判断误收）")

print("\n[8] check_incomplete 接归档层")
with tempfile.TemporaryDirectory() as d:
    arc = Archive(d, "acct")
    arc.append(Post(post_id="full", platform="instagram", account="x", text="t",
                    created_at="2026-08-01T00:00:00Z",
                    media=[Media(url="u", kind="image")], media_complete=True))
    arc.append(Post(post_id="cover_only", platform="instagram", account="x", text="t",
                    created_at="2026-08-02T00:00:00Z",
                    media=[Media(url="cover", kind="image")], media_complete=False))
    inc = check_incomplete(arc)
    check(len(inc) == 1, f"1 条进待补清单，实得 {len(inc)}")
    check(inc[0]["post_id"] == "cover_only", "正是那条只有封面的轮播帖")

print("\n[9] 阈值来自 config.toml，不写死")
gap, quiet = params()
check(isinstance(gap, int) and isinstance(quiet, int), "params() 返回两个整数")
check((gap, quiet) == (5, 4), f"读到 config.toml 的 [integrity]，实得 gap={gap} quiet={quiet}")

print("\n" + ("全部通过" if not fails else f"{len(fails)} 项失败"))
sys.exit(1 if fails else 0)
