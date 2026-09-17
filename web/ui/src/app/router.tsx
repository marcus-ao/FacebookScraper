import { Navigate, createBrowserRouter, useLocation } from 'react-router'
import type { RouteObject } from 'react-router'

import { AppShell } from './AppShell'
import { NotFound } from './NotFound'
import type { PageMeta } from './page-meta'
import { legacyRedirect, parsePlatform } from './search-params'
import { reviewPath } from './nav-model'
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

/**
 * 旧的 `/review` 现在按 `?platform=` 落到对应入口。
 * 面包屑和外部链接都还在用它，硬跳 Facebook 会把 Instagram 的返回路径带错地方。
 */
function ReviewEntry() {
  const location = useLocation()
  const search = new URLSearchParams(location.search)
  const platform = parsePlatform(search.get('platform')) ?? 'facebook'
  return <Navigate to={{ pathname: reviewPath(platform), search: location.search }} replace />
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

      // 两个平台各一个入口。静态段先于 :account 匹配，所以详情路由不受影响。
      { path: 'review', element: <ReviewEntry /> },
      {
        path: 'review/facebook',
        element: <ReviewPage platform="facebook" />,
        handle: meta({ title: 'Facebook 待审' }),
      },
      {
        path: 'review/instagram',
        element: <ReviewPage platform="instagram" />,
        handle: meta({ title: 'Instagram 待审' }),
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
