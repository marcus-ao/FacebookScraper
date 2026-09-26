import { describe, expect, it } from 'vitest'
import { renderToStaticMarkup } from 'react-dom/server'
import { readFileSync } from 'node:fs'
import type { Mark, RiskScan } from '@/types/domain'
import fixture from '@/types/__fixtures__/task-detail-active.json'
import { BodyReviewActions, TextWorkspace } from './TextWorkspace'

const scan = fixture.risk_scan as unknown as RiskScan
const mark: Mark = { index: 0, type: 'risk', kind: 'pun', label: '核对这处双关用语', en: [0, 4], de: null }

function bodyMarkup(de: string, state: RiskScan = scan, marks: readonly Mark[] = []) {
  return renderToStaticMarkup(<TextWorkspace en="Only four words" de={de} marks={marks} liveMarks={marks}
    active={-1} editing={false} checking={false} human={false} scan={state}
    onChange={() => {}} onSelect={() => {}} onJump={() => {}}
    onStartEdit={() => {}} onGenerateDraft={() => {}} />)
}

describe('正文审核工作区', () => {
  it('无德语稿时显示空状态和手写、生成入口，不称为机器初译', () => {
    const markup = bodyMarkup('')
    expect(markup).toContain('尚无德语文案')
    expect(markup).toContain('手写德语正文')
    expect(markup).toContain('生成德语初稿')
    expect(markup).not.toContain('机器初译')
  })

  it('未扫描、失败、过期与完成零结果分别表述，均不宣称安全', () => {
    expect(bodyMarkup('Entwurf', { ...scan, status: 'not_scanned' })).toContain('尚未扫描')
    expect(bodyMarkup('Entwurf', { ...scan, status: 'failed' })).toContain('扫描未完成')
    expect(bodyMarkup('Entwurf', { ...scan, status: 'stale' })).toContain('扫描结果已过期')
    const complete = bodyMarkup('Entwurf', { ...scan, status: 'completed', risks: [] })
    expect(complete).not.toContain('无风险')
    expect(complete).not.toContain('安全')
  })

  it('风险文字可以用键盘激活并读到对应说明', () => {
    const markup = bodyMarkup('Entwurf', { ...scan, status: 'completed' }, [mark])
    expect(markup).toContain('tabindex="0"')
    expect(markup).toContain('role="button"')
    expect(markup).toContain('核对这处双关用语')
  })

  it('短文自然高度，长文双栏独立滚动，窄屏上下排列', () => {
    const css = readFileSync(new URL('./TextWorkspace.module.css', import.meta.url), 'utf8')
    expect(css).not.toContain('height: calc(100vh')
    expect(css).toMatch(/max-height:[^;]+;/)
    expect(css).toMatch(/overflow:\s*auto/)
    expect(css).toContain('@media')
  })
})

describe('正文确认动作', () => {
  const props = { canEdit: true, saving: false, confirming: false, confirmed: false,
    onEdit: () => {}, onSave: () => {}, onDiscard: () => {}, onConfirm: (_confirmed: boolean) => {} }
  it('未改动的机器初稿可以直接确认，且保存与确认是两个动作', () => {
    const view = renderToStaticMarkup(<BodyReviewActions {...props} hasBody editing={false} />)
    expect(view).toContain('确认当前德语正文')
    const editing = renderToStaticMarkup(<BodyReviewActions {...props} hasBody editing />)
    expect(editing).toContain('保存修改')
    expect(editing).not.toContain('确认当前德语正文')
  })

  it('没有德语正文或正在保存时不能确认，已确认后明确显示状态', () => {
    expect(renderToStaticMarkup(<BodyReviewActions {...props} hasBody={false} editing={false} />)).not.toContain('确认当前德语正文')
    expect(renderToStaticMarkup(<BodyReviewActions {...props} hasBody editing={false} saving />)).not.toContain('确认当前德语正文')
    expect(renderToStaticMarkup(<BodyReviewActions {...props} hasBody editing={false} confirming />)).toContain('正在保存正文确认')
    expect(renderToStaticMarkup(<BodyReviewActions {...props} hasBody editing={false} confirmed />)).toContain('当前正文已确认')
  })
})
