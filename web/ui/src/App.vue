<script setup>
import { onMounted, onUnmounted, reactive, ref } from 'vue'
import { api } from './api.js'

import Icon from './components/Icon.vue'
import TaskList from './components/TaskList.vue'
import TaskDetail from './components/TaskDetail.vue'
import CalendarPanel from './components/CalendarPanel.vue'

const detailView = ref(null)
function locationState() {
  const url = new URL(window.location.href)
  const openId = url.searchParams.get('task')
  return { openId, view: !openId && url.searchParams.get('view') === 'calendar' ? 'calendar' : 'tasks' }
}
const state = reactive({
  tasks: [],
  summary: { total: 0, with_hard_alerts: 0 },
  loading: true,
  error: '',
  ...locationState(),
  index: null, calendar: null, calendarBusy: false, calendarError: ''
})

async function load() {
  state.loading = true
  state.error = ''
  try {
    const data = await api.listTasks()
    state.tasks = data.tasks
    state.summary = data.summary
    state.index = data.index
  } catch (exc) {
    state.error = String(exc.message || exc)
  } finally {
    state.loading = false
  }
}

function writeLocation() {
  const url = new URL(window.location.href)
  if (state.openId) url.searchParams.set('task', state.openId)
  else url.searchParams.delete('task')
  if (state.view === 'calendar') url.searchParams.set('view', 'calendar')
  else url.searchParams.delete('view')
  window.history.pushState({}, '', url)
}
function openTask(taskId, alreadyAllowed = false) {
  if (!alreadyAllowed && detailView.value && !detailView.value.mayLeave()) return
  state.openId = taskId || null
  state.view = 'tasks'
  writeLocation()
}
function openCalendar() {
  if (detailView.value && !detailView.value.mayLeave()) return
  state.openId = null
  state.view = 'calendar'
  writeLocation()
  loadCalendar()
}
async function loadCalendar(refresh = false) {
  state.calendarBusy = true
  state.calendarError = ''
  try { state.calendar = await (refresh ? api.refreshCalendar() : api.calendar()) }
  catch (exc) {
    if (Array.isArray(exc.payload?.cards)) state.calendar = exc.payload
    state.calendarError = exc.message
  } finally { state.calendarBusy = false }
}
function restoreLocation() {
  const next = locationState()
  if ((next.openId !== state.openId || next.view !== state.view) && detailView.value && !detailView.value.mayLeave()) {
    writeLocation()
    return
  }
  Object.assign(state, next)
  if (state.view === 'calendar') loadCalendar()
}
function patchRow(detail) {
  const row = state.tasks.find((task) => task.id === detail.id)
  if (!row) return
  Object.assign(row, { status: detail.status, review: detail.review, tags: detail.tags,
    schedule: detail.schedule, source_text_sha256: detail.text.source_text_sha256 })
  const text = detail.text.de_human || detail.text.de_machine || ''
  const excerpt = text.replace(/\s+/g, ' ').trim()
  row.text_de_excerpt = excerpt.length > 90 ? excerpt.slice(0, 90) + ' …' : excerpt
}
onMounted(() => { load(); if (state.view === 'calendar') loadCalendar(); window.addEventListener('popstate', restoreLocation) })
onUnmounted(() => window.removeEventListener('popstate', restoreLocation))
</script>

<template>
  <div class="shell">
    <header class="topbar">
      <div class="brand">
        <span class="brand-mark">DE</span>
        <div>
          <h1>审校台</h1>
          <p>US 站图文帖 → 德语正文与德语图 → 德国站定时发布</p>
        </div>
      </div>

      <p class="prototype-note">
        <Icon name="info" :size="14" />
        保存与排期均会留档；自动排期需完成本机核验
      </p>
    </header>

    <nav class="workspace-nav" aria-label="工作区">
      <button :class="['btn btn-sm', { selected: state.view === 'tasks' }]" :aria-pressed="state.view === 'tasks'" @click="openTask(null)">审校列表</button>
      <button :class="['btn btn-sm', { selected: state.view === 'calendar' }]" :aria-pressed="state.view === 'calendar'" @click="openCalendar">发布月历</button>
    </nav>
    <p v-if="state.index?.stale && state.view === 'tasks'" class="index-note">展示索引暂未更新，当前按归档文件读取。</p>
    <p v-if="state.error" class="banner-error" role="alert">
      <Icon name="alert" :size="15" />
      {{ state.error }}
      <button class="btn btn-sm" @click="load">
        <Icon name="refresh" :size="13" /> 重试
      </button>
    </p>

    <main>
      <CalendarPanel v-if="state.view === 'calendar'" :calendar="state.calendar" :busy="state.calendarBusy" :error="state.calendarError" @refresh="loadCalendar(true)" />
      <p v-else-if="state.loading" class="loading">正在读归档…</p>

      <TaskList
        v-else-if="!state.openId"
        :tasks="state.tasks"
        :summary="state.summary"
        @open="openTask"
        @changed="patchRow"
      />

      <TaskDetail
        v-else
        ref="detailView"
        :key="state.openId"
        :task-id="state.openId"
        @back="openTask(null, true)"
        @changed="patchRow"
      />
    </main>

  </div>
</template>

<style scoped>
.shell { min-height: 100%; display: flex; flex-direction: column; }

.topbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-5);
  padding: var(--space-3) var(--space-5);
  background: var(--card);
  border-bottom: 1px solid var(--border);
}
.brand { display: flex; align-items: center; gap: var(--space-3); }
.brand-mark {
  display: grid;
  place-items: center;
  width: 32px; height: 32px;
  border-radius: var(--radius);
  background: var(--primary);
  color: var(--primary-fg);
  font-size: 12px; font-weight: 700; letter-spacing: .04em;
}
.brand h1 { margin: 0; font-size: 15px; font-weight: 650; }
.brand p { margin: 0; font-size: 12px; color: var(--muted-fg); }

.prototype-note {
  display: flex; align-items: center; gap: 6px;
  margin: 0;
  padding: var(--space-1) var(--space-3);
  border: 1px dashed var(--border-strong);
  border-radius: var(--radius);
  background: var(--muted);
  color: var(--muted-fg);
  font-size: 12px;
}

main { flex: 1; min-height: 0; display: flex; flex-direction: column; }

.loading { padding: var(--space-6); color: var(--muted-fg); }

.banner-error {
  display: flex; align-items: center; gap: var(--space-2);
  margin: 0;
  padding: var(--space-2) var(--space-5);
  background: var(--error-soft);
  color: var(--error);
  font-size: 13px;
}
.banner-error .btn { margin-left: auto; }
.workspace-nav { display: flex; gap: 8px; padding: 10px var(--space-5); background: var(--card); border-bottom: 1px solid var(--border); }
.workspace-nav .selected { background: var(--primary); color: var(--primary-fg); }
.index-note { margin: 0; padding: 8px var(--space-5); background: var(--risk-soft); color: #92400e; font-size: 12px; }
</style>
