<script setup lang="ts">
import { ClipboardCopy, Download, FolderOpen, PackageCheck, RefreshCw, Server, ShieldCheck, Trash2 } from 'lucide-vue-next'
import { onMounted, reactive, ref } from 'vue'
import { get, post } from '../api'
import {
  clearDiagnosticEntries,
  copyText,
  downloadDiagnosticEntries,
  listDiagnosticEntries,
  serializeDiagnosticPayload,
  type DiagnosticEntry,
} from '../platform/diagnostics'

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
const checked = ref<UpdateCheck | null>(null)
const downloaded = ref<DownloadReceipt | null>(null)
const diagnostics = ref<DiagnosticEntry[]>(listDiagnosticEntries())
const busy = ref('')
const notice = ref('')
const error = ref('')
const lanSync = reactive({ enabled: false, listening: false, passcode: '', tables: [] as string[], last_sync_at: '', paired_devices: 0, host_urls: [] as string[] })

function formatBytes(bytes: number) {
  return bytes >= 1024 * 1024
    ? `${(bytes / 1024 / 1024).toFixed(1)} MiB`
    : `${Math.ceil(bytes / 1024)} KiB`
}

function formatTime(value: string) {
  return new Intl.DateTimeFormat('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  }).format(new Date(value))
}

async function refreshLanSync() {
  Object.assign(lanSync, await get('/lan-sync/status'))
}

async function loadStatus() {
  try {
    status.value = await get<UpdateStatus>('/updates/status')
    await refreshLanSync()
  } catch (cause) {
    error.value = String(cause)
  }
}

async function generatePasscode() {
  busy.value = 'passcode'
  error.value = ''
  try {
    Object.assign(lanSync, await post('/lan-sync/passcode'))
    notice.value = '已生成新的局域网同步口令'
  } catch (cause) {
    error.value = String(cause)
  } finally {
    busy.value = ''
  }
}

async function disableLanSync() {
  if (!confirm('关闭局域网同步主机？已签发的同步会话将立即失效。')) return
  busy.value = 'disable-lan'
  error.value = ''
  try {
    Object.assign(lanSync, await post('/lan-sync/disable'))
    lanSync.passcode = ''
    notice.value = '局域网同步主机已关闭'
  } catch (cause) {
    error.value = String(cause)
  } finally {
    busy.value = ''
  }
}

async function revokePairedDevices() {
  if (!lanSync.paired_devices || !confirm(`将解除全部 ${lanSync.paired_devices} 台已配对设备？`)) return
  busy.value = 'revoke'
  error.value = ''
  try {
    const result = await post<{ revoked: number }>('/lan-sync/devices/revoke')
    notice.value = `已解除 ${result.revoked || 0} 台设备配对`
    await refreshLanSync()
  } catch (cause) {
    error.value = String(cause)
  } finally {
    busy.value = ''
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
    diagnostics.value = listDiagnosticEntries()
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
    diagnostics.value = listDiagnosticEntries()
  }
}

async function openUpdate() {
  if (!downloaded.value || !confirm('将打开已校验的安装包，是否继续？')) return
  busy.value = 'open'
  error.value = ''
  try {
    await post('/updates/open', { file_name: downloaded.value.fileName })
    notice.value = '已交给 Windows 打开更新包，请按系统提示继续'
  } catch (cause) {
    error.value = String(cause)
  } finally {
    busy.value = ''
    diagnostics.value = listDiagnosticEntries()
  }
}

async function copyDiagnostics() {
  if (!diagnostics.value.length) return
  await copyText(serializeDiagnosticPayload(diagnostics.value))
  notice.value = `已复制 ${diagnostics.value.length} 条脱敏诊断`
}

function exportDiagnostics() {
  const count = downloadDiagnosticEntries()
  notice.value = count ? `已导出 ${count} 条脱敏诊断` : '暂无可导出的诊断'
}

function clearDiagnostics() {
  if (!diagnostics.value.length || !confirm('确认清空全部本地诊断吗？')) return
  clearDiagnosticEntries()
  diagnostics.value = []
  notice.value = '本地诊断已清空'
}

onMounted(loadStatus)
</script>

<template>
  <div class="page updates-page">
    <div class="page-head">
      <div>
        <span class="eyebrow">WINDOWS UPDATE</span>
        <h1>更新中心</h1>
        <p class="lead">只从固定 GitHub 官方发布源检查更新。更新包必须通过大小和 SHA-256 校验，打开前会再次核验。</p>
      </div>
      <span v-if="status" class="pill">当前 {{ status.currentVersion }}（{{ status.currentVersionCode }}）</span>
    </div>

    <div v-if="error" class="warning" role="alert">{{ error }}</div>
    <div v-if="notice" class="settings-success" role="status"><PackageCheck :size="17" />{{ notice }}</div>

    <section class="card lan-sync-card">
      <header>
        <span class="update-icon"><Server :size="21" /></span>
        <div><h2>局域网同步主机</h2><p>默认关闭。生成配对口令后，手机可在同一局域网同步做题记录、错题本和单词本。</p></div>
      </header>
      <div class="lan-sync-addresses">
        <strong>手机端主机地址</strong>
        <code v-for="url in lanSync.host_urls" :key="url">{{ url }}</code>
        <span v-if="!lanSync.host_urls.length" class="lead">{{ lanSync.listening ? '未检测到可用的局域网 IPv4 地址' : (lanSync.enabled ? '同步主机未在监听，端口 8766 可能被占用，可尝试重新生成口令' : '局域网同步未开启，点击下方按钮开启') }}</span>
      </div>
      <div class="lan-sync-passcode">
        <strong>配对口令</strong>
        <code>{{ lanSync.passcode || (lanSync.listening ? '正在监听，重新生成可显示新口令' : '尚未生成') }}</code>
      </div>
      <p class="lead">上次同步：{{ lanSync.last_sync_at ? formatTime(lanSync.last_sync_at) : '尚无客户端同步' }} · 已配对设备：{{ lanSync.paired_devices }} 台</p>
      <div class="update-actions">
        <button class="button secondary" type="button" :disabled="Boolean(busy)" @click="generatePasscode">{{ busy === 'passcode' ? '生成中…' : (lanSync.enabled ? '重新生成口令' : '生成口令并开启') }}</button>
        <button v-if="lanSync.paired_devices" class="button ghost" type="button" :disabled="Boolean(busy)" @click="revokePairedDevices">{{ busy === 'revoke' ? '解除中…' : '解除全部设备配对' }}</button>
        <button v-if="lanSync.enabled || lanSync.listening" class="button danger" type="button" :disabled="Boolean(busy)" @click="disableLanSync">关闭局域网同步</button>
      </div>
    </section>

    <section class="card update-card">
      <header>
        <span class="update-icon"><ShieldCheck :size="21" /></span>
        <div><h2>程序更新</h2><p>{{ status?.source || 'GitHub 官方发布源' }}，不接受自定义地址或镜像。</p></div>
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
    </section>

    <section class="card update-diagnostics">
      <header>
        <div><h2>本地脱敏诊断</h2><p>与“帮助与反馈”共用本机数据。不会后台上传，只有你主动复制或导出时才离开本机。</p></div>
        <strong>{{ diagnostics.length }} 条</strong>
      </header>
      <div class="update-actions">
        <button class="button secondary" type="button" :disabled="!diagnostics.length" @click="copyDiagnostics"><ClipboardCopy :size="16" />复制诊断</button>
        <button class="button" type="button" :disabled="!diagnostics.length" @click="exportDiagnostics"><Download :size="16" />导出 JSON</button>
        <button class="button ghost" type="button" :disabled="!diagnostics.length" @click="clearDiagnostics"><Trash2 :size="16" />清空</button>
      </div>
    </section>
  </div>
</template>

<style scoped>
.updates-page{max-width:1000px}.update-card,.update-diagnostics,.lan-sync-card{display:grid;gap:18px;margin-bottom:16px}.update-card>header,.update-diagnostics>header,.lan-sync-card>header{display:flex;align-items:flex-start;gap:12px}.update-diagnostics>header{justify-content:space-between}.update-card h2,.update-diagnostics h2,.lan-sync-card h2{margin:0 0 7px}.update-card header p,.update-diagnostics header p,.lan-sync-card header p{margin:0;color:var(--muted);line-height:1.65}.update-icon{width:44px;height:44px;display:grid;place-items:center;flex:0 0 auto;border-radius:8px;color:var(--primary);background:var(--primary-soft)}.update-actions{display:flex;flex-wrap:wrap;gap:9px}.lan-sync-addresses,.lan-sync-passcode{display:flex;align-items:center;gap:10px;flex-wrap:wrap}.lan-sync-addresses code,.lan-sync-passcode code{padding:7px 10px;border:1px solid var(--line);border-radius:7px;background:var(--primary-faint);overflow-wrap:anywhere}.lan-sync-passcode code{font-size:17px;letter-spacing:2px}.update-result,.verified-receipt{padding:14px;border:1px solid var(--line);border-radius:8px;background:var(--primary-faint)}.update-result{display:grid;gap:7px}.update-result span,.update-result p{color:var(--muted)}.update-result p{margin:0;white-space:pre-wrap;line-height:1.6}.verified-receipt{display:flex;align-items:center;gap:9px;min-width:0;color:var(--success)}.verified-receipt span{min-width:0;overflow-wrap:anywhere}.spinning{animation:spin .8s linear infinite}@keyframes spin{to{transform:rotate(360deg)}}@media(max-width:720px){.update-diagnostics>header{display:block}.update-diagnostics>header>strong{display:block;margin-top:10px}.update-actions .button{flex:1}}
</style>
