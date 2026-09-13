<script setup>
import { computed } from 'vue'

const props = defineProps({ calendar: { type: Object, default: null }, busy: Boolean, error: String })
defineEmits(['refresh'])

const zone = computed(() => props.calendar?.business_timezone || 'Europe/Berlin')
const weekdays = ['周一', '周二', '周三', '周四', '周五', '周六', '周日']
const channels = { facebook: 'Facebook', instagram: 'Instagram' }
const seenAt = computed(() => props.calendar?.cached_at ? new Intl.DateTimeFormat('zh-CN', {
  timeZone: zone.value, month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false
}).format(new Date(props.calendar.cached_at)) : '')
const complete = computed(() => props.calendar?.coverage?.matches_current_month)
const grid = computed(() => {
  const data = props.calendar
  if (!data?.display_start || !data?.display_end_exclusive) return []
  // 这里只迭代日期标签；排期时刻及当地日期由服务端用 IANA 时区换算。
  const first = new Date(`${data.display_start.slice(0, 10)}T00:00:00Z`)
  const last = new Date(`${data.display_end_exclusive.slice(0, 10)}T00:00:00Z`)
  const cells = Array.from({ length: (first.getUTCDay() + 6) % 7 }, (_, index) => ({ key: `pad-${index}` }))
  for (let stamp = first.getTime(); stamp <= last.getTime(); stamp += 86400000) {
    const key = new Date(stamp).toISOString().slice(0, 10)
    cells.push({ key, label: `${Number(key.slice(5, 7))}/${Number(key.slice(8, 10))}`,
      edge: key.slice(0, 7) !== data.month_ui,
      cards: (data.cards || []).filter(card => card.at_business?.slice(0, 10) === key) })
  }
  return cells
})
const showGrid = computed(() => props.calendar?.cached_at && complete.value)

function time(at) {
  return new Intl.DateTimeFormat('zh-CN', { timeZone: zone.value,
    hour: '2-digit', minute: '2-digit', hour12: false }).format(new Date(at))
}
</script>

<template>
  <section class="calendar-panel" aria-label="发布月历" :aria-busy="busy">
    <header class="calendar-heading">
      <div>
        <h2>{{ calendar?.month_ui || '' }} 发布月历</h2>
        <p>以柏林时间查看 Business Suite 中的排期和已发布内容，也包含人工在后台创建的帖子。</p>
      </div>
      <button class="btn btn-sm" :disabled="busy || !calendar?.refresh_available" @click="$emit('refresh')">
        {{ busy ? '正在读取…' : '刷新月历' }}
      </button>
    </header>

    <div class="calendar-meta">
      <span v-if="seenAt">数据截至 {{ seenAt }}（柏林时间）</span>
      <span v-else>尚未读取发布日历</span>
      <span v-if="calendar?.stale" class="calendar-stale">数据可能已过期</span>
      <span v-if="calendar?.cached_at">{{ calendar.cards?.length || 0 }} 条可见记录</span>
    </div>
    <p v-if="error || calendar?.error" class="calendar-warning" role="alert">{{ error || calendar.error }}</p>
    <p v-if="calendar?.status === 'partial'" class="calendar-warning">
      部分帖子的渠道暂未识别，请在后台确认；目前无法可靠判断可用时刻。
    </p>
    <details v-if="calendar && !calendar.refresh_available" class="calendar-unavailable">
      <summary>日历刷新暂不可用</summary>
      <p>{{ calendar.refresh_unavailable_reason }}</p>
    </details>

    <div v-if="!calendar" class="calendar-empty" role="status">正在读取月历信息…</div>
    <div v-else-if="!calendar.cached_at" class="calendar-empty">
      <h3>还没有读取过发布日历</h3>
      <p>读取成功后，这里会显示 Business Suite 中本月的帖子。</p>
    </div>
    <div v-else-if="!complete" class="calendar-empty">
      <h3>当前缓存没有覆盖本月</h3>
      <p>上次的数据已保留。刷新成功后才能确认这个月的排期。</p>
    </div>
    <div v-if="showGrid" class="calendar-scroll">
      <p v-if="!calendar.cards?.length" class="calendar-empty-month">本次读取的月份中没有排期记录。</p>
      <div class="calendar-grid" role="list" aria-label="按柏林日期排列的帖子">
        <div v-for="day in weekdays" :key="day" class="calendar-weekday" aria-hidden="true">{{ day }}</div>
        <div v-for="cell in grid" :key="cell.key" class="calendar-day" :class="{ 'calendar-pad': !cell.label }"
             :role="cell.label ? 'listitem' : undefined" :aria-label="cell.label ? `${cell.key} 柏林时间` : undefined">
          <div v-if="cell.label" class="calendar-date">
            <span>{{ cell.label }}</span><span v-if="cell.edge" class="calendar-edge">跨月时差</span>
          </div>
          <details v-for="(card, index) in cell.cards || []" :key="`${card.at}-${card.card_sha256}-${index}`" class="calendar-entry">
            <summary>
              <strong>{{ time(card.at) }}</strong>
              <span>{{ card.channels.length ? card.channels.map(channel => channels[channel]).join(' / ') : '渠道待确认' }}</span>
              <span>{{ card.delivery === 'published' ? '已观测到公开发布' : card.delivery === 'scheduled' ? '已创建定时任务' : '发布状态待核验' }}</span>
              <span class="calendar-excerpt">{{ card.rendered || '正文未提供' }}</span>
            </summary>
            <p class="calendar-fulltext">{{ card.rendered || '正文未提供' }}</p>
          </details>
        </div>
      </div>
    </div>
    <p class="calendar-note">月份按发布后台的美西自然月计算，柏林月初的部分时刻可能属于上一发布月份。选时提示来自缓存；正式排期前会再次读取后台核对。</p>
  </section>
</template>

<style scoped>
.calendar-panel { padding: 20px; overflow: auto; height: 100%; }
.calendar-heading { display: flex; justify-content: space-between; align-items: center; gap: 20px; }
.calendar-heading h2 { margin: 0 0 6px; font-size: 20px; }
.calendar-heading p, .calendar-note { margin: 0; color: var(--muted-fg); font-size: 12px; }
.calendar-meta { display: flex; flex-wrap: wrap; gap: 16px; margin: 16px 0; color: var(--muted-fg); font-size: 12px; }
.calendar-stale { color: var(--risk); }
.calendar-warning { padding: 10px 12px; border: 1px solid var(--risk-line); border-radius: 6px; background: var(--risk-soft); }
.calendar-unavailable { margin: 12px 0; color: var(--muted-fg); font-size: 12px; }
.calendar-unavailable summary { cursor: pointer; }
.calendar-unavailable p { white-space: pre-wrap; overflow-wrap: anywhere; }
.calendar-empty { padding: 52px 24px; text-align: center; border: 1px dashed var(--border-strong); border-radius: 8px; background: var(--card); }
.calendar-empty h3 { font-size: 16px; margin: 0 0 8px; }
.calendar-empty p, .calendar-empty-month { color: var(--muted-fg); font-size: 13px; }
.calendar-scroll { overflow: auto; border: 1px solid var(--border); border-radius: 8px; background: var(--card); }
.calendar-empty-month { margin: 12px; }
.calendar-grid { display: grid; grid-template-columns: repeat(7, minmax(135px, 1fr)); min-width: 945px; }
.calendar-weekday { padding: 8px 10px; color: var(--muted-fg); background: var(--muted); font-size: 12px; }
.calendar-day { min-height: 110px; padding: 8px; border-top: 1px solid var(--border); border-right: 1px solid var(--border); }
.calendar-day:nth-child(7n) { border-right: 0; }
.calendar-pad { background: var(--muted); }
.calendar-date { display: flex; justify-content: space-between; gap: 3px; color: var(--muted-fg); font-size: 12px; }
.calendar-edge { font-size: 10px; }
.calendar-entry { margin-top: 7px; padding: 7px; border: 1px solid var(--border); border-radius: 5px; background: var(--muted); }
.calendar-entry summary { display: flex; flex-wrap: wrap; column-gap: 7px; cursor: pointer; font-size: 11px; }
.calendar-entry strong { font-size: 12px; }
.calendar-excerpt { flex-basis: 100%; display: -webkit-box; -webkit-box-orient: vertical; -webkit-line-clamp: 3; overflow: hidden; margin-top: 4px; overflow-wrap: anywhere; }
.calendar-fulltext { white-space: pre-wrap; overflow-wrap: anywhere; font-size: 12px; margin: 8px 0 0; }
.calendar-note { margin-top: 12px; }
@media (max-width: 700px) { .calendar-panel { padding: 12px; } .calendar-heading { align-items: flex-start; } }
</style>
