import type { ReactNode } from 'react'
import { ShanghaiTime } from '@/components/Time'
import { AUTHOR_KIND_LABEL } from '@/lib/format'
import type { DetailStepId } from '@/app/search-params'
import type { TaskDetail } from '@/types/domain'
import type { ReviewStep, ReviewTodo, StepStatus } from './step-model'
import styles from './ReviewDetailPage.module.css'

const statusCopy: Record<StepStatus, string> = {
  complete: '已完成', pending: '待处理', not_required: '无需审核', view_only: '可查看',
}

/** 箭头只提示“会跳转”，读屏时不念出来。 */
export const Arrow = () => <span aria-hidden="true"> ↗</span>

export function SourceStrip({ detail }: { detail: TaskDetail }) {
  const { meta } = detail
  return <div className={styles.source} aria-label="原帖来源">
    <span>来源账号：{meta.account}</span>
    <span>{AUTHOR_KIND_LABEL[meta.author_kind]}：{meta.owner || '作者待核对'}</span>
    {meta.coauthors.length > 0 && <span>合作方：{meta.coauthors.join('、')}</span>}
    <span>原帖发布：<ShanghaiTime at={meta.created_at} /></span>
    {meta.permalink && <a href={meta.permalink} target="_blank" rel="noopener noreferrer">查看原帖<Arrow /></a>}
  </div>
}

export function ReviewTodoSummary({ todos, expanded, onExpand, onNavigate }: {
  todos: readonly ReviewTodo[]; expanded: boolean; onExpand: () => void; onNavigate: (step: DetailStepId) => void
}) {
  if (todos.length === 0) return <p className={styles.todoEmpty}>当前没有待处理事项</p>
  const shown = expanded ? todos : todos.slice(0, 3)
  return <section className={styles.todoSummary} aria-label="审核待办">
    <strong>待处理 {todos.length} 项</strong>
    <ul>{shown.map((item, index) => <li key={`${item.step}-${item.text}-${index}`}>
      <button type="button" onClick={() => onNavigate(item.step)}>{item.text}<Arrow /></button>
    </li>)}</ul>
    {todos.length > 3 && <button type="button" className={styles.moreTodos} onClick={onExpand}>
      {expanded ? '收起' : `查看全部 ${todos.length} 项`}
    </button>}
  </section>
}

export function ReviewStepNav({ steps, active, onChange }: {
  steps: readonly ReviewStep[]; active: DetailStepId; onChange: (step: DetailStepId) => void
}) {
  return <nav className={styles.stepNav} aria-label="审核步骤">
    <div className={styles.stepButtons}>{steps.map((step, index) => <button key={step.id} type="button"
      aria-current={active === step.id ? 'step' : undefined} onClick={() => onChange(step.id)}>
      <span className={styles.stepNumber}>{index + 1}</span><span>{step.label}</span><small>{statusCopy[step.status]}</small>
    </button>)}</div>
    <label className={styles.stepSelect}>审核步骤<select value={active} onChange={event => onChange(event.target.value as DetailStepId)}>
      {steps.map((step, index) => <option key={step.id} value={step.id}>{index + 1}. {step.label} · {statusCopy[step.status]}</option>)}
    </select></label>
  </nav>
}

export function ReviewStepPanel({ id, active, opened, children }: {
  id: DetailStepId; active: DetailStepId; opened: boolean; children: ReactNode
}) {
  if (!opened) return null
  return <section hidden={active !== id} data-step={id} className={styles.stepPanel}>{children}</section>
}
