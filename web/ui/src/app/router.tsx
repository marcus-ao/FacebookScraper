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

/** 兼容飞书等外部系统已有的 ?task=、?view= 链接。 */
function LegacyEntry() {
  const location = useLocation()
  const target = legacyRedirect(location.search)
  return <Navigate to={target ?? '/review'} replace />
}

function meta(value: PageMeta): PageMeta {
  return value
}

/** task id 含斜杠，路由拆为 account/postId，请求时由 idPath 逐段编码。 */
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
