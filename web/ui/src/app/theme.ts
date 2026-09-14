/**
 * 设计 token 的**唯一真相源**。
 *
 * DESIGN.md 的规矩：颜色、间距、字号、圆角、高度、阴影参数的字面值
 * 只允许出现在这一个文件里。CSS Modules 只引用语义变量，不写字面值。
 *
 * 现状的反面教材：`--space-1..6` 定在 web/ui/src/styles.css，但 ApprovalPanel、
 * RefinementPanel、HashtagEditor 写 18px，CalendarPanel 写 20px，SettingsPanel
 * 写 24px —— 五个区块五个内边距，都不在刻度上。
 *
 * 三路输出，全部从下面同一组常量派生：
 *
 *   tokens         →  给 TS 代码直接读（列宽、行高这类需要数字的地方）
 *   antdToken      →  ConfigProvider 的 theme.token（antd 组件）
 *   antdComponents →  ConfigProvider 的 theme.components（Layout / Menu 等）
 *   cssVariables   →  :root 上的 --rc-* 语义变量（*.module.css）
 *
 * DECISION_LOG.md：如果 antd 的 `theme.cssVar` 行为不符合预期，
 * **不许 hack** —— 就用这里的同一个对象生成各路，仍然只有一个真相源。
 * 本文件走的就是这条路：不依赖 cssVar 也成立，开了 cssVar 只是多一层便利。
 */

/** 中性阶。使用无色相偏移的 slate 系。 */
const neutral = {
  bg: '#f1f5f9',
  surface: '#ffffff',
  surfaceSunken: '#f8fafc',
  fg: '#0f172a',
  fgMuted: '#475569',
  border: '#e2e8f0',
  borderStrong: '#cbd5e1',
  /** 压在图片上的遮罩（缩略图右下角的张数角标）。整个应用只有这一处半透明。 */
  scrim: 'rgba(15, 23, 42, 0.72)',
} as const

/**
 * 状态色。红与黄用于图标、下划线、边框和正文标记，不能承担小号彩色文字。
 *
 * ⚠️ 内容区里只有这两种饱和色：红=这里错了，黄=这里注意看。
 * 混成一种，用几次她两种都不信。
 *
 * ⛔ 状态色不表达动作（DESIGN.md）：
 *    `skipped`（这篇不发）是一个正常的业务决定，**不用红色**。
 */
const status = {
  error: '#dc2626',
  errorSoft: '#fee2e2',
  errorLine: '#fca5a5',
  risk: '#b45309',
  riskSoft: '#fef3c7',
  riskLine: '#fcd34d',
  success: '#047857',
  successSoft: '#d1fae5',
} as const

/**
 * ── 主色 ───────────────────────────────────────────────────────────────────
 *
 * **#155EEF。** 对白色 **5.41:1**（算法见 src/lib/contrast.ts，断言见 theme.test.ts）。
 *
 * `#1677ff` 对白色只有 **4.10:1**，达不到 4.5:1。那条不是审美偏好 —— 主按钮上的白字是
 * 「通过并创建排期」这种不能看错的文案，而她一天要按几十次。
 *
 * `#155EEF` 的色相约为 220°，保持明确的冷蓝。
 *
 * 逐条对 DESIGN.md 的判据（全部在 theme.test.ts 里算，不靠肉眼）：
 *
 *   1. 填充按钮白字     contrast(#155EEF, #ffffff) = 5.41:1  ≥ 4.5 ✅
 *   2. 与错误红区分     色相距 #dc2626(0°)   = 220°           ✅
 *   3. 与风险黄区分     色相距 #b45309(26°)  = 194°           ✅
 *   4. 焦点环可见       对 #ffffff 5.41:1、对 #f1f5f9 4.94:1，都 ≥ 3:1 ✅
 *   5. 不是 AI 紫       色相 220°，不在 250–300° 区间          ✅
 *
 * hover / active **故意往深走**，不用 antd 默认的"变浅"：变浅会让白字的
 * 对比度在 hover 那一刻掉下 4.5。深色 hover 反而让对比度升到 8:1 以上。
 *
 * `onSoft` 是浅底上的前景色：12px 的 Tag 不适用大文本豁免，所以浅底 + 主色
 * 直接用 base 只有 4.69:1 的余量太小，改用 strong（6.96:1）。
 */
const primary = {
  /** 填充按钮底、焦点环、选中态。 */
  base: '#155EEF',
  /** hover 与浅底上的前景。 */
  strong: '#0F47B8',
  /** active（按下）。 */
  deep: '#0C3A97',
  /** 浅底：Tag、选中的导航项。 */
  soft: '#E8EFFD',
  /** 浅底上的文字。 */
  onSoft: '#0F47B8',
  /** 填充上的文字。 */
  fg: '#ffffff',
} as const

/** 8px 基准，六档，刻度外的值不许出现（DESIGN.md）。 */
const space = { s1: 4, s2: 8, s3: 12, s4: 16, s5: 24, s6: 32 } as const

/** 七级排版（DESIGN.md）。正文对照使用 15px 字号与 1.7 行高。 */
const typography = {
  pageTitle: 20,
  sectionTitle: 14,
  subsectionTitle: 13,
  body: 14,
  secondary: 13,
  meta: 12,
  /** 英德双栏正文：她一天里唯一要逐字读的地方。 */
  proseSize: 15,
  proseLineHeight: 1.7,
  /** 正文与 Tag 的行高。正文对照那 1.7 是例外，不用这个。 */
  lineHeight: 1.5,
  weightStrong: 600,
  weightNormal: 400,
} as const

/** 固定高度（DESIGN.md），列表行高为 48px。 */
const layout = {
  topbarHeight: 48,
  sidebarWidth: 200,
  sidebarCollapsedWidth: 48,
  listHeaderHeight: 96,
  reviewHeaderHeight: 56,
  tableRowHeight: 48,
  controlHeight: 32,
  controlHeightSmall: 28,
  /** 左侧导航项：比普通控件高一档，折叠到 48px 时仍然点得准。 */
  navItemHeight: 40,
  /** 列表行里的缩略图。48px 行高里上下各留 4px（DESIGN.md）。 */
  thumbnailSize: 40,
  iconSize: 16,
  borderRadius: 6,
  /** 焦点环：2px 主色 + 2px offset，全应用一种（DESIGN.md）。 */
  focusRingWidth: 2,
  focusRingOffset: 2,
  tableHeaderHeight: 32,
  listToolsHeight: 80,
  filterWidth: 128,
  platformFilterWidth: 116,
  thumbnailColumnWidth: 56,
  problemColumnWidth: 32,
  statusColumnWidth: 112,
  timeColumnWidth: 164,
  platformColumnWidth: 112,
  tagsColumnWidth: 132,
  actionsColumnWidth: 48,
  accountColumnWidth: 172,
  proseReservedHeight: 380,
  imageReservedHeight: 528,
  initialPanelHeight: 112,
  initialSpace: 0,
  calendarDayHeight: 64,
  calendarPopoverWidth: 420,
  settingsWidth: 880,
  settingsFormWidth: 520,
} as const

/**
 * 阴影。**只有浮层有阴影**（DESIGN.md）。
 *
 * 只保留 antd 浮层所需的一组阴影；页面表面用 1px 边框分层。
 */
const elevation = {
  overlay: '0 6px 16px rgba(15, 23, 42, 0.12), 0 3px 6px rgba(15, 23, 42, 0.08)',
} as const

/** 动效 2/10：只有状态过渡，没有入场编排（DESIGN.md）。 */
const motion = {
  duration: '0.18s',
  easing: 'cubic-bezier(0.4, 0, 0.2, 1)',
} as const

export const tokens = {
  neutral,
  status,
  primary,
  space,
  typography,
  layout,
  elevation,
  motion,
} as const

/**
 * 给 `ConfigProvider theme={{ token }}` 用。
 *
 * 只映射我们确实有主张的项；其余留给 antd 默认，不为了"填满"而瞎写。
 */
export const antdToken = {
  colorPrimary: primary.base,
  colorPrimaryHover: primary.strong,
  colorPrimaryActive: primary.deep,
  colorPrimaryBg: primary.soft,
  colorPrimaryBorder: primary.base,
  colorPrimaryText: primary.onSoft,
  colorLink: primary.base,
  colorLinkHover: primary.strong,
  colorLinkActive: primary.deep,
  // DESIGN.md：info 就是主色，不另开一种蓝。
  colorInfo: primary.base,

  colorError: status.error,
  colorErrorBg: status.errorSoft,
  colorWarning: status.risk,
  colorWarningBg: status.riskSoft,
  colorSuccess: status.success,
  colorSuccessBg: status.successSoft,

  colorText: neutral.fg,
  colorTextSecondary: neutral.fgMuted,
  colorBorder: neutral.border,
  colorBorderSecondary: neutral.border,
  colorBgLayout: neutral.bg,
  colorBgContainer: neutral.surface,
  colorBgElevated: neutral.surface,

  fontFamily:
    "'Segoe UI', -apple-system, BlinkMacSystemFont, 'PingFang SC', 'Hiragino Sans GB', 'Microsoft YaHei', Roboto, Helvetica, Arial, sans-serif",
  fontSize: typography.body,
  fontSizeSM: typography.meta,
  fontSizeLG: typography.sectionTitle,
  // antd 的 Typography.Title 默认是 38/30/24/20/16px 五档营销级字号。
  // 拉回七级刻度，避免任意 <Title level={2}> 变成 30px 并破坏同级字号。
  fontSizeHeading1: typography.pageTitle,
  fontSizeHeading2: typography.sectionTitle,
  fontSizeHeading3: typography.subsectionTitle,
  fontSizeHeading4: typography.subsectionTitle,
  fontSizeHeading5: typography.meta,

  // 焦点环宽度，与 global.css 的 html:root :focus-visible 同源。
  lineWidthFocus: layout.focusRingWidth,

  borderRadius: layout.borderRadius,
  controlHeight: layout.controlHeight,
  controlHeightSM: layout.controlHeightSmall,
  sizeUnit: 4,
  sizeStep: 4,

  boxShadow: elevation.overlay,
  boxShadowSecondary: elevation.overlay,

  motionDurationMid: motion.duration,
  motionDurationSlow: motion.duration,
  motionEaseInOut: motion.easing,
  wireframe: false,
} as const

/**
 * 组件级覆盖。
 *
 * 只有两类东西配进来：
 *   1. antd 默认与 DESIGN.md 明确冲突的（`Layout.Header` 默认是深色 #001529）；
 *   2. 外壳尺寸这种必须由 token 驱动、不能各处写死的（48 / 200 / 48）。
 *
 * 能靠全局 token 解决的就不写在这里 —— 组件级覆盖越多，以后越难说清
 * 某个颜色到底从哪来。
 */
export const antdComponents = {
  Layout: {
    headerBg: neutral.surface,
    headerHeight: layout.topbarHeight,
    headerPadding: `0 ${space.s4}px`,
    siderBg: neutral.surface,
    bodyBg: neutral.bg,
  },
  Menu: {
    itemBg: neutral.surface,
    itemSelectedBg: primary.soft,
    itemSelectedColor: primary.onSoft,
    itemColor: neutral.fg,
    itemHeight: layout.navItemHeight,
    itemMarginInline: space.s2,
    collapsedWidth: layout.sidebarCollapsedWidth,
    collapsedIconSize: layout.iconSize,
    iconSize: layout.iconSize,
  },
  Table: {
    rowHoverBg: neutral.surfaceSunken,
    headerBg: neutral.surfaceSunken,
    headerSplitColor: 'transparent',
    cellPaddingBlockSM: space.s1,
    cellPaddingInlineSM: space.s2,
  },
  Tag: {
    defaultBg: neutral.bg,
    defaultColor: neutral.fgMuted,
    // 12px、无边框、浅底 + 深前景（DESIGN.md）。
    fontSize: typography.meta,
    lineHeight: typography.lineHeight,
  },
  Breadcrumb: {
    fontSize: typography.secondary,
    itemColor: neutral.fgMuted,
    lastItemColor: neutral.fg,
    linkColor: neutral.fgMuted,
    linkHoverColor: primary.strong,
    separatorColor: neutral.borderStrong,
  },
} as const

/**
 * 语义 CSS 变量。由上面同一组常量派生，所以不可能和 antd 那边漂移。
 *
 * 变量名故意用 `--rc-` 前缀（review console），和 antd 自己的 `--ant-` 区分开：
 * 混在一个命名空间里，以后就分不清"这是我们的主张"还是"antd 的默认"。
 */
export const cssVariables: Readonly<Record<string, string>> = {
  '--rc-bg': neutral.bg,
  '--rc-surface': neutral.surface,
  '--rc-surface-sunken': neutral.surfaceSunken,
  '--rc-fg': neutral.fg,
  '--rc-fg-muted': neutral.fgMuted,
  '--rc-border': neutral.border,
  '--rc-border-strong': neutral.borderStrong,
  '--rc-scrim': neutral.scrim,

  '--rc-primary': primary.base,
  '--rc-primary-strong': primary.strong,
  '--rc-primary-deep': primary.deep,
  '--rc-primary-soft': primary.soft,
  '--rc-primary-on-soft': primary.onSoft,
  '--rc-primary-fg': primary.fg,

  '--rc-error': status.error,
  '--rc-error-soft': status.errorSoft,
  '--rc-error-line': status.errorLine,
  '--rc-risk': status.risk,
  '--rc-risk-soft': status.riskSoft,
  '--rc-risk-line': status.riskLine,
  '--rc-success': status.success,
  '--rc-success-soft': status.successSoft,

  '--rc-space-1': `${space.s1}px`,
  '--rc-space-2': `${space.s2}px`,
  '--rc-space-3': `${space.s3}px`,
  '--rc-space-4': `${space.s4}px`,
  '--rc-space-5': `${space.s5}px`,
  '--rc-space-6': `${space.s6}px`,

  '--rc-font-page-title': `${typography.pageTitle}px`,
  '--rc-font-section-title': `${typography.sectionTitle}px`,
  '--rc-font-subsection-title': `${typography.subsectionTitle}px`,
  '--rc-font-body': `${typography.body}px`,
  '--rc-font-secondary': `${typography.secondary}px`,
  '--rc-font-meta': `${typography.meta}px`,
  '--rc-font-prose': `${typography.proseSize}px`,
  '--rc-line-height': String(typography.lineHeight),
  '--rc-line-height-prose': String(typography.proseLineHeight),
  '--rc-weight-strong': String(typography.weightStrong),
  '--rc-weight-normal': String(typography.weightNormal),

  '--rc-topbar-h': `${layout.topbarHeight}px`,
  '--rc-sidebar-w': `${layout.sidebarWidth}px`,
  '--rc-sidebar-collapsed-w': `${layout.sidebarCollapsedWidth}px`,
  '--rc-list-header-h': `${layout.listHeaderHeight}px`,
  /** 吸顶补偿从 token 派生，避免写死高度。 */
  '--rc-review-header-h': `${layout.reviewHeaderHeight}px`,
  '--rc-row-h': `${layout.tableRowHeight}px`,
  '--rc-table-header-h': `${layout.tableHeaderHeight}px`,
  '--rc-list-tools-h': `${layout.listToolsHeight}px`,
  '--rc-filter-w': `${layout.filterWidth}px`,
  '--rc-platform-filter-w': `${layout.platformFilterWidth}px`,
  '--rc-control-h': `${layout.controlHeight}px`,
  '--rc-control-h-sm': `${layout.controlHeightSmall}px`,
  '--rc-nav-item-h': `${layout.navItemHeight}px`,
  '--rc-thumb-size': `${layout.thumbnailSize}px`,
  '--rc-icon-size': `${layout.iconSize}px`,
  '--rc-focus-w': `${layout.focusRingWidth}px`,
  '--rc-focus-offset': `${layout.focusRingOffset}px`,
  '--rc-radius': `${layout.borderRadius}px`,
  '--rc-prose-reserved-h': `${layout.proseReservedHeight}px`,
  '--rc-image-reserved-h': `${layout.imageReservedHeight}px`,
  '--rc-initial-panel-h': `${layout.initialPanelHeight}px`,
  '--rc-initial-space': `${layout.initialSpace}px`,
  '--rc-calendar-day-h': `${layout.calendarDayHeight}px`,
  '--rc-calendar-popover-w': `${layout.calendarPopoverWidth}px`,
  '--rc-settings-w': `${layout.settingsWidth}px`,
  '--rc-settings-form-w': `${layout.settingsFormWidth}px`,

  '--rc-shadow-overlay': elevation.overlay,
  '--rc-motion-duration': motion.duration,
  '--rc-motion-easing': motion.easing,
}

/** 把语义变量刷到 :root。main.tsx 启动时调一次。 */
export function applyCssVariables(target: HTMLElement): void {
  for (const [name, value] of Object.entries(cssVariables)) {
    target.style.setProperty(name, value)
  }
}
