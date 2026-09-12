<script setup>
import { computed, ref } from 'vue'
import { api } from '../api.js'
import { formatSchedule, PLATFORM_LABEL, STATUS_LABEL } from '../format.js'
import Icon from './Icon.vue'

const props = defineProps({
  tasks: { type: Array, required: true },
  summary: { type: Object, required: true }
})
const emit = defineEmits(['open'])

// ⛔ **不做「有告警」和「常规」两个 tab**（§10）：分 tab 的实际结果是只有第一个
// 被看，第二个 tab 里的会被批量通过——那正好绕过了这个界面存在的意义。
// 所以只有一个可开关的筛选，且默认关着，62 篇始终在同一个列表里。
const onlyAlerts = ref(false)

const rows = computed(() => props.tasks.filter(
  (t) => !onlyAlerts.value || t.hard_alerts.length > 0))

// 一行的唯一职责：回答「我需不需要点开这篇」。所以显示的是异常，不是内容。
const isQuiet = (t) => t.hard_alerts.length === 0 && !t.risk_count && !t.author_flag
const isDone = (t) => ['approved', 'scheduled', 'skipped'].includes(t.status)
const canOpen = (t) => t.status !== 'not_ready'
</script>

<template>
  <div class="list-page">
    <div class="toolbar">
      <p class="count">
        近 90 天图文帖 <strong>{{ summary.total }}</strong> 篇
      </p>

      <!-- §10 的那一条筛选。0 篇时不显示——一个永远是 0 的筛选器只是噪声。 -->
      <button
        v-if="summary.with_hard_alerts > 0"
        :class="['filter', { on: onlyAlerts }]"
        :aria-pressed="onlyAlerts"
        @click="onlyAlerts = !onlyAlerts"
      >
        <Icon name="alert" :size="14" />
        {{ summary.with_hard_alerts }} 篇有硬闸告警
        <span>{{ onlyAlerts ? '· 显示全部' : '· 只看这些' }}</span>
      </button>
      <p v-else class="no-alerts">
        <Icon name="check" :size="14" /> 没有硬闸告警
      </p>
    </div>

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
            <span :class="['when', { firm: task.status === 'scheduled' }]">
              <Icon name="calendar" :size="13" />
              <span v-if="task.schedule && task.status !== 'scheduled'">建议</span>
              {{ formatSchedule(task.schedule && task.schedule.at) || '暂无建议时刻' }}
            </span>
            <span class="dot">·</span>
            <span>{{ PLATFORM_LABEL[task.platform] }}</span>
            <span class="dot">·</span>
            <span><Icon name="image" :size="13" /> {{ task.image_count }} 张图</span>
            <!-- 已处理过的才显示状态文字；待办的不显示，视觉区分已经够了。 -->
            <template v-if="isDone(task) || ['not_ready', 'edited'].includes(task.status)">
              <span class="dot">·</span>
              <span class="status">{{ STATUS_LABEL[task.status] }}</span>
            </template>
          </p>
        </div>

        <div class="actions">
          <button
            class="btn btn-sm" :disabled="!canOpen(task)"
            :title="canOpen(task) ? '' : '还没有译文，暂时无法审校'"
            @click="emit('open', task.id)"
          >
            <Icon name="eye" :size="13" /> 查看
          </button>
          <button
            class="btn btn-sm btn-primary" disabled
            title="审校通过与排期将在后续接通"
          >
            <Icon name="check" :size="13" /> 通过
          </button>
          <button
            class="btn btn-sm btn-danger" disabled
            title="不发与挂起等审校状态将在后续接通"
          >
            <Icon name="ban" :size="13" /> 这篇不发
          </button>
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
.row.done { opacity: .55; }
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
</style>
