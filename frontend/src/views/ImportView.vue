<script setup lang="ts">
import {
  Check,
  Download,
  FileArchive,
  FileCheck2,
  FileKey2,
  FileUp,
  LibraryBig,
  Lock,
  Pause,
  Play,
  RefreshCw,
  Save,
  Search,
  Settings,
  Sparkles,
  Trash2,
} from 'lucide-vue-next'
import { computed, nextTick, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { api, del, get, patch, post, put } from '../api'
import QuestionBankSwitcher from '../components/QuestionBankSwitcher.vue'
import { useConfirm } from '../composables/useConfirm'
import { loadQuestionBankProfiles, questionBankProfilesState } from '../services/questionBankProfiles'

const confirm = useConfirm()
import {
  type LabelScope,
  type LabelStatus,
  loadQuestionLabelingStatus,
  pauseQuestionLabeling,
  questionLabelingState,
  startQuestionLabeling,
} from '../services/questionLabeling'

type QuestionLabel = {
  question_id: number
  number: number
  year: number
  unit_title: string
  primary_skill: string
  secondary_skills: string[]
  trap_types: string[]
  attention_points: string[]
  vocabulary_demand: 'low' | 'medium' | 'high'
  context_dependency: 'low' | 'medium' | 'high'
  grammar_dependency: 'low' | 'medium' | 'high'
  confidence: number
  locked: boolean
  user_edited: boolean
  model_name: string
  updated_at: string
}

const router = useRouter()
const jobs = ref<any[]>([])
const current = ref<any>(null)
const selectedFile = ref<File | null>(null)
const selectedAnswerFiles = ref<File[]>([])
const selectedAudioFiles = ref<File[]>([])
const useModelAssist = ref(true)
const modelAssistRewrite = ref(false)
const importConfirmOpen = ref(false)
const assistDialogOpen = ref(false)
const assistError = ref('')
const assistBusy = ref(false)
const showModelSelector = ref(false)
const selectorModels = ref<any[]>([])
const selectedModelKey = ref('')
const bulkAnswers = ref<Record<string, string>>({})
const busy = ref(false)
const uploadStage = ref('')
const uploadElapsedSeconds = ref(0)
const error = ref('')
const notice = ref('')
const aiInstructions = ref('')
const aiSuggestion = ref<any>(null)
const expandedEditorUnits = ref<Record<string, boolean>>({})
const esqJobs = ref<any[]>([])
const esqCurrent = ref<any>(null)
const selectedEsqFile = ref<File | null>(null)
const esqResolutions = ref<Record<string, 'keep_existing' | 'replace_with_imported'>>({})
const labelScopeMode = ref('all')
const importLabelScope = ref<LabelScope | null>(null)
const overwriteUnlocked = ref(false)
const labelManagerOpen = ref(false)
const labelRows = ref<QuestionLabel[]>([])
const labelSearch = ref('')
const editingLabel = ref<QuestionLabel | null>(null)
const labelBusyQuestionId = ref<number | null>(null)
const labelPromptOpen = ref(false)
const labelPromptScope = ref<LabelScope | null>(null)
const labelPromptStatus = ref<LabelStatus | null>(null)
const labelPromptModel = ref('')
const labelPromptHasModel = ref(false)
const labelPromptBusy = ref(false)
const labelPromptError = ref('')
const labelLaterButton = ref<HTMLButtonElement | null>(null)
const targetProfileId = ref(0)
const answerUnits = computed(() => current.value?.draft?.units || [])
const answerProgress = computed(() => {
  const questions = answerUnits.value.flatMap((unit: any) => unit.questions || [])
  return {
    completed: questions.filter((question: any) => String(current.value?.draft?.answers?.[question.number] || '').trim()).length,
    total: questions.length,
  }
})

async function loadJobs() { jobs.value = await get('/imports') }
async function loadEsqJobs() { esqJobs.value = await get('/question-banks/imports') }

function allLabelScope(): LabelScope {
  return { kind: 'all', title: '全部题库', year: null, paperIds: [] }
}

function selectedLabelScope(): LabelScope {
  if (labelScopeMode.value === 'current' && importLabelScope.value) {
    return importLabelScope.value
  }
  if (labelScopeMode.value.startsWith('year:')) {
    const year = Number(labelScopeMode.value.slice(5))
    return { kind: 'year', title: `${year} 年题库`, year, paperIds: [] }
  }
  return allLabelScope()
}

function labelScopeQuery(scope: LabelScope) {
  const query = new URLSearchParams()
  if (scope.year !== null) query.set('year', String(scope.year))
  if (scope.paperIds.length) query.set('paper_ids', scope.paperIds.join(','))
  return query
}

async function loadSelectedLabelStatus() {
  try {
    await loadQuestionLabelingStatus(selectedLabelScope())
  } catch (cause) {
    questionLabelingState.error = `读取标注进度失败：${String(cause)}`
  }
}

function setImportLabelScope(scope: LabelScope) {
  importLabelScope.value = scope
  labelScopeMode.value = 'current'
}

async function prepareLabelPrompt(scope: LabelScope) {
  if (questionLabelingState.isRunning || questionLabelingState.isPausing) {
    notice.value = `“${questionLabelingState.scope?.title || '当前题库'}”正在智能标注，请等待完成或暂停后再启动其他范围。`
    return
  }
  setImportLabelScope(scope)
  labelPromptScope.value = scope
  labelPromptOpen.value = true
  labelPromptBusy.value = true
  labelPromptError.value = ''
  labelPromptModel.value = ''
  labelPromptHasModel.value = false
  await nextTick()
  labelLaterButton.value?.focus()
  try {
    const [status, profiles] = await Promise.all([
      loadQuestionLabelingStatus(scope),
      get<any[]>('/ai/profiles'),
    ])
    const defaultProfile = profiles.find(profile => profile.is_default && profile.enabled)
    const defaultModel = String(defaultProfile?.default_model || '').trim()
    labelPromptStatus.value = status
    labelPromptHasModel.value = Boolean(defaultProfile && defaultModel)
    labelPromptModel.value = labelPromptHasModel.value
      ? `${defaultProfile.name} / ${defaultModel}`
      : ''
  } catch (cause) {
    labelPromptError.value = String(cause)
  } finally {
    labelPromptBusy.value = false
  }
}

async function beginPromptedLabeling() {
  const scope = labelPromptScope.value
  if (!scope) return
  if (!labelPromptHasModel.value) {
    labelPromptOpen.value = false
    await router.push('/settings')
    return
  }
  if (!labelPromptStatus.value?.remaining && !overwriteUnlocked.value) {
    labelPromptOpen.value = false
    notice.value = `${scope.title}已有完整智能标签，无需重复调用模型`
    return
  }
  labelPromptOpen.value = false
  void startQuestionLabeling(scope, overwriteUnlocked.value)
}

function closeLabelPrompt() {
  labelPromptOpen.value = false
}

function paperIdsFromJob(job: any) {
  return (job?.published_paper_ids || [])
    .map((value: unknown) => Number(value))
    .filter((value: number) => Number.isInteger(value) && value > 0)
}

async function promptLabelingForJob(job: any) {
  const paperIds = paperIdsFromJob(job)
  if (!paperIds.length) return
  await prepareLabelPrompt({
    kind: 'papers',
    title: job.published_scope_title || job.filename || '本次导入题库',
    year: null,
    paperIds,
  })
}

async function loadQuestionLabels() {
  const query = labelScopeQuery(selectedLabelScope())
  if (labelSearch.value.trim()) query.set('search', labelSearch.value.trim())
  query.set('limit', '120')
  try {
    labelRows.value = await get<QuestionLabel[]>(`/ai/question-labels?${query}`)
    labelManagerOpen.value = true
  } catch (cause) {
    questionLabelingState.error = String(cause)
  }
}

function editLabel(row: QuestionLabel) {
  editingLabel.value = {
    ...row,
    locked: true,
    secondary_skills: [...(row.secondary_skills || [])],
    trap_types: [...(row.trap_types || [])],
    attention_points: [...(row.attention_points || [])],
  }
}

function splitTags(value: string) {
  return value.split(/[，,；;\n]/).map(item => item.trim()).filter(Boolean)
}

async function saveQuestionLabel() {
  const row = editingLabel.value
  if (!row) return
  labelBusyQuestionId.value = row.question_id
  try {
    await put(`/ai/question-labels/${row.question_id}`, {
      primary_skill: row.primary_skill,
      secondary_skills: row.secondary_skills,
      trap_types: row.trap_types,
      attention_points: row.attention_points,
      vocabulary_demand: row.vocabulary_demand,
      context_dependency: row.context_dependency,
      grammar_dependency: row.grammar_dependency,
      confidence: row.confidence,
      locked: row.locked,
    })
    questionLabelingState.message = `已保存并${row.locked ? '锁定' : '解除锁定'} ${row.year} 年第 ${row.number} 题标签`
    editingLabel.value = null
    await Promise.all([loadQuestionLabels(), loadSelectedLabelStatus()])
  } catch (cause) {
    questionLabelingState.error = String(cause)
  } finally {
    labelBusyQuestionId.value = null
  }
}

onMounted(async () => {
  try {
    await loadQuestionBankProfiles()
    targetProfileId.value = questionBankProfilesState.activeId
    await Promise.all([loadJobs(), loadEsqJobs()])
    if (questionLabelingState.scope?.kind === 'papers') {
      setImportLabelScope(questionLabelingState.scope)
    } else if (questionLabelingState.scope?.kind === 'year') {
      labelScopeMode.value = `year:${questionLabelingState.scope.year}`
    }
    if (!questionLabelingState.status) {
      await loadSelectedLabelStatus()
    }
  } catch (cause) {
    error.value = String(cause)
  }
})

async function handleProfileChanged() {
  targetProfileId.value = questionBankProfilesState.activeId
  current.value = null
  esqCurrent.value = null
  await Promise.all([loadJobs(), loadEsqJobs()])
}

async function upload() {
  if (!selectedFile.value) return
  if (!importConfirmOpen.value) {
    importConfirmOpen.value = true
    return
  }
  importConfirmOpen.value = false
  busy.value = true; error.value = ''; notice.value = ''
  uploadStage.value = '正在上传并解析 Word 与答案附件'
  uploadElapsedSeconds.value = 0
  const uploadTimer = window.setInterval(() => { uploadElapsedSeconds.value += 1 }, 1000)
  const form = new FormData(); form.append('file', selectedFile.value)
  form.append('profile_id', String(targetProfileId.value))
  selectedAnswerFiles.value.forEach(file => form.append('answer_files', file))
  selectedAudioFiles.value.forEach(file => form.append('audio_files', file))
  form.append('use_model_assist', useModelAssist.value ? 'true' : 'false')
  form.append('model_assist_correct_structure', modelAssistRewrite.value ? 'true' : 'false')
  form.append('defer_model_assist', useModelAssist.value ? 'true' : 'false')
  try {
    current.value = await api('/imports', { method: 'POST', body: form })
    const splitJobs: any[] = current.value.split_jobs?.length
      ? current.value.split_jobs : [current.value]
    let assist = current.value.model_assist
    if (useModelAssist.value) {
      let completed = 0
      for (const job of splitJobs) {
        if (job.has_objective_questions === false) continue
        uploadStage.value = splitJobs.length > 1
          ? `已拆分 ${splitJobs.length} 套，正在校对第 ${job.paper_index || completed + 1} 套`
          : '本地草稿已建立，正在调用模型辅助校对'
        const result: any = await post(`/imports/${job.id}/model-assist`, {
          profile_id: null,
          model: '',
          correct_structure: modelAssistRewrite.value,
        })
        job.draft = result.draft
        job.warnings = result.warnings
        job.model_assist = result.model_assist
        completed += 1
        if (job.id === current.value.id) {
          current.value.draft = result.draft
          current.value.warnings = result.warnings
          current.value.model_assist = result.model_assist
          assist = result.model_assist
        }
      }
    }
    if (assist?.status === 'failed') {
      assistError.value = assist.error || '未知错误'
      assistDialogOpen.value = true
      showModelSelector.value = false
    } else if (assist?.status === 'applied') {
      const firstPaperNotice = current.value.ignored_paper_count > 0
        ? `文档共检测到 ${current.value.detected_paper_count} 套，仅导入第 1 套，其余 ${current.value.ignored_paper_count} 套已忽略。`
        : ''
      notice.value = `${firstPaperNotice}模型辅助解析完成：核对 ${assist.applied_answers} 道答案，发现 ${assist.issue_count} 个结构问题，请核对后发布`
    } else {
      notice.value = current.value.ignored_paper_count > 0
        ? `文档共检测到 ${current.value.detected_paper_count} 套；本地仅生成第 1 套草稿，其余 ${current.value.ignored_paper_count} 套已忽略`
        : '本地解析完成；本次未请求模型辅助校对'
    }
    bulkAnswers.value = {}
    await loadJobs()
  } catch (e) { error.value = String(e) }
  finally {
    window.clearInterval(uploadTimer)
    uploadStage.value = ''
    busy.value = false
  }
}

async function openModelSelector() {
  assistBusy.value = true
  try {
    const result: any = await get('/ai/selector-models')
    selectorModels.value = result?.models || []
    selectedModelKey.value = ''
  } catch (e) { assistError.value = String(e) }
  finally { assistBusy.value = false }
}

async function retryAssist() {
  const [profileId, modelId] = String(selectedModelKey.value).split('|')
  if (!current.value?.id || !profileId || !modelId) return
  assistBusy.value = true
  error.value = ''
  try {
    const result: any = await post(`/imports/${current.value.id}/model-assist`, {
      profile_id: Number(profileId),
      model: modelId,
      correct_structure: modelAssistRewrite.value,
    })
    if (result.model_assist?.status === 'failed') {
      assistError.value = result.model_assist.error || '重试失败'
      return
    }
    current.value.draft = result.draft
    current.value.warnings = result.warnings
    assistDialogOpen.value = false
    notice.value = `模型辅助解析完成：应用 ${result.model_assist.applied_answers} 道答案，请核对后发布`
  } catch (e) { error.value = String(e) }
  finally { assistBusy.value = false }
}

async function openJob(id: number) {
  esqCurrent.value = null
  current.value = await get(`/imports/${id}`)
  current.value.draft = current.value.draft_data
  bulkAnswers.value = {}
}

async function saveDraft() {
  const result: any = await put(`/imports/${current.value.id}`, { draft_data: current.value.draft, reason: '用户编辑' })
  current.value.draft = result.draft; notice.value = '草稿已保存'
}

function setAnswer(question: any, answer: string) {
  if (!current.value?.draft) return
  current.value.draft.answers ||= {}
  current.value.draft.answer_sources ||= {}
  current.value.draft.answers[String(question.number)] = answer
  question.answer = answer
  if (answer) current.value.draft.answer_sources[String(question.number)] = '人工录入'
  else delete current.value.draft.answer_sources[String(question.number)]
}

function setDraftField(field: string, value: string) {
  if (!current.value?.draft) return
  current.value.draft[field] = field === 'year' ? (value ? Number(value) : null) : value
}

function setUnitField(unit: any, field: string, value: string) {
  if (field === 'directions') {
    unit.shared_data ||= {}
    unit.shared_data.directions = value
    return
  }
  unit[field] = value
}

function toggleEditorUnit(unit: any) {
  const key = String(unit.sequence || unit.title)
  expandedEditorUnits.value[key] = !expandedEditorUnits.value[key]
}

function isEditorUnitOpen(unit: any) {
  const key = String(unit.sequence || unit.title)
  return expandedEditorUnits.value[key] ?? false
}

function updateOption(question: any, option: any, value: string) {
  option.content = value
  if (current.value?.draft?.answers?.[question.number] === option.key) {
    question.answer = option.key
  }
}

function updateCandidate(unit: any, key: string, value: string) {
  unit.shared_data ||= {}
  unit.shared_data.candidates ||= {}
  unit.shared_data.candidates[key] = value
}

function applyBulkAnswers(unit: any) {
  const letters = String(bulkAnswers.value[unit.title] || '').toUpperCase().match(/[A-OT]/g) || []
  if (letters.length !== unit.questions.length) {
    error.value = `${unit.title} 需要输入 ${unit.questions.length} 个答案，当前识别到 ${letters.length} 个`
    return
  }
  unit.questions.forEach((question: any, index: number) => setAnswer(question, letters[index]))
  error.value = ''
  notice.value = `${unit.title} 的答案已填入草稿，点击“保存答案”后生效`
}

async function saveAnswers() {
  if (!current.value?.draft) return
  busy.value = true; error.value = ''
  try {
    const result: any = await patch(`/imports/${current.value.id}/answers`, {
      answers: current.value.draft.answers || {},
      reason: '答案校对面板人工录入',
    })
    current.value.draft = result.draft
    notice.value = `标准答案已保存（${answerProgress.value.completed}/${answerProgress.value.total}）`
  } catch (e) { error.value = String(e) }
  finally { busy.value = false }
}

async function askAi() {
  busy.value = true
  try {
    aiSuggestion.value = await post(`/ai/imports/${current.value.id}/suggest-correction`, { scope: 'all', instructions: aiInstructions.value })
  } catch (e) { error.value = String(e) } finally { busy.value = false }
}

async function acceptAi() {
  if (aiSuggestion.value.requires_answer_confirmation) {
    const changes = aiSuggestion.value.answer_changes
      .map((change: any) => `第${change.number}题：${change.old} → ${change.new}`)
      .join('\n')
    const typed = prompt(`模型建议修改标准答案：\n${changes}\n\n答案修改会影响判分。若已逐项核对，请输入“确认修改答案”：`)
    if (typed !== '确认修改答案') return
  }
  current.value.draft = aiSuggestion.value.suggested_draft
  await saveDraft(); aiSuggestion.value = null
}

async function publish() {
  busy.value = true
  error.value = ''
  try {
    if (current.value.draft.warnings?.length) return
    const ok = await confirm({
      title: '发布题库？',
      message: `确认发布 ${current.value.draft.year} 年题库吗？发布后模型不能直接修改正式题库。`,
      confirmText: '发布',
    })
    if (!ok) return
    const result: any = await post(`/imports/${current.value.id}/publish`)
    notice.value = '题库已正式发布'
    await loadJobs()
    await openJob(current.value.id)
    const paperIds = result.paper_ids || (result.paper_id ? [result.paper_id] : [])
    if (paperIds.length) {
      await prepareLabelPrompt({
        kind: 'papers',
        title: result.scope_title || current.value.draft.title || `${current.value.draft.year} 年题库`,
        year: null,
        paperIds,
      })
    }
  } catch (e) { error.value = String(e) }
  finally { busy.value = false }
}

async function uploadEsq() {
  if (!selectedEsqFile.value) return
  busy.value = true; error.value = ''
  const form = new FormData(); form.append('file', selectedEsqFile.value)
  form.append('profile_id', String(targetProfileId.value))
  try {
    const result: any = await api('/question-banks/imports', { method: 'POST', body: form })
    esqCurrent.value = await get(`/question-banks/imports/${result.id}`)
    esqResolutions.value = {}
    await loadEsqJobs()
    notice.value = 'ESQ 题库包已完成校验，请检查冲突后发布'
  } catch (e) { error.value = String(e) }
  finally { busy.value = false }
}

async function removeImportJob(job: any, esq = false) {
  const ok = await confirm({
    title: '移入回收站？',
    message: `将未完成导入“${job.filename}”及原始文件移入回收站？`,
    confirmText: '移入回收站',
    danger: true,
  })
  if (!ok) return
  try {
    await del(`${esq ? '/question-banks/imports' : '/imports'}/${job.id}`)
    if (esq) {
      if (esqCurrent.value?.id === job.id) esqCurrent.value = null
      await loadEsqJobs()
    } else {
      if (current.value?.id === job.id) current.value = null
      await loadJobs()
    }
  } catch (cause) {
    error.value = String(cause)
  }
}

async function openEsqJob(id: number) {
  current.value = null
  esqCurrent.value = await get(`/question-banks/imports/${id}`)
  esqResolutions.value = {}
}

function conflictAction(paperKey: string, action: 'keep_existing' | 'replace_with_imported') {
  esqResolutions.value = { ...esqResolutions.value, [paperKey]: action }
}

async function publishEsq() {
  if (!esqCurrent.value) return
  const conflicts = esqCurrent.value.preview?.conflicts?.filter((item: any) => item.existing) || []
  const missing = conflicts.filter((item: any) => !esqResolutions.value[item.paperKey])
  if (missing.length) {
    error.value = `请先决定冲突题库的处理方式：${missing.map((item: any) => item.year).join('、')} 年`
    return
  }
  busy.value = true; error.value = ''
  try {
    const resolutions = Object.entries(esqResolutions.value).map(([paper_key, action]) => ({ paper_key, action }))
    const result: any = await post(`/question-banks/imports/${esqCurrent.value.id}/publish`, {
      resolutions,
      import_ai_labels: true,
    })
    notice.value = 'ESQ 题库已发布'
    await loadEsqJobs()
    await openEsqJob(esqCurrent.value.id)
    if (result.paperIds?.length) {
      await prepareLabelPrompt({
        kind: 'papers',
        title: result.scopeTitle || esqCurrent.value.preview?.title || '本次 ESQ 题库',
        year: null,
        paperIds: result.paperIds,
      })
    } else {
      notice.value = 'ESQ 题库已发布；本次选择保留的题库无需重新标注'
    }
  } catch (e) { error.value = String(e) }
  finally { busy.value = false }
}

async function exportEsq(includeLabels = false) {
  busy.value = true; error.value = ''
  try {
    const response = await fetch(`/api/question-banks/export?include_answers=true&include_labels=${includeLabels}`)
    if (!response.ok) throw new Error(`${response.status} ${response.statusText}`)
    const blob = await response.blob()
    const url = URL.createObjectURL(blob)
    const anchor = document.createElement('a')
    anchor.href = url
    anchor.download = 'english-practice-question-bank.esq'
    anchor.click()
    URL.revokeObjectURL(url)
    notice.value = '题库包已导出'
  } catch (e) { error.value = String(e) }
  finally { busy.value = false }
}
</script>

<template>
  <div class="page">
    <div class="page-head"><div><span class="eyebrow">IMPORT & REVIEW</span><h1>导入题库</h1><p class="lead">试卷和答案分别解析。即使答案缺失，也可以先保存题目草稿，再人工补全。</p></div></div>
    <QuestionBankSwitcher @changed="handleProfileChanged" />
    <div v-if="error" class="warning" role="alert">{{ error }}</div><div v-if="notice" class="card" style="margin-bottom:16px;color:var(--success)">{{ notice }}</div>

    <section class="question-label-workspace card" aria-labelledby="question-label-title">
      <div class="question-label-heading">
        <span class="api-profile-icon"><LibraryBig :size="21" /></span>
        <div>
          <span class="eyebrow">QUESTION INTELLIGENCE</span>
          <h2 id="question-label-title">题库智能标注</h2>
          <p>题库批准入库后再启动模型标注，避免草稿被提前消费 Token。默认只处理刚刚入库的试卷，已锁定的人工标签不会被覆盖。</p>
        </div>
      </div>
      <div class="question-label-controls import-label-controls">
        <div class="field">
          <label for="label-scope">标注范围</label>
          <select id="label-scope" v-model="labelScopeMode" :disabled="questionLabelingState.isRunning" @change="loadSelectedLabelStatus">
            <option value="all">全部未标注题目</option>
            <option v-if="importLabelScope" value="current">本次入库：{{ importLabelScope.title }}</option>
            <option v-for="year in questionLabelingState.status?.years || []" :key="year" :value="`year:${year}`">{{ year }} 年未标注题目</option>
          </select>
        </div>
        <label class="default-profile-check label-overwrite">
          <input v-model="overwriteUnlocked" type="checkbox" :disabled="questionLabelingState.isRunning">
          重新标注未锁定题目
        </label>
        <div class="question-label-actions">
          <button v-if="!questionLabelingState.isRunning" class="button" type="button" @click="prepareLabelPrompt(selectedLabelScope())">
            <Play :size="16" />开始标注
          </button>
          <button v-else class="button secondary" type="button" :disabled="questionLabelingState.isPausing" @click="pauseQuestionLabeling">
            <Pause :size="16" />{{ questionLabelingState.isPausing ? '正在暂停…' : '暂停' }}
          </button>
          <button class="button secondary" type="button" :disabled="questionLabelingState.isRunning" @click="loadQuestionLabels">
            <Search :size="16" />查看与校正
          </button>
        </div>
      </div>
      <div v-if="questionLabelingState.status" class="question-label-progress">
        <div>
          <span>已标注 {{ questionLabelingState.status.labeled }} / {{ questionLabelingState.status.total }} 道</span>
          <strong>{{ questionLabelingState.status.percentage }}%</strong>
        </div>
        <div class="question-label-track" role="progressbar" :aria-valuenow="questionLabelingState.status.percentage" aria-valuemin="0" aria-valuemax="100">
          <span :style="{ width: `${questionLabelingState.status.percentage}%` }" />
        </div>
        <small>{{ questionLabelingState.status.locked }} 道标签已锁定；人工校正后会默认锁定，不会被批量任务覆盖。</small>
      </div>
      <p v-if="questionLabelingState.message" class="api-profile-notice" role="status">{{ questionLabelingState.message }}</p>
      <p v-if="questionLabelingState.error" class="warning" role="alert">{{ questionLabelingState.error }}</p>
      <div v-if="labelManagerOpen" class="question-label-manager">
        <div class="question-label-filter">
          <div class="field">
            <label for="label-search">搜索标签</label>
            <input id="label-search" v-model="labelSearch" placeholder="篇目、题号或主要考点" @keyup.enter="loadQuestionLabels">
          </div>
          <button class="button secondary compact" type="button" @click="loadQuestionLabels"><Search :size="15" />搜索</button>
        </div>
        <div v-if="labelRows.length" class="question-label-list">
          <button v-for="row in labelRows" :key="row.question_id" type="button" class="question-label-row" :class="{ unlabeled: !row.primary_skill }" @click="editLabel(row)">
            <span><strong>{{ row.year }} 年 · {{ row.unit_title }}</strong><small>第 {{ row.number }} 题</small></span>
            <span>{{ row.primary_skill || '尚未标注' }}</span>
            <span class="question-label-state"><Lock v-if="row.locked" :size="13" />{{ row.locked ? '已锁定' : '可更新' }}</span>
          </button>
        </div>
        <div v-else class="api-model-empty">当前范围没有符合条件的题目。</div>
      </div>
    </section>

    <div class="grid" style="grid-template-columns:320px 1fr">
      <aside>
        <div class="card">
          <label class="field">
            <span>导入到题库配置</span>
            <select v-model.number="targetProfileId">
              <option v-for="profile in questionBankProfilesState.items" :key="profile.id" :value="profile.id">{{ profile.name }}</option>
            </select>
          </label>
          <label class="field"><span>试卷 Word / 文本型 PDF（必选）</span><input type="file" accept=".doc,.docx,.pdf" @change="selectedFile=($event.target as HTMLInputElement).files?.[0] || null"></label>
          <label class="field"><span>答案附件（可多选）</span><input type="file" accept=".doc,.docx,.pdf" multiple @change="selectedAnswerFiles=Array.from(($event.target as HTMLInputElement).files || [])"><small v-if="selectedAnswerFiles.length">已选择 {{ selectedAnswerFiles.length }} 份答案附件</small></label>
          <label class="field"><span>听力音频（可多选，支持 MP3 / M4A / WAV / OGG）</span><input type="file" accept=".mp3,.m4a,.wav,.ogg,audio/mpeg,audio/mp4,audio/wav,audio/ogg" multiple @change="selectedAudioFiles=Array.from(($event.target as HTMLInputElement).files || [])"><small v-if="selectedAudioFiles.length">已选择 {{ selectedAudioFiles.length }} 个音频文件</small></label>
          <label class="import-assist-toggle">
            <input v-model="useModelAssist" type="checkbox">
            <span>上传解析时用模型辅助定位题目与对应答案（默认开启）</span>
          </label>
          <label class="import-assist-toggle">
            <input v-model="modelAssistRewrite" type="checkbox" :disabled="!useModelAssist">
            <span>允许模型直接修正题干与选项归属（默认关闭，风险较高）</span>
          </label>
          <p class="lead import-file-hint">支持 DOC、DOCX 和文本型 PDF。扫描版或水印干扰严重的 PDF 会回退到人工录入。</p>
          <p v-if="useModelAssist" class="lead import-file-hint">本地解析完成后会自动调用默认模型核对答案，可能需要 30 秒以上。</p>
          <button class="button" style="width:100%" :disabled="!selectedFile || busy" @click="upload"><FileUp :size="16" />{{ busy ? '正在分析…' : '上传并解析' }}</button>
          <p v-if="busy && uploadStage" class="lead import-file-hint import-progress">{{ uploadStage }} · {{ uploadElapsedSeconds }} 秒</p>
        </div>
        <div class="card">
          <label class="field"><span>导入 ESQ 共享题库</span><input type="file" accept=".esq,.zip" @change="selectedEsqFile=($event.target as HTMLInputElement).files?.[0] || null"></label>
          <button class="button secondary" style="width:100%" :disabled="!selectedEsqFile || busy" @click="uploadEsq"><FileArchive :size="16" />{{ busy ? '正在校验…' : '上传 ESQ 题库包' }}</button>
          <div class="lead" style="font-size:12px;margin-top:10px">题库包会先进入预览，不会自动覆盖本地题库。</div>
        </div>
        <div class="card">
          <div style="display:flex;justify-content:space-between;align-items:center"><h3>题库包导出</h3><Download :size="18" /></div>
          <p class="lead" style="font-size:12px;margin:10px 0 14px">默认导出全部正式题库和标准答案，不包含做题记录、单词本和 API 配置。</p>
          <div style="display:flex;gap:8px;flex-wrap:wrap">
            <button class="button secondary compact" :disabled="busy" @click="exportEsq(false)"><Download :size="15" />导出题库</button>
            <button class="button ghost compact" :disabled="busy" @click="exportEsq(true)"><Sparkles :size="15" />含 AI 标签</button>
          </div>
        </div>
        <div v-if="esqJobs.length">
          <div class="section-title"><h3>ESQ 导入记录</h3><button class="button ghost compact" @click="loadEsqJobs"><RefreshCw :size="14" />刷新</button></div>
          <div v-for="job in esqJobs" :key="job.id" class="card import-history-card">
            <button type="button" class="import-history-open" @click="openEsqJob(job.id)"><span><strong>{{ job.detected_year || '多年份' }}</strong><small>{{ job.filename }}</small></span><span v-if="job.published_paper_ids?.length" class="pill">已入库</span></button>
            <button v-if="job.published_paper_ids?.length" class="button ghost compact" type="button" @click="promptLabelingForJob(job)"><Sparkles :size="14" />开始智能标注</button>
            <button v-else class="button ghost danger compact" type="button" @click="removeImportJob(job, true)"><Trash2 :size="14" />删除草稿</button>
          </div>
        </div>
        <div class="section-title"><h3>导入记录</h3></div>
        <div v-for="job in jobs" :key="job.id" class="card import-history-card">
          <button type="button" class="import-history-open" @click="openJob(job.id)"><span><strong>{{ job.detected_year || '未知年份' }}</strong><small>{{ job.filename }}</small></span><span v-if="job.published_paper_ids?.length" class="pill">已入库</span></button>
          <button v-if="job.published_paper_ids?.length" class="button ghost compact" type="button" @click="promptLabelingForJob(job)"><Sparkles :size="14" />开始智能标注</button>
          <button v-else class="button ghost danger compact" type="button" @click="removeImportJob(job)"><Trash2 :size="14" />删除草稿</button>
        </div>
      </aside>
      <section v-if="esqCurrent?.preview" class="grid">
        <div class="card">
          <div style="display:flex;justify-content:space-between;align-items:center">
            <div><span class="pill">ESQ {{ esqCurrent.draft_data?.manifest?.schemaVersion || '1.0' }}</span><h2 style="margin-top:12px">{{ esqCurrent.preview.title }}</h2><p class="lead" style="font-size:12px;margin-top:7px">发布者：{{ esqCurrent.preview.publisher }} · {{ esqCurrent.preview.contentVersion }}</p></div>
            <button class="button" :disabled="busy" @click="publishEsq"><FileCheck2 :size="17" />发布题库包</button>
          </div>
          <div class="grid grid-4" style="margin-top:18px">
            <div class="stat-card card"><span class="stat-label">年份</span><strong>{{ esqCurrent.preview.totals.papers }}</strong></div>
            <div class="stat-card card"><span class="stat-label">篇目</span><strong>{{ esqCurrent.preview.totals.units }}</strong></div>
            <div class="stat-card card"><span class="stat-label">题目</span><strong>{{ esqCurrent.preview.totals.questions }}</strong></div>
            <div class="stat-card card"><span class="stat-label">资源</span><strong>{{ esqCurrent.preview.totals.assets }}</strong></div>
          </div>
        </div>
        <div class="card">
          <h3>冲突处理</h3>
          <p class="lead" style="font-size:12px;margin:8px 0 14px">本地已存在的年份必须明确选择，程序不会自动替换。</p>
          <div v-for="item in esqCurrent.preview.conflicts" :key="item.paperKey" class="api-model-row">
            <div><strong>{{ item.year }} 年</strong><div class="lead" style="font-size:12px">{{ item.title }}</div></div>
            <div v-if="item.existing" style="display:flex;gap:6px">
              <button class="button compact" :class="{secondary:esqResolutions[item.paperKey] !== 'replace_with_imported'}" @click="conflictAction(item.paperKey,'replace_with_imported')">替换</button>
              <button class="button compact" :class="{secondary:esqResolutions[item.paperKey] !== 'keep_existing'}" @click="conflictAction(item.paperKey,'keep_existing')">保留本地</button>
            </div>
            <span v-else class="pill">新增</span>
          </div>
        </div>
      </section>
      <section v-if="current?.draft" class="grid">
        <div class="card">
          <div style="display:flex;justify-content:space-between;align-items:center"><div><span class="pill">{{ current.draft.detected_format }}</span><h2 style="margin-top:12px">{{ current.draft.title }}</h2><p class="lead" style="font-size:12px;margin-top:7px">试卷来源：{{ current.draft.source_file }} · 答案来源：{{ current.draft.answer_source || '未提供' }}</p></div><button class="button" :disabled="current.draft.warnings?.length" @click="publish"><FileCheck2 :size="17" />批准入库</button></div>
          <div class="answer-status" :class="current.draft.answer_status?.status || 'missing'">
            <FileKey2 :size="18" />
            <div><strong>{{ answerProgress.completed }}/{{ answerProgress.total }} 道答案已填写</strong><span>{{ current.draft.answer_status?.message || '请在下方校对并补全标准答案' }}</span></div>
          </div>
          <div v-if="current.draft.import_diagnostics" class="import-diagnostics">
            <span>解析器 {{ current.draft.import_diagnostics.pipeline_revision }}</span>
            <span>答案附件：{{ current.draft.import_diagnostics.answer_file_received ? '已接收' : '未提供' }}</span>
            <span>本地答案 {{ current.draft.import_diagnostics.local_answer_count || 0 }} 道</span>
            <span v-if="current.draft.import_diagnostics.model_call_status === 'completed'">模型已调用 · {{ current.draft.import_diagnostics.model_name || '默认模型' }} · {{ Math.round((current.draft.import_diagnostics.model_call_elapsed_ms || 0) / 1000) }} 秒</span>
            <span v-else-if="current.draft.import_diagnostics.model_call_status === 'failed'">模型调用失败</span>
            <span v-else-if="current.draft.import_diagnostics.model_call_status === 'deferred'">等待模型校对</span>
            <span v-else>本次未调用模型</span>
          </div>
          <div v-if="current.draft.model_assist?.status === 'applied'" class="import-assist-banner">
            <Sparkles :size="17" />
            <div>
              <strong>模型辅助解析已应用</strong>
              <span>本次核对 {{ current.draft.model_assist.applied_answers }} 道答案（来源标注“模型辅助”）<template v-if="current.draft.model_assist.applied_number_fixes">，修正 {{ current.draft.model_assist.applied_number_fixes }} 个题号</template><template v-if="current.draft.model_assist.applied_fixes">，直接修正 {{ current.draft.model_assist.applied_fixes }} 处题干/选项</template>，共识别 {{ current.draft.model_assist.answer_total }} 道；发现 {{ current.draft.model_assist.issue_count }} 个结构问题，见下方警告。{{ current.draft.model_assist.notes || '' }}</span>
            </div>
          </div>
          <div v-for="warning in current.draft.warnings" class="warning" :key="warning">{{ warning }}</div>
        </div>
        <div class="card answer-editor">
          <div class="answer-editor-head">
            <div><h3>答案校对</h3><p class="lead">自动识别只负责预填。你可以修改单题答案，也可以按篇目粘贴答案串。</p></div>
            <button class="button" :disabled="busy" @click="saveAnswers"><Check :size="16" />保存答案</button>
          </div>
          <details v-for="unit in answerUnits" :key="unit.title" class="answer-unit" open>
            <summary>
              <span>{{ unit.title }}</span>
              <small>{{ unit.questions.filter((question:any) => current.draft.answers?.[question.number]).length }}/{{ unit.questions.length }}</small>
            </summary>
            <div class="bulk-answer-row">
              <label :for="`bulk-${unit.sequence}`">批量粘贴</label>
              <input :id="`bulk-${unit.sequence}`" v-model="bulkAnswers[unit.title]" :placeholder="`例如：${'A'.repeat(unit.questions.length)}`" @keyup.enter="applyBulkAnswers(unit)" />
              <button class="button secondary compact" @click="applyBulkAnswers(unit)">填入本篇</button>
            </div>
            <div class="answer-question-grid">
              <label v-for="question in unit.questions" :key="question.number" class="answer-question" :class="{missing:!current.draft.answers?.[question.number]}">
                <span>{{ question.number }}</span>
                <select :value="current.draft.answers?.[question.number] || ''" @change="setAnswer(question, ($event.target as HTMLSelectElement).value)">
                  <option value="">未填</option>
                  <option v-for="option in question.options" :key="option.key" :value="option.key">{{ option.key }}</option>
                </select>
              </label>
            </div>
          </details>
        </div>
        <div class="card">
          <h3>模型辅助校正</h3><p class="lead">模型只生成建议；正式应用前由你确认，答案变化会特别提示。</p>
          <div class="field" style="margin-top:15px"><textarea rows="3" v-model="aiInstructions" placeholder="例如：重点检查跨页断行和2024年阅读选项归属"></textarea></div>
          <button class="button secondary" :disabled="busy" @click="askAi"><Sparkles :size="16" />生成校正建议</button>
          <div v-if="aiSuggestion" style="margin-top:17px">
            <div class="lead">{{ aiSuggestion.summary }}</div>
            <div v-for="change in aiSuggestion.answer_changes" :key="change.number" class="warning">第{{ change.number }}题答案：{{ change.old }} → {{ change.new }}。{{ change.reason }}</div>
            <button class="button" @click="acceptAi">接受建议并保存草稿</button>
          </div>
        </div>
        <div class="card visual-draft-editor">
          <div class="visual-editor-head">
            <div>
              <span class="eyebrow">FIELD-BY-FIELD REVIEW</span>
              <h3>结构化草稿校对器</h3>
              <p class="lead">按试卷、篇目、题目和选项逐项检查。修改会直接写入当前草稿，最后点击“保存人工校正”。</p>
            </div>
            <button class="button" :disabled="busy" @click="saveDraft"><Check :size="16" />保存校正</button>
          </div>
          <div class="draft-field-grid">
            <label class="field"><span>年份</span><input :value="current.draft.year || ''" @input="setDraftField('year', ($event.target as HTMLInputElement).value)" /></label>
            <label class="field"><span>科目</span><input :value="current.draft.subject || ''" @input="setDraftField('subject', ($event.target as HTMLInputElement).value)" /></label>
            <label class="field draft-field-wide"><span>试卷标题</span><input :value="current.draft.title || ''" @input="setDraftField('title', ($event.target as HTMLInputElement).value)" /></label>
          </div>
          <div v-for="unit in answerUnits" :key="`editor-${unit.sequence}`" class="draft-unit">
            <button type="button" class="draft-unit-head" @click="toggleEditorUnit(unit)">
              <span><strong>{{ unit.title }}</strong><small>{{ unit.unit_type }} · {{ unit.questions.length }} 题</small></span>
              <span class="draft-unit-progress">{{ unit.questions.filter((question:any) => question.stem || question.options?.some((option:any) => option.content)).length }}/{{ unit.questions.length }} 已检查</span>
            </button>
            <div v-if="isEditorUnitOpen(unit)" class="draft-unit-body">
              <label class="field"><span>篇目说明 / 方向</span><textarea :value="unit.shared_data?.directions || ''" @input="setUnitField(unit, 'directions', ($event.target as HTMLTextAreaElement).value)" rows="2" placeholder="可选：填写题型说明或答题要求"></textarea></label>
              <label class="field" v-if="unit.unit_type !== 'part_b' || unit.subtype === 'true_false'"><span>文章正文</span><textarea :value="unit.passage || ''" @input="setUnitField(unit, 'passage', ($event.target as HTMLTextAreaElement).value)" rows="6" placeholder="检查段落、空位和断行"></textarea></label>
              <div v-if="unit.unit_type === 'part_b' && unit.subtype !== 'true_false'" class="draft-candidates">
                <span class="field-label">Part B 候选项</span>
                <label v-for="(candidate, key) in (unit.shared_data?.candidates || {})" :key="`candidate-${unit.sequence}-${key}`" class="field"><span>{{ key }} 候选段落</span><textarea :value="candidate" rows="3" @input="updateCandidate(unit, String(key), ($event.target as HTMLTextAreaElement).value)"></textarea></label>
              </div>
              <div v-for="question in unit.questions" :key="`editor-question-${question.number}`" class="draft-question">
                <div class="draft-question-head"><span class="draft-number">{{ question.number }}</span><span class="draft-answer-source">答案来源：{{ current.draft.answer_sources?.[question.number] || '未设置' }}</span></div>
                <label class="field"><span>题干</span><textarea v-model="question.stem" rows="2" placeholder="检查题干和题号归属"></textarea></label>
                <div class="draft-options-grid">
                  <label v-for="option in question.options" :key="`${question.number}-${option.key}`" class="field draft-option-field">
                    <span>{{ option.key }} 选项</span><textarea :value="option.content" rows="2" @input="updateOption(question, option, ($event.target as HTMLTextAreaElement).value)"></textarea>
                  </label>
                </div>
                <label class="field draft-answer-field"><span>标准答案</span><select :value="current.draft.answers?.[question.number] || ''" @change="setAnswer(question, ($event.target as HTMLSelectElement).value)"><option value="">未设置</option><option v-for="option in question.options" :key="`answer-${question.number}-${option.key}`" :value="option.key">{{ option.key }}</option></select></label>
              </div>
            </div>
          </div>
          <details class="advanced-json-editor">
            <summary>高级 JSON 入口（仅用于批量修复）</summary>
            <p class="lead">普通校对建议使用上方字段编辑器；JSON 入口保留给熟悉 ESQ 结构的用户。</p>
            <textarea :value="JSON.stringify(current.draft,null,2)" @change="current.draft=JSON.parse(($event.target as HTMLTextAreaElement).value)"></textarea>
          </details>
        </div>
      </section>
      <div v-else class="card empty illustrated-empty">
        <img src="/assets/quiet-study-empty.webp" alt="" />
        <strong>等待一份新试卷</strong>
        <p>上传或选择一条导入记录后，在这里校对题库。</p>
      </div>
    </div>

    <div v-if="labelPromptOpen" class="label-editor-overlay" role="dialog" aria-modal="true" aria-labelledby="label-prompt-title">
      <section class="label-editor label-prompt card">
        <header>
          <div><span class="eyebrow">POST-PUBLISH OPTION</span><h2 id="label-prompt-title">是否立即进行智能标注？</h2></div>
          <button class="button ghost compact" type="button" aria-label="稍后处理" @click="closeLabelPrompt">稍后</button>
        </header>
        <div class="label-prompt-summary">
          <strong>{{ labelPromptScope?.title }}</strong>
          <span v-if="labelPromptBusy">正在读取待标注数量…</span>
          <span v-else>当前有 {{ labelPromptStatus?.remaining || 0 }} 道题待标注，已标注 {{ labelPromptStatus?.labeled || 0 }} 道</span>
        </div>
        <ul class="label-prompt-notes">
          <li>本次只处理刚刚入库的试卷，不会影响其他年份。</li>
          <li>将使用默认 API：{{ labelPromptModel || '尚未配置可用模型' }}，会产生 Token 消耗。</li>
          <li>人工锁定的标签不会被覆盖；任务可在后台继续运行。</li>
        </ul>
        <p v-if="labelPromptError" class="warning" role="alert">{{ labelPromptError }}</p>
        <p v-if="!labelPromptBusy && !labelPromptHasModel" class="api-profile-notice">未检测到已启用且填写默认模型的 API，点击主按钮将前往“模型与 API”配置。</p>
        <footer>
          <button ref="labelLaterButton" class="button ghost" type="button" @click="closeLabelPrompt">稍后再说</button>
          <button class="button" type="button" :disabled="labelPromptBusy || Boolean(labelPromptError)" @click="beginPromptedLabeling"><Settings v-if="!labelPromptHasModel" :size="16" /><Sparkles v-else :size="16" />{{ labelPromptHasModel ? '立即智能标注' : '前往模型与 API' }}</button>
        </footer>
      </section>
    </div>

    <div v-if="editingLabel" class="label-editor-overlay" role="presentation" @click.self="editingLabel=null">
      <section class="label-editor card" role="dialog" aria-modal="true" aria-labelledby="label-editor-title">
        <header>
          <div><span class="eyebrow">MANUAL REVIEW</span><h2 id="label-editor-title">{{ editingLabel.year }} 年第 {{ editingLabel.number }} 题</h2></div>
          <button class="button ghost compact" type="button" @click="editingLabel=null">取消</button>
        </header>
        <div class="field"><label>主要考点</label><input v-model.trim="editingLabel.primary_skill"></div>
        <div class="field"><label>次要考点（逗号分隔）</label><input :value="editingLabel.secondary_skills.join('，')" @input="editingLabel.secondary_skills=splitTags(($event.target as HTMLInputElement).value)"></div>
        <div class="field"><label>常见陷阱（逗号分隔）</label><textarea :value="editingLabel.trap_types.join('，')" rows="2" @input="editingLabel.trap_types=splitTags(($event.target as HTMLTextAreaElement).value)"></textarea></div>
        <div class="field"><label>注意事项（每条用逗号或换行分隔）</label><textarea :value="editingLabel.attention_points.join('\n')" rows="3" @input="editingLabel.attention_points=splitTags(($event.target as HTMLTextAreaElement).value)"></textarea></div>
        <div class="grid grid-3">
          <div class="field"><label>词汇依赖</label><select v-model="editingLabel.vocabulary_demand"><option value="low">低</option><option value="medium">中</option><option value="high">高</option></select></div>
          <div class="field"><label>上下文依赖</label><select v-model="editingLabel.context_dependency"><option value="low">低</option><option value="medium">中</option><option value="high">高</option></select></div>
          <div class="field"><label>语法依赖</label><select v-model="editingLabel.grammar_dependency"><option value="low">低</option><option value="medium">中</option><option value="high">高</option></select></div>
        </div>
        <label class="default-profile-check"><input v-model="editingLabel.locked" type="checkbox">保存后锁定，后续批量标注不会覆盖</label>
        <footer><small>人工保存的内容会标记为“人工校正”，默认建议保持锁定。</small><button class="button" type="button" :disabled="labelBusyQuestionId === editingLabel.question_id || !editingLabel.primary_skill.trim()" @click="saveQuestionLabel"><Save :size="16" />保存标签</button></footer>
      </section>
    </div>

    <div v-if="assistDialogOpen" class="review-overlay" role="dialog" aria-modal="true" aria-label="模型辅助不可用">
      <div class="review-card import-assist-dialog">
        <h3 style="margin-bottom:10px">模型辅助不可用</h3>
        <p class="lead" style="font-size:13px;line-height:1.7">本地解析已完成，草稿已保留，可以直接人工审查。原因：{{ assistError }}</p>
        <div v-if="!showModelSelector" style="display:flex;gap:10px;margin-top:20px;justify-content:center">
          <button class="button ghost" @click="assistDialogOpen=false">人工审查</button>
          <button class="button" @click="showModelSelector=true;openModelSelector()">选择其他模型重试</button>
        </div>
        <div v-else style="margin-top:16px">
          <label class="field"><span>选择模型</span>
            <select v-model="selectedModelKey" :disabled="assistBusy">
              <option value="" disabled>请选择已配置且启用的模型</option>
              <option v-for="model in selectorModels" :key="`${model.profile_id}|${model.model_id}`" :value="`${model.profile_id}|${model.model_id}`">{{ model.profile_name }} / {{ model.model_id }}</option>
            </select>
          </label>
          <p v-if="!selectorModels.length && !assistBusy" class="lead" style="font-size:12px;margin-top:8px">没有可用模型，请先在“模型与设置”中配置并启用 API。</p>
          <div style="display:flex;gap:10px;margin-top:16px;justify-content:center">
            <button class="button ghost" @click="showModelSelector=false">返回</button>
            <button class="button" :disabled="!selectedModelKey || assistBusy" @click="retryAssist">{{ assistBusy ? '正在解析…' : '使用该模型重试' }}</button>
          </div>
        </div>
      </div>
    </div>

    <div v-if="importConfirmOpen" class="review-overlay" role="dialog" aria-modal="true" aria-labelledby="single-import-title">
      <div class="review-card import-assist-dialog">
        <h3 id="single-import-title" style="margin-bottom:10px">确认开始导入</h3>
        <p class="lead" style="font-size:13px;line-height:1.7">
          当前一次只允许导入一套题目。同一个 Word / PDF 文件如果包含多套真题，系统只会默认生成并导入第 1 套，其余套次会被忽略。
        </p>
        <div style="display:flex;gap:10px;margin-top:20px;justify-content:center">
          <button class="button ghost" type="button" @click="importConfirmOpen=false">取消</button>
          <button class="button" type="button" @click="upload">确认导入第 1 套</button>
        </div>
      </div>
    </div>
  </div>
</template>
