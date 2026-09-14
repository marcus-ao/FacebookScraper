# 切换清单

从上到下勾。任何一步失败就停，先判断要不要回滚（见最后两节）。

背景与判据在 [PRE_CUTOVER_REPORT.md](PRE_CUTOVER_REPORT.md)，这里不重复。

---

## PRE-SWITCH

- [ ] 工作区已经形成你认可的 release commit
- [ ] React 构建 PASS — `npm --prefix web/ui-next run build`
- [ ] Vue 回滚构建 PASS — `npm --prefix web/ui run build`
- [ ] Python 全量 PASS — `scripts\run_python.bat tools/test_offline.py`
- [ ] React 单测 PASS — `npm --prefix web/ui-next test`
- [ ] 演练 React PASS — `scripts\run_python.bat docs/ui-refactor/tools/cutover_rehearsal.py --dist web/ui-next/dist`
- [ ] 演练 Vue 回滚 PASS — 同上，`--dist web/ui/dist`
- [ ] 记下当前 `config.toml` 里 `[paths].web_dist` 的值：`________________`（没有这个键就写"没有"）
- [ ] `web/ui/dist/index.html` 存在
- [ ] `web/ui-next/dist/index.html` 存在

---

## SWITCH

- [ ] `config.toml` 的 `[paths]` 表里设置 `web_dist = "web/ui-next/dist"`
- [ ] 重启 Web 进程

⛔ 只刷新浏览器没用：`DIST` 在 `web/api/app.py` import 时就定死了。

---

## READ-ONLY SMOKE

**每一条都从地址栏直接敲，不要只点侧栏导航**——侧栏走的是前端路由，
地址栏走的才是服务端。

- [ ] `/review`
- [ ] `/history`
- [ ] 一篇活账号详情
- [ ] **在那一页按 F5**
- [ ] 一篇冻结账号（`read_only`）详情：确认没有任何写入入口
- [ ] `/calendar`
- [ ] `/settings`
- [ ] `/runtime`
- [ ] 旧链接 `/?task=<真实账号>/<真实帖子>` → 跳到 `/review/...`
- [ ] 旧链接 `/?view=history` → 跳到 `/history?page=1&limit=50`
- [ ] 浏览器 console 无报错
- [ ] Network 里没有异常 404 / 422
- [ ] 柏林时刻显示合理（不是本地时区换算过的）
- [ ] 队列四个页签的计数与真实数据对得上

---

## LOW-RISK WRITE SMOKE

由你或授权运营手工做。每步之后回列表看一眼状态。

- [ ] 编辑德语正文并保存
- [ ] 返回列表，状态正确
- [ ] 再打开这一篇，内容仍然正确
- [ ] 修改产品分类并保存
- [ ] snooze 一篇
- [ ] wake 回来

---

## OPTIONAL EXTERNAL READ

前面全部成功之后才考虑。

- [ ] 由你决定是否执行一次 calendar refresh（会打开发布浏览器读后台，数十秒）

---

## SUPERVISED EXTERNAL WRITE

**必须单独得到你的批准才做。**

- [ ] 选一篇明确允许用来测试的帖子
- [ ] 选好柏林时间
- [ ] 执行真实 approve
- [ ] 在 Business Suite 里确认这条排期真的存在
- [ ] React 界面回读为「已排期」
- [ ] ⛔ 不能只因为 HTTP 200 就判成功——回执必须是 `ok === true && status === 'scheduled'`

---

## OBSERVE（第一个工作日）

- [ ] 历史归档页缩略图补齐速度（已知风险，见报告 §13；太慢先改成 20 条/页）
- [ ] `/runtime` 状态
- [ ] 队列计数与列表局部更新是否对得上
- [ ] 记录出现过的每一次 409 恢复
- [ ] 记录运营任何一次"不知道该点哪"的瞬间

---

## ROLLBACK TRIGGERS

出现任意一条就回滚：

- 深链接又出现 404（`/review`、`/calendar`、详情页刷新）
- 页面白屏 / JS 报错
- 保存之后读不回来
- revision / 409 冲突恢复走不通
- 已排期状态显示错误（尤其把 `scheduled` 显示成已发布）
- 关键写入口消失（编辑德语、分类、snooze、wake、approve）
- 真实请求体异常（字段缺失、带了时区偏移、revision 不对）
- 历史页大面积不可用

---

## ROLLBACK

- [ ] `config.toml` 的 `[paths].web_dist` 改回 PRE-SWITCH 记下的值
      （原来没有这个键就把整行删掉），或直接写 `web_dist = "web/ui/dist"`
- [ ] 重启 Web 进程
- [ ] 打开 `/`，确认是旧 Vue 界面
- [ ] 核对几个基本 GET：`/?view=history`、`/?view=calendar`、`/?task=...`

归档与 state **不需要**回滚：这次切换没有改任何数据格式或写入契约。
