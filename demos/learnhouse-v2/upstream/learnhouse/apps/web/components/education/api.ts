export type RecordData = Record<string, any>
export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string
  ) {
    super(message)
  }
}
const pendingKeys = new Map<string, string>()
const messages: Record<string, string> = {
  STALE_RECOMMENDATION: '这条推荐已过期，请返回课程查看最新建议。',
  STALE_EVIDENCE: '推荐依据已失效，请返回课程查看学习状态。',
  EXERCISE_UNAVAILABLE: '这项练习当前不可访问，请返回课程查看其他建议。',
  SCOPE_DENIED: '你没有此课程或对象的访问权限。',
  VERSION_CONFLICT: '内容已更新，请刷新后重试。',
  IDEMPOTENCY_CONFLICT: '这次操作的内容与之前请求不同，请刷新后重试。',
  EXECUTION_NOT_AUTHORIZED: '研究执行尚未获得本次任务授权。',
  RESEARCH_RETRY_UNAVAILABLE: '本版尚不支持研究任务恢复。',
  CAPABILITY_UNAVAILABLE: '该能力当前尚未接通。',
  DEMO_OFFLINE: '本地演示尚未连接模型。',
  VALIDATION_ERROR: '提交内容不符合要求，请检查表单。',
  AUTH_REQUIRED: '会话已失效，请重新登录。',
}
export async function api(
  path: string,
  options: {
    method?: string
    body?: unknown
    etag?: string
    form?: boolean
  } = {}
): Promise<any> {
  const method = options.method || 'GET',
    fingerprint = JSON.stringify([method, path, options.body, options.etag])
  const headers: Record<string, string> = {}
  let body: BodyInit | undefined
  if (options.body !== undefined) {
    headers['Content-Type'] = options.form
      ? 'application/x-www-form-urlencoded'
      : 'application/json'
    body = options.form
      ? new URLSearchParams(options.body as Record<string, string>)
      : JSON.stringify(options.body)
  }
  if (method !== 'GET') {
    if (!pendingKeys.has(fingerprint))
      pendingKeys.set(fingerprint, crypto.randomUUID())
    headers['Idempotency-Key'] = pendingKeys.get(fingerprint)!
    if (options.etag) headers['If-Match'] = options.etag
  }
  let response: Response
  try {
    response = await fetch(
      path.startsWith('/api/') ? path : '/api/v1/education' + path,
      { method, headers, body, credentials: 'include', cache: 'no-store' }
    )
  } catch {
    throw new ApiError(
      0,
      'NETWORK_ERROR',
      '暂时无法连接服务，页面内容已保留。请恢复连接后重试。'
    )
  }
  const data = await response.json().catch(() => ({}))
  if (!response.ok) {
    if (response.status < 500) pendingKeys.delete(fingerprint)
    if (response.status === 401 && !path.startsWith('/api/v1/auth/')) {
      window.dispatchEvent(new Event('education-session-expired'))
    }
    const detail = data.detail || data.error || data
    const code =
      typeof detail === 'object'
        ? detail.code || 'REQUEST_FAILED'
        : 'REQUEST_FAILED'
    throw new ApiError(
      response.status,
      code,
      messages[code] ||
        (typeof detail === 'string'
          ? detail
          : typeof detail?.message === 'string'
            ? detail.message
            : response.status === 422
              ? '提交内容不符合要求，请检查表单。'
              : '') ||
        `操作失败（${response.status}）`
    )
  }
  pendingKeys.delete(fingerprint)
  return {
    ...(Array.isArray(data) ? { items: data } : data),
    etag: data.etag || response.headers.get('etag') || undefined,
  }
}
export const items = (data: any): any[] =>
  Array.isArray(data)
    ? data
    : data?.items ||
      data?.courses ||
      data?.bindings ||
      data?.submissions ||
      data?.policies ||
      data?.events ||
      []
export const date = (value: any) =>
  value
    ? new Date(value).toLocaleString('zh-CN', {
        month: 'short',
        day: 'numeric',
        hour: '2-digit',
        minute: '2-digit',
      })
    : '尚未设置'
export const label = (value: any) =>
  (
    ({
      assigned: '进行中',
      paused: '已暂停',
      closed: '已结束',
      archived: '已归档',
      draft: '草稿',
      submitted: '已提交',
      published: '已发布',
      ready: '已就绪',
      pending: '等待处理',
      queued: '等待处理',
      running: '处理中',
      succeeded: '已完成',
      failed: '未完成',
      cancelled: '已取消',
      scored: '已评分',
      needs_review: '待复核',
      effective: '已生效',
      needs_teacher: '待教师批阅',
      unknown: '证据不足',
      good: '已有正确证据',
      bad: '需要巩固',
      conflict: '证据存在分歧',
      open: '待处理',
      retained: '维持原结果',
      revised: '已修订',
      research_prototype: '研究画像',
      rule_statistics: '课程记录统计',
      rule: '规则评分',
      teacher: '教师评分',
      not_connected: '尚未接通',
      stale: '待重建',
      unconfigured: '尚未配置',
      test_fixture: '演示数据',
      demo: '演示数据',
      simulated: '模拟数据',
      mixed: '混合来源',
      real: '真实数据',
      manual_example: '人工样例',
      source_text: '学习资料',
      exercise: '练习',
      lesson: '学习内容',
      pass: '检查通过',
      approve: '审核通过',
      reject: '审核未通过',
      fail: '检查未通过',
    }) as Record<string, string>
  )[String(value)] || String(value ?? '—')
