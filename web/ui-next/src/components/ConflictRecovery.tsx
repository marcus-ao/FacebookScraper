import { Alert, Button } from 'antd'

/**
 * 版本冲突的统一恢复展示。
 *
 * 后端的 409 表示"版本/锁/许可/能力冲突"（web/DESIGN.md §5）。
 * 会发生的场景很实在：她开着一篇在改德语，同一篇在别处被动过 —— 保存时
 * 服务端发现 revision 对不上，拒绝写入。**这是保护，不是故障。**
 *
 * ⛔ **一个工程词都不许露出来。**
 *
 * 她不是工程师。`HTTP 409`、`revision mismatch`、`CAS`、`version conflict`、
 * `precondition failed` 这些词对她没有任何可操作含义，只会把一个
 * "点一下就能继续"的情况变成一个"要找人问"的情况。
 * 所以文案只说两件事：**发生了什么**，以及**按这个按钮会怎样**。
 *
 * ⛔ 也不许写成"保存失败"。她的修改**没有丢** —— 这正是要说清楚的部分。
 *
 * 职责边界（本会话 §24）：**这个组件只负责展示**。
 * 具体怎么重新载入、怎么把她的修改合回去，由业务 feature 通过 `onRecover` 提供 ——
 * 因为不同的冲突恢复动作完全不同（正文草稿要保留、分类要重读、设置要重取版本）。
 */

export type ConflictKind = 'draft' | 'tags' | 'settings' | 'schedule'

interface ConflictCopy {
  readonly message: string
  readonly description: string
  readonly action: string
}

/**
 * 四种冲突各一句话。
 *
 * 写法上刻意保留旧 UI 的动作文案语气（「载入最新分类」「刷新状态，保留填写内容」），
 * 她已经认识这几个说法了。
 */
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
  /** 恢复动作。业务 feature 提供，组件不知道它做什么。 */
  readonly onRecover: () => void
  /** 恢复进行中。 */
  readonly recovering?: boolean
  /** 覆盖默认的按钮文案。少用 —— 默认那几句是统一口径。 */
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
      // role="alert"：她可能正在输入框里，没看着这块（DESIGN.md §13 动态区域）。
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
