"""验证通知失败降级及日志留存，不测试真实桌面弹窗。"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))   # tests/ 在子目录，得把项目根加进来
from core.console import force_utf8   # noqa: E402

force_utf8()   # 输出被重定向到文件/管道时，cp936 编不出 ß/⚠ 会让整套测试崩掉

import core.notify as N
import core.config as C

fails = []


def check(cond, msg):
    print(("  OK   " if cond else "  FAIL ") + msg)
    if not cond:
        fails.append(msg)


def read_log(d):
    p = Path(d) / "alerts.log"
    return p.read_text(encoding="utf-8") if p.exists() else ""


with tempfile.TemporaryDirectory() as d:
    print("[0] config.toml 致命错误也会退回默认 state 目录")
    orig_cfg, orig_root, real_state_dir = C.cfg, N.ROOT, N._state_dir
    C.cfg = lambda: (_ for _ in ()).throw(SystemExit("模拟配置错误"))
    N.ROOT = Path(d)
    try:
        check(real_state_dir() == Path(d) / "state",
              "配置加载的 SystemExit 被通知层隔离并创建回退目录")
    finally:
        C.cfg, N.ROOT = orig_cfg, orig_root

    N._state_dir = lambda: Path(d)          # 把日志引到临时目录，别污染真实 state/

    print("[1] 必定留下记录")
    N.notify("测试标题", "测试正文", popup=False)
    log = read_log(d)
    check("测试标题" in log and "测试正文" in log, "标题与正文都写进了 alerts.log")
    check(log.count("\n") == 1, f"一条告警一行，实得 {log.count(chr(10))} 行")
    check("(log)" in log, "记录了走的是哪条通道，便于排查'为什么没看到弹窗'")

    print("\n[2] 追加而不是覆盖")
    N.notify("第二条", "又一条", popup=False)
    check(read_log(d).count("\n") == 2, "第二条追加在后面，第一条还在")

    print("\n[3] 多行正文折成一行，不破坏日志的一行一条")
    N.notify("多行", "第一行\n第二行\r\n第三行", popup=False)
    last = read_log(d).strip().splitlines()[-1]
    check("第一行 / 第二行" in last, "换行被折成分隔符")
    check(read_log(d).count("\n") == 3, "仍然是一条告警一行")

    print("\n[4] 脏输入不抛异常（主流程不能被通知搞崩）")
    for label, t, m in [
        ("空字符串", "", ""),
        ("引号", 'He said "stop" & ran', "a | b > c"),
        ("德语变音", "Prüfung", "Größe: 42 · Straße"),
        ("超长", "x" * 500, "y" * 5000),
        ("emoji", "⚠️", "instagram 连续 5 天零新增"),
    ]:
        try:
            N.notify(t, m, popup=False)
            check(True, f"{label} 不抛异常")
        except Exception as e:
            check(False, f"{label} 抛了 {e!r}")

    print("\n[5] 弹窗通道整个炸掉也只是降级，不上抛")
    boom = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("模拟 PowerShell 不可用"))
    orig_toast, orig_msg = N._toast, N._msgbox
    N._toast, N._msgbox = boom, boom
    before = read_log(d).count("\n")
    try:
        N.notify("降级", "toast 和 msg 都炸了", popup=True)
        check(True, "两级弹窗都抛异常时 notify 仍正常返回")
    except Exception as e:
        check(False, f"上抛了 {e!r}")
    after = read_log(d)
    check(after.count("\n") == before + 1, "降级后仍然写了日志")
    check("(log-only)" in after.strip().splitlines()[-1], "通道标记为 log-only")
    N._toast, N._msgbox = orig_toast, orig_msg

    print("\n[6] 连日志都写不了也不抛（磁盘满 / 无权限）")
    N._state_dir = lambda: (_ for _ in ()).throw(OSError("模拟无法访问 state 目录"))
    try:
        N.notify("最后一道", "日志也写不了", popup=False)
        check(True, "退到 stdout，仍不抛异常")
    except Exception as e:
        check(False, f"上抛了 {e!r}")

print("\n" + ("全部通过" if not fails else f"{len(fails)} 项失败"))
if not fails:
    print("\n真实弹窗需手工验收：.venv\\Scripts\\python.exe -m core.notify")
sys.exit(1 if fails else 0)
