// 后端接口。基础契约见 web/DESIGN.md；人工保存同时携带源文和人工稿版本。
//
// ⚠️ 任务 id 形如 `in_neakasa.tech/3965025107383038890`，**自带一个斜杠**。
// 拼 URL 时不能 encodeURIComponent 整个 id（那会把斜杠变成 %2F，
// 后端的 {task_id:path} 就匹配不上了），只逐段编码。

function idPath(taskId) {
  return String(taskId).split('/').map(encodeURIComponent).join('/')
}

async function fetchResponse(url, options) {
  const response = await fetch(url, options)
  if (!response.ok) {
    let detail = `HTTP ${response.status}`
    let payload = null
    try {
      const body = await response.json()
      payload = body
      if (body && body.detail) detail = body.detail
    } catch { /* 响应不是 JSON，就用状态码 */ }
    const error = new Error(detail)
    error.status = response.status
    error.payload = payload
    throw error
  }
  return response
}

async function request(url, options) {
  return (await fetchResponse(url, options)).json()
}

const json = (body) => ({
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body)
})

export const api = {
  calendar: () => request('/api/calendar'),
  refreshCalendar: () => request('/api/calendar/refresh', json({})),

  listTasks: () => request('/api/tasks'),

  getTask: (taskId) => request(`/api/tasks/${idPath(taskId)}`),

  imageUrl: (taskId, index, variant) =>
    `/api/tasks/${idPath(taskId)}/image/${index}?variant=${variant}`,

  reviewAction: (taskId, body) => request(`/api/tasks/${idPath(taskId)}/review`, json(body)),

  saveTags: (taskId, body) => request(`/api/tasks/${idPath(taskId)}/tags`, {
    ...json(body), method: 'PUT'
  }),

  exportPost: async (taskId, body) => {
    const response = await fetchResponse(`/api/tasks/${idPath(taskId)}/export`, json(body))
    const filename = /filename="([^"]+)"/.exec(response.headers.get('Content-Disposition') || '')?.[1]
    return { blob: await response.blob(), filename: filename || 'post_de.zip' }
  },

  approvalOptions: taskId => request(`/api/tasks/${idPath(taskId)}/approval-options`),
  approve: (taskId, body) => request(`/api/tasks/${idPath(taskId)}/approve`, json(body)),

  refinementCapabilities: taskId => request(`/api/refinements/task/${idPath(taskId)}`),
  initialCapabilities: taskId => request(`/api/initial-translation/task/${idPath(taskId)}`),
  initialTranslate: (taskId, body) => request(`/api/initial-translation/task/${idPath(taskId)}`, json(body)),
  initialJob: jobId => request(`/api/initial-translation/jobs/${encodeURIComponent(jobId)}`),
  refine: (taskId, body) => request(`/api/refinements/task/${idPath(taskId)}`, json(body)),
  refinementJob: jobId => request(`/api/refinements/jobs/${encodeURIComponent(jobId)}`),
  template: kind => request(`/api/templates/${kind}`),
  suggestHashtags: (taskId, body) => request(`/api/hashtags/task/${idPath(taskId)}`, json(body)),

  saveLocalization: (taskId, body) => request(`/api/tasks/${idPath(taskId)}/localization`, { ...json(body), method: 'PUT' }),

  saveTextDe: (taskId, textDe, { sourceTextSha256, humanRevision, reviewRevision }) =>
    request(`/api/tasks/${idPath(taskId)}/text_de`, {
      ...json({ text_de: textDe, source_text_sha256: sourceTextSha256,
        human_revision: humanRevision, review_revision: reviewRevision }),
      method: 'PUT'
    }),

  // 编辑时的实时校验。**只算不写**，也永远不会拒绝保存（第 11.4 节）。
  check: (taskId, textDe, bodyOnly = false) =>
    request(`/api/tasks/${idPath(taskId)}/check`, json({ text_de: textDe, body_only: bodyOnly }))
}
