/**
 * WCAG 2.1 相对亮度与对比度。**纯函数，没有依赖。**
 *
 * 存在的理由只有一个：主色不能靠肉眼定。
 *
 * DESIGN.md §5.2 给主色定了三条判据，其中"填充按钮上白字的对比度 ≥ 4.5:1"
 * 是个可以算的数 —— 旧候选 #1677ff 看起来完全正常，算出来只有 4.10:1。
 * 所以这份实现进仓库、进单测，而不是在文档里写一句"已确认可读"。
 *
 * 算法出处：WCAG 2.1 定义的 relative luminance 与 contrast ratio
 * （https://www.w3.org/TR/WCAG21/#dfn-relative-luminance）。
 */

/** `#rgb` / `#rrggbb`（大小写均可）→ [0..255, 0..255, 0..255]。 */
export function parseHex(hex: string): readonly [number, number, number] {
  const raw = hex.trim().replace(/^#/, '')
  const full =
    raw.length === 3
      ? raw
          .split('')
          .map((ch) => ch + ch)
          .join('')
      : raw
  if (!/^[0-9a-fA-F]{6}$/.test(full)) {
    throw new Error(`不是合法的十六进制颜色：${hex}`)
  }
  return [
    Number.parseInt(full.slice(0, 2), 16),
    Number.parseInt(full.slice(2, 4), 16),
    Number.parseInt(full.slice(4, 6), 16),
  ] as const
}

/** 单通道 sRGB（0..1）→ 线性值。 */
function toLinear(channel: number): number {
  return channel <= 0.04045 ? channel / 12.92 : ((channel + 0.055) / 1.055) ** 2.4
}

/** 相对亮度，0（黑）..1（白）。 */
export function relativeLuminance(hex: string): number {
  const [r, g, b] = parseHex(hex)
  return (
    0.2126 * toLinear(r / 255) + 0.7152 * toLinear(g / 255) + 0.0722 * toLinear(b / 255)
  )
}

/** 对比度，1..21。参数顺序无关。 */
export function contrastRatio(a: string, b: string): number {
  const la = relativeLuminance(a)
  const lb = relativeLuminance(b)
  const [light, dark] = la >= lb ? [la, lb] : [lb, la]
  return (light + 0.05) / (dark + 0.05)
}

/**
 * HSL 的色相角，0..360。
 *
 * 用来证明"主色与红、黄在同一屏上不混淆"这条判据（DESIGN.md §5.2 第 1 条）
 * 有一个可复算的依据，而不是"我看着不像"。
 */
export function hueOf(hex: string): number {
  const [r255, g255, b255] = parseHex(hex)
  const r = r255 / 255
  const g = g255 / 255
  const b = b255 / 255
  const max = Math.max(r, g, b)
  const min = Math.min(r, g, b)
  const delta = max - min
  if (delta === 0) return 0 // 灰，没有色相
  let hue: number
  if (max === r) hue = ((g - b) / delta) % 6
  else if (max === g) hue = (b - r) / delta + 2
  else hue = (r - g) / delta + 4
  hue *= 60
  return hue < 0 ? hue + 360 : hue
}

/** 两个色相之间的最短夹角，0..180。 */
export function hueDistance(a: string, b: string): number {
  const diff = Math.abs(hueOf(a) - hueOf(b)) % 360
  return diff > 180 ? 360 - diff : diff
}
