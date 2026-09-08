<script setup>
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { formatSchedule, PLATFORM_LABEL } from '../format.js'
import Icon from './Icon.vue'

const props = defineProps({
  kind: { type: String, required: true },     // 'approve' | 'skip'
  task: { type: Object, required: true },
  reason: { type: String, default: '' }
})
const emit = defineEmits(['cancel', 'confirm', 'update:reason'])

const panel = ref(null)
const isApprove = computed(() => props.kind === 'approve')
const when = computed(() => formatSchedule(props.task.schedule && props.task.schedule.at))

function onKey(event) {
  if (event.key === 'Escape') emit('cancel')
}

onMounted(() => {
  document.addEventListener('keydown', onKey)
  // 焦点进弹窗，否则键盘用户会留在被遮住的列表里。
  requestAnimationFrame(() => panel.value && panel.value.focus())
})
onUnmounted(() => document.removeEventListener('keydown', onKey))
</script>

<template>
  <div class="overlay" @click.self="emit('cancel')">
    <div
      ref="panel" class="panel" role="dialog" aria-modal="true"
      :aria-label="isApprove ? '确认通过' : '确认不发这篇'" tabindex="-1"
    >
      <h2>
        <Icon :name="isApprove ? 'check' : 'ban'" :size="17" />
        {{ isApprove ? '通过这篇' : '这篇不发' }}
      </h2>

      <p class="excerpt">{{ task.text_de_excerpt || '（还没有德语译文）' }}</p>

      <!--
        §13：这个弹窗存在的目的就是把**时刻**和**渠道**摆到她眼前，
        让她当场提意见。所以这两项是弹窗的主体，不是脚注。
      -->
      <dl v-if="isApprove" class="facts">
        <div>
          <dt><Icon name="calendar" :size="13" /> 排期时刻</dt>
          <dd :class="{ muted: !when }">{{ when || '还没有分配槽位' }}</dd>
        </div>
        <div>
          <dt><Icon name="image" :size="13" /> 发布渠道</dt>
          <dd>{{ PLATFORM_LABEL[task.schedule && task.schedule.channel] || PLATFORM_LABEL[task.platform] }}</dd>
        </div>
      </dl>

      <!--
        「什么情况下我会点这个？」是这个原型最想套出的答案（§13）。
        写不写都行 —— 硬性要求填理由就变成一道闸，而这个项目的反模式正是硬拦人工。
      -->
      <label v-else class="reason">
        <span>为什么不发？（选填，会留痕）</span>
        <textarea
          :value="reason" rows="3"
          placeholder="例如：Black Friday 是美国限定活动，德国站没有这个档期"
          @input="emit('update:reason', $event.target.value)"
        ></textarea>
      </label>

      <p class="footnote">
        <Icon name="info" :size="13" />
        原型不会真的发帖，也不会改动 archive/ 与 state/ 里的任何文件。
      </p>

      <div class="actions">
        <button class="btn" @click="emit('cancel')">取消</button>
        <button
          :class="['btn', isApprove ? 'btn-primary' : 'btn-danger']"
          @click="emit('confirm')"
        >
          {{ isApprove ? '确认通过' : '确认不发' }}
        </button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.overlay {
  position: fixed; inset: 0; z-index: 50;
  display: grid; place-items: center;
  padding: var(--space-5);
  background: rgba(15, 23, 42, .42);
}
.panel {
  width: min(440px, 100%);
  padding: var(--space-5);
  border-radius: var(--radius-lg);
  background: var(--card);
  box-shadow: var(--shadow-lg);
}
h2 {
  display: flex; align-items: center; gap: var(--space-2);
  margin: 0 0 var(--space-3);
  font-size: 15px; font-weight: 650;
}
.excerpt {
  margin: 0 0 var(--space-4);
  padding: var(--space-2) var(--space-3);
  border-left: 3px solid var(--border-strong);
  background: var(--muted);
  color: var(--muted-fg);
  font-size: 13px;
  /* 三行封顶：弹窗是来做决定的，不是来读全文的 */
  display: -webkit-box; -webkit-line-clamp: 3; -webkit-box-orient: vertical;
  overflow: hidden;
}

.facts { margin: 0 0 var(--space-4); display: grid; gap: var(--space-2); }
.facts > div {
  display: flex; align-items: baseline; justify-content: space-between;
  gap: var(--space-4);
  padding-bottom: var(--space-2);
  border-bottom: 1px solid var(--border);
}
.facts dt {
  display: flex; align-items: center; gap: 6px;
  color: var(--muted-fg); font-size: 12px;
}
.facts dd { margin: 0; font-size: 14px; font-weight: 600; }
.facts dd.muted { font-weight: 400; color: var(--muted-fg); }

.reason { display: block; margin-bottom: var(--space-4); }
.reason span { display: block; margin-bottom: 6px; font-size: 12px; color: var(--muted-fg); }
.reason textarea {
  width: 100%;
  padding: var(--space-2);
  border: 1px solid var(--border-strong);
  border-radius: var(--radius);
  font: inherit; font-size: 13px;
  resize: vertical;
}

.footnote {
  display: flex; align-items: center; gap: 6px;
  margin: 0 0 var(--space-4);
  color: var(--muted-fg); font-size: 12px;
}

.actions { display: flex; justify-content: flex-end; gap: var(--space-2); }
</style>
