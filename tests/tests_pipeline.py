"""流水线状态与告警的离线验证，使用隔离归档。"""
from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.console import force_utf8  # noqa: E402
from core import paid_requests  # noqa: E402

force_utf8()

from pipeline import cli as P  # noqa: E402
from tools.schedule import (ALIVE_TASK, CATCHUP_TASK, DAILY_TASK,  # noqa: E402
                            NS, plan)

fails = []


def check(cond, msg):
    print(("  OK   " if cond else "  FAIL ") + msg)
    if not cond:
        fails.append(msg)


def raises(exc_type, call, contains: str = ""):
    try:
        call()
    except exc_type as exc:
        return not contains or contains in str(exc)
    except Exception:
        return False
    return False


NOW = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)
GOOD_CONFIG = {"autonomy": "manual", "dead_man_days": 3,
               "monthly_budget_usd": 60, "daily_budget_usd": 5}


def write_state(d: Path, **files) -> Path:
    state = d / "state"
    state.mkdir(parents=True, exist_ok=True)
    for name, payload in files.items():
        (state / name.replace("__", ".")).write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return state


# ---------------------------------------------------------------------------
print("\n[1] [pipeline] 配置审计：不许有死旋钮，也不许有拼错了却静默失效的键")

check(P.pipeline_settings(GOOD_CONFIG)["dead_man_days"] == 3,
      "合法配置正常读出")
check(raises(P.PipelineConfigError,
             lambda: P.pipeline_settings({**GOOD_CONFIG, "dead_man_dayz": 3}),
             "不认识的键"),
      "拼错的键被拒绝——静默失效的配置项是这个项目明令不许有的")
check(raises(P.PipelineConfigError,
             lambda: P.pipeline_settings(
                 {k: v for k, v in GOOD_CONFIG.items() if k != "dead_man_days"}),
             "缺少必需键"),
      "少了键也被拒绝，不悄悄用默认值兜底")
check(raises(P.PipelineConfigError,
             lambda: P.pipeline_settings({**GOOD_CONFIG, "autonomy": "full-auto"}),
             "autonomy"),
      "autonomy 只接受四档里的一个，不接受看起来合理的别名")
check(raises(P.PipelineConfigError,
             lambda: P.pipeline_settings({**GOOD_CONFIG, "dead_man_days": 0}),
             "dead_man_days"),
      "dead_man_days = 0 被拒：那等于把死人开关关掉，必须显式改配置才行")
check(raises(P.PipelineConfigError,
             lambda: P.pipeline_settings({**GOOD_CONFIG, "dead_man_days": True}),
             "dead_man_days"),
      "布尔值不算整数（Python 里 True == 1，这个坑踩过就知道）")
check(raises(P.PipelineConfigError,
             lambda: P.pipeline_settings({**GOOD_CONFIG, "monthly_budget_usd": -1}),
             "monthly_budget_usd"),
      "负预算被拒")
check(all(P.PIPELINE_CONFIG_KEYS.values()),
      "autonomy、死人开关与日/月预算都已经有真实消费者")

real = P.pipeline_settings()
check(set(real) == set(P.PIPELINE_CONFIG_KEYS),
      "真实 config.toml 的 [pipeline] 段能通过审计（合并时补回来的那一段）")


# ---------------------------------------------------------------------------
print("\n[2] 最近一次成功运行：判据必须是「跑过」，不是「产出过」")

with tempfile.TemporaryDirectory() as d:
    base = Path(d)
    state = write_state(base, delta_state__json={
        "facebook": {"last_run": "2026-08-31T04:41:02Z",
                     "last_success": "2026-08-31T04:41:02Z",
                     "last_new_count": 0},
        "instagram": {"last_run": "2026-09-01T04:41:34Z",
                      "last_success": "2026-09-01T04:41:34Z",
                      "last_new_count": 0},
    })
    when, sources = P.last_successful_run(state)
    check(when == datetime(2026, 9, 1, 4, 41, 34, tzinfo=timezone.utc),
          "取各平台 last_success 的最大值")
    check(sources == ["delta_state.json[instagram]"],
          "点名证据来源——用户要能自己去核对那个文件")
    check(P.run_check_alive(NOW, state, lambda *a, **k: None) == P.ALIVE,
          "**抓到 0 篇也算活着**：这正是 PIPELINE_PLAN 第 7 节要区分的两件事，"
          "拿产出当判据会把一个健康的流水线误判成死的")

with tempfile.TemporaryDirectory() as d:
    state = write_state(Path(d), delta_state__json={
        "facebook": {"last_success": "2026-08-20T00:00:00Z"}},
        pipeline_state__json={"last_successful_run": "2026-09-01T06:00:00Z"})
    when, sources = P.last_successful_run(state)
    check(sources == ["pipeline_state.json"] and when.day == 1,
          "L1a 以后写的 pipeline_state.json 会被一并考虑；现在缺了也不报错")

with tempfile.TemporaryDirectory() as d:
    state = write_state(Path(d), delta_state__json={
        "facebook": {"last_success": "2026-08-31 04:41:02"}})   # 无时区
    when, _ = P.last_successful_run(state)
    check(when is None,
          "没带时区的时间戳当作没有证据——不猜本机时区，"
          "猜错会让死人开关早响或晚响整整一天")


# ---------------------------------------------------------------------------
print("\n[3] 死人开关（L0c）：任务书那条验收")

def silent_notify():
    sent = []
    def fn(title, message, popup=True):
        sent.append((title, message, popup))
    return sent, fn

with tempfile.TemporaryDirectory() as d:
    base = Path(d)
    stale = (NOW - timedelta(days=4)).strftime("%Y-%m-%dT%H:%M:%SZ")
    state = write_state(base, delta_state__json={"facebook": {"last_success": stale}})
    sent, fn = silent_notify()
    with contextlib.redirect_stdout(io.StringIO()):
        rc = P.run_check_alive(NOW, state, fn, popup=False)
    check(rc == P.DEAD and len(sent) == 1, "4 天前 → 判定为死并告警一次")
    check("3 天" in sent[0][1] and "4.0 天" in sent[0][1],
          "告警里同时写出实际天数与阈值，用户不用去翻配置")
    check("Task Scheduler" in sent[0][1] and "delta.log" in sent[0][1],
          "告警直接给出下一步查哪两样，而不是只说'出事了'")
    check(sent[0][2] is False, "--no-popup 一路传到 notify，自动化验收不糊桌面")

    fresh = (NOW - timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
    write_state(base, delta_state__json={"facebook": {"last_success": fresh}})
    sent2, fn2 = silent_notify()
    with contextlib.redirect_stdout(io.StringIO()):
        rc2 = P.run_check_alive(NOW, state, fn2, popup=False)
    check(rc2 == P.ALIVE and not sent2, "改回当天 → 不响，一条通知都不发")

with tempfile.TemporaryDirectory() as d:
    state = write_state(Path(d))          # 什么状态文件都没有
    sent, fn = silent_notify()
    with contextlib.redirect_stdout(io.StringIO()):
        rc = P.run_check_alive(NOW, state, fn, popup=False)
    check(rc == P.DEAD and "从未" in sent[0][1], "从未跑过也算死，不是'暂时没数据'")
    check("刚装完计划任务" in sent[0][1] and "证明告警通道是通的" in sent[0][1],
          "首次安装后必然响的那一次被明确写成自检——"
          "不解释清楚，用户第一次见到就会把通知关掉，那是最坏的结果")
    check("第 9 步" in sent[0][1], "还没装的情况直接指向 MANUAL_STEPS 第 9 步")

check(P.ALIVE == 0 and P.ALIVE_CHECK_FAILED == 1 and P.DEAD == 2,
      "退出码 0/1/2 三态分开：'查不了'与'查出来是死的'不能混成同一个非零码")

import inspect  # noqa: E402
import core.notify  # noqa: E402

check(inspect.signature(P.run_check_alive).parameters["notify_fn"].default
      is core.notify.notify,
      "默认真的走 core.notify（三级降级 toast → msg → alerts.log）——"
      "上面几条都注入了假 notify，这条防的是'假的留在了默认值里'")


# ---------------------------------------------------------------------------
print("\n[4] 「可发」是发布口径，不是翻译口径（这个项目最容易搞错的一处数字）")

rows = [
    {"post_id": "text+img", "text": "hi",
     "media": [{"kind": "image", "local_path": "posts/a/01.jpg"}]},
    {"post_id": "text-only", "text": "hi", "media": []},
    {"post_id": "video-only", "text": "hi",
     "media": [{"kind": "video", "url": "https://x.invalid/v"}]},
    {"post_id": "img-not-downloaded", "text": "hi",
     "media": [{"kind": "image", "url": "https://x.invalid/1.jpg"}]},
    {"post_id": "no-text", "text": "   ",
     "media": [{"kind": "image", "local_path": "posts/b/01.jpg"}]},
    {"post_id": "mixed", "text": "hi",
     "media": [{"kind": "video", "url": "https://x.invalid/v"},
               {"kind": "image", "local_path": "posts/c/02.jpg"}]},
]
publishable = P.publishable_ids(rows)
check(publishable == {"text+img", "mixed"}, "只有'有正文且有已下载的图'算可发")
check("video-only" not in publishable and "text-only" not in publishable,
      "纯视频与无媒体帖不算可发——归档 1067 与可发 470 的差就是这 597 篇")
check("img-not-downloaded" not in publishable,
      "**有 URL 但没落盘**不算：发布要上传真实文件，不是 URL")
check(P.publishable_ids([{"post_id": "", "text": "x", "media": []},
                         {"text": "x"}, "not-a-dict", None]) == set(),
      "脏行被跳过而不是让整份对账崩掉（manifest 允许有脏行）")


# ---------------------------------------------------------------------------
print("\n[5] 待发布只认 scheduled/source_refs，并受激活边界约束")

with tempfile.TemporaryDirectory() as d:
    state = Path(d) / "state"
    state.mkdir()
    check(P.published_ids(state) == set(),
          "published.jsonl 不存在表示尚无 scheduled 成功记录")
    (state / "published.jsonl").write_text(
        '{"post_id":"a","platform":"facebook","status":"scheduled"}\n'
        'not json at all\n'
        '{"post_id":"b","platform":"instagram","status":"prepared"}\n'
        '{"post_id":"c","platform":"instagram","status":"scheduled"}\n', encoding="utf-8")
    check(P.published_ids(state) == {"a", "c"},
          "逐行容错且只把最终 scheduled 计为已发布；prepared 不扣积压")
    published_refs = P.published_source_refs(state)
    check(P.pending_publish_count(
              {"publishable_refs": {"facebook:a", "facebook:other"}},
              published_refs) == 1
          and P.pending_publish_count(
              {"publishable_refs": {"instagram:b", "instagram:c"}},
              published_refs) == 1,
          "积压按每个账号的 source_refs 求交集；其它账号记录不会从本账号扣除")
    check(P.pending_publish_count(
              {"publishable_refs": {"facebook:history", "facebook:new"},
               "pipeline_publishable_refs": {"facebook:new"}},
              set()) == 1,
          "激活前历史库存不计入待发布，只统计边界后的 source_refs")

with tempfile.TemporaryDirectory() as d:
    state = Path(d) / "state"
    state.mkdir()
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        P.run_status([], NOW, state)
    text = out.getvalue()
    check("—" in text and "尚未激活" in text,
          "G8/activate 前待发布列打 ——，不会把历史存量冒充流水线积压")
    check("发布口径" in text, "可发的口径写在脚注里，读表的人不用去翻文档")
    check("autonomy 与日/月预算均已" in text,
          "status 如实说明 autonomy 与预算已经接入 pipeline run")


# ---------------------------------------------------------------------------
print("\n[6] 本月花费按真实 usage 算，不按字符/张数外推")

with tempfile.TemporaryDirectory() as d:
    acct = Path(d) / "in_x"
    acct.mkdir(parents=True)
    (acct / "translated.jsonl").write_text(
        json.dumps({"post_id": "p1", "translated_at": "2026-09-01T00:00:00Z",
                    "usage": {"input_tokens": 1_000_000,
                              "output_tokens": 1_000_000}}) + "\n"
        + json.dumps({"post_id": "p2", "translated_at": "2026-08-01T00:00:00Z",
                      "usage": {"input_tokens": 9_000_000,
                                "output_tokens": 9_000_000}}) + "\n",
        encoding="utf-8")
    text_cost, image_cost, problems = P.month_spend([acct], "2026-09", lambda m: None)
    check(not problems, "真实费率可用时不报问题")
    check(abs(text_cost - (1.32 + 3.96)) < 1e-9,
          "只统计本月那一条，按 config.toml 的真实费率算（1.32 + 3.96）")
    check(image_cost == 0.0, "没有 images_de.jsonl 时图片花费是 0，不是编一个数")
    prev_text, _, _ = P.month_spend([acct], "2026-08", lambda m: None)
    check(abs(prev_text - (9 * 1.32 + 9 * 3.96)) < 1e-9, "上月那条归到上月")

check(P._previous_month("2026-01") == "2025-12", "上个月跨年正确")
check(P._previous_month("2026-09") == "2026-08", "上个月常规情况正确")
check(P._parse_iso_z("2026-09-01T00:00:00Z") is not None
      and P._parse_iso_z("2026-09-01") is None
      and P._parse_iso_z(None) is None,
      "时间解析只接受带时区的 ISO，其余一律当作没有")

with tempfile.TemporaryDirectory() as d:
    root = Path(d)
    state = root / "state"
    acct = root / "in_x"
    acct.mkdir()
    controller = paid_requests.RequestController(state, preflight=lambda: None)
    _result, receipt = controller.run(
        stage="translation", job_key="status-rejected",
        source_ref="facebook:p-ledger", media_index=None, model="fixture",
        request=lambda: "translated", usage_getter=lambda: {"tokens": 1},
        usage_errors=lambda _usage: [], usage_cost=lambda _usage: 2.5)
    controller.finalize(receipt, accepted=False, reason="hard gate")
    (acct / "translated.jsonl").write_text(json.dumps({
        "post_id": "p-ledger", "translated_at": "2026-09-01T12:00:00Z",
        "paid_request_id": receipt.request_id,
        "usage": {"input_tokens": 999999, "output_tokens": 999999},
    }) + "\n", encoding="utf-8")
    text_cost, image_cost, problems = P.month_spend(
        [acct], "2026-09", lambda _m: None, state_dir=state)
    check(text_cost == 2.5 and image_cost == 0 and not problems,
          "status 从独立 ledger 计入 output_rejected 真实费用，并按 paid_request_id 去重产物")

with tempfile.TemporaryDirectory() as d:
    state = Path(d) / "state"
    controller = paid_requests.RequestController(state, preflight=lambda: None)
    try:
        controller.run(
            stage="image", job_key="status-uncertain",
            source_ref="instagram:p", media_index=1, model="fixture",
            request=lambda: (_ for _ in ()).throw(RuntimeError("lost")),
            usage_getter=lambda: {}, usage_errors=lambda _usage: ["missing"],
            usage_cost=lambda _usage: None)
    except paid_requests.PaidRequestBlocked:
        pass
    _text, _image, problems = P.month_spend(
        [], "2026-09", lambda _m: None, state_dir=state)
    check(any("未闭合" in problem for problem in problems),
          "status 明示 paid ledger 的 uncertain/unknown，不再假称费用完整")


# ---------------------------------------------------------------------------
print("\n[7] 表格列宽按终端显示宽度算（中文占两列）")

check(P.display_width("账号") == 4 and P.display_width("abcd") == 4,
      "CJK 按 2 列计")
check(P.display_width(P._pad("账号", 10)) == 10
      and P.display_width(P._pad("fa_x", 10)) == 10,
      "补齐之后中英文行的显示宽度一致——%-24s 按字符补会让表歪掉")


# ---------------------------------------------------------------------------
print("\n[8] 死人开关必须是一个**独立**的计划任务")

tasks = dict(plan())
check(sorted(tasks) == sorted([DAILY_TASK, CATCHUP_TASK, ALIVE_TASK]),
      "plan() 出三个任务；status/remove 都遍历它，加在这里就等于处处加上")
alive = tasks[ALIVE_TASK]
check("check-alive" in alive and "run_pipeline.bat" in alive,
      "死人开关跑的是 run_pipeline.bat check-alive，不是增量入口")
check("run_delta.bat" not in alive,
      "**它不能跟增量共用入口**：要抓的失效正是'增量任务不跑了'，"
      "挂进去就会跟着一起哑掉")
check("<RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>" in alive,
      "不要求联网：这个检查一个字节都不联网，"
      "**'家里断网了'绝不能成为警报不响的理由**")
for name in (DAILY_TASK, CATCHUP_TASK):
    check("<RunOnlyIfNetworkAvailable>true</RunOnlyIfNetworkAvailable>"
          in tasks[name], "%s 仍然要求联网（它真的要联网）" % name)
check("SessionStateChangeTrigger" not in alive,
      "不加解锁触发器：一天最多一个新结论，锁屏解锁就弹是纯噪音——"
      "误报的代价是让整条告警通道失效")
check("<LogonTrigger>" in alive and "<CalendarTrigger>" in alive,
      "登录时 + 每天各查一次：前者抓'笔记本两周没开机'，后者抓'开着但任务被禁用'")
check("<WakeToRun>false</WakeToRun>" in alive, "绝不把机器叫醒")
check("<ExecutionTimeLimit>PT10M</ExecutionTimeLimit>" in alive,
      "只读检查给 10 分钟就够，不沿用抓取那个 2 小时")
check(alive.startswith('<?xml version="1.0" encoding="UTF-16"?>') and NS in alive,
      "XML 头与命名空间与另外两个一致")
check(ALIVE_TASK.isascii(), "任务名保持纯 ASCII（schtasks 的任务名走命令行）")


# ---------------------------------------------------------------------------
print("\n[9] run_pipeline.bat 的字节约定（cmd 对这三样零容忍）")

raw = (ROOT / "scripts" / "run_pipeline.bat").read_bytes()
crlf = raw.count(b"\r\n")
check(raw.count(b"\n") - crlf == 0,
      "没有裸 LF——LF 换行会让 cmd 误解析整行（刚踩过一次）")
check(not raw.startswith(b"\xef\xbb\xbf"), "没有 BOM")
check(all(byte < 128 for byte in raw), "纯 ASCII：中文消息一律留在 pipeline.py 里")
text = raw.decode("ascii")
check("PYTHONIOENCODING=utf-8" in text,
      "设了 PYTHONIOENCODING：check-alive 的输出会重定向进 pipeline.log，"
      "本机 cp936 编不出 ⚠，第一次打印就会把整个进程带走")
check('>> "state\\pipeline.log"' in text and '> "state\\pipeline.log"' not in
      text.replace('>> "state\\pipeline.log"', ""),
      "check-alive 的输出是**追加**不是覆盖：无人值守跑的东西，"
      "上一次的输出往往是排查今天问题的唯一线索")
check("goto :alive" in text and "%ERRORLEVEL%" in text,
      "用 goto 标签而不是括号块取 ERRORLEVEL——块里的 %ERRORLEVEL% "
      "在解析时就展开了，拿到的是命令执行**前**的值")


# ---------------------------------------------------------------------------
print("\n[9] preflight 把「现在到底差什么」算出来，而不是靠手维护一张表")

out = io.StringIO()
with contextlib.redirect_stdout(out):
    code = P.run_preflight(days=7)
text = out.getvalue()
check("G6/G6c 三道发布校验" in text and "ui_constraints_verified" in text,
      "预检覆盖发布证据闸与 14 个 UI 上限那一位")
check("G8 真机证据" in text and "激活边界" in text and "计划任务" in text,
      "预检覆盖 GO_LIVE 上剩下的每一步，不用人再去对照文档")
check("会怎么走" in text and "不在图文发布范围" in text,
      "预检回答的是「激活之后每天会发生什么」，不只是「配置填没填」")
check("下一步" in text, "预检末尾必须给出下一条命令，否则读完还是不知道按哪个键")
check(code in (0, 1), "预检是只读判断，用退出码表达通过与否，不抛异常")
check(P.run_preflight.__doc__ and "零写盘" in P.run_preflight.__doc__,
      "预检的只读承诺写在 docstring 里；它会在真实 archive 上跑，不许有副作用")


print("\n" + ("全部通过" if not fails else "%d 项失败" % len(fails)))
raise SystemExit(1 if fails else 0)
