# Stage I — 行为回归与请求对照

前端：430/430 测试通过，22 个测试文件；原 415 项全部保留。新增 15 项覆盖列表缓存/patch、来源变化草稿恢复和运行状态语义。typecheck 和 React build 通过；旧 Vue build 通过。

Python：原有 65 个脚本完整运行，首轮 64/65；仅 tests_operating_settings 的配置重载断言出现 3 != 4。原样单独复跑 1/1 脚本、5/5 用例通过，未修改后端或该测试。现有 Config 用 mtime_ns+size 判断重载，失败现象与快速等长写入未被识别相符，但本轮没有进一步扩大调查范围。首轮与复跑都保存在 python-evidence/；不要把首轮写成 65/65。

browser-stage-all.json：12 个隔离浏览器场景组通过，涵盖 B1–B24。各组 C、E、D1–D5、D、F、G、H、I 保留独立请求记录。测试包含八状态、四类冲突恢复、check 不挡保存、长文码点/重叠标记、实际 DST 解析拒绝、三种离开守卫、无写入口的冻结归档、图片、付费任务终态与恢复、月历失败缓存和 runtime 三种恢复。

network-comparison.json：同一临时帖子、相同运营输入分别实际操作 Vue/React，16 个工作流的 mutation method/path/完整 body 全部一致；保存的最后一个 check body 也完全一致。16 组只读请求序列有差异：新 Header 共享 runtime GET、详情使用 calendar GET、图片加载位置和 Query 缓存时机变化。未新增写 endpoint 或 body 字段。

串联验证中为保存和排期主按钮补固定中文 aria-label，避免加载图标进入按钮名称。各场景使用独立 fixture 宿主和归档，所有外部动作 stub，服务器禁止列表未触发。真实 approve、calendar refresh、paid model、Feishu resend 均为 0。

integrity-check.json：原七个 dirty 文件、两个 config、两套 package/package-lock 与 BASELINE_BEHAVIOR 均与开工哈希相同。测试产生的 workspace state/offline-validation-* 日志已移动到 docs 的 python-evidence，业务状态未改动。
