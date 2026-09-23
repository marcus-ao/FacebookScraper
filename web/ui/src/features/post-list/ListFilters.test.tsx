import { describe, expect, it } from 'vitest'
import { renderToStaticMarkup } from 'react-dom/server'

import { ListFilters } from './ListFilters'

const render = (filters: { platform: string | null; month: string | null; tag: string | null }) =>
  renderToStaticMarkup(<ListFilters filters={filters} months={['2026-09', 'undated']} tags={['Riko']} onChange={() => {}} />)

describe('「全部」是显式选项，不靠悬停才出现的清除钮', () => {
  it('未选筛选时三个下拉都停在「全部」选中项，而不是占位符', () => {
    const markup = render({ platform: null, month: null, tag: null })
    expect(markup).not.toContain('ant-select-selection-placeholder')
    expect(markup).toContain('全部平台')
    expect(markup).toContain('全部月份')
    expect(markup).toContain('全部分类')
  })

  it('选中值照常显示；undated 月份显示「无日期」', () => {
    const markup = render({ platform: 'facebook', month: 'undated', tag: 'Riko' })
    expect(markup).toContain('Facebook')
    expect(markup).toContain('无日期')
    expect(markup).toContain('Riko')
  })

  it('审校台入口不渲染平台筛选', () => {
    const markup = renderToStaticMarkup(
      <ListFilters filters={{ platform: null, month: null, tag: null }} months={[]} tags={[]} onChange={() => {}} platformFilter={false} />)
    expect(markup).not.toContain('筛选平台')
    expect(markup).toContain('全部月份')
  })
})
