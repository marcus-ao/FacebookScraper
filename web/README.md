# 审校台

给业务同事用的德语审校界面。设计见 [DESIGN.md](DESIGN.md)，
需求背景见 [docs/REQUIREMENTS.md](../docs/REQUIREMENTS.md)。**本文件只讲怎么跑。**

## 当前可用功能

```
web/api/reader.py  读取源帖、机器译文与人工文案
web/api/writer.py  真实保存人工文案，保留版本
```

点「保存」会追加到账号目录的 `translated_human.jsonl`。刷新页面、重跑机器翻译后，
人工文案仍优先显示。源帖更新后保留人工稿并提示复核；旧浏览器页面保存时会核对源文版本
与人工稿版本，冲突时保留编辑框中的草稿。当前不记录操作人员。

「通过」「这篇不发」暂未接通，按钮禁用、接口返回 501。旧的 `fake_writer.py` 与
`web/_fake_state.json` 仅保留作原型历史，当前应用不读取或写入它们。

详情页展示最近一次人工保存时间；全部历史版本仍保存在人工文案账本。当前修改正文不会
自动重生成图片：当前机器译文仍有效时沿用它作为图片依据；机器版缺失或过期时，可按
已经重新复核的人工稿补图和检查。源文变化后尚未复核的人工稿不能继续调图。

## 跑起来

在 Git worktree 中启动时，默认读取该 worktree 自己的 `archive/` 与 `state/`。
它们与原工作区的真实归档互相独立；新 worktree 尚未准备数据时，列表为空是正常情况。
不要为演示直接修改原工作区的归档。离线验证使用 `tests/tests_web_review.py` 自动创建的
临时源帖和译文，测试结束后清理。

### 生产机（不装 Node）

```
.venv\Scripts\python.exe -m uvicorn web.api.app:app --port 8765
```

打开 http://127.0.0.1:8765/ 。前端是静态文件，由 FastAPI 直接伺服，
**不需要 Node 运行时、不需要 npm install、断网也能跑**。

### 开发机（改前端时）

```
cd web\ui
npm install
npm run dev            # 5173，/api 自动转发到 8765
```

后端仍然要另起一个 uvicorn。改完之后：

```
npm run build          # 产出 web/ui/dist/
```

**把 `web/ui/dist/` 拷到生产机**即可。`dist/` 与 `node_modules/` 都在
`.gitignore` 里——构建链只存在于开发环境，这是同时满足「用 Vue」和
「不破坏极简依赖」的唯一方式。

### 不开界面，只看数据

```
.venv\Scripts\python.exe -m web.api.reader --list
.venv\Scripts\python.exe -m web.api.reader --detail in_neakasa.tech/3973012230169803390
```

零网络、零费用、零写盘。

## 风险预扫描是静态文件

`web/api/fixtures/risks.json` 是**预跑存下来的**，原型阶段不实时调 LLM
（零成本、零延迟、演示当场不会因为 API 抖动而尴尬）。

`kind` 只允许三种：`pun` / `ambiguous` / `us_only`。
金额、单位、标签由 `core/translated.py` 的 regex 负责，**不进这个文件**。

⚠️ 当前文件里的内容是**按设计文档三类风险手写的示例**，锚点落在真实原文的
真实位置上，但判断不是 LLM 出的。要换成真数据，需要对那几篇原文跑一次
预扫描——**那一步花钱**。
