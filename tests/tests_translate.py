r"""翻译层自测。对应实施计划 F 组的【验收】中不依赖真实 API 的部分。

**这套测试不调用任何 API**：翻译器被整体替换成假的。
真实网关的验收走 `scripts\run_translate.bat --check`，真实译文质量的验收
（F1「对 3 篇试跑」、F2「人工检查 3 篇」）必须等 B 组抓到真实文案之后。

这里保证的是：管道本身对不对——挑哪几篇、写到哪、重跑会不会重复花钱、
以及**绝对不碰 manifest.jsonl**。
"""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))   # tests/ 在子目录，得把项目根加进来
from core.console import force_utf8   # noqa: E402

force_utf8()   # 输出被重定向到文件/管道时，cp936 编不出 ß/⚠ 会让整套测试崩掉

import translate as T

fails = []


def check(cond, msg):
    print(("  OK   " if cond else "  FAIL ") + msg)
    if not cond:
        fails.append(msg)


class FakeTranslator:
    """假翻译器：把英文原样加个前缀返回，并记录收到的 system prompt。"""

    def __init__(self, fail_on=()):
        self.calls = []
        self.fail_on = set(fail_on)

    def translate(self, text, system):
        self.calls.append({"text": text, "system": system})
        if text in self.fail_on:
            raise RuntimeError("模拟网关错误")
        return "DE::" + text


def make_archive(base: Path, posts: list[dict]) -> Path:
    d = base / "in_acme"
    (d / "media").mkdir(parents=True, exist_ok=True)
    with (d / "manifest.jsonl").open("w", encoding="utf-8") as f:
        for p in posts:
            f.write(json.dumps(p, ensure_ascii=False) + "\n")
    return d


def post(pid, text, day=1, media=None, complete=True):
    return {"post_id": pid, "platform": "instagram", "account": "acme",
            "text": text, "created_at": f"2026-08-{day:02d}T12:00:00Z",
            "permalink": f"https://www.instagram.com/p/{pid}/",
            "media": media or [], "source_route": "backfill",
            "media_complete": complete}


s = T.Settings()

print("[1] Settings 从 config.toml 读到 [translate]")
check(s.api_key_env == "ANTHROPIC_API_KEY", f"api_key_env={s.api_key_env}")
check(s.auth_style in ("x-api-key", "bearer"), f"auth_style={s.auth_style}")
check(s.style_examples == 6, f"style_examples={s.style_examples}")
check(s.max_tokens > 0 and s.max_retries >= 0, "max_tokens / max_retries 有值")
check(s.effort is None and s.temperature is None,
      "effort / temperature 默认不发送（兼容第三方网关）")
check(s.prompt_cache is False, "prompt_cache 默认关闭（兼容第三方网关）")
check(bool(s.tone), "tone 非空")
check(s.address_form in ("du", "Sie", "sie"), f"address_form={s.address_form}")
check(s.gender_style in ("neutral", "colon", "generic"), f"gender_style={s.gender_style}")
check(s.anglicism_policy in ("moderate", "minimal", "keep"),
      f"anglicism_policy={s.anglicism_policy}")
check(isinstance(s.glossary, dict), "glossary 是字典（[translate.glossary] 子表）")

print("\n[2] 风格示例取长度中位数附近，不取两头")
rows = [post(f"p{i}", "x" * (10 + i * 40), i) for i in range(1, 10)]
ex = T.pick_style_examples(rows, 3)
check(len(ex) == 3, f"取到 3 篇，实得 {len(ex)}")
lengths = sorted(len((r["text"] or "")) for r in rows if len(r["text"]) >= 20)
check(min(len(e) for e in ex) > lengths[0], "没取到最短的那篇")
check(max(len(e) for e in ex) < lengths[-1], "没取到最长的那篇")

print("\n[3] 风格示例的边界情况")
check(T.pick_style_examples(rows, 0) == [], "n=0 返回空")
check(T.pick_style_examples([], 3) == [], "空归档返回空")
check(len(T.pick_style_examples(rows, 99)) <= len(rows), "n 超过总数不报错")
check(all("x" * 130 not in e for e in T.pick_style_examples(rows, 3, exclude_id="p4")) or True,
      "exclude_id 被接受")
ex_ex = T.pick_style_examples(rows, 8, exclude_id="p5")
check(rows[4]["text"] not in ex_ex, "exclude_id 那篇确实被排除（不拿自己当自己的参照）")
check(T.pick_style_examples([post("s", "short")], 3) == [], "过短的文案不做风格示例")

print("\n[4] system prompt 由模板渲染，含全部硬性要求")
sp = T.build_system_prompt(s, ["Example one text here"])
check(T.TEMPLATE_PATH.exists(), f"模板文件存在：{T.TEMPLATE_PATH.name}")
check(not sp.lstrip().startswith("<!--"), "给人看的 HTML 注释头没被发给模型")
check("{{" not in sp, "没有残留未替换的占位符")
for token, label in [("最多 3 个", "话题标签上限"), ("换行", "保留换行结构"),
                     ("19,99", "德式小数分隔"), ("24.12.2026", "德式日期格式"),
                     ("14:30 Uhr", "24 小时制时间"), ("20 %", "百分号前空格"),
                     ("„Text", "德式引号"), ("Größe", "德国正字法 ß"),
                     ("只输出德语译文本身", "只输出译文"), ("控制长度", "控制长度"),
                     ("情绪强度", "不加码情绪"), ("Denglisch", "避免英语句法直译")]:
    check(token in sp, f"包含{label}")

print("\n[5] 数字类硬规则（修掉了原提示词里「把 $49.99 写成 49,99 €」的错误）")
check("擅自改价" in sp, "明确禁止换算货币金额")
check("逐字符原样复制" in sp, "金额是逐字符复制，不是「本地化排版」")
check("US 9 ≠ EU 42" in sp, "明确禁止换算数字尺码")
check("×2.54" in sp, "长度重量可换算，且给了系数")
check("不要凭空提高精度" in sp, "换算后不得虚增有效位数")
check("不要自己补" in sp, "禁止凭空造原文没有的数字")

print("\n[5b] 排版规范与金额规则之间不得留下矛盾")
# 这是实测踩到的坑：排版表里原本有一行「货币符号 -> 19,99 €」，
# 与「金额原样不动」直接打架，模型完全可以「合规地」把 $49.99 改成 49,99 $。
typography = sp[sp.index("## 4."):sp.index("## 5.")]
check("货币符号" not in typography,
      "排版规范表里已无货币行（它会和「金额原样不动」打架）")
check("不适用于货币金额" in typography, "排版表显式声明不管金额")
check("`49,99 €`" in sp and "`49,99 $`" in sp and "`$49,99`" in sp,
      "三种错法（换币种 / 换写法 / 换分隔符）都被点名为错误")
check("符号的位置" in sp, "符号位置也明确不许动")
check(sp.count("$49.99") >= 3, "用同一个金额反复示范，减少模型自由发挥的空间")

print("\n[6] 品牌语域决策被渲染进提示词")
check("du / dein / dir" in sp, "du 形式的具体代词都列出来了")
check("Kaufen Sie jetzt" not in sp, "配置是 du，就不该出现 Sie 形式的指令")
check("中性改写" in sp, "性别表达策略已渲染")
check("Kund:innen 之间挑一个" in sp, "中性策略说清了是改写而不是选词")
check("适度保留" in sp, "英语借词政策已渲染")
check(s.tone in sp, "包含 config.toml 的 tone")

print("\n[7] 语域配置真的会改变提示词（不是写死的）")


class _S:                                   # 轻量替身，只改要测的几项
    def __init__(self, base, **over):
        self.__dict__.update(base.__dict__)
        self.__dict__.update(over)


sie = T.build_system_prompt(_S(s, address_form="Sie", gender_style="generic",
                               anglicism_policy="minimal"), [])
check("Kaufen Sie jetzt" in sie, "改成 Sie 后提示词给的是 Sie 形式祈使句")
check("du / dein / dir" not in sie, "Sie 配置下不再出现 du 形式指令")
check("传统阳性泛指" in sie, "generic 性别策略生效")
check("尽量避免" in sie, "minimal 借词策略生效")

bad_cfg = False
try:
    T.build_system_prompt(_S(s, address_form="ihr"), [])
except SystemExit as e:
    bad_cfg = "address_form" in str(e) and "可选值" in str(e)
check(bad_cfg, "无效的 address_form 直接报错并列出可选值，不静默走默认值")

print("\n[7b] 两种鉴权风格只发送各自的凭据头")


class _ClientS(_S):
    def api_key(self):
        return "test-secret"


bearer_client = T.build_client(_ClientS(s, auth_style="bearer"))
check(bearer_client.auth_headers.get("Authorization") == "Bearer test-secret",
      "bearer 模式发送 Authorization: Bearer")
check(not any(k.lower() == "x-api-key" for k in bearer_client.auth_headers),
      "bearer 模式不再夹带占位 X-Api-Key")
bearer_client.close()

key_client = T.build_client(_ClientS(s, auth_style="x-api-key"))
check(key_client.auth_headers.get("X-Api-Key") == "test-secret",
      "x-api-key 模式只发送真实 API Key")
check(not any(k.lower() == "authorization" for k in key_client.auth_headers),
      "x-api-key 模式不夹带 Bearer 头")
key_client.close()

bad_auth = False
try:
    T.build_client(_ClientS(s, auth_style="bear"))
except SystemExit as e:
    bad_auth = "auth_style" in str(e) and "bearer" in str(e)
check(bad_auth, "鉴权风格拼错时在发请求前明确报错")

print("\n[8] 术语表渲染")
check("还没有配置术语表" in T.render_glossary({}), "空术语表给出说明而非留白")
gl = T.render_glossary({"hoodie": "Hoodie", "free shipping": "kostenloser Versand"})
check("| hoodie | Hoodie |" in gl, "渲染成 Markdown 表格")
check("kostenloser Versand" in gl, "多条都在")
check(gl.index("free shipping") < gl.index("hoodie"), "按英文词排序，输出稳定")
check("必须" in gl, "措辞是强制而非建议")
sp_gl = T.build_system_prompt(_S(s, glossary={"hoodie": "Hoodie"}), [])
check("| hoodie | Hoodie |" in sp_gl, "术语表进了提示词")

print("\n[9] 风格示例渲染")
check("暂无" in T.render_style_examples([]), "没有示例时给出说明")
check("不要去翻译它们" in sp, "明确示例是语气参照而非翻译对照")
check("Example one text here" in sp, "示例内容进了提示词")

with tempfile.TemporaryDirectory() as prompt_tmp:
    original_template = T.TEMPLATE_PATH
    broken_template = Path(prompt_tmp) / "translate_de.md"
    broken_template.write_text(
        original_template.read_text(encoding="utf-8") + "\n{{MISSPELLED_RULE}}\n",
        encoding="utf-8")
    T.TEMPLATE_PATH = broken_template
    unknown_placeholder = False
    try:
        T.build_system_prompt(s, [])
    except SystemExit as e:
        unknown_placeholder = "MISSPELLED_RULE" in str(e)
    finally:
        T.TEMPLATE_PATH = original_template
    check(unknown_placeholder, "模板里拼错的新占位符会在调用 API 前报错")

try:
    T.build_system_prompt(s, ["A literal {{SALE}} token in a captured caption"])
    check(True, "风格示例正文里的双花括号不会被误判为模板占位符")
except SystemExit as e:
    check(False, f"风格示例被误判：{e}")

print("\n[10] 需人工确认的数字识别")
for text, expect, label in [
    ("Kostenloser Versand ab $50.", True, "美元符号"),
    ("Nur 49.99 USD", True, "USD 代码"),
    ("Jetzt für 19,99 € sichern", True, "欧元金额"),
    ("Herrenschuhe US 9–13", True, "US 鞋码"),
    ("Größe 42 verfügbar", True, "数字尺码"),
    ("18 inches breit", True, "英制单位"),
    ("Passend für 15\" Laptops", True, "英寸缩写"),
    ("Neu da 🔥 Limitierte Stückzahl.", False, "普通文案不误报"),
    ("30 % Rabatt auf alles", False, "百分比不算需确认的数字"),
    ("Erhältlich in den Größen S–XXL", False, "字母尺码不误报"),
    ("", False, "空字符串不崩"),
]:
    got = bool(T.numeric_flags(text))
    check(got == expect, f"{label}：{'检出' if expect else '不误报'}　{text[:32]!r}")
check(len(T.numeric_flags("US 9 für $49.99, 18 inches")) == 3, "三类可同时检出")

print("\n[10b] 金额必须原样保留 —— 代码侧强制（提示词只是要求，模型可能不听）")
_MP = T.money_preserved
check(_MP("Only $49.99 today", "Nur $49.99 heute") == [], "原样保留 -> 放行")
check(_MP("Free shipping over $50.", "Kostenloser Versand ab $50.") == [],
      "句尾句号不影响比对（正则不吞标点）")
check(_MP("Get it for $49.99", "Für $ 49.99 sichern") == [],
      "只忽略空白差异")
# 下面四种都必须被拦下来
for src, de, label in [
    ("Only $49.99 today", "Nur 49,99 € heute", "换成欧元（最贵的错误）"),
    ("Only $49.99 today", "Nur 49,99 $ heute", "币种没变但写法与符号位置全变"),
    ("Only $49.99 today", "Nur $49,99 heute", "只把小数点改成逗号"),
    ("Only $49.99 today", "Jetzt zugreifen", "金额整个丢失"),
]:
    got = _MP(src, de)
    check(len(got) == 1, f"拦下：{label}")
    check("$49.99" in got[0] if got else False, f"报错里点名了具体金额（{label}）")
eur_hint = _MP("Only $49.99", "Nur 49,99 €")
check("八成是被换算了" in eur_hint[0], "译文冒出 € 时给出更具体的判断")
check("可能被改写" in _MP("Only $49.99", "Jetzt zugreifen")[0],
      "没有 € 时给的是通用提示，不乱下结论")
check(_MP("New drop, no prices here", "Neu da, keine Preise") == [],
      "原文没有金额 -> 不检查、不误报")
check(_MP("", "") == [] and _MP("$5", "") != [], "空输入不崩")
check(len(_MP("Was $99.00, now $49.99", "Statt 99,00 € jetzt 49,99 €")) == 1
      and "$99.00" in _MP("Was $99.00, now $49.99", "Statt 99,00 € jetzt 49,99 €")[0]
      and "$49.99" in _MP("Was $99.00, now $49.99", "Statt 99,00 € jetzt 49,99 €")[0],
      "多个金额都被列出来")
check(_MP("Only $5 today", "Nur $50 heute") != [],
      "金额按完整 token 比对，$5 不会误命中 $50 的前缀")
check(_MP("Was $10, now $10", "Nur $10") != [],
      "按出现次数比对，原文两次而译文一次会被拦下")
check(_MP("Only 50 € today", "Nur 50 € heute") == [],
      "后置货币符号能被识别并在原样保留时放行")
check(_MP("Only 50 € today", "Nur 50 $ heute") != [],
      "后置货币符号被改动时不会因正则边界漏检")
check(_MP("Only $49.99", "Nur $49.99 (ca. 45 €)") != [],
      "保留原价但擅自追加换算价同样被拦下")
check(_MP("No price in the source", "Jetzt nur 45 €") != [],
      "译文凭空新增原文没有的金额会被拦下")

print("\n[11] 代码围栏剥离")
check(T._strip_wrapper("```de\nHallo Welt\n```") == "Hallo Welt", "剥掉 ```de 围栏")
check(T._strip_wrapper("```\nHallo\n```") == "Hallo", "剥掉裸围栏")
check(T._strip_wrapper("Hallo Welt") == "Hallo Welt", "无围栏时原样返回")
check(T._strip_wrapper("Preis: ```code``` hier").startswith("Preis"),
      "正文中间的围栏不动（不做激进清洗）")
# 回归：_FENCES 曾把短的排在前面，"```de" 抢先匹配 "```deutsch"，
# 只切掉 5 个字符，留下 "utsch\n..." 直接污染译文。
for fence in ("```deutsch", "```german", "```text", "```de"):
    check(T._strip_wrapper(f"{fence}\nHallo Welt\n```") == "Hallo Welt",
          f"{fence} 围栏被完整剥掉（长的必须先匹配）")

print("\n[12] 待译筛选")
done = {"p2": {"post_id": "p2"}}
cand = [post("p1", "hello", 3), post("p2", "world", 1),
        post("p3", "", 2), post("p4", "   ", 4), post("p5", "again", 2)]
td = T.pending(cand, done, force=False)
check([r["post_id"] for r in td] == ["p5", "p1"],
      f"跳过空正文与已译，按时间正序，实得 {[r['post_id'] for r in td]}")
check(len(T.pending(cand, done, force=True)) == 3, "--force 时已译的也回到待译")

with tempfile.TemporaryDirectory() as tmp:
    base = Path(tmp)
    posts = [
        post("a1", "Free shipping on all orders over $50", 1,
             [{"url": "u1", "kind": "image", "local_path": "media/a1_0.jpg",
               "width": 1080, "height": 1080}]),
        post("a2", "New drop: the Summer Tee is here", 2,
             [{"url": "u2", "kind": "video", "local_path": None}]),
        post("a3", "", 3),
        post("a4", "Our best-selling hoodie is back in stock, sizes S to XXL", 4,
             [{"url": "u4", "kind": "image", "local_path": "media/a4_0.jpg",
               "width": 1440, "height": 1800}], complete=False),
    ]
    arc = make_archive(base, posts)
    manifest_before = (arc / "manifest.jsonl").read_bytes()

    print("\n[13] 翻译主流程")
    fake = FakeTranslator()
    ok, bad = T.run_translate(s, fake, arc, limit=None, force=False, dry_run=False)
    check((ok, bad) == (3, 0), f"3 篇有正文的被翻译，实得 ok={ok} bad={bad}")
    check(len(fake.calls) == 3, "空正文那篇没有发起调用（不白花钱）")
    systems = {c["system"] for c in fake.calls}
    check(len(systems) == 1,
          f"同一账号内 system prompt 完全一致，实得 {len(systems)} 个不同版本")
    check("{{" not in fake.calls[0]["system"], "发出去的提示词没有残留占位符")

    tr = T.load_translated(arc / "translated.jsonl")
    check(set(tr) == {"a1", "a2", "a4"}, f"译文覆盖正确，实得 {sorted(tr)}")
    check(tr["a1"]["text_de"] == "DE::" + posts[0]["text"], "译文内容写对了")
    for field in ("post_id", "text_de", "translated_at", "model"):
        check(field in tr["a1"], f"译文行含 {field} 字段")
    check(tr["a1"]["translated_at"].endswith("Z"), "translated_at 是 UTC ISO")
    check(tr["a1"]["prompt_version"] == T.PROMPT_VERSION, "记录了提示词版本")

    print("\n[14] 抓取产物不可变（这条最关键）")
    check((arc / "manifest.jsonl").read_bytes() == manifest_before,
          "manifest.jsonl 一个字节都没变")
    check((arc / "translated.jsonl").exists(), "译文写在独立文件里")

    print("\n[15] 重跑幂等，不重复花钱")
    fake2 = FakeTranslator()
    ok2, bad2 = T.run_translate(s, fake2, arc, limit=None, force=False, dry_run=False)
    check((ok2, bad2) == (0, 0), f"没有待译项，实得 ok={ok2}")
    check(len(fake2.calls) == 0, "一次 API 调用都没发")

    print("\n[16] --limit 与 --force")
    fake3 = FakeTranslator()
    T.run_translate(s, fake3, arc, limit=2, force=True, dry_run=False)
    check(len(fake3.calls) == 2, f"--limit 2 只调用 2 次，实得 {len(fake3.calls)}")
    check(len(T.load_translated(arc / "translated.jsonl")) == 3,
          "重译后按 post_id 收敛仍是 3 条")

    print("\n[17] --dry-run 不调用 API")
    fake4 = FakeTranslator()
    T.run_translate(s, fake4, arc, limit=None, force=True, dry_run=True)
    check(len(fake4.calls) == 0, "dry-run 零调用")

    print("\n[18] 单条失败不中断整批")
    arc2 = make_archive(base / "second", posts)
    fake5 = FakeTranslator(fail_on=[posts[1]["text"]])
    ok5, bad5 = T.run_translate(s, fake5, arc2, limit=None, force=False, dry_run=False)
    check((ok5, bad5) == (2, 1), f"2 成 1 败且没有抛出，实得 ok={ok5} bad={bad5}")
    check(set(T.load_translated(arc2 / "translated.jsonl")) == {"a1", "a4"},
          "失败那篇没有写进译文文件，下次重跑会自动补")

    print("\n[19] 审核清单 review.md")
    n = T.run_review(arc)
    md = (arc / "review.md").read_text(encoding="utf-8")
    check(n == 3, f"3 篇进清单，实得 {n}")
    check("Free shipping on all orders over $50" in md, "含英文原文")
    check("DE::Free shipping" in md, "含德语译文")
    check("![a1](media/a1_0.jpg)" in md, "图片用相对路径引用，预览器能直接显示")
    check("(1440×1800)" in md.replace("（", "(").replace("）", ")"), "标了分辨率")
    check(md.count("- [ ] 图内含英文文字，需人工替换") == 3, "每篇都有图内文字待确认框")
    check(md.count("- [ ] 译文已审校") == 3, "每篇都有审校勾选框")
    check("媒体不全" in md, "media_complete=False 的帖子有警示")
    check("https://www.instagram.com/p/a1/" in md, "带原帖链接便于比对")
    check(md.index("`a1`") < md.index("`a2`") < md.index("`a4`"), "按时间正序排列")

    print("\n[19b] 审核清单标出需人工确认的数字")
    # a1 的原文含 "$50"，假翻译器原样带过来，应被标出
    check("1 篇含需人工确认的数字" in md, "抬头统计了待确认篇数")
    check("需人工确认的数字" in md, "正文里有警示块")
    check("需替换成德国站定价" in md, "说清了要人工做什么")
    check(md.count("- [ ] 数字已按德国站确认/替换") == 1,
          "只有含金额那篇多一个勾选框，其余不加噪音")
    a1_sec = md[md.index("`a1`"):md.index("`a2`")]
    a2_sec = md[md.index("`a2`"):md.index("`a4`")]
    check("需人工确认的数字" in a1_sec, "含 $50 的 a1 被标出")
    check("需人工确认的数字" not in a2_sec, "不含数字的 a2 没被误标")

    print("\n[20] 账号目录发现")
    check([p.name for p in T.account_dirs(base)] == ["in_acme"],
          "只认含 manifest.jsonl 的目录")
    check(T.account_dirs(base, "nope") == [], "--account 过滤生效")
    check(T.account_dirs(base / "does-not-exist") == [], "archive 不存在时返回空不崩")

    print("\n[21] 脏 translated.jsonl 不崩")
    p = arc / "translated.jsonl"
    # 最后那行是合法 JSON 但不是对象——曾经会抛 TypeError 而不是被跳过
    p.write_text('{"post_id":"x","text_de":"a"}\n不是json\n{"no_id":1}\n\n'
                 '{"post_id":"x","text_de":"b"}\n[1,2]\n"字符串"\n',
                 encoding="utf-8")
    loaded = T.load_translated(p)
    check(set(loaded) == {"x"}, "坏行被跳过")
    check(loaded["x"]["text_de"] == "b", "同 post_id 后写胜出")

print("\n[22] --limit 是跨账号的总上限，负数直接拒绝")
with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    for name in ("fa_acme", "in_acme"):
        d = root / name
        d.mkdir()
        (d / "manifest.jsonl").write_text("", encoding="utf-8")

    base_config = T.cfg()

    class FakeConfig:
        archive_dir = root

        def get(self, section, key, default=None):
            return base_config.get(section, key, default)

    original_cfg, original_run = T.cfg, T.run_translate
    calls = []
    try:
        T.cfg = lambda: FakeConfig()

        def fake_run(_s, _translator, arc_base, limit, _force, _dry_run):
            calls.append((arc_base.name, limit))
            return 1, 0

        T.run_translate = fake_run
        rc = T.main(["--dry-run", "--limit", "1"])
    finally:
        T.cfg, T.run_translate = original_cfg, original_run
    check(rc == 0, "跨账号小批量试跑正常结束")
    check(len(calls) == 1 and calls[0][1] == 1,
          "--limit 1 只处理一个账号中的一篇，不会每个账号各处理一篇")

negative_limit = False
try:
    T.main(["--limit", "-1"])
except SystemExit as e:
    negative_limit = e.code == 2
check(negative_limit, "--limit 为负数时 argparse 明确拒绝")

print("\n" + ("全部通过" if not fails else f"{len(fails)} 项失败"))
if not fails:
    print("\n真实网关验收：scripts\\run_translate.bat --check")
    print("真实译文验收（F1/F2）需等 B 组抓到文案后进行。")
sys.exit(1 if fails else 0)
