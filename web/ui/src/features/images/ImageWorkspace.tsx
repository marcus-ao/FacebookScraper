import { useEffect, useState } from 'react'
import { Alert, Button, Collapse, Empty, Modal, Space, Tag, Upload } from 'antd'
import { CheckOutlined, DownloadOutlined, UploadOutlined } from '@ant-design/icons'
import type { ImageAsset, ImageVersion, TaskDetail } from '@/types/domain'
import { selectImageVersion, uploadImage } from '@/services/jobs'
import { downloadPost } from '@/services/review'
import { canEditTask } from '@/features/localization/model'
import { ShanghaiTime } from '@/components/Time'
import styles from './ImageWorkspace.module.css'

/** 浏览器读成 data URL；后端复用既有的 base64 图片入口，不引入 multipart 依赖。 */
const readAsDataUrl = (file: File) => new Promise<string>((resolve, reject) => {
  const reader = new FileReader()
  reader.onload = () => resolve(String(reader.result ?? ''))
  reader.onerror = () => reject(reader.error ?? new Error('读取失败'))
  reader.readAsDataURL(file)
})

const percent = (value: number | null | undefined) =>
  typeof value === 'number' ? `${(value * 100).toFixed(3)} %` : '—'

export function ImageWorkspace({ images, detail, versions, editing, maxImageCount, imageModel, onChanged, onProgress }: {
  images: readonly ImageAsset[]
  detail: TaskDetail
  versions: Readonly<Record<string, readonly ImageVersion[]>>
  editing: boolean
  maxImageCount?: number | null
  imageModel?: string
  onChanged: () => void | Promise<unknown>
  onProgress: (unseen: number) => void
}) {
  const [current, setCurrent] = useState(0), [seen, setSeen] = useState(() => new Set([0])), [zoom, setZoom] = useState(false)
  const [busy, setBusy] = useState(false), [error, setError] = useState<string | null>(null)
  const [preview, setPreview] = useState<ImageVersion | null>(null)
  useEffect(() => { onProgress(Math.max(0, images.length - seen.size)) }, [images.length, seen, onProgress])
  const choose = (index: number) => { setCurrent(index); setSeen(old => new Set([...old, index])) }
  const image = images[current]
  if (!image) return <Empty description="这篇没有图片" />
  const history = versions[String(image.index)] ?? []
  const disabled = busy || editing || !canEditTask(detail)
  const act = async (run: () => Promise<unknown>) => {
    setBusy(true); setError(null)
    try { await run(); await onChanged() }
    catch (cause) { setError(cause instanceof Error ? cause.message : '这一步没有完成，请刷新后重试') }
    finally { setBusy(false) }
  }
  const upload = (file: File) => act(async () => {
    if (file.size > 32 * 1024 * 1024) throw new Error('图片超过 32 MB，请压缩后再上传')
    return uploadImage(detail, image.index, await readAsDataUrl(file), file.name)
  })
  const pair = (large = false) => <div className={`${styles.pair} ${large ? styles.large : ''}`}>
    <figure><figcaption>原图（英文）</figcaption><img src={image.original_url} alt={`原图 ${current + 1}`} /></figure>
    <figure><figcaption>德语图 {image.manual && <Tag color="green">人工图片</Tag>}
      {image.replaced_at && <>替换于 <ShanghaiTime at={image.replaced_at} /></>}
      {!image.de_present && <span className={styles.missing}>缺德语图，显示的是原图</span>}</figcaption><img src={image.de_url || image.original_url} alt={`德语图 ${current + 1}`} /></figure>
  </div>
  return <section aria-label="图片对照">
    <p className={styles.help}>共 {images.length} 张 · {maxImageCount ? `已核验发布上限 ${maxImageCount} 张` : '发布张数上限未知（待平台录证）'}{imageModel && ` · 当前图片模型：${imageModel}`}</p>
    <div className={styles.controls}><span>第 {current + 1} / {images.length} 张 · {Math.max(0, images.length - seen.size) ? `还有 ${images.length - seen.size} 张没看` : '都看过了'}</span><Space><Button disabled={current === 0} onClick={() => choose(current - 1)}>上一张</Button><Button disabled={current >= images.length - 1} onClick={() => choose(current + 1)}>下一张</Button><Button onClick={() => setZoom(true)}>放大对照</Button></Space></div>
    {!image.de_present && <Alert type="warning" showIcon title="这一张缺少德语图，当前展示原图，请人工核对" />}
    {image.metrics?.changed_pixel_ratio === 0 && <Alert type="warning" showIcon
      title="未检测到明显像素变化"
      description="这张图可能本来就没有需要本地化的英文，也可能生成没有照做；较小的颜色变化不会计入此指标。请先核对图内文字，再决定是否需要优化。" />}
    {image.warnings?.map(warning => <Alert key={warning} type="warning" showIcon title={warning} />)}
    {error && <Alert type="warning" showIcon title={error} />}
    {pair()}
    <div className={styles.sheet} aria-label="图片缩略图">{images.map((item, index) => <button type="button" key={item.index} className={styles.thumb} aria-label={`第 ${index + 1} 张${seen.has(index) ? '（看过）' : '（未看）'}`} aria-current={current === index} onClick={() => choose(index)}><img src={item.de_url || item.original_url} alt="" loading="lazy" /><span>{index + 1} {seen.has(index) && <CheckOutlined />}{!item.de_present && ' · 缺德语图'}</span></button>)}</div>
    <div className={styles.replace}>
      {!detail.read_only && <Button aria-label="下载本篇素材" icon={<DownloadOutlined />} disabled={busy || editing}
        onClick={() => void act(() => downloadPost(detail))}>下载本篇素材</Button>}
      {canEditTask(detail) && <>
      <Upload beforeUpload={file => { void upload(file as File); return false }} showUploadList={false}
        accept="image/jpeg,image/png,image/webp" disabled={disabled}>
        <Button icon={<UploadOutlined />} disabled={disabled} loading={busy}>上传图片替换第 {current + 1} 张</Button>
      </Upload>
      <span className={styles.help}>下载不改变排期状态。替换后这一张按人工图优先使用，这篇仍然留在系统里继续排期发布。支持 JPEG / PNG / WebP，最大 32 MB。</span></>}
    </div>
    {/* 不折叠：换回上一版是个动作，不是可选的技术细节。折起来她就不知道有这条路。 */}
    {(history.length > 1 || history.some(version => !version.current)) && <section className={styles.versionBox} aria-label="这一张的历史版本">
      <h3 className={styles.versionTitle}>这一张生成过 {history.length} 版</h3>
      <ul className={styles.versions}>
        {history.map((version, index) => <li key={version.out_path}>
          <Space wrap>
            <span>{version.current ? <Tag color="blue">当前版</Tag> : null}{version.created_at ? <ShanghaiTime at={version.created_at} /> : '时间未知'}</span>
            <span className={styles.help}>{version.refine_instruction ? `指令：${version.refine_instruction}` : '首次生成'}</span>
            <span className={styles.help}>改动 {percent(version.metrics.changed_pixel_ratio)}</span>
            {version.preview_url && <Button size="small" onClick={() => setPreview(version)}>预览第 {index + 1} 版</Button>}
            {version.current ? null : version.usable
              ? <Button size="small" disabled={disabled} onClick={() => void act(() => selectImageVersion(detail, image.index, version.out_path))}>采用这一版</Button>
              : <span className={styles.missing}>不能采用：{version.unusable_reasons.join('；')}</span>}
          </Space>
        </li>)}
      </ul>
    </section>}
    <Collapse ghost items={[{ key: 'metrics', label: '图片比较指标', children: image.metrics ? <dl className={styles.metrics}><div><dt>dHash 距离</dt><dd>{image.metrics.dhash_distance ?? '—'}</dd></div><div><dt>改动像素占比</dt><dd>{percent(image.metrics.changed_pixel_ratio)}</dd></div><div><dt>宽高比形变</dt><dd>{image.metrics.aspect_drift ?? '—'} %</dd></div><div><dt>缩放</dt><dd>{image.metrics.scale_ratio ?? '—'}×</dd></div><div><dt>生成耗时</dt><dd>{image.metrics.elapsed_s ?? '—'} 秒</dd></div></dl> : <p>这张没有程序生成记录，可能是人工放置的图片。</p> }]} />
    <Modal title={`图片放大对照 · ${current + 1} / ${images.length}`} open={zoom} onCancel={() => setZoom(false)} footer={null} width="90vw">{pair(true)}</Modal>
    <Modal title="历史版本预览（尚未采用）" open={!!preview} onCancel={() => setPreview(null)} footer={null} width="90vw">
      {preview?.preview_url && <div className={`${styles.pair} ${styles.large}`}>
        <figure><figcaption>当前采用的图片</figcaption><img src={image.de_url || image.original_url} alt="当前采用的图片" /></figure>
        <figure><figcaption>待比较的历史版本</figcaption><img src={preview.preview_url} alt="待比较的历史版本" /></figure>
      </div>}
    </Modal>
  </section>
}
