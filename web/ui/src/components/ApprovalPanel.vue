<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { api } from '../api.js'
const props = defineProps({ detail: { type: Object, required: true }, editing: Boolean })
const emit = defineEmits(['changed'])
const options = ref(null)
const when = ref('')
const loading = ref(false)
const submitting = ref(false)
const error = ref('')
const suggestions = ref([])
const receipt = ref(null)
let sequence = 0
function berlinInput(iso) {
  if (!iso) return ''
  const value = new Date(iso)
  if (Number.isNaN(value.getTime())) return ''
  const parts = Object.fromEntries(new Intl.DateTimeFormat('en-CA', {
    timeZone: 'Europe/Berlin', year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', hourCycle: 'h23'
  }).formatToParts(value).map(part => [part.type, part.value]))
  return `${parts.year}-${parts.month}-${parts.day}T${parts.hour}:${parts.minute}`
}
const environmentBlocked = computed(() => /probe|config|验收证据/i.test(options.value?.reason || ''))
const minimum = computed(() => berlinInput(options.value?.earliest))
const maximum = computed(() => berlinInput(options.value?.latest))
const eligible = computed(() => ['pending_review', 'edited'].includes(props.detail.status))
const disabled = computed(() => props.editing || loading.value || submitting.value || !eligible.value || !options.value?.available || !when.value)
async function load() {
  loading.value = true
  const current = ++sequence
  try {
    const data = await api.approvalOptions(props.detail.id)
    if (current !== sequence) return
    options.value = data
    if (!when.value && data.earliest) when.value = berlinInput(props.detail.schedule?.at || data.earliest)
  } catch (exc) { if (current === sequence) error.value = exc.message }
  finally { if (current === sequence) loading.value = false }
}
async function submit() {
  submitting.value = true
  error.value = ''
  suggestions.value = []
  try {
    const result = await api.approve(props.detail.id, {
      scheduled_at: when.value,
      source_text_sha256: props.detail.text.source_text_sha256,
      human_revision: props.detail.text.human_revision,
      review_revision: props.detail.review.revision,
      content_fingerprint: options.value.fingerprint
    })
    if (!result.ok || result.status !== 'scheduled') throw new Error(result.message || '排期尚未确认，请核对回执')
    receipt.value = result
    emit('changed', await api.getTask(props.detail.id))
  } catch (exc) {
    error.value = exc.message
    suggestions.value = exc.payload?.suggestions || []
    await load()
  } finally { submitting.value = false }
}
onMounted(load)
watch(() => props.detail, load)
</script>
<template>
  <section class="approval">
    <header><h3>审核与排期</h3><button class="btn btn-sm btn-ghost" :disabled="loading || submitting" @click="load">重新核对排期条件</button></header>
    <p v-if="receipt" class="confirmed" role="status">排期已确认。{{ receipt.message }}</p>
    <p v-else-if="detail.status === 'scheduled'" class="confirmed">这篇已有经回读确认的排期。</p>
    <p v-if="editing" class="help">先保存或放弃当前编辑，再确认发布时间。</p>
    <p v-else-if="!eligible && detail.status !== 'scheduled'" class="help">当前处理状态不支持直接排期。</p>
    <p v-if="loading" class="help">正在核对本机记录、内容与发布条件…</p>
    <template v-else-if="options && !options.available">
      <p class="blocked">{{ environmentBlocked ? '发布环境尚未完成本机核验，暂时不能创建排期。' : options.reason }}</p>
      <details v-if="environmentBlocked" class="technical"><summary>查看核验信息</summary><p>{{ options.reason }}</p></details>
    </template>
    <div v-if="eligible" class="schedule-form">
      <label>发布时间（柏林当地时间）<input v-model="when" type="datetime-local" :min="minimum" :max="maximum" :disabled="editing || submitting || !options?.available" /></label>
      <button class="btn btn-primary" :disabled="disabled" @click="submit">{{ submitting ? '正在提交并核验…' : '通过并创建排期' }}</button>
    </div>
    <p v-if="minimum && maximum" class="help">可选范围：{{ minimum.replace('T', ' ') }} 至 {{ maximum.replace('T', ' ') }}（柏林）。</p>
    <p v-if="error" class="error" role="alert">{{ error }}</p>
    <div v-if="suggestions.length" class="suggestions"><span>可以改选：</span><button v-for="value in suggestions" :key="value" class="btn btn-sm" @click="when = berlinInput(value)">{{ berlinInput(value).replace('T', ' ') }} 柏林</button></div>
  </section>
</template>
<style scoped>
.approval { padding: 18px; margin-top: 16px; background: var(--card); border: 1px solid var(--border); border-radius: var(--radius); }
header { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
h3 { font-size: 13px; margin: 0; }
.help { font-size: 12px; color: var(--muted-fg); }
.blocked { color: #92400e; background: var(--risk-soft); border-radius: var(--radius); padding: 8px; font-size: 12px; overflow-wrap: anywhere; }
.schedule-form { display: flex; align-items: end; gap: 16px; margin-top: 12px; }
.schedule-form label { display: grid; gap: 6px; font-size: 12px; }
input { border: 1px solid var(--border-strong); border-radius: var(--radius); padding: 8px; font: inherit; }
.error { font-size: 12px; color: var(--error); }
.confirmed { font-size: 13px; color: var(--ok); }
.suggestions { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; font-size: 12px; }
.technical { font-size: 12px; color: var(--muted-fg); overflow-wrap: anywhere; }
</style>
