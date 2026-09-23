import type { MirrorStatus, RuntimeSnapshot, RuntimeStage } from '@/types/domain'
export type RuntimeTone = 'success' | 'processing' | 'warning' | 'default'
const attention = new Set(['failed','interrupted','uncertain','blocked','missing','needs_attention','configuration_invalid','state_unreadable'])
export function stageSummary(stage: RuntimeStage): { tone: RuntimeTone; label: string; conclusion: string } {
  if (attention.has(stage.status)) return { tone: 'warning', label: '需要处理', conclusion: ['请核对监测是否暂停。','源内容或归档尚未准备好。','本轮内容处理需要人工核对。','部分提醒或投递条件需要核对。','当前发布条件尚未齐备。'][stage.number - 1] ?? '请人工核对当前状态。' }
  if (['pending','running'].includes(stage.status)) return { tone: 'processing', label: '正在处理', conclusion: '本轮仍在处理中。' }
  if (stage.status === 'active') return { tone: 'processing', label: '进程活跃', conclusion: '已观测到调度进程，业务结果请查看各阶段。' }
  if (stage.status === 'disabled') return { tone: 'default', label: '未启用', conclusion: '这项能力当前未启用。' }
  if ((stage.number === 2 && stage.status === 'available') || (stage.number === 3 && stage.status === 'completed') || (stage.number === 4 && stage.status === 'ready') || (stage.number === 5 && stage.status === 'available')) return { tone: 'success', label: stage.status === 'completed' ? '处理完成' : '条件可用', conclusion: stage.number === 3 ? '本轮处理已完成，发布仍需人工审校。' : stage.number === 5 ? '已取得发布前置条件，单篇仍需确认内容和时刻。' : stage.number === 4 ? '当前已知提醒没有待核对的投递。' : '已找到源内容归档。' }
  return { tone: 'default', label: '未知', conclusion: '现有记录不足以确认这一阶段的结果。' }
}
export function runtimeSummary(snapshot: RuntimeSnapshot | undefined): { tone: RuntimeTone; label: string } {
  if (!snapshot) return { tone: 'default', label: '未知' }
  if (snapshot.stages.some(stage => stageSummary(stage).tone === 'warning' || (stage.unconfirmed_attempts ?? 0) > 0)) return { tone: 'warning', label: '需要处理' }
  // 进程活跃不代表业务成功；阶段不全或未知时不推定正常。
  if (snapshot.stages.length !== 5 || snapshot.stages.some(stage => stageSummary(stage).tone === 'default')) return { tone: 'default', label: '未知' }
  return { tone: 'processing', label: '已有运行记录' }
}

export function mirrorSummary(mirror: MirrorStatus): { tone: RuntimeTone; label: string; conclusion: string } {
  const { status, counts } = mirror
  if (status === 'disabled') return { tone: 'default', label: '未启用', conclusion: '云盘镜像尚未启用，本地归档不受影响。' }
  if (status === 'idle') return { tone: 'default', label: '尚无记录', conclusion: '尚无待同步内容；此页面没有向云端查询。' }
  if (status === 'pending') return { tone: 'processing', label: '等待同步', conclusion: `有 ${counts.pending} 项等待同步；完成前请以本地归档为准。` }
  if (status === 'uncertain') return { tone: 'warning', label: '待人工核对', conclusion: `有 ${counts.uncertain} 项同步结果待人工核对，暂不判断为已完成。` }
  if (status === 'blocked') return { tone: 'warning', label: '需要维护', conclusion: `有 ${counts.blocked} 项同步已暂停，请由维护人员核对。` }
  if (status === 'completed') return { tone: 'success', label: '已有完成记录', conclusion: '本地记录显示已有完成项；此页面没有向云端再次确认。' }
  return { tone: 'default', label: '未知', conclusion: '现有镜像记录不足以确认同步结果。' }
}

/** 维护者只需待核对操作编号；云端令牌、摘要和原始异常不属于页面信息。 */
export function mirrorDiagnostics(mirror: MirrorStatus) {
  return {
    enabled: mirror.enabled, status: mirror.status, counts: mirror.counts,
    last_success_at: mirror.last_success_at, last_error: mirror.last_error,
    operations: mirror.operations.filter(operation => ['uncertain', 'blocked'].includes(operation.status ?? ''))
      .map(operation => ({ id: operation.id, status: operation.status })),
  }
}
export const runtimeSignature = (snapshot: RuntimeSnapshot) => JSON.stringify({ stages: snapshot.stages, processing: snapshot.business.processing, last_success: snapshot.business.last_successful_run, alive: snapshot.process.alive, heartbeat: snapshot.heartbeat.status })
