<script setup lang="ts">
import LanQuestionBankSharing from '../components/LanQuestionBankSharing.vue'
import LanPairedDevices from '../components/LanPairedDevices.vue'
import { ClipboardCopy, Download, FolderOpen, PackageCheck, RefreshCw, Server, ShieldCheck, Trash2 } from 'lucide-vue-next'
import { nextTick, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import QRCode from 'qrcode'
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

// 程序更新卡片底部的社区入口：只放图标不放文字。GitHub 交给系统浏览器，QQ 群走客户端深链。
const communityLinks = {
  qq: 'mqqapi://card/show_pslcard?uin=1012639381&card_type=group',
  github: 'https://github.com/wssfk12138/english-multiple-choice-practice-machine',
}
const checked = ref<UpdateCheck | null>(null)
const downloaded = ref<DownloadReceipt | null>(null)
const diagnostics = ref<DiagnosticEntry[]>(listDiagnosticEntries())
const busy = ref('')
const notice = ref('')
const error = ref('')
const lanSync = reactive({ enabled: false, listening: false, passcode: '', tables: [] as string[], last_sync_at: '', paired_devices: 0, host_urls: [] as string[] })
const deviceListRevision = ref(0)
const qrCanvas = ref<HTMLCanvasElement | null>(null)
const tlsPairing = ref<Record<string, any> | null>(null)
const pairingBusy = ref(false)
let pairingTimer: ReturnType<typeof setTimeout> | undefined
let pairingRevision = 0
function clearPairingCode() {
  pairingRevision += 1
  clearTimeout(pairingTimer)
  tlsPairing.value = null
  void renderLanSyncQr()
}
onUnmounted(clearPairingCode)

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
  clearPairingCode()
    Object.assign(lanSync, await get('/lan-sync/status'))
    deviceListRevision.value += 1
  await renderLanSyncQr()
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
    await refreshPairingCode()
    notice.value = '已生成新的局域网同步口令'
  } catch (cause) {
    error.value = String(cause)
  } finally {
    busy.value = ''
  }
}

function lanSyncQrPayload(): string {
  const value = tlsPairing.value
  return value ? 'EPM2:' + JSON.stringify([value.host, value.host_id,
    value.certificate_pem.replace(/-----BEGIN CERTIFICATE-----|-----END CERTIFICATE-----|\s/g, ''),
    value.certificate_pin, value.pairing_code, value.expires_at]) : ''
}

async function refreshPairingCode(replaceDeviceId = ''): Promise<void> {
  if (pairingBusy.value) return
  pairingBusy.value = true
  clearPairingCode()
  const revision = pairingRevision
  try {
    const result: any = await post('/lan-sync/pairing' + (replaceDeviceId ? '?replace_device_id=' + encodeURIComponent(replaceDeviceId) : ''))
    if (revision !== pairingRevision) return
    tlsPairing.value = result
    pairingTimer = setTimeout(clearPairingCode, Math.max(0, result.expires_at * 1000 - Date.now()))
    await renderLanSyncQr()
  } catch (cause) {
    error.value = String(cause)
  } finally {
    pairingBusy.value = false
  }
}

async function renderLanSyncQr(): Promise<void> {
  await nextTick()
  const canvas = qrCanvas.value
  if (!canvas) return
  const payload = lanSyncQrPayload()
  if (!payload) {
    canvas.getContext('2d')?.clearRect(0, 0, canvas.width, canvas.height)
    return
  }
  await QRCode.toCanvas(canvas, payload, { scale: 5, margin: 4, errorCorrectionLevel: 'M' })
}

async function copyLanSyncValue(value: string, message: string) {
  if (!value) return
  try {
    await navigator.clipboard.writeText(value)
    notice.value = message
  } catch (cause) {
    error.value = '复制失败：' + String(cause)
  }
}

watch(() => [lanSync.host_urls.slice(), lanSync.passcode, lanSync.enabled, lanSync.listening], () => {
  void renderLanSyncQr()
}, { deep: true })

async function disableLanSync() {
  if (!confirm('关闭局域网同步主机？已签发的同步会话将立即失效。')) return
  busy.value = 'disable-lan'
  error.value = ''
  try {
    Object.assign(lanSync, await post('/lan-sync/disable'))
    clearPairingCode()
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
      </div>
      <span v-if="status" class="pill">当前 {{ status.currentVersion }}（{{ status.currentVersionCode }}）</span>
    </div>

    <div v-if="error" class="warning" role="alert">{{ error }}</div>
    <div v-if="notice" class="settings-success" role="status"><PackageCheck :size="17" />{{ notice }}</div>

    <section class="card lan-sync-card">
      <header>
        <span class="update-icon"><Server :size="21" /></span>
        <div><h2>局域网同步主机</h2></div>
      </header>
      <div class="lan-sync-addresses">
        <strong>手机端主机地址</strong>
        <code v-for="url in lanSync.host_urls" :key="url">{{ url }}</code>
        <span v-if="!lanSync.host_urls.length" class="lead">{{ lanSync.listening ? '未检测到可用的局域网 IPv4 地址' : (lanSync.enabled ? '同步主机未启动，请重新生成口令；若仍失败，请检查端口占用' : '局域网同步未开启') }}</span>
      </div>
      <div class="lan-sync-passcode">
        <strong>配对口令</strong>
        <code>{{ lanSync.passcode || (lanSync.listening ? '正在监听，重新生成可显示新口令' : '尚未生成') }}</code>
      </div>
      <div class="lan-sync-qr-layout">
        <div class="lan-sync-qr-frame" aria-label="局域网同步二维码">
          <canvas ref="qrCanvas" width="220" height="220"></canvas>
          <span v-if="!lanSyncQrPayload()">刷新安全配对码</span>
        </div>
        <div class="lan-sync-qr-copy">
          <strong>HTTPS 安全配对</strong>
          <span v-if="tlsPairing">单次有效 · 截止 {{ new Date(tlsPairing.expires_at * 1000).toLocaleTimeString() }}</span>
          <button class="button secondary compact" type="button" :disabled="!lanSync.enabled || pairingBusy" title="刷新安全配对码" aria-label="刷新安全配对码" @click="refreshPairingCode()"><RefreshCw :size="17" /></button>
          <div class="update-actions">
            <button class="button secondary compact" type="button" :disabled="!lanSync.host_urls.length" @click="copyLanSyncValue(lanSync.host_urls[0], '电脑地址已复制')"><ClipboardCopy :size="15" />复制地址</button>
            <button class="button secondary compact" type="button" :disabled="!lanSync.passcode" @click="copyLanSyncValue(lanSync.passcode, '配对口令已复制')"><ClipboardCopy :size="15" />复制口令</button>
          </div>
        </div>
      </div>
      <p>上次同步：{{ lanSync.last_sync_at ? formatTime(lanSync.last_sync_at) : '尚无客户端同步' }}</p>
      <div class="update-actions">
        <button class="button secondary" type="button" :disabled="Boolean(busy)" @click="generatePasscode">{{ busy === 'passcode' ? '生成中…' : (lanSync.enabled ? '重新生成口令' : '生成口令并开启') }}</button>
        <button v-if="lanSync.paired_devices" class="button ghost" type="button" :disabled="Boolean(busy)" @click="revokePairedDevices">{{ busy === 'revoke' ? '解除中…' : '解除全部设备配对' }}</button>
        <button v-if="lanSync.enabled || lanSync.listening" class="button danger" type="button" :disabled="Boolean(busy)" @click="disableLanSync">关闭局域网同步</button>
      </div>
      <LanPairedDevices :key="deviceListRevision" @changed="refreshLanSync" @replace="refreshPairingCode" />
      <LanQuestionBankSharing />
    </section>

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

    <section class="card update-diagnostics">
      <header>
        <div><h2>本地脱敏诊断</h2></div>
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
.lan-sync-qr-layout{display:grid;grid-template-columns:minmax(236px,260px) 1fr;gap:18px;align-items:center;margin-top:16px}.lan-sync-qr-frame{width:236px;aspect-ratio:1;display:grid;place-items:center;position:relative;border:1px solid var(--line);border-radius:8px;background:#fff;overflow:hidden}.lan-sync-qr-frame canvas{width:220px;height:220px;display:block}.lan-sync-qr-frame span{position:absolute;color:var(--muted);font-size:14px;text-align:center;padding:16px}.lan-sync-qr-copy{display:grid;gap:8px;align-content:center}.lan-sync-qr-copy p{margin:0;color:var(--muted);line-height:1.6}@media(max-width:720px){.lan-sync-qr-layout{grid-template-columns:1fr}.lan-sync-qr-frame{width:min(236px,100%);justify-self:center}}
</style>

<style scoped>
.lan-sync-qr-layout { grid-template-columns: minmax(0, 320px) minmax(0, 1fr); }
/* 程序更新卡片：更新结果排在按钮上方，末行左侧操作按钮、右侧社区图标，整行贴到卡片底部。
   该卡片原有的 scoped 规则用的是「一行空格分隔」写法，这里用 !important 覆盖 display 与行数。 */
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
.lan-sync-qr-copy { min-width: 0; overflow-wrap: anywhere; }
.lan-sync-qr-copy > button { width: 44px; height: 44px; padding: 0; justify-self: start; }
.lan-sync-qr-frame span { color: #616d7d; }
.update-source-card code { overflow-wrap: anywhere; min-width: 0; }
.lan-sync-qr-frame { width: 320px; max-width: 100%; }
.lan-sync-qr-frame canvas { width: calc(100% - 16px) !important; height: auto !important; aspect-ratio: 1; min-width: 0; image-rendering: pixelated; }
@media (max-width: 720px) {
  .lan-sync-qr-layout { grid-template-columns: minmax(0, 1fr); }
  .lan-sync-qr-frame { width: 320px; max-width: 100%; }
}
</style>
