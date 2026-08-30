export type DiagnosticModule =
  | 'startup'
  | 'library'
  | 'practice'
  | 'import'
  | 'settings'
  | 'ai'
  | 'vocabulary'
  | 'update'

export interface DiagnosticEntry {
  createdAt: string
  event: string
  module: DiagnosticModule
  appVersion: string
  errorCategory: string
  symptom: string
}

const STORAGE_KEY = 'english-practice-public-diagnostics-v1'
const MAX_ENTRIES = 50
const MAX_EXPORT_BYTES = 1024 * 1024
const APP_VERSION = '0.1.0'

function truncate(value: string, limit: number): string {
  return value.length > limit ? `${value.slice(0, Math.max(0, limit - 1))}…` : value
}

export function sanitizeDiagnosticValue(value: unknown, limit = 240): string {
  let text = typeof value === 'string'
    ? value
    : value instanceof Error
      ? value.message
      : String(value ?? '')
  text = text
    .replace(/\bBearer\s+[A-Za-z0-9._~+/=-]+/gi, 'Bearer [已隐藏]')
    .replace(/\bsk-[A-Za-z0-9_-]{8,}\b/gi, '[API Key 已隐藏]')
    .replace(/((?:api[-_ ]?key|authorization|access[-_ ]?token|refresh[-_ ]?token|secret|password)\s*[:=]\s*)[^\s,;}\]]+/gi, '$1[已隐藏]')
    .replace(/https?:\/\/[^\s\"'<>]+/gi, '[URL 已隐藏]')
    .replace(/[A-Za-z]:\\(?:[^\\\r\n]+\\)+[^\\\r\n]*/g, '[完整路径已隐藏]')
    .replace(/\/(?:home|Users|storage|data|sdcard|mnt|var|tmp)\/[^\s\"'<>]*/gi, '[完整路径已隐藏]')
    .replace(/\b(?:\d{1,3}\.){3}\d{1,3}\b/g, '[IP 已隐藏]')
    .replace(/\b(?:[A-F0-9]{0,4}:){2,7}[A-F0-9]{0,4}\b/gi, '[IP 已隐藏]')
    .replace(/((?:device[-_ ]?id|fingerprint|machine[-_ ]?id|imei|serial)\s*[:=]\s*)[^\s,;}\]]+/gi, '$1[设备标识已隐藏]')
    .replace(/((?:question|题目|题库正文|answer|答案正文|chat|聊天内容|learning[-_ ]?record|学习记录)\s*[:=：]\s*)[^\r\n;}]*/gi, '$1[内容已隐藏]')
    .replace(/\s+/g, ' ')
    .trim()
  return truncate(text, limit)
}

function safeIdentifier(value: unknown, fallback: string, limit: number): string {
  const candidate = sanitizeDiagnosticValue(value, limit)
  return /^[A-Za-z0-9_.-]+$/.test(candidate) ? candidate : fallback
}

function safeAppVersion(value: unknown): string {
  const candidate = sanitizeDiagnosticValue(value, 40)
  return /^(?:unknown|v?\d+(?:\.\d+){1,3}(?:[-+][A-Za-z0-9.-]+)?)$/.test(candidate) ? candidate : 'unknown'
}

function safeEvent(value: unknown): string {
  const candidate = safeIdentifier(value, 'unknown_event', 80)
  return /^(?:load_(?:startup|library)|(?:practice|import|settings|ai|vocabulary|update)_(?:get|post|put|patch|delete))$/.test(candidate)
    ? candidate
    : 'unknown_event'
}

function safeTimestamp(value: unknown): string {
  const parsed = new Date(String(value ?? ''))
  return Number.isNaN(parsed.getTime()) ? new Date().toISOString() : parsed.toISOString()
}

function isModule(value: unknown): value is DiagnosticModule {
  return ['startup', 'library', 'practice', 'import', 'settings', 'ai', 'vocabulary', 'update'].includes(String(value))
}

function genericSymptom(errorCategory: string): string {
  return `操作未完成（${errorCategory}），请重试；如仍失败，可主动导出此诊断。`
}

function projectEntry(value: unknown): DiagnosticEntry | null {
  if (!value || typeof value !== 'object') return null
  const source = value as Record<string, unknown>
  if (!isModule(source.module)) return null
  const errorCategory = safeIdentifier(source.errorCategory, 'UNKNOWN_ERROR', 64)
  return {
    createdAt: safeTimestamp(source.createdAt),
    event: safeEvent(source.event),
    module: source.module,
    appVersion: safeAppVersion(source.appVersion ?? APP_VERSION),
    errorCategory,
    symptom: genericSymptom(errorCategory),
  }
}

export function projectDiagnosticEntries(values: unknown): DiagnosticEntry[] {
  if (!Array.isArray(values)) return []
  return values.map(projectEntry).filter((entry): entry is DiagnosticEntry => entry !== null).slice(0, MAX_ENTRIES)
}

export function listDiagnosticEntries(): DiagnosticEntry[] {
  try {
    return projectDiagnosticEntries(JSON.parse(localStorage.getItem(STORAGE_KEY) || '[]'))
  } catch {
    return []
  }
}

function writeEntries(entries: DiagnosticEntry[]): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(projectDiagnosticEntries(entries)))
  } catch {
    // Diagnostics must never break the user operation that produced them.
  }
}

export function clearDiagnosticEntries(): void { localStorage.removeItem(STORAGE_KEY) }

export function recordDiagnosticError(
  module: DiagnosticModule,
  event: string,
  cause: unknown,
): DiagnosticEntry {
  const candidate = cause as { status?: unknown, code?: unknown, name?: unknown, message?: unknown }
  const errorCategory = candidate?.code
    ? candidate.code
    : candidate?.status
      ? `HTTP_${candidate.status}`
      : candidate?.name ?? 'UNKNOWN_ERROR'
  const entry: DiagnosticEntry = {
    createdAt: new Date().toISOString(),
    event: safeEvent(event),
    module,
    appVersion: APP_VERSION,
    errorCategory: safeIdentifier(errorCategory, 'UNKNOWN_ERROR', 64),
    symptom: genericSymptom(safeIdentifier(errorCategory, 'UNKNOWN_ERROR', 64)),
  }
  writeEntries([entry, ...listDiagnosticEntries()])
  return entry
}

export function serializeDiagnosticPayload(entries: unknown): string {
  const projected = projectDiagnosticEntries(entries)
  while (projected.length) {
    const text = JSON.stringify({
      format: 'english-practice-machine-diagnostics',
      schemaVersion: 2,
      exportedAt: new Date().toISOString(),
      privacyNotice: '仅包含事件、模块、应用版本、时间、错误类别和短症状；不含题库、答案、聊天或学习记录。',
      entries: projected,
    }, null, 2)
    if (new TextEncoder().encode(text).byteLength <= MAX_EXPORT_BYTES) return text
    projected.pop()
  }
  return JSON.stringify({
    format: 'english-practice-machine-diagnostics',
    schemaVersion: 2,
    exportedAt: new Date().toISOString(),
    entries: [],
  }, null, 2)
}

export async function copyText(text: string): Promise<void> {
  try {
    await navigator.clipboard.writeText(text)
  } catch {
    const textarea = document.createElement('textarea')
    textarea.value = text
    textarea.style.position = 'fixed'
    textarea.style.opacity = '0'
    document.body.appendChild(textarea)
    textarea.select()
    const copied = document.execCommand('copy')
    textarea.remove()
    if (!copied) throw new Error('系统剪贴板不可用')
  }
}

export function issueReportTemplate(): string {
  return [
    '# 英语刷题机公测问题报告',
    `应用版本：${APP_VERSION}`,
    '问题发生时间：',
    '所在功能：',
    '复现步骤：',
    '预期结果：',
    '实际结果：',
    '是否每次出现：',
    '补充说明：',
    '',
    '请勿填写 API Key、题库或答案正文、聊天内容、学习记录及完整文件路径。',
  ].join('\n')
}

export function downloadDiagnosticEntries(): number {
  const entries = listDiagnosticEntries()
  if (!entries.length) return 0
  const blob = new Blob([serializeDiagnosticPayload(entries)], { type: 'application/json' })
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = `english-practice-diagnostics-${new Date().toISOString().replace(/[:.]/g, '-')}.json`
  anchor.click()
  URL.revokeObjectURL(url)
  return entries.length
}
