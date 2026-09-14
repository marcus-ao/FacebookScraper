import { useEffect, useState } from 'react'
import { Alert, Button, Collapse, Empty, Modal, Space } from 'antd'
import { CheckOutlined } from '@ant-design/icons'
import type { ImageAsset } from '@/types/domain'
import styles from './ImageWorkspace.module.css'

export function ImageWorkspace({ images, onProgress }: { images: readonly ImageAsset[]; onProgress: (unseen: number) => void }) {
  const [current, setCurrent] = useState(0), [seen, setSeen] = useState(() => new Set([0])), [zoom, setZoom] = useState(false)
  useEffect(() => { onProgress(Math.max(0, images.length - seen.size)) }, [images.length, seen, onProgress])
  const choose = (index: number) => { setCurrent(index); setSeen(old => new Set([...old, index])) }
  const image = images[current]
  if (!image) return <Empty description="这篇没有图片" />
  const pair = (large = false) => <div className={`${styles.pair} ${large ? styles.large : ''}`}>
    <figure><figcaption>原图（英文）</figcaption><img src={image.original_url} alt={`原图 ${current + 1}`} /></figure>
    <figure><figcaption>德语图 {!image.de_present && <span className={styles.missing}>缺德语图，显示的是原图</span>}</figcaption><img src={image.de_url || image.original_url} alt={`德语图 ${current + 1}`} /></figure>
  </div>
  return <section aria-label="图片对照">
    <div className={styles.controls}><span>第 {current + 1} / {images.length} 张 · {Math.max(0, images.length - seen.size) ? `还有 ${images.length - seen.size} 张没看` : '都看过了'}</span><Space><Button disabled={current === 0} onClick={() => choose(current - 1)}>上一张</Button><Button disabled={current >= images.length - 1} onClick={() => choose(current + 1)}>下一张</Button><Button onClick={() => setZoom(true)}>放大对照</Button></Space></div>
    {!image.de_present && <Alert type="warning" showIcon title="这一张缺少德语图，当前展示原图，请人工核对" />}
    {pair()}
    <div className={styles.sheet} aria-label="图片缩略图">{images.map((item, index) => <button type="button" key={item.index} className={styles.thumb} aria-label={`第 ${index + 1} 张${seen.has(index) ? '（看过）' : '（未看）'}`} aria-current={current === index} onClick={() => choose(index)}><img src={item.de_url || item.original_url} alt="" loading="lazy" /><span>{index + 1} {seen.has(index) && <CheckOutlined />}{!item.de_present && ' · 缺德语图'}</span></button>)}</div>
    <Collapse ghost items={[{ key: 'metrics', label: '图片比较指标', children: image.metrics ? <dl className={styles.metrics}><div><dt>dHash 距离</dt><dd>{image.metrics.dhash_distance ?? '—'}</dd></div><div><dt>宽高比形变</dt><dd>{image.metrics.aspect_drift ?? '—'} %</dd></div><div><dt>缩放</dt><dd>{image.metrics.scale_ratio ?? '—'}×</dd></div><div><dt>生成耗时</dt><dd>{image.metrics.elapsed_s ?? '—'} 秒</dd></div></dl> : <p>这张没有程序生成记录，可能是人工放置的图片。</p> }]} />
    <Modal title={`图片放大对照 · ${current + 1} / ${images.length}`} open={zoom} onCancel={() => setZoom(false)} footer={null} width="90vw">{pair(true)}</Modal>
  </section>
}
