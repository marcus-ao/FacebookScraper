"""Windows 桌面通知。对应实施计划的 D2。

**这个模块的头号要求不是"能弹窗"，而是"永远不会把主流程搞崩"。**
它服务于每天自动跑的增量任务：通知本身失败（旧版 Windows、权限、
PowerShell 被策略禁掉、会话不是交互式）都属于可预期情况，
而"因为提醒你出了问题的代码出了问题，于是你连问题都不知道"是最坏结果。

降级链，从上到下，任一级成功即停：
    1. PowerShell toast   —— 正常情况下你在桌面右下角看到的那种
    2. msg 命令           —— toast 不可用时的老式弹窗
    3. state/alerts.log   —— 兜底，永远执行

第 3 级**无条件先执行**，不是"前两级都失败才写"。
落盘是唯一不依赖桌面会话的通道：计划任务在无人登录时触发、
或者你根本不在电脑前，toast 弹了也等于没弹，日志才是能回溯的那份。
"""
from __future__ import annotations

import base64
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# 通知子进程的最长等待。日常任务里挂死比通知不到更糟。
CHANNEL_TIMEOUT = 20

# 借用 PowerShell 自己已注册的 AppUserModelID。
# 不借的话得先给本项目注册一个开始菜单快捷方式，为一条通知不值得。
_PS_APP_ID = r"{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe"

_TOAST_PS = r"""
try {
  [Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType=WindowsRuntime] | Out-Null
  [Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType=WindowsRuntime] | Out-Null
  $tpl = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent(
           [Windows.UI.Notifications.ToastTemplateType]::ToastText02)
  $nodes = $tpl.GetElementsByTagName('text')
  $nodes.Item(0).AppendChild($tpl.CreateTextNode($env:FBS_TITLE))   | Out-Null
  $nodes.Item(1).AppendChild($tpl.CreateTextNode($env:FBS_MESSAGE)) | Out-Null
  $toast = [Windows.UI.Notifications.ToastNotification]::new($tpl)
  [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($env:FBS_APPID).Show($toast)
  exit 0
} catch {
  exit 1
}
"""


def _state_dir() -> Path:
    """告警日志的落点。config.toml 读不出来也要有地方写。"""
    try:
        from core.config import cfg
        return cfg().state_dir
    except Exception:
        d = ROOT / "state"
        d.mkdir(parents=True, exist_ok=True)
        return d


def _run(argv: list[str], env_extra: dict | None = None) -> bool:
    import os
    env = dict(os.environ)
    if env_extra:
        env.update(env_extra)
    try:
        proc = subprocess.run(
            argv, env=env, timeout=CHANNEL_TIMEOUT,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return proc.returncode == 0
    except Exception:
        # 找不到可执行文件、超时、权限——一律当成"这条通道不可用"
        return False


def _toast(title: str, message: str) -> bool:
    if not sys.platform.startswith("win"):
        return False
    # 用 -EncodedCommand 传脚本：base64(UTF-16LE) 彻底绕开 cmd/PowerShell 的
    # 引号转义地狱，标题正文里带引号、换行、中文都不会把命令行拆坏。
    encoded = base64.b64encode(_TOAST_PS.encode("utf-16-le")).decode("ascii")
    return _run(
        ["powershell", "-NoProfile", "-NonInteractive",
         "-ExecutionPolicy", "Bypass", "-EncodedCommand", encoded],
        {"FBS_TITLE": title, "FBS_MESSAGE": message, "FBS_APPID": _PS_APP_ID},
    )


def _msgbox(title: str, message: str) -> bool:
    """老式 msg 命令。家庭版 Windows 常常没有 msg.exe，失败是正常的。"""
    if not sys.platform.startswith("win"):
        return False
    return _run(["msg", "*", "/TIME:60", f"{title}: {message}"])


def _log(title: str, message: str, channel: str) -> None:
    """写 state/alerts.log。**这一级不允许失败得无声无息**，所以它是最后的退路。"""
    line = "[{ts}] ({ch}) {title} | {msg}\n".format(
        ts=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        ch=channel, title=title,
        msg=message.replace("\r", " ").replace("\n", " / "),
    )
    try:
        with (_state_dir() / "alerts.log").open("a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        # 连日志都写不了（磁盘满、权限）——最后打到 stdout，仍然不抛
        try:
            print("[alert] " + line.rstrip())
        except Exception:
            pass


def notify(title: str, message: str, popup: bool = True) -> None:
    """发一条告警。任何情况下都不抛异常。

    popup=False 用于自动化测试：跳过桌面弹窗，只走日志，
    免得跑一次测试往桌面糊一串通知。
    """
    channel = "log"
    try:
        if popup:
            if _toast(title, message):
                channel = "toast"
            elif _msgbox(title, message):
                channel = "msg"
            else:
                channel = "log-only"
    except Exception:
        channel = "log-only"
    _log(title, message, channel)


if __name__ == "__main__":
    # 手工验收：python -m core.notify
    from core.console import force_utf8

    force_utf8()
    notify("测试", "这是一条测试通知")
    print("已发送；记录见 %s" % (_state_dir() / "alerts.log"))
