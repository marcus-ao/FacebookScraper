# Stage G — 运营设置

完成上方日常运营设置 Card/Form、下方只读系统配置 Collapse。只开放 default_times 与 snooze_default_days；校验 1–12 个唯一 HH:MM 时刻、1–30 个工作日。配置原注释完整保留于折叠详情。

browser-stage-g.json：只出现两个输入；应用内导航、浏览器后退、beforeunload 均拦住未保存草稿；在临时 fixture 中实际改写配置制造 CAS 冲突，恢复后保留用户输入并用最新 version 保存，PUT 严格 {values,version}。重复时刻禁止提交。类型检查、构建通过，生产配置未改动。
