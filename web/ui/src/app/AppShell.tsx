import { useCallback, useEffect, useState } from 'react'
import { Breadcrumb, Button, Layout, Tooltip } from 'antd'
import { MenuFoldOutlined, MenuUnfoldOutlined } from '@ant-design/icons'
import { Link, Outlet, useLocation, useMatches, useSearchParams } from 'react-router'

import { Navigation } from './Navigation'
import { RuntimeIndicator } from './RuntimeIndicator'
import { LIST_SOURCE_LABEL, resolvePageMeta } from './page-meta'
import { buildListSearch } from './search-params'
import { tokens } from './theme'
import { browserStore, readSiderCollapsed, writeSiderCollapsed } from './ui-preferences'
import { cx } from '@/lib/css'
import { useDialogTabLoop } from '@/hooks/useDialogTabLoop'
import styles from './AppShell.module.css'

const { Header, Sider, Content } = Layout

export function AppShell() {
  useDialogTabLoop()
  const location = useLocation()
  const matches = useMatches()
  const [search] = useSearchParams()
  const meta = resolvePageMeta(matches)

  // 挂载后读取折叠偏好，存储不可用时保留默认值。
  const [collapsed, setCollapsed] = useState(false)
  useEffect(() => {
    setCollapsed(readSiderCollapsed(browserStore()))
  }, [])

  const toggle = useCallback(() => {
    setCollapsed((previous) => {
      const next = !previous
      writeSiderCollapsed(browserStore(), next)
      return next
    })
  }, [])

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
          <RuntimeIndicator />
        </div>
      </Header>

      <Layout>
        <Sider
          className={cx(styles.sider)}
          theme="light"
          collapsible
          collapsed={collapsed}
          trigger={null}
          width={tokens.layout.sidebarWidth}
          collapsedWidth={tokens.layout.sidebarCollapsedWidth}
        >
          <Navigation />
        </Sider>

        <Content className={cx(styles.content)}>
          <Outlet key={location.pathname} />
        </Content>
      </Layout>
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
