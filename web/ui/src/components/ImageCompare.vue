<script setup>
import { computed, ref, watch } from 'vue'
import { api } from '../api.js'
import Icon from './Icon.vue'

const props = defineProps({
  taskId: { type: String, required: true },
  images: { type: Array, default: () => [] }
})
// 详情页顶部的动作条要显示"还有 N 张没查看"——那条提示必须**在按通过之前**
// 就在她眼皮底下，而不是按下去之后才弹（§11.2：提示不拦）。
const emit = defineEmits(['progress'])

const current = ref(0)
// 看过哪几张。**这就是"我还有几张没看"能被看见的全部机制**（§11.2）：
// 纵向堆叠很容易漏掉中间某张，而漏看一张德语图正是要防的事。
const seen = ref(new Set([0]))
const zoom = ref(false)

watch(current, (index) => { seen.value = new Set([...seen.value, index]) })
watch(() => props.taskId, () => { current.value = 0; seen.value = new Set([0]) })

const image = computed(() => props.images[current.value] || null)
const unseen = computed(() => props.images.length - seen.value.size)

watch(unseen, (value) => emit('progress', value), { immediate: true })
const metrics = computed(() => (image.value && image.value.metrics) || null)

function step(delta) {
  const next = current.value + delta
  if (next >= 0 && next < props.images.length) current.value = next
}

const fmt = (value, digits = 3) =>
  (typeof value === 'number' ? value.toFixed(digits).replace(/\.?0+$/, '') : '—')
</script>

<template>
  <section :class="['images', { zoom }]">
    <header class="bar">
      <h3>图片对比</h3>
      <p class="progress">
        第 {{ current + 1 }} / {{ images.length }} 张
        <span v-if="unseen > 0" class="tag tag-neutral">还有 {{ unseen }} 张没看</span>
        <span v-else class="tag tag-ok"><Icon name="check" :size="12" /> 都看过了</span>
      </p>
      <div class="nav">
        <button class="btn btn-sm" :disabled="current === 0" @click="step(-1)">
          <Icon name="arrowLeft" :size="13" /> 上一张
        </button>
        <button
          class="btn btn-sm" :disabled="current >= images.length - 1"
          @click="step(1)"
        >
          下一张 <Icon name="arrowRight" :size="13" />
        </button>
        <button class="btn btn-sm" :aria-pressed="zoom" @click="zoom = !zoom">
          {{ zoom ? '还原' : '放大' }}
        </button>
      </div>
    </header>

    <!-- 左右并排：适合逐字核对型号、优惠码、展位号（§11.2）。 -->
    <div v-if="image" class="stage">
      <figure>
        <figcaption>原图（英文）</figcaption>
        <img :src="api.imageUrl(taskId, image.index, 'original')" :alt="`原图 ${current + 1}`" />
      </figure>
      <figure>
        <figcaption>
          德语图
          <span v-if="!image.de_present" class="tag tag-error">
            <Icon name="alert" :size="12" /> 缺德语图，显示的是原图
          </span>
        </figcaption>
        <img :src="api.imageUrl(taskId, image.index, 'de')" :alt="`德语图 ${current + 1}`" />
      </figure>
    </div>

    <!-- 四项自动指标。她关不关心这些数，是这个原型要问出来的问题之一（§13）。 -->
    <dl v-if="metrics" class="metrics">
      <div><dt>dHash 距离</dt><dd>{{ metrics.dhash_distance ?? '—' }}</dd></div>
      <div><dt>宽高比形变</dt><dd>{{ fmt(metrics.aspect_drift, 4) }} %</dd></div>
      <div><dt>缩放</dt><dd>{{ fmt(metrics.scale_ratio, 4) }}×</dd></div>
      <div><dt>生成耗时</dt><dd>{{ fmt(metrics.elapsed_s, 1) }} 秒</dd></div>
    </dl>
    <p v-else class="metrics-empty">
      <Icon name="info" :size="13" />
      这张没有程序生成记录（没跑过德语图，或当前用的是人工放置的图）。
    </p>

    <!-- 缩略图条：看过的打勾，让"全部核对完"这件事可见。 -->
    <ol v-if="images.length > 1" class="strip">
      <li v-for="img in images" :key="img.index">
        <button
          :class="['thumb', { on: img.index === current, seen: seen.has(img.index) }]"
          :aria-current="img.index === current"
          :aria-label="`第 ${img.index + 1} 张${seen.has(img.index) ? '（看过）' : ''}`"
          @click="current = img.index"
        >
          <img :src="api.imageUrl(taskId, img.index, 'de')" alt="" loading="lazy" />
          <span class="badge">{{ img.index + 1 }}</span>
          <span v-if="seen.has(img.index)" class="seen-mark"><Icon name="check" :size="11" /></span>
          <span v-if="!img.de_present" class="miss" title="缺德语图，已回退原图"></span>
        </button>
      </li>
    </ol>
  </section>
</template>

<style scoped>
.images {
  border: 1px solid var(--border);
  border-radius: var(--radius);
  background: var(--card);
  overflow: hidden;
}

.bar {
  display: flex; align-items: center; gap: var(--space-3);
  padding: var(--space-2) var(--space-3);
  border-bottom: 1px solid var(--border);
  background: var(--muted);
}
.bar h3 { margin: 0; font-size: 12px; font-weight: 650; color: var(--muted-fg); }
.progress {
  display: flex; align-items: center; gap: var(--space-2);
  margin: 0; font-size: 12px; color: var(--muted-fg);
}
.nav { margin-left: auto; display: flex; gap: var(--space-2); }

.stage {
  display: grid; grid-template-columns: 1fr 1fr; gap: var(--space-4);
  padding: var(--space-4);
  background: #0f172a08;
}
figure { margin: 0; display: flex; flex-direction: column; gap: 6px; min-width: 0; }
figcaption {
  display: flex; align-items: center; gap: var(--space-2);
  font-size: 12px; font-weight: 600; color: var(--muted-fg);
}
figure img {
  width: 100%;
  height: 46vh;
  object-fit: contain;
  background: var(--muted);
  border: 1px solid var(--border);
  border-radius: var(--radius);
}
.images.zoom figure img { height: calc(100vh - 200px); }

.metrics {
  display: grid; grid-template-columns: repeat(4, 1fr);
  gap: var(--space-3);
  margin: 0;
  padding: var(--space-3) var(--space-4);
  border-top: 1px solid var(--border);
}
.metrics dt { font-size: 11px; color: var(--muted-fg); }
.metrics dd { margin: 0; font-size: 14px; font-weight: 600; font-variant-numeric: tabular-nums; }
.metrics-empty {
  display: flex; align-items: center; gap: 6px;
  margin: 0; padding: var(--space-3) var(--space-4);
  border-top: 1px solid var(--border);
  font-size: 12px; color: var(--muted-fg);
}

.strip {
  list-style: none;
  display: flex; flex-wrap: wrap; gap: var(--space-2);
  margin: 0;
  padding: var(--space-3);
  border-top: 1px solid var(--border);
  background: var(--muted);
}
.thumb {
  position: relative;
  width: 60px; height: 60px;
  padding: 0;
  border: 2px solid var(--border-strong);
  border-radius: var(--radius);
  background: var(--card);
  overflow: hidden;
  transition: border-color var(--ease), transform var(--ease);
}
.thumb:hover { transform: translateY(-1px); }
.thumb.on { border-color: var(--primary); }
.thumb img { width: 100%; height: 100%; object-fit: cover; display: block; }
.badge {
  position: absolute; left: 2px; top: 2px;
  padding: 0 4px;
  border-radius: 3px;
  background: rgba(15, 23, 42, .7);
  color: #fff; font-size: 10px; font-weight: 700;
}
.seen-mark {
  position: absolute; right: 2px; bottom: 2px;
  display: grid; place-items: center;
  width: 15px; height: 15px;
  border-radius: 50%;
  background: var(--ok);
  color: #fff;
}
/* 没有德语图的那几张在缩略图条上就能看出来，不用逐张点过去才发现。 */
.miss {
  position: absolute; inset: auto 0 0 0;
  height: 3px;
  background: var(--error);
}
</style>
