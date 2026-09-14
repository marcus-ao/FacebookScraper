# Stage C — 完成

队列已接 GET /api/tasks，query key 固定 `['tasks','review']`，筛选从 URL 派生。四桶总数取 summary.by_status；写成功 patch 详情、当前行和状态总数。review 缓存在会话内保留，禁止 mount/reconnect 自动刷新；显式导航刷新只在 PUSH 生效，浏览器 POP 返回不触发。

复用 PostRow、StatusTag、ProblemIndicator、BerlinTime；48px Table，摘要真实 Link，行尾 Dropdown，skip 理由必填/danger，挂起保留 +08:00，409 单篇恢复并保留表单。第三方作者在未就绪行可识别。分类在 <1600 隐藏。

验证：原 415 + 新 6 = **421 tests passed**；typecheck/build 通过。实际 Python Playwright：B21/B22 PASS；所有五类筛选 + 详情往返 **1 次 GET**；mutation **0 次整表 GET**；显式导航 **1 次新 GET**。隔离真实响应数据测得 1366 **12 行**、1920 **19 行**，均 **48px**。

证据：browser-stage-c.json；screenshots/final-review@1366x768.png、final-review@1920x1080.png。真实外部动作 false，server denials=[]。详情在本阶段仍为 A/B 占位路由，完整详情往返与导航上下文将在 D/I 再验。

无 dependency/API/backend/旧 Vue 变更，未修改生产数据或配置。构建 >500k 警告按要求仅记录。下一阶段 E′。
