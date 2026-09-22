import { describe, expect, it, vi } from 'vitest'
import { renderToStaticMarkup } from 'react-dom/server'

import { ImageWorkspace } from './ImageWorkspace'
import type { ImageAsset, ImageVersion, TaskDetail } from '@/types/domain'

const metrics = (changed: number | null) => ({
  dhash_distance: 1, aspect_drift: 0, scale_ratio: 1, elapsed_s: 12,
  changed_pixel_ratio: changed,
})

const asset = (patch: Partial<ImageAsset> = {}): ImageAsset => ({
  index: 0,
  original_url: '/api/tasks/acme/1/image/0?variant=original',
  de_url: '/api/tasks/acme/1/image/0?variant=de',
  de_present: true,
  selection: 'generated',
  ready: true,
  source_image_sha256: 'a'.repeat(64),
  metrics: metrics(0.0123),
  ...patch,
})

const version = (patch: Partial<ImageVersion> = {}): ImageVersion => ({
  out_path: 'posts/2026-09/P1-Pro/post/media_de/01.jpg',
  created_at: '2026-09-15T02:00:00Z',
  refine_id: null,
  refine_instruction: null,
  model: 'gpt-image-2',
  available: true,
  current: false,
  usable: true,
  unusable_reasons: [],
  metrics: metrics(0.0123),
  ...patch,
})

const detail = { id: 'acme/1', text: { source_text_sha256: 'a'.repeat(64) }, review: { revision: 'r1' } } as unknown as TaskDetail

const html = (images: readonly ImageAsset[], versions: Record<string, readonly ImageVersion[]> = {}) =>
  renderToStaticMarkup(<ImageWorkspace images={images} detail={detail} versions={versions}
    editing={false} onChanged={vi.fn()} onProgress={vi.fn()} />)


describe('图片对照：模型没改动这件事必须自己冒出来', () => {
  it('改动占比为 0 时给出满宽提示，并说清两种可能', () => {
    const markup = html([asset({ metrics: metrics(0) })])
    expect(markup).toContain('未检测到明显像素变化')
    // 两种成因本地分不开，提示必须同时给出，不能替她断言是哪一种。
    expect(markup).toContain('本来就没有需要本地化的英文')
    expect(markup).toContain('没有照做')
  })

  it('真的改过的图不弹这条提示', () => {
    expect(html([asset()])).not.toContain('未检测到明显像素变化')
  })

  it('没量过（旧记录 null）不等于没改动，不能弹提示', () => {
    expect(html([asset({ metrics: metrics(null) })])).not.toContain('未检测到明显像素变化')
  })

  it('改动占比在版本行上直接可读，不用展开折叠面板', () => {
    const markup = html([asset()], {
      0: [version({ metrics: metrics(0.0123) }),
          version({ out_path: 'posts/p/media_de/01_vab.jpg', current: true, metrics: metrics(0.0456) })],
    })
    expect(markup).toContain('改动 1.230 %')
    expect(markup).toContain('改动 4.560 %')
  })
})

describe('历史版本：3 次预算的前提是上一版还找得回来', () => {
  it('只有一版时不显示版本区——没有可比的东西', () => {
    const markup = html([asset()], { 0: [version({ current: true })] })
    expect(markup).not.toContain('生成过')
  })

  it('人工替换后仍可预览唯一的生成版本，并解释不能采用的原因', () => {
    const markup = html([asset({ manual: true })], { 0: [version({
      preview_url: '/api/image-versions/task/acme/1/preview?media_index=0', usable: false,
      unusable_reasons: ['这一张已采用人工图片；历史版本仅供查看'],
    })] })
    expect(markup).toContain('这一张生成过 1 版')
    expect(markup).toContain('预览第 1 版')
    expect(markup).toContain('历史版本仅供查看')
    expect(markup).not.toContain('采用这一版')
  })

  it('多版时列出每一版，当前版标出来且不给采用按钮', () => {
    const markup = html([asset()], {
      0: [
        version(),
        version({ out_path: 'posts/2026-09/P1-Pro/post/media_de/01_vab.jpg', current: true, refine_instruction: '把 CTA 换短' }),
      ],
    })
    expect(markup).toContain('这一张生成过 2 版')
    expect(markup).toContain('当前版')
    expect(markup).toContain('把 CTA 换短')
    expect(markup).toContain('采用这一版')
    expect(markup.match(/采用这一版/g)).toHaveLength(1)
  })

  it('不可用的版本给出原因，不给采用按钮', () => {
    const markup = html([asset()], {
      0: [
        version({ available: false, usable: false, unusable_reasons: ['文件已不在归档里'] }),
        version({ out_path: 'posts/2026-09/P1-Pro/post/media_de/01_vab.jpg', current: true }),
      ],
    })
    expect(markup).toContain('文件已不在归档里')
    expect(markup).not.toContain('采用这一版')
  })
})

describe('上传替换：是换素材，不是转交人工', () => {
  it('已确认的原图显示选择和撤销入口，不再提示缺德语图', () => {
    const markup = html([asset({ selection: 'original_confirmed', ready: true, de_present: false })])
    expect(markup).toContain('已确认使用原图')
    expect(markup).toContain('撤销原图确认')
    expect(markup).not.toContain('缺德语图')
  })

  it('未确认的原图仍提示未就绪，并提供逐图确认入口', () => {
    const markup = html([asset({ selection: 'original', ready: false, de_present: false })])
    expect(markup).toContain('确认使用原图')
    expect(markup).toContain('缺德语图')
  })
  it('入口写明替换后仍走系统发布，避免与转交人工混淆', () => {
    const markup = html([asset()])
    expect(markup).toContain('上传图片替换第 1 张')
    expect(markup).toContain('仍然留在系统里继续排期发布')
    expect(markup).toContain('下载本篇素材')
  })

  it('人工选择及画幅提示在当前图片旁显示', () => {
    const markup = html([asset({ manual: true, replaced_at: '2026-09-16T02:00:00Z',
      warnings: ['替换图的宽高比与原图相差 20%'] })])
    expect(markup).toContain('人工图片')
    expect(markup).toContain('替换于')
    expect(markup).toContain('宽高比与原图相差 20%')
  })
})
