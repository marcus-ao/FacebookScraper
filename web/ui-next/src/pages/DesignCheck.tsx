import { Divider, Space, Table, Typography } from 'antd'

import { PageTitle } from '@/app/PageTitle'

import { ConflictRecovery } from '@/components/ConflictRecovery'
import { PaidActionButton } from '@/components/PaidActionButton'
import { PlatformLabel } from '@/components/PlatformLabel'
import { ProblemIndicator } from '@/components/ProblemIndicator'
import { StatusTag } from '@/components/StatusTag'
import { BerlinTime, ShanghaiTime } from '@/components/Time'
import {
  createPostColumns,
  postRowClassName,
  POST_ROW_HEIGHT,
} from '@/features/post-list/columns'
import type { PostRowBase } from '@/features/post-list/columns'
import type { DisplayStatus } from '@/types/domain'
import type { TaskId } from '@/types/brands'

/**
 * 共享基元的核验台。**内部页，生产导航里没有入口。**
 *
 * 它不是"组件展示页"这种给人看的东西 —— 运营永远不该看到它。
 * 它只解决一个问题：B2 抽出来的七个基元，在真实浏览器里长什么样、
 * 在 1366 与 1920 两个分辨率下会不会折行、Tooltip 弹不弹得出来。
 * 这些事 SSR 的字符串断言验不了（弹层走 portal），得在浏览器里看。
 *
 * 路由 `/_internal/design-check` 上挂了 `internal: true`，
 * 外壳会在页面顶部挂一条"这不是业务界面"的说明。
 * Stage C 起如果不再需要，删掉这个文件和那一条路由即可。
 */

const { Paragraph, Text } = Typography

const ALL_STATUSES: readonly DisplayStatus[] = [
  'not_ready',
  'pending_review',
  'edited',
  'snoozed',
  'approved',
  'scheduled',
  'skipped',
  'handed_off',
]

/** 核验用的假行。**不请求任何接口**，也不指向真实归档。 */
interface CheckRow extends PostRowBase {
  readonly hard_alerts: readonly { code: string; label: string }[]
  readonly risk_count: number
  readonly author_flag: string | null
  readonly at: string | null
}

const ROWS: readonly CheckRow[] = [
  {
    id: 'fa_example/1001' as TaskId,
    platform: 'facebook',
    thumbnail_url: '',
    text_de_excerpt:
      'Der neue Katzenklo-Roboter räumt hinter deiner Katze auf, damit du es nicht tun musst.',
    image_count: 3,
    tags: ['自动猫砂盆', '新品'],
    status: 'pending_review',
    hard_alerts: [{ code: 'unknown_collaborator', label: '第三方作者 @partner 不在白名单' }],
    risk_count: 2,
    author_flag: null,
    at: '2026-09-14T17:00:00+02:00',
  },
  {
    id: 'in_example.tech/1002' as TaskId,
    platform: 'instagram',
    thumbnail_url: '',
    text_de_excerpt: '',
    image_count: 1,
    tags: [],
    status: 'not_ready',
    hard_alerts: [],
    risk_count: 3,
    author_flag: null,
    at: null,
  },
  {
    id: 'fa_example/1003' as TaskId,
    platform: 'facebook',
    thumbnail_url: '',
    text_de_excerpt: 'Winteraktion: bis Sonntag versandkostenfrei in ganz Deutschland.',
    image_count: 1,
    tags: ['促销', '冬季', '配件'],
    status: 'scheduled',
    hard_alerts: [],
    risk_count: 0,
    author_flag: null,
    at: '2026-10-25T02:30:00+02:00',
  },
  {
    id: 'fa_example/1004' as TaskId,
    platform: 'facebook',
    thumbnail_url: '',
    text_de_excerpt: 'Diese Kampagne läuft nur in den USA.',
    image_count: 2,
    tags: ['美国限定'],
    status: 'skipped',
    hard_alerts: [],
    risk_count: 0,
    author_flag: '第三方作者 @creator',
    at: null,
  },
]

const columns = createPostColumns<CheckRow>({
  columns: ['thumbnail', 'problem', 'summary', 'status', 'time', 'platform', 'tags'],
  problem: (row) => row,
  time: { title: '排期时刻', zone: 'berlin', at: (row) => row.at },
})

export function DesignCheck() {
  return (
    <div>
      <PageTitle />
      <Section title="StatusTag · 八个展示态">
        <Note>
            scheduled 是「已排期」不是「已发布」；skipped 是中性不是红色；每一个都带文字，不只靠颜色。
        </Note>
        <Space wrap size={[8, 8]}>
          {ALL_STATUSES.map((status) => (
            <StatusTag key={status} status={status} />
          ))}
        </Space>
      </Section>

      <Section title="PlatformLabel">
        <Note>图标走 currentColor，不用品牌色 —— 内容区只有红黄两种饱和色。</Note>
        <Space size={16}>
          <PlatformLabel platform="facebook" />
          <PlatformLabel platform="instagram" />
          <PlatformLabel platform="facebook" iconOnly />
          <PlatformLabel platform="instagram" iconOnly />
        </Space>
      </Section>

      <Section title="BerlinTime / ShanghaiTime">
        <Note>
            柏林时刻按字符串自带的偏移读墙上时刻，不经过浏览器本地时区。下面两条正好跨夏令时切换（3/29 与 10/25），显示的小时数应当只跟字符串走。
        </Note>
        <Space direction="vertical" size={4}>
          <div>
            夏令时 <BerlinTime at="2026-03-29T03:30:00+02:00" />
          </div>
          <div>
            冬令时 <BerlinTime at="2026-10-25T02:30:00+01:00" />
          </div>
          <div>
            没有排期 <BerlinTime at={null} />
          </div>
          <div>
            操作记录 <ShanghaiTime at="2026-09-13T07:51:04Z" />
          </div>
          <div>
            不带时区词 <ShanghaiTime at="2026-09-13T07:51:04Z" showZone={false} />
          </div>
        </Space>
      </Section>

      <Section title="ProblemIndicator · 硬闸 > 风险 > 第三方 > 无">
        <Note>列表里红色只出现在这一列。完整文案进 Tooltip，鼠标悬停看。</Note>
        <Space size={16}>
          <ProblemIndicator
            source={{
              hard_alerts: [{ code: 'material_gate', label: '素材不齐（正文/图片/轮播完整性未通过）' }],
              risk_count: 5,
              author_flag: '第三方作者 @creator',
            }}
          />
          <ProblemIndicator source={{ hard_alerts: [], risk_count: 2, author_flag: '第三方作者 @creator' }} />
          <ProblemIndicator source={{ hard_alerts: [], risk_count: 0, author_flag: '第三方作者 @creator' }} />
          <ProblemIndicator source={{ hard_alerts: [], risk_count: 0, author_flag: null }} />
        </Space>
      </Section>

      <Section title="ConflictRecovery · 四种冲突">
        <Note>
          一个工程词都不许出现 —— 下面四条里找不到状态码、版本号、比较并交换这类说法。
        </Note>
        <Space direction="vertical" size={8} style={{ display: 'flex' }}>
          <ConflictRecovery kind="draft" onRecover={noop} />
          <ConflictRecovery kind="tags" onRecover={noop} />
          <ConflictRecovery kind="settings" onRecover={noop} />
          <ConflictRecovery kind="schedule" onRecover={noop} recovering />
        </Space>
      </Section>

      <Section title="PaidActionButton">
        <Note>不是 primary；金额写在按钮上；灰着的一定说得出为什么（悬停看）。</Note>
        <Space wrap size={16}>
          <PaidActionButton label="初翻" amount="US$0.020" remaining={12} onClick={noop} />
          <PaidActionButton label="文案优化" amount="US$0.012" onClick={noop} />
          <PaidActionButton label="图片优化" amount="US$0.045" remaining={3} loading />
          <PaidActionButton
            label="初翻"
            amount="US$0.020"
            remaining={0}
            disabledReason="这一篇的第三方作者还没授权，先在上面完成授权再初翻。"
          />
        </Space>
      </Section>

      <Section title="PostRow 列工厂">
        <Note>
            没有勾选列、没有整表多选、没有「译文来源」四态列。第二行「还没有德语译文」来自摘要字段是空串，不是从状态猜的。最后一行是终态，整行降饱和。
        </Note>
        <Table<CheckRow>
          size="small"
          sticky
          rowKey="id"
          columns={columns}
          dataSource={[...ROWS]}
          pagination={false}
          rowClassName={postRowClassName}
          style={{ ['--rc-row-h' as string]: `${POST_ROW_HEIGHT}px` }}
        />
      </Section>
    </div>
  )
}

function noop() {}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section>
      {/* 用原生 h2：全局排版已经把它定成区段标题 14 / 600。 */}
      <h2 style={{ marginBottom: 'var(--rc-space-2)' }}>{title}</h2>
      {children}
      <Divider />
    </section>
  )
}

function Note({ children }: { children: React.ReactNode }) {
  return (
    <Paragraph style={{ fontSize: 'var(--rc-font-secondary)' }}>
      <Text type="secondary">{children}</Text>
    </Paragraph>
  )
}
