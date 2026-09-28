# History Media Type Filter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (- [ ]) syntax for tracking.

**Goal:** Give historical archive rows honest, visually coherent media previews and one server-backed filter for the actual source-post type.

**Architecture:** Derive post_type from archived source media and body, materialize it in the rebuildable display index, and apply it in both indexed and archive-file queries before pagination. Keep preview_kind and thumbnail_url separate; React uses them together only to render a row. Preserve filter state through list and detail URLs.

**Tech Stack:** Python 3.12, FastAPI, SQLite derived display index, React 19, TypeScript, Ant Design 6, Vitest, isolated Python unittest and Playwright fixtures.

**Spec:** ../specs/2026-09-24-history-media-type-filter.md

## Global Constraints

- Use the existing worktree and branch codex/history-media-filter-thumbnails. Start each execution session with git status and do not edit another worktree.
- post.json and all JSONL ledgers are truth sources. The SQLite index is a derived projection; never use it to overwrite archive content.
- Classify only from already archived evidence. No new platform browsing, history scrolling, video-cover fetch, paid call, or real publication.
- Keep list thumbnail footprint at 40 px and row height at 48 px. Use semantic theme tokens, accessible labels, and a visible “all types” option.
- Combine platform, month, category, and post type before pagination, and preserve their URL context for details, return, and adjacent navigation.
- Make small commits with English Conventional Commit titles and the configured Git identity. Add no AI attribution.
- Update docs/FUNCTIONALITY.md and docs/REQUIREMENTS.md with behavior and truthful acceptance state as each task lands. Offline tests establish only offline acceptance.

## Review Focus

1. Incomplete or unknown source media must classify as pending rather than silently becoming text-only; a fully enumerated source image whose bytes failed to download retains its image type. Task 1 tests this.
2. A mixed image/video post with a usable still must remain mixed while showing that still. Tasks 1 and 3 test this.
3. Missing local image bytes or HTTP 404 must change the preview only, not the type-filter result. Tasks 1 and 3 test this.
4. Indexed and archive-file fallback must agree for a combined type/month/platform/category filter and page total. Task 1 tests this.
5. Invalid type URL values must become “all types,” and returning from detail must keep a valid type and page. Task 2 tests this.

## File map and interfaces

| Responsibility | Files |
| --- | --- |
| Pure archive type classifier | Create core/post_type.py |
| Derived index schema and filter | Modify core/index_db.py and web/api/query_index.py |
| API parameter, response field, fallback | Modify web/api/app.py and web/api/reader.py |
| Browser query and types | Modify web/ui/src/app/search-params.ts, web/ui/src/types/domain.ts, web/ui/src/services/tasks.ts, web/ui/src/services/assert-shape.ts |
| List control and detail context | Modify web/ui/src/pages/history/HistoryPage.tsx, web/ui/src/features/post-list/ListFilters.tsx, web/ui/src/pages/review-detail/ReviewDetailPage.tsx |
| Thumbnail visuals and tokens | Modify web/ui/src/features/post-list/columns.tsx, columns.module.css, web/ui/src/app/theme.ts |
| Targeted tests | Modify tests/tests_history.py, web/ui/src/app/search-params.test.ts, web/ui/src/types/shape.test.ts, web/ui/src/features/post-list/columns.test.tsx, tests/browser_regression.py |

| 实施任务 | 对应已确认决策 | 可独立检查的交付结果 |
| --- | --- | --- |
| 1. 来源类型与服务端筛选 | H50、H53 | 六类派生类型；缺图不改类型；索引和文件回退先筛后分页 |
| 2. 筛选控件及导航上下文 | H52 | 全部类型与六类选项；平台／月份／产品分类组合；详情返回保留筛选 |
| 3. 缩略图插画 | H51、H53 | 40 像素插画与混合帖真实图片播放提示；404 回退不伪造内容 |

### Task 1: Source-backed type and server filtering

**Files:**
- Create: core/post_type.py
- Modify: core/index_db.py, web/api/query_index.py, web/api/reader.py, web/api/app.py
- Test: tests/tests_history.py
- Docs: docs/FUNCTIONALITY.md, docs/REQUIREMENTS.md

**Interfaces:**
- Produces: classify_post(row: Mapping[str, Any]) -> PostType; PostType values static_image_text, image_only, video, image_video, text_only, pending.
- Produces: query_index.history_page(..., post_type: str | None = None, ...) and index_db.query_page(..., post_type: str | None = None, ...).
- Produces: history API row post_type and a pre-pagination post_type filter. preview_kind and thumbnail_url keep their existing meanings.

- [ ] **Step 1: Add failing classifier, API, and parity tests.** Extend tests/tests_history.py with a fixture matrix that asserts:

      self.assertEqual(classify_post({"source_media_complete": True, "media": [
          {"kind": "image"}], "text": "Caption"}), "static_image_text")
      self.assertEqual(classify_post({"source_media_complete": True, "media": [
          {"kind": "image"}], "text": ""}), "image_only")
      self.assertEqual(classify_post({"source_media_complete": True, "media": [
          {"kind": "video"}], "text": "Caption"}), "video")
      self.assertEqual(classify_post({"source_media_complete": True, "media": [
          {"kind": "image"}, {"kind": "video"}], "text": ""}), "image_video")
      self.assertEqual(classify_post({"source_media_complete": True, "media": [],
          "text": "Caption"}), "text_only")
      self.assertEqual(classify_post({"source_media_complete": False, "media": [],
          "text": "Caption"}), "pending")
      self.assertEqual(classify_post({"source_media_complete": True,
          "media_complete": False, "media": [{"kind": "image", "local_path": None}],
          "text": "Caption"}), "static_image_text")
      self.assertEqual(classify_post({"source_media_complete": True,
          "source_media_count": 2, "media": [{"kind": "image"}],
          "text": "Caption"}), "pending")

  Add an API test with all six categories: GET /api/tasks?scope=history&post_type=image_video&limit=1 returns only mixed rows, correct total, and post_type=image_video. Repeat under a forced stale-index fallback and assert same ordered IDs and total. Assert an image source with no local bytes has an image type and preview_kind=image_pending. For a malformed API post_type value, assert the same rows and total as the unfiltered “all types” query.

- [ ] **Step 2: Run the targeted Python tests and see the intended failure.** Run: scripts\run_python.bat -m tools.test_offline --only tests_history. Expected before implementation: missing classifier or filter support.

- [ ] **Step 3: Implement the pure classifier and materialized index field.** Add a PostType Literal and the following decision order to core/post_type.py:

      def classify_post(row: Mapping[str, Any]) -> PostType:
          media = row.get("media")
          source_complete = row.get("source_media_complete")
          if source_complete is None:
              source_complete = row.get("media_complete", True)
          if source_complete is not True or not isinstance(media, list):
              return "pending"
          expected = row.get("source_media_count")
          if expected is not None and (type(expected) is not int
                                       or expected < 0 or expected != len(media)):
              return "pending"
          kinds = [item.get("kind") for item in media if isinstance(item, Mapping)]
          if len(kinds) != len(media) or any(kind not in {"image", "video"} for kind in kinds):
              return "pending"
          if "image" in kinds and "video" in kinds:
              return "image_video"
          if "video" in kinds:
              return "video"
          if "image" in kinds:
              return "static_image_text" if str(row.get("text") or "").strip() else "image_only"
          return "text_only" if str(row.get("text") or "").strip() else "pending"

  Increment core/index_db.py SCHEMA_VERSION from 2 to 3. Project this field into posts.post_type, row_json, and the index's expected columns; add an index useful for type plus created_at. Ensure existing SQLite files lacking the new column are rebuilt through the existing derived-index recovery path. Apply the same classifier in the stale-index archive scan. Normalize any absent or invalid history post_type parameter to no type filter; retain the existing review scope behavior. Apply type filtering before LIMIT/OFFSET, including in the fallback, then return post_type per row.

- [ ] **Step 4: Run the targeted tests, then document the landed contract.** Run: scripts\run_python.bat -m tools.test_offline --only tests_history. Expected: classifier matrix, index/fallback parity, pagination, and existing history tests pass. Update FUNCTIONALITY with the post-type definitions and distinction from preview_kind; add an honest code/acceptance row to REQUIREMENTS.

- [ ] **Step 5: Commit this tested slice.** Run git diff --check, inspect git status and staged files, then commit with an English title such as feat(history): classify archived post media types.

### Task 2: Filter control and URL context

**Files:**
- Modify: web/ui/src/app/search-params.ts, web/ui/src/types/domain.ts, web/ui/src/services/tasks.ts, web/ui/src/services/assert-shape.ts
- Modify: web/ui/src/pages/history/HistoryPage.tsx, web/ui/src/features/post-list/ListFilters.tsx, web/ui/src/pages/review-detail/ReviewDetailPage.tsx
- Test: web/ui/src/app/search-params.test.ts, web/ui/src/types/shape.test.ts, tests/tests_history.py
- Docs: docs/FUNCTIONALITY.md, docs/REQUIREMENTS.md

**Interfaces:**
- Consumes: server post_type values and server-side filter from Task 1.
- Produces: HistoryListQuery.post_type: PostType | null; parsePostType(value: string | null | undefined): PostType | null; history URL context includes post_type.

- [ ] **Step 1: Add failing query and navigation tests.** In search-params.test.ts, assert parseHistoryListQuery("post_type=video&page=3") keeps video and page 3, an invalid value becomes null, and buildDetailSearch/buildListSearch preserve post_type with platform/month/tag/page/limit. In shape.test.ts, assert post_type is required on each history row. In tests_history.py, assert invalid API post_type yields the same rows and total as “all types”.

      expect(parseHistoryListQuery(new URLSearchParams("post_type=video&page=3")).post_type)
        .toBe("video")
      expect(parseHistoryListQuery(new URLSearchParams("post_type=bogus")).post_type)
        .toBeNull()
      expect(buildListSearch(new URLSearchParams("post_type=image_video&page=2"), "history"))
        .toContain("post_type=image_video")

- [ ] **Step 2: Run the relevant frontend and API tests to confirm red.** Run: npm.cmd --prefix web/ui test -- src/app/search-params.test.ts src/types/shape.test.ts. Run: scripts\run_python.bat -m tools.test_offline --only tests_history. Expected before implementation: missing post_type contract.

- [ ] **Step 3: Connect the control to the server query and detail context.** Extend HISTORY_CONTEXT_KEYS and HistoryListQuery, sanitize post_type, and send it through listHistoryTasks. Add a required HistoryListItem.post_type plus an explicit shape validator and fixture values. Add “全部类型”, “静态图文”, “纯图片”, “视频”, “图片＋视频”, “纯文字”, and “类型待核对” to the history-only ListFilters dropdown. HistoryPage.change accepts post_type and resets page to 1; page-size changes retain the active type. The detail route uses the same HistoryListQuery for adjacent-row lookup and the same context serializer for return links.

      const POST_TYPES = [
        "static_image_text", "image_only", "video",
        "image_video", "text_only", "pending",
      ] as const
      const parsePostType = (value: string | null) =>
        POST_TYPES.find(type => type === value) ?? null

- [ ] **Step 4: Run targeted tests and build.** Run: npm.cmd --prefix web/ui test -- src/app/search-params.test.ts src/types/shape.test.ts. Run: npm.cmd --prefix web/ui run build. Verify combined filters remain in URL after page and detail navigation. Update affected FUNCTIONALITY/REQUIREMENTS lines in the same slice.

- [ ] **Step 5: Commit this tested slice.** Run git diff --check and commit with an English title such as feat(history): filter archive by source post type.

### Task 3: Rich, honest thumbnail illustrations

**Files:**
- Modify: web/ui/src/features/post-list/columns.tsx, columns.module.css, web/ui/src/app/theme.ts
- Test: web/ui/src/features/post-list/columns.test.tsx, tests/browser_regression.py
- Docs: docs/FUNCTIONALITY.md, docs/REQUIREMENTS.md

**Interfaces:**
- Consumes: HistoryListItem.post_type and independent preview_kind from Tasks 1–2.
- Produces: type-specific illustrated thumbnail only for history rows lacking a real image, and a small play overlay for a mixed row with a real still. Existing review-list rows remain valid without post_type.

- [ ] **Step 1: Add failing rendering tests.** In columns.test.tsx render a video, text-only, image/video mixed with still, mixed without still, and static-image row with an image 404 fallback. Assert the mixed real image retains its image element plus a play cue; the 404 switches to image-pending artwork but keeps the accessible mixed type; illustrative rows have no image URL. Check each placeholder carries an accessible label and icon-only decoration is hidden from assistive technology.

      expect(cell(historyColumns(), 'thumbnail', { ...historyRow,
        post_type: "image_video", thumbnail_url: "/api/tasks/example/image/0",
        preview_kind: "image" })).toContain("图片＋视频")
      expect(cell(historyColumns(), 'thumbnail', { ...historyRow,
        post_type: "text_only", thumbnail_url: "", preview_kind: "text" }))
        .not.toContain("<img")

  Define historyRow in the test file as a complete HistoryListItem fixture and use the existing cell helper; do not add a production-only test export.

- [ ] **Step 2: Run the rendering test to verify red.** Run: npm.cmd --prefix web/ui test -- src/features/post-list/columns.test.tsx. Expected before implementation: no post-type visual and no mixed overlay.

- [ ] **Step 3: Implement CSS-token-based miniatures.** Add semantic thumbnail colors to theme.ts and matching CSS variables. Keep .thumb at 40 x 40 and preserve the table's 48 px rows. Render a framed play scene, paper/text strokes, or split scene by post_type; if preview_kind=image and the real URL loads, render the real image and overlay a small play cue only for image_video. On image error, show the image-pending art without retry; use post_type for its accessible type cue. Do not download, decode, or request remote video frames.

- [ ] **Step 4: Verify the behavior at component and browser levels.** Run: npm.cmd --prefix web/ui test -- src/features/post-list/columns.test.tsx. Run: npm.cmd --prefix web/ui run build. Extend the isolated browser stage E fixture with the six types, two viewport widths, and a real 404 thumbnail; add post_type to every required HistoryListItem fixture row. Run scripts\run_python.bat tests\browser_regression.py --stage E. Confirm row height, no layout shift, correct filter-return context, and no image request for illustrated rows. Run tests\history_thumbnail_cost.py only if request accounting changed. Update docs with the new visuals and fixture-only evidence.

- [ ] **Step 5: Commit this tested slice.** Run git diff --check; inspect the staged diff for external-fetch changes or accidental shared-list regressions; commit with an English title such as feat(history): illustrate posts without image previews.

## Completion review

Trace H50–H53 in the spec to Tasks 1–3. Run targeted tests, frontend build, the isolated browser stage, and the relevant hygiene check because the derived-index schema changed. This branch and the detail-redesign branch both touch ReviewDetailPage.tsx and search-params.ts; before integrating both features, reconcile their URL contracts and rerun history-filter, detail-navigation, and browser stage E checks on the combined tree. Record exact commands and fixture-only evidence in docs/HANDOFF.md and the acceptance state in docs/REQUIREMENTS.md. Stop after those gates pass; live service behavior remains unverified until service-machine observation.
