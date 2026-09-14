import { useCallback, useEffect, useState } from 'react'
import { Alert, Breadcrumb, Button, Layout, Tooltip } from 'antd'
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

/**
 * 应用外壳。
 *
 * ┌──────────────────────────────────────────┐
 * │ 48px 顶栏                                │
 * ├──────────┬───────────────────────────────┤
 * │ 200 / 48 │ 内容                          │
 * └──────────┴───────────────────────────────┘
 *
 * 旧 UI 是三条横带共 115px，每屏都在、不承载工作
 * （UI_ARCHITECTURE_PROPOSAL.md §2.1）。这里纵向只让出 48px。
 *
 * ⛔ 顶栏里**没有**这两行永久说明，它们是已决定删除的：
 *    「US 站图文帖 → 德语正文与德语图 → 德国站定时发布」
 *    「保存与排期均会留档；自动排期需完成本机核验」
 *    她用了六个月之后还要每天看，常驻的解释文字就是噪声（DESIGN.md §10.3）。
 *
 * 48 / 200 / 48 三个数不在这个文件里 —— 它们从 app/theme.ts 来，
 * 同时喂给 antd 的 Layout token 和 CSS 变量（DESIGN.md §2）。
 */
export function AppShell() {
  useDialogTabLoop()
  const location = useLocation()
  const matches = useMatches()
  const [search] = useSearchParams()
  const meta = resolvePageMeta(matches)

  // 折叠偏好：localStorage 里唯一被允许的东西（app/ui-preferences.ts 写了判据）。
  // 首帧先用默认值，挂载后再读 —— 这样没有 storage 的环境也不会抛。
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
          {meta?.internal ? (
            <Alert
              className={cx(styles.internalNotice)}
              type="info"
              showIcon
              message="内部核验页"
              description="这个地址只用于开发期核对共享组件的样子，生产导航里没有入口。"
            />
          ) : null}
          {/* key 让每次换界面都重建子树：占位期不留上一个界面的残留状态。 */}
          <Outlet key={location.pathname} />
        </Content>
      </Layout>
    </Layout>
  )
}

/**
 * 顶栏中部。
 *
 * 详情页画面包屑「来源列表 / 单篇审核」，其余界面就是一个标题。
 * 面包屑第一段带回原来的筛选与页码 —— 这是 DECISION_LOG.md §2.2 第 3 条
 * 在外壳层的落地，参数全部从 URL 取，不碰 `location.state`。
 *
 * ⚠️ 这里**不是** `h1`。顶栏是外壳，它回答"我现在在哪" —— 尤其左侧折叠成
 * 48px 图标之后，这行小字是唯一的定位信息。界面的 `h1`（20/600）在正文的
 * 工作区头里，由 `app/PageTitle.tsx` 画，两者的文字来自同一个路由 handle。
 * 分工见 UI_ARCHITECTURE_PROPOSAL.md §2.1 的图。
 */
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
