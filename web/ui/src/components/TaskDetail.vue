<script setup>
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { ACTOR, api } from '../api.js'
import { buildMarks, charLength, MARK_ERROR, MARK_RISK, MARK_WARN } from '../marks.js'
import { PLATFORM_LABEL, RISK_KIND_LABEL, STATUS_LABEL } from '../format.js'
import Icon from './Icon.vue'
import TextCompare from './TextCompare.vue'
import ImageCompare from './ImageCompare.vue'
import MetaPanel from './MetaPanel.vue'

const props = defineProps({ taskId: { type: String, required: true } })
const emit = defineEmits(['back', 'changed', 'approve', 'skip'])

const detail = ref(null)
const loading = ref(true)
const error = ref('')

// 「下一处 →」的游标。§11.1：在 1700~3400 字符的长度下光高亮不够，
// 她会扫一眼觉得"差不多"就过了。逐个跳把"检查"从一个开放任务变成一个
// **有终点**的任务（跳到没有下一处 = 都看过了）。
const active = ref(-1)

// 编辑态。**显式进入**（§11.1）：审校的主要动作是读不是写，永远可编辑会让
// 光标乱跳、误触改动；更重要的是显式编辑态带出「你正在修改机器译文」
// 这个语义——它会产生一条 translated_human.jsonl 记录，是有分量的动作。
const editing = ref(false)
const draft = ref('')
const liveMarks = ref([])
const checking = ref(false)
const saving = ref(false)
let checkTimer = null

const unseenImages = ref(0)

// 标记地图的比例位置按**字符数**算，和后端下标同一口径。
const enChars = computed(() => charLength(detail.value ? detail.value.text.en : ''))

const marks = computed(() => (detail.value
  ? buildMarks(detail.value.highlights, detail.value.risks)
  : []))
const shownMarks = computed(() => (editing.value ? liveMarks.value : marks.value))

const currentText = computed(() => {
  if (!detail.value) return ''
  return detail.value.text.de_human || detail.value.text.de_machine || ''
})
const errorCount = computed(
  () => shownMarks.value.filter((m) => m.type === MARK_ERROR).length)
const warnCount = computed(
  () => shownMarks.value.filter((m) => m.type === MARK_WARN).length)
const riskCount = computed(
  () => shownMarks.value.filter((m) => m.type === MARK_RISK).length)

async function load() {
  loading.value = true
  error.value = ''
  try {
    detail.value = await api.getTask(props.taskId)
    active.value = -1
  } catch (exc) {
    error.value = String(exc.message || exc)
  } finally {
    loading.value = false
  }
}

function jump(delta) {
  const total = shownMarks.value.length
  if (!total) return
  active.value = (active.value + delta + total) % total
}

function startEdit() {
  draft.value = currentText.value
  liveMarks.value = marks.value
  editing.value = true
  active.value = -1
}

function discard() {
  editing.value = false
  draft.value = ''
  liveMarks.value = []
}

// 实时校验：前端调 API，几十毫秒（§11.4）。
// ⛔ 它**永远不会拦保存**——这几个检查是给人看的辅助，不是硬闸。
// 她可能有正当理由改一个金额（比如原文写错了）。
watch(draft, (value) => {
  if (!editing.value) return
  clearTimeout(checkTimer)
  checking.value = true
  checkTimer = setTimeout(async () => {
    try {
      const result = await api.check(props.taskId, value)
      // 风险预扫描扫的是英文原文，改德语不会让它变，所以照旧带着。
      liveMarks.value = buildMarks(result.highlights, detail.value.risks)
    } catch { /* 校验挂了不该影响编辑 */ } finally {
      checking.value = false
    }
  }, 250)
})

async function save() {
  saving.value = true
  try {
    detail.value = await api.saveTextDe(props.taskId, draft.value, ACTOR)
    emit('changed', detail.value)
    discard()
  } catch (exc) {
    error.value = String(exc.message || exc)
  } finally {
    saving.value = false
  }
}

function onKey(event) {
  if (editing.value || event.target.matches('input, textarea')) return
  if (event.key === 'n' || event.key === 'ArrowDown') { event.preventDefault(); jump(1) }
  if (event.key === 'p' || event.key === 'ArrowUp') { event.preventDefault(); jump(-1) }
  if (event.key === 'Escape') emit('back')
}

onMounted(() => { load(); document.addEventListener('keydown', onKey) })
onUnmounted(() => { clearTimeout(checkTimer); document.removeEventListener('keydown', onKey) })
watch(() => props.taskId, load)
</script>

<template>
  <div class="detail">
    <p v-if="loading" class="loading">正在读这一篇…</p>
    <p v-else-if="error" class="banner-error">{{ error }}</p>

    <template v-else-if="detail">
      <!-- ---------- 吸顶动作条 ---------- -->
      <header class="bar">
        <button class="btn btn-ghost btn-sm" @click="emit('back')">
          <Icon name="arrowLeft" :size="14" /> 返回列表
        </button>

        <span class="who">
          {{ PLATFORM_LABEL[detail.platform] }}
          <span class="tag tag-neutral">{{ STATUS_LABEL[detail.status] }}</span>
          <span v-if="detail.text.stale" class="tag tag-error">
            <Icon name="alert" :size="12" /> 原文已变更，请复核
          </span>
        </span>

        <!-- 标记计数 + 逐个跳。红黄分开数，因为它们是两件事。 -->
        <span class="counts">
          <span v-if="errorCount" class="tag tag-error">{{ errorCount }} 处错</span>
          <span v-if="warnCount" class="tag tag-error">{{ warnCount }} 处待确认</span>
          <span v-if="riskCount" class="tag tag-risk">{{ riskCount }} 处需注意</span>
          <span v-if="!shownMarks.length" class="tag tag-ok">
            <Icon name="check" :size="12" /> 没有标记
          </span>
        </span>

        <span class="jump">
          <span class="pos">
            {{ active >= 0 ? `${active + 1} / ${shownMarks.length}` : `共 ${shownMarks.length} 处` }}
          </span>
          <button class="btn btn-sm" :disabled="!shownMarks.length" @click="jump(-1)">
            <Icon name="arrowLeft" :size="13" /> 上一处
          </button>
          <button class="btn btn-sm" :disabled="!shownMarks.length" @click="jump(1)">
            下一处 <Icon name="arrowRight" :size="13" />
          </button>
        </span>

        <span class="acts">
          <template v-if="!editing">
            <button class="btn btn-sm" @click="startEdit">
              <Icon name="pencil" :size="13" /> 编辑德语
            </button>
            <button class="btn btn-sm btn-primary" @click="emit('approve', detail)">
              <Icon name="check" :size="13" /> 通过
            </button>
            <button class="btn btn-sm btn-danger" @click="emit('skip', detail)">
              <Icon name="ban" :size="13" /> 这篇不发
            </button>
          </template>
          <template v-else>
            <button class="btn btn-sm" :disabled="saving" @click="discard">放弃修改</button>
            <button class="btn btn-sm btn-primary" :disabled="saving" @click="save">
              <Icon name="check" :size="13" /> {{ saving ? '保存中…' : '保存' }}
            </button>
          </template>
        </span>
      </header>

      <!--
        §11.2：没逐张看完就点通过 —— **提示不拦**。硬拦人工是这个项目的反模式。
        所以这条提示常驻在动作条下方，看得见，但从不禁用「通过」。
      -->
      <p v-if="unseenImages > 0 && !editing" class="nudge">
        <Icon name="info" :size="13" />
        还有 {{ unseenImages }} 张图没查看。可以直接通过，这里只是提醒。
      </p>

      <!-- 标记地图（§11.1 的加分项）：全部标记按在原文里的位置摊平，
           一眼看出它们扎堆在哪儿，也能直接点过去。 -->
      <div v-if="shownMarks.length" class="mark-map" aria-hidden="true">
        <button
          v-for="mark in shownMarks" :key="mark.index"
          :class="['tick', `tick-${mark.type}`, { on: mark.index === active }]"
          :style="{ left: `${((mark.en ? mark.en[0] : enChars) / Math.max(1, enChars)) * 100}%` }"
          :title="mark.label"
          tabindex="-1"
          @click="active = mark.index"
        ></button>
      </div>

      <!-- ---------- 正文对比 ---------- -->
      <TextCompare
        :en="detail.text.en"
        :de="currentText"
        :marks="marks"
        :live-marks="liveMarks"
        :active-index="active"
        :editing="editing"
        :checking="checking"
        :is-human="!!detail.text.de_human"
        v-model:draft="draft"
        @select="active = $event"
      />

      <!-- 当前那一处的说明。标记本身只有颜色，说明在这里。 -->
      <p v-if="active >= 0 && shownMarks[active]" :class="['explain', shownMarks[active].type]">
        <Icon :name="shownMarks[active].type === 'risk' ? 'info' : 'alert'" :size="14" />
        <span v-if="shownMarks[active].type === 'risk'" class="kind">
          {{ RISK_KIND_LABEL[shownMarks[active].kind] || shownMarks[active].kind }}
        </span>
        {{ shownMarks[active].label }}
      </p>

      <div class="lower">
        <ImageCompare
          v-if="detail.images.length"
          :task-id="detail.id"
          :images="detail.images"
          @progress="unseenImages = $event"
        />
        <MetaPanel :detail="detail" />
      </div>
    </template>
  </div>
</template>

<style scoped>
.detail { padding: 0 var(--space-5) var(--space-6); }
.loading { padding: var(--space-6); color: var(--muted-fg); }
.banner-error { padding: var(--space-4); color: var(--error); }

.bar {
  position: sticky; top: 0; z-index: 10;
  display: flex; align-items: center; flex-wrap: wrap; gap: var(--space-3);
  margin: 0 calc(var(--space-5) * -1) var(--space-3);
  padding: var(--space-2) var(--space-5);
  background: var(--card);
  border-bottom: 1px solid var(--border);
  box-shadow: var(--shadow);
}
.who { display: flex; align-items: center; gap: var(--space-2); font-size: 13px; font-weight: 600; }
.counts { display: flex; align-items: center; gap: var(--space-2); }
.jump { display: flex; align-items: center; gap: var(--space-2); margin-left: auto; }
.pos {
  font-size: 12px; color: var(--muted-fg);
  font-variant-numeric: tabular-nums; min-width: 62px; text-align: right;
}
.acts { display: flex; gap: var(--space-2); }

.nudge {
  display: flex; align-items: center; gap: 6px;
  margin: 0 0 var(--space-3);
  padding: var(--space-2) var(--space-3);
  border: 1px solid var(--border-strong);
  border-radius: var(--radius);
  background: var(--muted);
  color: var(--muted-fg);
  font-size: 12px;
}

.mark-map {
  position: relative;
  height: 10px;
  margin-bottom: var(--space-2);
  border-radius: 5px;
  background: var(--muted);
  border: 1px solid var(--border);
}
.tick {
  position: absolute; top: -1px;
  width: 3px; height: 10px;
  padding: 0;
  border: 0; border-radius: 2px;
  transform: translateX(-1px);
}
.tick-error { background: var(--error); }
.tick-warn { background: var(--error-line); }
.tick-risk { background: var(--risk-line); }
.tick.on { height: 16px; top: -4px; width: 4px; box-shadow: 0 0 0 2px var(--primary); }

.explain {
  display: flex; align-items: flex-start; gap: var(--space-2);
  margin: var(--space-3) 0 0;
  padding: var(--space-2) var(--space-3);
  border-radius: var(--radius);
  font-size: 13px;
}
.explain.error, .explain.warn { background: var(--error-soft); color: #7f1d1d; }
.explain.risk { background: var(--risk-soft); color: #78350f; }
.explain .kind { font-weight: 700; }

.lower {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 340px;
  gap: var(--space-4);
  margin-top: var(--space-4);
  align-items: start;
}

/* §12：只考虑桌面（办公室电脑，≥1440px）。左右并排在窄屏上物理不成立，
   所以窄屏只做**不崩**，不做适配——这条要在业务访谈时确认。 */
@media (max-width: 1200px) {
  .lower { grid-template-columns: 1fr; }
}
</style>
