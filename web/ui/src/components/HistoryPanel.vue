<script setup>
import { onMounted, reactive, watch } from 'vue'
import { api } from '../api.js'
import { PLATFORM_LABEL, STATUS_LABEL } from '../format.js'
const emit = defineEmits(['open'])
const filters = reactive({ platform: '', month: '', tag: '' })
const state = reactive({ rows: [], page: 1, total: 0, tags: [], months: [], loading: true, error: '', index: null })
let sequence = 0
async function load(page = 1) {
  const request = ++sequence
  state.loading = true
  state.error = ''
  try {
    const result = await api.listTasks({ scope: 'history', ...filters, page, limit: 30 })
    if (request !== sequence) return
    Object.assign(state, { rows: result.tasks, page, total: result.pagination.total,
      tags: result.summary.tags, months: result.summary.months, index: result.index })
  } catch (error) { if (request === sequence) state.error = error.message }
  finally { if (request === sequence) state.loading = false }
}
watch(filters, () => load())
onMounted(load)
</script>
<template>
  <section class="history-page">
    <h2>历史归档</h2>
    <p class="help">查阅全部月份的来源和处理记录。冻结账号只供查阅，打开历史内容不会开始翻译。</p>
    <div class="filters">
      <label>平台 <select v-model="filters.platform"><option value="">全部平台</option><option value="facebook">Facebook</option><option value="instagram">Instagram</option></select></label>
      <label>月份 <select v-model="filters.month"><option value="">全部月份</option><option v-for="month in state.months" :key="month">{{ month }}</option></select></label>
      <label>产品分类 <select v-model="filters.tag"><option value="">全部分类</option><option value="__untagged__">未分类</option><option v-for="tag in state.tags" :key="tag">{{ tag }}</option></select></label>
      <span class="help">共 {{ state.total }} 篇</span>
    </div>
    <p v-if="state.index?.stale" role="status">索引暂未更新，当前结果来自本地归档文件。</p>
    <p v-if="state.error" role="alert">{{ state.error }} <button class="btn btn-sm" @click="load(state.page)">重试</button></p>
    <p v-if="state.loading" role="status">正在查阅归档…</p>
    <p v-else-if="!state.rows.length">当前筛选下没有归档帖子。</p>
    <ol v-else class="history-rows">
      <li v-for="row in state.rows" :key="row.id">
        <img v-if="row.image_count" :src="row.thumbnail_url" alt="" width="56" height="56" loading="lazy">
        <div class="body"><p>{{ row.text_de_excerpt }}</p><p class="help">{{ row.created_at?.slice(0, 10) }} · {{ PLATFORM_LABEL[row.platform] }} · {{ row.account }} · {{ STATUS_LABEL[row.status] }}<span v-if="row.read_only"> · 冻结归档</span></p><p v-if="row.tags.length" class="help">{{ row.tags.join(' / ') }}</p></div>
        <button class="btn btn-sm" @click="emit('open', row.id)">查看归档</button>
      </li>
    </ol>
    <div class="pagination"><button class="btn btn-sm" :disabled="state.loading || state.page <= 1" @click="load(state.page-1)">上一页</button><span>第 {{ state.page }} / {{ Math.max(1, Math.ceil(state.total / 30)) }} 页</span><button class="btn btn-sm" :disabled="state.loading || state.page*30 >= state.total" @click="load(state.page+1)">下一页</button></div>
  </section>
</template>
<style scoped>
.history-page { padding: 24px; } h2 { margin-top: 0; font-size: 18px; }
.help { color: var(--muted-fg); font-size: 12px; line-height: 1.6; }
.filters, .pagination { display: flex; align-items: center; gap: 16px; flex-wrap: wrap; margin: 18px 0; font-size: 13px; }
select { padding: 7px; background: var(--card); border: 1px solid var(--border); border-radius: var(--radius); }
.history-rows { list-style: none; padding: 0; display: grid; gap: 8px; }
li { display: flex; align-items: center; gap: 16px; padding: 14px; border: 1px solid var(--border); border-radius: var(--radius); background: var(--card); }
li img { object-fit: cover; border-radius: var(--radius); } .body { flex: 1; min-width: 0; } .body p { margin: 3px 0; } [role='alert'] { color: var(--error); }
</style>
