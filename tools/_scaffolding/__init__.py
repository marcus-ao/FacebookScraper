r"""一次性脚手架：**不是业务链路的一部分。**

这里的东西只在一种场合用得上：**Meta 改了 Business Suite 的 DOM，需要重录
一次探查、重新生成证据登记表。** 那件事一年最多发生一两次。

判据（2026-09-02 架构审查）：

- `publish/` 里零处 import 这两个模块；
- `probe_signals.py` 的产物 `publish/signals_backfilled.py` **已经生成并已提交**，
  生成器的活儿干完了；
- `probe_publish.py` 的输入（1.97 MB 的 dump）在 `.gitignore` 里。

它们之所以有 3,200 行，是因为 `probe_publish.py` 不是"录制器"，而是一个带
隐私脱敏 + 原子写盘 + 断线自愈 + 有界关停的 CDP 代理——那些是为了应付
CR-64/66/67 三个**录制期**事故，与发布本身没有关系。

⚠️ **不要把这里的代码往 `publish/` 搬，也不要让 `publish/` import 它。**
它们的生命周期不同：`publish/` 每天跑，这里一年跑一两次。
"""
