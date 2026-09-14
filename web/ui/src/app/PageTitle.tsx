import { useMatches } from 'react-router'

import { resolvePageMeta } from './page-meta'
import { cx } from '@/lib/css'
import styles from './AppShell.module.css'

/**
 * 界面的 `h1`。**20 / 600，工作区头的第一行**（DESIGN.md）。
 *
 * 标题文字来自路由的 `handle`，和顶栏那个用的是同一份 —— 所以不存在
 * "顶栏说在队列、正文标题说在历史"这种不同步。
 *
 * 为什么 h1 在正文而不在顶栏：
 *
 * 顶栏那 48px 是**外壳**，它回答"我现在在哪"（尤其左侧折叠成 48px 图标之后）。
 * 正文的 20/600 才是界面标题：顶栏小字负责定位，工作区头负责标题。
 *
 * 筛选、计数和动作位于这一行右侧，**不要再另起一个页面标题**。
 */
export function PageTitle() {
  const meta = resolvePageMeta(useMatches())
  if (!meta) return null
  return <h1 className={cx(styles.pageTitle)}>{meta.title}</h1>
}
