"""Business Suite 发布链路。

- `compose`  离线硬闸（G0b）：不碰浏览器就把能拦的全拦掉；
- `selectors` G1 回填的定位与**没录到的缺口**；
- `business_suite` UI 操作（G2–G6c、G7）；
- `workflow` 单次提交、成功信号与内容日历回读状态机；
- `journal`  `state/published.jsonl` 的追加式留痕与幂等（G6b）。

G6 代码已经实现，但发布注册表仍保持关闭：只有同一份完整 v2 probe 按顺序证明
目标账号上下文、提交动作、成功语义、Planner 数据就绪，以及同一卡片内独立的
时刻/正文/FB/IG/图片语义之后，显式 ``--submit`` 才能进入点击路径；该入口还会
强制启用经 probe 复核的严格 UI 约束。

---

**这里刻意不做任何 re-export。** 曾经为了少打几个字，本文件从 `compose` /
`business_suite` / `journal` 各拉了几个名字上来，代价是：

- 导入 `publish` 里**任何一个**子模块，都会先把这三个重模块整个拉起来；
- 这三个又要 import 各自的兄弟，于是 `publish` 包本身成了环的一部分
  （`publish ↔ compose ↔ business_suite ↔ evidence ↔ selectors ↔
  signals_backfilled` 六个模块一个强连通分量）；
- 环一旦成立，`business_suite` 只好在 5 个函数体里写
  `from publish import evidence`（注释就写着"延迟导入，避免模块初始化环"），
  `selectors` 和 `compose` 各再写一处。**导入顺序变成了承重结构。**

而那 8 个 re-export 的名字，业务代码和测试里**一个消费者都没有** ——
所有人写的都是 `from publish import <子模块>`。

所以规矩是：**本文件只放文档。** 要什么就从定义它的子模块直接拿。
"""
