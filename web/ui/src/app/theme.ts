/** 设计 token 的唯一来源，同时生成组件主题与 --rc-* 语义变量。 */

const neutral = {
  bg: '#f1f5f9',
  surface: '#ffffff',
  surfaceSunken: '#f8fafc',
  fg: '#0f172a',
  fgMuted: '#475569',
  border: '#e2e8f0',
  borderStrong: '#cbd5e1',
  scrim: 'rgba(15, 23, 42, 0.72)',
} as const

/** 红、黄用于问题提示；小字号文字使用更深的前景色。 */
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

/** hover/active 加深以保持白字对比度；浅底小字号文字使用 strong。 */
const primary = {
  base: '#155EEF',
  strong: '#0F47B8',
  deep: '#0C3A97',
  soft: '#E8EFFD',
  onSoft: '#0F47B8',
  fg: '#ffffff',
} as const

const space = { s1: 4, s2: 8, s3: 12, s4: 16, s5: 24, s6: 32 } as const

const typography = {
  pageTitle: 20,
  sectionTitle: 14,
  subsectionTitle: 13,
  body: 14,
  secondary: 13,
  meta: 12,
  /** 英德正文对照使用独立的字号与行高。 */
  proseSize: 15,
  proseLineHeight: 1.7,
  lineHeight: 1.5,
  weightStrong: 600,
  weightNormal: 400,
} as const

const layout = {
  topbarHeight: 48,
  sidebarWidth: 200,
  sidebarCollapsedWidth: 48,
  listHeaderHeight: 96,
  reviewHeaderHeight: 56,
  tableRowHeight: 48,
  controlHeight: 32,
  controlHeightSmall: 28,
  navItemHeight: 40,
  thumbnailSize: 40,
  iconSize: 16,
  borderRadius: 6,
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
} as const

/** 仅浮层使用阴影，页面内容用边框分层。 */
const elevation = {
  overlay: '0 6px 16px rgba(15, 23, 42, 0.12), 0 3px 6px rgba(15, 23, 42, 0.08)',
} as const

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
  // 将 Typography.Title 映射到统一字号刻度。
  fontSizeHeading1: typography.pageTitle,
  fontSizeHeading2: typography.sectionTitle,
  fontSizeHeading3: typography.subsectionTitle,
  fontSizeHeading4: typography.subsectionTitle,
  fontSizeHeading5: typography.meta,

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

/** 使用 --rc- 前缀，避免与组件库的 --ant- 变量冲突。 */
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

  '--rc-shadow-overlay': elevation.overlay,
  '--rc-motion-duration': motion.duration,
  '--rc-motion-easing': motion.easing,
}

export function applyCssVariables(target: HTMLElement): void {
  for (const [name, value] of Object.entries(cssVariables)) {
    target.style.setProperty(name, value)
  }
}
