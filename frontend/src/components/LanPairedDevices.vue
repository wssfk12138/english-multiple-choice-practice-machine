<script setup lang="ts">
import { ChevronLeft, ChevronRight, RefreshCw, Unplug, Replace, Save, Smartphone, Pencil, MoreHorizontal } from 'lucide-vue-next'
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { del, get, put } from '../api'
import { confirmDialog, promptDialog } from '../platform/dialogs'

type Device = { device_id: string; paired_at: string; last_seen_at: string; display_name?: string; device_model?: string }
type Permission = { categories: string[]; effective_categories: string[] }
const props = defineProps<{ revoking?: boolean }>()
const emit = defineEmits<{ changed: []; replace: [deviceId: string]; 'revoke-all': [] }>()
const managementMenu = ref<HTMLDetailsElement | null>(null)
function closeManagementMenu(event?: Event) {
  if (event && managementMenu.value?.contains(event.target as Node)) return
  if (managementMenu.value) managementMenu.value.open = false
}
function escapeManagementMenu() {
  closeManagementMenu()
  managementMenu.value?.querySelector('summary')?.focus()
}
function requestRevokeAll() {
  if (busy.value || props.revoking) return
  closeManagementMenu()
  emit('revoke-all')
}
const devices = ref<Device[]>([])
const total = ref(0), offset = ref(0), selectedId = ref(''), busy = ref(false), error = ref(''), notice = ref('')
const pageSize = 10
const labels: Record<string, string> = { practice: '做题记录', question_bank: '题库（电脑到安卓）', vocabulary: '单词本', wrong: '错题本', models: '模型配置（含密钥）' }
const permissions = ref<Record<string, Permission>>({})
const saved = ref<Record<string, string[]>>({})
const current = computed(() => devices.value.find(d => d.device_id === selectedId.value))
const currentPermissions = computed(() => permissions.value[selectedId.value])
const dirty = (id: string) => JSON.stringify([...(permissions.value[id]?.categories || [])].sort()) !== JSON.stringify([...(saved.value[id] || [])].sort())
const name = (d: Device) => d.display_name || d.device_model || '未命名 Android 设备'
function time(value: string) {
  const date = new Date(value && !/[Z+]|\d-\d\d:\d\d$/.test(value) ? value.replace(' ', 'T') + 'Z' : value)
  return Number.isNaN(date.getTime()) ? '暂无记录' : new Intl.DateTimeFormat('zh-CN', { month:'2-digit', day:'2-digit', hour:'2-digit', minute:'2-digit' }).format(date)
}
async function load(nextOffset = offset.value) {
  let result = await get<{ items: Device[]; total: number }>('/lan-sync/devices?limit=' + pageSize + '&offset=' + nextOffset)
  if (!result.items.length && nextOffset > 0) {
    nextOffset = Math.max(0, Math.floor((result.total - 1) / pageSize) * pageSize)
    result = await get('/lan-sync/devices?limit=' + pageSize + '&offset=' + nextOffset)
  }
  const entries = await Promise.all(result.items.map(async d => [d.device_id, await get<Permission>('/lan-sync/devices/' + encodeURIComponent(d.device_id) + '/permissions')] as const))
  devices.value = result.items; total.value = result.total; offset.value = nextOffset
  permissions.value = Object.fromEntries(entries)
  saved.value = Object.fromEntries(entries.map(([id, p]) => [id, [...p.categories]]))
  if (!result.items.some(d => d.device_id === selectedId.value)) selectedId.value = result.items[0]?.device_id || ''
}
async function refresh(delta = 0, discard = false) {
  if (busy.value) return
  if (!discard && Object.keys(permissions.value).some(dirty) && !await confirmDialog({ title:'放弃未保存的权限修改？', message:'刷新或翻页将重新读取设备权限。', confirmLabel:'重新读取' })) return
  busy.value = true; error.value = ''; notice.value = ''
  try { await load(Math.max(0, offset.value + delta)) } catch { error.value = '无法读取绑定设备，请刷新重试' }
  finally { busy.value = false }
}
async function savePermissions(device: Device) {
  if (busy.value) return
  busy.value = true; error.value = ''; notice.value = ''
  try {
    const id = device.device_id
    await put('/lan-sync/devices/' + encodeURIComponent(id) + '/permissions', { categories: permissions.value[id]!.categories })
    const result = await get<Permission>('/lan-sync/devices/' + encodeURIComponent(id) + '/permissions')
    permissions.value[id] = result; saved.value[id] = [...result.categories]
    notice.value = '权限已保存'
  } catch { error.value = '同步权限保存失败，请重试' }
  finally { busy.value = false }
}
async function rename(device: Device) {
  const value = await promptDialog({ title:'修改设备名称', message:'留空时使用设备上报的型号。', confirmLabel:'保存名称', input:{ label:'设备名称（最多80字）', value:device.display_name || '', placeholder:device.device_model || '例如：日常复习手机' } })
  if (value === null || busy.value) return
  if (Array.from(value).length > 80) { error.value = '设备名称不能超过80字'; return }
  busy.value = true; error.value = ''; notice.value = ''
  try {
    const result = await put<{display_name:string}>('/lan-sync/devices/' + encodeURIComponent(device.device_id) + '/name', { display_name:value })
    device.display_name = result.display_name; notice.value = '设备名称已保存'
  } catch { error.value = '设备名称保存失败，请重试' }
  finally { busy.value = false }
}
async function revoke(device: Device) {
  if (busy.value || !await confirmDialog({ title:'解除设备配对', message:`将解除“${name(device)}”（${device.device_id}）的配对。该设备需要重新扫码或输入口令才能同步。`, confirmLabel:'解除配对', danger:true })) return
  if (Object.keys(permissions.value).some(id => id !== device.device_id && dirty(id)) && !await confirmDialog({ title:'存在未保存的权限修改', message:'解除后将刷新设备列表，未保存的修改会被放弃。', confirmLabel:'继续解除', danger:true })) return
  busy.value = true; error.value = ''; notice.value = ''
  try { await del('/lan-sync/devices/' + encodeURIComponent(device.device_id)); await load(); emit('changed') }
  catch { error.value = '解除配对失败，请刷新重试' } finally { busy.value = false }
}
async function replace(device: Device) {
  if (busy.value) return
  if (await confirmDialog({ title:'重装设备替换', message:`为“${name(device)}”（${device.device_id}）生成替换码？新设备成功扫码后才解除旧绑定。`, confirmLabel:'生成替换码' })) emit('replace', device.device_id)
}
function choose(id: string) { selectedId.value = id; notice.value = '' }
onMounted(() => { void refresh(); document.addEventListener('click', closeManagementMenu) })
onUnmounted(() => document.removeEventListener('click', closeManagementMenu))
defineExpose({ refresh, focusManagement: escapeManagementMenu })
</script>

<template>
  <section class="card lan-devices" :class="{'no-devices':!devices.length}" aria-labelledby="lan-devices-title" :aria-busy="busy">
    <div class="device-master">
      <header><h2 id="lan-devices-title">绑定设备 · {{ total }}</h2><div class="device-header-actions"><button class="icon-button" type="button" :disabled="busy || props.revoking" title="刷新绑定设备" aria-label="刷新绑定设备" @click="refresh()"><RefreshCw :size="20" /></button><details v-if="total > 0" ref="managementMenu" class="device-more device-management" @keydown.esc.stop.prevent="escapeManagementMenu"><summary class="icon-button" aria-label="绑定设备管理" title="绑定设备管理" :aria-disabled="busy || props.revoking" @click="(busy || props.revoking) && $event.preventDefault()"><MoreHorizontal :size="21" /></summary><div class="device-menu management-menu"><button class="danger" type="button" :disabled="busy || props.revoking" @click="requestRevokeAll"><Unplug :size="18" />{{ props.revoking ? '解除中…' : '解除全部设备配对' }}</button></div></details></div></header>
      <p v-if="!devices.length" class="empty">{{ busy ? '正在读取…' : error ? '设备读取失败' : '暂无绑定设备' }}</p>
      <div v-else class="device-list" aria-label="绑定设备列表">
        <button v-for="device in devices" :key="device.device_id" class="device-choice" type="button" :aria-pressed="selectedId === device.device_id" :disabled="busy" @click="choose(device.device_id)">
          <Smartphone :size="21" aria-hidden="true" /><span><strong>{{ name(device) }}</strong><small><template v-if="!device.display_name && !device.device_model">{{ device.device_id.slice(-8) }} · </template>{{ time(device.last_seen_at) }}<span v-if="dirty(device.device_id)"> · 未保存</span></small></span>
        </button>
      </div>
      <nav v-if="total > pageSize" class="lan-device-pages" aria-label="设备分页">
        <button class="icon-button" type="button" :disabled="busy || offset === 0" title="上一页" aria-label="上一页" @click="refresh(-pageSize)"><ChevronLeft :size="20" /></button><span>{{ Math.floor(offset / pageSize) + 1 }} / {{ Math.ceil(total / pageSize) }}</span><button class="icon-button" type="button" :disabled="busy || offset + pageSize >= total" title="下一页" aria-label="下一页" @click="refresh(pageSize)"><ChevronRight :size="20" /></button>
      </nav>
    </div>
    <div class="device-detail">
      <template v-if="current">
        <header class="detail-heading"><span class="device-symbol"><Smartphone :size="24" /></span><div class="identity"><h3>{{ name(current) }}</h3><p>上次连接 · {{ time(current.last_seen_at) }}</p></div><div class="device-actions"><button class="icon-button" type="button" :disabled="busy" title="修改设备名称" aria-label="修改设备名称" @click="rename(current)"><Pencil :size="20" /></button><details class="device-more"><summary class="icon-button" aria-label="更多设备操作" title="更多设备操作"><MoreHorizontal :size="21" /></summary><div class="device-menu"><button type="button" :disabled="busy" @click="replace(current)"><Replace :size="18" />重装替换</button><button class="danger" type="button" :disabled="busy" @click="revoke(current)"><Unplug :size="18" />解除配对</button></div></details></div></header>
        <p class="device-id">设备 ID · {{ current.device_id }}</p>
        <fieldset v-if="currentPermissions" class="lan-permissions" :disabled="busy"><legend>允许同步</legend><div class="permissions-grid"><label v-for="(label, category) in labels" :key="category" :class="{ models: category === 'models', checked: currentPermissions.categories.includes(category) }"><input v-model="currentPermissions.categories" type="checkbox" :value="category"><span>{{ label }}<small v-if="currentPermissions.effective_categories.includes(category)" class="effective">最近协商已启用</small></span></label></div></fieldset>
        <footer class="device-footer"><span role="status">{{ notice || (dirty(current.device_id) ? '权限有未保存的修改' : '已保存的同步范围') }}</span><button class="button secondary" type="button" :disabled="busy" @click="savePermissions(current)"><Save :size="18" />{{ busy ? '处理中…' : '保存权限' }}</button></footer>
      </template>
      <p v-if="error" class="warning" role="alert">{{ error }}</p>
    </div>
  </section>
</template>

<style scoped>
.lan-devices{display:grid;grid-template-columns:minmax(235px,27%) minmax(0,1fr);padding:0;min-width:0;overflow:visible}.device-master{padding:20px 16px;border-right:1px solid var(--line);min-width:0}.device-master>header{display:flex;align-items:center;justify-content:space-between;gap:8px;margin-bottom:12px}.device-header-actions{display:flex;gap:4px;flex:none}.device-master h2{font-size:1rem;margin:0}.icon-button{width:44px;height:44px;min-width:44px;padding:0;flex:0 0 44px}.device-list{display:grid;gap:8px}.device-choice{display:flex;align-items:center;gap:12px;text-align:left;padding:14px 12px;min-height:76px;width:100%;border:1px solid transparent;border-radius:12px;background:transparent;color:var(--ink);cursor:pointer;font:inherit}.device-choice>svg{flex:0 0 21px}.device-choice>span{min-width:0}.device-choice strong{display:block;font-size:1rem;overflow-wrap:anywhere;line-height:1.45}.device-choice small{display:block;color:var(--muted);font-size:.8125rem;margin-top:4px}.device-choice[aria-pressed=true]{background:var(--primary-soft);border-color:var(--line-strong)}.device-choice:hover{border-color:var(--line-strong)}.device-detail{padding:24px;min-width:0}.detail-heading{display:flex;align-items:flex-start;gap:12px}.identity{min-width:0;flex:1}.identity h3{margin:0;font-size:1.125rem;overflow-wrap:anywhere}.identity p{margin:5px 0 0;font-size:.875rem;color:var(--muted)}.device-symbol{display:grid;place-items:center;border-radius:12px;background:var(--primary-soft);color:var(--primary);width:44px;height:44px;flex:0 0 44px}.device-actions{display:flex;gap:4px;flex:none}.device-id{color:var(--muted);font-size:.8125rem;overflow-wrap:anywhere;padding-bottom:16px;border-bottom:1px solid var(--line);margin:16px 0}.device-more{position:relative}.device-more summary{display:grid;place-items:center;list-style:none;cursor:pointer}.device-more summary::-webkit-details-marker{display:none}.device-menu{position:absolute;right:0;top:50px;width:190px;padding:6px;background:var(--surface-solid);border:1px solid var(--line-strong);border-radius:12px;box-shadow:var(--shadow);z-index:10}.device-menu button{display:flex;gap:10px;align-items:center;background:transparent;color:var(--ink);border:0;min-height:48px;width:100%;text-align:left;font:inherit;padding:8px;border-radius:8px;cursor:pointer}.device-menu button:hover{background:var(--primary-faint)}.device-menu .danger{color:var(--danger)}.management-menu{left:0;right:auto;width:260px;max-width:calc(100vw - 48px)}.device-menu button:disabled{opacity:.5;cursor:default}.device-management summary[aria-disabled=true]{opacity:.5;cursor:default}.lan-permissions{border:0;padding:0;margin:0;min-width:0}.lan-permissions legend{font-size:1rem;font-weight:700;margin-bottom:14px}.permissions-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px}.permissions-grid label{display:flex;gap:9px;align-items:flex-start;border:1px solid var(--line);border-radius:12px;min-height:52px;padding:12px;font-size:1rem;overflow-wrap:anywhere}.permissions-grid label.checked{background:var(--primary-faint);border-color:var(--line-strong)}.permissions-grid input{width:19px;height:19px;flex:0 0 19px;margin:4px 0 0;accent-color:var(--primary)}.permissions-grid .models{grid-column:1/-1}.effective{display:block;color:var(--muted);font-size:.8125rem;margin-top:4px}.device-footer{display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap;margin-top:20px}.device-footer>span{font-size:.875rem;color:var(--muted)}.lan-device-pages{display:flex;gap:4px;align-items:center;justify-content:center;flex-wrap:wrap;margin-top:16px;font-size:.875rem}.empty{color:var(--muted);margin:16px 0;overflow-wrap:anywhere}button:focus-visible,summary:focus-visible{outline:3px solid var(--focus);outline-offset:3px}
@media(max-width:1120px){.lan-devices{grid-template-columns:minmax(210px,30%) minmax(0,1fr)}.device-detail{padding:18px}.device-master{padding:18px 12px}}
@media(max-width:760px){.management-menu{left:auto;right:0}.lan-devices{grid-template-columns:minmax(0,1fr)}.device-master{border-right:0;border-bottom:1px solid var(--line)}.device-list{grid-template-columns:repeat(2,minmax(0,1fr))}.device-choice{padding:10px}.device-detail{padding:18px}.device-master h2{font-size:1.125rem}}
@media(max-width:480px){.permissions-grid,.device-list{grid-template-columns:minmax(0,1fr)}.detail-heading{flex-wrap:wrap}.identity{flex-basis:calc(100% - 60px)}.device-actions{margin-left:auto}.device-footer .button{width:100%}.device-master>header h2{font-size:1rem}}
</style>

<style scoped>
.device-list{max-height:clamp(220px,50dvh,520px);overflow-y:auto;overscroll-behavior:contain;scrollbar-gutter:stable;align-content:start}
.no-devices{grid-template-columns:minmax(0,1fr)}.no-devices .device-master{border:0}.no-devices .empty{padding:10px 0;margin:0;text-align:left}.no-devices .device-detail{padding:0 20px}.no-devices .device-detail:empty{display:none}
</style>
