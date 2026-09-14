import { useMatches } from 'react-router'

import { resolvePageMeta } from './page-meta'
import { cx } from '@/lib/css'
import styles from './AppShell.module.css'

export function PageTitle() {
  const meta = resolvePageMeta(useMatches())
  if (!meta) return null
  return <h1 className={cx(styles.pageTitle)}>{meta.title}</h1>
}
