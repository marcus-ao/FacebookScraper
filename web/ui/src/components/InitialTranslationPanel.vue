<script setup>
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { api } from '../api.js'
const props = defineProps({ detail: { type: Object, required: true }, editing: Boolean })
const emit = defineEmits(['changed'])
const capability = ref(null)
const job = ref(null)
const consent = ref(false)
const submitting = ref(false)
const error = ref('')
let alive = true
let polling = false
let timer = null
const running = computed(() => ['pending', 'running'].includes(job.value?.status))
const labels = { pending: '已受理，等待处理', running: '正在翻译并处理图片', succeeded: '本轮处理完成', failed: '处理尚未完成' }
async function refresh() {
  try {
    const result = await api.initialCapabilities(props.detail.id)
    if (!alive) return
    capability.value = result
    job.value = result.job
  } catch (exc) { if (alive) error.value = exc.message }
}
async function submit() {
  submitting.value = true
  error.value = ''
  try {
    job.value = await api.initialTranslate(props.detail.id, {
      consent: consent.value, source_fingerprint: capability.value.source_fingerprint,
      source_text_sha256: props.detail.text.source_text_sha256,
      human_revision: props.detail.text.human_revision, review_revision: props.detail.review.revision
    })
    consent.value = false
    await refresh()
  } catch (exc) {
    error.value = exc.message
    if (exc.status === 409 && !props.editing) {
      consent.value = false
      try { emit('changed', await api.getTask(props.detail.id)); await refresh() }
      catch { /* 保留原错误，允许手动刷新 */ }
    }
  }
  finally { submitting.value = false }
}
async function poll() {
  if (!running.value || polling) return
  polling = true
  try {
    const result = await api.initialJob(job.value.job_id)
    if (!alive) return
    job.value = result
    if (['succeeded', 'failed'].includes(result.status)) {
      await refresh()
      const detail = await api.getTask(props.detail.id)
      if (alive && !props.editing) emit('changed', detail)
    }
  } catch (exc) { if (alive) error.value = exc.message }
  finally { polling = false }
}
watch(() => [props.detail.text.source_text_sha256, props.detail.review.revision], () => {
  consent.value = false
  refresh()
})
onMounted(() => { refresh(); timer = setInterval(poll, 1500) })
onUnmounted(() => { alive = false; clearInterval(timer) })
</script>
<template>
  <section v-if="capability?.third_party" class="initial-translation">
    <h3>这篇来自第三方作者</h3>
    <p>请先看原帖，确认本篇可以用于德国站内容运营，再让系统翻译文案和处理图片。生成后仍由你审校和安排发布。</p>
    <template v-if="capability.available && !running">
      <label><input v-model="consent" type="checkbox" :disabled="editing || submitting">我已确认可以处理这篇内容，开始本篇模型处理。</label>
      <p class="help">本次处理按模型实际用量记账，仍受日/月预算约束。</p>
      <button class="btn btn-sm btn-primary" :disabled="!consent || editing || submitting" @click="submit">{{ submitting ? '正在受理…' : '翻译这篇' }}</button>
    </template>
    <p v-else-if="capability.reason" class="help">{{ capability.reason }}</p>
    <p v-if="editing" class="help">请先保存或放弃正在编辑的文案，再开始处理。</p>
    <p v-if="job" role="status">{{ labels[job.status] }}<span v-if="job.message"> · {{ job.message }}</span></p>
    <p v-if="error" role="alert">{{ error }}</p>
    <button v-if="error || running" class="btn btn-sm" @click="refresh">刷新处理状态</button>
  </section>
</template>
<style scoped>
.initial-translation { padding: 16px; margin-bottom: 18px; border: 1px solid var(--border-strong); border-radius: var(--radius); background: var(--card); font-size: 13px; }
h3 { margin: 0 0 8px; font-size: 14px; }
p { line-height: 1.65; }
label { display: flex; gap: 8px; align-items: flex-start; }
.help { color: var(--muted-fg); font-size: 12px; }
[role='alert'] { color: var(--error); }
</style>
