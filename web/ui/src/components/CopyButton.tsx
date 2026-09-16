import { useEffect, useState } from 'react'
import { Button } from 'antd'
import { CheckOutlined, CopyOutlined } from '@ant-design/icons'

import { DisabledReason } from './DisabledReason'

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
  useEffect(() => {
    if (!copied) return
    const timer = window.setTimeout(() => setCopied(false), 2000)
    return () => window.clearTimeout(timer)
  }, [copied])
  // 复制失败不弹错：浏览器可能因为焦点或权限拒绝，此时按钮不变成"已复制"就是反馈。
  const copy = () => void navigator.clipboard?.writeText(text ?? '').then(() => setCopied(true), () => setCopied(false))
  const reason = disabledReason ?? (text ? '' : '还没有可复制的成品文案')
  return (
    <DisabledReason label={label} reason={reason}>
      <Button size="small" disabled={!!reason} onClick={copy}
        icon={copied ? <CheckOutlined /> : <CopyOutlined />}>
        {copied ? '已复制' : label}
      </Button>
    </DisabledReason>
  )
}
