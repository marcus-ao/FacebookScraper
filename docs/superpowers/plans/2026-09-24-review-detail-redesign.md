# Review Detail Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (- [ ]) syntax for tracking.

**Goal:** Turn the pending-review detail into a four-step business review with durable body/image decisions and a trustworthy final scheduling confirmation.

**Architecture:** Add append-only, content-bound review decisions and enforce them at new real-submit entrypoints. Project simple step states and to-dos from backend facts, then replace the large tabbed detail with a compact shell and four focused workspaces. Keep source data and technical diagnostics on the backend; preserve the existing freeze, snapshot, remote readback, and publication ledger.

**Tech Stack:** Python 3.12, FastAPI, append-only JSONL ledgers, React 19, TypeScript, Ant Design 6, Vitest, isolated Python unittest and Playwright fixtures.

**Spec:** ../specs/2026-09-24-review-detail-redesign.md

## Global Constraints

- Execute in the existing codex/review-detail-redesign worktree. Check git status before starting; do not mix edits with the separate history-list feature or other worktrees.
- The business interface follows D01–D49 exactly. All system-generated visible dates/times across the web UI use the existing Asia/Shanghai business basis without place or time-zone wording; source-post content itself is never rewritten to suppress a place name.
- Preserve source identity and target account as separate facts. Schedule confirmation displays the frozen exact text, image bytes/order, sole channel, target account, and selected time.
- Append to review_items.jsonl and translated_human.jsonl using the existing concurrency and validation rules. Never rewrite/delete these or other truth sources, archived originals, or published.jsonl.
- Confirmations have actor=null; do not imply identity. Version changes invalidate only the affected decision. Existing frozen/scheduled records are not backfilled with invented confirmations.
- Every new real submit requires current body and per-image decisions. Ready candidate creation and read-only preparation remain available; paid budget/source gates and real-submit evidence gates remain intact.
- Use local fixture data for all tests. Do not connect a real account, pay a model, or create a real remote schedule during implementation validation.
- Keep blue/white/light-neutral theme tokens and accessible controls. Date/time UI labels contain no location name. No money, model identifiers, raw metrics, raw JSON, archive paths, or technical diagnosis in the business detail.
- Update docs/FUNCTIONALITY.md and docs/REQUIREMENTS.md as each testable slice lands; record exact offline evidence and limits in docs/HANDOFF.md. Use English Conventional Commits with the configured identity and no AI attribution.

## Review Focus

1. A saved-but-unconfirmed body or a browsed-but-unconfirmed image must block freeze and new real submit. Tasks 1–2 test this.
2. Source text, selected image bytes/version, and image order must invalidate only the correct decisions; stale confirmation must never become true after refresh. Task 1 tests this.
3. An old frozen snapshot without the new decision records stays intact but cannot start a new real submit until it is unfrozen, reviewed, and frozen again; existing scheduled history is unchanged. Task 2 tests this.
4. An uncertain remote submit must offer reconciliation, never a new submit button or automatic retry. Task 9 tests this.
5. Leaving a dirty detail for the list/next post must preserve the draft until save/discard is chosen; changing steps must not trigger a false leave prompt. Task 10 tests this.

## File map and task traceability

| Responsibility | Files |
| --- | --- |
| Durable review decisions and projection | core/review.py; create core/content_confirmation.py and pipeline/content_confirmation.py; web/api/app.py, writer.py, reader.py |
| New real-submit gate | pipeline/approval.py, pipeline/engine.py, tools/publish_post.py, publish/workflow.py if a pre-click callback is needed |
| Free publication hashtags | core/localization.py, web/api/writer.py, web/ui/src/features/localization/model.ts and LocalizationEditor.tsx |
| Step shell, header, source, to-dos | web/ui/src/pages/review-detail/ReviewDetailPage.tsx and CSS; create focused step-state and presentation modules under that folder |
| Body and image workspaces | web/ui/src/features/localization/TextWorkspace.tsx and CSS, web/ui/src/features/images/ImageWorkspace.tsx and CSS |
| Link/tag and model tools | web/ui/src/features/localization/LocalizationEditor.tsx, web/ui/src/features/content-jobs/ContentJobs.tsx, web/ui/src/features/localization/SuggestionPanel.tsx, web/ui/src/components/PaidActionButton.tsx |
| Final schedule and outcomes | web/ui/src/features/approval/DecisionPanel.tsx and SchedulePreviewDialog.tsx, web/ui/src/hooks/useApproval.ts |
| Navigation and auxiliary actions | web/ui/src/app/AppShell.tsx, web/ui/src/hooks/useUnsavedChangesGuard.tsx, web/ui/src/features/review-actions/ReviewActions.tsx, web/ui/src/features/diagnostics/DetailDrawers.tsx |
| Shared business-time display | web/ui/src/lib/format.ts, web/ui/src/components/Time.tsx, plus actual date/time call sites in the detail, list, calendar, and status views |
| Browser contract | web/ui/src/types/domain.ts, web/ui/src/services/review.ts, web/ui/src/services/assert-shape.ts, web/ui/src/app/search-params.ts |

| 实施任务 | 对应已确认决策 | 可独立检查的交付结果 |
| --- | --- | --- |
| 1. 正文与逐图确认账本 | D06、D09–D11、D47 | 分别持久确认、按内容版本自动失效、无图不造确认 |
| 2. 冻结与真实提交闸门 | D26，承接 D06 | 单篇、CLI、批量路径在新真实提交前复核确认；旧证据保持原样 |
| 3. 标签完全可编辑 | D21 | 品牌与型号也可删改，空标签合法，语法与数量校验保留 |
| 4. 紧凑页头和四步状态 | D01–D05、D07、D30–D31、D35、D45、D48–D49 | 正确步骤、待办、来源与筛选位置，窄屏可导航 |
| 5. 德语正文工作区 | D13–D16 | 长短文对照、空状态、扫描状态、独立保存和确认 |
| 6. 图片工作区 | D17–D20、D43 | 逐张对照和确认、无德语图、版本比较、缺图处理 |
| 7. 标签和链接工作区 | D22–D25、D28、D44 | 来源／目标一一对应、光标插入、单个完成动作、IG 提醒 |
| 8. 次要模型工具与建议 | D08、D12、D38、D42 | 工具按步骤出现，候选可选，费用和技术依据不进业务页面 |
| 9. 最终确认与状态结果 | D27–D29、D36、D39–D40 | 准确文案及图片冻结预览、选时冲突、结果待核对和重开状态 |
| 10. 离开保护和整页整合 | D32–D33、D37、D41、D46 | 草稿安全离开、更多业务动作、简洁记录与整体视觉 |
| 11. 全站业务时刻一致性 | D34 | 所有系统生成时间按同一基准显示，旧偏移量和跨日也正确 |

每个 D 编号都在中文规格中对应 Q1–Q49 的一个具体功能要点；实施时逐行对照，不以这张任务分组表替代逐项验收。

### Task 1: Persist body and per-image confirmation

**Files:**
- Create: core/content_confirmation.py, pipeline/content_confirmation.py
- Modify: core/review.py, web/api/app.py, web/api/writer.py, web/api/reader.py
- Modify: web/ui/src/types/domain.ts, web/ui/src/services/review.ts, web/ui/src/services/assert-shape.ts
- Test: tests/tests_review.py, tests/tests_web_review.py, web/ui/src/types/shape.test.ts
- Docs: docs/FUNCTIONALITY.md, docs/REQUIREMENTS.md

**Interfaces:**
- Produces: a ReviewContentState with body.confirmed and images[{index, confirmed}] on TaskDetail; internal digests/selected paths remain server-only.
- Produces: PUT /api/tasks/{task_id}/review-confirmations/body and PUT /api/tasks/{task_id}/review-confirmations/images/{index}, each accepting confirmed:boolean, source_text_sha256, review_revision. Existing image-selection action may accept confirm:true so choosing original plus confirming is atomic.
- Produces: pipeline.content_confirmation.current_tokens(account_dir, source) and require_current(account_dir, source, post=None). Text token binds source-text digest and exact German body; image token binds ordinal, source-media identity and digest, selection identity/version, and selected byte digest.

- [ ] **Step 1: Write failing ledger and API tests.** In tests_review.py, append a body decision and two image decisions, then change only the body and assert only body becomes unconfirmed; replace only image 2 and assert image 1 remains confirmed; reorder images and assert affected positions become unconfirmed. In tests_web_review.py, assert a failed image read cannot be confirmed and that a machine draft can be confirmed without a preceding edit. Use isolated archive fixtures and actor=None.

      self.assertTrue(current["body"]["confirmed"])
      self.assertEqual([image["confirmed"] for image in current["images"]], [True, True])
      self.assertFalse(after_body_edit["body"]["confirmed"])
      self.assertEqual([image["confirmed"] for image in after_body_edit["images"]], [True, True])

- [ ] **Step 2: Run targeted tests and confirm red.** Run: scripts\run_python.bat -m tools.test_offline --only tests_review. Run: scripts\run_python.bat -m tools.test_offline --only tests_web_review. Expected before implementation: confirmation actions and detail state are absent.

- [ ] **Step 3: Add append-only confirmation actions and pure projection.** Add body_reviewed and image_reviewed to the allowed review actions. Validate the action-specific payload when reading each ledger line; preserve the previous business status when recording these decisions. Fold events by post and by image index, comparing them to current tokens rather than trusting a saved boolean:

      def confirmed(saved: Mapping[str, object] | None,
                    current_token: str) -> bool:
          return bool(saved and saved.get("confirmed") is True
                      and saved.get("content_token") == current_token)

  Store explicit false decisions too. Compute selected image byte hashes from current local files, include selection identity/order, and refuse confirmation when the selected image is unavailable. Keep image selections independent of later text edits. Use review.transaction and revision CAS for both write APIs; project statuses in reader.task_detail and type/shape validators. Do not materialize a reviewer name.

- [ ] **Step 4: Run the targeted tests and update docs.** Run the two Python selections above and npm.cmd --prefix web/ui test -- src/types/shape.test.ts. Expected: new and existing cases pass; a broken ledger line still fails closed. Record the contract and the current code/acceptance status in FUNCTIONALITY/REQUIREMENTS.

- [ ] **Step 5: Commit this tested slice.** Run git diff --check, inspect staged paths and any new JSONL test fixture, then commit with an English title such as feat(review): persist content-bound review decisions.

### Task 2: Enforce confirmations at freeze and every new real submit

**Files:**
- Modify: pipeline/approval.py, pipeline/engine.py, tools/publish_post.py; publish/workflow.py only for a wired pre-click check
- Test: tests/tests_approval.py, tests/tests_publish.py, tests/tests_pipeline_human.py
- Docs: docs/FUNCTIONALITY.md, docs/REQUIREMENTS.md, docs/HANDOFF.md

**Interfaces:**
- Consumes: require_current(account_dir, source, post=None) from Task 1.
- Produces: a business-language refusal before freeze and before any new real-submit click; no changes to historical frozen bytes or scheduled records.

- [ ] **Step 1: Write failing entrypoint tests.** Check approval.options reports lockable=false for missing decisions, approval.lock refuses before snapshots.freeze, and approve rechecks current decisions after freeze. Exercise tools.publish_post.main with --submit in an isolated fixture and assert zero browser attachment when decisions are absent; its preparation mode still reaches its normal preparation branch. Exercise pipeline.engine batch approval and assert the batch refuses before the first real submit when one selected post lacks confirmation. Test a legacy frozen snapshot remains readable and is directed to unfreeze/review/refreeze rather than being silently marked confirmed.

      self.assertFalse(approval.options(account_dir, source)["lockable"])
      with self.assertRaises(review.ReviewConflict):
          approval.lock(account_dir, source, source_text_sha256=digest,
                        review_revision=revision, content_fingerprint=fingerprint)
      self.assertFalse(snapshot_path.exists())

- [ ] **Step 2: Run the targeted gates to confirm red.** Run: scripts\run_python.bat -m tools.test_offline --only tests_approval. Run: scripts\run_python.bat -m tools.test_offline --only tests_publish. Run: scripts\run_python.bat -m tools.test_offline --only tests_pipeline_human. Expected before implementation: current content can freeze or submit without the new decisions.

- [ ] **Step 3: Wire the gates at the correct boundaries.** Validate before snapshots.freeze in approval.lock and surface an actionable lock_reason in approval.options. Revalidate under the publication lock in approval.approve. Gate the public CLI's --submit path after composition and before any browser attachment, and preflight the selected batch before its first submission. If a post can change while the composer is open, pass a required pre-submit callback from each real-submit caller to workflow and invoke it just before durable submit intent/click; keep callback absent for read-only preparation. Continue checking the existing snapshot fingerprint, paid consent, target, inventory, and duplicate-publication ledgers.

      if submit_enabled:
          require_current(account_dir, source, post)
      # Read-only candidate creation and preparation do not call this branch.

- [ ] **Step 4: Run gate tests and document exact limits.** Repeat the three test selections. Verify a scheduled post is never re-submitted and an old frozen post gains no invented record. Update docs with the new guard, its user recovery action, and offline-only evidence.

- [ ] **Step 5: Commit this tested slice.** Run git diff --check and commit with an English title such as feat(publish): require current manual content decisions.

### Task 3: Remove the hard lock on every publication hashtag

**Files:**
- Modify: core/localization.py, web/api/writer.py, web/ui/src/features/localization/model.ts, web/ui/src/features/localization/LocalizationEditor.tsx
- Test: tests/tests_localization.py, tests/tests_web_review.py, web/ui/src/features/localization/model.test.ts
- Docs: docs/FUNCTIONALITY.md, docs/REQUIREMENTS.md

**Interfaces:**
- Produces: editable publication tags including formerly protected brand/model values; source tags remain read-only reference and source differences remain visible.

- [ ] **Step 1: Write failing freedom and limit tests.** Save tags after removing a brand tag, replacing a model tag, adding an arbitrary valid tag, and selecting an empty list; assert the server preserves exactly the chosen order and values. Confirm that malformed tag syntax and the existing measured Instagram count limit still reject. In model.test.ts, assert conflict recovery does not silently reinsert a removed brand/model tag.

      draft["tags"] = ["#MeineWahl"]
      result = localization.validate(draft)
      self.assertNotIn("protected_tags_changed",
                       {issue["code"] for issue in result["issues"]})
      self.assertEqual(saved["localization"]["tags"], ["#MeineWahl"])

- [ ] **Step 2: Run tests to confirm red.** Run: scripts\run_python.bat -m tools.test_offline --only tests_localization. Run: scripts\run_python.bat -m tools.test_offline --only tests_web_review. Run: npm.cmd --prefix web/ui test -- src/features/localization/model.test.ts. Expected before implementation: protected-tag removal is restored or rejected.

- [ ] **Step 3: Remove only the protected-tag restriction.** Stop automatically merging protected_tags into edited or recovered publication tags. Remove protected_tags_changed from localization.validate and the writer's hard-reject set; retain hashtag syntax and measured platform limits. Require a tag decision when either source_tags or chosen tags are nonempty, including brand/model-only source tags whose chosen publication list is now empty. A post with no source or chosen tags needs no tag decision. Default confirmation to unconfirmed whenever a decision is needed. Keep source tags for the difference view and optional contextual information.

- [ ] **Step 4: Run tests and update docs.** Repeat the three selections. Verify final server caption uses the chosen tags exactly, including an empty set, and no old source tag reappears after a conflict refresh. Update the business and acceptance docs.

- [ ] **Step 5: Commit this tested slice.** Run git diff --check and commit with an English title such as feat(localization): allow all publication hashtags to be edited.

### Task 4: Build the compact shell and four-step state model

**Files:**
- Create: web/ui/src/pages/review-detail/step-model.ts and focused header/step components under the same folder
- Modify: web/ui/src/pages/review-detail/ReviewDetailPage.tsx, ReviewDetailPage.module.css, web/ui/src/app/search-params.ts
- Test: web/ui/src/pages/review-detail/step-model.test.ts, web/ui/src/pages/review-detail/tab-mounting.test.tsx, web/ui/src/app/search-params.test.ts
- Docs: docs/FUNCTIONALITY.md, docs/REQUIREMENTS.md

**Interfaces:**
- Consumes: TaskDetail.content_review from Task 1 and existing localization_validation, risk_scan, images, meta, status, and list context.
- Produces: StepId = text | images | localization | final; deriveSteps(detail) with completion and actionable to-dos; route search parameter tab accepts the fourth step and existing deep links.

- [ ] **Step 1: Write failing step-state and URL tests.** Assert the first incomplete step is selected when tab is absent; a valid tab deep link wins; no-image posts mark the image step “not required” without confirmation; tag/link issues point to localization; a body change points to text. Assert the first three to-dos are shown and full list expands. Assert source context contains author, original time, and permalink but no archive path; source account is not passed as target account.

      expect(defaultStep(detailWithMissingImageConfirmation)).toBe("images")
      expect(defaultStep(detailWithNoImages)).not.toBe("images")
      expect(parseDetailStep(new URLSearchParams("tab=final"))).toBe("final")

- [ ] **Step 2: Run focused frontend tests to confirm red.** Run: npm.cmd --prefix web/ui test -- src/pages/review-detail/step-model.test.ts src/pages/review-detail/tab-mounting.test.tsx src/app/search-params.test.ts. Expected before implementation: four-step model and default-incomplete behavior do not exist.

- [ ] **Step 3: Implement the shell with one source of step truth.** Add pure deriveSteps and defaultStep functions, using server confirmation state rather than a viewed-image Set. Keep URL-driven free navigation and existing list filters. Render one compact sticky header, source strip, editable auxiliary product category, at-most-three to-do summary, and horizontal four-step selector; narrow width changes to a compact selector. Render the source publish instant through ShanghaiTime until Task 11 unifies the shared formatter; do not slice the ISO date string. Hide normal/unknown runtime copy and remove storage/index/mirror data from the business canvas. For a filtered-out post, show the truthful message and disable unreliable adjacent navigation.

- [ ] **Step 4: Run focused tests and build.** Run the frontend selection above, then npm.cmd --prefix web/ui run build. Update relevant docs with actual layout and code/acceptance status.

- [ ] **Step 5: Commit this tested slice.** Run git diff --check and commit with an English title such as feat(review-ui): organize post detail into business review steps.

### Task 5: German body comparison, editing, and explicit confirmation

**Files:**
- Modify: web/ui/src/features/localization/TextWorkspace.tsx, TextWorkspace.module.css, web/ui/src/hooks/useLocalization.ts, web/ui/src/pages/review-detail/ReviewDetailPage.tsx
- Test: web/ui/src/features/localization/TextWorkspace.test.tsx, tests/tests_web_review.py
- Docs: docs/FUNCTIONALITY.md, docs/REQUIREMENTS.md

**Interfaces:**
- Consumes: TaskDetail.content_review.body and body-confirmation API from Task 1.
- Produces: body step with explicit empty/scan states and a separately saved confirmation bound to the current body.

- [ ] **Step 1: Add failing body-step tests.** Cover no German draft, untouched machine draft confirmation, editing/save without confirmation, source/body change invalidation, scanner not_scanned/failed/stale/completed-empty, and click/keyboard activation of a marked passage. Confirm the empty state offers manual entry and initial translation, not a machine-draft label.

      expect(bodyMarkup(noGermanDetail)).toContain("尚无德语文案")
      expect(bodyMarkup(noGermanDetail)).not.toContain("机器初译")
      expect(bodyMarkup(unscannedDetail)).not.toContain("无风险")

  Define bodyMarkup as a local test helper around the existing TextWorkspace renderer; avoid a production-only test hook.

- [ ] **Step 2: Run targeted frontend and API tests to confirm red.** Run: npm.cmd --prefix web/ui test -- src/features/localization/TextWorkspace.test.tsx. Run: scripts\run_python.bat -m tools.test_offline --only tests_web_review. Expected before implementation: missing confirmation and incorrect empty/scan behavior.

- [ ] **Step 3: Implement the readable body workspace.** Let content determine short-post panel height; cap long-post panel height and scroll each pane independently; stack on narrow screens. Keep deterministic and model findings distinguishable, accessible and explained at the passage. Place edit/save/discard and “确认当前德语正文” at the body-step end. A save waits for its response; then confirmation sends the resulting current revision. Allow confirmation of an untouched machine draft. If the scanner has no valid result, state that fact without blocking a human confirmation.

- [ ] **Step 4: Verify and document.** Repeat the two targeted selections; run npm.cmd --prefix web/ui run build because panel layout changed. Update docs with the confirmed body decision and scanner display semantics.

- [ ] **Step 5: Commit this tested slice.** Run git diff --check and commit with an English title such as feat(review-ui): make German body review explicit.

### Task 6: Review and confirm each proposed image

**Files:**
- Modify: web/ui/src/features/images/ImageWorkspace.tsx, ImageWorkspace.module.css, web/ui/src/pages/review-detail/ReviewDetailPage.tsx
- Modify: web/ui/src/services/review.ts; web/ui/src/services/jobs.ts only if the existing selection call must carry the atomic confirm flag
- Test: web/ui/src/features/images/ImageWorkspace.test.tsx, tests/tests_image_workflow_review.py, tests/tests_web_review.py
- Docs: docs/FUNCTIONALITY.md, docs/REQUIREMENTS.md

**Interfaces:**
- Consumes: TaskDetail.content_review.images[index] and the version-bound confirmation endpoints from Task 1.
- Produces: one image decision per source image; choosing original plus confirmation is one server transaction, while upload or version adoption clears that image decision.

- [ ] **Step 1: Write failing interaction and API tests.** In ImageWorkspace.test.tsx, distinguish “viewed” from “confirmed”; assert numbered thumbnails show independent confirmation state, a single image has no redundant previous/next controls, and no-image detail says “无需审核图片”. Exercise missing German image, source-original selection, upload, version adoption, and a failed source-image request. In tests_image_workflow_review.py and tests_web_review.py, assert upload/adoption clears only that image confirmation and an unreadable source cannot be confirmed.

      expect(screen.getByRole('button', { name: /确认第 2 张图片用于发布/ })).toBeEnabled()
      expect(screen.getByRole('button', { name: /第 1 张.*已确认/ })).toBeVisible()
      expect(screen.queryByText('dHash 距离')).not.toBeInTheDocument()

- [ ] **Step 2: Run the focused tests and confirm red.** Run: npm.cmd --prefix web/ui test -- src/features/images/ImageWorkspace.test.tsx. Run: scripts\run_python.bat -m tools.test_offline --only tests_image_workflow_review. Run: scripts\run_python.bat -m tools.test_offline --only tests_web_review. Expected before implementation: viewed state is still displayed as progress and there is no independent proposed-image confirmation.

- [ ] **Step 3: Replace the image interaction.** Keep one source/proposed pair at a time with object-fit containment and original aspect ratio. Number thumbnails and derive their status from content_review, not the local seen Set. If no German image is ready, show a genuine empty proposed slot with actions to use the original, upload, or generate; do not silently show the original as the proposal. At the step end, submit explicit confirmation for the current selected publication image. Wire “使用原图并确认” through the atomic select-and-confirm API. Hide metrics, model name, raw paths, cost, and replacement timestamps; make version comparison an optional visual current-versus-candidate dialog with a short human instruction. Keep only concise, uncertain warnings for visible-change or shape concerns, and show affected image number plus recovery action when bytes are missing.

      const confirmed = detail.content_review.images.find(item => item.index === image.index)?.confirmed === true
      const proposedUrl = image.ready || image.selection === 'original_confirmed'
        ? image.de_url || image.original_url
        : null
      // proposedUrl === null renders an empty proposal state, never a source-image clone.

- [ ] **Step 4: Run focused checks and document.** Repeat the three test selections and run npm.cmd --prefix web/ui run build. Check that a newly selected version remains unconfirmed until the staff member confirms it, while other image decisions remain current. Update FUNCTIONALITY and REQUIREMENTS with the landed image states and offline-only evidence.

- [ ] **Step 5: Commit this tested slice.** Run git diff --check and commit with an English title such as feat(review-ui): make image decisions explicit.

### Task 7: Make publication tags and links a single business review step

**Files:**
- Modify: web/ui/src/features/localization/LocalizationEditor.tsx, LocalizationEditor.module.css, web/ui/src/hooks/useLocalization.ts
- Modify: web/ui/src/pages/review-detail/ReviewDetailPage.tsx, web/ui/src/features/localization/TextWorkspace.tsx
- Test: Create web/ui/src/features/localization/LocalizationEditor.test.tsx; modify tests/tests_localization.py, tests/tests_web_review.py
- Docs: docs/FUNCTIONALITY.md, docs/REQUIREMENTS.md

**Interfaces:**
- Consumes: unrestricted publication tags from Task 3 and existing localization.tags, links, hashtags_confirmed, links_confirmed, ig_cta, and ig_bio_url.
- Produces: one visible “完成标签与链接审核” action that saves two independent confirmation flags; Facebook numbered source/target link rows remain in original order.

- [ ] **Step 1: Write failing step tests.** Test brand/model tag deletion, arbitrary tag addition, and an intentionally empty tag list in the editor. The difference view appears only when chosen tags differ from source tags. For Facebook, assert each source URL is paired with its target, open-target and missing-target actions are present, and an insert-link action keeps cursor position while the exact target URL appears in the server caption. For Instagram, assert a chosen bio CTA without a configured bio target shows a warning but allows confirmation. In API tests assert one visible completion call can persist hashtags_confirmed and links_confirmed independently and that a changed section invalidates only its own flag.

      expect(screen.getByRole('button', { name: '完成标签与链接审核' })).toBeEnabled()
      expect(screen.getByText('主页目标尚未配置')).toBeVisible()
      expect(screen.getByRole('link', { name: /打开德语落地页/ })).toHaveAttribute('href', targetUrl)

- [ ] **Step 2: Run targeted tests and confirm red.** Run: npm.cmd --prefix web/ui test -- src/features/localization/LocalizationEditor.test.tsx. Run: scripts\run_python.bat -m tools.test_offline --only tests_localization. Run: scripts\run_python.bat -m tools.test_offline --only tests_web_review. Expected before implementation: brand/model tags remain visually locked and the step has two separate checkboxes.

- [ ] **Step 3: Build the business editing flow.** Render all current tags as removable/editable, with source tags in a small comparison only when different; preserve the source list as reference. Keep platform syntax and count errors inline. For Facebook, render one numbered row per existing source URL, edit only its target, retain order, and use the existing cursor-insertion behavior behind a plain “插入链接 1” action. Use the server-rendered caption from POST /api/tasks/{task_id}/check for final displayed links, including the one-time append of any valid unused target; do not build an approximate caption in the browser. For Instagram, place the bio-target warning beside the CTA and repeat it in final confirmation without adding a block. Replace two visible confirmation checkboxes with one step-end action; send both flags in the existing localization save transaction when their section needs a decision, then let server validation report any still-missing Facebook target. A source list intentionally reduced to zero chosen tags still needs its explicit decision; a genuinely empty source/current section does not. A changed tag or link clears only its own confirmation.

      const needsTagDecision = draft.source_tags.length > 0 || draft.tags.length > 0
      const needsLinkDecision = draft.links.length > 0 || draft.ig_cta.trim().length > 0
      const next = {
        ...draft,
        hashtags_confirmed: needsTagDecision ? true : draft.hashtags_confirmed,
        links_confirmed: needsLinkDecision ? true : draft.links_confirmed,
      }
      // Save through the existing localization revision/CAS path; validation decides final eligibility.

- [ ] **Step 4: Verify and document.** Repeat the targeted tests, then run npm.cmd --prefix web/ui run build. Check empty tags, missing Facebook target, and missing Instagram bio target separately. Update docs with editable-tag and link-step semantics.

- [ ] **Step 5: Commit this tested slice.** Run git diff --check and commit with an English title such as feat(review-ui): simplify tag and link approval.

### Task 8: Keep model tools contextual and remove technical display

**Files:**
- Modify: web/ui/src/features/content-jobs/ContentJobs.tsx, ContentJobs.module.css, web/ui/src/features/localization/SuggestionPanel.tsx, SuggestionPanel.module.css
- Modify: web/ui/src/features/localization/LocalizationEditor.tsx, web/ui/src/components/PaidActionButton.tsx, web/ui/src/pages/review-detail/ReviewDetailPage.tsx
- Test: web/ui/src/features/localization/SuggestionPanel.test.ts, web/ui/src/features/images/ImageWorkspace.test.tsx; create web/ui/src/features/content-jobs/ContentJobs.test.tsx if existing tests do not cover contextual consent
- Docs: docs/FUNCTIONALITY.md, docs/REQUIREMENTS.md

**Interfaces:**
- Consumes: existing model capability, paid-consent, remaining image-attempt, and job-status APIs without changing their server gates.
- Produces: secondary translation and revision controls only in the body step, generation/revision controls only in the image step, and optional short tag suggestions only in the localization step.

- [ ] **Step 1: Add failing display and consent tests.** Test that the normal detail DOM contains no currency values, fee estimates, model names, diagnostic drawer trigger, processing-basis link, template JSON, raw signal statistics, or sampling timestamp. Assert image refinement shows only the accurate remaining attempt count and a contextual quota note. For a third-party source, opening an eligible paid action reveals the source-consent explanation and requires explicit consent before the existing request; dismissing it leaves the detail uncluttered. Assert suggestions cannot change publication tags until explicitly selected and applied.

      expect(screen.queryByText(/按实际用量计费|费用估计|dHash|采样批次|模型：/)).not.toBeInTheDocument()
      expect(screen.getByText(/还可调整 2 次/)).toBeVisible()
      expect(screen.getByRole('button', { name: '采用所选建议' })).toBeEnabled()

- [ ] **Step 2: Run focused frontend tests to confirm red.** Run: npm.cmd --prefix web/ui test -- src/features/localization/SuggestionPanel.test.ts src/features/images/ImageWorkspace.test.tsx src/features/content-jobs/ContentJobs.test.tsx. Expected before implementation: the detail still renders model and money copy or technical evidence.

- [ ] **Step 3: Simplify the contextual tools.** Move initial translation and text refinement actions into the body step; locate image generation/refinement in the image step. Keep their existing async pending/running/interrupted/succeeded/failed handling. Replace amount/model/template/technical text with a short task-related label, accurate remaining image attempts, and an optional “使用模型额度” note. Show third-party source and permalink in the source strip; ask for source consent only when the paid action begins and preserve the server-side consent, budget, and license checks. In hashtag suggestions show candidate German tags and the grounded one-line relationship to each group.source_tag (for example “原帖 #Cats 的德语候选”); the current API has no candidate.reason and the UI must not invent popularity or effectiveness claims. Keep selection explicitly optional; hide trend values, sampling details, comparison groups, and raw evidence from the UI. Remove the normal technical drawer trigger from this route rather than exposing backend JSON under a new label.

      const visibleCandidate = {
        tag: candidate.tag,
        description: '原帖 ' + group.source_tag + ' 的德语候选',
      }
      // The full API result may remain available to server logic; no raw signals enter the rendered card.

- [ ] **Step 4: Verify and document.** Repeat the focused tests and run npm.cmd --prefix web/ui run build. Verify no change to paid endpoint eligibility in the existing Python tests if a service call contract changed; otherwise keep this slice frontend-only. Update docs with the new business presentation and preserved server gates.

- [ ] **Step 5: Commit this tested slice.** Run git diff --check and commit with an English title such as feat(review-ui): simplify contextual model tools.

### Task 9: Exact final review, schedule choice, and outcome states

**Files:**
- Modify: web/ui/src/features/approval/DecisionPanel.tsx, DecisionPanel.module.css, web/ui/src/features/approval/SchedulePreviewDialog.tsx, SchedulePreviewDialog.module.css
- Modify: web/ui/src/hooks/useApproval.ts, web/ui/src/pages/review-detail/ReviewDetailPage.tsx; modify web/api/reader.py only if exact pre-freeze caption is not already returned
- Test: tests/tests_approval.py, tests/browser_schedule_preview.py; create web/ui/src/features/approval/DecisionPanel.test.tsx
- Docs: docs/FUNCTIONALITY.md, docs/REQUIREMENTS.md, docs/HANDOFF.md

**Interfaces:**
- Consumes: approval-options.preview, lock_reason, earliest/latest, target, channel, schedule operation, and current confirmation state from Tasks 1–2.
- Produces: a compact final summary, an on-demand exact server caption/copy action, a frozen submit dialog, and status-specific read-only outcomes.

- [ ] **Step 1: Add failing final-step and freeze tests.** Assert an unconfirmed body or image yields a linked outstanding task rather than a dead disabled freeze button. Assert exact caption preview/copy equals the server response, character count appears only near a limit or on validation error, and an Instagram missing-bio warning repeats here without blocking. Check the selected time, allowed range, nearby same-channel conflict/uncertainty, suggested alternatives, and lack of cache-diagnostic text. In the browser fixture, freeze a three-image caption, mutate the subsequent server response while the dialog is open, and assert the displayed text/target/order/time remain pinned to the frozen snapshot. Test running, uncertain, scheduled, snoozed, skipped, and handed-off states; uncertain state has reconciliation but no duplicate-submit action.

      expect(screen.getByRole('button', { name: '查看完整发布文案' })).toBeVisible()
      expect(screen.queryByText('依据缓存')).not.toBeInTheDocument()
      expect(screen.getByText('提交结果待核对')).toBeVisible()
      expect(screen.queryByRole('button', { name: '再次提交' })).not.toBeInTheDocument()

- [ ] **Step 2: Run targeted tests and confirm red.** Run: scripts\run_python.bat -m tools.test_offline --only tests_approval. Run: npm.cmd --prefix web/ui test -- src/features/approval/DecisionPanel.test.tsx. Run the isolated browser scenario only after the frontend build exists: scripts\run_python.bat tests\browser_regression.py --stage SCHEDULE_PREVIEW. Expected before implementation: final business summary and status-specific states are absent.

- [ ] **Step 3: Implement the final step and immutable dialog.** Show source account only in source context and the chosen publication target only in final confirmation. The final step lists body, image, tag, link, and target/time readiness with direct step links. Before freeze, read the exact caption from the existing POST /api/tasks/{task_id}/check response using the current saved localization draft; show it on demand and copy those exact bytes, never a browser reconstruction. While an unsaved draft exists, require save and a fresh server check before offering freeze. After freeze, keep the frozen SchedulePreviewDialog bound to the original snapshot while open and display exact caption, ordered image gallery, target account, sole channel, and chosen date/time. Time control displays the accepted range, only nearby same-channel conflict or uncertainty, and server alternatives; live inventory recheck remains in the submit path. A running operation shows progress; an uncertain operation offers only reconciliation; scheduled clearly means a remotely confirmed schedule, not public delivery. Reopened frozen goes to time choice, snoozed to wake, and skipped/handed-off to concise decision plus reason; finished states drop the unusable four-step checklist.

      const preview = controller.snapshot?.preview
      const caption = preview?.text ?? ''
      const imageUrls = preview?.images.map(image => image.url) ?? []
      // Keep this snapshot object pinned for the lifetime of the confirmation dialog.

- [ ] **Step 4: Verify at API, component, and browser levels.** Repeat the approval and frontend tests, run npm.cmd --prefix web/ui run build, then run the registered schedule-preview browser stage from Step 2. Check keyboard focus and dialog escape/cancel behavior, including a missing preview image. Update docs with exact fixture evidence and the fact that live Business Suite behavior remains unverified.

- [ ] **Step 5: Commit this tested slice.** Run git diff --check and commit with an English title such as feat(review-ui): present exact scheduling decisions.

### Task 10: Safe navigation, business actions, and final page integration

**Files:**
- Create: web/ui/src/app/review-draft-actions.ts
- Modify: web/ui/src/hooks/useUnsavedChangesGuard.tsx, web/ui/src/hooks/useLocalization.ts, web/ui/src/lib/unsaved-changes.ts
- Modify: web/ui/src/features/review-actions/ReviewActions.tsx, web/ui/src/features/diagnostics/DetailDrawers.tsx, web/ui/src/app/AppShell.tsx
- Modify: web/ui/src/pages/review-detail/ReviewDetailPage.tsx, ReviewDetailPage.module.css
- Test: web/ui/src/lib/unsaved-changes.test.ts, web/ui/src/pages/review-detail/tab-mounting.test.tsx, tests/browser_regression.py
- Docs: docs/FUNCTIONALITY.md, docs/REQUIREMENTS.md, docs/HANDOFF.md

**Interfaces:**
- Consumes: draft dirty/save/discard behavior from useLocalization, deploymentStore's existing dirty signal, router blocker, status-specific detail state, and the four-step model.
- Produces: save/continue/discard on actual departure, step changes without draft loss, business-only “more” actions/history, and one responsive integrated detail view.
- Produces: registerReviewDraftActions(taskId, { save: () => Promise<'saved' | 'still_dirty' | 'failed'>, discard: () => void }) in review-draft-actions.ts. The registration is removed when the detail unmounts.

- [ ] **Step 1: Add failing navigation and business-history tests.** Navigate between the four steps while body or tags are dirty and assert no leave prompt and no draft reset. Try return, prior/next, and browser back while dirty; assert continue cancels departure, save waits for successful save then leaves, and discard restores the last persisted draft then leaves. If save fails, remain on the same post with the draft intact. Test the more menu for snooze, wake, skip, handoff, and download, and its handling-history view for meaningful action/time/reason only. At narrow width assert no page-wide horizontal scroll and that the step selector and warning state remain usable.

      expect(screen.getByRole('button', { name: '继续编辑' })).toBeVisible()
      expect(screen.getByRole('button', { name: '保存并离开' })).toBeVisible()
      expect(screen.getByRole('button', { name: '放弃修改并离开' })).toBeVisible()

- [ ] **Step 2: Run focused tests and confirm red.** Run: npm.cmd --prefix web/ui test -- src/lib/unsaved-changes.test.ts src/pages/review-detail/tab-mounting.test.tsx. Expected before implementation: the existing guard only offers leave/stay and the three-way draft action is absent.

- [ ] **Step 3: Wire departure actions without losing content.** Keep the existing deployment-store dirty signal as the one navigation source. Register the current detail's save and discard callbacks with the guard while that detail is mounted; step-selector clicks change only the step parameter and remain in place, while changes to task ID or list route are blocked. Change useLocalization.save to return an explicit result because its current Promise resolves even after a caught save error. A save result is “saved” only after the response applied and no newer input remains; otherwise remain on the post with the draft intact. Let discard restore the persisted revision before proceeding; reset the blocker on continue. Preserve return-list context and truthful adjacency from Task 4. Move secondary review actions under one “更多” menu. Replace the technical drawer with a business handling trail that filters routine processing entries and renders only decision, date/time, and reason; show verified runtime failures at their action, not as a permanent global “unknown” banner. Apply the blue/white neutral tokens, clear focus indicators, light separators, and compact spacing across four steps and statuses.

      if (choice === 'save') {
        const result = await actions.save()
        if (result === 'saved') blocker.proceed?.()
      } else if (choice === 'discard') {
        actions.discard()
        blocker.proceed?.()
      } else {
        blocker.reset?.()
      }

- [ ] **Step 4: Run focused build and browser checks.** Repeat the frontend selection and run npm.cmd --prefix web/ui run build. Update the existing isolated detail stages D1–D5 for the four-step controls and add short/long body, one/multiple/no images, missing image bytes, third-party source, dirty navigation, filtered-out adjacency, and each completed status to stage D. Run scripts\run_python.bat tests\browser_regression.py --stage D and the affected D1–D5 stages; do not run ALL without a concrete need. Use fixture screenshots at desktop and narrow widths to inspect spacing and absence of visible money, model, storage, raw JSON, and place/time-zone words. Record exact commands, fixture nature, and results in HANDOFF; update FUNCTIONALITY/REQUIREMENTS to the achieved status.

- [ ] **Step 5: Commit the integrated slice.** Run git diff --check, inspect status and generated files, and commit with an English title such as feat(review-ui): integrate business review detail.

### Task 11: Make every displayed business time use one clock basis

**Files:**
- Modify: web/ui/src/lib/format.ts, web/ui/src/components/Time.tsx
- Modify: web/ui/src/pages/review-detail/ReviewDetailPage.tsx, web/ui/src/features/approval/DecisionPanel.tsx, web/ui/src/features/approval/SchedulePreviewDialog.tsx only where they still slice ISO strings or show source instants without conversion
- Modify: web/ui/src/pages/calendar/CalendarPage.tsx and web/ui/src/features/post-list/columns.tsx only where shared formatter behavior reveals an inconsistent call site
- Test: web/ui/src/lib/format.test.ts, web/ui/src/components/Time.test.tsx, web/ui/src/features/post-list/columns.test.tsx, web/ui/src/pages/calendar/CalendarPage.test.tsx, tests/browser_schedule_preview.py
- Docs: docs/FUNCTIONALITY.md, docs/REQUIREMENTS.md

**Interfaces:**
- Consumes: ISO instants with explicit Z or offset, already-business-wall datetime-local values without offset, and the configured Asia/Shanghai business basis.
- Produces: formatSchedule(iso), formatDate(iso), BusinessTime, and ShanghaiTime with consistent business-facing dates and hours; parsing and submission retain the actual instant and original offset as backend data.

- [ ] **Step 1: Add failing cross-boundary time tests.** Assert ISO instants with Z, +02:00, and +08:00 show the same Asia/Shanghai clock for the same instant; a near-midnight UTC source post changes to the correct next-day business date. Assert an already-business-wall datetime-local value is displayed as entered, rather than parsed as browser local time. Check a frozen preview, history row, list status time, and calendar header without any system-added city/time-zone label. Keep original post body text untouched even if it mentions a place.

      expect(formatSchedule('2026-09-24T16:30:00+02:00')).toContain('22:30')
      expect(formatSchedule('2026-09-24T14:30:00Z')).toContain('22:30')
      expect(formatDate('2026-09-24T20:30:00Z')).toBe('2026-09-25')
      expect(formatSchedule('2026-09-25T04:30')).toContain('04:30')

- [ ] **Step 2: Run targeted time and caller tests to confirm red.** Run: npm.cmd --prefix web/ui test -- src/lib/format.test.ts src/components/Time.test.tsx src/features/post-list/columns.test.tsx src/pages/calendar/CalendarPage.test.tsx. Expected before implementation: formatSchedule and formatDate preserve the written ISO date/hour instead of converting an offset instant.

- [ ] **Step 3: Normalize system-generated time display.** In formatSchedule, detect an explicit offset or Z and convert that instant through zonedInput(iso, BUSINESS_TIMEZONE) before composing day, weekday, and hour; retain no-offset datetime-local strings as already-business-wall inputs. Make formatDate convert an offset instant before taking its date. Keep the semantic time element's original dateTime attribute for machines. Replace detail source-time substring calls with the shared formatter. Audit rendered web UI time labels and API-provided help copy for system-added “上海”, “上海时间”, “柏林”, “北京时间”, and raw time-zone names; remove place words in user-facing metadata, without editing source-post text, comments, fixture data, or the real timezone configuration. Retain the server's DST, inventory, and scheduled-at checks.

      const wall = /(?:Z|[+-]\d{2}:\d{2})$/i.test(iso)
        ? zonedInput(iso, BUSINESS_TIMEZONE)
        : iso
      // Format wall; do not interpret a datetime-local value in the browser's timezone.

- [ ] **Step 4: Verify and document.** Repeat the targeted time tests, run npm.cmd --prefix web/ui run build, and run scripts\run_python.bat tests\browser_regression.py --stage SCHEDULE_PREVIEW. Confirm a server offset instant and local time input refer to the intended moment and the visible output has no system-added place names. Update the business-time contract and offline acceptance evidence.

- [ ] **Step 5: Commit this tested slice.** Run git diff --check and commit with an English title such as fix(review-ui): unify displayed business times.

## Completion review

Trace D01–D49 from the spec to the task coverage table and to tested behavior. Run only the impacted Python selections, frontend tests/build, and the relevant isolated browser stages; expand testing only for a concrete gap or failure. Check that the business detail contains no fee, model identifier, technical diagnostics, storage paths, or raw evidence, and that system-generated time text throughout the web UI contains no place/time-zone names. Run the relevant hygiene test because new functions and endpoints span Python modules. Update all four project docs where their contracts, acceptance rows, evidence, or manual instructions changed. Report exact test commands and fixture-only limits. Do not claim live paid service or real publication acceptance from offline results.
