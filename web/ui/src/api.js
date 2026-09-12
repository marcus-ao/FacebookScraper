// 后端接口。基础契约见 web/DESIGN.md；人工保存同时携带源文和人工稿版本。
//
// ⚠️ 任务 id 形如 `in_neakasa.tech/3965025107383038890`，**自带一个斜杠**。
// 拼 URL 时不能 encodeURIComponent 整个 id（那会把斜杠变成 %2F，
// 后端的 {task_id:path} 就匹配不上了），只逐段编码。

function idPath(taskId) {
  return String(taskId).split('/').map(encodeURIComponent).join('/')
}

async function request(url, options) {
  const response = await fetch(url, options)
  if (!response.ok) {
    let detail = `HTTP ${response.status}`
    try {
      const body = await response.json()
      if (body && body.detail) detail = body.detail
    } catch { /* 响应不是 JSON，就用状态码 */ }
    const error = new Error(detail)
    error.status = response.status
    throw error
  }
  return response.json()
}

const json = (body) => ({
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body)
})

export const api = {
  listTasks: () => request('/api/tasks'),

  getTask: (taskId) => request(`/api/tasks/${idPath(taskId)}`),

  imageUrl: (taskId, index, variant) =>
    `/api/tasks/${idPath(taskId)}/image/${index}?variant=${variant}`,

  approve: (taskId, actor) =>
    request(`/api/tasks/${idPath(taskId)}/approve`, json({ actor })),

  skip: (taskId, actor, reason) =>
    request(`/api/tasks/${idPath(taskId)}/skip`, json({ actor, reason })),

  saveTextDe: (taskId, textDe, { sourceTextSha256, humanRevision }) =>
    request(`/api/tasks/${idPath(taskId)}/text_de`, {
      ...json({ text_de: textDe, source_text_sha256: sourceTextSha256,
        human_revision: humanRevision }),
      method: 'PUT'
    }),

  // 编辑时的实时校验。**只算不写**，也永远不会拒绝保存（第 11.4 节）。
  check: (taskId, textDe) =>
    request(`/api/tasks/${idPath(taskId)}/check`, json({ text_de: textDe }))
}
