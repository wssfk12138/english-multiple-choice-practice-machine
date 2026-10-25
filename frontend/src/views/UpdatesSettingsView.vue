<script setup lang="ts">
import CommunityLinks from '../components/CommunityLinks.vue'
import RemoteQuestionBanksPanel from '../components/RemoteQuestionBanksPanel.vue'
import { Download, FolderOpen, PackageCheck, RefreshCw, ShieldCheck } from 'lucide-vue-next'
import { onMounted, ref } from 'vue'
import { confirmDialog } from '../platform/dialogs'
import { get, post } from '../api'

type UpdateStatus = {
  currentVersion: string
  currentVersionCode: number
  source: string
  manifestUrl: string
}

type UpdateCheck = {
  available: boolean
  currentVersion: string
  currentVersionCode: number
  latest: {
    versionName: string
    versionCode: number
    packageSize: number
    packageType: string
    releaseNotes: string
  }
}

type DownloadReceipt = {
  fileName: string
  versionName: string
  versionCode: number
  packageType: string
  packageSize: number
  packageSha256: string
  verifiedAt: string
}

const status = ref<UpdateStatus | null>(null)

const checked = ref<UpdateCheck | null>(null)
const downloaded = ref<DownloadReceipt | null>(null)
const busy = ref('')
const notice = ref('')
const error = ref('')

function formatBytes(bytes: number) {
  return bytes >= 1024 * 1024
    ? `${(bytes / 1024 / 1024).toFixed(1)} MiB`
    : `${Math.ceil(bytes / 1024)} KiB`
}

async function loadStatus() {
  try {
    status.value = await get<UpdateStatus>('/updates/status')
  } catch (cause) {
    error.value = String(cause)
  }
}

async function checkUpdate() {
  busy.value = 'check'
  error.value = ''
  notice.value = ''
  downloaded.value = null
  try {
    checked.value = await post<UpdateCheck>('/updates/check')
    notice.value = checked.value.available ? '检测到可用更新' : '当前已经是最新版本'
  } catch (cause) {
    error.value = String(cause)
  } finally {
    busy.value = ''
  }
}

async function downloadUpdate() {
  busy.value = 'download'
  error.value = ''
  notice.value = ''
  try {
    downloaded.value = await post<DownloadReceipt>('/updates/download')
    notice.value = '更新包已下载，并通过大小与 SHA-256 校验'
  } catch (cause) {
    error.value = String(cause)
  } finally {
    busy.value = ''
  }
}

async function openUpdate() {
  if (!downloaded.value || !await confirmDialog({ title: '打开已校验安装包', message: '将打开已校验的安装包。安装过程中程序可能需要关闭，是否继续？', confirmLabel: '打开' })) return
  busy.value = 'open'
  error.value = ''
  try {
    await post('/updates/open', { file_name: downloaded.value.fileName })
    notice.value = '已交给 Windows 打开更新包，请按系统提示继续'
  } catch (cause) {
    error.value = String(cause)
  } finally {
    busy.value = ''
  }
}

onMounted(loadStatus)
</script>

<template>
  <div class="page updates-page">
    <div class="page-head">
      <div>
        <h1>更新与远程题库</h1>
      </div>
      <div class="update-head-actions">
        <span v-if="status" class="pill">当前 {{ status.currentVersion }}（{{ status.currentVersionCode }}）</span>
        <CommunityLinks />
      </div>
    </div>

    <div v-if="error" class="warning" role="alert">{{ error }}</div>
    <div v-if="notice" class="settings-success" role="status"><PackageCheck :size="17" />{{ notice }}</div>

    <section id="question-banks" class="card update-source-card" aria-labelledby="remote-banks-title">
      <div class="update-section-heading"><span class="update-icon"><PackageCheck :size="24" /></span><h2 id="remote-banks-title">远程题库目录</h2></div>
      <RemoteQuestionBanksPanel embedded />
    </section>

    <section class="card update-card" data-update-section="update">
      <header>
        <span class="update-icon"><Download :size="21" /></span>
        <div><h2>程序更新</h2></div>
      </header>
      <div v-if="status" class="official-update-source">
        <span>版本更新地址</span>
        <a :href="status.manifestUrl" target="_blank" rel="noopener noreferrer">{{ status.manifestUrl }}</a>
      </div>
      <div class="update-actions">
        <button class="button secondary" type="button" :disabled="Boolean(busy)" @click="checkUpdate">
          <RefreshCw :size="16" :class="{ spinning: busy === 'check' }" />{{ busy === 'check' ? '检查中…' : '检查更新' }}
        </button>
        <button v-if="checked?.available" class="button" type="button" :disabled="Boolean(busy)" @click="downloadUpdate">
          <Download :size="16" />{{ busy === 'download' ? '下载并校验中…' : '下载并校验' }}
        </button>
        <button v-if="downloaded" class="button" type="button" :disabled="Boolean(busy)" @click="openUpdate">
          <FolderOpen :size="16" />打开安装包
        </button>
      </div>
      <div v-if="checked" class="update-result">
        <strong>{{ checked.available ? `可更新至 ${checked.latest.versionName}` : '已是最新版本' }}</strong>
        <span>{{ checked.latest.packageType.toUpperCase() }} · {{ formatBytes(checked.latest.packageSize) }}</span>
        <p v-if="checked.latest.releaseNotes">{{ checked.latest.releaseNotes }}</p>
      </div>
      <div v-if="downloaded" class="verified-receipt">
        <ShieldCheck :size="18" /><span>已验证 {{ downloaded.fileName }} · SHA-256 {{ downloaded.packageSha256.slice(0, 12) }}…</span>
      </div>

    </section>
  </div>
</template>

<style scoped>
.updates-page{max-width:1280px}.update-card{display:grid;gap:18px;margin-bottom:16px}.update-card>header{display:flex;align-items:flex-start;gap:12px}.update-card h2{font-size:1.25rem;margin:0 0 7px}.update-card header p{margin:0;color:var(--muted);line-height:1.65}.update-icon{width:44px;height:44px;display:grid;place-items:center;flex:0 0 auto;border-radius:8px;color:var(--primary);background:var(--primary-soft)}.update-actions{display:flex;flex-wrap:wrap;gap:9px}.update-result,.verified-receipt{padding:14px;border:1px solid var(--line);border-radius:8px;background:var(--primary-faint)}.update-result{display:grid;gap:7px}.update-result span,.update-result p{color:var(--muted)}.update-result p{margin:0;white-space:pre-wrap;line-height:1.6}.verified-receipt{display:flex;align-items:center;gap:9px;min-width:0;color:var(--success)}.verified-receipt span{min-width:0;overflow-wrap:anywhere}.spinning{animation:spin .8s linear infinite}@keyframes spin{to{transform:rotate(360deg)}}

.updates-page > .page-head { display:flex; flex-wrap:wrap; align-items:flex-start; gap:16px; }
.update-head-actions { display:flex; flex-direction:column; align-items:flex-end; gap:10px; margin-left:auto; min-width:0; }
.official-update-source { display:grid; gap:7px; min-width:0; }
.official-update-source > span { color:var(--muted); font-size:0.9375rem; }
.official-update-source > a { color:var(--primary); overflow-wrap:anywhere; font-size:0.875rem; line-height:1.6; }
[data-update-section='update'] { display:flex; flex-direction:column; gap:16px; }
[data-update-section='update'] > .update-section-heading { margin-bottom:0; }
[data-update-section='update'] > .update-result, [data-update-section='update'] > .verified-receipt { order:1; }
[data-update-section='update'] > .update-actions { order:2; }
[data-update-section='update'] > .update-actions > .button { min-height:44px; }
</style>
