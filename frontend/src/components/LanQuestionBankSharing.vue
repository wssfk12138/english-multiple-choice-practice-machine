<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { RefreshCw, Save } from 'lucide-vue-next'
import { get, put } from '../api'

const enabled = ref(false)
const selected = ref<number[]>([])
const choices = ref<{ id: number; name: string; papers: number }[]>([])
const busy = ref(false)
const error = ref('')
const notice = ref('')
async function load() {
  busy.value = true
  error.value = ''
  try {
    const state: any = await get('/lan-sync/question-banks/sharing')
    enabled.value = state.enabled
    selected.value = state.profileIds
    choices.value = state.choices
  } catch (cause) { error.value = String(cause) }
  finally { busy.value = false }
}
async function save() {
  busy.value = true
  error.value = ''
  notice.value = ''
  try {
    await put('/lan-sync/question-banks/sharing', { enabled: enabled.value, profile_ids: selected.value })
    notice.value = enabled.value ? '共享快照已发布' : '共享已关闭'
  } catch (cause) { error.value = String(cause) }
  finally { busy.value = false }
}
onMounted(load)
</script>

<template>
  <section class="lan-bank-sharing" aria-labelledby="lan-bank-sharing-title">
    <div class="heading">
      <h2 id="lan-bank-sharing-title">局域网题库共享</h2>
      <button class="icon-button" :disabled="busy" title="刷新共享状态" aria-label="刷新共享状态" @click="load"><RefreshCw :size="18" /></button>
    </div>
    <label><input v-model="enabled" type="checkbox" :disabled="busy">开放题库共享</label>
    <fieldset :disabled="busy">
      <legend>共享范围</legend>
      <label v-for="item in choices" :key="item.id"><input v-model="selected" type="checkbox" :value="item.id">{{ item.name }} · {{ item.papers }} 套</label>
      <span v-if="!choices.length">暂无正式题库</span>
    </fieldset>
    <button class="button" :disabled="busy || (enabled && !selected.length)" @click="save"><Save :size="16" />{{ busy ? '处理中' : enabled ? '发布共享快照' : '保存关闭状态' }}</button>
    <p v-if="error" class="warning" role="alert">{{ error }}</p>
    <p v-if="notice" role="status">{{ notice }}</p>
  </section>
</template>

<style scoped>
.lan-bank-sharing { padding: 20px 0; border-block: 1px solid var(--line); }
.heading { display: flex; gap: 12px; align-items: center; justify-content: space-between; }
h2 { font-size: 18px; }
fieldset { border: 0; padding: 12px 0; min-width: 0; }
label { display: flex; align-items: center; gap: 8px; padding: 6px 0; overflow-wrap: anywhere; }
input { width: 18px; height: 18px; flex: 0 0 18px; }
</style>
