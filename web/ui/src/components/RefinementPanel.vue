<script setup>
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { api } from '../api.js'
const props = defineProps({ detail: { type: Object, required: true }, editing: Boolean })
const emit = defineEmits(['candidate', 'changed'])
const kind = ref('text')
const mediaIndex = ref(0)
const instruction = ref('')
const capabilities = ref(null)
const job = ref(null)
const submitting = ref(false)
const error = ref('')
const template = ref('')
const showingTemplate = ref(false)
const templateLoading = ref(false)
let timer = null
let alive = true
let polling = false
const running = computed(() => ['pending', 'running'].includes(job.value?.status))
const eligible = computed(() => ['pending_review', 'edited', 'not_ready'].includes(props.detail.status) && !props.detail.text.stale)
const remaining = computed(() => Math.max(0, (capabilities.value?.max_refine_per_media ?? 0) - (capabilities.value?.image_attempts?.[mediaIndex.value] || 0)))
const unavailable = computed(() => submitting.value || running.value || job.value?.status === 'interrupted' || props.editing || !eligible.value || !instruction.value.trim()
  || (kind.value === 'image' && (!capabilities.value || !remaining.value)))
const price = computed(() => Number(capabilities.value?.estimated_image_usd || 0).toFixed(3))
const jobLabel = { pending: '已受理，等待处理', running: '正在生成', succeeded: '生成完成', failed: '生成未完成', interrupted: '处理已中断，待核对' }

async function recover() {
  submitting.value = true
  error.value = ''
  try { job.value = await api.recoverContentJob(job.value); await loadCapabilities() }
  catch (exc) { error.value = exc.message }
  finally { submitting.value = false }
}

async function loadCapabilities() {
  try {
    const data = await api.refinementCapabilities(props.detail.id)
    if (!alive) return
    capabilities.value = data
    const jobs = data.jobs || []
    const current = jobs.find(item => ['pending', 'running'].includes(item.status)) || jobs.at(-1)
    if (current && (!job.value || job.value.job_id === current.job_id || !running.value)) job.value = current
    if (job.value?.kind === 'text' && job.value.status === 'succeeded') job.value = await api.refinementJob(job.value.job_id)
  } catch (exc) { if (alive) error.value = exc.message }
}
async function submit() {
  submitting.value = true
  error.value = ''
  try {
    job.value = await api.refine(props.detail.id, {
      kind: kind.value, instruction: instruction.value,
      media_index: kind.value === 'image' ? Number(mediaIndex.value) : null,
      source_text_sha256: props.detail.text.source_text_sha256,
      human_revision: props.detail.text.human_revision, review_revision: props.detail.review.revision
    })
    await loadCapabilities()
  } catch (exc) { error.value = exc.message }
  finally { submitting.value = false }
}
async function poll() {
  if (!running.value || polling) return
  polling = true
  try {
    const latest = await api.refinementJob(job.value.job_id)
    if (!alive) return
    job.value = latest
    if (latest.status === 'succeeded' || latest.status === 'failed') {
      await loadCapabilities()
      if (latest.kind === 'image' && latest.status === 'succeeded') emit('changed', await api.getTask(props.detail.id))
    }
  } catch (exc) { if (alive) error.value = exc.message }
  finally { polling = false }
}
async function toggleTemplate() {
  showingTemplate.value = !showingTemplate.value
  if (!showingTemplate.value) return
  templateLoading.value = true
  try { template.value = (await api.template(kind.value)).content }
  catch (exc) { error.value = exc.message }
  finally { templateLoading.value = false }
}
watch(kind, () => { showingTemplate.value = false; template.value = '' })
onMounted(() => { loadCapabilities(); timer = setInterval(poll, 1200) })
onUnmounted(() => { alive = false; clearInterval(timer) })
</script>
<template>
  <section class="refinement">
    <header><h3>单篇优化</h3><button class="btn btn-sm btn-ghost" @click="toggleTemplate">{{ showingTemplate ? '收起模板' : '查看模板（只读）' }}</button></header>
    <div class="form-row">
      <label>优化内容<select v-model="kind" :disabled="running || submitting"><option value="text">文案</option><option value="image">图片</option></select></label>
      <label v-if="kind === 'image'">选择图片<select v-model="mediaIndex" :disabled="running || submitting"><option v-for="(image, index) in detail.images" :key="index" :value="index">第 {{ index + 1 }} 张</option></select></label>
    </div>
    <label class="instruction">这一次希望怎样调整
      <textarea v-model="instruction" rows="2" maxlength="4000" placeholder="例如：保持事实与型号，语气更自然，缩短开头。" :disabled="running || submitting"></textarea>
    </label>
    <p v-if="editing" class="help">请先保存或放弃当前编辑，再提交优化要求。生成候选不会替你保存人工稿。</p>
    <p v-else-if="!eligible" class="help">请先恢复审校，并复核保存源帖变更后再发起优化。</p>
    <p v-if="kind === 'image' && capabilities" class="help">预计约 US${{ price }} / 张 · {{ capabilities.estimate_basis }}。本次为第 {{ (capabilities.image_attempts?.[mediaIndex] || 0) + 1 }} 次，剩余 {{ remaining }} 次。实际费用以模型记录为准。</p>
    <div class="submit-row">
      <span class="help">指令只作用于本篇，不改写模板。</span>
      <button class="btn btn-sm btn-primary" :disabled="unavailable" @click="submit">{{ submitting ? '正在受理…' : running ? '正在生成…' : kind === 'image' ? `生成图片 · 约 US$${price}` : '生成文案候选' }}</button>
    </div>
    <p v-if="error" role="alert" class="error">{{ error }} <button class="btn btn-sm" @click="loadCapabilities">刷新任务状态</button></p>
    <div v-if="job" class="job">
      <p>{{ job.kind === 'image' ? `图片 ${Number(job.media_index) + 1}` : '文案' }} · {{ jobLabel[job.status] }}</p>
      <p class="help">本次要求：{{ job.instruction }}</p>
      <p v-if="job.paid_request_ids?.length" class="help">已记录费用 US${{ Number(job.cost_usd || 0).toFixed(4) }} · 请求 {{ job.paid_request_ids.join('、') }}</p>
      <p v-if="job.status === 'interrupted'" class="error">{{ job.message }} <button class="btn btn-sm" :disabled="submitting" @click="recover">核对并恢复本地状态</button><span class="help">此操作只核对记录，不会重新生成。</span></p>
      <p v-if="job.worker_state === 'unknown'" class="help">旧任务缺少进程依据，需要人工核对。</p>
      <template v-if="job.kind === 'text' && job.status === 'succeeded'">
        <pre class="candidate">{{ job.body_de }}</pre>
        <button class="btn btn-sm" :disabled="job.source_text_sha256 !== detail.text.source_text_sha256 || !eligible" @click="emit('candidate', job)">采用到正文编辑区</button>
        <span class="help">标签与链接选择保留，确认后请保存。</span>
        <p v-if="job.source_text_sha256 !== detail.text.source_text_sha256" class="error">源文已更新，这个候选已失效，请基于当前源文重新优化。</p>
      </template>
      <p v-if="job.kind === 'image' && job.status === 'succeeded'" class="help">新版图片已生成；若归档里已有人工图片，页面继续优先显示人工图片。</p>
      <p v-if="job.status === 'failed'" class="error">{{ job.message }} <span v-if="job.error">（{{ job.error }}）</span></p>
    </div>
    <pre v-if="showingTemplate" class="template">{{ templateLoading ? '正在读取模板…' : template }}</pre>
  </section>
</template>
<style scoped>
.refinement { background: var(--card); border: 1px solid var(--border); border-radius: var(--radius); padding: 18px; margin-top: 16px; }
header, .submit-row { display: flex; justify-content: space-between; align-items: center; gap: 12px; }
h3 { font-size: 13px; margin: 0; }
.form-row { display: flex; gap: 12px; margin: 10px 0; }
.form-row label { display: flex; align-items: center; gap: 8px; font-size: 12px; }
select, textarea { border: 1px solid var(--border-strong); border-radius: var(--radius); padding: 8px; font: inherit; background: var(--card); }
.instruction { display: grid; gap: 6px; font-size: 12px; }
textarea { width: 100%; resize: vertical; }
.help { font-size: 12px; color: var(--muted-fg); }
.submit-row { margin-top: 10px; }
.job { margin-top: 12px; padding: 12px; border: 1px solid var(--border); border-radius: var(--radius); font-size: 13px; }
.candidate { white-space: pre-wrap; font: inherit; background: var(--muted); padding: 12px; border-radius: var(--radius); }
.template { max-height: 350px; overflow: auto; white-space: pre-wrap; font-size: 12px; line-height: 1.7; padding: 12px; background: var(--muted); }
.error { color: var(--error); font-size: 12px; }
</style>
