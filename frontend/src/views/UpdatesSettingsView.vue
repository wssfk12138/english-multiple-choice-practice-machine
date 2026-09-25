<script setup lang="ts">
import { Download, FolderOpen, PackageCheck, RefreshCw, ShieldCheck } from 'lucide-vue-next'
import { onMounted, ref } from 'vue'
import { confirmDialog } from '../platform/dialogs'
import { get, post } from '../api'

type UpdateStatus = {
  currentVersion: string
  currentVersionCode: number
  source: string
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

// 程序更新卡片底部的社区入口：只放图标不放文字。GitHub 交给系统浏览器，QQ 群走客户端深链。
const communityLinks = {
  qq: 'mqqapi://card/show_pslcard?uin=1012639381&card_type=group',
  github: 'https://github.com/wssfk12138/english-multiple-choice-practice-machine',
}
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
        <h1>更新中心</h1>
      </div>
      <span v-if="status" class="pill">当前 {{ status.currentVersion }}（{{ status.currentVersionCode }}）</span>
    </div>

    <div v-if="error" class="warning" role="alert">{{ error }}</div>
    <div v-if="notice" class="settings-success" role="status"><PackageCheck :size="17" />{{ notice }}</div>

    <section class="card update-card" data-update-section="update">
      <header>
        <span class="update-icon"><ShieldCheck :size="21" /></span>
        <div><h2>程序更新</h2></div>
      </header>
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
      <div class="update-community">
        <a class="icon-button" :href="communityLinks.qq" rel="noreferrer noopener" aria-label="QQ 群 1012639381" title="QQ 群 1012639381"><svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true" focusable="false" fill="currentColor" stroke="none"><path d="M21.395 15.035a40 40 0 0 0-.803-2.264l-1.079-2.695c.001-.032.014-.562.014-.836C19.526 4.632 17.351 0 12 0S4.474 4.632 4.474 9.241c0 .274.013.804.014.836l-1.08 2.695a39 39 0 0 0-.802 2.264c-1.021 3.283-.69 4.643-.438 4.673.54.065 2.103-2.472 2.103-2.472 0 1.469.756 3.387 2.394 4.771-.612.188-1.363.479-1.845.835-.434.32-.379.646-.301.778.343.578 5.883.369 7.482.189 1.6.18 7.14.389 7.483-.189.078-.132.132-.458-.301-.778-.483-.356-1.233-.646-1.846-.836 1.637-1.384 2.393-3.302 2.393-4.771 0 0 1.563 2.537 2.103 2.472.251-.03.581-1.39-.438-4.673" /></svg>
        </a>
        <a class="icon-button" :href="communityLinks.github" target="_blank" rel="noreferrer noopener" aria-label="GitHub 项目" title="GitHub 项目"><svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true" focusable="false" fill="currentColor" stroke="none"><path d="M12 .297c-6.63 0-12 5.373-12 12 0 5.303 3.438 9.8 8.205 11.385.6.113.82-.258.82-.577 0-.285-.01-1.04-.015-2.04-3.338.724-4.042-1.61-4.042-1.61C4.422 18.07 3.633 17.7 3.633 17.7c-1.087-.744.084-.729.084-.729 1.205.084 1.838 1.236 1.838 1.236 1.07 1.835 2.809 1.305 3.495.998.108-.776.417-1.305.76-1.605-2.665-.3-5.466-1.332-5.466-5.93 0-1.31.465-2.38 1.235-3.22-.135-.303-.54-1.523.105-3.176 0 0 1.005-.322 3.3 1.23.96-.267 1.98-.399 3-.405 1.02.006 2.04.138 3 .405 2.28-1.552 3.285-1.23 3.285-1.23.645 1.653.24 2.873.12 3.176.765.84 1.23 1.91 1.23 3.22 0 4.61-2.805 5.625-5.475 5.92.42.36.81 1.096.81 2.22 0 1.606-.015 2.896-.015 3.286 0 .315.21.69.825.57C20.565 22.092 24 17.592 24 12.297c0-6.627-5.373-12-12-12" /></svg>
        </a>
      </div>
    </section>
  </div>
</template>

<style scoped>
.updates-page{max-width:1000px}.update-card{display:grid;gap:18px;margin-bottom:16px}.update-card>header{display:flex;align-items:flex-start;gap:12px}.update-card h2{margin:0 0 7px}.update-card header p{margin:0;color:var(--muted);line-height:1.65}.update-icon{width:44px;height:44px;display:grid;place-items:center;flex:0 0 auto;border-radius:8px;color:var(--primary);background:var(--primary-soft)}.update-actions{display:flex;flex-wrap:wrap;gap:9px}.update-result,.verified-receipt{padding:14px;border:1px solid var(--line);border-radius:8px;background:var(--primary-faint)}.update-result{display:grid;gap:7px}.update-result span,.update-result p{color:var(--muted)}.update-result p{margin:0;white-space:pre-wrap;line-height:1.6}.verified-receipt{display:flex;align-items:center;gap:9px;min-width:0;color:var(--success)}.verified-receipt span{min-width:0;overflow-wrap:anywhere}.spinning{animation:spin .8s linear infinite}@keyframes spin{to{transform:rotate(360deg)}}
[data-update-section='update'] { display:grid; grid-template-columns:minmax(0,1fr) auto; grid-template-rows:min-content min-content 1fr; gap:10px; }
[data-update-section='update'] > header { grid-column:1/-1; grid-row:1; }
[data-update-section='update'] > .update-result { grid-column:1/-1; grid-row:2; }
[data-update-section='update'] > .verified-receipt { grid-column:1/-1; grid-row:2; }
[data-update-section='update'] > .update-actions { grid-column:1; grid-row:3; align-self:end; }
[data-update-section='update'] > .update-actions > .button { min-height:44px; }
[data-update-section='update'] > .update-community { grid-column:2; grid-row:3; align-self:end; justify-self:end; }
.update-community { display:flex; align-items:center; gap:8px; flex:0 0 auto; }
.update-community .icon-button { width:44px; height:44px; flex:0 0 44px; padding:0; text-decoration:none; }
.update-community .icon-button svg { width:20px; height:20px; fill:currentColor; stroke:none; }
</style>
