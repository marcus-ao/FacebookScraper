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

// 界面是中文，日期选择器也必须使用中文。
// dayjs 因此是显式 dependency，不是搭 antd 的便车。
dayjs.locale('zh-cn')

const root = document.getElementById('root')
if (!root) throw new Error('#root 不存在：index.html 被改过了')

// 语义变量由 theme.ts 这一个真相源刷到 :root。
// antd 那边的 token 走 ConfigProvider，两边同源，不可能漂移（DESIGN.md）。
applyCssVariables(document.documentElement)

createRoot(root).render(
  <StrictMode>
    <ConfigProvider
      locale={zhCN}
      // antd 默认会在两个汉字的按钮里插一个空格（「通 过」）。
      // 这是给营销页做的，放在一天按几十次的操作台上只会显得别扭。
      button={{ autoInsertSpace: false }}
      // antd 6 要求 `cssVar` 使用对象形式：
      // antd 6 把 `cssVar` 收窄成**只接对象**，v5 那种 `cssVar: true` 会
      // 直接类型报错（TS2559）。对象形式是 v6 的正式 API，不是绕路。
      //
      // 更要紧的是：我们自己的 `--rc-*` 语义变量由 app/theme.ts 那一个对象
      // 生成，**不依赖 cssVar**。所以"字面值只有一个真相源"这条不受 antd
      // 的 API 变动影响，cssVar 只是让 antd 组件也走变量、少一层重算。
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
