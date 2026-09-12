r"""注册 / 查看 / 删除每日增量的 Windows 计划任务。对应实施计划的 E3。

    python -m tools.schedule xml       只打印将要注册的 XML，什么都不改
    python -m tools.schedule install   注册（--dry-run 只看命令）
    python -m tools.schedule status    查询已注册的任务
    python -m tools.schedule remove    删除

**为什么是三个任务而不是一个**：Task Scheduler 的一个任务只能有一个 Action，
而这些触发器需要三种参数：

  FBScraperDelta         每天固定时刻 → run_pipeline.bat run
  FBScraperDeltaCatchup  登录时 / 解锁时 → run_pipeline.bat run --if-stale
  FBScraperAlive         登录时 / 每天 20:00 → run_pipeline.bat check-alive

⚠️ **每天那个不能带 `--if-stale`。** `stale_after_hours = 26`，而每天同一时刻
的间隔是 24 小时——带上就会"跑一天、跳一天"。补跑触发器才需要它去重。

⚠️ **`FBScraperAlive` 是死人开关（L0c），它必须独立于上面两个。**
它要抓的失效正是「上面两个不跑了而没人知道」——挂进它们里面就会跟着一起哑掉。
它只读 `state/` 下的运行标记：**不联网、不抓取、不花钱**，
所以也**不设** `RunOnlyIfNetworkAvailable`（没网不能成为警报不响的理由）。

**为什么用 XML 而不是拼 schtasks 参数**：`/SC ONLOGON` 有，但"解锁时触发"
（SessionStateChangeTrigger）只能通过 XML 表达。计划里也写明允许走 XML 导入。

⚠️ **注册之后每天就会真的调用一次流水线。** `manual` 模式只对账；
`assisted` 会访问 Facebook / Instagram 并按预算执行增量、翻译与调图，
但 `pipeline run` 仍不会接触发布浏览器。方案 B 的累积敞口从激活并切到
`assisted` 后开始计。装之前先用 `pipeline status` 核对当前状态，否则装上去的
可能是一个每天准时失败的任务。
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path
from xml.sax.saxutils import escape

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.config import cfg                # noqa: E402
from core.console import force_utf8        # noqa: E402

DAILY_TASK = "FBScraperDelta"
CATCHUP_TASK = "FBScraperDeltaCatchup"
ALIVE_TASK = "FBScraperAlive"
SCHEDULER_TASK = "FBScraperScheduler"

# 任务名保持纯 ASCII：A1 的结论是 cmd 处理非 ASCII 不可靠，
# 而 schtasks 的任务名会经过命令行。描述走 XML（UTF-16），中文没问题。
NS = "http://schemas.microsoft.com/windows/2004/02/mit/task"


def _user() -> str:
    domain = os.environ.get("USERDOMAIN") or os.environ.get("COMPUTERNAME") or ""
    name = os.environ.get("USERNAME") or ""
    return "%s\\%s" % (domain, name) if domain else name


def _settings(network: bool = True, time_limit: str = "PT2H") -> str:
    """任务共用的 Settings 段。每一项都有理由，不是抄来的模板。

    ⚠️ ``network=False`` 只给**死人开关**用，理由在 :func:`alive_xml`：
    那个检查一个字节都不联网，而**"没网"绝不能成为警报不响的原因**。
    """
    return """  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <!-- 笔记本常在电池上跑。勾着"仅交流电"等于这个任务大半时间不工作。 -->
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <!-- 合盖错过了就在醒来后补跑。这正是"笔记本会睡"要解决的问题。 -->
    <StartWhenAvailable>true</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>%s</RunOnlyIfNetworkAvailable>
    <IdleSettings>
      <StopOnIdleEnd>false</StopOnIdleEnd>
      <RestartOnIdle>false</RestartOnIdle>
    </IdleSettings>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <Enabled>true</Enabled>
    <Hidden>false</Hidden>
    <RunOnlyIfIdle>false</RunOnlyIfIdle>
    <!-- 绝不把机器叫醒来抓取：半夜自己醒一下既没必要，也是个显眼的行为特征。 -->
    <WakeToRun>false</WakeToRun>
    <!-- 随机延迟最多 45 分钟 + 抓取本身，2 小时足够；卡住了也不会一直挂着。 -->
    <ExecutionTimeLimit>%s</ExecutionTimeLimit>
    <Priority>7</Priority>
  </Settings>""" % ("true" if network else "false", time_limit)


def _principal() -> str:
    """⚠️ InteractiveToken = "只在用户登录时运行"。

    **不要改成 Password / S4U（"不管用户是否登录都运行"）**：那样拉起的
    Chrome 没有桌面会话，CDP 附着行为不确定。这条路径本来就依赖一个真实的、
    有人在用的浏览器。
    """
    return """  <Principals>
    <Principal id="Author">
      <UserId>%s</UserId>
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>""" % _user()


def _actions(bat: Path, root: Path, arguments: str) -> str:
    args = ("\n      <Arguments>%s</Arguments>" % arguments) if arguments else ""
    return """  <Actions Context="Author">
    <Exec>
      <Command>%s</Command>%s
      <WorkingDirectory>%s</WorkingDirectory>
    </Exec>
  </Actions>""" % (bat, args, root)


def daily_xml(bat: Path, root: Path, at: str) -> str:
    """每天固定时刻调用完整 pipeline run，不带 --if-stale。"""
    return """<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="%s">
  <RegistrationInfo>
    <Description>每日增量抓取（登录态 + CDP 附着）。程序内部还会再随机延迟 0-45 分钟，所以实际发起时间不是这个整点。</Description>
    <URI>\\%s</URI>
  </RegistrationInfo>
  <Triggers>
    <CalendarTrigger>
      <StartBoundary>2026-01-01T%s:00</StartBoundary>
      <Enabled>true</Enabled>
      <ScheduleByDay>
        <DaysInterval>1</DaysInterval>
      </ScheduleByDay>
    </CalendarTrigger>
  </Triggers>
%s
%s
%s
</Task>
""" % (NS, DAILY_TASK, at, _principal(), _settings(),
       _actions(bat, root, "run"))


def catchup_xml(bat: Path, root: Path) -> str:
    """登录时 + 解锁时。**必须带 --if-stale**，否则一天会跑很多次。"""
    return """<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="%s">
  <RegistrationInfo>
    <Description>增量补跑：登录或解锁时检查一次，距上次成功够久才真的抓。合盖睡眠错过每日触发时靠它兜底。</Description>
    <URI>\\%s</URI>
  </RegistrationInfo>
  <Triggers>
    <LogonTrigger>
      <Enabled>true</Enabled>
      <UserId>%s</UserId>
      <Delay>PT3M</Delay>
    </LogonTrigger>
    <SessionStateChangeTrigger>
      <Enabled>true</Enabled>
      <UserId>%s</UserId>
      <Delay>PT2M</Delay>
      <StateChange>SessionUnlock</StateChange>
    </SessionStateChangeTrigger>
  </Triggers>
%s
%s
%s
</Task>
""" % (NS, CATCHUP_TASK, _user(), _user(), _principal(), _settings(),
       _actions(bat, root, "run --if-stale"))


def alive_xml(bat: Path, root: Path) -> str:
    r"""死人开关（L0c）。登录时 + 每天 20:00 各查一次，**只读、不联网**。

    ⚠️ **为什么必须是一个独立任务，而不是挂进上面两个**：
    `PIPELINE_PLAN` 第 7 节要抓的失效是「整条流水线停了而没人知道」——
    而计划任务被禁用/删除正是最常见的停法。**挂在增量任务里的检查，
    会跟着增量任务一起哑掉**，恰恰在最需要它的时候不响。
    独立任务意味着要**两个**东西同时失效才会静默，而不是一个。

    ⚠️ **`RunOnlyIfNetworkAvailable` 必须是 false。**
    `pipeline check-alive` 一个字节都不联网（它只读 `state/` 下的运行标记），
    而"家里网断了"绝不该成为"流水线死了但没告警"的原因。

    ⚠️ **不加 SessionUnlock 触发器**（上面的补跑任务有）。
    这个检查一天最多只会得出一个新结论，锁屏解锁一次就弹一次是纯噪音——
    `core/integrity.py` 已经立过规矩：**误报的代价不是打扰，
    是让整条告警通道失效**，用户关掉通知之后真故障就再没人知道了。
    """
    return """<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="%s">
  <RegistrationInfo>
    <Description>死人开关：检查流水线是不是还在跑。只读 state/ 下的运行标记，不联网、不抓取、不花钱。超过 [pipeline].dead_man_days 天没有成功运行就告警。</Description>
    <URI>\\%s</URI>
  </RegistrationInfo>
  <Triggers>
    <LogonTrigger>
      <Enabled>true</Enabled>
      <UserId>%s</UserId>
      <Delay>PT5M</Delay>
    </LogonTrigger>
    <CalendarTrigger>
      <StartBoundary>2026-01-01T20:00:00</StartBoundary>
      <Enabled>true</Enabled>
      <ScheduleByDay>
        <DaysInterval>1</DaysInterval>
      </ScheduleByDay>
    </CalendarTrigger>
  </Triggers>
%s
%s
%s
</Task>
""" % (NS, ALIVE_TASK, _user(), _principal(),
       _settings(network=False, time_limit="PT10M"),
       _actions(bat, root, "check-alive"))


def plan(root: Path | None = None, at: str | None = None) -> list[tuple[str, str]]:
    """返回 [(任务名, XML)]。抽成纯函数是为了能离线断言。

    `status` / `remove` 都遍历这个列表，所以在这里加一项就等于处处都加上了。
    """
    root = root or Path(__file__).resolve().parent.parent
    bat = root / "scripts" / "run_pipeline.bat"
    alive_bat = bat
    at = at or str(cfg().get("delta", "daily_time", "09:30"))
    return [(DAILY_TASK, daily_xml(bat, root, at)),
            (CATCHUP_TASK, catchup_xml(bat, root)),
            (ALIVE_TASK, alive_xml(alive_bat, root))]


def _write_xml(name: str, xml: str) -> Path:
    # schtasks 要求 XML 是 Unicode：UTF-16 LE + BOM 是它最稳的一种。
    # 写进 state/（已 gitignore），不污染仓库。
    path = cfg().state_dir / ("task_%s.xml" % name)
    path.write_bytes(b"\xff\xfe" + xml.encode("utf-16-le"))
    return path


def install(dry_run: bool = False) -> int:
    if not sys.platform.startswith("win"):
        print("[!] 计划任务只在 Windows 上有意义。")
        return 1
    rc = 0
    for name, xml in plan():
        path = _write_xml(name, xml)
        argv = ["schtasks", "/Create", "/TN", name, "/XML", str(path), "/F"]
        print("  %s" % " ".join(argv))
        if dry_run:
            continue
        result = subprocess.run(argv, capture_output=True, text=True)
        out = (result.stdout or "").strip() or (result.stderr or "").strip()
        print("    %s" % out)
        rc = rc or result.returncode
    if dry_run:
        print("\n（--dry-run：只打印了命令，什么都没注册。XML 已写到 state\\）")
        return 0
    if rc == 0:
        print("\n[ok] 三个任务都注册好了。验证：")
        print("     python -m tools.schedule status")
        print("     schtasks /Run /TN %s" % DAILY_TASK)
        print("\n⚠️ 从现在开始，每天会真的去访问一次 Facebook 和 Instagram。")
        print("   想停：python -m tools.schedule remove")
        print("\n%s 是死人开关：只读、不联网、不花钱。" % ALIVE_TASK)
        print("   在第一次增量成功之前，它会告诉你「流水线从未成功运行」——")
        print("   **那是正常的，而且正好证明这条告警通道是通的**。")
        print("   想现在就看它说什么：python pipeline.py check-alive")
    else:
        print("\n[!] 注册失败。若提示拒绝访问，用管理员身份开命令提示符再跑一次。")
    return rc


def status() -> int:
    for name, _ in plan():
        print("\n=== %s ===" % name)
        result = subprocess.run(
            ["schtasks", "/Query", "/TN", name, "/V", "/FO", "LIST"],
            capture_output=True, text=True)
        text = (result.stdout or "") + (result.stderr or "")
        if result.returncode != 0:
            print("  未注册（%s）" % text.strip().splitlines()[-1:] or "")
            continue
        # /V /FO LIST 会打几十行，挑出人真正要看的那几行
        wanted = ("状态", "Status", "下次运行时间", "Next Run Time",
                  "上次运行时间", "Last Run Time", "上次结果", "Last Result")
        for line in text.splitlines():
            if any(line.strip().startswith(w) for w in wanted):
                print("  " + line.strip())
    print("\n增量自己的状态（不联网）：python -m routes.delta --status")
    return 0


def remove() -> int:
    rc = 0
    for name, _ in plan():
        result = subprocess.run(["schtasks", "/Delete", "/TN", name, "/F"],
                                capture_output=True, text=True)
        print("  %s: %s" % (name, ((result.stdout or "") + (result.stderr or "")).strip()))
        rc = rc or result.returncode
    return rc


def scheduler_xml(root: Path | None = None) -> str:
    """常驻调度器模板：系统只负责启动和崩溃恢复，时间窗口由 Python 管理。

    这里只生成文本。旧每日任务迁移需要人工停用，不能让两个调度源同时提频。
    InteractiveToken 保证 Chrome 使用人工登录的桌面会话。
    """
    root = root or Path(__file__).resolve().parent.parent
    settings = _settings(network=False, time_limit="PT0S").replace(
        "  </Settings>",
        "    <RestartOnFailure><Interval>PT1M</Interval><Count>999</Count></RestartOnFailure>\n  </Settings>")
    return '''<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="%s">
  <RegistrationInfo><Description>上海窗口监测常驻进程；只抓取，模型处理需另行显式启用。</Description><URI>\\%s</URI></RegistrationInfo>
  <Triggers>
    <BootTrigger><Enabled>true</Enabled><Delay>PT2M</Delay></BootTrigger>
    <LogonTrigger><Enabled>true</Enabled><UserId>%s</UserId><Delay>PT2M</Delay></LogonTrigger>
  </Triggers>
%s
%s
  <Actions Context="Author"><Exec>
    <Command>%s</Command><Arguments>--run</Arguments>
    <WorkingDirectory>%s</WorkingDirectory>
  </Exec></Actions>
</Task>
''' % (NS, SCHEDULER_TASK, escape(_user()), _principal(), settings,
       escape(str(root / "scripts" / "run_scheduler.bat")), escape(str(root)))


def main(argv=None) -> int:
    force_utf8()
    p = argparse.ArgumentParser(prog="python -m tools.schedule",
                                description="每日增量的 Windows 计划任务")
    p.add_argument("action", choices=("xml", "scheduler-xml", "install", "status", "remove"))
    p.add_argument("--dry-run", action="store_true", help="install 时只打印命令")
    args = p.parse_args(argv)
    if args.action == "scheduler-xml":
        print(scheduler_xml())
        return 0

    if args.action == "xml":
        for name, xml in plan():
            print("=" * 60)
            print("# %s" % name)
            print("=" * 60)
            print(xml)
        return 0
    if args.action == "install":
        return install(args.dry_run)
    if args.action == "status":
        return status()
    return remove()


if __name__ == "__main__":
    raise SystemExit(main())
