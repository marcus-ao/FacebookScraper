<script setup>
import { onMounted, onUnmounted, ref } from 'vue'
import { api } from '../api.js'
const data = ref(null), busy = ref(false), error = ref('')
const labels = { available: '可用', active: '进程活跃', not_observed: '尚未观测', idle: '空闲',
  pending: '等待处理', running: '正在处理', completed: '处理完成', failed: '需要检查',
  interrupted: '处理已中断', uncertain: '结果待核对', blocked: '前置条件未齐', missing: '尚未配置',
  ready: '可用', needs_attention: '需要处理', disabled: '未启用', configuration_invalid: '配置需要修正' }
const checks = { browser_isolation: '浏览器隔离', account_context: '发布账号证据', submission: '提交成功信号',
  readback: '远端回读证据', facebook_controls: 'FB 单渠道控件', instagram_controls: 'IG 单渠道控件',
  facebook_window: 'FB 排期范围', instagram_window: 'IG 排期范围', calendar_coverage: '完整月历读取' }
function time(value) { return value ? new Intl.DateTimeFormat('zh-CN', { timeZone: 'Asia/Shanghai',
  dateStyle: 'short', timeStyle: 'medium' }).format(new Date(value)) : '尚无记录' }
async function load() {
  if (busy.value) return
  busy.value = true; error.value = ''
  try { data.value = await api.runtime() } catch (exc) { error.value = exc.message }
  finally { busy.value = false }
}
async function resolve(item, action) {
  let messageId = ''
  if (action === 'delivered') { messageId = window.prompt('请填写已核对到的飞书消息 ID'); if (!messageId) return }
  else if (!window.confirm('已在飞书核对这条消息没有送达？确认后，系统将用原消息内容和去重标识恢复投递。')) return
  busy.value = true; error.value = ''
  try { await api.resolveNotification(item, action, messageId) } catch (exc) { error.value = exc.message }
  finally { busy.value = false }
  if (!error.value) await load()
}
async function recoverProcessing() {
  const batch = data.value.business.processing
  if (!window.confirm(batch.paid_request_ids?.length ? '请先核对本批次的费用、已保存文案和图片。确认已核对后，只关闭中断批次，不会再次调用模型。' : '关闭这个未完成批次？本次操作不会调用模型。')) return
  busy.value = true; error.value = ''
  try { await api.recoverProcessing(batch, true) } catch (exc) { error.value = exc.message }
  finally { busy.value = false }
  if (!error.value) await load()
}
let timer
onMounted(() => { load(); timer = setInterval(load, 30000) })
onUnmounted(() => clearInterval(timer))
</script>

<template>
  <section class="runtime" aria-label="运行状态" :aria-busy="busy">
    <header><div><h2>运行状态</h2><p>按五个阶段查看进展，时间以上海时间显示。</p></div>
      <button class="btn btn-sm" :disabled="busy" @click="load">{{ busy ? '正在读取…' : '刷新状态' }}</button></header>
    <p v-if="error" role="alert" class="warning">{{ error }}</p>
    <template v-if="data">
      <div class="facts">
        <p>调度进程：<strong>{{ data.process.alive === true ? '活跃' : data.process.alive === false ? '已退出' : '尚未确认' }}</strong><br>最近心跳 {{ time(data.process.last_tick_at) }}</p>
        <p>最近完整业务处理成功：<strong>{{ time(data.business.last_successful_run) }}</strong><br>进程活跃与业务处理成功分别记录。</p>
      </div>
      <p v-if="data.business.timings?.status === 'available'">近 {{ data.business.timings.window_days }} 天，{{ data.business.timings.sample_count }} 批新内容从发现到待审就绪：P50 {{ data.business.timings.p50_minutes }} 分钟，P95 {{ data.business.timings.p95_minutes }} 分钟。
        <span v-if="data.business.timings.morning_sample_count">下班后发现的 {{ data.business.timings.morning_sample_count }} 批中，{{ (data.business.timings.morning_deadline_rate * 100).toFixed(1) }}% 在次日上班前就绪。</span></p>
      <p v-else class="foot">处理时效：{{ data.business.timings?.status === 'state_unreadable' ? '时间记录无法读取，请检查运行账本。' : '还没有带完整发现时间和待审就绪时间的样本。' }}</p>
      <p v-if="data.business.timings?.published_to_ready?.status === 'available'">源帖发布到首次待审就绪：{{ data.business.timings.published_to_ready.sample_count }} 篇，P50 {{ data.business.timings.published_to_ready.p50_minutes }} 分钟，P95 {{ data.business.timings.published_to_ready.p95_minutes }} 分钟。此项包含发现新帖之前的等待。</p>
      <article v-for="stage in data.stages" :key="stage.number">
        <div class="stage-heading"><h3>{{ stage.number }}. {{ stage.name }}</h3><strong>{{ labels[stage.status] || stage.status }}</strong></div>
        <template v-if="stage.number === 1"><p>发现新帖后进入独立处理队列。处理模型素材期间，监测与提醒继续运行。</p>
          <p v-if="stage.detection?.detect_hard_blocked" class="warning">{{ stage.detection.detect_hard_blocked.reason || '探测会话已停止，请人工核对后恢复。' }}</p></template>
        <p v-if="stage.mirror_status">云盘镜像：{{ labels[stage.mirror_status] || stage.mirror_status }}</p>
        <template v-if="stage.number === 3">
          <p v-if="data.business.processing.recovery_reason" class="warning">{{ data.business.processing.recovery_reason }}</p>
          <p>处理开始 {{ time(data.business.processing.started_at) }} · 完成 {{ time(data.business.processing.finished_at) }}</p>
          <p v-if="data.business.processing.batch_id">本批 {{ data.business.processing.paid_request_ids?.length || 0 }} 次模型请求，已记录费用 US$ {{ Number(data.business.processing.cost_usd || 0).toFixed(4) }}</p>
          <p v-if="['interrupted', 'uncertain'].includes(data.business.processing.status)"><button class="btn btn-sm" :disabled="busy" @click="recoverProcessing">核对后关闭中断批次</button></p>
          <p v-if="stage.tag_sampling?.status">标签周采样：{{ labels[stage.tag_sampling.status] || stage.tag_sampling.status }} · {{ stage.tag_sampling.message }}</p>
          <p v-if="stage.trends_export?.status === 'blocked'" class="warning">Google Trends 采样已暂停：{{ stage.trends_export.reason || (stage.trends_export.http_status ? `HTTP ${stage.trends_export.http_status}` : '访问需要人工核对') }}。继续提供语义候选，恢复前不会自动重试。</p>
        </template>
        <template v-if="stage.outbox">
          <p v-if="stage.outbox.enabled && !stage.outbox.credentials_present" class="warning">飞书应用凭据尚未配置完整。</p>
          <p>已送达 {{ stage.outbox.counts?.sent || 0 }} · 等待重试 {{ stage.outbox.counts?.retry || 0 }} · 结果待核对 {{ stage.outbox.counts?.uncertain || 0 }}</p>
          <details v-if="stage.outbox.deliveries?.length"><summary>查看最近消息状态</summary>
            <ul><li v-for="item in stage.outbox.deliveries" :key="item.delivery_id">{{ time(item.created_at) }} · {{ labels[item.status] || item.status }}{{ item.error ? ` · ${item.error}` : '' }}
              <p v-if="item.preview_error">卡片首图未能上传，请从审校页查看完整素材。</p>
              <p v-if="item.task_ids?.filter(Boolean).length">对应帖子：{{ item.task_ids.filter(Boolean).join('、') }}</p>
              <p v-if="['retry', 'uncertain'].includes(item.status)"><button class="btn btn-sm" :disabled="busy" @click="resolve(item, 'delivered')">登记已送达</button>
                <button class="btn btn-sm" :disabled="busy" @click="resolve(item, 'not_delivered')">核对未送达后恢复</button></p>
            </li></ul>
          </details>
        </template>
        <template v-if="stage.checks">
          <ul class="checks"><li v-for="item in stage.checks" :key="item.name"><span>{{ item.available ? '✓' : '待补齐' }} {{ checks[item.name] || item.name }}</span><p v-if="!item.available">{{ item.reason }}</p></li></ul>
          <p>FB 单渠道验收：{{ stage.acceptance?.facebook?.verified ? '真实通过' : '尚未完成' }} · IG 单渠道验收：{{ stage.acceptance?.instagram?.verified ? '真实通过' : '尚未完成' }}</p>
          <p v-if="stage.unconfirmed_attempts">{{ stage.unconfirmed_attempts }} 次提交结果需要核对，请在对应帖子中查看恢复入口。</p>
        </template>
      </article>
      <p class="foot">外部心跳：{{ labels[data.heartbeat.status] || data.heartbeat.status }}。缺席告警由外部服务发送，本页显示本机记录的发送结果。</p>
      <p class="foot">更新于 {{ time(data.observed_at) }}，每 30 秒读取一次。</p>
    </template>
  </section>
</template>

<style scoped>
.runtime { padding: 24px; overflow: auto; }
header, .stage-heading, .facts { display: flex; justify-content: space-between; gap: 24px; align-items: center; }
h2, h3 { margin: 0; } h3 { font-size: 15px; } p, li { font-size: 13px; line-height: 1.6; }
header p, .foot { color: var(--muted-fg); } article { margin-top: 16px; border: 1px solid var(--border); background: var(--card); border-radius: 8px; padding: 16px; }
.stage-heading strong { font-size: 13px; } .warning { color: var(--risk); } .facts { flex-wrap: wrap; justify-content: flex-start; }
.checks { padding-left: 18px; } .checks p { margin: 3px 0 12px; color: var(--muted-fg); white-space: pre-wrap; overflow-wrap: anywhere; }
details summary { cursor: pointer; font-size: 13px; } .foot { margin-bottom: 0; }
</style>
