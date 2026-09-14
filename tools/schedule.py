"""管理 Windows 每日及常驻计划任务；参数见 --help，安装前先预览。"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from xml.sax.saxutils import escape

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.config import cfg                # noqa: E402
from core.console import force_utf8        # noqa: E402

DAILY_TASK = "FBScraperDelta"
CATCHUP_TASK = "FBScraperDeltaCatchup"
ALIVE_TASK = "FBScraperAlive"
SCHEDULER_TASK = "FBScraperScheduler"

# 任务名使用 ASCII；中文描述写入 UTF-16 XML。
NS = "http://schemas.microsoft.com/windows/2004/02/mit/task"


def _user() -> str:
    domain = os.environ.get("USERDOMAIN") or os.environ.get("COMPUTERNAME") or ""
    name = os.environ.get("USERNAME") or ""
    return "%s\\%s" % (domain, name) if domain else name


def _settings(network: bool = True, time_limit: str = "PT2H") -> str:
    """共用任务设置；本地缺席检查不要求网络可用。"""
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
    """使用 InteractiveToken，保证 Chrome 运行在人工登录的桌面会话。"""
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
    """独立缺席告警任务：登录及每日检查，不依赖网络，也不随解锁重复触发。"""
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
    """返回任务名与 XML，供安装、查询和删除共用。"""
    root = root or Path(__file__).resolve().parent.parent
    bat = root / "scripts" / "run_pipeline.bat"
    alive_bat = bat
    at = at or str(cfg().get("delta", "daily_time", "09:30"))
    return [(DAILY_TASK, daily_xml(bat, root, at)),
            (CATCHUP_TASK, catchup_xml(bat, root)),
            (ALIVE_TASK, alive_xml(alive_bat, root))]


def _write_xml(name: str, xml: str) -> Path:
    # schtasks 导入使用带 BOM 的 UTF-16 LE。
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
    """生成常驻任务 XML；系统负责重启，Python 管理轮询时间，不能与每日调度并用。"""
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


def _task_state(name: str) -> dict:
    """Query locale-independent task XML and return registration/enabled state."""
    result = subprocess.run(["schtasks", "/Query", "/TN", name, "/XML"],
                            capture_output=True)
    if result.returncode != 0:
        return {"name": name, "registered": False, "enabled": False}
    try:
        root = ET.fromstring(result.stdout or "")
        enabled = root.findtext("./{%s}Settings/{%s}Enabled" % (NS, NS))
        if enabled is None:
            enabled = root.findtext("./Settings/Enabled")
    except ET.ParseError:
        return {"name": name, "registered": True, "enabled": None,
                "error": "task_xml_unreadable"}
    normalized = str(enabled).strip().lower() if enabled is not None else ""
    return {"name": name, "registered": True,
            "enabled": True if normalized == "true" else False if normalized == "false" else None}


def _legacy_scheduler_conflicts() -> list[dict]:
    return [state for state in (_task_state(DAILY_TASK), _task_state(CATCHUP_TASK))
            if state["registered"] and state.get("enabled") is not False]


def scheduler_install(dry_run: bool = False) -> int:
    """Install only the persistent scheduler, refusing active legacy pollers."""
    if not sys.platform.startswith("win"):
        print("[!] 常驻计划任务只在 Windows 上有意义。")
        return 1
    conflicts = _legacy_scheduler_conflicts()
    if conflicts:
        print("[!] 旧增量任务仍启用：%s。先停用旧任务，避免重复抓取。"
              % ", ".join(item["name"] for item in conflicts))
        return 2
    path = _write_xml(SCHEDULER_TASK, scheduler_xml())
    argv = ["schtasks", "/Create", "/TN", SCHEDULER_TASK, "/XML", str(path), "/F"]
    print("  %s" % " ".join(argv))
    if dry_run:
        return 0
    result = subprocess.run(argv, capture_output=True, text=True)
    print("  " + (((result.stdout or "") + (result.stderr or "")).strip() or "(no output)"))
    return int(result.returncode)


def scheduler_status() -> int:
    """Show persistent task state and fail when a legacy poller can duplicate it."""
    if not sys.platform.startswith("win"):
        print("[!] 常驻计划任务只在 Windows 上有意义。")
        return 1
    current = _task_state(SCHEDULER_TASK)
    label = ("未注册" if not current["registered"] else
             "启用" if current.get("enabled") is True else
             "停用" if current.get("enabled") is False else "状态无法解析")
    print("%s: %s" % (SCHEDULER_TASK, label))
    conflicts = _legacy_scheduler_conflicts()
    if conflicts:
        print("[!] 发现会重复抓取的旧任务：%s"
              % ", ".join(item["name"] for item in conflicts))
        return 2
    return 0


def scheduler_set_enabled(enabled: bool) -> int:
    """Stop or immediately resume the persistent scheduler and its definition."""
    if not sys.platform.startswith("win"):
        print("[!] 常驻计划任务只在 Windows 上有意义。")
        return 1
    if enabled:
        conflicts = _legacy_scheduler_conflicts()
        if conflicts:
            print("[!] 旧增量任务仍启用：%s。未恢复常驻任务，避免重复抓取。"
                  % ", ".join(item["name"] for item in conflicts))
            return 2
    flag = "/ENABLE" if enabled else "/DISABLE"
    result = subprocess.run(["schtasks", "/Change", "/TN", SCHEDULER_TASK, flag],
                            capture_output=True, text=True)
    print("  " + (((result.stdout or "") + (result.stderr or "")).strip() or "(no output)"))
    if result.returncode:
        return int(result.returncode)
    action = "/Run" if enabled else "/End"
    result = subprocess.run(["schtasks", action, "/TN", SCHEDULER_TASK],
                            capture_output=True, text=True)
    print("  " + (((result.stdout or "") + (result.stderr or "")).strip() or "(no output)"))
    return int(result.returncode)


def main(argv=None) -> int:
    force_utf8()
    p = argparse.ArgumentParser(prog="python -m tools.schedule",
                                description="每日增量的 Windows 计划任务")
    p.add_argument("action", choices=("xml", "scheduler-xml", "install", "status", "remove",
                                      "scheduler-install", "scheduler-status",
                                      "scheduler-disable", "scheduler-enable"))
    p.add_argument("--dry-run", action="store_true", help="install 时只打印命令")
    args = p.parse_args(argv)
    if args.action == "scheduler-xml":
        print(scheduler_xml())
        return 0
    if args.action == "scheduler-install":
        return scheduler_install(args.dry_run)
    if args.action == "scheduler-status":
        return scheduler_status()
    if args.action == "scheduler-disable":
        return scheduler_set_enabled(False)
    if args.action == "scheduler-enable":
        return scheduler_set_enabled(True)

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
