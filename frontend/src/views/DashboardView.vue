<script setup lang="ts">
import { TextCursorInput, ListOrdered, Headphones, Files, ListChecks, ArrowRight, BookOpen, Sparkles, Star } from 'lucide-vue-next'
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { get, post } from '../api'
import QuestionBankSwitcher from '../components/QuestionBankSwitcher.vue'
import StudyTodos from '../components/StudyTodos.vue'
import '../dashboard-glass.css'

const router = useRouter()
const data = ref<any>(null)
const error = ref('')
const studyTodos = computed(() => {
  if (!data.value) return null
  const session = data.value.resume_session
  return {
    profile_id: Number(data.value.profile_id || 0),
    resume_session: session ? { id: Number(session.id), mode: String(session.mode || ''), title: session.title } : null,
    review_count: Number(data.value.review_count || 0),
    frequent_count: Number(data.value.frequent_count || 0),
  }
})
const vocabulary = ref<any[]>([])
const tickerPaused = ref(false)
const vocabularyPage = ref(0)
const wordsPerPage = 4
let vocabularyTimer: number | null = null
let homeDisposed = false
const vocabularyPages = computed(() => {
  const pages: any[][] = []
  for (let index = 0; index < vocabulary.value.length; index += wordsPerPage) {
    pages.push(vocabulary.value.slice(index, index + wordsPerPage))
  }
  return pages
})
const visibleWords = computed(() =>
  vocabularyPages.value[vocabularyPage.value] || vocabularyPages.value[0] || [],
)
const hasListening = computed(() =>
  Number(data.value?.paper_type_counts?.listening || 0) > 0,
)
const hasPracticeType = (type: string) =>
  Number(type === 'listening' ? data.value?.paper_type_counts?.listening || 0 : data.value?.unit_type_counts?.[type] || 0) > 0
const hasAnyPractice = computed(() =>
  ['cloze', 'reading', 'part_b', 'listening'].some(hasPracticeType),
)
const practiceGridClass = computed(() => {
  const count = ['cloze', 'reading', 'part_b', 'listening']
    .filter(hasPracticeType).length
  if (count >= 4) return 'grid-4'
  if (count === 3) return 'grid-3'
  if (count === 2) return 'grid-2'
  return ''
})

function wordMeaning(word: any) {
  return word.common_meaning || word.contextual_meaning || '释义整理中'
}

function advanceVocabulary() {
  if (tickerPaused.value || vocabularyPages.value.length <= 1) return
  vocabularyPage.value = (vocabularyPage.value + 1) % vocabularyPages.value.length
}

function startVocabularyRotation() {
  if (homeDisposed) return
  if (vocabularyTimer !== null) window.clearInterval(vocabularyTimer)
  vocabularyTimer = window.setInterval(advanceVocabulary, 5000)
}

function wait(milliseconds: number) {
  return new Promise(resolve => window.setTimeout(resolve, milliseconds))
}

let homeLoad: Promise<void> | null = null
let homeReloadQueued = false

function loadHome(): Promise<void> {
  // 挂载与题库切换可能同时触发首页刷新：合并为一条在途请求，期间再次
  // 请求只排队一次补跑，加载完成后循环退出。
  homeReloadQueued = true
  homeLoad ||= (async () => {
    try {
      while (homeReloadQueued) {
        homeReloadQueued = false
        await refreshHome()
      }
    } finally {
      homeLoad = null
    }
  })()
  return homeLoad
}

async function refreshHome() {
  error.value = ''
  const embedded = (window as any).__LINJIAN_STARTUP__
  // The injected payload only accelerates the first paint. Profile changes
  // must fetch fresh counts for the newly active question bank.
  if (embedded && !data.value) {
    try { data.value = embedded } catch { data.value = null }
  }
  if (embedded) delete (window as any).__LINJIAN_STARTUP__

  let dashboardResult: PromiseSettledResult<any> | null = null
  let wordsResult: PromiseSettledResult<any> | null = null
  for (let attempt = 0; attempt < 2; attempt++) {
    [dashboardResult, wordsResult] = await Promise.allSettled([
      embedded && !data.value ? Promise.resolve(embedded) : get('/startup'),
      get('/vocabulary/home?limit=20'),
    ])
    if (dashboardResult.status === 'fulfilled') break
    if (attempt === 0) await wait(500)
  }
  if (dashboardResult?.status === 'fulfilled') data.value = dashboardResult.value
  else error.value = '首页数据暂时没有加载成功，请刷新页面重试。'
  if (wordsResult?.status === 'fulfilled') vocabulary.value = wordsResult.value.items || []
  vocabularyPage.value = 0
}

onMounted(async () => {
  await loadHome()
  startVocabularyRotation()
})
onBeforeUnmount(() => {
  homeDisposed = true
  if (vocabularyTimer !== null) window.clearInterval(vocabularyTimer)
})

async function randomPractice(type: string) {
  if (!hasPracticeType(type)) {
    error.value = '当前题库配置中没有可练习的该题型，请先切换题库配置或导入题目。'
    return
  }
  try {
    const session: any = await post('/practice/sessions', {
      mode: 'random',
      unit_type: type,
      selection_scope: type === 'listening' ? 'paper_unit_type' : 'unit',
      count: 1,
      shuffle_options: true,
    })
    router.push(`/practice/${session.id}`)
  } catch (e) { error.value = String(e) }
}
</script>

<template>
  <div class="page glass-dashboard">
    <div class="dashboard-bank-row"><QuestionBankSwitcher :show-library-link="true" @changed="loadHome" /></div>
    <StudyTodos :tasks="studyTodos" :failed="!!error" @retry="loadHome" />
    <div v-if="error" class="warning">{{ error }}</div>
    <section v-if="vocabulary.length" class="vocabulary-ticker card" @mouseenter="tickerPaused=true" @mouseleave="tickerPaused=false">
      <div class="ticker-heading"><div><span class="eyebrow">VOCABULARY REVIEW</span><h3>词汇回顾</h3></div><RouterLink to="/vocabulary">查看单词本 →</RouterLink></div>
      <div class="ticker-window">
        <Transition name="vocabulary-flip" mode="out-in">
          <div :key="vocabularyPage" class="ticker-group">
          <RouterLink v-for="word in visibleWords" :key="word.id" :to="`/vocabulary?word=${word.id}`" class="ticker-word">
            <Star v-if="word.is_frequent" class="vocab-star" :size="15" fill="currentColor" aria-label="高频词" />
            <span class="ticker-word-copy">
              <strong>{{ word.lemma || word.term }}</strong>
              <small :title="wordMeaning(word)">{{ wordMeaning(word) }}</small>
            </span>
          </RouterLink>
          </div>
        </Transition>
      </div>
    </section>
    <div v-if="hasAnyPractice" class="section-title"><h2>题型练习</h2></div>
    <div v-if="hasAnyPractice" class="grid practice-actions" :class="practiceGridClass">
      <button v-if="hasPracticeType('cloze')" class="card action-card" type="button" @click="randomPractice('cloze')">
        <span class="feature-icon orange"><TextCursorInput :size="28" /></span>
        <span class="action-copy"><h3>完形填空</h3></span>
        <ArrowRight class="action-arrow" :size="19" />
      </button>
      <button v-if="hasPracticeType('reading')" class="card action-card" type="button" @click="randomPractice('reading')">
        <span class="feature-icon sage"><BookOpen :size="28" /></span>
        <span class="action-copy"><h3>阅读理解</h3></span>
        <ArrowRight class="action-arrow" :size="19" />
      </button>
      <button v-if="hasPracticeType('part_b')" class="card action-card" type="button" @click="randomPractice('part_b')">
        <span class="feature-icon blue"><ListOrdered :size="28" /></span>
        <span class="action-copy"><h3>阅读 Part B</h3></span>
        <ArrowRight class="action-arrow" :size="19" />
      </button>
      <button v-if="hasListening" class="card action-card" type="button" @click="randomPractice('listening')">
        <span class="feature-icon purple"><Headphones :size="42" aria-hidden="true" /></span>
        <span class="action-copy"><h3>听力练习</h3></span>
        <ArrowRight class="action-arrow" :size="19" />
      </button>
    </div>
    <div v-else-if="data" class="card empty">
      当前题库配置中还没有可练习的已发布题目，请先切换题库配置或导入题库。
      <RouterLink class="button ghost compact" to="/library">前往题库</RouterLink>
    </div>
    <section v-if="data" class="dashboard-bank-summary" aria-label="题库概览">
        <h2>题库概览</h2>
        <dl class="overview-grid">
          <div class="overview-stat"><dt><span>试卷</span><Files aria-hidden="true" /></dt><dd>{{ data.paper_count }}<small>套</small></dd></div>
          <div class="overview-stat"><dt><span>篇目</span><BookOpen aria-hidden="true" /></dt><dd>{{ data.unit_count }}<small>篇</small></dd></div>
          <div class="overview-stat"><dt><span>客观题</span><ListChecks aria-hidden="true" /></dt><dd>{{ data.question_count }}<small>道</small></dd></div>
        </dl>
      </section>
  </div>
</template>


<style scoped>

.dashboard-bank-row{display:flex;align-items:flex-start;justify-content:space-between;gap:12px;flex-wrap:wrap}.dashboard-bank-row :deep(.bank-switcher){flex:1;min-width:0;margin:0}.dashboard-bank-row>.button{flex-shrink:0}.dashboard-bank-summary{display:flex;align-items:baseline;flex-wrap:wrap;gap:8px 20px;padding:14px 0;border-top:1px solid var(--line);color:var(--muted)}.dashboard-bank-summary h2{margin:0;font-size:.95rem;color:var(--ink)}.dashboard-bank-summary span{font-size:.88rem}.section-title h2{font-size:1.1rem;letter-spacing:0}
</style>
