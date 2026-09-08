# 审校台原型

给业务同事用的德语审校界面。设计见 [docs/PROTOTYPE_DESIGN.md](../docs/PROTOTYPE_DESIGN.md)，
需求背景见 [docs/REQUIREMENTS.md](../docs/REQUIREMENTS.md)。**本文件只讲怎么跑。**

## 一条必须先知道的分界

```
web/api/reader.py       只读数据层    ← 生产代码，切换到生产时不动
web/api/fake_writer.py  假写入端      ← 一次性代码，切换到生产时只换它
```

**读全真、写全假。** 读的那半碰的是只读归档，怎么试错都无害；写的那半只写
`web/_fake_state.json`，**永远不碰 `archive/` 与 `state/`**。

假写入端返回的 JSON 与 `GET /api/tasks/<id>` **逐字段同构**——真实现照着返回
同一份即可，前端一行不用改。

## 跑起来

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
