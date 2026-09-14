"""审校台 UI 审计用的离线取景台（read-only audit harness）。

它做一件事：用 `tests/browser_fixture.BrowserFixture` 的隔离数据起一个本机
HTTP 宿主，再往里塞足够多、足够多样的样本，让 UI 的各种状态（硬闸告警、
语义风险、人工稿、挂起/不发/已交人工、多图、IG 链接与 CTA）能被看见、
被截图、被量尺寸。

为什么需要它：真实归档当前 26 篇全是 `not_ready`，`risk_scan` 全是
`not_scanned`，列表 24 行文字完全相同。那份数据能证明"扫不动"，但证明不了
"有信号时长什么样"。审计需要两者都看到。

安全边界（和浏览器回归夹具同一套）：

* 数据在临时目录，真实 `archive/` 与 `state/` 一个字节都不碰；
* `socket.connect` 被限制在回环地址，任何外部请求直接失败；
* 除 `PUT /api/settings`、`PUT …/localization`、`POST …/check` 之外，
  所有非 GET 请求返回 503 —— 点到"这篇不发"也不会真的写账本；
* 退出时校验真实 `config.toml` 未被改动。

用法：

    python docs/ui-refactor/tools/audit_fixture_host.py
    python docs/ui-refactor/tools/audit_fixture_host.py --port 8799

不是自动化测试，不产生验收证据，不进回归套件。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from PIL import Image                                   # noqa: E402

from core import review, translated                     # noqa: E402
from core.store import post_dirname                     # noqa: E402
from pipeline import risk_scan                          # noqa: E402
from tests.browser_fixture import BrowserFixture        # noqa: E402

FB = "fa_neakasaofficial"
IG = "in_neakasa.global"

# 正文都带金额 / 标签 / 链接，为的是让确定性检查（红）有东西可标。
EN_TEMPLATE = (
    "📢 {title}\n\n"
    "Hi pet parents! 😸 {lead}\n\n"
    "Grab the Neakasa {model} for ${price} this week only — that is ${save} off "
    "the usual price. Ships in 2-3 days, 12 in wide, fits size M litter mats.\n"
    "🔗 Official store link:\n{url}\n\n"
    "#Neakasa #{model} #CatFeeder #CatLife #SmartPet"
)
DE_TEMPLATE = (
    "📢 {title_de}\n\n"
    "Hallo Katzeneltern! 😸 {lead_de}\n\n"
    "Hol dir den Neakasa {model} diese Woche für ${price} — das sind ${save} "
    "weniger als sonst. Lieferung in 2-3 Tagen.\n\n"
    "#Neakasa #{model} #CatFeeder #CatLife #SmartPet"
)

SAMPLES = [
    # (key, platform, title, model, price, save, images, variant)
    ("clean-1", "facebook", "Frischhalte-Update für Riko", "Riko", "219.99", "30", 1, "clean"),
    ("clean-2", "instagram", "Zwei Napf-Modi im Test", "M1Pro", "189.00", "20", 1, "clean"),
    ("money", "facebook", "Preisaktion zum Herbst", "Riko", "219.99", "30", 1, "money_changed"),
    ("hashtag", "instagram", "Neue Farbe im Shop", "M1Pro", "159.00", "15", 1, "hashtag_changed"),
    ("risk", "facebook", "Labor Day Blowout", "AirStep", "99.00", "10", 1, "risky"),
    ("carousel", "instagram", "Sechs Bilder aus dem Studio", "Riko", "219.99", "30", 6, "clean"),
    ("human", "facebook", "Von der Kollegin überarbeitet", "M1Pro", "189.00", "20", 2, "human"),
    ("untranslated", "facebook", "Noch keine Übersetzung", "Riko", "249.00", "40", 3, "raw"),
    ("snoozed", "instagram", "Warte auf Rückfrage", "AirStep", "119.00", "10", 1, "clean"),
    ("skipped", "facebook", "Nicht veröffentlichen", "Riko", "219.99", "30", 1, "clean"),
    ("handed", "instagram", "Selbst erledigt", "M1Pro", "189.00", "20", 1, "clean"),
    ("clean-3", "facebook", "Reinigungsroutine", "AirStep", "129.00", "15", 4, "clean"),
    ("clean-4", "instagram", "Kundenstimmen der Woche", "Riko", "219.99", "30", 1, "clean"),
    ("clean-5", "facebook", "Zubehör wieder lieferbar", "M1Pro", "39.00", "5", 2, "clean"),
    ("clean-6", "instagram", "Tipps gegen Streu-Spuren", "AirStep", "129.00", "15", 1, "clean"),
    ("clean-7", "facebook", "Wasserwechsel richtig gemacht", "Riko", "219.99", "30", 1, "clean"),
    ("clean-8", "instagram", "Vorher / Nachher", "M1Pro", "189.00", "20", 3, "clean"),
    ("clean-9", "facebook", "Frage aus der Community", "AirStep", "129.00", "15", 1, "clean"),
]

RISKS = [
    {"kind": "pun", "quote": "Labor Day Blowout",
     "label": "“Blowout”在德语里没有对应的促销双关，直译会读成“爆胎”"},
    {"kind": "us_only", "quote": "Labor Day",
     "label": "美国劳动节，德国站没有这个节日，需要换成当地促销由头"},
    {"kind": "ambiguous", "quote": "12 in wide",
     "label": "“in”既可读成英寸也可读成介词，德语需要明确写 Zoll 或 cm"},
]


def _shade(index: int) -> str:
    return ["#1e40af", "#b45309", "#047857", "#7f1d1d", "#4f46e5", "#0f172a"][index % 6]


class AuditHost:
    def __init__(self, fixture: BrowserFixture) -> None:
        self.fx = fixture
        self.ids: dict[str, str] = {}

    # -- 归档写入 ------------------------------------------------------
    def add(self, key, platform, title, model, price, save, images, variant, age_days):
        account = FB if platform == "facebook" else IG
        owner = "neakasaofficial" if platform == "facebook" else "neakasa.global"
        account_dir = self.fx.root / "archive" / account
        post_id = "9%011d" % (abs(hash(key)) % 10**11)
        created = (datetime.now(timezone.utc) - timedelta(days=age_days)).isoformat()
        folder = "posts/" + post_dirname(post_id, created)
        directory = account_dir / folder
        directory.mkdir(parents=True, exist_ok=True)
        media = []
        for index in range(images):
            name = "%02d.jpg" % (index + 1)
            Image.new("RGB", (1080, 1080), _shade(index)).save(directory / name)
            media.append({"kind": "image", "local_path": folder + "/" + name})
        url = "https://us.example.invalid/shop/" + model.lower()
        text_en = EN_TEMPLATE.format(title=title, lead="A quick update from the studio.",
                                     model=model, price=price, save=save, url=url)
        if variant == "risky":
            text_en = text_en.replace(title, "Labor Day Blowout")
        source = {
            "post_id": post_id, "platform": platform, "account": owner, "owner": owner,
            "coauthors": [], "created_at": created, "media_complete": True,
            "permalink": "https://example.invalid/post/" + post_id,
            "text": text_en, "tags": ["Riko"] if model == "Riko" else [model],
            "media": media,
        }
        if variant == "third_party":
            source.update(owner="catclub.de", owner_name="Cat Club Berlin", coauthors=[owner])
        task_id = account + "/" + post_id
        self.fx.sources[task_id] = (source, directory, account_dir)
        self.fx.write_source(task_id)
        with (account_dir / "manifest.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(source, ensure_ascii=False) + "\n")

        if variant != "raw":
            text_de = DE_TEMPLATE.format(title_de=title, lead_de="Ein kurzes Update aus dem Studio.",
                                         model=model, price=price, save=save)
            if variant == "money_changed":          # 金额被改 → 红色 error
                text_de = text_de.replace("$" + price, "199,00 €")
            if variant == "hashtag_changed":        # 标签被改 → 红色 error
                text_de = text_de.replace("#CatLife", "#Katzenleben")
            self.fx.write_machine(task_id, text_de)
            if variant == "human":
                translated.append_human_translation(
                    account_dir / "translated_human.jsonl", source,
                    text_de + "\n\nVon der Kollegin geprüft.", expected_revision=None)
        if variant == "risky":
            self.write_risks(task_id, text_en)
        self.ids[key] = task_id
        return task_id

    def write_risks(self, task_id: str, text_en: str) -> None:
        _text, prompt_sha = risk_scan._prompt(risk_scan.PROMPT_PATH)
        rows = []
        for item in RISKS:
            at = text_en.find(item["quote"])
            if at < 0:
                continue
            rows.append({"kind": item["kind"], "label": item["label"],
                         "quote": item["quote"], "en_span": [at, at + len(item["quote"])]})
        record = {
            "task_id": task_id, "status": "completed", "risks": rows,
            "source_text_sha256": translated.source_text_sha256(text_en),
            "scan_text_sha256": risk_scan._digest(text_en),
            "prompt_sha256": prompt_sha, "prompt_version": risk_scan.PROMPT_VERSION,
            "scanned_at": datetime.now(timezone.utc).isoformat(),
            "source": {"provider": "audit-harness", "model": "offline-no-api"},
        }
        path = risk_scan._state_path(self.fx.config.state_dir)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, ensure_ascii=False) + "\n")

    def act(self, key: str, action: str, **kwargs) -> None:
        task_id = self.ids[key]
        source, _directory, account_dir = self.fx.sources[task_id]
        with review.transaction(account_dir) as session:
            session.change(dict(source), action, expected_revision=None,
                           expected_source_sha256=translated.source_text_sha256(source["text"]),
                           **kwargs)


def main() -> int:
    parser = argparse.ArgumentParser(description="审校台 UI 审计取景台（隔离数据、禁止外网、禁止写真实账本）")
    parser.add_argument("--port", type=int, default=0, help="固定端口；默认随机")
    parser.add_argument("--seconds", type=int, default=3600, help="保持运行的秒数")
    args = parser.parse_args()

    with BrowserFixture() as fixture:
        host = AuditHost(fixture)
        for index, (key, platform, title, model, price, save, images, variant) in enumerate(SAMPLES):
            host.add(key, platform, title, model, price, save, images, variant, age_days=index + 2)
        host.act("snoozed", "snoozed", reason="等运营确认德国站落地页", snooze_days=3)
        host.act("skipped", "skipped", reason="美国限定活动，德国站不发")
        host.act("handed", "handed_off", handoff_url="https://example.invalid/manual/post")

        print("AUDIT HOST READY")
        print("  base_url : " + fixture.base_url)
        print("  data     : " + str(fixture.root))
        print("  samples  : %d" % len(SAMPLES))
        for key in ("clean-1", "money", "hashtag", "risk", "carousel", "human", "untranslated"):
            print("  %-13s %s" % (key, host.ids[key]))
        sys.stdout.flush()
        deadline = time.monotonic() + args.seconds
        while time.monotonic() < deadline:
            time.sleep(1)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
