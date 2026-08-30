const API_ROOT = '/api'

function diagnosticContext(path: string, options: RequestInit) {
  const pathname = new URL(path, 'https://local.english-practice.invalid').pathname
  const method = String(options.method || 'GET').toUpperCase()
  if (pathname === '/startup') return { module: 'startup' as const, event: 'load_startup' }
  if (pathname === '/papers' || pathname.startsWith('/question-bank')) return { module: 'library' as const, event: 'load_library' }
  if (pathname.startsWith('/practice/')) return { module: 'practice' as const, event: `practice_${method.toLowerCase()}` }
  if (pathname === '/imports' || pathname.startsWith('/imports/')) return { module: 'import' as const, event: `import_${method.toLowerCase()}` }
  if (pathname.startsWith('/ai/profiles')) return { module: 'settings' as const, event: `settings_${method.toLowerCase()}` }
  if (pathname.startsWith('/ai/')) return { module: 'ai' as const, event: `ai_${method.toLowerCase()}` }
  if (pathname.startsWith('/vocabulary')) return { module: 'vocabulary' as const, event: `vocabulary_${method.toLowerCase()}` }
  if (pathname.startsWith('/remote-question-banks')) return { module: 'import' as const, event: `remote_question_bank_${method.toLowerCase()}` }
  if (pathname.startsWith('/updates')) return { module: 'update' as const, event: `update_${method.toLowerCase()}` }
  return null
}

export async function api<T>(path: string, options: RequestInit = {}): Promise<T> {
  const headers = new Headers(options.headers)
  if (!(options.body instanceof FormData)) {
    headers.set('Content-Type', 'application/json')
  }
  let response: Response
  try {
    response = await fetch(`${API_ROOT}${path}`, { ...options, headers })
  } catch (cause) {
    const diagnostic = diagnosticContext(path, options)
    if (diagnostic) {
      const { recordDiagnosticError } = await import('./platform/diagnostics')
      recordDiagnosticError(diagnostic.module, diagnostic.event, cause)
    }
    throw cause
  }
  if (!response.ok) {
    let message = `${response.status} ${response.statusText}`
    let detail: unknown = null
    try {
      const data = await response.json()
      detail = data.detail
      message = typeof detail === 'string'
        ? detail
        : (detail as any)?.message || JSON.stringify(detail)
    } catch {
      // Keep status text.
    }
    const error = new Error(message) as Error & { status?: number, detail?: unknown }
    error.status = response.status
    error.detail = detail
    const diagnostic = diagnosticContext(path, options)
    if (diagnostic) {
      const { recordDiagnosticError } = await import('./platform/diagnostics')
      recordDiagnosticError(diagnostic.module, diagnostic.event, error)
    }
    throw error
  }
  return response.json()
}

export const get = <T>(path: string) => api<T>(path)
export const post = <T>(path: string, body?: unknown) =>
  api<T>(path, { method: 'POST', body: body === undefined ? undefined : JSON.stringify(body) })
export const put = <T>(path: string, body?: unknown) =>
  api<T>(path, { method: 'PUT', body: body === undefined ? undefined : JSON.stringify(body) })
export const patch = <T>(path: string, body?: unknown) =>
  api<T>(path, { method: 'PATCH', body: body === undefined ? undefined : JSON.stringify(body) })
export const del = <T>(path: string) => api<T>(path, { method: 'DELETE' })
