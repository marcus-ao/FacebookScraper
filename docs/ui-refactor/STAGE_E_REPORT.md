# Stage E′ — 历史归档

完成共享 48px Table、日期/冻结账号列、服务端筛选与分页；默认 50，可选 20/50/100，所有条件及页码进入 URL。分页位于首屏标题右侧。

真实隔离 fixture 浏览器验证：2021-01 / Instagram / OfflineFixture 共 31 条，20 条一页，第 2 页 11 条；刷新、详情刷新及返回恢复全部条件；90 天外只读详情 API 可读。B19 的完整详情交互随 Stage D 复跑；B24 已通过。

1366×768 首屏 12 行，1920×1080 首屏 19 行；均为 48px，分页可达。证据见 browser-stage-e.json 和 screenshots/final-history-*.png。

前端 421 测试通过；typecheck/build 通过。无新增依赖、无后端或生产数据改动、无真实外部动作。
