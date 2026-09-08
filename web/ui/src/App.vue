<script setup>
import { onMounted, reactive, ref } from 'vue'
import { ACTOR, api } from './api.js'

import Icon from './components/Icon.vue'
import TaskList from './components/TaskList.vue'
import TaskDetail from './components/TaskDetail.vue'
import ConfirmDialog from './components/ConfirmDialog.vue'

const state = reactive({
  tasks: [],
  summary: { total: 0, with_hard_alerts: 0 },
  loading: true,
  error: '',
  openId: null          // null = 列表页
})

// 确认弹窗。approve 那个是 §13 明写要有的：「她会当场对时刻和渠道提意见」——
// 弹窗把排期时刻和渠道摆到她眼前，那条意见才提得出来。
const dialog = reactive({ open: false, kind: '', task: null, reason: '' })
const busy = ref('')

async function load() {
  state.loading = true
  state.error = ''
  try {
    const data = await api.listTasks()
    state.tasks = data.tasks
    state.summary = data.summary
  } catch (exc) {
    state.error = String(exc.message || exc)
  } finally {
    state.loading = false
  }
}

// 详情页里的动作改完状态后，把那一行就地更新，不整表重拉——
// 62 行重拉一次要读一遍归档，点一下等半秒的界面用起来是另一回事。
function patchRow(detail) {
  const row = state.tasks.find((t) => t.id === detail.id)
  if (!row) return
  row.status = detail.status
  const human = detail.text && detail.text.de_human
  if (human) row.text_de_excerpt = human.replace(/\s+/g, ' ').slice(0, 90) + ' …'
}

function ask(kind, task) {
  Object.assign(dialog, { open: true, kind, task, reason: '' })
}

async function confirmDialog() {
  const task = dialog.task
  const kind = dialog.kind
  const reason = dialog.reason
  dialog.open = false
  busy.value = task.id
  try {
    const detail = kind === 'approve'
      ? await api.approve(task.id, ACTOR)
      : await api.skip(task.id, ACTOR, reason)
    patchRow(detail)
  } catch (exc) {
    state.error = String(exc.message || exc)
  } finally {
    busy.value = ''
  }
}

onMounted(load)
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

      <!--
        ⚠️ 第 13 节：演示前必须说明「原型上点的"通过"不会真的发帖」，
        否则她会以为已经排了。所以这句话不放在口头，钉在界面上。
      -->
      <p class="prototype-note">
        <Icon name="info" :size="14" />
        原型：点「通过」不会真的发帖，也不会改动任何归档数据
      </p>
    </header>

    <p v-if="state.error" class="banner-error" role="alert">
      <Icon name="alert" :size="15" />
      {{ state.error }}
      <button class="btn btn-sm" @click="load">
        <Icon name="refresh" :size="13" /> 重试
      </button>
    </p>

    <main>
      <p v-if="state.loading" class="loading">正在读归档…</p>

      <TaskList
        v-else-if="!state.openId"
        :tasks="state.tasks"
        :summary="state.summary"
        :busy="busy"
        @open="state.openId = $event"
        @approve="ask('approve', $event)"
        @skip="ask('skip', $event)"
      />

      <TaskDetail
        v-else
        :key="state.openId"
        :task-id="state.openId"
        @back="state.openId = null"
        @changed="patchRow"
        @approve="ask('approve', $event)"
        @skip="ask('skip', $event)"
      />
    </main>

    <ConfirmDialog
      v-if="dialog.open"
      :kind="dialog.kind"
      :task="dialog.task"
      v-model:reason="dialog.reason"
      @cancel="dialog.open = false"
      @confirm="confirmDialog"
    />
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
</style>
