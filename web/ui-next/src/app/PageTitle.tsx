import { useMatches } from 'react-router'

import { resolvePageMeta } from './page-meta'
import { cx } from '@/lib/css'
import styles from './AppShell.module.css'

/**
 * 界面的 `h1`。**20 / 600，工作区头的第一行**（DESIGN.md §3.1）。
 *
 * 标题文字来自路由的 `handle`，和顶栏那个用的是同一份 —— 所以不存在
 * "顶栏说在队列、正文标题说在历史"这种不同步。
 *
 * 为什么 h1 在正文而不在顶栏：
 *
 * 顶栏那 48px 是**外壳**，它回答"我现在在哪"（尤其左侧折叠成 48px 图标之后）。
 * 正文的 20/600 才是这个界面的标题 —— UI_ARCHITECTURE_PROPOSAL.md §2.1 的图里
 * 两处都画了，分工就是这样：顶栏小字定位，工作区头是标题。
 *
 * Stage C/D 把筛选、计数、动作加到这一行的右侧，**不要再另起一个页面标题**。
 * 旧 UI 的详情页压根没有 h1（BASELINE_BEHAVIOR.md §10），这个组件就是补那个洞。
 */
export function PageTitle() {
  const meta = resolvePageMeta(useMatches())
  if (!meta) return null
  return <h1 className={cx(styles.pageTitle)}>{meta.title}</h1>
}
