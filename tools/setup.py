r"""一次性环境搭建。对应实施计划的 A1。

由 scripts\setup.bat 调用，也可以直接 `py -3 tools\\setup.py` 跑。

**为什么这些逻辑在 Python 里而不在 .bat 里**：cmd.exe 解析含非 ASCII 字符的
批处理文件不可靠——实测中文 REM 注释行会被从中间劈开，后半段当命令执行
（`'录下双击运行。' is not recognized as an internal or external command`），
加不加 `chcp 65001` 都会犯。而 Python 在 Windows 控制台走 Unicode API
（WriteConsoleW），任何码页下中文都正确显示。所以 .bat 只留纯 ASCII 的壳。

本文件用系统 Python 启动（venv 还不存在），因此只用标准库、不用 3.11+ 语法。
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# PyPI 镜像。官方源 files.pythonhosted.org 在国内网络实测吞吐近 0：
# playwright（36 MB）跑 8 分钟零进展，换清华镜像后 45 秒装完。
# 境外网络可设环境变量 PYPI_INDEX_URL= （空值）走官方源。
DEFAULT_INDEX = "https://pypi.tuna.tsinghua.edu.cn/simple"
INDEX_URL = os.environ.get("PYPI_INDEX_URL", DEFAULT_INDEX).strip()

VENV = ROOT / ".venv"
VENV_PY = VENV / "Scripts" / "python.exe"


def out(msg=""):
    print(msg, flush=True)


def die(msg):
    out("[!] " + msg)
    sys.exit(1)


def run(argv, **kw):
    """跑一条命令，回显命令行本身，返回退出码。"""
    out("    $ " + " ".join(str(a) for a in argv))
    return subprocess.call([str(a) for a in argv], cwd=str(ROOT), **kw)


def step(title):
    out()
    out("=== " + title + " ===")


# --------------------------------------------------------------------------

def pick_toolchain():
    """优先 uv：它能自己挑合适版本的解释器，比手工比版本号可靠。"""
    if shutil.which("uv"):
        try:
            ver = subprocess.check_output(["uv", "--version"], text=True).strip()
        except Exception:
            ver = "uv"
        out("    找到 %s，用它建环境与装依赖" % ver)
        return "uv"
    out("    未找到 uv，回退到 python -m venv + pip")
    # 回退路径下 venv 继承当前解释器，所以这里就得卡版本
    if sys.version_info < (3, 11):
        die("需要 Python 3.11 或更高（当前 %d.%d）。\n"
            "    原因：core/config.py 用了 3.11 才有的 tomllib。\n"
            "    装 uv 也可以，它会自己拉一个合适的解释器。"
            % (sys.version_info[0], sys.version_info[1]))
    out("    Python %d.%d.%d OK" % sys.version_info[:3])
    return "venv"


def make_venv(toolchain):
    if VENV_PY.exists():
        out("    .venv 已存在，跳过创建")
        return
    if toolchain == "uv":
        rc = run(["uv", "venv", "--python", ">=3.11"])
    else:
        rc = run([sys.executable, "-m", "venv", str(VENV)])
    if rc != 0 or not VENV_PY.exists():
        die("venv 创建失败")


def install_deps(toolchain):
    if INDEX_URL:
        out("    使用镜像：%s" % INDEX_URL)
    else:
        out("    使用 PyPI 官方源")
    if toolchain == "uv":
        # uv 缓存常在 C 盘而项目在别的盘，跨盘硬链接必失败并刷告警，直接用复制
        os.environ["UV_LINK_MODE"] = "copy"
        argv = ["uv", "pip", "install", "--python", str(VENV_PY)]
        if INDEX_URL:
            argv += ["--index-url", INDEX_URL]
        argv += ["-r", str(ROOT / "requirements.txt")]
    else:
        argv = [str(VENV_PY), "-m", "pip", "install"]
        if INDEX_URL:
            argv += ["-i", INDEX_URL]
        argv += ["-r", str(ROOT / "requirements.txt")]
    if run(argv) != 0:
        die("依赖安装失败。若卡在下载不动，多半是网络到 PyPI 的吞吐问题，\n"
            "    换个镜像重试：set PYPI_INDEX_URL=https://mirrors.aliyun.com/pypi/simple")


def install_playwright():
    # 抓取用的是 start_chrome 起的系统 Chrome（CDP 附着），附着本身不需要这个
    # chromium。装它是给 G 组的 playwright codegen 用。
    if run([str(VENV_PY), "-m", "playwright", "install", "chromium"]) != 0:
        die("playwright install 失败")


def self_check():
    rc = run([str(VENV_PY), "-c",
              "import playwright, httpx, tomllib; print('    依赖导入 OK')"])
    if rc != 0:
        die("依赖导入失败")
    rc = run([str(VENV_PY), "-c",
              "import sys; sys.path.insert(0, r'%s'); "
              "from core.config import cfg; print('    Chrome 探测:', cfg().chrome_exe)"
              % str(ROOT)])
    if rc != 0:
        out("[!] Chrome 未自动探测到。请把完整路径填入 config.toml 的 [chrome].exe")


def run_tests():
    """离线测试是 B 组校准的基线，必须全绿才算 A1 通过。

    原版 setup.bat 不检查测试退出码，测试挂了照样打印"完成"——
    那会让 A2 的"基线全绿"形同虚设。

    用 glob 而不是写死文件名：后续任务每新增一套 tests_*.py 就自动纳入基线，
    不需要回来改这里（改这里很容易忘，忘了就等于那套测试没人跑）。
    """
    paths = sorted(ROOT.glob("tests/tests_*.py"))
    if not paths:
        die("tests/ 下一套离线测试都没找到，请确认目录结构完整")
    out("    共 %d 套：%s" % (len(paths), ", ".join(p.name for p in paths)))
    failed = []
    for p in paths:
        if run([str(VENV_PY), p.relative_to(ROOT)]) != 0:
            failed.append(p.name)
    if failed:
        out()
        die("离线测试未全绿：%s\n"
            "    先修到全绿再往下走。它们是 B 组校准的基线；\n"
            "    基线本身是坏的，就分不清\"我改坏了\"和\"本来就坏\"。"
            % ", ".join(failed))


def main():
    if hasattr(sys.stdout, "reconfigure"):
        # 输出被重定向到管道时，locale 编码可能表示不了某些字符；
        # 别让一条打印语句把整个安装流程搞崩。
        sys.stdout.reconfigure(errors="replace")

    step("检查工具链")
    toolchain = pick_toolchain()

    step("创建虚拟环境")
    make_venv(toolchain)

    step("安装依赖")
    install_deps(toolchain)

    step("安装 Playwright 浏览器驱动")
    install_playwright()

    step("自检")
    self_check()

    step("离线测试（B 组校准的基线，必须全绿）")
    run_tests()

    out()
    out("完成。下一步：")
    out("  1. 编辑 config.toml 的 [targets]，填入真实账号")
    out("  2. 双击 scripts\\start_chrome.bat，在打开的窗口里登录抓取小号")
    out("  3. 运行 scripts\\run_backfill.bat facebook")
    out()


if __name__ == "__main__":
    main()
