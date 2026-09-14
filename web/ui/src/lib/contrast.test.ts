import { describe, expect, it } from 'vitest'

import { contrastRatio, hueDistance, hueOf, parseHex, relativeLuminance } from './contrast'

describe('parseHex', () => {
  it('读六位十六进制', () => {
    expect(parseHex('#155EEF')).toEqual([21, 94, 239])
  })

  it('读三位简写', () => {
    expect(parseHex('#fff')).toEqual([255, 255, 255])
  })

  it('不区分大小写，允许省略井号', () => {
    expect(parseHex('dc2626')).toEqual(parseHex('#DC2626'))
  })

  it('非法输入直接抛，不静默返回黑色', () => {
    expect(() => parseHex('#12345')).toThrow()
    expect(() => parseHex('rgb(1,2,3)')).toThrow()
  })
})

describe('relativeLuminance', () => {
  it('黑白两端是 0 与 1', () => {
    expect(relativeLuminance('#000000')).toBeCloseTo(0, 10)
    expect(relativeLuminance('#ffffff')).toBeCloseTo(1, 10)
  })

  it('低通道走线性分支（≤ 0.04045）', () => {
    expect(relativeLuminance('#090909')).toBeCloseTo(0.0027317, 6)
  })
})

describe('contrastRatio', () => {
  it('黑白是 21:1', () => {
    expect(contrastRatio('#000000', '#ffffff')).toBeCloseTo(21, 6)
  })

  it('同色是 1:1', () => {
    expect(contrastRatio('#155EEF', '#155EEF')).toBeCloseTo(1, 10)
  })

  it('参数顺序无关', () => {
    expect(contrastRatio('#155EEF', '#ffffff')).toBeCloseTo(
      contrastRatio('#ffffff', '#155EEF'),
      10,
    )
  })

  it('确认 #1677ff 对白 ≈ 4.10:1，低于正文门槛', () => {
    expect(contrastRatio('#1677ff', '#ffffff')).toBeCloseTo(4.1, 1)
    expect(contrastRatio('#1677ff', '#ffffff')).toBeLessThan(4.5)
  })
})

describe('hueOf', () => {
  it('红 0°、绿 120°、蓝 240°', () => {
    expect(hueOf('#ff0000')).toBeCloseTo(0, 6)
    expect(hueOf('#00ff00')).toBeCloseTo(120, 6)
    expect(hueOf('#0000ff')).toBeCloseTo(240, 6)
  })

  it('灰没有色相，记 0', () => {
    expect(hueOf('#808080')).toBe(0)
  })
})

describe('hueDistance', () => {
  it('取最短夹角，不会超过 180', () => {
    expect(hueDistance('#ff0000', '#00ffff')).toBeCloseTo(180, 6)
    expect(hueDistance('#ff0026', '#ff2a00')).toBeLessThan(30)
  })
})
