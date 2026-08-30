import { reactive } from 'vue'
import { get, post } from '../api'

export type LabelStatus = {
  year: number | null
  paper_ids: number[]
  years: number[]
  total: number
  labeled: number
  locked: number
  review_pending: number
  remaining: number
  percentage: number
  question_bank_profile_id?: number
  run_id?: string
}

export type LabelScope = {
  kind: 'all' | 'year' | 'papers'
  title: string
  year: number | null
  paperIds: number[]
  questionBankProfileId: number
}

type LabelingState = {
  isRunning: boolean
  isPausing: boolean
  runId: string
  scope: LabelScope | null
  status: LabelStatus | null
  message: string
  error: string
}

export const questionLabelingState = reactive<LabelingState>({
  isRunning: false,
  isPausing: false,
  runId: '',
  scope: null,
  status: null,
  message: '',
  error: '',
})

let activeLoop = 0

function normalizedScope(scope: LabelScope): LabelScope {
  return {
    ...scope,
    paperIds: [...new Set(scope.paperIds.filter(value => value > 0))].sort((a, b) => a - b),
  }
}

function scopeKey(scope: LabelScope) {
  return JSON.stringify([scope.kind, scope.year, scope.questionBankProfileId, normalizedScope(scope).paperIds])
}

function statusQuery(scope: LabelScope, runId = '') {
  const query = new URLSearchParams()
  if (scope.year !== null) query.set('year', String(scope.year))
  if (scope.paperIds.length) query.set('paper_ids', scope.paperIds.join(','))
  if (scope.questionBankProfileId > 0) query.set('question_bank_profile_id', String(scope.questionBankProfileId))
  if (runId) query.set('run_id', runId)
  const suffix = query.toString()
  return suffix ? `?${suffix}` : ''
}

export async function loadQuestionLabelingStatus(scope: LabelScope) {
  const nextScope = normalizedScope(scope)
  const sameRunningScope = questionLabelingState.isRunning
    && questionLabelingState.scope
    && scopeKey(questionLabelingState.scope) === scopeKey(nextScope)
  const status = await get<LabelStatus>(`/ai/question-labels/status${statusQuery(nextScope, sameRunningScope ? questionLabelingState.runId : '')}`)
  if (!questionLabelingState.isRunning || scopeKey(questionLabelingState.scope || nextScope) === scopeKey(nextScope)) {
    questionLabelingState.scope = nextScope
    questionLabelingState.status = status
  }
  return status
}

export async function startQuestionLabeling(
  scope: LabelScope,
  overwriteUnlocked = false,
) {
  const nextScope = normalizedScope(scope)
  if (questionLabelingState.isRunning || questionLabelingState.isPausing) return
  const isSameScope = questionLabelingState.scope
    && scopeKey(questionLabelingState.scope) === scopeKey(nextScope)
  if (!isSameScope || !questionLabelingState.runId) {
    questionLabelingState.runId = crypto.randomUUID()
  }
  questionLabelingState.scope = nextScope
  questionLabelingState.isRunning = true
  questionLabelingState.isPausing = false
  questionLabelingState.message = '正在读取下一篇题目…'
  questionLabelingState.error = ''
  const loopId = ++activeLoop
  try {
    while (questionLabelingState.isRunning && loopId === activeLoop) {
      const result = await post<any>('/ai/question-labels/next', {
        year: nextScope.year,
        paper_ids: nextScope.paperIds,
        overwrite_unlocked: overwriteUnlocked,
        run_id: questionLabelingState.runId,
        question_bank_profile_id: nextScope.questionBankProfileId,
      })
      if (loopId !== activeLoop) return
      questionLabelingState.runId = result.run_id || questionLabelingState.runId
      questionLabelingState.status = result
      if (questionLabelingState.isPausing) {
        await post(`/ai/question-labels/runs/${encodeURIComponent(questionLabelingState.runId)}/pause`, {})
        questionLabelingState.isRunning = false
        questionLabelingState.isPausing = false
        questionLabelingState.message = '已暂停。再次开始时会从下一篇未完成材料继续。'
        return
      }
      if (result.done) {
        questionLabelingState.message = `${nextScope.title}已完成智能标注`
        questionLabelingState.isRunning = false
        questionLabelingState.isPausing = false
        questionLabelingState.runId = ''
        return
      }
      questionLabelingState.message = `已完成：${result.unit_title}，本篇标注 ${result.processed} 道`
      await new Promise(resolve => window.setTimeout(resolve, 120))
    }
  } catch (cause) {
    if (loopId !== activeLoop) return
    questionLabelingState.error = String(cause)
    questionLabelingState.message = ''
    questionLabelingState.isRunning = false
    questionLabelingState.isPausing = false
  }
}

export function pauseQuestionLabeling() {
  if (!questionLabelingState.isRunning || questionLabelingState.isPausing) return
  questionLabelingState.isPausing = true
  questionLabelingState.message = '正在完成当前篇目，随后暂停…'
}
