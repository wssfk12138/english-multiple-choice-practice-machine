<script setup lang="ts">
import { selectedSourceSentence } from '../vocabulary-context'
import {
  AlertCircle,
  ArrowLeft,
  Award,
  CheckCircle2,
  ChevronRight,
  Clock3,
  Coffee,
  Play,
  Save,
  Send,
  X,
} from 'lucide-vue-next'
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { onBeforeRouteLeave, onBeforeRouteUpdate, useRoute, useRouter } from 'vue-router'
import { get, post, put } from '../api'
import { confirmDialog } from '../platform/dialogs'
import { createTimer, elapsedTimer, checkpointTimer, pauseTimerState, resumeTimerState, finishTimerState, timerSnapshot, restoreTimer, type PracticeTimerState, type TimerPauseReason } from '../services/practiceTimer'
import PracticeContentChoice from '../components/PracticeContentChoice.vue'
import ContentBlocks from '../components/ContentBlocks.vue'
import PassageBlank from '../components/PassageBlank.vue'
import { splitPassageBlanks } from '../passage-blanks'
import ListeningPlayer from '../components/ListeningPlayer.vue'

const route = useRoute()
const router = useRouter()
const session = ref<any>(null)
const activeUnitIndex = ref(0)
const error = ref('')
const saving = ref<number | null>(null)
const vocabularyToast = ref('')
const unansweredNotice = ref('')
const highlightedQuestionId = ref<number | null>(null)
const resultPanelVisible = ref(false)
const resultPanelMode = ref<'unit' | 'session'>('unit')
const resultPanelUnitId = ref<number | null>(null)
const vocabMenu = ref({ visible: false, x: 0, y: 0, term: '', sentence: '', sourceKind: 'unknown', selectionStart: -1, questionId: null as number | null })
const listeningPlayer = ref<InstanceType<typeof ListeningPlayer> | null>(null)
const currentListeningQuestionId = ref<number | null>(null)
const currentWordBankQuestionId = ref<number | null>(null)
type VocabularyTranslationTrigger = 'unit_submit' | 'session_submit' | 'practice_exit'
type PendingVocabularyRecord = { entryId: number, unitId: number | null }
const pendingVocabulary = ref<PendingVocabularyRecord[]>([])
const timerState = ref<PracticeTimerState | null>(null)
const timerPromptVisible = ref(false)
const timerNow = ref(performance.now())
const timerNotice = ref('')
const timerSaveError = ref('')
const pageForeground = ref(document.visibilityState === 'visible')
const pageLeaving = ref(false)
const submissionBusy = ref(false)
const timerLoading = ref(true)
let pageAlive = true
let loadGeneration = 0
let loadedSessionId: number | null = null
let practiceExitTranslation: Promise<void> | null = null
let timerTicker: number | null = null
let checkpointAt = 0
let timerBackupBlocked = false
const activeUnit = computed(() => session.value?.units?.[activeUnitIndex.value])
const activeContentBlocks = computed(() => activeUnit.value?.content_blocks || [])
const clozeBlankNumbers = computed<number[]>(() => activeUnit.value?.unit_type === 'cloze'
  ? (activeUnit.value.questions || []).map((question: any) => Number(question.number)).filter(Number.isFinite)
  : [])
const activeContentPackage = computed(() => ({
  packageId: activeUnit.value?.shared_data?.content_package_id || '',
  contentVersion: activeUnit.value?.shared_data?.content_version || '',
}))
const progress = computed(() => {
  if (!session.value) return { answered: 0, total: 0 }
  let answered = 0
  let total = 0
  for (const unit of session.value.units) {
    for (const question of unit.questions) {
      total++
      if (question.user_answer) answered++
    }
  }
  return { answered, total }
})
const isPartB = computed(() => activeUnit.value?.unit_type === 'part_b')
const isMatchingPartB = computed(() => isPartB.value && activeUnit.value?.subtype !== 'true_false')
const isOrdering = computed(() => activeUnit.value?.subtype === 'paragraph_reordering')
const isListening = computed(() => activeUnit.value?.unit_type === 'listening')
const isWordBank = computed(() => activeUnit.value?.unit_type === 'word_bank')
const isParagraphMatching = computed(() => activeUnit.value?.unit_type === 'paragraph_matching')
const audioSeekable = computed(() => !timerEnabled.value || timerState.value?.mode === 'finished')
const audioTracks = computed(() => activeUnit.value?.shared_data?.audio_tracks || [])
const candidateOptions = computed(() => activeUnit.value?.questions?.[0]?.options || [])
const selectedWordBank = computed<Record<string, string>>(() => Object.fromEntries((activeUnit.value?.questions || []).map((question: any) => [String(question.id), String(question.user_answer || '')])))
const usedWordBankLetters = computed(() => new Set(Object.values(selectedWordBank.value).filter(Boolean)))
const wordBankWords = computed(() => {
  if (!isWordBank.value) return []
  const opts = activeUnit.value?.questions?.[0]?.options || []
  return opts.map((option: any) => ({
    stableKey: option.stable_key,
    label: option.label,
    content: option.content,
    used: usedWordBankLetters.value.has(option.stable_key),
  }))
})
const listeningAnsweredCount = computed(() => isListening.value ? (activeUnit.value?.questions || []).filter((question: any) => String(question.user_answer || '').trim()).length : 0)
const currentListeningQuestion = computed(() => {
  if (!isListening.value) return null
  const questions = activeUnit.value?.questions || []
  return questions.find((question: any) => question.id === currentListeningQuestionId.value) || questions.find((question: any) => !String(question.user_answer || '').trim()) || questions[0] || null
})
const currentListeningQuestionNumber = computed(() => currentListeningQuestion.value?.number ?? null)
const orderingFixedSlots = computed(() => {
  const slots = activeUnit.value?.shared_data?.fixed_slots
  return Array.isArray(slots) ? slots : []
})
const timerElapsedMs = computed(() => elapsedTimer(timerState.value, timerNow.value))
const timerText = computed(() => formatDuration(timerElapsedMs.value))
const timerEnabled = computed(() =>
  Boolean(timerState.value && timerState.value.mode !== 'off'),
)
const timerReady = computed(() =>
  pageAlive && !timerLoading.value && pageForeground.value && !pageLeaving.value && !submissionBusy.value
  && contentChoiceSessionId.value === null && Number(route.params.id) === loadedSessionId
  && session.value?.id === loadedSessionId && session.value?.status !== 'submitted',
)
const activeUnitSubmitted = computed(() =>
  Boolean(activeUnit.value?.submission?.submitted || session.value?.status === 'submitted'),
)
const resultUnit = computed(() =>
  session.value?.units?.find((unit: any) => unit.id === resultPanelUnitId.value)
  || activeUnit.value,
)
const overallResult = computed(() => {
  if (!session.value || session.value.status !== 'submitted') return null
  if (session.value.result_summary) return session.value.result_summary
  const submissions = session.value.units
    .map((unit: any) => unit.submission)
    .filter((submission: any) => submission?.submitted)
  return {
    score: session.value.score,
    max_score: session.value.max_score,
    wrong_count: submissions.reduce(
      (sum: number, submission: any) => sum + (submission.wrong_count || 0),
      0,
    ),
    correct_count: submissions.reduce(
      (sum: number, submission: any) => sum + (submission.correct_count || 0),
      0,
    ),
    question_count: submissions.reduce(
      (sum: number, submission: any) => sum + (submission.question_count || 0),
      0,
    ),
  }
})
const hasPendingUnits = computed(() =>
  Boolean(session.value?.units?.some((unit: any) => !unit.submission?.submitted)),
)
type PassageSegment = {
  type: 'text' | 'blank'
  text: string
  number?: number
}
const passageSegments = computed<PassageSegment[]>(() => {
  const unit = activeUnit.value
  const passage = unit?.passage || '该题型请在右侧完成候选项匹配。'
  if (unit?.unit_type === 'cloze') return splitPassageBlanks(passage, clozeBlankNumbers.value)
  const usesInlineBlanks = unit?.unit_type === 'cloze'
    || unit?.unit_type === 'word_bank'
    || (unit?.unit_type === 'part_b' && unit?.subtype !== 'true_false')
  if (!unit || !usesInlineBlanks) {
    return [{ type: 'text', text: passage }]
  }

  const numbers: number[] = [...new Set<number>(
    unit.questions.map((question: any) => Number(question.number)),
  )]
    .filter(Number.isFinite)
    .sort((left, right) => left - right)
  const candidates = [...passage.matchAll(/(?<!\d)(?:\(\s*)?([1-4]?\d)(?:\s*\))?(?:\s*_{2,})?(?!\d)/g)]
  const selected: { start: number, end: number, number: number }[] = []
  let after = -1

  for (const number of numbers) {
    const candidate = candidates.find(match =>
      Number(match[1]) === number
      && (match.index ?? -1) > after
      && /^\s*(?:[.,;:!?，。；：！？]|$)/.test(
        passage.slice((match.index ?? 0) + match[0].length),
      )
        === false
    ) || candidates.find(match =>
      Number(match[1]) === number && (match.index ?? -1) > after
    )
    if (!candidate || candidate.index === undefined) continue
    selected.push({
      start: candidate.index,
      end: candidate.index + candidate[0].length,
      number,
    })
    after = candidate.index
  }

  if (!selected.length) return [{ type: 'text', text: passage }]
  const result: PassageSegment[] = []
  let cursor = 0
  for (const blank of selected) {
    if (blank.start > cursor) {
      result.push({ type: 'text', text: passage.slice(cursor, blank.start) })
    }
    result.push({ type: 'blank', text: String(blank.number), number: blank.number })
    cursor = blank.end
  }
  if (cursor < passage.length) result.push({ type: 'text', text: passage.slice(cursor) })
  return result
})

function timerStorageKey(sessionId: number) {
  return `linjian-practice-timer-${sessionId}`
}

function formatDuration(milliseconds: number) {
  const totalSeconds = Math.max(0, Math.floor(milliseconds / 1000))
  const hours = Math.floor(totalSeconds / 3600)
  const minutes = Math.floor((totalSeconds % 3600) / 60)
  const seconds = totalSeconds % 60
  return [hours, minutes, seconds]
    .map(value => String(value).padStart(2, '0'))
    .join(':')
}

function formatScore(value: unknown) {
  const score = Number(value)
  if (!Number.isFinite(score)) return '0'
  return score.toLocaleString('zh-CN', { maximumFractionDigits: 2 })
}

function persistTimer() {
  if (loadedSessionId === null || session.value?.id !== loadedSessionId || !timerState.value) return
  if (timerBackupBlocked) return
  try {
    localStorage.setItem(timerStorageKey(loadedSessionId), JSON.stringify(timerSnapshot(timerState.value, performance.now())))
    timerSaveError.value = ''
  } catch {
    timerSaveError.value = '计时保存失败；异常关闭可能丢失尚未保存的用时，请保持页面打开并检查设备存储。'
  }
}

function vocabularyQueueKey(sessionId: number) {
  return `linjian:vocabulary-queue:${sessionId}`
}

function loadPendingVocabulary(sessionId: number) {
  try {
    const raw = localStorage.getItem(vocabularyQueueKey(sessionId))
    if (!raw) {
      pendingVocabulary.value = []
      return
    }
    const parsed = JSON.parse(raw)
    if (!Array.isArray(parsed)) throw new Error('invalid vocabulary queue')
    pendingVocabulary.value = parsed
      .filter(item => Number.isInteger(item?.entryId) && item.entryId > 0)
      .slice(0, 200)
      .map(item => ({
        entryId: Number(item.entryId),
        unitId: Number.isInteger(item.unitId) ? Number(item.unitId) : null,
      }))
  } catch {
    pendingVocabulary.value = []
  }
}

function persistPendingVocabulary() {
  if (!session.value?.id) return
  localStorage.setItem(
    vocabularyQueueKey(session.value.id),
    JSON.stringify(pendingVocabulary.value),
  )
}

function rememberVocabulary(entryId: number, unitId: number | null) {
  if (
    pendingVocabulary.value.some(
      item => item.entryId === entryId && item.unitId === unitId,
    )
  ) return
  pendingVocabulary.value.push({ entryId, unitId })
  persistPendingVocabulary()
}

function translationRecords(unitId?: number) {
  return unitId === undefined
    ? pendingVocabulary.value
    : pendingVocabulary.value.filter(item => item.unitId === unitId)
}

async function flushVocabularyTranslations(
  trigger: VocabularyTranslationTrigger,
  unitId?: number,
) {
  const records = [...translationRecords(unitId)]
  const entryIds = [...new Set(records.map(item => item.entryId))]
  if (!entryIds.length) return
  try {
    const result: any = await post('/vocabulary/translation-runs', {
      entry_ids: entryIds,
      trigger,
    })
    const recordKeys = new Set(records.map(item => `${item.entryId}:${item.unitId ?? ''}`))
    pendingVocabulary.value = pendingVocabulary.value.filter(
      item => !recordKeys.has(`${item.entryId}:${item.unitId ?? ''}`),
    )
    persistPendingVocabulary()
    if (result.queuedCount > 0 && trigger !== 'practice_exit') {
      vocabularyToast.value = `已将 ${result.queuedCount} 个单词提交后台翻译`
      window.setTimeout(() => { vocabularyToast.value = '' }, 2600)
    }
  } catch {
    // 单词已经保存在本地。保留队列，下一次退出答题界面时继续尝试。
  }
}

function flushVocabularyOnPageHide() {
  pausePracticeAutomatically()
  const entryIds = [...new Set(pendingVocabulary.value.map(item => item.entryId))]
  if (!entryIds.length) return
  void fetch('/api/vocabulary/translation-runs', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      entry_ids: entryIds,
      trigger: 'practice_exit',
    }),
    keepalive: true,
  }).catch(() => undefined)
}

function flushVocabularyOnPracticeExit(): Promise<void> {
  if (!practiceExitTranslation) {
    practiceExitTranslation = flushVocabularyTranslations('practice_exit')
  }
  return practiceExitTranslation
}
function flushVocabularyOnVisibilityChange() {
  pageForeground.value = document.visibilityState === 'visible'
  if (!pageForeground.value) {
    pausePracticeAutomatically()
    void flushVocabularyOnPracticeExit()
  } else {
    // The user may return to the same practice session and add more words.
    practiceExitTranslation = null
    resumeAutomaticTimer()
  }
}

function initializeTimer() {
  if (!session.value || loadedSessionId !== session.value.id) return
  timerNotice.value = ''
  timerSaveError.value = ''
  timerBackupBlocked = false
  let raw: string | null = null
  try { raw = localStorage.getItem(timerStorageKey(session.value.id)) }
  catch { timerSaveError.value = '计时记录读取失败，无法恢复上次用时。' }
  const saved = restoreTimer(raw, session.value.status === 'submitted')
  timerNotice.value = saved.notice
  if (saved.backup && raw !== null) {
    try {
      const key = timerStorageKey(session.value.id) + '-legacy-backup'
      if (localStorage.getItem(key) === null) localStorage.setItem(key, raw.slice(0, 16384))
    } catch { timerBackupBlocked = true; timerSaveError.value = '旧计时记录备份失败；原记录未覆盖，本次计时暂不能保存。'; timerState.value = saved.state; timerPromptVisible.value = !saved.state && session.value.status !== 'submitted'; return }
  }
  timerState.value = saved.state
  if (session.value.status === 'submitted') {
    timerPromptVisible.value = false
  } else if (saved.state) {
    timerPromptVisible.value = false
  } else if (raw === null && session.value.units?.some((unit: any) => unit.submission?.submitted)) {
    timerState.value = { ...createTimer(), mode: 'off', pauseReason: null }
    timerPromptVisible.value = false
  } else {
    timerPromptVisible.value = true
  }
  // Unknown/corrupt snapshots stay intact until the user chooses a new timer.
  if (timerState.value) persistTimer()
}

function startTimedPractice() {
  timerNow.value = performance.now()
  timerState.value = resumeTimerState(createTimer(), timerNow.value, timerReady.value, true)
  timerPromptVisible.value = false
  checkpointAt = timerNow.value
  persistTimer()
  resetPracticeScroll()
}

function startWithoutTimer() {
  timerState.value = { ...createTimer(), mode: 'off', pauseReason: null }
  timerPromptVisible.value = false
  persistTimer()
  resetPracticeScroll()
}

function resetPracticeScroll() {
  ;(document.activeElement as HTMLElement | null)?.blur?.()
  window.scrollTo(0, 0)
  document.documentElement.scrollTop = 0
}

function pausePracticeTimer(reason: TimerPauseReason) {
  timerNow.value = performance.now()
  if (timerState.value) timerState.value = pauseTimerState(timerState.value, timerNow.value, reason)
  listeningPlayer.value?.pause(reason === 'manual')
  persistTimer()
}
function pausePracticeAutomatically() { pausePracticeTimer('automatic') }
function pauseTimer() { pausePracticeTimer('manual') }
function resumeAutomaticTimer() {
  if (!timerState.value) return
  timerNow.value = performance.now()
  const next = resumeTimerState(timerState.value, timerNow.value, timerReady.value)
  if (next !== timerState.value) { timerState.value = next; persistTimer() }
}
function resumeTimer() {
  if (!timerState.value || submissionBusy.value) return
  timerNow.value = performance.now()
  timerState.value = resumeTimerState(timerState.value, timerNow.value, timerReady.value, true)
  persistTimer()
}
function finishTimer() {
  if (timerState.value) timerState.value = finishTimerState(timerState.value, performance.now())
  persistTimer()
}
function releaseSubmissionTimer(wasRunning: boolean) {
  submissionBusy.value = false
  if (wasRunning && timerState.value?.mode === 'paused') {
    timerState.value = pauseTimerState(timerState.value, performance.now(), 'automatic')
    resumeAutomaticTimer()
  }
  persistTimer()
}

const contentChoiceSessionId = ref<number | null>(null)
const contentChoiceBusy = ref(false)
const contentChoiceError = ref('')
const contentChoiceNotice = ref('')
async function chooseContent(choice: 'continue' | 'restart') {
  if (contentChoiceBusy.value || contentChoiceSessionId.value === null) return
  contentChoiceBusy.value = true
  contentChoiceError.value = ''
  const generation = loadGeneration, sid = Number(route.params.id)
  try {
    const result: any = await post('/practice/sessions/' + contentChoiceSessionId.value + '/content-choice', {choice})
    if (!pageAlive || pageLeaving.value || generation !== loadGeneration || Number(route.params.id) !== sid) return
    if (Number(route.params.id) !== Number(result.id)) await router.replace('/practice/' + result.id)
    else await load()
    if (result.carried !== undefined) {
      contentChoiceNotice.value = `已保留 ${Number(result.carried) || 0} 题作答，${Number(result.reset) || 0} 题因内容修正需重做；${Number(result.carried_submissions) || 0} 篇已提交成绩原样带入。`
    }
  } catch (e) { contentChoiceError.value = String(e) }
  finally { contentChoiceBusy.value = false }
}

async function load() {
  const generation = ++loadGeneration
  const sid = Number(route.params.id)
  timerLoading.value = true
  const current = () => pageAlive && !pageLeaving.value && generation === loadGeneration && Number(route.params.id) === sid
  try {
    const loaded: any = await get('/practice/sessions/' + sid)
    if (!current()) return
    if (loaded.content_choice_required) {
      session.value = null
      contentChoiceSessionId.value = Number(loaded.id)
      return
    }
    contentChoiceSessionId.value = null
    session.value = loaded
    loadedSessionId = sid
    loadPendingVocabulary(session.value.id)
    initializeTimer()
    syncCurrentQuestionTargets()
  }
  catch (e) { if (current()) error.value = String(e) }
  finally { if (current()) { timerLoading.value = false; resumeAutomaticTimer() } }
}
watch(() => route.params.id, () => {
  pausePracticeAutomatically()
  pageLeaving.value = false
  session.value = null
  loadedSessionId = null
  timerState.value = null
  timerPromptVisible.value = false
  activeUnitIndex.value = 0
  submissionBusy.value = false
  practiceExitTranslation = null
  void load()
})
function handlePageShow() { pageForeground.value = document.visibilityState === 'visible'; resumeAutomaticTimer() }
const removeAfterNavigation = router.afterEach((_to, _from, failure) => {
  if (failure && pageAlive && Number(route.params.id) === loadedSessionId) {
    pageLeaving.value = false
    practiceExitTranslation = null
    resumeAutomaticTimer()
    if (timerLoading.value) void load()
  }
})

onMounted(() => {
  timerTicker = window.setInterval(() => {
    timerNow.value = performance.now()
    if (timerState.value?.mode === 'running' && timerNow.value - checkpointAt >= 5000) {
      timerState.value = checkpointTimer(timerState.value, timerNow.value)
      persistTimer()
      checkpointAt = timerNow.value
    }
  }, 500)
  window.addEventListener('keydown', handleWindowKeydown)
  window.addEventListener('pagehide', flushVocabularyOnPageHide)
  document.addEventListener('visibilitychange', flushVocabularyOnVisibilityChange)
  window.addEventListener('pageshow', handlePageShow)
  void load()
})
onBeforeUnmount(() => {
  pausePracticeAutomatically()
  pageAlive = false
  loadGeneration++
  removeAfterNavigation()
  window.removeEventListener('pageshow', handlePageShow)
  if (timerTicker !== null) window.clearInterval(timerTicker)
  window.removeEventListener('keydown', handleWindowKeydown)
  window.removeEventListener('pagehide', flushVocabularyOnPageHide)
  document.removeEventListener('visibilitychange', flushVocabularyOnVisibilityChange)
})
async function preparePracticeLeave() {
  if (isListening.value && session.value?.status === 'active' && !activeUnitSubmitted.value) {
    const proceed = await confirmDialog({title:'退出听力练习？',message:'听力练习尚未完成，本次练习记录不会保留。',confirmLabel:'继续退出',danger:true})
    if (!proceed) return false
  }
  pageLeaving.value = true
  pausePracticeAutomatically()
  await flushVocabularyOnPracticeExit()
  const current = session.value
  if (current && current.mode === 'paper' && current.status === 'active') {
    const sid = Number(current.id)
    try {
      const result: any = await post(`/practice/sessions/${sid}/abandon-if-empty`, {})
      if (result && result.kept === false) localStorage.removeItem(timerStorageKey(sid))
    } catch { /* Leave cleanup never discards answers or blocks navigation. */ }
  }
}
onBeforeRouteLeave(preparePracticeLeave)
onBeforeRouteUpdate(preparePracticeLeave)

function handleWindowKeydown(event: KeyboardEvent) {
  if (event.key === 'Escape' && resultPanelVisible.value) {
    resultPanelVisible.value = false
  }
}

async function select(question: any, key: string) {
  if (session.value.status === 'submitted' || activeUnitSubmitted.value) return false
  if (isListening.value) focusListeningQuestion(question.id, false)
  const previous = question.user_answer
  question.user_answer = key
  saving.value = question.id
  try {
    await put(`/practice/sessions/${session.value.id}/answers/${question.id}`, {
      answer: key, option_order: question.option_order,
    })
    if (isListening.value) await advanceListeningQuestion(question.id)
    return true
  } catch (e) {
    question.user_answer = previous
    error.value = String(e)
    return false
  } finally { saving.value = null }
}

function isOrderingOptionUsedByAnother(question: any, stableKey: string) {
  return (activeUnit.value?.questions || []).some((other: any) =>
    other.id !== question.id && other.user_answer === stableKey,
  )
}

function selectOrdering(question: any, stableKey: string) {
  if (!stableKey || isOrderingOptionUsedByAnother(question, stableKey)) return
  void select(question, stableKey)
}

async function selectWordBank(question: any, letter: string) {
  if (session.value.status === 'submitted' || activeUnitSubmitted.value) return
  currentWordBankQuestionId.value = question.id
  const nextAnswer = question.user_answer === letter ? '' : letter
  const saved = await select(question, nextAnswer)
  if (!saved || !nextAnswer) return
  const next = (activeUnit.value?.questions || []).find((candidate: any) => !String(candidate.user_answer || '').trim())
  currentWordBankQuestionId.value = next?.id ?? question.id
}

function wordBankAnswerForBlank(number?: number) {
  if (!number || !isWordBank.value) return ''
  const question = (activeUnit.value?.questions || []).find((candidate: any) => Number(candidate.number) === number)
  if (!question?.user_answer) return ''
  return question.options?.find((option: any) => option.stable_key === question.user_answer)?.content || ''
}

async function focusWordBankQuestion(number?: number, scroll = true) {
  if (!number || !isWordBank.value) return
  const question = (activeUnit.value?.questions || []).find((candidate: any) => Number(candidate.number) === number)
  if (!question) return
  currentWordBankQuestionId.value = question.id
  if (!scroll) return
  await new Promise(resolve => requestAnimationFrame(resolve))
  document.querySelector<HTMLElement>(`[data-question-id="${question.id}"]`)?.scrollIntoView({ behavior: 'smooth', block: 'center' })
}
function selectWordForCurrentBlank(letter: string) {
  if (!isWordBank.value) return
  const questions = activeUnit.value?.questions || []
  const question = questions.find((candidate: any) => candidate.id === currentWordBankQuestionId.value) || questions.find((candidate: any) => !String(candidate.user_answer || '').trim()) || questions[0]
  if (question) void selectWordBank(question, letter)
}
function syncCurrentQuestionTargets() {
  const questions = activeUnit.value?.questions || []
  if (isListening.value) { const target = questions.find((question: any) => !String(question.user_answer || '').trim()) || questions[0]; currentListeningQuestionId.value = target?.id ?? null } else currentListeningQuestionId.value = null
  if (isWordBank.value) { const target = questions.find((question: any) => !String(question.user_answer || '').trim()) || questions[0]; currentWordBankQuestionId.value = target?.id ?? null } else currentWordBankQuestionId.value = null
}
async function focusListeningQuestion(questionId: number, scroll = true) {
  if (!isListening.value) return
  currentListeningQuestionId.value = questionId
  if (!scroll) return
  await new Promise(resolve => requestAnimationFrame(resolve))
  document.querySelector<HTMLElement>(`[data-question-id="${questionId}"]`)?.scrollIntoView({ behavior: 'smooth', block: 'center' })
}
async function advanceListeningQuestion(answeredQuestionId: number) {
  const questions = activeUnit.value?.questions || []
  const answeredIndex = questions.findIndex((question: any) => question.id === answeredQuestionId)
  const next = questions.find((question: any, index: number) => index > answeredIndex && !String(question.user_answer || '').trim()) || questions.find((question: any) => !String(question.user_answer || '').trim())
  if (next) await focusListeningQuestion(next.id)
}

function resultClass(question: any, option: any) {
  if (session.value.status !== 'submitted' && !activeUnitSubmitted.value) {
    return { selected: question.user_answer === option.stable_key }
  }
  return {
    selected: question.user_answer === option.stable_key,
    wrong: question.user_answer === option.stable_key && question.is_correct === false,
  }
}

function firstUnanswered(unitIndexes: number[]) {
  for (const unitIndex of unitIndexes) {
    const unit = session.value.units[unitIndex]
    for (const question of unit.questions) {
      if (!String(question.user_answer || '').trim()) {
        return { unitIndex, question }
      }
    }
  }
  return null
}

async function focusUnanswered(unitIndex: number, question: any) {
  activeUnitIndex.value = unitIndex
  highlightedQuestionId.value = question.id
  unansweredNotice.value = `${session.value.units[unitIndex].title}的第 ${question.number} 题还未作答，已为你定位。`
  await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))
  const target = document.querySelector<HTMLElement>(`[data-question-id="${question.id}"]`)
  target?.scrollIntoView({ behavior: 'smooth', block: 'center' })
  window.setTimeout(() => {
    if (highlightedQuestionId.value === question.id) highlightedQuestionId.value = null
  }, 3200)
}

async function handleIncompleteSubmission(errorValue: any) {
  const detail = errorValue?.detail
  if (detail?.code !== 'incomplete_submission') return false
  session.value = await get(`/practice/sessions/${session.value.id}`)
  const unitIndex = session.value.units.findIndex((unit: any) => unit.id === detail.unit_id)
  if (unitIndex < 0) return false
  const question = session.value.units[unitIndex].questions.find(
    (item: any) => item.id === detail.question_id,
  )
  if (!question) return false
  await focusUnanswered(unitIndex, question)
  return true
}

async function submitCurrentUnit() {
  const missing = firstUnanswered([activeUnitIndex.value])
  if (missing) {
    await focusUnanswered(missing.unitIndex, missing.question)
    return
  }
  const confirmed = await confirmDialog({
    title: '确认提交本篇',
    message: `提交“${activeUnit.value.title}”后会显示本篇对错，且本篇不能继续修改。`,
    confirmLabel: '确认提交',
  })
  if (!confirmed) return
  const submittedUnitId = activeUnit.value.id
  const wasRunning = timerState.value?.mode === 'running'
  submissionBusy.value = true
  pausePracticeTimer('submitting')
  try {
    session.value = await post(
      `/practice/sessions/${session.value.id}/units/${submittedUnitId}/submit`,
    )
    unansweredNotice.value = ''
    showUnitResult(submittedUnitId)
  } catch (e) {
    if (await handleIncompleteSubmission(e)) return
    error.value = String(e)
  } finally {
    // 分篇提交不结束整卷计时；失败按前台条件恢复提交前的运行状态。
    releaseSubmissionTimer(wasRunning)
  }
}

async function submitSession() {
  const missing = firstUnanswered(session.value.units.map((_: any, index: number) => index))
  if (missing) {
    await focusUnanswered(missing.unitIndex, missing.question)
    return
  }
  const label = session.value.mode === 'paper' ? '整年试卷' : '本次练习'
  const confirmed = await confirmDialog({
    title: session.value.mode === 'paper' ? '确认提交整卷' : '确认提交练习',
    message: `提交${label}后才会显示对错，且不能继续修改。`,
    confirmLabel: '确认提交',
  })
  if (!confirmed) return
  const wasRunning = timerState.value?.mode === 'running'
  submissionBusy.value = true
  pausePracticeTimer('submitting')
  try {
    session.value = await post(`/practice/sessions/${session.value.id}/submit`)
    finishTimer()
    showSessionResult()
  }
  catch (e) {
    if (await handleIncompleteSubmission(e)) return
    error.value = String(e)
  }
  finally {
    releaseSubmissionTimer(wasRunning)
  }
}

function switchUnit(index: number) {
  if (!session.value?.units?.[index] || saving.value !== null) return
  activeUnitIndex.value = index
  unansweredNotice.value = ''
  highlightedQuestionId.value = null
  syncCurrentQuestionTargets()
}

function showUnitResult(unitId = activeUnit.value?.id) {
  if (!unitId) return
  resultPanelMode.value = 'unit'
  resultPanelUnitId.value = unitId
  resultPanelVisible.value = true
}

function showSessionResult() {
  resultPanelMode.value = 'session'
  resultPanelUnitId.value = null
  resultPanelVisible.value = true
}

function continueToPendingUnit(fromUnitId?: number) {
  const fromIndex = fromUnitId
    ? session.value?.units?.findIndex((unit: any) => unit.id === fromUnitId) ?? -1
    : activeUnitIndex.value
  const units = session.value?.units || []
  const laterIndex = units.findIndex(
    (unit: any, index: number) => index > fromIndex && !unit.submission?.submitted,
  )
  const fallbackIndex = units.findIndex((unit: any) => !unit.submission?.submitted)
  const targetIndex = laterIndex >= 0 ? laterIndex : fallbackIndex
  if (targetIndex >= 0) switchUnit(targetIndex)
  resultPanelVisible.value = false
}

function openUnitFromResult(unitId: number) {
  const index = session.value?.units?.findIndex((unit: any) => unit.id === unitId) ?? -1
  if (index >= 0) switchUnit(index)
  resultPanelVisible.value = false
}

function openVocabularyMenu(event: MouseEvent) {
  const selection = window.getSelection()
  const term = selection?.toString().trim().replace(/\s+/g, ' ') || ''
  if (!term || !/^[A-Za-z][A-Za-z'’\-]*(?:\s+[A-Za-z][A-Za-z'’\-]*){0,4}$/.test(term)) {
    vocabMenu.value.visible = false
    return
  }
  const target = event.target as HTMLElement
  const source = target.closest<HTMLElement>('[data-vocab-text]')
  if (!source) return
  event.preventDefault()
  const question = target.closest<HTMLElement>('[data-question-id]')
  const selectedRange = selection?.rangeCount ? selection.getRangeAt(0) : null
  if (!selectedRange || !source.contains(selectedRange.startContainer) || !source.contains(selectedRange.endContainer)) return
  const sourceRange = selectedRange.cloneRange()
  sourceRange.selectNodeContents(source)
  const sourceText = sourceRange.toString()
  sourceRange.setEnd(selectedRange.startContainer, selectedRange.startOffset)
  const selectionStart = sourceRange.toString().length + (selectedRange.toString().length - selectedRange.toString().trimStart().length)
  vocabMenu.value = {
    visible: true,
    x: Math.min(event.clientX, window.innerWidth - 220),
    y: Math.min(event.clientY, window.innerHeight - 120),
    term,
    sentence: selectedSourceSentence(sourceText, term, selectionStart),
    sourceKind: source.closest('.option, .candidate-reference, .ordering-paragraph') ? 'option' : source.closest('.passage') ? 'passage' : 'question',
    selectionStart,
    questionId: question?.dataset.questionId ? Number(question.dataset.questionId) : null,
  }
}

async function addSelectedVocabulary() {
  const item = vocabMenu.value
  vocabMenu.value.visible = false
  try {
    const result: any = await post('/vocabulary', {
      term: item.term,
      context_sentence: item.sentence,
      source_kind: item.sourceKind,
      selection_start: item.selectionStart,
      unit_id: activeUnit.value.id,
      question_id: item.questionId,
      year: activeUnit.value.year,
      unit_title: activeUnit.value.title,
      unit_type: activeUnit.value.unit_type,
    })
    if (result.translation_status !== 'ready') {
      rememberVocabulary(result.entry_id, activeUnit.value.id)
    }
    vocabularyToast.value = result.is_frequent
      ? `已记录第 ${result.encounter_count} 次，已标记为 🌟 高频词`
      : '已加入单词本，退出答题界面后统一翻译'
    window.setTimeout(() => { vocabularyToast.value = '' }, 2600)
    window.getSelection()?.removeAllRanges()
  } catch (e) {
    error.value = String(e)
  }
}

async function copySelectedTerm() {
  await window.navigator.clipboard.writeText(vocabMenu.value.term)
  vocabMenu.value.visible = false
}
</script>

<template>
  <PracticeContentChoice v-if="contentChoiceSessionId !== null" :busy="contentChoiceBusy" :error="contentChoiceError" @choose="chooseContent" />
  <div v-else class="practice-page" @click="vocabMenu.visible=false">
    <header class="practice-top">
      <div style="display:flex;align-items:center;gap:18px">
        <button class="button ghost" @click="router.push('/library')"><ArrowLeft :size="18" />退出</button>
        <div class="unit-tabs" v-if="session">
          <button v-for="(unit, i) in session.units" :key="unit.id" class="unit-tab" :class="{active:i===activeUnitIndex,submitted:unit.submission?.submitted}" @click="switchUnit(i)">
            {{ unit.title }}<CheckCircle2 v-if="unit.submission?.submitted" :size="13" />
          </button>
        </div>
      </div>
      <div v-if="session" class="practice-status">
        <span v-if="saving"><Save :size="15" />正在保存</span><span v-else>已完成 {{ progress.answered }}/{{ progress.total }}</span>
        <div v-if="timerEnabled" class="practice-timer" :class="{ paused: timerState?.mode === 'paused', finished: timerState?.mode === 'finished' }" aria-live="polite">
          <Clock3 :size="16" />
          <span v-if="timerState?.mode === 'finished'" class="timer-label">本次用时</span>
          <strong>{{ timerText }}</strong>
          <button v-if="timerState?.mode === 'running' && session.status === 'active'" type="button" @click="pauseTimer">
            <Coffee :size="15" />休息一下
          </button>
          <button v-else-if="timerState?.mode === 'paused' && session.status === 'active'" type="button" @click="resumeTimer">
            <Play :size="14" />继续练习
          </button>
        </div>
        <button
          v-if="session.status==='submitted'"
          class="result-banner"
          type="button"
          aria-label="查看整卷成绩"
          @click="showSessionResult"
        >
          <Award :size="16" />整卷 {{ formatScore(session.score) }} / {{ formatScore(session.max_score) }}
        </button>
      </div>
    </header>
    <div v-if="error" class="warning" style="margin:15px">{{ error }}</div>
    <div v-if="contentChoiceNotice" class="transfer-notice" role="status">{{ contentChoiceNotice }}</div>
    <div v-if="timerSaveError" class="warning" style="margin:0 15px 12px" role="alert">{{ timerSaveError }}</div>
    <div v-else-if="timerNotice" class="timer-notice" role="status">{{ timerNotice }}</div>
    <div v-if="unansweredNotice" class="unanswered-banner" role="alert">
      <AlertCircle :size="18" />{{ unansweredNotice }}
    </div>
    <div v-if="session && activeUnit" class="practice-layout" :class="{'listening-layout':isListening}">
      <section class="passage-pane" :class="{'listening-console-pane':isListening}">
        <span class="eyebrow">{{ activeUnit.year }} · {{ activeUnit.title }}</span>
        <ListeningPlayer
          v-if="isListening"
          ref="listeningPlayer"
          :tracks="audioTracks"
          :seekable="audioSeekable"
          :timer-paused="timerState?.mode === 'paused' || !pageForeground || pageLeaving"
          :current-question-number="currentListeningQuestionNumber"
          :answered-count="listeningAnsweredCount"
          :question-count="activeUnit.questions.length"
          @locate-question="currentListeningQuestion && focusListeningQuestion(currentListeningQuestion.id)"
          @playback-start="currentListeningQuestion && focusListeningQuestion(currentListeningQuestion.id)"
        />
        <template v-else>
        <h1 v-if="!isPartB">{{ activeUnit.unit_type === 'cloze' ? 'Use of English' : activeUnit.title }}</h1>
        <p v-if="activeUnit.shared_data?.directions" class="lead" style="margin-bottom:24px">{{ activeUnit.shared_data.directions }}</p>
        <div v-if="isOrdering" class="ordering-reference-sheet" aria-label="候选段落 A 到 G">
          <article v-for="option in candidateOptions" :key="option.stable_key" class="ordering-paragraph" data-vocab-text @contextmenu="openVocabularyMenu">
            <strong class="ordering-paragraph-label">{{ option.label }}.</strong>
            <div class="ordering-paragraph-content">
              <ContentBlocks v-if="option.content_blocks?.length" :blocks="option.content_blocks" :package-id="activeContentPackage.packageId" :content-version="activeContentPackage.contentVersion" />
              <p v-else>{{ option.content }}</p>
            </div>
          </article>
        </div>
        <div v-else class="passage" data-vocab-text @contextmenu="openVocabularyMenu">
          <ContentBlocks
            v-if="activeContentBlocks.length"
            :blocks="activeContentBlocks"
            :blank-numbers="clozeBlankNumbers"
            :package-id="activeContentPackage.packageId"
            :content-version="activeContentPackage.contentVersion"
          />
          <template v-else v-for="(segment, index) in passageSegments" :key="`${segment.type}-${index}`">
            <button v-if="segment.type === 'blank' && isWordBank" type="button" class="passage-blank word-bank-passage-blank" :class="{ selected: wordBankAnswerForBlank(segment.number) }" :disabled="activeUnitSubmitted" :aria-label="`第 ${segment.number} 空，${wordBankAnswerForBlank(segment.number) || '尚未选择词语'}`" @click="focusWordBankQuestion(segment.number)">
              <span class="word-bank-blank-number">{{ segment.number }}</span><span class="word-bank-blank-answer">{{ wordBankAnswerForBlank(segment.number) || '选择词语' }}</span>
            </button>
            <PassageBlank v-else-if="segment.type === 'blank'" :number="segment.number" />
            <template v-else>{{ segment.text }}</template>
          </template>
        </div>
        </template>
      </section>
      <section class="question-pane">
        <div v-if="isOrdering" class="ordering-board">
          <div class="ordering-section-heading"><span>选择答案</span><small>{{ activeUnit.questions.filter((question: any) => question.user_answer).length }} / {{ activeUnit.questions.length }} 已完成</small></div>
          <div
            v-if="orderingFixedSlots.length"
            class="ordering-fixed-slots"
            aria-label="原题固定段落位置"
          >
            <span class="ordering-fixed-caption">原题固定位置</span>
            <div class="ordering-fixed-chain">
              <template v-for="(slot, index) in orderingFixedSlots" :key="`${slot.type}-${slot.number ?? slot.label}-${index}`">
                <strong v-if="slot.type === 'question'" class="ordering-fixed-question">{{ slot.number }}</strong>
                <span v-else class="ordering-fixed-label">{{ slot.label }}</span>
                <span v-if="index < orderingFixedSlots.length - 1" class="ordering-slot-arrow" aria-hidden="true">→</span>
              </template>
            </div>
          </div>
          <div class="ordering-answer-list">
            <div v-for="question in activeUnit.questions" :key="question.id" class="ordering-answer-row" :class="{'unanswered-focus': highlightedQuestionId === question.id}" :data-question-id="question.id">
              <strong>{{ question.number }}.</strong>
              <div class="ordering-choice-row" role="group" :aria-label="`${question.number}题段落选择`">
                <button
                  v-for="option in candidateOptions"
                  :key="option.stable_key"
                  type="button"
                  class="ordering-choice"
                  :class="resultClass(question, option)"
                  :disabled="activeUnitSubmitted || isOrderingOptionUsedByAnother(question, option.stable_key)"
                  @click="selectOrdering(question, option.stable_key)"
                >{{ option.label }}</button>
              </div>
              <span v-if="activeUnitSubmitted" class="order-result" :class="{correct: question.is_correct}">{{ question.is_correct ? '正确' : '回答错误' }}</span>
            </div>
          </div>
        </div>
        <div v-else-if="isMatchingPartB" class="matching-board">
          <div class="candidate-bank">
            <article v-for="option in candidateOptions" :key="option.stable_key" class="candidate-reference" data-vocab-text @contextmenu="openVocabularyMenu">
              <span class="option-letter">{{ option.label }}</span><ContentBlocks v-if="option.content_blocks?.length" :blocks="option.content_blocks" :package-id="activeContentPackage.packageId" :content-version="activeContentPackage.contentVersion" /><p v-else>{{ option.content }}</p>
            </article>
          </div>
          <div v-for="question in activeUnit.questions" :key="question.id" class="question-card compact-match" :class="{'unanswered-focus':highlightedQuestionId===question.id}" data-vocab-text :data-question-id="question.id" @contextmenu="openVocabularyMenu">
            <div class="question-title"><strong>{{ question.number }}.</strong> <ContentBlocks v-if="question.stem_blocks?.length" :blocks="question.stem_blocks" :package-id="activeContentPackage.packageId" :content-version="activeContentPackage.contentVersion" /><template v-else>{{ question.stem }}</template></div>
            <div class="match-buttons">
              <button v-for="option in question.options" :key="option.stable_key" class="match-chip" :class="resultClass(question, option)" :disabled="activeUnitSubmitted" @click="select(question, option.stable_key)">{{ option.label }}</button>
            </div>
            <div v-if="activeUnitSubmitted" class="match-result" :style="{color:question.is_correct?'var(--success)':'var(--danger)'}">
              {{ question.is_correct ? '回答正确' : '回答错误' }}
            </div>
          </div>
        </div>
        <div v-else-if="isWordBank" class="word-bank-board">
          <div class="word-bank-list">
            <button
              v-for="word in wordBankWords"
              :key="word.stableKey"
              class="word-bank-chip"
              :class="{ used: word.used }"
              :disabled="activeUnitSubmitted || word.used"
              :aria-label="`将 ${word.content} 填入当前空位`"
              @click="selectWordForCurrentBlank(word.stableKey)"
            >{{ word.label }}. {{ word.content }}</button>
          </div>
          <div v-for="question in activeUnit.questions" :key="question.id" class="question-card compact-match" :class="{'unanswered-focus':highlightedQuestionId===question.id,'word-bank-current':currentWordBankQuestionId===question.id}" data-vocab-text :data-question-id="question.id" @click="currentWordBankQuestionId=question.id" @focusin="currentWordBankQuestionId=question.id" @contextmenu="openVocabularyMenu">
            <div class="question-title"><strong>{{ question.number }}.</strong> <ContentBlocks v-if="question.stem_blocks?.length" :blocks="question.stem_blocks" :package-id="activeContentPackage.packageId" :content-version="activeContentPackage.contentVersion" /><template v-else>{{ question.stem }}</template></div>
            <div class="match-buttons">
              <button
                v-for="option in question.options"
                :key="option.stable_key"
                class="match-chip"
                :class="{ ...resultClass(question, option), used: usedWordBankLetters.has(option.stable_key) && question.user_answer !== option.stable_key }"
                :disabled="activeUnitSubmitted || (usedWordBankLetters.has(option.stable_key) && question.user_answer !== option.stable_key)"
                @click="selectWordBank(question, option.stable_key)"
              >{{ option.label }}</button>
            </div>
            <div v-if="activeUnitSubmitted" class="match-result" :style="{color:question.is_correct?'var(--success)':'var(--danger)'}">
              {{ question.is_correct ? '回答正确' : '回答错误' }}
            </div>
          </div>
        </div>
        <div v-else-if="isParagraphMatching" class="matching-board">
          <div class="paragraph-candidate-bank" aria-label="候选段落 A 到 J"><div class="candidate-bank-heading"><strong>候选段落</strong><span>先阅读段落，再为每道题选择对应字母</span></div><article v-for="option in candidateOptions" :key="option.stable_key" class="candidate-reference paragraph-candidate-reference" data-vocab-text @contextmenu="openVocabularyMenu"><span class="option-letter">{{ option.label }}</span><ContentBlocks v-if="option.content_blocks?.length" :blocks="option.content_blocks" :package-id="activeContentPackage.packageId" :content-version="activeContentPackage.contentVersion" /><p v-else>{{ option.content }}</p></article></div>
          <div v-for="question in activeUnit.questions" :key="question.id" class="question-card compact-match" :class="{'unanswered-focus':highlightedQuestionId===question.id}" data-vocab-text :data-question-id="question.id" @contextmenu="openVocabularyMenu">
            <div class="question-title"><strong>{{ question.number }}.</strong> <ContentBlocks v-if="question.stem_blocks?.length" :blocks="question.stem_blocks" :package-id="activeContentPackage.packageId" :content-version="activeContentPackage.contentVersion" /><template v-else>{{ question.stem }}</template></div>
            <div class="match-buttons">
              <button
                v-for="option in question.options"
                :key="option.stable_key"
                class="match-chip"
                :class="resultClass(question, option)"
                :disabled="activeUnitSubmitted"
                @click="select(question, option.stable_key)"
              >{{ option.label }}</button>
            </div>
            <div v-if="activeUnitSubmitted" class="match-result" :style="{color:question.is_correct?'var(--success)':'var(--danger)'}">
              {{ question.is_correct ? '回答正确' : '回答错误' }}
            </div>
          </div>
        </div>
        <div v-else v-for="question in activeUnit.questions" :key="question.id" class="question-card" :class="{'unanswered-focus':highlightedQuestionId===question.id,'listening-current':isListening && currentListeningQuestionId===question.id}" :aria-current="isListening && currentListeningQuestionId===question.id ? 'true' : undefined" data-vocab-text :data-question-id="question.id" @click="isListening && focusListeningQuestion(question.id, false)" @focusin="isListening && focusListeningQuestion(question.id, false)" @contextmenu="openVocabularyMenu">
          <div v-if="isListening && currentListeningQuestionId===question.id" class="listening-current-label">当前听力题</div>
          <div class="question-title"><strong>{{ question.number }}.</strong> <ContentBlocks v-if="question.stem_blocks?.length" :blocks="question.stem_blocks" :package-id="activeContentPackage.packageId" :content-version="activeContentPackage.contentVersion" /><template v-else>{{ question.stem }}</template></div>
          <button
            v-for="option in question.options"
            :key="option.stable_key"
            class="option"
            :class="resultClass(question, option)"
            :disabled="activeUnitSubmitted"
            @click="select(question, option.stable_key)"
            @contextmenu.stop="openVocabularyMenu"
          >
            <span class="option-letter">{{ option.label }}</span>
            <span class="option-content" data-vocab-text><ContentBlocks v-if="option.content_blocks?.length" :blocks="option.content_blocks" :package-id="activeContentPackage.packageId" :content-version="activeContentPackage.contentVersion" /><template v-else>{{ option.content }}</template></span>
          </button>
          <div v-if="activeUnitSubmitted" style="margin-top:10px;font-size:13px" :style="{color:question.is_correct?'var(--success)':'var(--danger)'}">
            <CheckCircle2 :size="15" style="vertical-align:-2px" /> {{ question.is_correct ? '回答正确' : '回答错误' }}
          </div>
        </div>
      </section>
      <footer class="practice-footer" :class="{'listening-footer':isListening}">
        <div class="practice-footer-summary">
          <span>{{ activeUnitIndex + 1 }} / {{ session.units.length }} 篇</span>
          <button
            v-if="activeUnit.submission?.submitted"
            class="unit-result-link"
            type="button"
            @click="showUnitResult(activeUnit.id)"
          >
            本篇 {{ formatScore(activeUnit.submission.score) }} / {{ formatScore(activeUnit.submission.max_score) }}
            · 错 {{ activeUnit.submission.wrong_count ?? 0 }} 题
            <ChevronRight :size="15" />
          </button>
        </div>
        <div v-if="session.status==='active'" class="practice-submit-actions">
          <button v-if="session.mode==='paper' && !activeUnit.submission?.submitted" class="button secondary" @click="submitCurrentUnit">
            <CheckCircle2 :size="16" />提交本篇
          </button>
          <button class="button" @click="submitSession"><Send :size="16" />{{ session.mode === 'paper' ? '整卷交卷' : '提交练习' }}</button>
        </div>
        <button v-else class="button secondary" @click="router.push('/wrong')">查看错题</button>
      </footer>
    </div>
    <div v-else class="loading">正在展开试卷…</div>
    <div v-if="vocabMenu.visible" class="vocab-context-menu" :style="{left:`${vocabMenu.x}px`,top:`${vocabMenu.y}px`}" @click.stop>
      <button @click="addSelectedVocabulary">加入单词本</button>
      <button @click="copySelectedTerm">复制所选内容</button>
    </div>
    <div v-if="vocabularyToast" class="toast vocabulary-toast">{{ vocabularyToast }}</div>
    <section
      v-if="resultPanelVisible && session"
      class="result-overlay"
      role="dialog"
      aria-modal="true"
      aria-labelledby="practice-result-title"
      @click.self="resultPanelVisible=false"
    >
      <div class="result-dialog card">
        <button
          class="result-dialog-close"
          type="button"
          aria-label="关闭成绩面板"
          @click="resultPanelVisible=false"
        >
          <X :size="19" />
        </button>

        <template v-if="resultPanelMode === 'unit' && resultUnit?.submission?.submitted">
          <div class="result-dialog-heading">
            <span class="result-icon"><CheckCircle2 :size="27" /></span>
            <div>
              <span class="eyebrow">UNIT COMPLETE</span>
              <h2 id="practice-result-title">{{ resultUnit.title }}已提交</h2>
              <p>本篇成绩已保存，其他篇目仍可继续作答。</p>
            </div>
          </div>
          <div class="result-score-hero">
            <span>本篇得分</span>
            <strong>
              {{ formatScore(resultUnit.submission.score) }}
              <small>/ {{ formatScore(resultUnit.submission.max_score) }}</small>
            </strong>
          </div>
          <div class="result-stat-grid">
            <div class="result-stat correct">
              <span>答对</span>
              <strong>{{ resultUnit.submission.correct_count ?? 0 }}</strong>
              <small>共 {{ resultUnit.submission.question_count ?? resultUnit.questions.length }} 题</small>
            </div>
            <div class="result-stat wrong">
              <span>答错</span>
              <strong>{{ resultUnit.submission.wrong_count ?? 0 }}</strong>
              <small>{{ resultUnit.submission.wrong_count ? '已自动加入错题本' : '本篇全部答对' }}</small>
            </div>
          </div>
          <div class="result-dialog-actions">
            <button class="button secondary" type="button" @click="resultPanelVisible=false">
              查看本篇对错
            </button>
            <button
              v-if="hasPendingUnits"
              class="button"
              type="button"
              @click="continueToPendingUnit(resultUnit.id)"
            >
              继续下一篇<ChevronRight :size="16" />
            </button>
            <button v-else class="button" type="button" @click="resultPanelVisible=false">
              完成
            </button>
          </div>
        </template>

        <template v-else-if="overallResult">
          <div class="result-dialog-heading">
            <span class="result-icon paper"><Award :size="29" /></span>
            <div>
              <span class="eyebrow">PAPER COMPLETE</span>
              <h2 id="practice-result-title">{{ session.units[0]?.year || '' }} 年整卷成绩</h2>
              <p>所有客观题已判分，各篇成绩如下。</p>
            </div>
          </div>
          <div class="paper-result-overview">
            <div class="result-score-hero paper">
              <span>整卷总分</span>
              <strong>
                {{ formatScore(overallResult.score) }}
                <small>/ {{ formatScore(overallResult.max_score) }}</small>
              </strong>
            </div>
            <div class="paper-result-counts">
              <span><b>{{ overallResult.correct_count }}</b> 题答对</span>
              <span><b>{{ overallResult.wrong_count }}</b> 题答错</span>
              <span>共 {{ overallResult.question_count }} 题</span>
            </div>
          </div>
          <div class="unit-result-list" aria-label="各篇成绩">
            <button
              v-for="unit in session.units"
              :key="unit.id"
              type="button"
              class="unit-result-row"
              @click="openUnitFromResult(unit.id)"
            >
              <span class="unit-result-title">
                <small>第 {{ unit.sequence }} 篇</small>
                <strong>{{ unit.title }}</strong>
              </span>
              <span class="unit-result-numbers">
                <strong>{{ formatScore(unit.submission.score) }} / {{ formatScore(unit.submission.max_score) }}</strong>
                <small>错 {{ unit.submission.wrong_count ?? 0 }} 题</small>
              </span>
              <ChevronRight :size="17" />
            </button>
          </div>
          <div class="result-dialog-actions">
            <button class="button secondary" type="button" @click="router.push('/wrong')">
              查看错题本
            </button>
            <button class="button" type="button" @click="resultPanelVisible=false">
              返回试卷
            </button>
          </div>
        </template>
      </div>
    </section>
    <section v-if="timerPromptVisible" class="timer-overlay" role="dialog" aria-modal="true" aria-labelledby="timer-choice-title">
      <div class="timer-dialog card">
        <span class="timer-dialog-icon"><Clock3 :size="30" /></span>
        <span class="eyebrow">FOCUS TIMER</span>
        <h2 id="timer-choice-title">这次练习要计时吗？</h2>
        <p class="lead">计时能帮助你了解自己的答题节奏。中途可以点击“休息一下”，暂停期间不会计入用时。</p>
        <div class="timer-dialog-actions">
          <button class="button" type="button" @click="startTimedPractice">
            <Clock3 :size="17" />开始计时
          </button>
          <button class="button secondary" type="button" @click="startWithoutTimer">暂不计时</button>
        </div>
        <small>本次选择只对当前练习生效。</small>
      </div>
    </section>
    <section v-if="timerState?.mode === 'paused' && timerState?.pauseReason === 'manual' && session?.status === 'active'" class="timer-overlay timer-pause-overlay" role="dialog" aria-modal="true" aria-labelledby="timer-pause-title">
      <div class="timer-dialog pause-dialog card">
        <span class="timer-dialog-icon rest"><Coffee :size="30" /></span>
        <span class="eyebrow">TAKE A BREATH</span>
        <h2 id="timer-pause-title">计时已暂停</h2>
        <div class="paused-time">{{ timerText }}</div>
        <p class="lead">活动一下肩颈、喝口水。准备好后再继续，休息时间不会计入练习用时。</p>
        <div class="timer-dialog-actions">
          <button class="button" type="button" @click="resumeTimer"><Play :size="17" />继续练习</button>
          <button class="button secondary" type="button" @click="router.push('/library')">退出练习</button>
        </div>
      </div>
    </section>
  </div>
</template>
