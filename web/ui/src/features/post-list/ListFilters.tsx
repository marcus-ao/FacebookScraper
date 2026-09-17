import { Select } from 'antd'
import { monthGroups } from './model'
import styles from './PostTable.module.css'

export function ListFilters({ filters, months, tags, onChange, platformFilter = true }: {
  filters: { platform: string | null; month: string | null; tag: string | null }
  months: readonly string[]; tags: readonly string[]
  onChange: (key: 'platform' | 'month' | 'tag', value: string | undefined) => void
  /** 审校台按平台分了入口，那里不再需要这个下拉；历史页跨平台检索仍然要。 */
  platformFilter?: boolean
}) {
  return <div className={styles.filters}>
    {platformFilter && <Select aria-label="筛选平台" placeholder="全部平台" allowClear value={filters.platform ?? undefined}
      options={[{ value: 'facebook', label: 'Facebook' }, { value: 'instagram', label: 'Instagram' }]}
      onChange={value => onChange('platform', value)} />}
    <Select aria-label="筛选月份" placeholder="全部月份" allowClear showSearch optionFilterProp="label"
      value={filters.month ?? undefined} options={monthGroups(months)} onChange={value => onChange('month', value)} />
    <Select aria-label="筛选分类" placeholder="全部分类" allowClear showSearch optionFilterProp="label"
      value={filters.tag ?? undefined} options={[{ value: '__untagged__', label: '未分类' }, ...tags.map(value => ({ value, label: value }))]}
      onChange={value => onChange('tag', value)} />
  </div>
}
