<script setup>
import { computed, nextTick, ref, watch } from 'vue'
import { charLength, segment } from '../marks.js'
import Icon from './Icon.vue'

const props = defineProps({
  en: { type: String, default: '' },
  de: { type: String, default: '' },
  marks: { type: Array, default: () => [] },
  activeIndex: { type: Number, default: -1 },
  editing: { type: Boolean, default: false },
  draft: { type: String, default: '' },
  // 编辑态下由 /check 实时算出来的标记，和只读态用同一套画法
  liveMarks: { type: Array, default: () => [] },
  checking: { type: Boolean, default: false },
  isHuman: { type: Boolean, default: false }
})
const emit = defineEmits(['update:draft', 'select'])

const enPane = ref(null)
const dePane = ref(null)
const editor = ref(null)
const mirror = ref(null)

const shownMarks = computed(() => (props.editing ? props.liveMarks : props.marks))
const deText = computed(() => (props.editing ? props.draft : props.de))

const enParts = computed(() => segment(props.en, props.marks, 'en'))
const deParts = computed(() => segment(deText.value, shownMarks.value, 'de'))

const cls = (type) => (type ? `mk mk-${type}` : '')

// 两栏**不做滚动同步**（§11.1）：德语更长，同步会逐渐错位。
// 但点跳转时两边一起定位到对应位置——那是有明确目标的一次定位，不会错位。
// ⚠️ **不要用 behavior: 'smooth'。** 实测在内嵌 Chromium 里它静默不动——
// 一次都不滚，也不报错。而「下一处 →」是这一页唯一不能失灵的东西：
// 它失灵时界面看起来完全正常（标记确实高亮了），只是**跳不过去**，
// 而人会以为自己已经看完了。直接定位既可靠，也免了等动画才能读。
function scrollTo(pane, index) {
  if (!pane) return
  const node = pane.querySelector(`[data-mark="${index}"]`)
  if (!node) return
  node.scrollIntoView({ block: 'center' })
}

watch(() => props.activeIndex, async (index) => {
  if (index < 0) return
  await nextTick()
  scrollTo(enPane.value, index)
  scrollTo(dePane.value, index)
})

// 编辑态：透明 textarea 压在等排版的镜像层上，于是**打字的同时就能看到标红**。
// 两层的字体、行高、内边距、换行规则必须完全一致，否则字会错位。
function syncScroll() {
  if (editor.value && mirror.value) mirror.value.scrollTop = editor.value.scrollTop
}
</script>

<template>
  <div class="compare">
    <!-- ---------------- 英文原文 ---------------- -->
    <section class="pane">
      <header>
        <h3>英文原文</h3>
        <span class="len">{{ charLength(en) }} 字符</span>
      </header>
      <div ref="enPane" class="body">
        <p class="text">
          <template v-for="(part, i) in enParts" :key="i">
            <mark
              v-if="part.type"
              :class="[cls(part.type), { 'mk-active': part.index === activeIndex }]"
              :data-mark="part.index"
              @click="emit('select', part.index)"
            >{{ part.text }}</mark>
            <template v-else>{{ part.text }}</template>
          </template>
        </p>
      </div>
    </section>

    <!-- ---------------- 德语译文 ---------------- -->
    <section class="pane">
      <header>
        <h3>
          德语译文
          <span v-if="isHuman" class="tag tag-ok">人工版</span>
        </h3>
        <span class="len">
          <span v-if="checking" class="checking">校验中…</span>
          {{ charLength(deText) }} 字符
        </span>
      </header>

      <!-- 只读态 -->
      <div v-if="!editing" ref="dePane" class="body">
        <p v-if="deText" class="text">
          <template v-for="(part, i) in deParts" :key="i">
            <mark
              v-if="part.type"
              :class="[cls(part.type), { 'mk-active': part.index === activeIndex }]"
              :data-mark="part.index"
              @click="emit('select', part.index)"
            >{{ part.text }}</mark>
            <template v-else>{{ part.text }}</template>
          </template>
        </p>
        <p v-else class="empty">这篇还没有德语译文。</p>
      </div>

      <!-- 编辑态：镜像层画标记，textarea 透明压在上面 -->
      <div v-else class="body editing">
        <div ref="mirror" class="text mirror" aria-hidden="true"><template
          v-for="(part, i) in deParts" :key="i"
        ><mark
            v-if="part.type" :class="cls(part.type)"
          >{{ part.text }}</mark><template v-else>{{ part.text }}</template></template><br /></div>
        <textarea
          ref="editor" class="text editor" spellcheck="false"
          aria-label="德语译文"
          :value="draft"
          @input="emit('update:draft', $event.target.value)"
          @scroll="syncScroll"
        ></textarea>
      </div>
    </section>
  </div>
</template>

<style scoped>
.compare {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: var(--space-4);
  min-height: 0;
}

.pane {
  display: flex; flex-direction: column;
  min-width: 0; min-height: 0;
  border: 1px solid var(--border);
  border-radius: var(--radius);
  background: var(--card);
  overflow: hidden;
}
.pane header {
  display: flex; align-items: center; justify-content: space-between;
  gap: var(--space-2);
  padding: var(--space-2) var(--space-3);
  border-bottom: 1px solid var(--border);
  background: var(--muted);
}
.pane h3 {
  display: flex; align-items: center; gap: var(--space-2);
  margin: 0; font-size: 12px; font-weight: 650;
  letter-spacing: .02em; color: var(--muted-fg);
}
.len { display: flex; align-items: center; gap: var(--space-2); font-size: 11px; color: var(--muted-fg); }
.checking { color: var(--primary); font-weight: 600; }

/* 中位 843 字符、最长 2838，德语再长 10~20% —— 两栏各约一屏。
   给一个高度上限让两侧**各自**滚动，页面本身不被撑成一条长卷
   （§11.2 说过"一屏又一屏"是要避免的形状，正文这边同理）。
   ⚠️ 这里不能写 `flex: 1`：flex-basis 会盖掉高度，两栏就又长回内容高度了。
   用 max-height 而不是 height，短帖（最短 119 字符）才不会拖一大片空白。 */
.body {
  max-height: 52vh;
  min-height: 160px;
  overflow: auto;
  padding: var(--space-4);
  scroll-padding-block: 96px;
}

/* 两层必须逐像素同排版，否则编辑态的标记会和字错位 */
.text {
  margin: 0;
  font-family: var(--font-sans);
  font-size: 15px;
  line-height: 1.7;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  letter-spacing: 0;
}
.empty { margin: 0; color: var(--muted-fg); font-style: italic; }

mark.mk { cursor: pointer; }

/* 镜像层与 textarea 都是绝对定位 → 这个盒子没有内在高度，只写 max-height
   的话它会塌到 min-height，编辑区只剩三行。所以编辑态给一个确定高度，
   和只读态那一栏对齐。 */
.editing {
  position: relative;
  height: 52vh;
  min-height: 320px;
  padding: 0;
  overflow: hidden;
}
.editing .mirror,
.editing .editor {
  position: absolute; inset: 0;
  padding: var(--space-4);
  border: 0;
  overflow: auto;
}
.editing .mirror { pointer-events: none; }
.editing .editor {
  background: transparent;
  color: var(--fg);
  caret-color: var(--primary);
  resize: none;
}
/* 镜像层只负责画底色，字由 textarea 出——两份字叠在一起会发糊。 */
.editing .mirror { color: transparent; }
.editing .editor:focus-visible { outline: 2px solid var(--primary); outline-offset: -2px; }
</style>
