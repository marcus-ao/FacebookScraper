<script setup>
import { computed, ref, watch } from 'vue'
import { api } from '../api.js'
import { formatWakeAt } from '../format.js'
const props = defineProps({ draft: { type: Object, required: true }, editing: Boolean, detail: { type: Object, required: true } })
const emit = defineEmits(['update'])
const protectedTags = computed(() => props.draft.protected_tags || [])
const semanticTags = computed(() => props.draft.tags.filter(tag => !protectedTags.value.includes(tag)))
const sourceSemantic = computed(() => props.draft.source_tags.filter(tag => !protectedTags.value.includes(tag)))
const input = ref('')
watch(() => props.editing, () => { input.value = semanticTags.value.join(' ') }, { immediate: true })
function updateTags(event) {
  input.value = event.target.value
  const tags = event.target.value.split(/[\s,，]+/u).filter(Boolean).map(tag => tag.startsWith('#') ? tag : '#' + tag)
  emit('update', { tags: [...protectedTags.value, ...tags], hashtags_confirmed: false })
}
const suggesting = ref(false)
const suggestion = ref(null)
const selected = ref([])
const suggestionError = ref('')
const signalLabels = { media_count: '累计帖子数', peer_uses_14d: '同类账号近14天使用', trend_score: '德国趋势信号' }
async function suggest() {
  suggesting.value = true
  suggestionError.value = ''
  try {
    const result = await api.suggestHashtags(props.detail.id, {
      source_text_sha256: props.detail.text.source_text_sha256,
      human_revision: props.detail.text.human_revision, review_revision: props.detail.review.revision
    })
    suggestion.value = result
    selected.value = [...result.selected]
  } catch (exc) { suggestionError.value = exc.message }
  finally { suggesting.value = false }
}
function choose(tag, checked) {
  selected.value = checked ? [...new Set([...selected.value, tag])] : selected.value.filter(value => value !== tag)
}
function adopt() {
  const tags = [...protectedTags.value, ...selected.value.filter(tag => !protectedTags.value.includes(tag))]
  input.value = tags.filter(tag => !protectedTags.value.includes(tag)).join(' ')
  emit('update', { tags, hashtags_confirmed: false })
}
</script>
<template>
  <section class="local-section">
    <header><h3>话题标签</h3><span :class="['counter', { near: draft.platform === 'instagram' && draft.tags.length >= 27 }]">{{ draft.tags.length }}{{ draft.platform === 'instagram' ? ' / 30' : '' }} 个标签</span></header>
    <p v-if="protectedTags.length" class="tags"><span class="caption">品牌与型号 · 保持原样</span><span v-for="(tag, i) in protectedTags" :key="i" class="tag tag-ok">{{ tag }}</span></p>
    <p v-if="sourceSemantic.length" class="tags"><span class="caption">原帖语义标签</span><span v-for="(tag, i) in sourceSemantic" :key="i" class="tag tag-neutral">{{ tag }}</span></p>
    <label v-if="editing" class="field">本篇使用的语义标签（用空格分隔，可增删）
      <textarea :value="input" rows="2" placeholder="#Katzenliebe #Tierpflege" @input="updateTags"></textarea>
    </label>
    <p v-else class="tags"><span class="caption">本篇选择</span><span v-for="(tag, i) in semanticTags" :key="i" class="tag tag-neutral">{{ tag }}</span><span v-if="!semanticTags.length" class="caption">无语义标签</span></p>
    <label v-if="editing && (sourceSemantic.length || semanticTags.length)" class="confirmation">
      <input type="checkbox" :checked="draft.hashtags_confirmed" @change="emit('update', { hashtags_confirmed: $event.target.checked })" /> 我已确认本篇使用的话题标签
    </label>
    <p v-else-if="sourceSemantic.length || semanticTags.length" :class="['choice-status', { pending: !draft.hashtags_confirmed }]">{{ draft.hashtags_confirmed ? '已人工确认选择' : '语义标签尚待人工确认' }}</p>
    <div v-if="sourceSemantic.length" class="suggest-actions">
      <button class="btn btn-sm" :disabled="!editing || suggesting" @click="suggest">{{ suggesting ? '正在生成语义建议…' : '生成德语标签建议' }}</button>
      <span class="help">建议不自动写入，采样不可用时可继续手动选择。</span>
    </div>
    <p v-if="suggestionError" class="suggestion-error" role="alert">{{ suggestionError }}，仍可手动编辑。</p>
    <div v-if="suggestion" class="suggestions">
      <p class="help">{{ suggestion.notice }}</p>
      <p v-if="suggestion.generated_at" class="help">候选生成：{{ formatWakeAt(suggestion.generated_at) }}（不是热度采样时间）</p>
      <div v-for="(group, i) in suggestion.groups.filter(item => !item.protected)" :key="i" class="candidate-group">
        <strong>{{ group.source_tag }}</strong>
        <div v-for="candidate in group.candidates" :key="candidate.tag" class="candidate">
          <label><input type="checkbox" :checked="selected.includes(candidate.tag)" :disabled="!editing" @change="choose(candidate.tag, $event.target.checked)" /> {{ candidate.tag }}</label>
          <span v-if="!Object.keys(candidate.signals || {}).length" class="help">未采样 · 语义建议</span>
          <span v-for="(signal, metric) in candidate.signals" :key="metric" class="help">{{ signalLabels[metric] || metric }}：{{ signal.value }} · {{ formatWakeAt(signal.sampled_at) }} · {{ signal.source }}{{ candidate.current_signals?.[metric] ? '' : '（已过期）' }}</span>
        </div>
      </div>
      <button class="btn btn-sm" :disabled="!editing || suggestion.source_text_sha256 !== detail.text.source_text_sha256" @click="adopt">采用勾选到编辑区</button>
    </div>
    <p class="help">保存不会自动删减超出上限的标签。</p>
  </section>
</template>
<style scoped>
.local-section { padding: 18px; background: var(--card); border: 1px solid var(--border); border-radius: var(--radius); }
header { display: flex; justify-content: space-between; gap: 12px; align-items: center; }
h3 { margin: 0; font-size: 13px; }
.counter, .caption, .help { font-size: 12px; color: var(--muted-fg); }
.counter.near, .pending { color: #92400e; background: var(--risk-soft); }
.tags { display: flex; flex-wrap: wrap; align-items: center; gap: 6px; margin: 12px 0; }
.field { display: grid; gap: 6px; font-size: 12px; margin-top: 12px; }
textarea { width: 100%; padding: 10px; font: inherit; border: 1px solid var(--border-strong); border-radius: var(--radius); resize: vertical; }
.confirmation { display: flex; align-items: center; gap: 6px; margin-top: 12px; font-size: 12px; }
.help { margin: 12px 0 0; }
.choice-status { font-size: 12px; color: var(--ok); }
.suggest-actions { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; margin-top: 10px; }
.suggestions { margin-top: 12px; padding: 12px; background: var(--muted); border-radius: var(--radius); }
.candidate-group { margin: 10px 0; font-size: 12px; }
.candidate { display: flex; flex-wrap: wrap; gap: 8px; margin: 7px 0; }
.candidate label { display: flex; align-items: center; gap: 4px; }
.suggestion-error { font-size: 12px; color: var(--error); }
</style>
