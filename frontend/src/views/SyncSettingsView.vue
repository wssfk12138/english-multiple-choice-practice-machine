<script setup lang="ts">
import LanQuestionBankSharing from '../components/LanQuestionBankSharing.vue'
import LanPairedDevices from '../components/LanPairedDevices.vue'
import { ClipboardCopy, RefreshCw, Server } from 'lucide-vue-next'
import { nextTick, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import QRCode from 'qrcode'
import { get, post } from '../api'

const lanSync = reactive({ enabled: false, listening: false, passcode: '', tables: [] as string[], last_sync_at: '', paired_devices: 0, host_urls: [] as string[] })
const busy = ref('')
const notice = ref('')
const error = ref('')
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

onMounted(refreshLanSync)
</script>

<template>
  <div class="page updates-page">
    <div class="page-head">
      <div>
        <h1>设备同步</h1>
      </div>
    </div>

    <div v-if="error" class="warning" role="alert">{{ error }}</div>
    <div v-if="notice" class="settings-success" role="status">{{ notice }}</div>

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
  </div>
</template>

<style scoped>
.lan-sync-card{display:grid;gap:18px;margin-bottom:16px}.lan-sync-card>header{display:flex;align-items:flex-start;gap:12px}.lan-sync-card h2{margin:0 0 7px}.lan-sync-card header p{margin:0;color:var(--muted);line-height:1.65}.update-icon{width:44px;height:44px;display:grid;place-items:center;flex:0 0 auto;border-radius:8px;color:var(--primary);background:var(--primary-soft)}.update-actions{display:flex;flex-wrap:wrap;gap:9px}.lan-sync-addresses,.lan-sync-passcode{display:flex;align-items:center;gap:10px;flex-wrap:wrap}.lan-sync-addresses code,.lan-sync-passcode code{padding:7px 10px;border:1px solid var(--line);border-radius:7px;background:var(--primary-faint);overflow-wrap:anywhere}.lan-sync-passcode code{font-size:17px;letter-spacing:2px}.spinning{animation:spin .8s linear infinite}@keyframes spin{to{transform:rotate(360deg)}}
.lan-sync-qr-layout{display:grid;grid-template-columns:minmax(236px,260px) 1fr;gap:18px;align-items:center;margin-top:16px}.lan-sync-qr-frame{width:236px;aspect-ratio:1;display:grid;place-items:center;position:relative;border:1px solid var(--line);border-radius:8px;background:#fff;overflow:hidden}.lan-sync-qr-frame canvas{width:220px;height:220px;display:block}.lan-sync-qr-frame span{position:absolute;color:var(--muted);font-size:14px;text-align:center;padding:16px}.lan-sync-qr-copy{display:grid;gap:8px;align-content:center}.lan-sync-qr-copy p{margin:0;color:var(--muted);line-height:1.6}@media(max-width:720px){.lan-sync-qr-layout{grid-template-columns:1fr}.lan-sync-qr-frame{width:min(236px,100%);justify-self:center}}
.lan-sync-qr-layout { grid-template-columns: minmax(0, 320px) minmax(0, 1fr); }
.lan-sync-qr-copy { min-width: 0; overflow-wrap: anywhere; }
.lan-sync-qr-copy > button { width: 44px; height: 44px; padding: 0; justify-self: start; }
.lan-sync-qr-frame span { color: #616d7d; }
.lan-sync-qr-frame { width: 320px; max-width: 100%; }
.lan-sync-qr-frame canvas { width: calc(100% - 16px) !important; height: auto !important; aspect-ratio: 1; min-width: 0; image-rendering: pixelated; }
@media (max-width: 720px) {
  .lan-sync-qr-layout { grid-template-columns: minmax(0, 1fr); }
  .lan-sync-qr-frame { width: 320px; max-width: 100%; }
}
</style>
