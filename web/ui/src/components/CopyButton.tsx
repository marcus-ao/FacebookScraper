import { useEffect, useState } from 'react'
import { Button, Input, Modal } from 'antd'
import { CheckOutlined, CopyOutlined } from '@ant-design/icons'

import { DisabledReason } from './DisabledReason'

async function copyText(text: string): Promise<void> {
  try {
    if (navigator.clipboard) { await navigator.clipboard.writeText(text); return }
  } catch { /* 非安全来源或权限限制时，尝试用户点击触发的复制。 */ }
  const focused = document.activeElement instanceof HTMLElement ? document.activeElement : null
  const input = document.createElement('textarea')
  input.value = text; input.readOnly = true
  input.style.position = 'fixed'; input.style.left = '-9999px'
  document.body.append(input)
  try {
    input.focus(); input.select()
    if (!document.execCommand('copy')) throw new Error('Copy unavailable')
  } finally { input.remove(); focused?.focus() }
}

/**
 * 复制一段确定的文本。
 *
 * ⛔ `text` 只接受服务端算好的成品，调用方不要在前端临时拼一份：复制出去的内容会被直接
 * 贴进 Business Suite，和实际发布内容不一致比没有这个按钮更糟。拿不到就传 undefined。
 */
export function CopyButton({ text, label, disabledReason }: {
  readonly text: string | undefined
  readonly label: string
  readonly disabledReason?: string
}) {
  const [copied, setCopied] = useState(false)
  const [manual, setManual] = useState(false)
  useEffect(() => setCopied(false), [text])
  useEffect(() => {
    if (!copied) return
    const timer = window.setTimeout(() => setCopied(false), 2000)
    return () => window.clearTimeout(timer)
  }, [copied])
  const copy = async () => {
    try { await copyText(text ?? ''); setCopied(true) }
    catch { setCopied(false); setManual(true) }
  }
  const reason = disabledReason ?? (text ? '' : '还没有可复制的成品文案')
  return (
    <><DisabledReason label={label} reason={reason}>
      <Button size="small" disabled={!!reason} onClick={() => void copy()}
        icon={copied ? <CheckOutlined /> : <CopyOutlined />}>
        {copied ? '已复制' : label}
      </Button>
    </DisabledReason>
      <Modal open={manual} title="手动复制发布文案" onCancel={() => setManual(false)}
        footer={<Button onClick={() => setManual(false)}>关闭</Button>}>
        <p>浏览器未允许自动复制，请选中下方文案后复制。</p>
        <Input.TextArea aria-label="完整发布文案" value={text} readOnly autoSize={{ minRows: 5, maxRows: 14 }}
          onFocus={event => event.target.select()} />
      </Modal>
    </>
  )
}
