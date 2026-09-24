import { useState } from 'react'
import { LeftOutlined, RightOutlined } from '@ant-design/icons'
import { Button, Modal } from 'antd'
import type { ApprovalController } from '@/hooks/useApproval'
import type { FrozenPreview } from '@/types/domain'
import { PlatformLabel } from '@/components/PlatformLabel'
import { PLATFORM_LABEL } from '@/lib/format'
import { cx } from '@/lib/css'
import styles from './SchedulePreviewDialog.module.css'

function PreviewImage({ image }: { image: FrozenPreview['images'][number] }) {
  const [failed, setFailed] = useState(false)
  return failed
    ? <p className={styles.missing} role="status">第 {image.index + 1} 张图片暂时无法显示，请返回核对后重试。</p>
    : <img className={styles.image} src={image.url} alt={`发布图片 ${image.index + 1}`} onError={() => setFailed(true)} />
}

function PreviewGallery({ images }: { images: FrozenPreview['images'] }) {
  const [current, setCurrent] = useState(0)
  const image = images[current]
  if (!image) return null
  return <section className={styles.media} aria-label="发布图片">
    <div className={styles.galleryToolbar}>
      <span aria-live="polite">图片 {current + 1} / {images.length}</span>
      {images.length > 1 && <div className={styles.arrows}>
        <Button type="text" aria-label="上一张图片" icon={<LeftOutlined />} disabled={current === 0} onClick={() => setCurrent(current - 1)} />
        <Button type="text" aria-label="下一张图片" icon={<RightOutlined />} disabled={current === images.length - 1} onClick={() => setCurrent(current + 1)} />
      </div>}
    </div>
    <div className={styles.stage}><PreviewImage key={image.url} image={image} /></div>
    {images.length > 1 && <div className={styles.thumbnails} aria-label="选择预览图片">
      {images.map((item, index) => <button key={item.index} type="button" className={styles.thumbnail}
        aria-label={`查看第 ${index + 1} 张图片`} aria-pressed={current === index} onClick={() => setCurrent(index)}>
        <img src={item.url} alt="" loading="lazy" /><span>{index + 1}</span>
      </button>)}
    </div>}
  </section>
}

interface SchedulePreviewDialogProps {
  snapshot: ApprovalController['snapshot']
  busy: boolean
  onCancel: () => void
  onConfirm: () => void
}

export function SchedulePreviewDialog({ snapshot, busy, onCancel, onConfirm }: SchedulePreviewDialogProps) {
  const preview = snapshot?.preview
  return <Modal title="确认发布内容与时间" open={!!snapshot} centered width={1080}
    className={cx(styles.dialog)} destroyOnHidden
    classNames={{ container: cx(styles.container), header: cx(styles.header), title: cx(styles.title),
      body: cx(styles.body), footer: cx(styles.footer), close: cx(styles.close) }}
    mask={{ closable: !busy }} keyboard={!busy} closable={{ disabled: busy }}
    onCancel={() => { if (!busy) onCancel() }} onOk={onConfirm}
    okText="确认并创建排期" cancelText="继续核对" confirmLoading={busy}
    okButtonProps={{ size: 'large' }} cancelButtonProps={{ disabled: busy, size: 'large' }}>
    {snapshot && preview && <>
      <div className={styles.summary}>
        <div className={styles.identity}>
          <span className={styles.platformMark}><PlatformLabel platform={preview.target.channel} iconOnly /></span>
          <div className={styles.account}><strong>{preview.target.account}</strong><span>{PLATFORM_LABEL[preview.target.channel]}</span></div>
        </div>
        <div className={styles.schedule}><span>发布时间</span><time dateTime={snapshot.body.scheduled_at}>{snapshot.body.scheduled_at.replace('T', ' ')}</time></div>
      </div>
      <div className={styles.post} data-has-images={preview.images.length > 0}>
        {preview.images.length > 0 && <PreviewGallery key={preview.snapshot_id} images={preview.images} />}
        <section className={styles.caption} aria-label="发布文案" lang="de" tabIndex={0}>{preview.text}</section>
      </div>
    </>}
  </Modal>
}
