<script setup>
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { api } from '../api.js'
import { buildMarks, charLength, MARK_ERROR, MARK_RISK, MARK_WARN } from '../marks.js'
import { PLATFORM_LABEL, RISK_KIND_LABEL, STATUS_LABEL } from '../format.js'
import Icon from './Icon.vue'
import TextCompare from './TextCompare.vue'
import ImageCompare from './ImageCompare.vue'
import MetaPanel from './MetaPanel.vue'
import ReviewActions from './ReviewActions.vue'
import HashtagEditor from './HashtagEditor.vue'
import LinkEditor from './LinkEditor.vue'
import RefinementPanel from './RefinementPanel.vue'
import ApprovalPanel from './ApprovalPanel.vue'
import InitialTranslationPanel from './InitialTranslationPanel.vue'

const props = defineProps({ taskId: { type: String, required: true } })
const emit = defineEmits(['back', 'changed'])

const detail = ref(null)
const loading = ref(true)
const error = ref('')
const conflict = ref(false)
const saved = ref(false)
const candidateAdopted = ref(false)

// 「下一处 →」的游标。§11.1：在 1700~3400 字符的长度下光高亮不够，
// 她会扫一眼觉得"差不多"就过了。逐个跳把"检查"从一个开放任务变成一个
// **有终点**的任务（跳到没有下一处 = 都看过了）。
const active = ref(-1)

// 编辑态。**显式进入**（§11.1）：审校的主要动作是读不是写，永远可编辑会让
// 光标乱跳、误触改动；更重要的是显式编辑态带出「你正在修改机器译文」
// 这个语义——它会产生一条 translated_human.jsonl 记录，是有分量的动作。
const editing = ref(false)
const draft = ref('')
const localizationDraft = ref(null)
const liveMarks = ref([])
const checking = ref(false)
const saving = ref(false)
let checkTimer = null
let checkSequence = 0

const unseenImages = ref(0)

// 标记地图的比例位置按**字符数**算，和后端下标同一口径。
const enChars = computed(() => charLength(detail.value ? detail.value.localization.source_body : ''))

const marks = computed(() => (detail.value
  ? buildMarks(detail.value.body_highlights, detail.value.body_risks)
  : []))
const shownMarks = computed(() => (editing.value ? liveMarks.value : marks.value))

const currentText = computed(() => {
  if (!detail.value) return ''
  return detail.value.localization.body_de || ''
})
const shownLocalization = computed(() => editing.value ? { ...localizationDraft.value, body_de: draft.value } : detail.value?.localization)
const captionLength = computed(() => {
  const value = shownLocalization.value
  if (!value) return 0
  const tail = value.platform === 'instagram' ? value.ig_cta : value.links.map(link => /^https?:\/\//.test(link.target_url) ? link.target_url : '').filter(Boolean).join('\n')
  return charLength([value.body_de.trim(), tail.trim(), value.tags.join(' ')].filter(Boolean).join('\n\n'))
})
function changeLocalization(fields) { Object.assign(localizationDraft.value, fields) }
function hasUnsavedChanges() {
  if (!editing.value) return false
  const baseline = detail.value.localization
  return draft.value !== baseline.body_de || ['tags', 'links', 'hashtags_confirmed', 'ig_cta'].some(key =>
    JSON.stringify(localizationDraft.value[key]) !== JSON.stringify(baseline[key]))
}
const canEdit = computed(() => !detail.value?.read_only && !['approved', 'scheduled', 'skipped', 'handed_off'].includes(detail.value?.status))
function applyDetail(value) {
  detail.value = value
  saved.value = false
  emit('changed', value)
}
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
  error.value = ''
  saved.value = false
  draft.value = currentText.value
  localizationDraft.value = JSON.parse(JSON.stringify(detail.value.localization))
  liveMarks.value = marks.value
  editing.value = true
  active.value = -1
}

function adoptCandidate(job) {
  if (editing.value && hasUnsavedChanges() && !window.confirm('采用候选将替换编辑区中的正文，继续吗？')) return
  if (!editing.value) startEdit()
  let body = job.body_de || ''
  const cta = localizationDraft.value.ig_cta
  if (detail.value.platform === 'instagram' && cta && body.endsWith(cta)) body = body.slice(0, -cta.length).trimEnd()
  draft.value = body
  candidateAdopted.value = true
}

function discard() {
  candidateAdopted.value = false
  clearTimeout(checkTimer)
  checkSequence += 1
  checking.value = false
  error.value = ''
  conflict.value = false
  editing.value = false
  draft.value = ''
  localizationDraft.value = null
  liveMarks.value = []
}

// 实时校验：前端调 API，几十毫秒（§11.4）。
// ⛔ 它**永远不会拦保存**——这几个检查是给人看的辅助，不是硬闸。
// 她可能有正当理由改一个金额（比如原文写错了）。
watch(draft, (value) => {
  if (!editing.value) return
  clearTimeout(checkTimer)
  const sequence = ++checkSequence
  checking.value = true
  checkTimer = setTimeout(async () => {
    try {
      const result = await api.check(props.taskId, value, true)
      // 风险预扫描扫的是英文原文，改德语不会让它变，所以照旧带着。
      if (sequence === checkSequence && editing.value) {
        liveMarks.value = buildMarks(result.highlights, detail.value.body_risks)
      }
    } catch { /* 校验挂了不该影响编辑 */ } finally {
      if (sequence === checkSequence) checking.value = false
    }
  }, 250)
})

async function save() {
  saving.value = true
  error.value = ''
  conflict.value = false
  try {
    detail.value = await api.saveLocalization(props.taskId, {
      body_de: draft.value,
      tags: localizationDraft.value.tags,
      hashtags_confirmed: localizationDraft.value.hashtags_confirmed,
      links: localizationDraft.value.links,
      ig_cta: localizationDraft.value.ig_cta,
      source_text_sha256: detail.value.text.source_text_sha256,
      human_revision: detail.value.text.human_revision,
      review_revision: detail.value.review.revision,
      localization_revision: detail.value.localization.revision
    })
    emit('changed', detail.value)
    discard()
    saved.value = true
  } catch (exc) {
    error.value = String(exc.message || exc)
    conflict.value = exc.status === 409
  } finally {
    saving.value = false
  }
}

async function refreshContext() {
  try {
    const latest = await api.getTask(props.taskId)
    const previous = localizationDraft.value
    const sourceChanged = latest.text.source_text_sha256 !== detail.value.text.source_text_sha256
    detail.value = latest
    localizationDraft.value = {
      ...JSON.parse(JSON.stringify(latest.localization)),
      tags: [...latest.localization.protected_tags,
        ...previous.tags.filter(tag => !previous.protected_tags.includes(tag))],
      hashtags_confirmed: sourceChanged ? false : previous.hashtags_confirmed,
      ig_cta: previous.ig_cta,
      links: latest.localization.links.map(link => {
        const kept = previous.links.find(item => item.source_url === link.source_url)
        return kept ? { ...link, target_url: kept.target_url, confirmed: sourceChanged ? false : kept.confirmed } : link
      })
    }
    emit('changed', detail.value)
    error.value = ''
    conflict.value = false
    const result = await api.check(props.taskId, draft.value, true)
    liveMarks.value = buildMarks(result.highlights, detail.value.body_risks)
  } catch (exc) {
    error.value = String(exc.message || exc)
  }
}

function mayLeave() {
  return !hasUnsavedChanges() ||
    window.confirm('修改尚未保存，确定离开并放弃当前草稿？')
}
defineExpose({ mayLeave })
function goBack() {
  if (mayLeave()) emit('back')
}

function beforeUnload(event) {
  if (!hasUnsavedChanges()) return
  event.preventDefault()
  event.returnValue = ''
}

function onKey(event) {
  if (document.querySelector('[role="dialog"]')) return
  if (editing.value || event.target.matches('input, textarea')) return
  if (event.key === 'n' || event.key === 'ArrowDown') { event.preventDefault(); jump(1) }
  if (event.key === 'p' || event.key === 'ArrowUp') { event.preventDefault(); jump(-1) }
  if (event.key === 'Escape') goBack()
}

onMounted(() => {
  load()
  document.addEventListener('keydown', onKey)
  window.addEventListener('beforeunload', beforeUnload)
})
onUnmounted(() => {
  clearTimeout(checkTimer)
  checkSequence += 1
  document.removeEventListener('keydown', onKey)
  window.removeEventListener('beforeunload', beforeUnload)
})
watch(() => props.taskId, load)
</script>

<template>
  <div class="detail">
    <p v-if="loading" class="loading">正在读这一篇…</p>
    <p v-if="error" class="banner-error" role="alert">
      {{ error }}
      <button v-if="conflict && editing" class="btn btn-sm" @click="refreshContext">
        载入最新内容并保留草稿
      </button>
      <button v-else-if="!detail" class="btn btn-sm" @click="load">重试</button>
    </p>
    <p v-if="candidateAdopted && editing" class="save-success" role="status">候选已载入正文编辑区，确认后请保存。</p>
    <p v-if="saved" class="save-success" role="status">本篇文案与本地化选择已保存</p>

    <template v-if="detail && !loading">
      <!-- ---------- 吸顶动作条 ---------- -->
      <header class="bar">
        <button class="btn btn-ghost btn-sm" :disabled="saving" @click="goBack">
          <Icon name="arrowLeft" :size="14" /> 返回列表
        </button>

        <span class="who">
          {{ PLATFORM_LABEL[detail.platform] }}
          <span class="tag tag-neutral">{{ STATUS_LABEL[detail.status] }}</span>
          <span v-if="detail.text.stale" class="tag tag-error">
            <Icon name="alert" :size="12" /> 原文已变更，请复核
          </span>
          <span v-else-if="detail.text.de_machine && detail.text.machine_current === false && !detail.text.de_human" class="tag tag-risk"
                title="旧机器译文仍可查阅；请重新翻译或保存已人工复核的文案，系统不会自动付费重做历史内容。">旧提示词译文，待复核</span>
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
            <button v-if="canEdit" class="btn btn-sm" @click="startEdit">
              <Icon name="pencil" :size="13" /> 编辑德语
            </button>
            <ReviewActions v-if="!detail.read_only" :detail="detail" @changed="applyDetail" />
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
        还有 {{ unseenImages }} 张图没查看。
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
      <p v-if="detail.read_only" role="status">这是冻结账号的历史归档，可查阅来源和处理记录。</p>
      <InitialTranslationPanel v-else :detail="detail" :editing="editing" @changed="applyDetail" />
      <TextCompare
        :en="detail.localization.source_body"
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

      <div class="localization-blocks">
        <HashtagEditor :detail="detail" :draft="shownLocalization" :editing="editing" @update="changeLocalization" />
        <LinkEditor :draft="shownLocalization" :editing="editing" @update="changeLocalization" />
      </div>
      <p :class="['caption-counter', { near: detail.platform === 'instagram' && captionLength >= 1980 }]">
        发布文案 {{ captionLength }}{{ detail.platform === 'instagram' ? ' / 2,200' : '' }} 字符（含标签与链接或引导话术）
      </p>
      <p v-if="!editing && detail.localization_validation.issues.length" class="localization-pending">
        {{ detail.localization_validation.issues.map(item => item.message).join('；') }}
      </p>
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
        <MetaPanel :detail="detail" :editable="!editing && !detail.read_only" @changed="applyDetail" />
      </div>
      <ApprovalPanel v-if="!detail.read_only" :detail="detail" :editing="editing" @changed="applyDetail" />
      <RefinementPanel v-if="!detail.read_only" :detail="detail" :editing="editing" @candidate="adoptCandidate" @changed="applyDetail" />
    </template>
  </div>
</template>

<style scoped>
.detail { padding: 0 var(--space-5) var(--space-6); }
.loading { padding: var(--space-6); color: var(--muted-fg); }
.banner-error { padding: var(--space-4); color: var(--error); }
.banner-error .btn { margin-left: var(--space-2); }
.save-success { padding: var(--space-2) 0; color: var(--ok); font-size: 13px; }

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
.acts { display: flex; flex-wrap: wrap; gap: var(--space-2); }

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
.localization-blocks { display: grid; grid-template-columns: 1fr 1fr; gap: var(--space-4); margin-top: var(--space-4); }
.caption-counter { text-align: right; margin: 9px 0; font-size: 12px; color: var(--muted-fg); }
.caption-counter.near, .localization-pending { color: #92400e; background: var(--risk-soft); border-radius: var(--radius); padding: 8px; }
.localization-pending { font-size: 12px; }
@media (max-width: 1100px) { .localization-blocks { grid-template-columns: 1fr; } }
</style>
