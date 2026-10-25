<script setup lang="ts">
import LanQuestionBankSharing from '../components/LanQuestionBankSharing.vue'
import LanPairedDevices from '../components/LanPairedDevices.vue'
import { ClipboardCopy, RefreshCw, Server, Plus, ScanLine, X, Maximize2, Minimize2 } from 'lucide-vue-next'
import { computed, nextTick, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import { confirmDialog } from '../platform/dialogs'
import QRCode from 'qrcode'
import { get, post } from '../api'

const lanSync=reactive({status:'',passcode:'',last_sync_at:'',paired_devices:0,host_urls:[] as string[],enabled:false,listening:false})
const busy=ref(''), notice=ref(''), error=ref(''), loading=ref(false), pairingError=ref('')
const deviceList=ref<InstanceType<typeof LanPairedDevices>|null>(null)
const dialog=ref<HTMLDialogElement|null>(null), dialogMode=ref<'connect'|'pair'>('connect'), expanded=ref(false)
const dialogOpen=ref(false)
const selectedUrl=ref(''), qrCanvas=ref<HTMLCanvasElement|null>(null)
type Pairing={host:string;host_id:string;certificate_pem:string;certificate_pin:string;pairing_code:string;expires_at:number}
const tlsPairing=ref<Pairing|null>(null), pairingBusy=ref(false), expired=ref(false), replacingId=ref('')
let pairingTimer:ReturnType<typeof setTimeout>|undefined, pairingRevision=0
const hostState=computed(()=>loading.value?'正在读取同步状态':lanSync.listening?'同步主机已开启':lanSync.enabled?'同步主机未就绪':'同步主机未开启')
function clearPairingCode(){pairingRevision++;clearTimeout(pairingTimer);tlsPairing.value=null;void renderQr()}
function closeDialog(){dialog.value?.close()}
function onDialogClose(){dialogOpen.value=false;clearPairingCode();expanded.value=false;replacingId.value='';pairingError.value=''}
onUnmounted(()=>{clearPairingCode();dialog.value?.close()})
function formatTime(value:string){const date=new Date(value&&!/[Z+]|\d-\d\d:\d\d$/.test(value)?value.replace(' ','T')+'Z':value);return Number.isNaN(date.getTime())?'暂无记录':new Intl.DateTimeFormat('zh-CN',{month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'}).format(date)}
async function refreshLanSync(){if(loading.value)return;loading.value=true;error.value='';try{Object.assign(lanSync,await get('/lan-sync/status'))}catch(cause){error.value='同步状态读取失败：'+String(cause)}finally{loading.value=false}}
watch(()=>lanSync.host_urls,urls=>{if(!urls.includes(selectedUrl.value))selectedUrl.value=urls[0]||''},{deep:true})
async function generatePasscode(){
  if(busy.value)return
  busy.value='passcode';error.value='';pairingError.value='';notice.value='';clearPairingCode()
  try{Object.assign(lanSync,await post('/lan-sync/passcode'));notice.value='同步口令已生成';if(dialogMode.value==='pair')await refreshPairingCode(replacingId.value)}catch(cause){pairingError.value=String(cause);error.value=String(cause)}finally{busy.value=''}
}
function payload(){const v=tlsPairing.value;return v?'EPM2:'+JSON.stringify([v.host,v.host_id,v.certificate_pem.replace(/-----BEGIN CERTIFICATE-----|-----END CERTIFICATE-----|\s/g,''),v.certificate_pin,v.pairing_code,v.expires_at]):''}
async function refreshPairingCode(replaceDeviceId=replacingId.value){
  if(pairingBusy.value||!lanSync.enabled)return
  pairingBusy.value=true;pairingError.value='';expired.value=false;clearPairingCode();const revision=pairingRevision
  try{const result=await post<Pairing>('/lan-sync/pairing'+(replaceDeviceId?'?replace_device_id='+encodeURIComponent(replaceDeviceId):''));if(revision!==pairingRevision)return;tlsPairing.value=result;pairingTimer=setTimeout(()=>{expired.value=true;clearPairingCode()},Math.max(0,result.expires_at*1000-Date.now()));await renderQr()}catch(cause){if(revision===pairingRevision)pairingError.value=String(cause)}finally{pairingBusy.value=false}
}
async function renderQr(){await nextTick();const canvas=qrCanvas.value;if(!canvas)return;const value=payload();if(!value){canvas.getContext('2d')?.clearRect(0,0,canvas.width,canvas.height);return}try{await QRCode.toCanvas(canvas,value,{scale:5,margin:4,errorCorrectionLevel:'M',color:{dark:'#000000ff',light:'#ffffffff'}})}catch{pairingError.value='配对码绘制失败，请重新生成'}}
async function openDialog(mode:'connect'|'pair',replaceDeviceId=''){
  dialogMode.value=mode;replacingId.value=replaceDeviceId;notice.value='';pairingError.value='';expired.value=false;expanded.value=false
  await nextTick();if(!dialog.value?.open)dialog.value?.showModal();dialogOpen.value=true
  if(mode==='pair'&&lanSync.enabled&&!tlsPairing.value)await refreshPairingCode(replaceDeviceId)
}
async function copy(value:string,message:string){if(!value)return;try{await navigator.clipboard.writeText(value);notice.value=message}catch{notice.value='';pairingError.value='复制失败，请手动选择并复制'}}
async function revokeAll(){
  if(!lanSync.paired_devices||busy.value)return
  if(!await confirmDialog({title:'解除全部设备配对',message:`将解除全部 ${lanSync.paired_devices} 台设备的配对，未保存的权限修改也会放弃。解除后设备需重新扫码或输入口令。`,confirmLabel:'解除全部配对',danger:true})){await nextTick();deviceList.value?.focusManagement();return}
  busy.value='revoke';error.value='';notice.value=''
  try{const result=await post<{revoked:number}>('/lan-sync/devices/revoke');clearPairingCode();notice.value=`已解除 ${result.revoked} 台设备配对`;await refreshLanSync();await deviceList.value?.refresh(0,true)}catch(cause){error.value=String(cause)}finally{busy.value=''}
}
async function deviceChanged(){clearPairingCode();await refreshLanSync()}
onMounted(refreshLanSync)
</script>

<template>
  <div class="page sync-settings-page">
    <header class="sync-page-head"><h1>设备同步</h1><div class="sync-page-actions"><span class="host-status">{{hostState}}</span><button class="icon-button" type="button" :disabled="loading" title="刷新同步状态" aria-label="刷新同步状态" @click="refreshLanSync"><RefreshCw :size="20" /></button><button class="button" type="button" :disabled="loading" @click="openDialog('pair')"><Plus :size="19" />添加设备</button></div></header>
    <p v-if="error" class="warning" role="alert">{{error}}</p><p v-if="notice && !dialogOpen" class="settings-success" role="status">{{notice}}</p>
    <section class="card sync-host-bar" aria-labelledby="sync-host-title"><div class="host-identity"><span class="host-symbol"><Server :size="23" /></span><div><h2 id="sync-host-title">局域网同步主机</h2><p>上次同步 · {{lanSync.last_sync_at?formatTime(lanSync.last_sync_at):'暂无记录'}}</p></div></div><div class="host-actions"><button class="button secondary" type="button" @click="openDialog('connect')">连接地址与口令</button><button class="button" type="button" @click="openDialog('pair')"><ScanLine :size="19" />扫码配对</button></div></section>
    <LanPairedDevices ref="deviceList" :revoking="busy==='revoke'" @changed="deviceChanged" @replace="id=>openDialog('pair',id)" @revoke-all="revokeAll" />
    <LanQuestionBankSharing />

    <dialog ref="dialog" class="sync-host-dialog" :class="{'qr-expanded':expanded}" aria-labelledby="sync-dialog-title" @close="onDialogClose">
      <header class="sync-dialog-heading"><h2 id="sync-dialog-title">{{dialogMode==='connect'?'本机连接':replacingId?'重装替换配对':'HTTPS 安全配对'}}</h2><button class="icon-button" type="button" title="关闭" aria-label="关闭弹窗" @click="closeDialog"><X :size="22" /></button></header>
      <template v-if="dialogMode==='connect'">
        <div class="field"><label for="sync-host-address">电脑地址</label><select v-if="lanSync.host_urls.length>1" id="sync-host-address" v-model="selectedUrl"><option v-for="url in lanSync.host_urls" :key="url" :value="url">{{url}}</option></select><code v-if="selectedUrl" class="connection-value">{{selectedUrl}}</code><p v-else class="host-help">{{lanSync.listening?'未检测到可用的局域网 IPv4 地址，请确认电脑已连接局域网。':lanSync.enabled?'同步主机未监听，请检查同步端口或重新生成口令。':'生成口令后开启局域网同步。'}}</p><button class="button secondary" type="button" :disabled="!selectedUrl" @click="copy(selectedUrl,'电脑地址已复制')"><ClipboardCopy :size="18" />复制地址</button></div>
        <div class="field passcode-field"><label>同步口令</label><code class="connection-value passcode">{{lanSync.passcode||'尚未生成'}}</code><div class="dialog-actions"><button class="button secondary" type="button" :disabled="!lanSync.passcode||Boolean(busy)" @click="copy(lanSync.passcode,'同步口令已复制')"><ClipboardCopy :size="18" />复制口令</button><button class="button secondary" type="button" :disabled="Boolean(busy)" @click="generatePasscode">{{busy==='passcode'?'生成中…':lanSync.passcode?'重新生成':'生成口令'}}</button></div></div>
      </template>
      <template v-else>
        <p v-if="replacingId" class="host-help">替换设备 ID · {{replacingId}}。新设备成功扫码后才解除旧绑定。</p>
        <div class="pairing-code" aria-label="局域网同步二维码"><canvas v-show="tlsPairing && !pairingError" ref="qrCanvas" width="380" height="380" role="img" aria-label="安全配对二维码"></canvas><div v-if="!tlsPairing || pairingError" class="qr-placeholder">{{pairingBusy?'正在生成安全配对码…':!lanSync.enabled?'同步主机尚未开启':expired?'配对码已过期':pairingError?'配对码暂不可用':'尚未生成配对码'}}</div></div>
        <p v-if="tlsPairing" class="pairing-expiry">单次有效 · 截止 {{new Date(tlsPairing.expires_at*1000).toLocaleTimeString()}}</p>
        <div class="dialog-actions pairing-actions"><button v-if="!lanSync.enabled" class="button" type="button" :disabled="Boolean(busy)" @click="generatePasscode">{{busy==='passcode'?'开启中…':'生成口令并开启'}}</button><button v-else class="button secondary" type="button" :disabled="pairingBusy" @click="refreshPairingCode()"><RefreshCw :size="18" />{{pairingBusy?'生成中…':tlsPairing?'刷新配对码':'生成配对码'}}</button><button v-if="tlsPairing" class="button secondary" type="button" @click="expanded=!expanded"><Minimize2 v-if="expanded" :size="18" /><Maximize2 v-else :size="18" />{{expanded?'恢复大小':'放大二维码'}}</button></div>
      </template>
      <p v-if="pairingError" class="warning" role="alert">{{pairingError}}</p><p v-if="notice" class="settings-success" role="status">{{notice}}</p>
    </dialog>
  </div>
</template>

<style scoped>
.sync-settings-page{display:grid;gap:20px}.sync-page-head{display:flex;align-items:center;justify-content:space-between;gap:18px;flex-wrap:wrap}.sync-page-head h1{margin:0}.sync-page-actions{display:flex;align-items:center;gap:12px;flex-wrap:wrap}.host-status{font-size:.875rem;color:var(--primary);overflow-wrap:anywhere}.sync-host-bar{display:flex;align-items:center;justify-content:space-between;gap:20px;padding:22px;min-width:0}.host-identity{display:flex;align-items:center;gap:12px;min-width:0}.host-symbol{width:44px;height:44px;display:grid;place-items:center;flex:0 0 44px;color:var(--primary);background:var(--primary-soft);border-radius:12px}.host-identity h2{font-size:1.25rem;margin:0;overflow-wrap:anywhere}.host-identity p{font-size:.875rem;color:var(--muted);margin:5px 0 0}.host-actions,.dialog-actions{display:flex;align-items:center;gap:10px;flex-wrap:wrap}.host-actions{flex:none}.icon-button{width:44px;height:44px;min-width:44px;flex:0 0 44px;padding:0}.sync-host-dialog{background:var(--surface-solid);color:var(--ink);border:1px solid var(--line-strong);border-radius:20px;padding:24px;width:min(540px,calc(100vw - 32px));max-width:calc(100vw - 32px);max-height:calc(100dvh - 32px);overflow:auto;box-sizing:border-box;box-shadow:var(--shadow)}.sync-host-dialog::backdrop{background:rgba(15,22,32,.55)}.sync-host-dialog.qr-expanded{width:min(760px,calc(100vw - 32px))}.sync-dialog-heading{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:20px}.sync-dialog-heading h2{margin:0;font-size:1.25rem;overflow-wrap:anywhere}.sync-host-dialog .field{display:grid;gap:12px}.connection-value{display:block;overflow-wrap:anywhere;padding:12px;border:1px solid var(--line);border-radius:10px;font-size:1rem;user-select:text}.passcode-field{border-top:1px solid var(--line);margin-top:22px;padding-top:22px}.passcode{font-size:1.125rem;letter-spacing:2px}.host-help,.pairing-expiry{font-size:.875rem;color:var(--muted);overflow-wrap:anywhere}.pairing-code{background:#fff;color:#29313c;border:1px solid #d7dce3;border-radius:12px;width:min(100%,380px);margin:0 auto;min-height:180px;display:grid;place-items:center}.pairing-code canvas{display:block;width:100%;height:auto;image-rendering:pixelated;aspect-ratio:1}.qr-expanded .pairing-code{width:min(100%,640px)}.qr-placeholder{padding:28px;text-align:center;color:#616d7d;font-size:1rem}.pairing-expiry{text-align:center;margin:14px 0}.pairing-actions{justify-content:center;margin-top:18px}.sync-host-dialog .warning,.sync-host-dialog .settings-success{margin:16px 0 0}.sync-settings-page>p{margin:0}button:focus-visible{outline:3px solid var(--focus);outline-offset:3px}
@media(max-width:1150px){.sync-host-bar{align-items:flex-start;flex-direction:column}.host-actions{width:100%}}
@media(max-width:540px){.sync-host-bar{padding:18px}.host-identity h2{font-size:1.125rem}.host-actions .button{flex:1;min-width:0}.sync-page-actions{gap:8px}.host-status{flex-basis:100%}.sync-host-dialog{padding:18px}.dialog-actions .button{flex:1}}
</style>

<style scoped>
/* qrcode writes inline pixel dimensions; constrain the rendered canvas to its frame. */
.pairing-code{min-width:0}.pairing-code canvas{width:100%!important;max-width:100%;height:auto!important}
.sync-settings-page :deep(.icon-button){display:inline-flex;align-items:center;justify-content:center;border:1px solid var(--line);border-radius:12px;background:var(--surface-solid);color:var(--muted);cursor:pointer}
.sync-settings-page :deep(.icon-button:hover){background:var(--primary-faint)}
.sync-settings-page :deep(.icon-button:disabled){opacity:.5;cursor:default}

</style>
