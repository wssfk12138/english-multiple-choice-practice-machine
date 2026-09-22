<script setup lang="ts">
import { ChevronLeft, ChevronRight, RefreshCw, Unplug, Replace, Save } from 'lucide-vue-next'
import { onMounted, ref } from 'vue'
import { del, get, put } from '../api'

type Device = { device_id: string; paired_at: string; last_seen_at: string }
const emit = defineEmits<{ changed: []; replace: [deviceId: string] }>()
const devices = ref<Device[]>([])
const total = ref(0)
const offset = ref(0)
const busy = ref(false)
const error = ref('')
const pageSize = 10
const labels: Record<string, string> = { practice: '做题记录', models: '模型配置（含密钥）', question_bank: '题库（电脑到安卓）', vocabulary: '单词本', wrong: '错题本' }
const permissions = ref<Record<string, { categories: string[]; effective_categories: string[] }>>({})

async function savePermissions(device: Device) {
  busy.value = true
  error.value = ''
  try {
    await put('/lan-sync/devices/' + encodeURIComponent(device.device_id) + '/permissions', { categories: permissions.value[device.device_id]!.categories })
    await load()
  } catch { error.value = '同步权限保存失败，请刷新后重试' }
  finally { busy.value = false }
}

async function load() {
  const result = await get<{ items: Device[]; total: number }>('/lan-sync/devices?limit=' + pageSize + '&offset=' + offset.value)
  devices.value = result.items
  total.value = result.total
  for (const device of devices.value) {
    permissions.value[device.device_id] = await get('/lan-sync/devices/' + encodeURIComponent(device.device_id) + '/permissions')
  }
  if (!devices.value.length && offset.value > 0) {
    offset.value = Math.max(0, offset.value - pageSize)
    await load()
  }
}

async function refresh(delta = 0) {
  if (busy.value) return
  busy.value = true
  error.value = ''
  offset.value = Math.max(0, offset.value + delta)
  try { await load() } catch { error.value = '无法读取绑定设备，请重试' }
  finally { busy.value = false }
}

async function revoke(device: Device) {
  if (busy.value || !confirm('解除设备 ' + device.device_id + ' 的绑定？该设备需要重新扫码或输入口令。')) return
  busy.value = true
  error.value = ''
  try {
    await del('/lan-sync/devices/' + encodeURIComponent(device.device_id))
    await load()
    emit('changed')
  } catch { error.value = '解除绑定失败，请刷新后重试' }
  finally { busy.value = false }
}

onMounted(() => { void refresh() })

function replace(device: Device) {
  if (confirm('为重装后的设备生成替换码？新设备成功扫码绑定后，将解除旧绑定 ' + device.device_id + '。')) {
    emit('replace', device.device_id)
  }
}
</script>

<template>
  <div class="lan-devices">
    <div class="lan-device-heading">
      <h3>绑定设备 · {{ total }}</h3>
      <button class="icon-button" type="button" :disabled="busy" title="刷新绑定设备" aria-label="刷新绑定设备" @click="refresh()"><RefreshCw :size="20" /></button>
    </div>
    <p v-if="error" role="alert">{{ error }}</p>
    <p v-else-if="!devices.length">{{ busy ? '正在读取' : '暂无绑定设备' }}</p>
    <ul v-else>
      <li v-for="device in devices" :key="device.device_id">
        <span class="lan-device-id">{{ device.device_id }}</span>
        <button class="icon-button" type="button" :disabled="busy" :title="'重装替换：' + device.device_id" :aria-label="'重装替换：' + device.device_id" @click="replace(device)"><Replace :size="20" /></button>
        <button class="icon-button danger" type="button" :disabled="busy" :title="'解除绑定：' + device.device_id" :aria-label="'解除绑定：' + device.device_id" @click="revoke(device)"><Unplug :size="20" /></button>
        <fieldset v-if="permissions[device.device_id]" class="lan-permissions">
          <legend>允许同步</legend>
          <label v-for="(label, category) in labels" :key="category">
            <input v-model="permissions[device.device_id]!.categories" type="checkbox" :value="category" :disabled="busy">
            <span>{{ label }}</span>
            <span v-if="permissions[device.device_id]!.effective_categories.includes(category)">最近协商已启用</span>
          </label>
          <button class="icon-button" type="button" :disabled="busy" title="保存同步权限" aria-label="保存同步权限" @click="savePermissions(device)"><Save :size="20" /></button>
        </fieldset>
      </li>
    </ul>
    <div v-if="total > pageSize" class="lan-device-pages">
      <button class="icon-button" type="button" :disabled="busy || offset === 0" title="上一页" aria-label="上一页" @click="refresh(-pageSize)"><ChevronLeft :size="20" /></button>
      <span>{{ Math.floor(offset / pageSize) + 1 }} / {{ Math.ceil(total / pageSize) }}</span>
      <button class="icon-button" type="button" :disabled="busy || offset + pageSize >= total" title="下一页" aria-label="下一页" @click="refresh(pageSize)"><ChevronRight :size="20" /></button>
    </div>
  </div>
</template>

<style scoped>
.lan-devices { margin-top: 16px; min-width: 0; }
.lan-permissions { grid-column: 1 / -1; border: 0; margin: 0; padding: 4px 0; min-width: 0; }
.lan-permissions label { display: flex; align-items: center; flex-wrap: wrap; gap: 8px; padding: 4px 0; }
.lan-permissions input { width: 18px; height: 18px; flex: 0 0 18px; }
.lan-device-heading, .lan-device-pages { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
.lan-device-heading h3 { margin: 0; font-size: 18px; }
ul { list-style: none; margin: 8px 0 0; padding: 0; }
li { display: grid; grid-template-columns: minmax(0, 1fr) 44px 44px; align-items: center; gap: 8px; padding: 6px 0; }
.lan-device-id { overflow-wrap: anywhere; font-size: 16px; }
.icon-button { width: 44px; height: 44px; flex: 0 0 44px; }
.lan-device-pages { justify-content: flex-end; margin-top: 8px; }
</style>
