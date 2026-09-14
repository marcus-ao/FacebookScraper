"""Windows 告警：先落日志，再尝试 toast/msg；通知失败不阻断主流程。"""
from __future__ import annotations

import base64
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# 通知子进程的最长等待。日常任务里挂死比通知不到更糟。
CHANNEL_TIMEOUT = 20

# 复用 PowerShell 已注册的 AppUserModelID。
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
        # 延迟导入：配置损坏时仍须记录告警。
        from core.config import cfg
        return cfg().state_dir
    except (Exception, SystemExit):
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
    # EncodedCommand 避免标题和正文参与 shell 解析。
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
    """发送告警且不抛异常；popup=False 仅写日志。"""
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
    from core.console import force_utf8

    force_utf8()
    notify("测试", "这是一条测试通知")
    print("已发送；记录见 %s" % (_state_dir() / "alerts.log"))
