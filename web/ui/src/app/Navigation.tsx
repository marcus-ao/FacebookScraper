import { Menu } from 'antd'
import { AuditOutlined, CalendarOutlined, InboxOutlined, SettingOutlined } from '@ant-design/icons'
import type { MenuProps } from 'antd'
import { Link, useLocation } from 'react-router'

import { NAV_ITEMS, selectedNavKeys } from './nav-model'
import type { NavKey } from './nav-model'
import { cx } from '@/lib/css'
import styles from './AppShell.module.css'

/**
 * 左侧主导航。
 *
 * ⛔ 不自己实现导航行为 —— 键盘遍历、折叠态的 Tooltip、选中态样式
 * 都由 antd 的 `Menu mode="inline"` 负责。
 *
 * ⛔ **`selectedKeys` 来自当前 route，不维护第二份 state**（本会话 §8）。
 * 自己存一份迟早会和 URL 不同步：浏览器前进后退、飞书卡片直接落地、
 * 详情页刷新，这三条路径都不经过"点了哪一项"。
 *
 * ⛔ 这里**没有「运行状态」**（DECISION_LOG.md）。它在顶栏。
 */

const ICONS: Readonly<Record<NavKey, React.ReactNode>> = {
  review: <AuditOutlined />,
  history: <InboxOutlined />,
  calendar: <CalendarOutlined />,
  settings: <SettingOutlined />,
}

export function Navigation() {
  const location = useLocation()

  const items: MenuProps['items'] = NAV_ITEMS.map((item) => ({
    key: item.key,
    icon: ICONS[item.key],
    label: <Link to={item.path} state={item.key === 'review' ? { refreshReview: true } : null}>{item.label}</Link>,
  }))

  return (
    <Menu
      className={cx(styles.menu)}
      mode="inline"
      theme="light"
      items={items}
      selectedKeys={selectedNavKeys(location.pathname)}
    />
  )
}
