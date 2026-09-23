import { Select } from 'antd'
import { monthGroups } from './model'
import styles from './PostTable.module.css'

/** 「全部」是显式选项：清除钮只在悬停时出现，业务人员发现不了。 */
const ALL = '__all__'

export function ListFilters({ filters, months, tags, onChange, platformFilter = true }: {
  filters: { platform: string | null; month: string | null; tag: string | null }
  months: readonly string[]; tags: readonly string[]
  onChange: (key: 'platform' | 'month' | 'tag', value: string | undefined) => void
  /** 审校台按平台分了入口，那里不再需要这个下拉；历史页跨平台检索仍然要。 */
  platformFilter?: boolean
}) {
  const pick = (value: string) => (value === ALL ? undefined : value)
  return <div className={styles.filters}>
    {platformFilter && <Select aria-label="筛选平台" value={filters.platform ?? ALL}
      options={[{ value: ALL, label: '全部平台' }, { value: 'facebook', label: 'Facebook' }, { value: 'instagram', label: 'Instagram' }]}
      onChange={value => onChange('platform', pick(value))} />}
    <Select aria-label="筛选月份" showSearch optionFilterProp="label"
      value={filters.month ?? ALL} options={[{ value: ALL, label: '全部月份' }, ...monthGroups(months)]}
      onChange={value => onChange('month', pick(value))} />
    <Select aria-label="筛选分类" showSearch optionFilterProp="label"
      value={filters.tag ?? ALL} options={[{ value: ALL, label: '全部分类' }, { value: '__untagged__', label: '未分类' }, ...tags.map(value => ({ value, label: value }))]}
      onChange={value => onChange('tag', pick(value))} />
  </div>
}
