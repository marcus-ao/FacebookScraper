import { useEffect, useState } from 'react'
import { Alert, Button, Empty, Modal, Space, Upload } from 'antd'
import { UploadOutlined } from '@ant-design/icons'
import type { ImageAsset, ImageVersion, TaskDetail } from '@/types/domain'
import { selectImageVersion, selectOriginalImage, uploadImage } from '@/services/jobs'
import { confirmImage } from '@/services/review'
import { isConflict } from '@/services/http'
import { businessNotice } from '@/lib/action-reasons'
import { canEditTask } from '@/features/localization/model'
import styles from './ImageWorkspace.module.css'

/** 版本不可采用的原因写给业务看：说清哪样东西过时了，不提提示词或文件路径。 */
const UNUSABLE_COPY: readonly (readonly [RegExp, string])[] = [
  [/文件已不在归档/, '图片文件已不可用'],
  [/提示词/, '按旧的生成要求制作'],
  [/更早版本的原图/, '对应的是更早的原图'],
  [/更早版本的德语正文/, '对应的是更早的德语正文'],
  [/人工图片/, '这一张已采用人工图片，旧版本仅供查看'],
  [/原路径已被替换/, '已被新图片替换，仅供查看'],
]
const unusableCopy = (reason: string) => UNUSABLE_COPY.find(([pattern]) => pattern.test(reason))?.[1] ?? '这一版已过时，仅供查看'

/** 浏览器读成 data URL；后端复用既有的 base64 图片入口，不引入 multipart 依赖。 */
const readAsDataUrl = (file: File) => new Promise<string>((resolve, reject) => {
  const reader = new FileReader()
  reader.onload = () => resolve(String(reader.result ?? ''))
  reader.onerror = () => reject(reader.error ?? new Error('读取失败'))
  reader.readAsDataURL(file)
})

export function ImageWorkspace({ images, detail, versions, editing, onChanged, onGenerate, onCurrentChange }: {
  images: readonly ImageAsset[]
  detail: TaskDetail
  versions: Readonly<Record<string, readonly ImageVersion[]>>
  editing: boolean
  onChanged: () => void | Promise<unknown>
  onGenerate?: (index: number) => void
  onCurrentChange?: (index: number) => void
}) {
  const [current, setCurrent] = useState(0)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [sourceFailed, setSourceFailed] = useState(false)
  const [proposedFailed, setProposedFailed] = useState(false)
  const [retryNonce, setRetryNonce] = useState(0)
  const [zoom, setZoom] = useState(false)
  const [versionsOpen, setVersionsOpen] = useState(false)
  const [preview, setPreview] = useState<ImageVersion | null>(null)
  const image = images[Math.min(current, images.length - 1)]
  useEffect(() => { setSourceFailed(false); setProposedFailed(false); setError(null) },
    [image?.index, image?.original_url, image?.de_url])
  if (!image) return <Empty description="本篇无需审核图片" />

  const number = current + 1
  const choose = (index: number) => { setCurrent(index); onCurrentChange?.(index) }
  const history = versions[String(image.index)] ?? []
  const confirmed = detail.content_review.images.find(item => item.index === image.index)?.confirmed === true
  const sourceReadable = !!image.source_image_sha256 && !sourceFailed
  const proposedUrl = image.ready ? (image.de_url || image.original_url) : null
  const proposedReadable = !!proposedUrl && !proposedFailed
  const disabled = busy || editing || !canEditTask(detail)
  const imageUrl = (url: string) => retryNonce ? `${url}${url.includes('?') ? '&' : '?'}review_retry=${retryNonce}` : url
  const retry = async () => {
    try {
      await onChanged()
      setSourceFailed(false)
      setProposedFailed(false)
      setRetryNonce(value => value + 1)
      setError(null)
    } catch { setError('素材暂时无法刷新，请稍后再试') }
  }
  const act = async (run: () => Promise<unknown>) => {
    if (busy) return
    setBusy(true); setError(null)
    try { await run(); await onChanged() }
    catch (cause) {
      // 后台出图或换版后，旧页面的确认会被拒；载入最新图片，让人看过再确认。
      // 先载入再提示：图片地址变化会清空旧提示。
      if (isConflict(cause)) {
        try { await onChanged(); setError('这张图片已有更新，已载入最新内容，请核对后再确认') }
        catch { setError('这张图片已有更新，请刷新后核对再确认') }
      } else setError(businessNotice(cause instanceof Error ? cause.message : '', '这一步没有完成，请刷新后重试'))
    }
    finally { setBusy(false) }
  }
  const upload = (file: File) => act(async () => {
    if (file.size > 32 * 1024 * 1024) throw new Error('图片超过 32 MB，请压缩后再上传')
    return uploadImage(detail, image.index, await readAsDataUrl(file), file.name)
  })
  const pair = (large = false) => <div className={`${styles.pair} ${large ? styles.large : ''}`}>
    <figure><figcaption>第 {number} 张原图</figcaption>
      {image.source_image_sha256 ? <img src={imageUrl(image.original_url)} alt={`原图 ${number}`} onError={() => setSourceFailed(true)} />
        : <div className={styles.empty}>原图无法读取</div>}
    </figure>
    <figure><figcaption>拟发布图片{image.selection === 'original_confirmed' && ' · 当前选用原图'}{image.manual && ' · 人工图片'}</figcaption>
      {proposedUrl ? <img src={imageUrl(proposedUrl)} alt={`拟发布图片 ${number}`} onError={() => setProposedFailed(true)} />
        : <div className={styles.empty}>暂无可用的拟发布图片</div>}
    </figure>
  </div>
  return <section aria-label="图片对照">
    <div className={styles.controls}><strong>第 {number} / {images.length} 张</strong><Space>
      {images.length > 1 && <><Button disabled={current === 0} onClick={() => choose(current - 1)}>上一张</Button>
        <Button disabled={current >= images.length - 1} onClick={() => choose(current + 1)}>下一张</Button></>}
      <Button onClick={() => setZoom(true)}>放大对照</Button>
    </Space></div>
    {!sourceReadable && <Alert type="warning" showIcon title={`第 ${number} 张原图无法读取，请核对归档素材后刷新`}
      action={<Button size="small" onClick={() => void retry()}>刷新素材</Button>} />}
    {sourceReadable && !proposedUrl && <p className={styles.note}>第 {number} 张尚无可用的拟发布图片。请选择原图、上传图片或生成德语图。</p>}
    {proposedFailed && <Alert type="warning" showIcon title={`第 ${number} 张拟发布图片无法读取，请刷新后核对素材`}
      action={sourceFailed ? undefined : <Button size="small" onClick={() => void retry()}>刷新素材</Button>} />}
    {image.metrics?.changed_pixel_ratio === 0 && proposedUrl && <p className={styles.note}>第 {number} 张未见明显改动；可能无需修改，也可能未按要求生成，请核对图中文字。</p>}
    {image.warnings?.map((warning, index) => <p className={styles.note} key={index}>
      {warning.includes('宽高比') || warning.includes('画幅') ? `请核对第 ${number} 张图片的画幅是否适合发布。`
        : warning.includes('多个人工图片候选') ? `第 ${number} 张有多个待选图片，请核对归档素材并保留一张。`
        : businessNotice(warning, `请核对第 ${number} 张图片后再确认。`)}
    </p>)}
    {error && <Alert type="warning" showIcon title={error} />}
    {pair()}
    {images.length > 1 && <div className={styles.sheet} aria-label="图片缩略图">{images.map((item, index) => {
      const itemConfirmed = detail.content_review.images.find(decision => decision.index === item.index)?.confirmed === true
      return <button type="button" key={item.index} className={styles.thumb}
        aria-label={`第 ${index + 1} 张（${itemConfirmed ? '已确认' : '待确认'}）`} aria-current={current === index}
        onClick={() => choose(index)}>
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
              : <span className={styles.note}>此版不可采用：{[...new Set(version.unusable_reasons.map(unusableCopy))].join('；')}</span>)}
          </Space>
        </li>)}</ul>
      </Modal>
    </>}
    <Modal title={`图片放大对照 · ${number} / ${images.length}`} open={zoom} onCancel={() => setZoom(false)} footer={null} width="90vw">{pair(true)}</Modal>
    <Modal title="版本画面对比" open={!!preview} onCancel={() => setPreview(null)} footer={null} width="90vw">
      {preview?.preview_url && <div className={`${styles.pair} ${styles.large}`}>
        <figure><figcaption>当前拟发布图片</figcaption>{proposedUrl && <img src={proposedUrl} alt="当前拟发布图片" />}</figure>
        <figure><figcaption>待比较的图片</figcaption><img src={preview.preview_url} alt="待比较的图片" /></figure>
      </div>}
    </Modal>
  </section>
}
