"""Business Suite 发布链路。

- `compose`  离线硬闸（G0b）：不碰浏览器就把能拦的全拦掉；
- `selectors` G1 回填的定位与**没录到的缺口**；
- `business_suite` UI 操作（G2–G6c、G7）；
- `workflow` 单次提交、成功信号与内容日历回读状态机；
- `journal`  `state/published.jsonl` 的追加式留痕与幂等（G6b）。

G6 代码已经实现，但生产注册表仍保持关闭：只有同一份完整 v2 probe 按顺序证明
目标账号上下文、提交动作、成功语义、Planner 数据就绪，以及同一卡片内独立的
时刻/正文/FB/IG/图片语义之后，显式 ``--submit`` 才能进入点击路径；该入口还会
强制启用经 probe 复核的严格 UI 约束。
"""

from publish.compose import (ComposeError, DePost, InstagramConstraints,
                             ScheduleWindow, compose_post)
from publish.business_suite import ScheduledReadback, SubmitResult
from publish.journal import PublishAttempt

__all__ = [
    "ComposeError",
    "DePost",
    "InstagramConstraints",
    "ScheduleWindow",
    "SubmitResult",
    "ScheduledReadback",
    "PublishAttempt",
    "compose_post",
]
