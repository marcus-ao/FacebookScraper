import { useEffect, useState } from 'react'
import { Alert, Button, Empty, Modal, Space, Upload } from 'antd'
import { DownloadOutlined, UploadOutlined } from '@ant-design/icons'
import type { ImageAsset, ImageVersion, TaskDetail } from '@/types/domain'
import { selectImageVersion, selectOriginalImage, uploadImage } from '@/services/jobs'
import { confirmImage, downloadPost } from '@/services/review'
import { canEditTask } from '@/features/localization/model'
import styles from './ImageWorkspace.module.css'

/** 浏览器读成 data URL；后端复用既有的 base64 图片入口，不引入 multipart 依赖。 */
const readAsDataUrl = (file: File) => new Promise<string>((resolve, reject) => {
  const reader = new FileReader()
  reader.onload = () => resolve(String(reader.result ?? ''))
  reader.onerror = () => reject(reader.error ?? new Error('读取失败'))
  reader.readAsDataURL(file)
})

export function ImageWorkspace({ images, detail, versions, editing, onChanged, onGenerate }: {
  images: readonly ImageAsset[]
  detail: TaskDetail
  versions: Readonly<Record<string, readonly ImageVersion[]>>
  editing: boolean
  onChanged: () => void | Promise<unknown>
  onGenerate?: (index: number) => void
}) {
  const [current, setCurrent] = useState(0)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [sourceFailed, setSourceFailed] = useState(false)
  const [proposedFailed, setProposedFailed] = useState(false)
  const [zoom, setZoom] = useState(false)
  const [versionsOpen, setVersionsOpen] = useState(false)
  const [preview, setPreview] = useState<ImageVersion | null>(null)
  const image = images[Math.min(current, images.length - 1)]
  useEffect(() => { setSourceFailed(false); setProposedFailed(false); setError(null) },
    [image?.index, image?.original_url, image?.de_url])
  if (!image) return <Empty description="本篇无需审核图片" />

  const number = current + 1
  const history = versions[String(image.index)] ?? []
  const confirmed = detail.content_review.images.find(item => item.index === image.index)?.confirmed === true
  const sourceReadable = !!image.source_image_sha256 && !sourceFailed
  const proposedUrl = image.ready ? (image.de_url || image.original_url) : null
  const proposedReadable = !!proposedUrl && !proposedFailed
  const disabled = busy || editing || !canEditTask(detail)
  const act = async (run: () => Promise<unknown>) => {
    if (busy) return
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
    <figure><figcaption>第 {number} 张原图</figcaption>
      {image.source_image_sha256 ? <img src={image.original_url} alt={`原图 ${number}`} onError={() => setSourceFailed(true)} />
        : <div className={styles.empty}>原图无法读取</div>}
    </figure>
    <figure><figcaption>拟发布图片{image.selection === 'original_confirmed' && ' · 当前选用原图'}{image.manual && ' · 人工图片'}</figcaption>
      {proposedUrl ? <img src={proposedUrl} alt={`拟发布图片 ${number}`} onError={() => setProposedFailed(true)} />
        : <div className={styles.empty}>暂无可用的拟发布图片</div>}
    </figure>
  </div>
  return <section aria-label="图片对照">
    <div className={styles.controls}><strong>第 {number} / {images.length} 张</strong><Space>
      {images.length > 1 && <><Button disabled={current === 0} onClick={() => setCurrent(current - 1)}>上一张</Button>
        <Button disabled={current >= images.length - 1} onClick={() => setCurrent(current + 1)}>下一张</Button></>}
      <Button onClick={() => setZoom(true)}>放大对照</Button>
    </Space></div>
    {!sourceReadable && <Alert type="warning" showIcon title={`第 ${number} 张原图无法读取，请核对归档素材后刷新`}
      action={<Button size="small" onClick={() => void onChanged()}>刷新素材</Button>} />}
    {sourceReadable && !proposedUrl && <p className={styles.note}>第 {number} 张尚无可用的拟发布图片。请选择原图、上传图片或生成德语图。</p>}
    {proposedFailed && <Alert type="warning" showIcon title={`第 ${number} 张拟发布图片无法读取，请刷新后核对素材`} />}
    {image.metrics?.changed_pixel_ratio === 0 && proposedUrl && <p className={styles.note}>第 {number} 张未见明显改动；可能无需修改，也可能未按要求生成，请核对图中文字。</p>}
    {image.warnings?.map((warning, index) => <p className={styles.note} key={index}>
      {warning.includes('宽高比') || warning.includes('画幅') ? `请核对第 ${number} 张图片的画幅是否适合发布。`
        : warning.includes('多个人工图片候选') ? `第 ${number} 张有多个待选图片，请核对归档素材并保留一张。` : warning}
    </p>)}
    {error && <Alert type="warning" showIcon title={error} />}
    {pair()}
    {images.length > 1 && <div className={styles.sheet} aria-label="图片缩略图">{images.map((item, index) => {
      const itemConfirmed = detail.content_review.images.find(decision => decision.index === item.index)?.confirmed === true
      return <button type="button" key={item.index} className={styles.thumb}
        aria-label={`第 ${index + 1} 张（${itemConfirmed ? '已确认' : '待确认'}）`} aria-current={current === index}
        onClick={() => setCurrent(index)}>
        {item.ready ? <img src={item.de_url || item.original_url} alt="" loading="lazy" /> : <span className={styles.thumbEmpty}>暂无图</span>}
        <span>{index + 1} · {itemConfirmed ? '已确认' : '待确认'}</span>
      </button>
    })}</div>}
    <div className={styles.actions}>
      {confirmed && <span role="status">第 {number} 张已确认</span>}
      {canEditTask(detail) && <>
        {sourceReadable && image.selection !== 'original_confirmed' && <Button disabled={disabled} loading={busy}
          onClick={() => void act(() => selectOriginalImage(detail, image, 'original', true))}>使用原图并确认</Button>}
        {sourceReadable && proposedReadable && (confirmed
          ? <Button disabled={disabled} onClick={() => void act(() => confirmImage(detail, image.index, false))}>撤销第 {number} 张确认</Button>
          : <Button type="primary" disabled={disabled} loading={busy}
            onClick={() => void act(() => confirmImage(detail, image.index, true))}>确认第 {number} 张图片用于发布</Button>)}
        <Upload beforeUpload={file => { void upload(file as File); return false }} showUploadList={false}
          accept="image/jpeg,image/png,image/webp" disabled={disabled}>
          <Button icon={<UploadOutlined />} disabled={disabled} loading={busy}>上传图片替换第 {number} 张</Button>
        </Upload>
        {!proposedUrl && onGenerate && <Button disabled={disabled} onClick={() => onGenerate(image.index)}>生成德语图</Button>}
      </>}
    </div>
    <p className={styles.note}>上传图片后仍由系统继续排期发布；新图片和采用其他版本都需重新确认。</p>
    {(history.length > 1 || history.some(version => !version.current)) && <>
      <Button type="link" onClick={() => setVersionsOpen(true)}>比较其他版本</Button>
      <Modal title={`第 ${number} 张 · 选择图片版本`} open={versionsOpen} onCancel={() => setVersionsOpen(false)} footer={null}>
        <p className={styles.note}>先比较画面与文字；采用其他版本后，再确认这张图片。</p>
        <ul className={styles.versions}>{history.map((version, index) => <li key={version.out_path}>
          <span>{version.current ? '当前版' : `第 ${index + 1} 版`}{version.refine_instruction && ` · ${version.refine_instruction}`}</span>
          <Space>
            {version.preview_url && <Button size="small" onClick={() => setPreview(version)}>对比画面</Button>}
            {!version.current && (version.usable ? <Button size="small" disabled={disabled}
              onClick={() => void act(async () => { await selectImageVersion(detail, image.index, version.out_path); setVersionsOpen(false) })}>采用这一版</Button>
              : <span className={styles.note}>此版不可采用：{version.unusable_reasons.join('；')}</span>)}
          </Space>
        </li>)}</ul>
      </Modal>
    </>}
    {!detail.read_only && <Button type="link" icon={<DownloadOutlined />} disabled={busy || editing}
      onClick={() => void act(() => downloadPost(detail))}>下载本篇素材</Button>}
    <Modal title={`图片放大对照 · ${number} / ${images.length}`} open={zoom} onCancel={() => setZoom(false)} footer={null} width="90vw">{pair(true)}</Modal>
    <Modal title="版本画面对比" open={!!preview} onCancel={() => setPreview(null)} footer={null} width="90vw">
      {preview?.preview_url && <div className={`${styles.pair} ${styles.large}`}>
        <figure><figcaption>当前拟发布图片</figcaption>{proposedUrl && <img src={proposedUrl} alt="当前拟发布图片" />}</figure>
        <figure><figcaption>待比较的图片</figcaption><img src={preview.preview_url} alt="待比较的图片" /></figure>
      </div>}
    </Modal>
  </section>
}
