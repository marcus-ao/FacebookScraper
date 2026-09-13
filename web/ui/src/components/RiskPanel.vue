<script setup>
import { computed } from 'vue'
import { formatWakeAt, RISK_KIND_LABEL } from '../format.js'

const props = defineProps({ scan: { type: Object, default: null } })
const status = computed(() => props.scan?.status || 'not_scanned')
const labels = {
  not_scanned: '尚未扫描',
  failed: '扫描失败，需人工审校',
  completed: '扫描完成',
  stale: '扫描已失效'
}
</script>

<template>
  <section class="risk-panel" :class="`status-${status}`" aria-label="英文语义风险预扫">
    <header>
      <h3>英文语义风险预扫</h3>
      <span class="tag" :class="status === 'completed' ? 'tag-ok' : 'tag-risk'">{{ labels[status] }}</span>
    </header>
    <p>{{ scan?.message || '尚未进行英文语义风险预扫；请人工检查双关、歧义和美国特定表达。' }}</p>
    <ul v-if="status === 'completed' && scan?.risks?.length">
      <li v-for="(risk, index) in scan.risks" :key="index">
        <strong>{{ RISK_KIND_LABEL[risk.kind] || risk.kind }}</strong>
        <q>{{ risk.quote }}</q> · {{ risk.label }}
      </li>
    </ul>
    <p v-else-if="status === 'completed'" class="empty">结果为空列表：模型未报告双关、歧义或美国特定表达；仍由人工完成审校。</p>
    <p v-if="scan?.scanned_at" class="meta">
      {{ formatWakeAt(scan.scanned_at) }} · prompt v{{ scan.prompt_version }}
      <template v-if="scan.source?.provider"> · {{ scan.source.provider }}</template>
      <template v-if="scan.source?.model"> / {{ scan.source.model }}</template>
    </p>
  </section>
</template>

<style scoped>
.risk-panel { padding: 14px 16px; margin-bottom: 18px; border: 1px solid var(--border-strong); border-radius: var(--radius); background: var(--card); font-size: 13px; }
header { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
h3 { margin: 0; font-size: 14px; }
p { margin: 8px 0 0; line-height: 1.55; }
ul { margin: 10px 0 0; padding-left: 20px; }
li { margin: 6px 0; }
li strong { margin-right: 6px; }
li q { font-weight: 600; }
.meta, .empty { color: var(--muted-fg); font-size: 12px; }
.status-failed, .status-stale, .status-not_scanned { border-color: var(--risk-line); background: var(--risk-soft); }
</style>
