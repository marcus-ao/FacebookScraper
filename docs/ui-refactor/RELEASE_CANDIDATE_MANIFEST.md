# Release Candidate 文件清单

**基线 `2c89f07`，未提交。** 这份只回答一个问题：建 release commit 时，
哪些是代码库的长期资产，哪些只是这几轮 review 留下的证据。

**不要按这份清单删文件。** 它只做标注。

---

## A. Production source

| 文件 | 状态 | 这次改了什么 |
|---|---|---|
| [core/config.py](../../core/config.py) | KEEP_IN_GIT | 新增 `invalidate_cfg_cache()` |
| [core/operating_settings.py](../../core/operating_settings.py) | KEEP_IN_GIT | `os.replace` 成功之后作废配置缓存 |
| [web/api/app.py](../../web/api/app.py) | KEEP_IN_GIT | `SinglePageFiles`：深链接刷新的 SPA 回落 |
| [web/ui-next/src/styles/global.css](../../web/ui-next/src/styles/global.css) | KEEP_IN_GIT | antd loading 图标离场残留 |
| [web/ui-next/src/pages/review-detail/ReviewDetailPage.tsx](../../web/ui-next/src/pages/review-detail/ReviewDetailPage.tsx) | KEEP_IN_GIT | 标签页「访问过才挂载，之后保持挂载」 |
| [web/ui-next/src/hooks/useApproval.ts](../../web/ui-next/src/hooks/useApproval.ts) | KEEP_IN_GIT | 回执非 scheduled 重读详情；清空时间不回填；原因链外提 |
| [web/ui-next/src/pages/history/HistoryPage.tsx](../../web/ui-next/src/pages/history/HistoryPage.tsx) | KEEP_IN_GIT | 列序不再靠写死的 splice 下标 |
| [web/ui-next/src/features/localization/CategoryEditor.tsx](../../web/ui-next/src/features/localization/CategoryEditor.tsx) | KEEP_IN_GIT | 冲突恢复走自己的 busy |
| [web/ui-next/src/features/localization/LocalizationEditor.tsx](../../web/ui-next/src/features/localization/LocalizationEditor.tsx) | KEEP_IN_GIT | 常驻说明再压一遍，保留链接落位那一句 |
| [web/ui-next/src/features/approval/DecisionPanel.tsx](../../web/ui-next/src/features/approval/DecisionPanel.tsx) | KEEP_IN_GIT | 灰按钮原因可聚焦；占用提示跨午夜 |
| [web/ui-next/src/features/approval/occupancy.ts](../../web/ui-next/src/features/approval/occupancy.ts) | KEEP_IN_GIT | **新增**，同渠道占用的纯函数 |
| [web/ui-next/src/features/content-jobs/ContentJobs.tsx](../../web/ui-next/src/features/content-jobs/ContentJobs.tsx) | KEEP_IN_GIT | 两条原因链改用纯函数 |
| [web/ui-next/src/components/DisabledReason.tsx](../../web/ui-next/src/components/DisabledReason.tsx) | KEEP_IN_GIT | **新增** |
| [web/ui-next/src/components/DisabledReason.module.css](../../web/ui-next/src/components/DisabledReason.module.css) | KEEP_IN_GIT | **新增** |
| [web/ui-next/src/components/PaidActionButton.tsx](../../web/ui-next/src/components/PaidActionButton.tsx) | KEEP_IN_GIT | 改用 `DisabledReason` |
| [web/ui-next/src/components/PaidActionButton.module.css](../../web/ui-next/src/components/PaidActionButton.module.css) | KEEP_IN_GIT | 删掉搬走的 `.disabledWrap` |
| [web/ui-next/src/pages/calendar/CalendarPage.tsx](../../web/ui-next/src/pages/calendar/CalendarPage.tsx) | KEEP_IN_GIT | 删无效 ARIA；`data-day`；柏林「今天」 |
| [web/ui-next/src/pages/calendar/CalendarPage.module.css](../../web/ui-next/src/pages/calendar/CalendarPage.module.css) | KEEP_IN_GIT | 「今天」的描边与小字 |
| [web/ui-next/src/lib/format.ts](../../web/ui-next/src/lib/format.ts) | KEEP_IN_GIT | 新增 `berlinToday()` |
| [web/ui-next/src/lib/action-reasons.ts](../../web/ui-next/src/lib/action-reasons.ts) | KEEP_IN_GIT | **新增**，三条原因链 + seed 判据 |
| [web/ui-next/src/app/search-params.ts](../../web/ui-next/src/app/search-params.ts) | KEEP_IN_GIT | URL 参数边界 |
| ~~web/ui-next/src/pages/Placeholder.tsx~~ | KEEP_IN_GIT（**删除**） | 122 行死文件，零引用 |

---

## B. Production tests

这些是"以后谁弄坏了会当场红"的部分。**全部 KEEP_IN_GIT。**

### Python（进 `tools/test_offline.py`，自动按 `tests/tests_*.py` 收集）

| 文件 | 守什么 |
|---|---|
| [tests/tests_spa_static.py](../../tests/tests_spa_static.py) | **本轮新增。** 深链接刷新、漏产物仍 404、接口不被吞、非 HTML 不回落、HEAD、真实接口优先、写方法 405、路径越界 |
| [tests/tests_operating_settings.py](../../tests/tests_operating_settings.py) | 等长改写 + 同一 mtime 刻度仍读得到新值；落盘失败不作废缓存 |

### Vitest

| 文件 | 守什么 |
|---|---|
| [src/pages/review-detail/tab-mounting.test.tsx](../../web/ui-next/src/pages/review-detail/tab-mounting.test.tsx) | 标签页挂载策略 |
| [src/components/DisabledReason.test.tsx](../../web/ui-next/src/components/DisabledReason.test.tsx) | 灰按钮原因的键盘可达性 |
| [src/features/approval/occupancy.test.ts](../../web/ui-next/src/features/approval/occupancy.test.ts) | 跨午夜占用 |
| [src/lib/action-reasons.test.ts](../../web/ui-next/src/lib/action-reasons.test.ts) | 三条原因链优先级 + 清空不回填 |
| [src/app/search-params.test.ts](../../web/ui-next/src/app/search-params.test.ts) | URL 参数边界 |
| [src/lib/format.test.ts](../../web/ui-next/src/lib/format.test.ts) | 柏林「今天」、跨午夜分钟差 |
| [src/app/design-discipline.test.ts](../../web/ui-next/src/app/design-discipline.test.ts) | **本轮加两条**：loading 离场规则还在、月历没有无效 list 语义 |

### 浏览器回归

| 文件 | 本轮变化 |
|---|---|
| [tools/browser_regression.py](tools/browser_regression.py) | KEEP_IN_GIT。Stage D4 增加「提交失败后必须重读这一篇」；日期格选择器改 `data-day`；导航超时放宽（断言仍 8 秒） |

---

## C. Engineering docs / scripts

| 文件 | 状态 | 说明 |
|---|---|---|
| [PRE_CUTOVER_REPORT.md](PRE_CUTOVER_REPORT.md) | KEEP_IN_GIT | 切换决策的依据，含未解决风险 |
| [CUTOVER_CHECKLIST.md](CUTOVER_CHECKLIST.md) | KEEP_IN_GIT | 上线当天要照着勾的那张 |
| [RELEASE_CANDIDATE_MANIFEST.md](RELEASE_CANDIDATE_MANIFEST.md) | KEEP_IN_GIT | 本文件 |
| [POST_IMPLEMENTATION_REVIEW.md](POST_IMPLEMENTATION_REVIEW.md) | KEEP_IN_GIT | 上一轮复核，解释了为什么有这些修改 |
| [README.md](README.md) | KEEP_IN_GIT | 索引，已加第 10、11 行 |
| [tools/cutover_rehearsal.py](tools/cutover_rehearsal.py) | KEEP_IN_GIT | 唯一能验证真实部署形状的东西；回滚路径也靠它 |
| [tools/review_probe.py](tools/review_probe.py) | KEEP_IN_GIT | 图片/标签页/密度/键盘/月历/排期时间的浏览器级断言 |
| [tools/history_thumbnail_cost.py](tools/history_thumbnail_cost.py) | KEEP_IN_GIT | 唯一未解决风险的复现脚本 |
| [tools/flaky_settings_probe.py](tools/flaky_settings_probe.py) | OPTIONAL | 配置缓存的概率实测。根因已经有确定性测试了，这个只是当时的取证过程 |

---

## D. Generated evidence

都是脚本重跑就能再生成的。留着方便对照，删了也不影响任何测试。

| 文件 | 状态 |
|---|---|
| `docs/ui-refactor/browser-stage-*.json` | OPTIONAL |
| `docs/ui-refactor/network-comparison.json` | OPTIONAL |
| `docs/ui-refactor/final-measurements.json` | OPTIONAL |
| `docs/ui-refactor/review-probe.json` | OPTIONAL |
| `docs/ui-refactor/cutover-rehearsal-react.json` | OPTIONAL |
| `docs/ui-refactor/cutover-rehearsal-vue.json` | OPTIONAL |
| `docs/ui-refactor/screenshots/*.png` | OPTIONAL |
| `docs/ui-refactor/browser-failure.txt` / `screenshots/browser-failure.png` | DO_NOT_REQUIRE。失败时的诊断转储，当前内容已恢复成 HEAD 版本 |
| `state/**` | DO_NOT_REQUIRE。已 gitignore，本来就不会进 commit |

---

## 不在本次 commit 里的东西

确认一遍，免得误会：

- **没有**修改真实 `config.toml`；
- **没有**切换 `[paths].web_dist`；
- **没有**删除 `web/ui/`（回滚要用）；
- **没有**动 `pipeline/`、`publish/`、`routes/`；
- `web/ui/dist` 与 `web/ui-next/dist` 是构建产物，按仓库现状处理，本清单不做判断。
