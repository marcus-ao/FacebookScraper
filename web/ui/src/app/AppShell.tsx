import { useCallback, useEffect, useState } from 'react'
import { Breadcrumb, Button, Layout, Tooltip } from 'antd'
import { MenuFoldOutlined, MenuUnfoldOutlined } from '@ant-design/icons'
import { Link, Outlet, useLocation, useMatches, useSearchParams } from 'react-router'

import { Navigation } from './Navigation'
import { RuntimeIndicator } from './RuntimeIndicator'
import { DeploymentBanner } from './DeploymentBanner'
import { LIST_SOURCE_LABEL, resolvePageMeta } from './page-meta'
import { buildListSearch } from './search-params'
import { tokens } from './theme'
import { browserStore, readSiderCollapsed, writeSiderCollapsed } from './ui-preferences'
import { cx } from '@/lib/css'
import { useDialogTabLoop } from '@/hooks/useDialogTabLoop'
import { useUnsavedChangesGuard } from '@/hooks/useUnsavedChangesGuard'
import styles from './AppShell.module.css'

const { Header, Sider, Content } = Layout

export function AppShell() {
  useDialogTabLoop()
  const unsavedGuard = useUnsavedChangesGuard()
  const location = useLocation()
  const matches = useMatches()
  const [search] = useSearchParams()
  const meta = resolvePageMeta(matches)

  // 挂载后读取折叠偏好，存储不可用时保留默认值。
  const [narrow, setNarrow] = useState(() => typeof window !== 'undefined' && !!window.matchMedia?.('(max-width: 760px)').matches)
  const [collapsed, setCollapsed] = useState(narrow)
  useEffect(() => {
    const media = window.matchMedia('(max-width: 760px)')
    const sync = () => { setNarrow(media.matches); setCollapsed(media.matches || readSiderCollapsed(browserStore())) }
    sync()
    media.addEventListener('change', sync)
    return () => media.removeEventListener('change', sync)
  }, [])
  useEffect(() => { if (narrow) setCollapsed(true) }, [narrow, location.pathname])

  const toggle = useCallback(() => {
    setCollapsed((previous) => {
      const next = !previous
      if (!narrow) writeSiderCollapsed(browserStore(), next)
      return next
    })
  }, [narrow])

  return (
    <Layout className={cx(styles.shell)}>
      <Header className={cx(styles.header)}>
        <Link to="/review" className={cx(styles.brand)}>
          <span className={cx(styles.mark)} aria-hidden="true">
            DE
          </span>
          <span className={cx(styles.brandText)}>审校台</span>
        </Link>

        <Tooltip title={collapsed ? '展开导航' : '收起导航'} placement="bottomLeft">
          <Button
            className={cx(styles.collapse)}
            type="text"
            aria-label={collapsed ? '展开导航' : '收起导航'}
            aria-expanded={!collapsed}
            icon={collapsed ? <MenuUnfoldOutlined /> : <MenuFoldOutlined />}
            onClick={toggle}
          />
        </Tooltip>

        <HeaderTitle
          title={meta?.title ?? null}
          {...(meta?.backTo ? { backTo: meta.backTo } : {})}
          backSearch={meta?.backTo ? buildListSearch(search, meta.backTo) : ''}
        />

        <div className={cx(styles.headerRight)}>
          {!/^\/(review|history)\/[^/]+\/[^/]+$/.test(location.pathname) && <RuntimeIndicator />}
        </div>
      </Header>

      <DeploymentBanner />
      <Layout data-deployment-content>
        <Sider
          className={cx(styles.sider)}
          theme="light"
          collapsible
          collapsed={collapsed}
          trigger={null}
          width={tokens.layout.sidebarWidth}
          collapsedWidth={narrow ? 0 : tokens.layout.sidebarCollapsedWidth}
        >
          <Navigation />
        </Sider>
        {narrow && !collapsed && <button type="button" className={styles.mobileScrim}
          aria-label="关闭导航" onClick={() => setCollapsed(true)} />}

        <Content className={cx(styles.content)}>
          <Outlet key={location.pathname} />
        </Content>
      </Layout>
      {unsavedGuard}
    </Layout>
  )
}

/** 顶栏显示位置，h1 由 PageTitle 提供；返回链接从 URL 恢复列表上下文。 */
function HeaderTitle({
  title,
  backTo,
  backSearch,
}: {
  title: string | null
  backTo?: 'review' | 'history'
  backSearch: string
}) {
  if (title === null) return <div className={cx(styles.title)} />

  if (backTo) {
    return (
      <div className={cx(styles.crumb)}>
        <Breadcrumb
          items={[
            {
              title: (
                <Link to={{ pathname: `/${backTo}`, search: backSearch }}>
                  {LIST_SOURCE_LABEL[backTo]}
                </Link>
              ),
            },
            { title: <span className={cx(styles.crumbTitle)}>{title}</span> },
          ]}
        />
      </div>
    )
  }

  return <div className={cx(styles.title)}>{title}</div>
}
