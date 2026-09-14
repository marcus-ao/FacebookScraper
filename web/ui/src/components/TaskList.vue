<script setup>
import { computed, ref } from 'vue'
import { api } from '../api.js'
import { formatSchedule, formatWakeAt, PLATFORM_LABEL, STATUS_LABEL } from '../format.js'
import Icon from './Icon.vue'
import ReviewActions from './ReviewActions.vue'

const props = defineProps({
  tasks: { type: Array, required: true },
  summary: { type: Object, required: true }
})
const emit = defineEmits(['open', 'changed'])
const onlyAlerts = ref(false)
const tab = ref('active')
const selectedTag = ref('')
const selectedMonth = ref('')
const groupOf = (task) => task.status === 'snoozed' ? 'snoozed'
  : ['scheduled', 'skipped', 'handed_off'].includes(task.status) ? 'done' : 'active'
const tabs = computed(() => [
  { id: 'active', label: '待处理' }, { id: 'snoozed', label: '已挂起' }, { id: 'done', label: '已处理' }
].map((item) => ({ ...item, count: props.tasks.filter((t) => groupOf(t) === item.id).length })))
const tags = computed(() => [...new Set(props.tasks.flatMap((t) => t.tags || []))].sort())
const months = computed(() => [...new Set(props.tasks.map((t) => t.month).filter(Boolean))].sort().reverse())
const rows = computed(() => props.tasks.filter((task) => {
  return groupOf(task) === tab.value && (!onlyAlerts.value || task.hard_alerts.length > 0)
    && (!selectedMonth.value || task.month === selectedMonth.value)
    && (!selectedTag.value || (selectedTag.value === '__untagged__'
      ? !(task.tags || []).length : (task.tags || []).includes(selectedTag.value)))
}))
const alertCount = computed(() => props.tasks.filter((t) => t.hard_alerts.length > 0).length)
const isQuiet = (t) => t.hard_alerts.length === 0 && !t.risk_count && !t.author_flag
const isDone = (t) => ['approved', 'scheduled', 'skipped', 'handed_off'].includes(t.status)
</script>

<template>
  <div class="list-page">
    <div class="toolbar">
      <p class="count">
        近 90 天图文帖 <strong>{{ tasks.length }}</strong> 篇
      </p>

      <!-- §10 的那一条筛选。0 篇时不显示——一个永远是 0 的筛选器只是噪声。 -->
      <button
        v-if="alertCount > 0"
        :class="['filter', { on: onlyAlerts }]"
        :aria-pressed="onlyAlerts"
        @click="onlyAlerts = !onlyAlerts"
      >
        <Icon name="alert" :size="14" />
        {{ alertCount }} 篇有硬闸告警
        <span>{{ onlyAlerts ? '· 显示全部' : '· 只看这些' }}</span>
      </button>
      <p v-else class="no-alerts">
        <Icon name="check" :size="14" /> 没有硬闸告警
      </p>
    </div>

    <div class="filters">
      <div class="tabs" role="group" aria-label="审校进度">
        <button v-for="item in tabs" :key="item.id" :class="['btn btn-sm', { selected: tab === item.id }]"
          :aria-pressed="tab === item.id" @click="tab = item.id">{{ item.label }} {{ item.count }}</button>
      </div>
      <label>分类 <select v-model="selectedTag" aria-label="筛选分类">
        <option value="">全部分类</option><option value="__untagged__">未分类</option>
        <option v-for="tag in tags" :key="tag" :value="tag">{{ tag }}</option>
      </select></label>
      <label>月份 <select v-model="selectedMonth" aria-label="筛选月份">
        <option value="">全部月份</option><option v-for="month in months" :key="month" :value="month">{{ month }}</option>
      </select></label>
    </div>
    <p v-if="!rows.length" class="empty">当前筛选下没有帖子。</p>
    <ol class="rows">
      <li
        v-for="task in rows" :key="task.id"
        :class="['row', {
          alert: task.hard_alerts.length > 0,
          quiet: isQuiet(task),
          done: isDone(task),
          waiting: task.status === 'not_ready'
        }]"
      >
        <!-- 缩略图。variant=de，缺德语图时后端回退原图（降级另有明示）。 -->
        <img
          class="thumb" loading="lazy" alt=""
          :src="api.imageUrl(task.id, 0, 'de')"
          width="56" height="56"
        />

        <div class="body">
          <p class="excerpt">
            {{ task.text_de_excerpt || '还没有德语译文' }}
          </p>

          <p v-if="!isQuiet(task)" class="signals">
            <span
              v-for="alert in task.hard_alerts" :key="alert.code"
              class="tag tag-error"
            >
              <Icon name="alert" :size="12" /> {{ alert.label }}
            </span>
            <span v-if="task.risk_count" class="tag tag-risk">
              {{ task.risk_count }} 处需注意
            </span>
            <!--
              常态不显示，异常才显示（§10）：近期 IG 帖 35/36 是合作帖，
              那是常态，每行都标就是噪声。只有第三方作者才标出来。
            -->
            <span v-if="task.author_flag" class="tag tag-neutral">
              <Icon name="user" :size="12" /> {{ task.author_flag }}
            </span>
          </p>

          <p class="facts">
            <span v-if="task.status === 'snoozed'" class="when"><Icon name="clock" :size="13" /> 恢复：{{ formatWakeAt(task.review.wake_at) }}</span>
            <span v-else :class="['when', { firm: task.status === 'scheduled' }]">
              <Icon name="calendar" :size="13" />
              <span v-if="task.schedule && task.status !== 'scheduled'">建议</span>
              {{ formatSchedule(task.schedule && task.schedule.at) || '暂无建议时刻' }}
            </span>
            <span class="dot">·</span>
            <span>{{ PLATFORM_LABEL[task.platform] }}</span>
            <span class="dot">·</span>
            <span><Icon name="image" :size="13" /> {{ task.image_count }} 张图</span>
            <!-- 已处理过的才显示状态文字；待办的不显示，视觉区分已经够了。 -->
            <template v-if="task.status !== 'pending_review'">
              <span class="dot">·</span>
              <span class="status">{{ STATUS_LABEL[task.status] }}</span>
            </template>
          </p>
          <p v-if="task.tags?.length" class="tags"><span v-for="tag in task.tags" :key="tag" class="tag tag-neutral">{{ tag }}</span></p>
        </div>

        <div class="actions">
          <button
            class="btn btn-sm"
            @click="emit('open', task.id)"
          >
            <!--
              字段名是 hard_alerts，不是 alerts（GET /api/tasks 的列表项契约）。
              原先写成 task.alerts 恒为 undefined，所以第三方作者那篇的
              「查看并翻译」从未渲染过。批准的既有缺陷修复，见
              docs/ui-refactor/DECISION_LOG.md D13a。
            -->
            <Icon name="eye" :size="13" /> {{ task.hard_alerts?.some(item => item.code === 'unknown_collaborator') ? '查看并翻译' : '查看' }}
          </button>
          <ReviewActions :detail="task" compact @changed="emit('changed', $event)" />
        </div>
      </li>
    </ol>
  </div>
</template>

<style scoped>
.list-page { padding: var(--space-4) var(--space-5) var(--space-6); }

.toolbar {
  display: flex; align-items: center; gap: var(--space-3);
  margin-bottom: var(--space-3);
}
.count { margin: 0; font-size: 13px; color: var(--muted-fg); }
.count strong { color: var(--fg); font-size: 15px; }

.filter {
  display: inline-flex; align-items: center; gap: 6px;
  min-height: 30px; padding: 0 var(--space-3);
  border: 1px solid var(--error-line);
  border-radius: 999px;
  background: var(--error-soft);
  color: var(--error);
  font-size: 13px; font-weight: 600;
  transition: background var(--ease), box-shadow var(--ease);
}
.filter:hover { background: #fecaca; }
.filter.on { box-shadow: inset 0 0 0 1px var(--error); }
.filter span { font-weight: 500; opacity: .8; }

.no-alerts {
  display: inline-flex; align-items: center; gap: 6px;
  margin: 0; font-size: 13px; color: var(--ok);
}

.rows { list-style: none; margin: 0; padding: 0; display: grid; gap: var(--space-2); }

.row {
  display: grid;
  grid-template-columns: 56px minmax(0, 1fr) auto;
  gap: var(--space-4);
  align-items: center;
  padding: var(--space-3) var(--space-4);
  border: 1px solid var(--border);
  border-left: 3px solid transparent;
  border-radius: var(--radius);
  background: var(--card);
  box-shadow: var(--shadow);
  transition: border-color var(--ease), box-shadow var(--ease);
}
.row:hover { border-color: var(--border-strong); box-shadow: var(--shadow-lg); }

/* 有硬闸告警的行：左侧一条红边，一眼认得出来。 */
.row.alert { border-left-color: var(--error); }
/* 干净本身就是信号——"这篇可以快速过"。所以干净的行**不加任何装饰**。 */
.row.quiet { background: var(--card); }
/* 已通过/已排期/不发的：整行变灰。视觉区分比多一列状态文字省地方。 */
.row.done { background: var(--muted); }
.row.waiting { background: var(--muted); }

.thumb {
  width: 56px; height: 56px;
  object-fit: cover;
  border-radius: var(--radius);
  background: var(--muted);
  border: 1px solid var(--border);
}

.body { min-width: 0; }
.excerpt {
  margin: 0 0 var(--space-1);
  font-size: 14px;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.row.waiting .excerpt { color: var(--muted-fg); font-style: italic; }

.signals {
  display: flex; flex-wrap: wrap; gap: var(--space-2);
  margin: 0 0 var(--space-1);
}

.facts {
  display: flex; align-items: center; flex-wrap: wrap; gap: 6px;
  margin: 0;
  color: var(--muted-fg); font-size: 12px;
}
.facts > span { display: inline-flex; align-items: center; gap: 4px; }
.dot { opacity: .5; }
.when.firm { font-weight: 700; color: var(--fg); }   /* 已排期的时刻加粗 */
.status { font-weight: 600; }

.actions { display: flex; gap: var(--space-2); }
.filters { display: flex; flex-wrap: wrap; align-items: center; gap: var(--space-3); margin-bottom: var(--space-3); }
.tabs { display: flex; gap: var(--space-1); }
.selected { background: var(--primary); color: var(--primary-fg); }
.filters label, .empty { font-size: 13px; color: var(--muted-fg); }
.filters select { border: 1px solid var(--border); border-radius: var(--radius); padding: 6px; background: var(--card); color: var(--fg); }
.tags { display: flex; gap: 4px; margin: 6px 0 0; flex-wrap: wrap; }
.actions { flex-wrap: wrap; }
</style>
