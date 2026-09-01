"""计划任务与入口脚本自测。对应实施计划 E1 / E3 的【验收】里能离线做的部分。

这套测试盯两件事，都是"错了要几天后才发现"的那类：

  1. **两个任务的参数不能搞反。** 每日触发器显式带 `--platform all`、
     但**不带** `--if-stale`
     （stale_after_hours = 26 > 24，带上就变成跑一天跳一天），
     补跑触发器**必须带**（否则每次解锁都抓一遍）。
  2. **`.bat` 必须纯 ASCII + CRLF。** 计划里写着"`.gitattributes` 只保证换行，
     ASCII 得靠人守"——靠人守的东西迟早会破，所以在这里守。
     实测后果不是乱码而是**行被从中间劈开、后半段当命令执行**。
"""
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from core.console import force_utf8   # noqa: E402

force_utf8()   # 输出被重定向到文件/管道时，cp936 编不出 ß/⚠ 会让整套测试崩掉

from core.config import cfg                                        # noqa: E402
from tools.schedule import (ALIVE_TASK, CATCHUP_TASK, DAILY_TASK,  # noqa: E402
                            NS, plan)

fails = []


def check(cond, msg):
    print(("  OK   " if cond else "  FAIL ") + msg)
    if not cond:
        fails.append(msg)


def q(tag):
    return "{%s}%s" % (NS, tag)


def find(root, *path):
    node = root
    for tag in path:
        node = node.find(q(tag)) if node is not None else None
    return node


def text(root, *path):
    node = find(root, *path)
    return node.text if node is not None else None


print("[1] 生成的 XML 本身要是合法的、且是那三个任务")

tasks = dict(plan())
check(sorted(tasks) == sorted([DAILY_TASK, CATCHUP_TASK, ALIVE_TASK]),
      "生成三个任务：%s / %s / %s" % (DAILY_TASK, CATCHUP_TASK, ALIVE_TASK))
check(all(name.isascii() for name in tasks),
      "任务名纯 ASCII —— 它要经过 schtasks 的命令行")

trees = {}
for name, xml in tasks.items():
    try:
        trees[name] = ET.fromstring(xml)
    except ET.ParseError as e:
        check(False, "%s 的 XML 解析失败：%s" % (name, e))
check(len(trees) == 3, "三份 XML 都能被 XML 解析器接受")


print("\n[2] 参数不能搞反（搞反了要几天后才看出来）")

daily, catchup = trees[DAILY_TASK], trees[CATCHUP_TASK]
daily_args = text(daily, "Actions", "Exec", "Arguments")
catchup_args = text(catchup, "Actions", "Exec", "Arguments")
check(daily_args == "run",
      "每日任务调用 pipeline run，由 autonomy 决定 manual/assisted 行为")
check("--if-stale" not in daily_args,
      "每日任务**不带 --if-stale** —— stale 阈值 26 小时 > 24，"
      "带上会变成跑一天、跳一天")
check(catchup_args == "run --if-stale",
      "补跑任务调用 pipeline run --if-stale —— 否则每次解锁都要真抓一遍")


print("\n[3] 三个触发器（每天 / 登录时 / 解锁时）")

cal = find(daily, "Triggers", "CalendarTrigger")
check(cal is not None and find(cal, "ScheduleByDay") is not None,
      "每日任务有按天的日历触发器")
start = text(cal, "StartBoundary") if cal is not None else ""
want = str(cfg().get("delta", "daily_time", "09:30"))
check(start and start.endswith("T%s:00" % want),
      "触发时刻取自 config.toml 的 [delta].daily_time（实得 %s，配置 %s）"
      % (start, want))

trig = find(catchup, "Triggers")
kinds = [child.tag.split("}")[-1] for child in trig] if trig is not None else []
check("LogonTrigger" in kinds, "补跑任务有「用户登录时」触发器")
check("SessionStateChangeTrigger" in kinds, "补跑任务有「会话状态变化」触发器")
sess = find(catchup, "Triggers", "SessionStateChangeTrigger")
check(text(sess, "StateChange") == "SessionUnlock" if sess is not None else False,
      "那个会话触发器是**解锁**（唤醒后解锁即补跑；schtasks 命令行做不出这个）")


print("\n[4] 笔记本相关的设置（错一个，任务就大半时间不工作）")

for name, tree in trees.items():
    check(text(tree, "Settings", "DisallowStartIfOnBatteries") == "false",
          "%s：不勾「仅在使用交流电时运行」（笔记本常在电池上）" % name)
    check(text(tree, "Settings", "StopIfGoingOnBatteries") == "false",
          "%s：切到电池时不中断" % name)
    check(text(tree, "Settings", "StartWhenAvailable") == "true",
          "%s：错过的触发在醒来后补跑" % name)
    check(text(tree, "Settings", "WakeToRun") == "false",
          "%s：**不把机器叫醒**来抓取" % name)
    check(text(tree, "Principals", "Principal", "LogonType") == "InteractiveToken",
          "%s：只在用户登录时运行 —— 不能是 Password/S4U，"
          "那样拉起的 Chrome 没有桌面会话，CDP 附着行为不确定" % name)
    check(text(tree, "Settings", "MultipleInstancesPolicy") == "IgnoreNew",
          "%s：上一次还没跑完时不并发第二个" % name)
    limit = text(tree, "Settings", "ExecutionTimeLimit")
    check(limit and limit.startswith("PT"),
          "%s：有执行时间上限（%s），卡住不会永远挂着" % (name, limit))


print("\n[5] 指向的入口脚本得真的存在")

bat = ROOT / "scripts" / "run_delta.bat"
check(bat.exists(), "scripts\\run_delta.bat 存在（E1）")
check((ROOT / "scripts" / "run_pipeline.bat").exists(),
      "scripts\\run_pipeline.bat 存在（L0b/L0c）")
# 三个稳定任务名都经 run_pipeline.bat；前两个动作分别是 run / run --if-stale。
# ⚠️ 这不是随手分的：死人开关要抓的失效正是「增量任务不跑了」，
#    与增量共用入口就会跟着一起哑掉。见 tools/schedule.py::alive_xml。
expected_bat = {DAILY_TASK: "run_pipeline.bat", CATCHUP_TASK: "run_pipeline.bat",
                ALIVE_TASK: "run_pipeline.bat"}
for name, tree in trees.items():
    cmd = text(tree, "Actions", "Exec", "Command")
    check(cmd and Path(cmd).name == expected_bat[name],
          "%s 指向 %s" % (name, expected_bat[name]))
    check(text(tree, "Actions", "Exec", "WorkingDirectory") == str(ROOT),
          "%s 的工作目录是项目根（.bat 里也会 cd，双保险）" % name)
check(text(trees[ALIVE_TASK], "Actions", "Exec", "Arguments") == "check-alive",
      "死人开关传的是 check-alive —— 传错子命令会变成一个不告警的哑任务")


print("\n[6] 所有 .bat 的字节级约定（靠人守迟早会破，所以在这里守）")

for path in sorted((ROOT / "scripts").glob("*.bat")):
    raw = path.read_bytes()
    bad = [b for b in raw if b > 127]
    check(not bad,
          "%s 纯 ASCII（含中文的行会被 cmd 从中间劈开、后半段当命令执行）"
          % path.name)
    check(raw.replace(b"\r\n", b"").count(b"\n") == 0,
          "%s 没有裸 LF（LF 换行会让 cmd 误解析整行）" % path.name)
    check(not raw.startswith(b"\xef\xbb\xbf"),
          "%s 没有 BOM（cmd 不认 BOM，会把 @echo off 一起打坏）" % path.name)

delta_bat = (ROOT / "scripts" / "run_delta.bat").read_bytes().decode("ascii")
check("PYTHONIOENCODING=utf-8" in delta_bat,
      "run_delta.bat 设了 PYTHONIOENCODING —— 输出重定向进 delta.log 时，"
      "本机 cp936 编不出 ⚠/❗，第一次打印就会把整个进程带走")
check(">> \"state\\delta.log\"" in delta_bat,
      "输出是**追加**（>>）而不是覆盖（>）：上一次的输出往往是排查今天问题的唯一线索")
check("-m routes.delta %*" in delta_bat,
      "参数原样透传，所以 --if-stale / --status / --reset-failures 都能用")

print("\n" + ("全部通过" if not fails else "%d 项失败" % len(fails)))
raise SystemExit(1 if fails else 0)
