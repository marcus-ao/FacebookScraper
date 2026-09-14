# 审校台决策记录

本文只保留仍影响当前 `web/ui/` 实现的决定。迁移步骤、旧目录保留策略、临时核验页面和已经完成的替换动作不再作为产品决策维护。设计数值见 [DESIGN.md](DESIGN.md)，接口约束见 [../DESIGN.md](../DESIGN.md)。

## 1. 当前前端与部署

### D1：只维护 `web/ui/`

前端是 React + TypeScript，源码、测试、构建与文档都位于 `web/ui/`。构建输出是 `web/ui/dist/`。项目不维护第二套界面、备用源码目录或界面切换说明。

`config.toml` 的 `[paths].web_dist` 显式写为 `web/ui/dist`。本机 `config.local.toml` 只接续 archive/state，不覆盖前端选择。日常服务在开发改动合并回原 `main` 后，从原主工作区运行 `scripts/run_web.bat`。

### D2：技术栈保持窄

当前依赖为 React、TypeScript strict、React Router、TanStack Query、Ant Design、Ant Design icons、dayjs、Vite 和 Vitest。不为相同能力再引入 Tailwind、shadcn/ui、Radix、Framer Motion、React Hook Form、Ant Design Pro Components、Sonner 或第二套图标库。

直接 import 的依赖必须显式列在 `package.json`。浏览器回归继续使用仓库已有的 Python Playwright 工具，不增加 `@playwright/test`。

### D3：开发端口和部署形状固定

Vite 使用 5174，把 `/api` 代理到 FastAPI 8765。构建后由 FastAPI 同源提供 `web/ui/dist/`，部署静态产物的机器不需要 Node。

真实路径需要 SPA fallback；API 404、缺失静态资源和非 HTML 请求不得被 fallback 隐藏。

## 2. 队列、历史与导航

### D4：队列固定四组

队列分为待我审、未就绪、已挂起、已处理。服务器返回各组计数，客户端不从当前页重算。`not_ready` 显示“未就绪”，避免与可行动的“待我审”混淆。

查询参数使用 `queue` 表示界面分组，不使用 `status`。一个组可能对应多个业务状态，例如待我审包含 `pending_review` 与 `edited`；`status` 保留给 API 的真实业务状态。

### D5：历史使用服务端分页

历史默认每页 50 条，可选择 20/50/100。page、limit、platform、month 和 tag 进入 URL；详情返回时恢复原页和筛选。90 天只用于待办默认范围，不限制历史详情。

本版不增加全文搜索和批量动作。它们都需要新的后端契约和独立业务判断，不能用当前页的客户端筛选伪装。

### D6：详情路径携带来源上下文

当前任务详情使用 `/review/<account>/<post_id>`，历史详情使用 `/history/<account>/<post_id>`。返回链接和前后篇导航保留列表来源与查询参数。

筛选、分页和来源写在 URL；`location.state` 只作导航辅助。这样刷新、收藏、粘贴给同事或浏览器恢复后仍能回到同一上下文。

### D7：旧查询链接继续可读

`/?task=...` 与 `/?view=history|calendar|settings|runtime` 转成当前路径。兼容逻辑只负责地址转换，不恢复已删除的界面或旧行为分支。

### D8：运行状态是辅助入口

运行状态保留为 `/runtime`，不与审校、历史、月历和设置争夺主业务导航层级。页面显示实际 snapshot 的状态与下一步，不自行推导“运行正常”。

## 3. 编辑与业务动作

### D9：标签输入支持整段粘贴

语义标签和产品分类都保留文本输入能力，再实时解析为可删除的 chips。它支持从聊天、表格或旧文案整段粘贴，也能处理空格、逗号和中文逗号。受保护标签自动并入并去重。

产品分类与本地化正文使用独立 CAS；保存一个区域不能偷偷提交另一区域未保存的内容。

### D10：正文链接使用可读显示和稳定存储

Facebook 的存储 token 是 `{{linkN}}`，但运营编辑时看到可读的“链接 N”标记。插入位置按当前光标；提交 `/check` 和保存时还原 token。最终计数由服务器换入真实 URL 后给出。

Instagram 不使用正文 token，只使用 bio CTA。未知编号、损坏 token 或缺目标地址是结构问题，不能把字面 token 发出去。

### D11：确定性检查辅助人工判断

金额、标签或来源差异需要可见，但检查结果不禁用保存。运营可能有正当理由纠正来源内容。结构性问题仍阻止通过或排期。

编辑态必须在输入区附近显现；保存冲突使用统一恢复流程，保留草稿并载入最新来源。未保存保护覆盖导航、前进后退、刷新和关闭。

### D12：图片查看进度不作硬闸

图片区记录逐张查看状态，缺德语图明确回退原图，切换任务时清空查看进度。未看完的数量用于提醒，不能单独阻止人工通过。图片放大使用 Modal，并恢复关闭后的焦点。

### D13：排期确认只接受严格成功回执

HTTP 200 不等于排期成功。界面只在 `ok === true && status === "scheduled"` 时显示已排期；其它结果提示核对回执并重新读取详情。`scheduled` 不能显示成已发布。

人工选择的柏林时刻发生同渠道 90 分钟冲突时直接拒绝并给建议，不自动改时刻。月历只做查看、刷新和占用判断，不从空日格直接创建排期。

### D14：付费动作不是页面主动作

初翻、文案优化和图片优化显示估算费用、剩余次数、许可与阻塞原因，按钮保持普通层级。中断恢复要求核对付费账本；恢复动作本身不重发模型请求。

## 4. 风险、状态和信息表达

### D15：风险扫描按真实四态展示

风险分为未扫描、失败、成功且零风险、成功且有风险；来源或提示词变化会使结果过期。未扫描使用中性状态，失败/过期使用警告，有风险显示具体标记，零风险不占一个满宽成功提示。

确定性错误与语义风险必须使用不同视觉语言。重叠算法优先级是 `error > risk > warn`，视觉重量是 `error > warn > risk`。

### D16：列表问题字段是 `hard_alerts`

列表契约使用 `hard_alerts`。类型、shape 测试和问题指示组件共同守住该字段，避免第三方作者或硬闸提示因字段名错误而静默消失。

### D17：列表不显示无法证明的“译文四态”

当前列表 payload 只有摘要和状态，不能可靠区分机器新鲜、机器过期、人工版和无译文四种情况。详细版本状态留在详情。若未来需要该列，先扩展后端字段并定义来源。

## 5. 路由、请求与数据形状

### D18：服务层与 React 解耦

`services/` 和 `lib/` 不依赖 React，便于单独测试 URL、错误载荷、字符索引、时区和响应形状。组件通过 hooks 消费服务，不直接散写 fetch。

HTTP 错误保留状态码和 JSON payload。排期冲突依赖 suggestions，月历错误可能仍携带 cards；统一错误层不能只留下字符串。

### D19：领域类型按实际响应维护

TypeScript 类型依据 FastAPI 实际返回形状，选填字段保持选填，不以空对象伪造能力。开发构建做轻量 shape assertion，失败写 console error 但不让整个页面白屏；部署构建不重复做完整 runtime schema validation。

API 与 UI 必须来自同一提交。契约变化时同时更新类型、服务、临时后端夹具和浏览器断言。

### D20：列表缓存只在明确动作后刷新

打开详情和返回不重复拉取列表；保存或状态动作在本地更新对应行和计数。显式点击审校队列导航或用户刷新才重新取列表。任何局部更新都必须保持服务器计数与行状态一致，无法安全推导时使查询失效并重取。

## 6. 视觉与可访问性定值

- 主色 `#155EEF`，对白色约 5.41:1；
- 顶栏 48px，详情吸顶摘要 56px，列表行 48px；
- 正文 15px / 1.7，英德 1:1 分栏且各自滚动；
- 1366×768 目标至少 12 行，1920×1080 目标至少 18 行；
- 错误红、风险黄，状态不能只靠颜色；
- 终态用中性色并降低整行饱和度；
- 破坏性动作放菜单和 danger 确认，不在每行放红色按钮；
- disabled 动作必须说明原因；
- 每页一个 `h1`，中文 locale，2px/2px 焦点环；
- 关闭 smooth scroll，尊重 `prefers-reduced-motion`。

这些值是回归目标。整理前版本曾有对应测量和 496 项 React 测试记录；本次单目录版本已在 `state/ui-consolidation-20260914T063209Z` 记录 26 个文件共 505 项测试和 TypeScript + Vite 构建通过。当前集成 worktree 的新报告已记录完整 66 个 Python 脚本、八个浏览器工作流场景、12 组综合浏览器检查、16 个网络契约和实际 FastAPI 静态演练通过；旧截图和旧浏览器结果不自动成为当前验收结果。

## 7. 延后事项

| 项目 | 当前决定 | 启动条件 |
|---|---|---|
| 历史全文/摘要搜索 | 暂不实现 | 后端定义查询、索引和高亮契约 |
| 批量挂起或不发 | 暂不实现 | 运营确认实际频率、回退和审计需求 |
| 从月历空槽创建排期 | 暂不实现 | 单渠道真实验收完成并明确交互 |
| 列表译文版本列 | 暂不实现 | 后端提供可证明的版本字段 |
| 正文可拖动分栏 | 暂不实现 | 实际长文使用证明固定 1:1 不够 |
| 表格虚拟滚动 | 暂不实现 | 真实列表量和性能测量表明确有需要 |
| 运行状态直接链接不确定任务 | 等后端字段 | snapshot 提供稳定 task IDs |
| 飞书恢复自动填接收组与原消息 | 等后端字段 | outbox 状态返回可安全复用的数据 |
| JavaScript 分包 | 先测量 | 当前构建出现可感知加载或缓存问题 |
| 排期详情媒体验证 | 必须完成 | 取得受控远端控件证据并实现数量、顺序和来源 SHA 回读 |

## 8. 验证契约

本次单目录整理后的最低验证集合如下，2026-09-14 当前集成 worktree 已完成这些入口；每次后续改动仍按相同命令复验：

```powershell
npm.cmd --prefix web/ui ci
npm.cmd --prefix web/ui test
npm.cmd --prefix web/ui run build
scripts\run_python.bat tests/tests_browser_workflow.py -v
scripts\run_python.bat tests/browser_regression.py --stage ALL
scripts\run_python.bat tests/network_compare.py
scripts\run_python.bat tests/cutover_rehearsal.py
scripts\run_python.bat tests/review_probe.py
scripts\run_python.bat tests/history_thumbnail_cost.py
```

`network_compare.py` 的名字保留，但当前职责是核对 React 的 16 个显式请求契约，不比较已删除界面。`cutover_rehearsal.py` 默认使用 `web/ui/dist` 和实际 FastAPI。任何新报告都要注明哪些行为使用临时真实后端，哪些使用界面响应替代，且不能把离线结果写成真实模型、飞书或平台提交通过。
