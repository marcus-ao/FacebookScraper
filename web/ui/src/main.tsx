import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { RouterProvider } from 'react-router'
import { QueryClientProvider } from '@tanstack/react-query'
import { App as AntdApp, ConfigProvider } from 'antd'
import zhCN from 'antd/locale/zh_CN'
import dayjs from 'dayjs'
import 'dayjs/locale/zh-cn'

import { createQueryClient } from './app/queryClient'
import { createRouter } from './app/router'
import { antdComponents, antdToken, applyCssVariables } from './app/theme'
import './styles/global.css'

dayjs.locale('zh-cn')

const root = document.getElementById('root')
if (!root) throw new Error('#root 不存在：index.html 被改过了')

// 语义变量与组件 token 均来自 theme.ts。
applyCssVariables(document.documentElement)

createRoot(root).render(
  <StrictMode>
    <ConfigProvider
      locale={zhCN}
      button={{ autoInsertSpace: false }}
      theme={{
        token: antdToken,
        components: antdComponents,
        cssVar: { prefix: 'ant' },
        hashed: false,
      }}
    >
      <AntdApp>
        <QueryClientProvider client={createQueryClient()}>
          <RouterProvider router={createRouter()} />
        </QueryClientProvider>
      </AntdApp>
    </ConfigProvider>
  </StrictMode>,
)
