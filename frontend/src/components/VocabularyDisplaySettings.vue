<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { loadVocabDisplayConfig, saveVocabDisplayConfig, VOCAB_DISPLAY_OPTIONS } from '../services/vocabularyDisplayConfig'
const config = ref(loadVocabDisplayConfig())
const error = ref('')
const groups = ['核心信息', '补充学习', '个人记录']
function refresh() { config.value = loadVocabDisplayConfig() }
function save() {
  try { saveVocabDisplayConfig(config.value); error.value = '' }
  catch { error.value = '显示设置未保存，请检查存储空间后重试' }
}
onMounted(() => { window.addEventListener('vocab-display-change', refresh); window.addEventListener('storage', refresh) })
onBeforeUnmount(() => { window.removeEventListener('vocab-display-change', refresh); window.removeEventListener('storage', refresh) })
</script>
<template>
  <section class="vocabulary-display-settings">
    <fieldset v-for="group in groups" :key="group">
      <legend>{{ group }}</legend>
      <label v-for="option in VOCAB_DISPLAY_OPTIONS.filter(o => o.group === group)" :key="option.key">
        <span>{{ option.label }}</span><input v-model="config[option.key]" type="checkbox" @change="save">
      </label>
    </fieldset>
    <p v-if="error" class="error" role="alert">{{ error }} <button class="button ghost" @click="save">重试</button></p>
  </section>
</template>
<style scoped>
.vocabulary-display-settings { display:grid; gap:16px; text-align:left; min-width:0; }
fieldset { border:0; padding:0; margin:0; min-width:0; }
legend { font-weight:600; margin-bottom:6px; }
label { display:flex; align-items:center; justify-content:space-between; gap:16px; min-height:40px; padding:6px 0; border-bottom:1px solid var(--line); cursor:pointer; }
input { width:18px; height:18px; flex-shrink:0; accent-color:var(--primary); }
</style>
