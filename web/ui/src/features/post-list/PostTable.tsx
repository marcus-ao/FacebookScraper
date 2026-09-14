import { Table } from 'antd'
import type { TableColumnsType } from 'antd'
import { useNavigate } from 'react-router'
import type { PostRowBase } from './columns'
import { postRowClassName } from './columns'
import { isRowNavigationTarget } from './model'
import styles from './PostTable.module.css'

export function PostTable<T extends PostRowBase>({ rows, columns, loading, href, empty }: {
  rows: T[]; columns: TableColumnsType<T>; loading: boolean; href: (row: T) => string; empty: React.ReactNode
}) {
  const navigate = useNavigate()
  return <Table<T> className={styles.table ?? ''} rowKey="id" size="small" tableLayout="fixed" sticky
    columns={columns} dataSource={rows} loading={loading} pagination={false} locale={{ emptyText: empty }}
    rowClassName={postRowClassName} onRow={row => ({
      'data-task-id': row.id,
      onClick: event => {
        if (event.button === 0 && !event.ctrlKey && !event.metaKey && !event.shiftKey && isRowNavigationTarget(event.target)) {
          void navigate(href(row))
        }
      },
    })} />
}
