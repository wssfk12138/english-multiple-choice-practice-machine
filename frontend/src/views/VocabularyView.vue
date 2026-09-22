<script setup lang="ts">
import { confirmDesktop } from '../services/desktopConfirm'
import VocabularyMemoryContent from '../components/VocabularyMemoryContent.vue'
import VocabularyDisplaySettings from '../components/VocabularyDisplaySettings.vue'
import { VOCAB_REVIEW_DISPLAY_CONFIG, loadVocabDisplayConfig } from '../services/vocabularyDisplayConfig'
import '../vocabulary-workspace.css'
import { BookOpen, Check, RefreshCw, Search, Settings, Star, Trash2 } from 'lucide-vue-next'
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { del, get, post, put } from '../api'
import { reconcileDueQueue, orderedDueKeys, completeDueWord, wordKey, type DueQueue } from '../vocabulary-due-queue'

const route = useRoute()
const items = ref<any[]>([])
const counts = ref<any>({ total:0, frequent:0, mastered:0, pending:0, review:0 })
// 首屏读到数据之前不得渲染终态：“正在读取”与“确实没有单词”必须可区分。
const countsLoaded = ref(false)
// 计数只在这一个入口落地：读到之前界面显示占位符，不显示 0。
function applyCounts(next: any) {
  if (!next) return
  counts.value = next
  countsLoaded.value = true
}
function displayCount(value: unknown) {
  return countsLoaded.value ? Number(value || 0) : '—'
}
const selected = ref<any>(null)
const filter = ref(route.query.review === 'scheduled' ? 'review' : 'all')
const currentBankScope = route.query.scope === 'current'
const search = ref('')
const error = ref('')
const notice = ref('')
const editing = ref(false)
const editForm = reactive<any>({})
const reviewMode = ref(false)
const reviewKind = ref<'scheduled' | 'reinforcement'>('scheduled')
const reinforcementSize = ref(10)
const reveal = ref(false)
const reviewIndex = ref(0)
const scheduledWords = ref<any[]>([])
const reviewItems = computed(() => {
  if (reviewKind.value === 'scheduled') return scheduledWords.value
  const ready = items.value.filter(item => item.translation_status === 'ready')
  return reviewKind.value === 'reinforcement' ? ready.slice(0, reinforcementSize.value) : scheduledWords.value
})
let dueQueue: DueQueue = reconcileDueQueue(null, [])
let dueQueueStorageKey = ''
const reviewSaving = ref(false)
function persistDueQueue() {
  try { localStorage.setItem(dueQueueStorageKey, JSON.stringify(dueQueue)) }
  catch { error.value = '无法保存复习进度，请检查设备存储空间' }
}
let dueRefreshPromise: Promise<void> | null = null
let reviewDisposed = false
function reviewRevision(word: any) {
  return word?.review_revision || (word ? JSON.stringify([wordKey(word), word.last_reviewed_at, word.next_review_at, word.review_count, word.updated_at]) : '')
}
function refreshDueQueue(): Promise<void> {
  if (dueRefreshPromise) return dueRefreshPromise
  dueRefreshPromise = loadDueQueue().finally(() => { dueRefreshPromise = null })
  return dueRefreshPromise
}
async function loadDueQueue() {
  const previous = reviewRevision(reviewWord.value)
  const result: any = await get(`/vocabulary?status=review${currentBankScope ? '&scope=current' : ''}`)
  if (reviewDisposed) return
  applyCounts(result.counts)
  dueQueueStorageKey = `vocab-due-queue:v1:${result.scope_key || (currentBankScope ? 'current' : 'all')}`
  let saved = null
  try { saved = JSON.parse(localStorage.getItem(dueQueueStorageKey) || 'null') } catch { /* Rebuild from durable reviews. */ }
  dueQueue = reconcileDueQueue(saved, result.items || [])
  persistDueQueue()
  const words = new Map<string, any>((result.items || []).map((word: any) => [wordKey(word), word]))
  scheduledWords.value = orderedDueKeys(dueQueue).map(key => words.get(key)).filter(Boolean)
  if (previous !== reviewRevision(reviewWord.value)) reveal.value = false
}
const reviewWord = computed(() => reviewItems.value[reviewIndex.value])
const displayConfig = ref(loadVocabDisplayConfig())
// 每日一背只显示「常用释义 + 真题原句」：复习卡读服务层的固定契约，设置页的显示项
// 只作用于单词本首页的单词学习卡片，因此设置里怎么改都不会改变复习卡内容。
const reviewDisplayConfig = VOCAB_REVIEW_DISPLAY_CONFIG
function refreshDisplayConfig() { displayConfig.value = loadVocabDisplayConfig() }
onMounted(() => { window.addEventListener('vocab-display-change', refreshDisplayConfig); window.addEventListener('storage', refreshDisplayConfig) })
onBeforeUnmount(() => { window.removeEventListener('vocab-display-change', refreshDisplayConfig); window.removeEventListener('storage', refreshDisplayConfig) })
const showDisplayDialog = ref(false)
const expandedAll = ref(false)
const enrichmentPending = ref(false)
async function enrichSelected() {
  if (!selected.value || enrichmentPending.value) return
  enrichmentPending.value = true
  const id = selected.value.id
  try {
    const result = await post(`/vocabulary/${id}/enrichment`)
    if (selected.value?.id === id) selected.value = result
    notice.value = (result as any).enrichment_status === 'ready' ? '词形和例句已补全，手动释义已保留' : '补全任务已保存，稍后可查看结果'
  } catch (cause) { error.value = String(cause) }
  finally {
    enrichmentPending.value = false
    if (!translationRefreshTimer) translationRefreshTimer = window.setInterval(refreshTranslationStatuses, 2500)
  }
}

function translationStatusText(status: string, detail = false) {
  if (status === 'translating') return detail ? '模型正在后台翻译' : '正在后台翻译…'
  if (status === 'failed') return detail ? '翻译暂未完成' : '等待重新翻译'
  return detail ? '等待练习提交或退出后翻译' : '等待练习结束后翻译'
}

async function load() {
  try {
    const result: any = await get(`/vocabulary?status=${filter.value}&search=${encodeURIComponent(search.value)}${currentBankScope ? '&scope=current' : ''}`)
    error.value = ''
    items.value = result.items || []
    applyCounts(result.counts)
    const requested = Number(route.query.word)
    const target = items.value.find(item => item.id === requested) || items.value[0]
    if (target) await select(target.id)
    else selected.value = null
  } catch (e) { error.value = String(e) }
}

const translationProgress = ref<any>({})
let translationRefreshTimer = 0
let translationRefreshBusy = false
async function refreshTranslationStatuses() {
  if (translationRefreshBusy || reviewDisposed) return
  translationRefreshBusy = true
  try {
    translationProgress.value = await get('/vocabulary/translation-status')
    if (!editing.value && !reviewMode.value) {
      const result: any = await get(`/vocabulary?status=${filter.value}&search=${encodeURIComponent(search.value)}${currentBankScope ? '&scope=current' : ''}`)
      items.value = result.items || []
      applyCounts(result.counts)
      if (selected.value && (selected.value.translation_status !== 'ready' || ['queued','inflight'].includes(selected.value.enrichment_status))) await select(selected.value.id)
    }
    if (!(translationProgress.value.queued > 0 || translationProgress.value.translating > 0 || translationProgress.value.enrichment_queued > 0 || translationProgress.value.enrichment_inflight > 0)) {
      window.clearInterval(translationRefreshTimer)
      translationRefreshTimer = 0
    }
  } catch (cause) { error.value = String(cause) }
  finally { translationRefreshBusy = false }
}
function watchTranslations() {
  if (!translationRefreshTimer) translationRefreshTimer = window.setInterval(refreshTranslationStatuses, 2500)
}
onBeforeUnmount(() => window.clearInterval(translationRefreshTimer))

async function select(id: number) {
  try {
    selected.value = await get(`/vocabulary/${id}`)
    error.value = ''
    Object.assign(editForm, selected.value)
    editing.value = false
    expandedAll.value = false
  } catch (e) {
    error.value = String(e)
  }
}

async function saveEdit() {
  selected.value = await put(`/vocabulary/${selected.value.id}`, {
    contextual_meaning: editForm.contextual_meaning,
    common_meaning: editForm.common_meaning,
    phonetic: editForm.phonetic,
    part_of_speech: editForm.part_of_speech,
    note: editForm.note,
    study_status: editForm.study_status,
    manually_frequent: Boolean(editForm.manually_frequent),
  })
  editing.value = false
  notice.value = '词条已保存'
  await load()
}

async function removeEntry() {
  if (!selected.value || !await confirmDesktop(`删除 ${selected.value.term} 吗？`)) return
  await del(`/vocabulary/${selected.value.id}`)
  selected.value = null
  await load()
}

async function retryTranslation() {
  await post('/vocabulary/retry-pending')
  notice.value = '已将整个单词本中等待及失败的词条加入翻译队列'
  await load()
  watchTranslations()
}

async function rate(rating: string) {
  if (!reviewWord.value || reviewSaving.value) return
  const target = reviewWord.value
  const targetMode = reviewKind.value
  reviewSaving.value = true
  const completedKey = wordKey(target)
  const revision = reviewRevision(target)
  try {
    if (targetMode === 'scheduled') {
      await refreshDueQueue()
      if (reviewDisposed || !reviewMode.value || targetMode !== reviewKind.value || revision !== reviewRevision(reviewWord.value)) {
        reveal.value = false
        notice.value = '复习列表已更新，请查看当前单词后重新评分'
        return
      }
    }
  await post(`/vocabulary/${target.id}/review`, { rating, mode: targetMode, expected_revision: target.review_revision })
  if (reviewDisposed || !reviewMode.value || revision !== reviewRevision(reviewWord.value) || targetMode !== reviewKind.value) return
  if (targetMode === 'scheduled') {
    dueQueue = completeDueWord(dueQueue, completedKey)
    persistDueQueue()
    scheduledWords.value = scheduledWords.value.filter(word => wordKey(word) !== completedKey)
    reveal.value = false
    await refreshDueQueue()
  }
  reveal.value = false
  if (targetMode === 'reinforcement') {
    await load()
    reviewIndex.value += 1
  }
  if (!reviewItems.value.length || reviewIndex.value >= reviewItems.value.length) {
    reviewMode.value = false
    reviewIndex.value = 0
    notice.value = targetMode === 'scheduled' ? '每日一背已完成，可继续额外巩固' : '本轮额外巩固已完成'
    await load()
  }
  } catch (cause) { error.value = String(cause) }
  finally { reviewSaving.value = false }
}

async function startReview() {
  if (reviewSaving.value) return
  reviewKind.value = 'scheduled'
  reveal.value = false
  reviewIndex.value = 0
  await refreshDueQueue()
  reviewMode.value = true
}

async function closeReview() {
  if (reviewSaving.value) return
  reviewMode.value = false
  reviewIndex.value = 0
  await load()
}

async function startReinforcement(size: number) {
  if (reviewSaving.value) return
  reinforcementSize.value = size
  reviewKind.value = 'reinforcement'
  reveal.value = false
  reviewIndex.value = 0
  filter.value = 'all'
  await load()
  reviewMode.value = true
}

let searchTimer = 0
watch(search, () => {
  window.clearTimeout(searchTimer)
  searchTimer = window.setTimeout(load, 250)
})
watch(filter, load)
async function refreshReviewOnForeground() {
  if (document.visibilityState === 'hidden' || !reviewMode.value || reviewSaving.value || !(reviewKind.value === 'scheduled')) return
  try { await refreshDueQueue() } catch (cause) { error.value = String(cause) }
}
onMounted(() => {
  document.addEventListener('visibilitychange', refreshReviewOnForeground)
  window.addEventListener('focus', refreshReviewOnForeground)
  window.addEventListener('storage', refreshReviewOnForeground)
})
onBeforeUnmount(() => {
  reviewDisposed = true
  document.removeEventListener('visibilitychange', refreshReviewOnForeground)
  window.removeEventListener('focus', refreshReviewOnForeground)
  window.removeEventListener('storage', refreshReviewOnForeground)
  window.clearTimeout(searchTimer)
})
onMounted(async () => {
  try {
    await post('/vocabulary/translation-runs', { entry_ids: [], trigger: 'vocabulary_open' })
    watchTranslations()
  } catch (cause) { error.value = String(cause) }
  if (route.query.review === 'scheduled') await startReview()
  else await load()
})
</script>

<template>
  <div class="page vocabulary-page">
    <div class="page-head">
      <div><span class="eyebrow">VOCABULARY BOOK</span><h1>我的单词本</h1><p class="lead">从真题语境中收集、理解并复习真正困扰你的词。</p></div>
      <div style="display:flex;gap:8px;align-items:center">
        <button class="button ghost" @click="showDisplayDialog=true"><Settings :size="17" />显示设置</button>
        <button class="button" @click="startReview"><BookOpen :size="17" />每日一背 · 待背 {{ displayCount(counts.review) }} 词</button>
      </div>
    </div>
    <div v-if="error" class="warning">{{ error }}</div>
    <div v-if="notice" class="card vocab-notice">{{ notice }}</div>
    <div v-if="translationProgress.queued || translationProgress.translating || translationProgress.failed" class="card vocab-notice" role="status">翻译进度：排队 {{ translationProgress.queued || 0 }} · 正在翻译 {{ translationProgress.translating || 0 }} · 失败 {{ translationProgress.failed || 0 }}</div>
    <div class="vocab-stats">
      <button class="card" @click="filter='all'"><span>全部单词</span><strong>{{ displayCount(counts.total) }}</strong></button>
      <button class="card amber" @click="filter='frequent'"><span>🌟 高频生词</span><strong>{{ displayCount(counts.frequent) }}</strong></button>
      <button class="card" @click="filter='review'"><span>每日一背</span><strong>{{ displayCount(counts.review) }}</strong></button>
      <button class="card" @click="filter='mastered'"><span>已掌握</span><strong>{{ displayCount(counts.mastered) }}</strong></button>
      <button class="card" @click="filter='pending'"><span>等待翻译</span><strong>{{ displayCount(counts.pending) }}</strong></button>
    </div>

    <section v-if="!reviewMode" class="vocab-toolbar card">
      <div class="search-field"><Search :size="16" /><input v-model="search" aria-label="搜索单词或释义" placeholder="搜索单词或释义"></div>
      <select v-model="filter" aria-label="单词状态"><option v-for="item in [['all','全部单词'],['review','每日一背'],['frequent','高频词'],['learning','学习中'],['mastered','已掌握'],['pending','等待翻译']]" :key="item[0]" :value="item[0]">{{ item[1] }}</option></select>
      <div class="vocab-reinforcement-actions"><span title="不改变正式复习计划，可反复练习">额外巩固</span><button v-for="size in [5,10,20]" :key="size" class="button secondary compact" @click="startReinforcement(size)">{{ size }} 词</button></div>
    </section>

    <section v-if="reviewMode" class="review-overlay vocabulary-review-overlay" role="dialog" aria-modal="true" aria-label="单词复习">
      <div class="review-card vocabulary-review-card" v-if="reviewWord">
        <!-- 进度分母：正式复习读整队到期计数，额外巩固读本轮选中的条数。 -->
        <header class="review-header"><span class="eyebrow">{{ reviewKind === 'scheduled' ? '每日一背' : '额外巩固' }} {{ String(reviewIndex + 1).padStart(2, '0') }} / {{ String(reviewKind === 'scheduled' ? displayCount(counts.review) : reviewItems.length).padStart(2, '0') }}</span><button class="button ghost" :disabled="reviewSaving" @click="closeReview">退出复习</button></header>
        <div class="review-content">
          <div class="review-term"><span v-if="reviewWord.is_frequent">🌟</span>{{ reviewWord.term }}</div>
          <div class="review-answer-wrap">
            <button v-if="!reveal" class="button secondary reveal-button" @click="reveal=true">显示释义和原句</button>
            <div class="review-answer" :class="{ 'answer-reserved': !reveal }" :aria-hidden="!reveal"><VocabularyMemoryContent :entry="reviewWord" :config="reviewDisplayConfig" review /></div>
          </div>
        </div>
        <div class="review-actions" :class="{ 'answer-reserved': !reveal }" :aria-hidden="!reveal">
          <button class="button secondary" :disabled="reviewSaving || !reveal" @click="rate('hard')">不认识</button>
          <button class="button secondary" :disabled="reviewSaving || !reveal" @click="rate('know')">认识</button>
          <button class="button" :disabled="reviewSaving || !reveal" @click="rate('fluent')">熟练</button>
        </div>
        <p v-if="error" class="error" role="alert">{{ error }}</p>
        <p v-if="notice" class="muted" role="status">{{ notice }}</p>
      </div>
      <div v-else class="card empty"><p>今天没有待复习的单词。</p><button class="button secondary" @click="closeReview">返回单词本</button></div>
    </section>

    <div v-else class="vocabulary-layout">
      <section class="vocab-list card">
        <button v-for="word in items" :key="word.id" class="vocab-list-item" :class="{active:selected?.id===word.id}" @click="select(word.id)">
          <div class="vocab-list-head"><strong><span v-if="word.is_frequent">🌟 </span>{{ word.lemma || word.term }}</strong><small>遇到 {{ word.encounter_count }} 次</small></div>
          <p v-if="word.translation_status==='ready'">{{ word.common_meaning || word.contextual_meaning }}</p>
          <p v-else class="pending-text">{{ translationStatusText(word.translation_status) }}</p>
          <div class="vocab-list-meta"><span>{{ word.part_of_speech }}</span><span>{{ word.study_status === 'mastered' ? '已掌握' : '学习中' }}</span></div>
        </button>
        <div v-if="!items.length" class="empty">这里还没有符合条件的单词。</div>
      </section>

      <section class="vocab-detail card" v-if="selected">
        <div class="vocab-detail-head">
          <div><span class="eyebrow">{{ selected.is_frequent ? '🌟 HIGH FREQUENCY' : 'VOCABULARY' }}</span><h2>{{ selected.lemma || selected.term }}</h2><p><span v-if="displayConfig.phonetic || expandedAll">{{ selected.phonetic }}</span> <span v-if="(displayConfig.part_of_speech || expandedAll) && selected.part_of_speech">{{ selected.part_of_speech }}</span></p></div>
          <div class="vocab-tools"><button class="button ghost" @click="expandedAll=!expandedAll">{{ expandedAll ? '收起全部' : '展开全部' }}</button><button class="button ghost" @click="editing=!editing">编辑</button><button class="button ghost danger-text" @click="removeEntry"><Trash2 :size="17" /></button></div>
        </div>
        <div v-if="selected.translation_status!=='ready'" class="vocab-pending-panel">
          <RefreshCw :size="22" /><strong>{{ translationStatusText(selected.translation_status, true) }}</strong>
          <p>单词和真题原句已经安全保存。</p>
          <button v-if="selected.translation_status==='failed' || selected.translation_status==='pending'" class="button secondary" @click="retryTranslation">重试全部待翻译</button>
        </div>
        <template v-else-if="!editing">
          <VocabularyMemoryContent :entry="selected" :config="displayConfig" :expanded="expandedAll" />
          <p v-if="selected.enrichment_status === 'failed'" class="error">补全失败：{{ selected.enrichment_error }}，可点击下方按钮重试。</p>
          <p v-else-if="['queued','inflight'].includes(selected.enrichment_status)">词形与例句补全任务已保存，正在排队或处理中。</p>
          <button class="button secondary" :disabled="enrichmentPending" @click="enrichSelected">{{ enrichmentPending ? '正在补全…' : '补全词形与例句' }}</button>
          <div class="detail-actions">
            <button class="button secondary" @click="put(`/vocabulary/${selected.id}`,{manually_frequent:!selected.manually_frequent}).then(()=>load())"><Star :size="16" />{{ selected.manually_frequent ? '取消重点' : '标记重点' }}</button>
            <button class="button" @click="put(`/vocabulary/${selected.id}`,{study_status:selected.study_status==='mastered'?'learning':'mastered'}).then(()=>load())"><Check :size="16" />{{ selected.study_status === 'mastered' ? '恢复学习' : '标记已掌握' }}</button>
          </div>
        </template>
        <div v-else class="vocab-edit">
          <label>音标<input v-model="editForm.phonetic"></label>
          <label>词性<input v-model="editForm.part_of_speech"></label>
          <label>当前语境释义<textarea rows="3" v-model="editForm.contextual_meaning"></textarea></label>
          <label>常用释义<textarea rows="3" v-model="editForm.common_meaning"></textarea></label>
          <label>我的笔记<textarea rows="4" v-model="editForm.note"></textarea></label>
          <div><button class="button" @click="saveEdit">保存修改</button><button class="button ghost" @click="editing=false">取消</button></div>
        </div>
      </section>
      <section v-else class="vocab-detail card empty">选择一个单词查看详细释义与真题语境。</section>
    </div>
    <div v-if="showDisplayDialog" class="review-overlay" role="dialog" aria-modal="true" aria-label="单词本显示设置">
      <div class="review-card vocab-display-dialog">
        <h3 style="margin-bottom:10px">单词本显示设置</h3>
        <VocabularyDisplaySettings />
        <div style="display:flex;justify-content:center;margin-top:22px">
          <button class="button" @click="showDisplayDialog=false">完成</button>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.vocab-reinforcement {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.vocab-reinforcement > div:first-child {
  display: grid;
  gap: 3px;
  min-width: 0;
}

.vocab-reinforcement span {
  color: var(--muted);
  font-size: .84rem;
}

.vocab-reinforcement-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

@media (max-width: 720px) {
  .vocab-reinforcement {
    align-items: flex-start;
    flex-direction: column;
  }

  .vocab-reinforcement-actions,
  .vocab-reinforcement-actions .button {
    width: 100%;
  }

  .vocab-reinforcement-actions .button {
    flex: 1 1 72px;
  }
}
</style>
