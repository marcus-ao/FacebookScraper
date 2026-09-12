<script setup>
import { ref } from 'vue'
import { api } from '../api.js'
const props = defineProps({ detail: { type: Object, required: true }, disabled: Boolean })
const emit = defineEmits(['changed'])
const editing = ref(false)
const input = ref('')
const busy = ref(false)
const error = ref('')
const conflict = ref(false)
function edit() {
  input.value = (props.detail.tags || []).join('，')
  error.value = ''
  editing.value = true
}
async function refresh() {
  try { emit('changed', await api.getTask(props.detail.id)); error.value = ''; conflict.value = false }
  catch (exc) { error.value = exc.message }
}
async function save() {
  busy.value = true
  try {
    const detail = await api.saveTags(props.detail.id, {
      tags: input.value.split(/[,，\n]/).map(value => value.trim()).filter(Boolean),
      tags_revision: props.detail.tags_revision,
      source_text_sha256: props.detail.text.source_text_sha256
    })
    emit('changed', detail)
    editing.value = false
    error.value = ''
  } catch (exc) { error.value = exc.message; conflict.value = exc.status === 409 }
  finally { busy.value = false }
}
</script>
<template>
  <div class="classification">
    <header><h4>产品分类</h4><button v-if="!editing" class="btn btn-sm btn-ghost" :disabled="disabled" @click="edit">编辑分类</button></header>
    <template v-if="editing">
      <label>多个分类用逗号隔开；清空后归为未分类。
        <textarea v-model="input" rows="2" placeholder="例如：M1 Pro，周年促销" :disabled="busy || disabled"></textarea>
      </label>
      <div class="tag-actions"><button class="btn btn-sm" :disabled="busy" @click="editing = false">取消</button><button class="btn btn-sm btn-primary" :disabled="busy || disabled" @click="save">{{ busy ? '保存中…' : '保存分类' }}</button></div>
    </template>
    <p v-else class="tags"><span v-for="tag in detail.tags" :key="tag" class="tag tag-neutral">{{ tag }}</span><span v-if="!detail.tags?.length" class="unclassified">未分类</span></p>
    <p v-if="error" role="alert" class="error">{{ error }} <button v-if="conflict" class="btn btn-sm" @click="refresh">载入最新分类</button></p>
  </div>
</template>
<style scoped>
.classification { border-top: 1px solid var(--border); padding-top: 12px; margin: 14px 0; }
header { display: flex; justify-content: space-between; align-items: center; }
h4 { margin: 0; font-size: 12px; }
label, .unclassified, .error { font-size: 12px; color: var(--muted-fg); }
textarea { width: 100%; margin-top: 7px; padding: 8px; border: 1px solid var(--border-strong); border-radius: var(--radius); font: inherit; }
.tag-actions { display: flex; justify-content: end; gap: 6px; }
.tags { display: flex; flex-wrap: wrap; gap: 6px; }
.error { color: var(--error); }
</style>
