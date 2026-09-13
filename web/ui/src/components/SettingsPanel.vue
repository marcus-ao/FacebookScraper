<script setup>
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { api } from '../api.js'
const data = ref(null)
const times = ref('')
const days = ref(3)
const loading = ref(false)
const error = ref('')
const saved = ref(false)
const values = computed(() => ({ default_times: times.value.split(/[,，\s]+/).filter(Boolean), snooze_default_days: Number(days.value) }))
const dirty = computed(() => data.value && JSON.stringify(values.value) !== JSON.stringify(data.value.editable))
function apply(result) {
  data.value = result
  times.value = result.editable.default_times.join(', ')
  days.value = result.editable.snooze_default_days
}
async function load() {
  loading.value = true
  error.value = ''
  try { apply(await api.settings()) }
  catch (exc) { error.value = exc.message }
  finally { loading.value = false }
}
async function save() {
  loading.value = true
  error.value = ''
  saved.value = false
  try { apply(await api.saveSettings(values.value, data.value.version)); saved.value = true }
  catch (exc) { error.value = exc.message }
  finally { loading.value = false }
}
function mayLeave() { return !dirty.value || window.confirm('设置尚未保存，离开会放弃这次修改。继续离开？') }
function beforeUnload(event) { if (dirty.value) { event.preventDefault(); event.returnValue = '' } }
defineExpose({ mayLeave })
onMounted(() => { load(); window.addEventListener('beforeunload', beforeUnload) })
onUnmounted(() => window.removeEventListener('beforeunload', beforeUnload))
const names = { targets: '监测来源账号', publish_identity: '发布账号核验名', price_map: '价格映射',
  trusted_owners: '信任名单', pipeline: '处理方式与预算', delta: '抓取控制', network_evidence: '网络探查设置' }
</script>
<template>
  <section class="settings">
    <header><h2>运营设置</h2><p>默认排期时间供选期时使用；修改不会移动已有排期。挂起期限按上海工作日计算。</p></header>
    <p v-if="error" class="error" role="alert">{{ error }}</p>
    <p v-if="saved && !dirty" role="status" class="success">设置已保存，下次选期或挂起时生效。</p>
    <form v-if="data" @submit.prevent="save">
      <label>默认排期时间（柏林）<input v-model="times" placeholder="10:00, 17:00" :disabled="loading" required /><small>用逗号分隔，例如 10:00, 17:00。</small><small v-if="data.editable_help?.default_times" class="help">{{ data.editable_help.default_times }}</small></label>
      <label>默认挂起期限<input v-model="days" type="number" min="1" max="30" step="1" :disabled="loading" required /><small>1 至 30 个工作日，到期后回到待审。</small><small v-if="data.editable_help?.snooze_default_days" class="help">{{ data.editable_help.snooze_default_days }}</small></label>
      <div class="actions"><button class="btn btn-primary" :disabled="loading || !dirty">{{ loading ? '正在保存…' : '保存设置' }}</button><button type="button" class="btn" :disabled="loading" @click="load">重新读取</button></div>
    </form>
    <p v-else-if="loading">正在读取设置…</p>
    <section v-if="data" class="controlled"><h3>系统配置</h3><p>以下内容供核对，由维护人员在配置文件中管理。</p>
      <details v-for="(value, key) in data.controlled" :key="key"><summary>{{ names[key] || key }}</summary>
        <dl v-if="data.controlled_fields?.[key]?.length"><div v-for="field in data.controlled_fields[key]" :key="field.key" class="field">
          <dt>{{ field.key }}</dt><dd>{{ typeof field.value === 'string' ? field.value : JSON.stringify(field.value) }}</dd>
          <dd v-if="field.help" class="help">{{ field.help }}</dd>
        </div></dl><p v-else>尚未配置。</p>
      </details>
    </section>
  </section>
</template>
<style scoped>
.settings { max-width: 900px; width: 100%; padding: 24px; margin: 0 auto; }
h2 { font-size: 19px; } h3 { font-size: 15px; }
header p, small, .controlled p { color: var(--muted-fg); font-size: 13px; }
form { display: grid; gap: 20px; margin: 24px 0; max-width: 420px; }
label { display: grid; gap: 8px; font-size: 14px; }
input { padding: 9px; border: 1px solid var(--border-strong); border-radius: var(--radius); font: inherit; }
.actions { display: flex; gap: 10px; }
.controlled { margin-top: 32px; border-top: 1px solid var(--border); padding-top: 16px; }
details { padding: 12px 0; border-bottom: 1px solid var(--border); }
summary { cursor: pointer; font-size: 13px; } pre { white-space: pre-wrap; overflow-wrap: anywhere; font-size: 12px; }
.field { padding: 10px 0; } dt { color: var(--muted-fg); font-size: 12px; overflow-wrap: anywhere; }
dd { margin: 5px 0; font-size: 13px; overflow-wrap: anywhere; } .help { white-space: pre-wrap; line-height: 1.65; color: var(--muted-fg); }
.error { color: var(--error); } .success { color: var(--ok); }
</style>
