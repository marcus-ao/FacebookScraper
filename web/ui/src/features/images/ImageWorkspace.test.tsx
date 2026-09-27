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

const detail = { id: 'acme/1', text: { source_text_sha256: 'a'.repeat(64) }, review: { revision: 'r1' },
  content_review: { images: [{ index: 0, confirmed: false }, { index: 1, confirmed: true }] } } as unknown as TaskDetail

const html = (images: readonly ImageAsset[], versions: Record<string, readonly ImageVersion[]> = {}) =>
  renderToStaticMarkup(<ImageWorkspace images={images} detail={detail} versions={versions}
    editing={false} onChanged={vi.fn()} onGenerate={vi.fn()} />)


describe('图片对照：模型没改动这件事必须自己冒出来', () => {
  it('改动占比为 0 时给出满宽提示，并说清两种可能', () => {
    const markup = html([asset({ metrics: metrics(0) })])
    expect(markup).toContain('未见明显改动')
    // 两种成因本地分不开，提示必须同时给出，不能替她断言是哪一种。
    expect(markup).toContain('可能无需修改')
    expect(markup).toContain('可能未按要求生成')
  })

  it('真的改过的图不弹这条提示', () => {
    expect(html([asset()])).not.toContain('未见明显改动')
  })

  it('没量过（旧记录 null）不等于没改动，不能弹提示', () => {
    expect(html([asset({ metrics: metrics(null) })])).not.toContain('未见明显改动')
  })

  it('版本入口不展示数值图像指标', () => {
    const markup = html([asset()], {
      0: [version({ metrics: metrics(0.0123) }),
          version({ out_path: 'posts/p/media_de/01_vab.jpg', current: true, metrics: metrics(0.0456) })],
    })
    expect(markup).not.toContain('改动 1.230 %')
    expect(markup).not.toContain('改动 4.560 %')
    expect(markup).not.toContain('dHash')
  })
})

describe('历史版本：保留视觉比较与换版入口', () => {
  it('只有一版时不显示版本区——没有可比的东西', () => {
    const markup = html([asset()], { 0: [version({ current: true })] })
    expect(markup).not.toContain('生成过')
  })

  it('人工替换后仍可进入唯一生成版本的比较入口', () => {
    const markup = html([asset({ manual: true })], { 0: [version({
      preview_url: '/api/image-versions/task/acme/1/preview?media_index=0', usable: false,
      unusable_reasons: ['这一张已采用人工图片；历史版本仅供查看'],
    })] })
    expect(markup).toContain('比较其他版本')
    expect(markup).not.toContain('采用这一版')
  })

  it('多版时提供次要比较入口，主画布保持简洁', () => {
    const markup = html([asset()], {
      0: [
        version(),
        version({ out_path: 'posts/2026-09/P1-Pro/post/media_de/01_vab.jpg', current: true, refine_instruction: '把 CTA 换短' }),
      ],
    })
    expect(markup).toContain('比较其他版本')
    expect(markup).not.toContain('out_path')
  })

  it('不可用版本不会在主画布出现采用按钮', () => {
    const markup = html([asset()], {
      0: [
        version({ available: false, usable: false, unusable_reasons: ['文件已不在归档里'] }),
        version({ out_path: 'posts/2026-09/P1-Pro/post/media_de/01_vab.jpg', current: true }),
      ],
    })
    expect(markup).not.toContain('采用这一版')
  })
})

describe('上传替换：是换素材，不是转交人工', () => {
  it('已确认的原图显示选择和撤销入口，不再提示缺德语图', () => {
    const markup = html([asset({ selection: 'original_confirmed', ready: true, de_present: false })])
    expect(markup).toContain('当前选用原图')
    expect(markup).toContain('确认第 1 张图片用于发布')
    expect(markup).not.toContain('暂无可用的拟发布图片')
  })

  it('未确认的原图仍提示未就绪，并提供逐图确认入口', () => {
    const markup = html([asset({ selection: 'original', ready: false, de_present: false })])
    expect(markup).toContain('使用原图并确认')
    expect(markup).toContain('暂无可用的拟发布图片')
    expect(markup).not.toContain('alt="拟发布图片 1"')
  })
  it('入口写明替换后仍走系统发布，避免与转交人工混淆', () => {
    const markup = html([asset()])
    expect(markup).toContain('上传图片替换第 1 张')
    expect(markup).toContain('仍由系统继续排期发布')
    // 纯下载是次要动作，收在页头「更多」里，不占逐张审核区。
    expect(markup).not.toContain('下载本篇素材')
  })

  it('人工选择及画幅提示在当前图片旁显示', () => {
    const markup = html([asset({ manual: true, replaced_at: '2026-09-16T02:00:00Z',
      warnings: ['替换图的宽高比与原图相差 20%'] })])
    expect(markup).toContain('人工图片')
    expect(markup).not.toContain('替换于')
    expect(markup).toContain('请核对第 1 张图片的画幅')
    expect(markup).not.toContain('20%')
  })

  it('认不出的后台提示不把数值或文件名带上页面', () => {
    const markup = html([asset({ warnings: ['dHash 距离 12，scale_ratio 2.35（01.jpg）'] })])
    expect(markup).toContain('请核对第 1 张图片后再确认')
    for (const hidden of ['dHash', '2.35', '01.jpg']) expect(markup).not.toContain(hidden)
  })
})

describe('逐张图片显式决定', () => {
  it('浏览缩略图不算确认，各张展示服务端确认状态', () => {
    const markup = html([asset(), asset({ index: 1, original_url: '/two-original', de_url: '/two-de' })])
    expect(markup).toContain('第 1 张（待确认）')
    expect(markup).toContain('第 2 张（已确认）')
    expect(markup).toContain('确认第 1 张图片用于发布')
    expect(markup).not.toContain('都看过了')
  })
  it('单图不展示翻页，无图不用生成假确认', () => {
    expect(html([asset()])).not.toContain('上一张')
    expect(html([asset()])).not.toContain('下一张')
    expect(html([])).toContain('本篇无需审核图片')
  })
  it('原图不可读取时不提供确认按钮，并标明序号与恢复动作', () => {
    const markup = html([asset({ source_image_sha256: '' })])
    expect(markup).toContain('第 1 张原图无法读取')
    expect(markup).toContain('核对归档素材')
    expect(markup).not.toContain('确认第 1 张图片用于发布')
  })
})
