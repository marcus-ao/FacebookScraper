import { Navigate, createBrowserRouter, useLocation } from 'react-router'
import type { RouteObject } from 'react-router'

import { AppShell } from './AppShell'
import { NotFound } from './NotFound'
import type { PageMeta } from './page-meta'
import { legacyRedirect } from './search-params'
import { ReviewPage } from '@/pages/review/ReviewPage'
import { HistoryPage } from '@/pages/history/HistoryPage'
import { ReviewDetailPage } from '@/pages/review-detail/ReviewDetailPage'
import { CalendarPage } from '@/pages/calendar/CalendarPage'
import { SettingsPage } from '@/pages/settings/SettingsPage'
import { RuntimePage } from '@/pages/runtime/RuntimePage'

/**
 * 旧 URL 的入口。
 *
 * ⚠️ 永久保留（DECISION_LOG.md）：web/DESIGN.md 规定飞书卡片里带
 * 审校链接，外部系统里可能已经散落着 `?task=` / `?view=` 这种 URL。
 * 不设删除期限。
 *
 * 不是旧形状时（`/` 上没有这两个参数）直接去审校队列。
 */
function LegacyEntry() {
  const location = useLocation()
  const target = legacyRedirect(location.search)
  return <Navigate to={target ?? '/review'} replace />
}

/** 让 `handle` 在写路由表时就受类型约束，不是随便挂一个对象。 */
function meta(value: PageMeta): PageMeta {
  return value
}

/**
 * 路由表。详情用两条父路径挂同一个界面，使“从哪来回哪去”由路由结构表达。
 *
 * task id 自带斜杠，所以拆成 `:account/:postId` 两段——React Router 不必
 * 处理带斜杠的单个参数，而请求 URL 仍由 services/http.ts 的 idPath() 逐段编码。
 *
 * `handle` 上挂的 PageMeta 是顶栏标题与面包屑的来源（app/page-meta.ts）：
 * 路由表已经是"哪个 URL 对应哪个界面"的真相源，标题挂在同一处就不会不同步。
 */
export const routes: RouteObject[] = [
  {
    path: '/',
    element: <AppShell />,
    children: [
      { index: true, element: <LegacyEntry /> },

      {
        path: 'review',
        element: <ReviewPage />,
        handle: meta({ title: '审校队列' }),
      },
      {
        path: 'review/:account/:postId',
        element: <ReviewDetailPage source="review" />,
        handle: meta({ title: '单篇审核', backTo: 'review' }),
      },

      {
        path: 'history',
        element: <HistoryPage />,
        handle: meta({ title: '历史归档' }),
      },
      {
        path: 'history/:account/:postId',
        element: <ReviewDetailPage source="history" />,
        handle: meta({ title: '单篇审核', backTo: 'history' }),
      },

      {
        path: 'calendar',
        element: <CalendarPage />,
        handle: meta({ title: '发布月历' }),
      },
      {
        path: 'settings',
        element: <SettingsPage />,
        handle: meta({ title: '运营设置' }),
      },
      {
        // DECISION_LOG.md：路由继续存在，但**不在主导航里**。
        // 唯一入口是顶栏右侧的 RuntimeIndicator。
        path: 'runtime',
        element: <RuntimePage />,
        handle: meta({ title: '运行状态' }),
      },

      { path: '*', element: <NotFound />, handle: meta({ title: '找不到页面' }) },
    ],
  },
]

export function createRouter() {
  return createBrowserRouter(routes)
}
