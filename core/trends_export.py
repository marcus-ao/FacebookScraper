"""Evidence-gated Google Trends public CSV export through the isolated browser."""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import tempfile
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Mapping
from urllib.parse import parse_qs, quote, urlencode, urlparse

from core import paid_model
from core.hashtag_sampling import close_browser_session


STATE_NAME = "trends_export_state.json"
EXPORT_DIR = "trends_exports"
PROOF_KIND = "google_trends_accessible_csv_control"
EXPLORE_ORIGIN = "https://trends.google.com"


class TrendsExportUnavailable(RuntimeError):
    """The verified public export route is unavailable without a safe fallback."""


class TrendsExportBlocked(TrendsExportUnavailable):
    def __init__(self, reason: str, *, http_status: int | None = None):
        super().__init__(reason)
        self.http_status = http_status


def _tag_key(value: str) -> str:
    return value.strip().removeprefix("#").casefold()


@dataclass(frozen=True)
class ExportRequest:
    candidate_group: str
    tags: tuple[str, ...]
    geo: str
    start: date
    end: date

    @classmethod
    def create(cls, *, candidate_group: str, tags, geo: str,
               start: str | date, end: str | date) -> "ExportRequest":
        names = tuple(dict.fromkeys(str(tag).strip() for tag in tags))
        if geo != "DE":
            raise ValueError("Google Trends 公开导出仅允许 geo=DE")
        if not isinstance(candidate_group, str) or not candidate_group.strip():
            raise ValueError("Trends 导出必须绑定一个英文原标签候选组")
        if not 2 <= len(names) <= 5 or any(
                not name.startswith("#") or not _tag_key(name) or any(ch.isspace() for ch in name)
                for name in names):
            raise ValueError("同一候选组必须包含 2..5 个不同的完整标签")
        if len({_tag_key(name) for name in names}) != len(names):
            raise ValueError("Trends 候选规范化后不得重复")
        start_date = date.fromisoformat(start) if isinstance(start, str) else start
        end_date = date.fromisoformat(end) if isinstance(end, str) else end
        if not isinstance(start_date, date) or not isinstance(end_date, date) or end_date < start_date:
            raise ValueError("Trends 导出时间范围无效")
        return cls(candidate_group.strip(), names, geo, start_date, end_date)

    @property
    def time_range(self) -> str:
        return "%s %s" % (self.start.isoformat(), self.end.isoformat())

    @property
    def url(self) -> str:
        params = [("geo", self.geo), ("date", self.time_range),
                  ("q", ",".join(name.removeprefix("#") for name in self.tags)),
                  ("hl", "en")]
        return EXPLORE_ORIGIN + "/trends/explore?" + urlencode(
            params, quote_via=quote, safe=",")

    def context(self) -> dict:
        return {"candidate_group": self.candidate_group, "tags": list(self.tags),
                "geo": self.geo, "start": self.start.isoformat(),
                "end": self.end.isoformat(), "time_range": self.time_range,
                "source_url": self.url}


def _validate_request_url(request: ExportRequest, value: str) -> None:
    parsed = urlparse(value)
    if parsed.scheme != "https" or parsed.netloc != "trends.google.com" or parsed.path != "/trends/explore":
        raise TrendsExportUnavailable("浏览器没有停在已批准的 Google Trends Explore 页面")
    query = parse_qs(parsed.query, keep_blank_values=True)
    expected = {"geo": ["DE"], "date": [request.time_range],
                "q": [",".join(tag.removeprefix("#") for tag in request.tags)], "hl": ["en"]}
    if any(query.get(key) != wanted for key, wanted in expected.items()):
        raise TrendsExportUnavailable("Explore 页面地域、日期或候选组与导出请求不一致")


def _proof_payload(request: ExportRequest, observation: Mapping, recorded_at: datetime) -> dict:
    if recorded_at.tzinfo is None or recorded_at.utcoffset() is None:
        raise ValueError("控件证据时刻必须带时区")
    if observation.get("method") != "passive_accessibility_snapshot":
        raise ValueError("导出控件只能来自被动可访问树录证")
    observed_url = observation.get("observed_url")
    if not isinstance(observed_url, str):
        raise ValueError("控件证据缺少观察页面")
    _validate_request_url(request, observed_url)
    control = observation.get("control")
    if (not isinstance(control, Mapping) or control.get("role") != 'button'
            or not isinstance(control.get("accessible_name"), str)
            or control['accessible_name'].strip().casefold() not in {'download csv', 'export csv', 'csv'}
            or control.get("exact") is not True
            or control.get("count") != 1):
        raise ValueError("必须录到唯一、精确命名的 CSV 导出可访问控件")
    return {"schema_version": 1, "kind": PROOF_KIND,
            "request": request.context(), "observed_url": observed_url,
            "method": observation["method"],
            "control": {"role": control["role"],
                        "accessible_name": control["accessible_name"].strip(),
                        "exact": True, "count": 1},
            "recorded_at": recorded_at.astimezone(timezone.utc).isoformat()}


def create_control_proof(request: ExportRequest, observation: Mapping, *,
                         recorded_at: datetime) -> dict:
    proof = _proof_payload(request, observation, recorded_at)
    proof["proof_sha256"] = hashlib.sha256(json.dumps(
        proof, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")).hexdigest()
    return proof


def validate_control_proof(request: ExportRequest, proof: Mapping) -> dict:
    if not isinstance(proof, Mapping):
        raise ValueError("缺少导出控件 proof")
    try:
        recorded_at = datetime.fromisoformat(str(proof["recorded_at"]).replace("Z", "+00:00"))
        observation = {"method": proof["method"], "observed_url": proof["observed_url"],
                       "control": proof["control"]}
        expected = create_control_proof(request, observation, recorded_at=recorded_at)
    except (KeyError, TypeError, ValueError, TrendsExportUnavailable) as exc:
        raise ValueError("导出控件 proof 无效或不属于本次请求") from exc
    if proof.get("kind") != PROOF_KIND or proof.get("request") != request.context() \
            or proof.get("proof_sha256") != expected["proof_sha256"]:
        raise ValueError("导出控件 proof 已变化或不属于本次请求")
    return expected


def state_path(c) -> Path:
    return Path(c.state_dir) / STATE_NAME


def load_state(c) -> dict:
    try:
        value = json.loads(state_path(c).read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError('expected object')
        return value
    except FileNotFoundError:
        return {}
    except (OSError, ValueError, TypeError) as exc:
        raise TrendsExportUnavailable('Trends 停止记录无法读取，保留现场且不访问页面') from exc


def save_state(c, value: Mapping) -> None:
    paid_model.atomic_write_json(state_path(c), dict(value), indent=2, sort_keys=True)


def state_revision(state: Mapping) -> str:
    return hashlib.sha256(json.dumps(dict(state), sort_keys=True, ensure_ascii=False,
                                     separators=(',', ':')).encode('utf-8')).hexdigest()


def reset_block(c=None, *, reason: str, expected_revision: str, now: datetime | None = None) -> dict:
    if c is None:
        from core.config import cfg  # 延迟导入：状态恢复允许注入临时目录而不初始化运行配置。
        c = cfg()
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError("人工恢复必须记录核对说明")
    from routes import delta  # 延迟导入：恢复与采样在同一进程锁内进行版本核验。
    with delta.DeltaRunLock(Path(c.state_dir) / 'delta.lock'):
        state = load_state(c)
        if state.get("status") != "blocked" or expected_revision != state_revision(state):
            raise TrendsExportUnavailable("Trends 停止记录已变化，请重新读取并核对当前版本")
        moment = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        state.update(status="recovered", recovered_at=moment.isoformat(),
                     recovery_reason=reason.strip(), actor=None)
        save_state(c, state)
    return state


def _challenge_url(value: str) -> bool:
    path = urlparse(value).path.casefold()
    return any(marker in path for marker in ("/challenge", "/captcha", "/sorry"))


async def _playwright_download(request: ExportRequest, proof: Mapping, c) -> dict:
    from core.chrome import attach  # 延迟导入：proof/CSV 离线校验不应加载 Playwright。

    c.assert_chrome_profiles_isolated()
    pw = browser = page = None
    blocked_statuses: list[int] = []

    def observe_response(response) -> None:
        try:
            parsed = urlparse(response.url)
            if parsed.netloc == "trends.google.com" and response.status in {401, 403, 429}:
                blocked_statuses.append(response.status)
        except (AttributeError, TypeError, ValueError):
            return

    try:
        pw, browser, context = await attach(
            port=c.detect_debug_port, profile=c.detect_profile_dir,
            start_script=r"scripts\start_chrome_detect.bat", login_hint="探测小号")
        page = await context.new_page()
        page.on("response", observe_response)
        response = await page.goto(request.url, wait_until="domcontentloaded")
        status = response.status if response is not None else None
        status = blocked_statuses[0] if blocked_statuses else status
        if status in {401, 403, 429}:
            raise TrendsExportBlocked("Google Trends HTTP %s" % status, http_status=status)
        if _challenge_url(page.url):
            raise TrendsExportBlocked("Google Trends 返回 challenge/captcha 页面")
        _validate_request_url(request, page.url)
        spec = proof["control"]
        control = page.get_by_role(
            spec["role"], name=spec["accessible_name"], exact=True)
        if await control.count() != 1 or not await control.is_visible() or not await control.is_enabled():
            raise TrendsExportUnavailable("proof 记录的唯一 CSV 控件当前不可用；未尝试其他控件")
        try:
            async with page.expect_download() as pending:
                await control.click()
        except Exception as exc:
            if blocked_statuses:
                raise TrendsExportBlocked(
                    "Google Trends HTTP %s" % blocked_statuses[0],
                    http_status=blocked_statuses[0]) from exc
            if _challenge_url(page.url):
                raise TrendsExportBlocked("Google Trends 返回 challenge/captcha 页面") from exc
            raise TrendsExportUnavailable("已录证 CSV 控件没有产生浏览器下载") from exc
        if blocked_statuses:
            raise TrendsExportBlocked(
                "Google Trends HTTP %s" % blocked_statuses[0],
                http_status=blocked_statuses[0])
        if _challenge_url(page.url):
            raise TrendsExportBlocked("Google Trends 返回 challenge/captcha 页面")
        download = await pending.value
        failure = await download.failure()
        if failure:
            raise TrendsExportUnavailable("浏览器 CSV 下载失败：%s" % failure)
        suggested = download.suggested_filename
        if not isinstance(suggested, str) or not suggested.casefold().endswith(".csv"):
            raise TrendsExportUnavailable("已录证控件下载的文件不是 CSV")
        temporary = await download.path()
        body = Path(temporary).read_bytes()
        return {"body": body, "final_url": page.url,
                "download_url": download.url, "suggested_filename": suggested}
    finally:
        if page is not None:
            page.remove_listener("response", observe_response)
            await page.close()
        await close_browser_session(pw, browser)


def _atomic_bytes(path: Path, body: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=path.parent, prefix="." + path.name, suffix=".tmp")
    temporary = Path(name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(body)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def export_public_csv(request: ExportRequest, *, proof: Mapping, c=None,
                      browser_runner=None, now: datetime | None = None) -> dict:
    """Download and preserve the raw official CSV; never tries an alternate endpoint."""
    if c is None:
        from core.config import cfg  # 延迟导入：离线导出测试可注入临时状态目录。
        c = cfg()
    verified_proof = validate_control_proof(request, proof)
    moment = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    from routes import delta  # 延迟导入：仅浏览器导出需要共享 detect 锁和硬停状态。
    with delta.DeltaRunLock(Path(c.state_dir) / "delta.lock"):
        previous = load_state(c)
        if previous.get("status") == "blocked":
            raise TrendsExportBlocked(
                "Google Trends 导出已持久硬停；人工核对并显式 reset 前不会再次访问",
                http_status=previous.get("http_status"))
        shared_detect = delta.load_state(Path(c.state_dir) / "delta_state.json")
        if shared_detect.get("detect_hard_blocked"):
            reason = "共享 detect 会话仍处于登录墙或限流硬停，未访问 Google Trends"
            save_state(c, {"schema_version": 1, "status": "blocked",
                           "http_status": None, "reason": reason,
                           "blocked_at": moment.isoformat(), "request": request.context()})
            raise TrendsExportBlocked(reason)
        runner = browser_runner
        try:
            result = (runner(request=request, proof=verified_proof, c=c)
                      if runner is not None else asyncio.run(
                          _playwright_download(request, verified_proof, c)))
            if not isinstance(result, Mapping) or not isinstance(result.get("body"), bytes):
                raise TrendsExportUnavailable("浏览器没有返回原始 CSV 字节")
            final_url = result.get("final_url")
            if not isinstance(final_url, str):
                raise TrendsExportUnavailable("下载结果缺少 Explore 页面上下文")
            _validate_request_url(request, final_url)
            if _challenge_url(final_url):
                raise TrendsExportBlocked("Google Trends 返回 challenge/captcha 页面")
            body = result["body"]
            if not body or len(body) > 5 * 1024 * 1024:
                raise TrendsExportUnavailable("CSV 下载为空或超过 5 MiB 安全上限")
            try:
                csv_text = body.decode("utf-8-sig")
            except UnicodeDecodeError as exc:
                raise TrendsExportUnavailable("下载内容不是 UTF-8 CSV") from exc
        except TrendsExportBlocked as exc:
            blocked = {"schema_version": 1, "status": "blocked",
                       "http_status": exc.http_status, "reason": str(exc),
                       "blocked_at": moment.isoformat(), "request": request.context()}
            save_state(c, blocked)
            raise
        except (Exception, SystemExit) as exc:
            failed = {"schema_version": 1, "status": "failed",
                      "reason": "%s: %s" % (type(exc).__name__, str(exc)),
                      "attempted_at": moment.isoformat(), "request": request.context()}
            save_state(c, failed)
            raise

        digest = hashlib.sha256(body).hexdigest()
        text_digest = hashlib.sha256(csv_text.encode("utf-8")).hexdigest()
        stem = moment.strftime("%Y%m%dT%H%M%SZ") + "_" + digest[:12]
        raw_path = Path(c.state_dir) / EXPORT_DIR / (stem + ".csv")
        metadata_path = raw_path.with_suffix(".json")
        export_context = dict(request.context(), verified=True,
                              exported_at=moment.isoformat(), source_sha256=digest,
                              text_sha256=text_digest,
                              proof_sha256=verified_proof["proof_sha256"])
        metadata = {"schema_version": 1, "sha256": digest,
                    "raw_path": str(raw_path), "export_context": export_context,
                    "final_url": result["final_url"],
                    "download_url": result.get("download_url"),
                    "suggested_filename": result.get("suggested_filename")}
        _atomic_bytes(raw_path, body)
        paid_model.atomic_write_json(metadata_path, metadata, indent=2, sort_keys=True)
        save_state(c, {"schema_version": 1, "status": "succeeded",
                       "completed_at": moment.isoformat(), "sha256": digest,
                       "raw_path": str(raw_path), "metadata_path": str(metadata_path),
                       "request": request.context()})
        return dict(metadata, metadata_path=str(metadata_path))
