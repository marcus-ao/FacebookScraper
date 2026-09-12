<script setup>
import { computed, ref, watch } from 'vue'
const props = defineProps({ draft: { type: Object, required: true }, editing: Boolean })
const emit = defineEmits(['update'])
const isInstagram = computed(() => props.draft.platform === 'instagram')
const presets = computed(() => props.draft.cta_presets || [])
const mode = ref('')
watch(() => props.draft.ig_cta, value => {
  if (mode.value !== 'custom') mode.value = !value ? '' : presets.value.includes(value) ? value : 'custom'
}, { immediate: true })
function selectMode(event) {
  mode.value = event.target.value
  if (mode.value !== 'custom') emit('update', { ig_cta: mode.value })
}
function updateLink(index, fields) {
  const links = props.draft.links.map((link, i) => i === index ? { ...link, ...fields } : link)
  emit('update', { links })
}
const canOpen = value => /^https?:\/\/[^\s]+$/i.test(value || '')
const sourceHref = value => value.startsWith('www.') ? 'https://' + value : value
</script>
<template>
  <section class="local-section">
    <header><h3>链接</h3><span>{{ isInstagram ? 'Instagram · bio 引导' : 'Facebook · 德语落地页' }}</span></header>
    <p v-if="!draft.links.length" class="help">原帖没有链接。</p>
    <div v-for="(link, index) in draft.links" :key="index" class="link-row">
      <p class="source"><span>原文</span><a v-if="link.source_url" :href="sourceHref(link.source_url)" target="_blank" rel="noopener noreferrer">{{ link.source_url }}</a><span v-else>人工添加的链接</span></p>
      <template v-if="!isInstagram">
        <p v-if="link.mapped_url" class="mapping">映射表（只读）：{{ link.mapped_url }}</p>
        <label v-if="editing" class="field">本篇德语落地页
          <input type="url" :value="link.target_url" placeholder="https://de.neakasa.com/…" @input="updateLink(index, { target_url: $event.target.value, confirmed: false })" />
        </label>
        <p v-else class="target">{{ link.target_url || '尚未填写德语落地页' }}</p>
        <div class="link-check">
          <a v-if="canOpen(link.target_url)" :href="link.target_url" target="_blank" rel="noopener noreferrer">打开检查 ↗</a>
          <label v-if="editing"><input type="checkbox" :checked="link.confirmed" @change="updateLink(index, { confirmed: $event.target.checked })" /> 我已确认落地页适用于德国站</label>
          <span v-else :class="{ pending: !link.confirmed }">{{ link.confirmed ? (link.target_url === link.mapped_url ? '沿用已确认的映射' : '本篇链接已确认') : '待人工确认' }}</span>
        </div>
      </template>
    </div>
    <template v-if="isInstagram">
      <p v-if="draft.links.length" class="help">原文链接已从发布文案中移除，使用下面的话术引导用户前往 bio。</p>
      <label v-if="editing" class="field">引导话术
        <select :value="mode" @change="selectMode"><option value="">不添加</option><option v-for="preset in presets" :key="preset" :value="preset">{{ preset }}</option><option value="custom">自定义</option></select>
      </label>
      <label v-if="editing && mode === 'custom'" class="field">自定义 bio 引导
        <input type="text" :value="draft.ig_cta" @input="emit('update', { ig_cta: $event.target.value })" />
      </label>
      <p v-if="!editing" class="target">{{ draft.ig_cta || '未添加引导话术' }}</p>
      <p class="bio">当前 bio（只读）：<a v-if="canOpen(draft.ig_bio_url)" :href="draft.ig_bio_url" target="_blank" rel="noopener noreferrer">{{ draft.ig_bio_url }}</a><span v-else>未配置</span></p>
    </template>
    <p v-else class="help">这里保存的是本篇链接选择，不会修改全局映射表。</p>
  </section>
</template>
<style scoped>
.local-section { padding: 18px; background: var(--card); border: 1px solid var(--border); border-radius: var(--radius); }
header { display: flex; justify-content: space-between; gap: 12px; }
h3 { margin: 0; font-size: 13px; }
header span, .help, .mapping, .bio { font-size: 12px; color: var(--muted-fg); overflow-wrap: anywhere; }
.link-row { border-bottom: 1px solid var(--border); padding: 8px 0 12px; }
.source { display: flex; gap: 10px; font-size: 12px; overflow-wrap: anywhere; }
.source span { flex: none; color: var(--muted-fg); }
.field { display: grid; gap: 6px; font-size: 12px; margin-top: 12px; }
input[type=url], input[type=text], select { width: 100%; padding: 9px; font: inherit; border: 1px solid var(--border-strong); border-radius: var(--radius); background: var(--card); }
.target { font-size: 13px; overflow-wrap: anywhere; }
.link-check { display: flex; flex-wrap: wrap; align-items: center; gap: 12px; font-size: 12px; margin-top: 10px; }
.link-check label { display: flex; gap: 5px; align-items: center; }
.pending { color: #92400e; }
</style>
