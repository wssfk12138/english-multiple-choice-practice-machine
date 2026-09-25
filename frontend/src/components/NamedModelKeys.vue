<script setup lang="ts">
import { confirmDialog } from '../platform/dialogs'
import { computed, ref, toRefs } from 'vue'
import { keyDraft } from '../services/keyDrafts'
import { profileDrafts, serializeProfileOperation } from '../services/profileDrafts'


import { Plus, Save, Trash2 } from 'lucide-vue-next'
import { post } from '../api'
const props = defineProps<{ profile: { id: number; keys?: { id: string; name: string; mask?: string }[]; selected_key_id?: string; has_api_key: boolean } }>()
const emit = defineEmits<{ changed: [] }>()
const session = keyDraft(props.profile.id)
const { name, value, busy, error, drafts, adding, retry } = toRefs(session.state)
const keys = computed(() => props.profile.keys || [])
const selectedKey = computed(() => keys.value.find(key => key.id === props.profile.selected_key_id) || null)
// 掩码由安全存储侧算好随配置一起返回，这里只截后四位供识别，界面上不会出现明文密钥。
function keyMask(key: { mask?: string }) {
  const raw = String(key.mask || '')
  if (!raw.includes('****')) return ''
  const tail = raw.split('****').pop() || ''
  return tail ? `···· ${tail}` : ''
}
async function edit(action: string, identity = '') {
  if (action === 'delete' && !await confirmDialog({ title: '删除密钥', message: '删除此密钥？删除后需要重新添加。', confirmLabel: '删除', danger: true })) return
  const alias = action === 'rename' ? drafts.value[identity] ?? props.profile.keys?.find(k => k.id === identity)?.name : name.value
  if (action === 'rename' && !alias?.trim()) { error.value = '请输入密钥名称，草稿尚未保存'; return }
  if (action === 'rename' && alias === props.profile.keys?.find(k => k.id === identity)?.name) return
  const secret = action === 'add' ? value.value : ''
  session.sequence = session.sequence.then(async () => {
  busy.value = true
  error.value = ''
  try {
    const result = await serializeProfileOperation(() => post<{ keys: { id: string; name: string; mask?: string }[]; selected_key_id: string; has_api_key: boolean }>(`/ai/profiles/${props.profile.id}/keys`,
      { action, identity, name: alias, value: secret }))
    Object.assign(props.profile, result)
    if (profileDrafts[props.profile.id]) Object.assign(profileDrafts[props.profile.id], result)
    if (action === 'add') { name.value = ''; value.value = ''; adding.value = false }
    emit('changed')
    retry.value = null
  } catch { error.value = '密钥操作失败，请检查名称、选择和安全存储'; retry.value = () => edit(action, identity) }
  finally { busy.value = false }
  })
  await session.sequence
}
</script>

<template>
  <fieldset class="named-keys" :disabled="busy">
    <legend>API 密钥</legend>
    <div class="key-summary">
      <span>{{ selectedKey?.name || (profile.has_api_key ? '已保存密钥' : '不使用密钥') }}</span>
    </div>
    <label class="no-key"><input type="radio" :name="`key-${profile.id}`" :checked="!profile.selected_key_id" @change="edit('select')">不使用密钥</label>
    <div class="key-list" role="listbox" aria-label="API Key 列表">
      <div v-for="key in keys" :key="key.id" class="key-row" role="option" :aria-selected="profile.selected_key_id === key.id">
        <input type="radio" :name="`key-${profile.id}`" :checked="profile.selected_key_id === key.id" :aria-label="`选用 ${key.name}`" @change="edit('select', key.id)">
        <div class="key-alias">
          <input :value="drafts[key.id] ?? key.name" maxlength="100" aria-label="密钥名称" @input="drafts[key.id] = ($event.target as HTMLInputElement).value">
          <small v-if="keyMask(key)">{{ keyMask(key) }}</small>
        </div>
        <button type="button" title="保存名称" aria-label="保存名称" @click="edit('rename', key.id)"><Save :size="17" /></button>
        <button type="button" title="删除密钥" aria-label="删除密钥" @click="edit('delete', key.id)"><Trash2 :size="17" /></button>
      </div>
    </div>
    <div class="new-key">
      <input v-model="name" aria-label="新密钥名称" placeholder="密钥名称" maxlength="100" autocomplete="off">
      <input v-model="value" aria-label="新 API 密钥" placeholder="API Key" type="password" maxlength="8192" autocomplete="new-password">
      <button type="button" :disabled="!name.trim() || !value.trim()" title="添加密钥" aria-label="添加密钥" @click="edit('add')"><Plus :size="18" /></button>
    </div>
    <p v-if="busy" role="status">正在保存密钥…</p>
    <p v-if="error" role="alert">{{ error }}<button v-if="retry" type="button" @click="retry()">重试</button></p>
  </fieldset>
</template>

<style scoped>
.named-keys { position:relative; border:0; padding:0; margin:12px 0; min-width:0; }
.named-keys legend { font-size: 1rem; font-weight: 600; margin-bottom: 8px; }
.key-summary { display:flex; align-items:center; gap:12px; min-width:0; margin-bottom:6px; }
.key-summary span { overflow-wrap:anywhere; min-width:0; }
.key-list { width:100%; }
.key-row { display: grid; grid-template-columns: 20px minmax(0, 1fr) 44px 44px; align-items: center; gap: 8px; margin: 8px 0; }
.new-key { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 2fr) 44px; gap: 8px; margin-top: 8px; }
.named-keys input { min-width: 0; width: 100%; }
.named-keys input[type=radio] { width: 16px; height: 16px; margin: 0; }
.named-keys button { width: 44px; height: 44px; padding: 0; display: grid; place-items: center; border-radius: 6px; border: 1px solid var(--line); background: var(--surface); color: var(--ink); }
.no-key { display: flex; align-items: center; gap: 8px; font-size: 14px; margin-bottom: 4px; }
.key-alias { min-width:0; }
.key-alias small { display:block; color:var(--muted); font-size:12.5px; letter-spacing:.14em; font-variant-numeric:tabular-nums; }
@media (max-width: 480px) { .new-key { grid-template-columns: minmax(0, 1fr) 44px; } .new-key input:first-child { grid-column: 1 / -1; } }
</style>
