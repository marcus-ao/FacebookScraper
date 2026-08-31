"""完整性检查自测。对应实施计划 D1 的【验收】。

三条验收：有缺口能检出、均匀序列不误报、media_complete=False 进待补清单。
另外补了边界与脏输入——这个模块本身是用来发现问题的，
它自己漏报（返回空）比崩掉更危险，因为崩掉至少看得见。
"""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))   # tests/ 在子目录，得把项目根加进来
from core.console import force_utf8   # noqa: E402

force_utf8()   # 输出被重定向到文件/管道时，cp936 编不出 ß/⚠ 会让整套测试崩掉

from core.integrity import (check_continuity, check_dropped_partners,
                            check_incomplete, check_quiet, check_undated,
                            known_partners, params, run_checks)
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

print("\n[9] 阈值来自 config.toml，且按平台分开（D3）")
gap, quiet = params()
check(isinstance(gap, int) and isinstance(quiet, int), "params() 返回两个整数")
fb_gap, fb_quiet = params("facebook")
ig_gap, ig_quiet = params("instagram")
check(isinstance(fb_quiet, int) and isinstance(ig_quiet, int),
      f"两个平台各自取到值（FB {fb_quiet} / IG {ig_quiet}）")
check(4 <= fb_quiet <= 14 and 4 <= ig_quiet <= 14,
      "零新增阈值在合理区间：太紧会天天误报，太松则真坏了要几周才报出来"
      f"（实得 FB {fb_quiet} / IG {ig_quiet}；两个账号近一年间隔中位都是 1.0 天）")
check(ig_gap >= fb_gap,
      f"IG 的缺口阈值不比 FB 紧（{ig_gap} >= {fb_gap}）—— 它的 p95 间隔更大")


print("\n[10] run_checks：只报**新出现**的问题（D3 的核心）")

NOW = datetime(2026, 8, 30, 12, 0, 0, tzinfo=timezone.utc)


def rows_every(n_days, count, start="2026-07-01"):
    base = datetime.fromisoformat(start + "T00:00:00+00:00")
    return [{"post_id": "p%d" % i,
             "created_at": (base + timedelta(days=i * n_days)).strftime(
                 "%Y-%m-%dT%H:%M:%SZ")}
            for i in range(count)]


def run(rows, entry, inc=(), platform="instagram", now=NOW, **kw):
    kw.setdefault("gap_days", 5)
    kw.setdefault("alert_after", 21)
    return run_checks(rows, list(inc), entry, platform, now=now, **kw)


entry = {"consecutive_quiet_days": 25}
hits = run([], entry)
check([h["kind"] for h in hits] == ["quiet"], "零新增超阈值 -> 告警")
check("25 天" in hits[0]["message"] and "21 天" in hits[0]["message"]
      and "instagram" in hits[0]["message"],
      "文案含平台、实际天数、阈值（反例：『发现问题』）")
check(run([], entry) == [], "同一个问题第二天不再重复报（否则用户会关掉通知）")
check(run([], entry, now=NOW + timedelta(days=8)),
      "隔了 8 天（> ALERT_REPEAT_DAYS）才允许再报一次")

entry = {"consecutive_quiet_days": 20}
check(run([], entry) == [], "没到阈值不报")
check(run([], {"consecutive_quiet_days": 25}, platform="facebook",
          alert_after=7), "FB 用自己的阈值（7 天）")

# 连续性：只看窗口内，且同一个缺口只报一次
gapped = [{"post_id": "a", "created_at": "2026-08-01T00:00:00Z"},
          {"post_id": "b", "created_at": "2026-08-20T00:00:00Z"}]
entry = {}
hits = run(gapped, entry)
check([h["kind"] for h in hits] == ["gap"], "窗口内的新缺口 -> 告警")
check("19.0 天" in hits[0]["message"] and "a" in hits[0]["message"],
      "文案含缺口天数与两端的 post_id")
check(run(gapped, entry) == [], "同一个缺口不再重复报")
check(entry["alerts"]["gaps"] == ["a->b"], "报过的缺口记在 state 里")

old = [{"post_id": "x", "created_at": "2021-01-01T00:00:00Z"},
       {"post_id": "y", "created_at": "2021-03-01T00:00:00Z"}]
check(run(old, {}) == [],
      "几年前的缺口不报 —— 现在也补不回来，天天重报只会淹掉今天的问题")

# 跨越窗口边界的缺口：起点在窗口外、终点在窗口内。
# 旧写法先剔帖子再算间隔，这种缺口会**整个消失**——而"一段历史根本没抓到"
# 这类失败的起点必然更早，越是大洞越容易被剔掉。
# 2026-08-31 实测撞上：IG 2026-07-01 -> 07-09 的 8.4 天缺口一次都没报过。
straddle = [{"post_id": "s1", "created_at": "2026-06-25T00:00:00Z"},   # 窗口外
            {"post_id": "s2", "created_at": "2026-07-20T00:00:00Z"}]   # 窗口内
hits = run(straddle, {})
check([h["kind"] for h in hits] == ["gap"],
      "跨窗口边界的缺口要报 —— 起点在窗口外不等于这段历史没问题")
check("s1" in hits[0]["message"] and "s2" in hits[0]["message"],
      "两端 post_id 都给出来，人能直接去归档里对")

# 但"终点也在窗口外"的仍然不报：那才是真正的陈年旧账
both_out = [{"post_id": "o1", "created_at": "2026-01-01T00:00:00Z"},
            {"post_id": "o2", "created_at": "2026-03-01T00:00:00Z"}]
check(run(both_out, {}) == [],
      "缺口两端都在窗口外 -> 仍然不报，避免每天重播陈年缺口")

check(run(rows_every(1, 20, "2026-08-10"), {}) == [], "节奏正常时不误报")

# 媒体不全 / 无日期：只在变多时报
entry = {}
check([h["kind"] for h in run([], entry, inc=[{"post_id": "1"}])] == ["incomplete"],
      "媒体不全从 0 涨到 1 -> 告警")
check(run([], entry, inc=[{"post_id": "1"}]) == [], "数量没变不重复报")
check([h["kind"] for h in run([], entry, inc=[{"post_id": "1"}, {"post_id": "2"}])]
      == ["incomplete"], "数量继续增加 -> 再报")
check(run([], entry, inc=[]) == [], "数量下降时静默更新，不打扰")
check(entry["alerts"]["incomplete"] == 0, "但记号跟着降下来了")

entry = {}
undated = [{"post_id": "u1", "created_at": ""}, {"post_id": "u2"}]
check([h["kind"] for h in run(undated, entry)] == ["undated"],
      "无日期记录变多 -> 告警（它们是连续性检查的盲区）")

entry = {"consecutive_quiet_days": 25}
hits = run(gapped, entry, inc=[{"post_id": "1"}])
check(len(hits) == 3 and {h["kind"] for h in hits} == {"quiet", "gap", "incomplete"},
      "多项同时命中时一起返回（调用方合成一条通知，不是弹三次）")


# ==========================================================================
print("\n[第四项] 丢弃了已知合作方的帖子 —— 归属判定漏判的哨兵（CR-19 那一类）")

# 根因（coauthor_producers 没被看）已经修了，**但让它一直没被发现的那个原因
# 没修**：丢弃是完全静默的。这一项盯的是下一次——合作机制会变。
archive_rows = [
    {"post_id": "1", "owner": "acme.us", "coauthors": []},              # 自己发的
    {"post_id": "2", "owner": "acme.us", "coauthors": ["partner.one"]},  # 自己发、别人合作
    {"post_id": "3", "owner": "Partner.Two", "coauthors": ["acme.us"]},  # 别人发、自己合作
]
partners = known_partners(archive_rows, "acme.us")
check(partners == {"partner.one", "partner.two"},
      "合作方名单同时取自『合作帖的 owner』和『自家帖的 coauthors』，且归一化小写")
check("acme.us" not in partners, "本账号自己不算合作方")
check(known_partners(archive_rows, "ACME.US") == partners,
      "目标账号大小写不敏感 —— 否则本账号会把自己算成合作方")
check(known_partners([{"post_id": "x"}], "acme.us") == set(),
      "没有归属信息的行不会污染名单")
check(known_partners([{"post_id": "x", "coauthors": [None, 5, ""]}], "acme.us") == set(),
      "coauthors 里的脏值跳过，不崩 —— 这个模块自己崩掉比漏报还糟")

dropped_partner = {"post_id": "9", "owner": "partner.two",
                   "reason": "owner_mismatch"}
dropped_stranger = {"post_id": "8", "owner": "chicagofire",
                    "reason": "owner_mismatch"}
hit = check_dropped_partners([dropped_partner, dropped_stranger], partners)
check([h["post_id"] for h in hit] == ["9"],
      "只挑出作者是已知合作方的那些；陌生账号的推荐位不报")
check(check_dropped_partners([dropped_stranger], partners) == [],
      "全是陌生账号 → 不报（真实数据里丢弃的 6 个账号与 210 个合作方零交集）")
check(check_dropped_partners([dropped_partner], set()) == [],
      "名单为空（新账号、还没抓过）时不报，而不是把所有丢弃都当可疑")
check(check_dropped_partners([{"post_id": "7", "owner": None}], partners) == [],
      "归属未知的丢弃不算这一项 —— 它由 owner_unknown 那条路径管")
check(check_dropped_partners([{"post_id": "6", "owner": "PARTNER.ONE"}],
                             partners)[0]["post_id"] == "6",
      "丢弃记录里的 owner 大小写不影响命中")

print("\n" + ("全部通过" if not fails else f"{len(fails)} 项失败"))
sys.exit(1 if fails else 0)
