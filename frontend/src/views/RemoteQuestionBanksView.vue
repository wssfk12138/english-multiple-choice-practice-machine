<script setup lang="ts">
import { CheckCircle2, CloudDownload, Download, RefreshCw, Save } from 'lucide-vue-next'
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { get, post, put } from '../api'

type CatalogPackage = {
  packageId: string
  title: string
  contentVersion: string
  fileName: string
  sha256: string
  size: number
  license: string
  years: number[]
}
type Catalog = { catalogVersion: 1, updatedAt: string, packages: CatalogPackage[] }
type PackageResult = { status: 'pending' | 'success' | 'failed', message: string }

const router = useRouter()
const catalogUrl = ref('')
const catalog = ref<Catalog | null>(null)
const selected = ref<string[]>([])
const results = ref<Record<string, PackageResult>>({})
const busy = ref('')
const notice = ref('')
const error = ref('')
const packageKey = (item: CatalogPackage) => `${item.packageId}@${item.contentVersion}`
const allSelected = computed(() => Boolean(catalog.value?.packages.length)
  && catalog.value!.packages.every(item => selected.value.includes(packageKey(item))))

async function loadSettings() {
  try {
    const value = await get<{ question_bank_catalog_url: string }>('/remote-question-banks/settings')
    catalogUrl.value = value.question_bank_catalog_url
  } catch (cause) {
    error.value = String(cause)
  }
}

async function saveSettings() {
  busy.value = 'save'; error.value = ''; notice.value = ''
  try {
    const value = await put<{ question_bank_catalog_url: string }>('/remote-question-banks/settings', {
      question_bank_catalog_url: catalogUrl.value,
    })
    catalogUrl.value = value.question_bank_catalog_url
    notice.value = '远程题库目录已保存'
  } catch (cause) {
    error.value = String(cause)
  } finally {
    busy.value = ''
  }
}

async function checkCatalog() {
  busy.value = 'check'; error.value = ''; notice.value = ''; results.value = {}
  try {
    catalog.value = await post<Catalog>('/remote-question-banks/check')
    selected.value = []
    notice.value = `已获取 ${catalog.value.packages.length} 个远程题库`
  } catch (cause) {
    error.value = String(cause)
  } finally {
    busy.value = ''
  }
}

function toggleAll() {
  selected.value = allSelected.value ? [] : (catalog.value?.packages.map(packageKey) || [])
}

async function createSelectedDrafts() {
  const packages = (catalog.value?.packages || []).filter(item => selected.value.includes(packageKey(item)))
  if (!packages.length) return
  busy.value = 'download'; error.value = ''; notice.value = ''; results.value = {}
  const importIds: number[] = []
  for (const item of packages) {
    const key = packageKey(item)
    results.value[key] = { status: 'pending', message: '正在下载、校验并建立草稿' }
    try {
      const result = await post<{ id: number }>('/remote-question-banks/download', {
        package_id: item.packageId,
        content_version: item.contentVersion,
      })
      importIds.push(result.id)
      results.value[key] = { status: 'success', message: '已建立导入草稿' }
    } catch (cause) {
      results.value[key] = { status: 'failed', message: String(cause) }
    }
  }
  busy.value = ''
  const failures = packages.length - importIds.length
  notice.value = `已建立 ${importIds.length} 个导入草稿${failures ? `，${failures} 个失败` : ''}`
  if (importIds.length) await router.push({ path: '/imports', query: { esqImportId: String(importIds[0]) } })
}

onMounted(loadSettings)
</script>

<template>
  <div class="page remote-banks-page">
    <div class="page-head">
      <div>
        <h1>远程题库</h1>
        <p class="lead">从你信任的 HTTPS 目录选择 ESQ 包。下载会重新核对目录身份、大小和 SHA-256，只建立导入草稿。</p>
      </div>
      <span class="pill"><CloudDownload :size="16" />ESQ 1.0</span>
    </div>

    <div v-if="error" class="warning" role="alert">{{ error }}</div>
    <div v-if="notice" class="settings-success" role="status"><CheckCircle2 :size="17" />{{ notice }}</div>

    <section class="remote-section">
      <h2>目录设置</h2>
      <div class="field">
        <label for="remote-catalog-url">题库目录 URL</label>
        <input id="remote-catalog-url" v-model.trim="catalogUrl" inputmode="url" placeholder="https://example.com/question-bank-catalog.json">
      </div>
      <div class="actions">
        <button class="button secondary" type="button" :disabled="Boolean(busy)" @click="saveSettings"><Save :size="16" />{{ busy === 'save' ? '保存中…' : '保存目录' }}</button>
        <button class="button" type="button" :disabled="Boolean(busy) || !catalogUrl" @click="checkCatalog"><RefreshCw :size="16" />{{ busy === 'check' ? '获取中…' : '获取题库列表' }}</button>
      </div>
      <p class="privacy-note">目录和下载地址只允许公开 HTTPS 目标；不支持本机、局域网地址或带账号密码的 URL。</p>
    </section>

    <section v-if="catalog" class="remote-section catalog-section">
      <div class="catalog-toolbar">
        <div><h2>可用题库</h2><p>{{ catalog.packages.length }} 个可用，已选 {{ selected.length }} 个</p></div>
        <div class="actions">
          <button class="button ghost compact" type="button" :disabled="Boolean(busy)" @click="toggleAll">{{ allSelected ? '清空选择' : '全选' }}</button>
          <button class="button compact" type="button" :disabled="Boolean(busy) || !selected.length" @click="createSelectedDrafts"><Download :size="15" />{{ busy === 'download' ? '正在处理…' : `建立导入草稿（${selected.length}）` }}</button>
        </div>
      </div>
      <div v-if="catalog.packages.length" class="package-list">
        <label v-for="item in catalog.packages" :key="packageKey(item)" class="package-row">
          <input v-model="selected" type="checkbox" :value="packageKey(item)" :disabled="Boolean(busy)">
          <span class="package-copy">
            <strong>{{ item.title }}</strong>
            <small>版本 {{ item.contentVersion }} · {{ item.years.join('、') || '多年份' }} · {{ Math.ceil(item.size / 1024) }} KiB</small>
            <small>{{ item.license }}</small>
            <small v-if="results[packageKey(item)]" :class="`result-${results[packageKey(item)].status}`">{{ results[packageKey(item)].message }}</small>
          </span>
        </label>
      </div>
      <p v-else class="empty-state">远程目录当前没有题库包。</p>
    </section>
  </div>
</template>

<style scoped>
.remote-banks-page { max-width:1040px; }
.remote-section { padding:24px 0; border-top:1px solid var(--line); }
.remote-section h2 { margin:0 0 16px; font-size:20px; }
.actions, .catalog-toolbar { display:flex; align-items:center; gap:10px; flex-wrap:wrap; }
.catalog-toolbar { justify-content:space-between; margin-bottom:14px; }
.catalog-toolbar h2, .catalog-toolbar p { margin:0; }
.catalog-toolbar p, .privacy-note, .package-copy small { color:var(--muted); }
.privacy-note { margin:12px 0 0; line-height:1.6; }
.package-list { display:grid; gap:8px; }
.package-row { display:flex; align-items:flex-start; gap:12px; padding:14px 0; border-top:1px solid var(--line); }
.package-row input { margin-top:3px; }
.package-copy { display:grid; gap:5px; min-width:0; }
.result-success { color:var(--success) !important; }
.result-failed { color:var(--danger) !important; }
.result-pending { color:var(--muted); }
.empty-state { color:var(--muted); padding:20px 0; }
@media (max-width:720px) { .catalog-toolbar { align-items:flex-start; } .actions { width:100%; } }
</style>
