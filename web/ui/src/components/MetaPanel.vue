<script setup>
import { computed } from 'vue'
import {
  AUTHOR_KIND_LABEL, formatDate, formatSchedule, formatTrailTime,
  PLATFORM_LABEL
} from '../format.js'
import Icon from './Icon.vue'

const props = defineProps({ detail: { type: Object, required: true } })

const meta = computed(() => props.detail.meta || {})
const when = computed(
  () => formatSchedule(props.detail.schedule && props.detail.schedule.at))

const ACTION_LABEL = {
  approved: '通过',
  skipped: '标记为不发',
  text_edited: '修改了德语译文'
}
</script>

<template>
  <section class="meta">
    <h3>元信息</h3>

    <dl>
      <div>
        <dt><Icon name="calendar" :size="13" /> {{ detail.status === 'scheduled' ? '排期时刻' : '建议时刻' }}</dt>
        <dd :class="{ muted: !when }">{{ when || '暂无建议时刻' }}</dd>
      </div>
      <div>
        <dt><Icon name="image" :size="13" /> 发布渠道</dt>
        <dd>
          {{ PLATFORM_LABEL[(detail.schedule && detail.schedule.channel) || detail.platform] }}
        </dd>
      </div>
      <div>
        <dt><Icon name="user" :size="13" /> 作者</dt>
        <dd>
          {{ AUTHOR_KIND_LABEL[meta.author_kind] || meta.author_kind }}
          <span v-if="meta.owner" class="sub">@{{ meta.owner }}</span>
        </dd>
      </div>
      <div v-if="meta.coauthors && meta.coauthors.length">
        <dt>合作方</dt>
        <dd class="sub">{{ meta.coauthors.map((c) => '@' + c).join('、') }}</dd>
      </div>
      <div>
        <dt><Icon name="clock" :size="13" /> 原帖发布</dt>
        <dd class="sub">{{ formatDate(meta.created_at) }}</dd>
      </div>
      <div v-if="meta.permalink">
        <dt><Icon name="link" :size="13" /> 原帖</dt>
        <dd>
          <a :href="meta.permalink" target="_blank" rel="noopener noreferrer">
            在 {{ PLATFORM_LABEL[detail.platform] }} 上打开
          </a>
        </dd>
      </div>
    </dl>

    <!-- 组装告警：主要是"缺德语图，已回退原图"。
         能降级的就降级，但必须让人看见。 -->
    <div v-if="meta.compose_warnings && meta.compose_warnings.length" class="warnings">
      <h4><Icon name="alert" :size="13" /> 组装时的告警</h4>
      <ul>
        <li v-for="(line, i) in meta.compose_warnings" :key="i">{{ line }}</li>
      </ul>
    </div>

    <!-- actor 留痕。**只在详情页显示**（§10）：列表上显示会诱发互相盯梢，
         几个人的小团队里是负面效果。 -->
    <div class="trail">
      <h4>最近保存</h4>
      <ol v-if="detail.trail && detail.trail.length">
        <li v-for="(row, i) in detail.trail" :key="i">
          <span class="at">{{ formatTrailTime(row.at) }}</span>
          <span class="what">{{ ACTION_LABEL[row.action] || row.action }}</span>
          <span v-if="row.note" class="note">「{{ row.note }}」</span>
        </li>
      </ol>
      <p v-else class="empty">尚未保存人工文案。</p>
    </div>
  </section>
</template>

<style scoped>
.meta {
  padding: var(--space-4);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  background: var(--card);
}
h3 {
  margin: 0 0 var(--space-3);
  font-size: 12px; font-weight: 650; color: var(--muted-fg);
}
h4 {
  display: flex; align-items: center; gap: 6px;
  margin: 0 0 var(--space-2);
  font-size: 12px; font-weight: 650;
}

dl { margin: 0 0 var(--space-4); display: grid; gap: var(--space-2); }
dl > div {
  display: flex; align-items: baseline; justify-content: space-between;
  gap: var(--space-4);
  padding-bottom: var(--space-2);
  border-bottom: 1px solid var(--border);
}
dt {
  display: flex; align-items: center; gap: 6px;
  flex: none;
  font-size: 12px; color: var(--muted-fg);
}
dd { margin: 0; font-size: 13px; font-weight: 600; text-align: right; }
dd.muted, .sub { font-weight: 400; color: var(--muted-fg); }

.warnings {
  margin-bottom: var(--space-4);
  padding: var(--space-3);
  border-radius: var(--radius);
  background: var(--error-soft);
  color: #7f1d1d;
}
.warnings ul { margin: 0; padding-left: 18px; font-size: 12px; }
.warnings li + li { margin-top: 4px; }

.trail ol { list-style: none; margin: 0; padding: 0; font-size: 12px; }
.trail li {
  display: flex; flex-wrap: wrap; gap: 6px;
  padding: 5px 0;
  border-bottom: 1px dashed var(--border);
}
.trail .at { color: var(--muted-fg); font-variant-numeric: tabular-nums; }
.trail .who { font-weight: 600; }
.trail .note { color: var(--muted-fg); flex-basis: 100%; }
.trail .empty { margin: 0; font-size: 12px; color: var(--muted-fg); }
</style>
