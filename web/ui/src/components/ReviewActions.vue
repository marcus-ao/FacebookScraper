<script setup>
import { computed, reactive, ref } from 'vue'
import { api } from '../api.js'
import Icon from './Icon.vue'

const props = defineProps({ detail: { type: Object, required: true }, compact: Boolean })
const emit = defineEmits(['changed'])
const form = reactive({ action: '', reason: '', wakeAt: '', handoffUrl: '' })
const busy = ref(false)
const error = ref('')
const conflict = ref(false)
const active = computed(() => ['not_ready', 'pending_review', 'edited', 'snoozed'].includes(props.detail.status))
const titles = { snoozed: '稍后再审', skipped: '这篇不发', handed_off: '由我自行处理',
  handoff_link: '补充手工发布链接', export: '下载并交由我处理', woke: '恢复审校' }

function open(action) {
  Object.assign(form, { action, reason: '', wakeAt: '', handoffUrl: props.detail.review?.handoff_url || '' })
  error.value = ''
  conflict.value = false
}

function close() {
  if (!busy.value) form.action = ''
}

function context() {
  return {
    source_text_sha256: props.detail.text?.source_text_sha256 || props.detail.source_text_sha256,
    review_revision: props.detail.review?.revision ?? null
  }
}

async function refresh() {
  try {
    emit('changed', await api.getTask(props.detail.id))
    error.value = ''
    conflict.value = false
  } catch (exc) { error.value = exc.message }
}

async function confirm() {
  busy.value = true
  error.value = ''
  try {
    const body = { ...context(), action: form.action, reason: form.reason, handoff_url: form.handoffUrl }
    if (form.wakeAt) body.wake_at = `${form.wakeAt}:00+08:00`
    if (form.action === 'export') {
      const result = await api.exportPost(props.detail.id, body)
      const url = URL.createObjectURL(result.blob)
      const link = document.createElement('a')
      link.href = url
      link.download = result.filename
      document.body.appendChild(link)
      link.click()
      link.remove()
      setTimeout(() => URL.revokeObjectURL(url), 30000)
      emit('changed', await api.getTask(props.detail.id))
    } else {
      emit('changed', await api.reviewAction(props.detail.id, body))
    }
    form.action = ''
  } catch (exc) {
    error.value = exc.message
    conflict.value = exc.status === 409
  } finally { busy.value = false }
}
</script>

<template>
  <div class="review-actions">
    <div class="buttons">
      <template v-if="active">
        <button v-if="detail.status === 'snoozed'" class="btn btn-sm" @click="open('woke')">恢复审校</button>
        <button v-else class="btn btn-sm" @click="open('snoozed')">稍后再审</button>
        <button class="btn btn-sm btn-danger" @click="open('skipped')">这篇不发</button>
        <template v-if="!compact">
          <button class="btn btn-sm" @click="open('export')">下载并由我处理</button>
          <button class="btn btn-sm btn-ghost" @click="open('handed_off')">我已自行处理</button>
        </template>
      </template>
      <template v-if="detail.status === 'handed_off' && !compact">
        <button class="btn btn-sm" @click="open('export')">重新下载资源</button>
        <button class="btn btn-sm" @click="open('handoff_link')">补充发布链接</button>
      </template>
    </div>
    <Teleport to="body">
      <div v-if="form.action" class="decision-overlay" @click.self="close">
        <section class="decision" role="dialog" aria-modal="true" :aria-label="titles[form.action]" @keydown.esc="close">
          <h2>{{ titles[form.action] }}</h2>
          <p v-if="form.action === 'snoozed'">默认在 {{ detail.review?.snooze_default_days || 3 }} 个工作日后回到待审列表。工作日按上海时间周一至周五计算。</p>
          <p v-if="form.action === 'skipped'">这篇会移到“已处理”，系统将停止后续处理。请留下不发的理由。</p>
          <p v-if="['handed_off', 'export'].includes(form.action)">这篇会标记为“已交人工处理”，由你继续调整或发布，系统不再自动推进。</p>
          <p v-if="form.action === 'export'">下载包含当前德语文案、图片与元信息。缺少德语图时会使用原图，并在资源包中注明。</p>
          <p v-if="form.action === 'woke'">恢复后，这篇会重新出现在待审列表。</p>
          <label v-if="form.action === 'snoozed'">指定回来时间（选填，上海时间）
            <input v-model="form.wakeAt" type="datetime-local" :disabled="busy" />
          </label>
          <label v-if="['snoozed', 'skipped'].includes(form.action)">理由{{ form.action === 'skipped' ? '（必填）' : '（选填）' }}
            <textarea v-model="form.reason" rows="3" maxlength="2000" :disabled="busy" autofocus></textarea>
          </label>
          <label v-if="['handed_off', 'handoff_link', 'export'].includes(form.action)">手工发布链接（选填）
            <input v-model="form.handoffUrl" type="url" placeholder="https://…" :disabled="busy" />
          </label>
          <p v-if="error" class="decision-error" role="alert">{{ error }}
            <button v-if="conflict" class="btn btn-sm" :disabled="busy" @click="refresh">刷新状态，保留填写内容</button>
          </p>
          <footer>
            <button class="btn" :disabled="busy" @click="close">取消</button>
            <button class="btn btn-primary" :disabled="busy || (form.action === 'skipped' && !form.reason.trim())" @click="confirm">
              {{ busy ? '处理中…' : form.action === 'export' ? '下载并交给我' : '确认' }}
            </button>
          </footer>
        </section>
      </div>
    </Teleport>
  </div>
</template>

<style scoped>
.buttons { display: flex; flex-wrap: wrap; gap: 6px; }
.decision-overlay { position: fixed; inset: 0; z-index: 100; background: #0f172a66; display: grid; place-items: center; padding: 24px; }
.decision { width: min(470px, 100%); background: var(--card); border-radius: var(--radius-lg); padding: 24px; box-shadow: var(--shadow-lg); }
.decision h2 { margin: 0 0 12px; font-size: 17px; }
.decision p { font-size: 13px; color: var(--muted-fg); line-height: 1.7; }
.decision label { display: grid; gap: 7px; margin: 14px 0; font-size: 13px; }
.decision input, .decision textarea { width: 100%; border: 1px solid var(--border-strong); border-radius: var(--radius); padding: 9px; font: inherit; }
.decision footer { display: flex; justify-content: end; gap: 8px; margin-top: 18px; }
.decision .decision-error { color: var(--error); }
</style>
