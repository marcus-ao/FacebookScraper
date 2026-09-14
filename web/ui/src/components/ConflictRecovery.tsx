import { Alert, Button } from 'antd'

/** 只展示冲突及恢复入口；各业务功能通过 onRecover 保留并恢复人工修改。 */

export type ConflictKind = 'draft' | 'tags' | 'settings' | 'schedule'

interface ConflictCopy {
  readonly message: string
  readonly description: string
  readonly action: string
}

export const CONFLICT_COPY: Readonly<Record<ConflictKind, ConflictCopy>> = {
  draft: {
    message: '这一篇在别处被改过了',
    description: '你写的内容还在，没有被覆盖。载入最新版本之后再保存一次就可以。',
    action: '载入最新内容并保留我的修改',
  },
  tags: {
    message: '这一篇的分类在别处被改过了',
    description: '你选的分类还在。先载入最新分类，确认之后再保存。',
    action: '载入最新分类',
  },
  settings: {
    message: '设置在别处被改过了',
    description: '你填的内容还在，没有被覆盖。载入最新设置之后再保存一次就可以。',
    action: '载入最新设置并保留我的修改',
  },
  schedule: {
    message: '这一篇的状态在别处变过了',
    description: '你填的内容还在。刷新一下当前状态，再决定要不要继续。',
    action: '刷新状态，保留填写内容',
  },
}

export interface ConflictRecoveryProps {
  readonly kind: ConflictKind
  readonly onRecover: () => void
  readonly recovering?: boolean
  readonly actionLabel?: string
}

export function ConflictRecovery({
  kind,
  onRecover,
  recovering = false,
  actionLabel,
}: ConflictRecoveryProps) {
  const copy = CONFLICT_COPY[kind]
  return (
    <Alert
      type="warning"
      showIcon
      role="alert"
      data-conflict={kind}
      message={copy.message}
      description={copy.description}
      action={
        <Button size="small" loading={recovering} onClick={onRecover}>
          {actionLabel ?? copy.action}
        </Button>
      }
    />
  )
}
