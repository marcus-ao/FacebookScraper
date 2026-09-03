r"""L 组流水线：把四个阶段的真相源重新对账，并按 autonomy 等级执行。

    settings  `[pipeline]` 配置的严格读取。只认识 config.toml，不认识流水线。
    engine    执行引擎：预算判据、阶段调度、待人工项、批量确认。
    cli       `python -m pipeline` 的子命令与全部中文输出。

三者曾经是根目录下的三个平级文件（`pipeline.py` / `pipeline_assisted.py` /
`pipeline_settings.py`）。它们服务的是同一件事，收进同一个包只是把这件事
写在目录结构上；**没有合并成一个文件**，因为 cli 是只读对账 + 人机交互，
engine 是会花钱的执行路径，两者的改动理由从来不同。

依赖方向是 ``cli → engine → settings``，单向。

⛔ **这里刻意不做任何 re-export**，理由与 `publish/__init__.py` 那段完全相同：
包级 re-export 会让导入任何一个子模块都先把其余的整个拉起来，然后子模块之间
为了绕开初始化环，只能在函数体里写延迟导入。`tests/tests_hygiene.py` 第 6 条
会盯着这件事。要什么就从定义它的子模块直接拿。
"""
