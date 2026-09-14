import { Menu } from 'antd'
import { AuditOutlined, CalendarOutlined, InboxOutlined, SettingOutlined } from '@ant-design/icons'
import type { MenuProps } from 'antd'
import { Link, useLocation } from 'react-router'

import { NAV_ITEMS, selectedNavKeys } from './nav-model'
import type { NavKey } from './nav-model'
import { cx } from '@/lib/css'
import styles from './AppShell.module.css'


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
